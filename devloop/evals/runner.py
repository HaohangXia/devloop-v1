"""跑评测集并打分。

## 判分口径（三条，每条都有理由）

1. **整体准确率不是主指标。** 20 题里 11 题答案是 `broken`——一个「一律答 broken」
   的模型能拿 55%。**必须分类看**：
   - `broken` 上的召回：能不能找出腐烂（主要目标）
   - `holds` 上的准确：会不会**乱报**（假阳性；只测 broken 就测不出来）
   - `undecidable` 上的准确：知不知道自己不知道
2. **`broken` 被答成 `undecidable`，和被答成 `holds`，不是一回事。**
   前者是保守（漏了，但没说错），后者是**说反了**（会让人信一句已经失效的话）。
   混淆矩阵全出，不合并成一个数。
3. **n=20，只够区分数量级。** ⛔ 不许把准确率报到小数点后一位，
   也不许拿两次 60% vs 65% 说「变好了」。
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .. import backends, naming
from ..dispatch import dispatch_one
from ..models import TaskSpec
from . import BASE_REPO, KINDS, Case, check_baseline, load

# ⚠️ 放宽的只是**行首与值的装饰**（加粗、反引号、列表符、全角冒号、句末标点），
#    词的位置仍然锚在整行上。实测 20 种真实写法里旧正则漏掉 10 种，
#    而 Markdown 加粗恰好是模型最常见的行为——**漏掉就记成「判断错了」**，
#    把格式问题算进了模型的准确率。
#    ⛔ 仍然不做模糊匹配：正文里出现 broken 就算，会把
#      「我一开始以为 broken，后来发现 holds」判成 broken。
_VERDICT = re.compile(
    r"^[\s>#*\-]*\**\s*VERDICT\s*[:：]\s*\**\s*`?(holds|broken|undecidable)`?"
    r"\**\s*[.。]?\s*$", re.M | re.I)


@dataclass
class Answer:
    case: Case
    got: str                    # holds / broken / undecidable / ""（没给出判定）
    ok: bool
    cost: float | None
    secs: float
    turns: int
    raw_tail: str = ""          # 报告末尾，判不出时给人看
    failure: str = ""           # 派单本身失败的原因；非空 = 这题**没有读数**

    @property
    def counted(self) -> bool:
        """这题算不算进准确率的分母。

        ⛔ 派单失败（超时、找不到 claude、撞满轮数被截断、跑在了别的模型上）
        **不是模型答错**。`models.py` 自己写过这条教训：「轮数不够是我的拆单错误，
        不是模型能力问题」——评测集一度正在犯它自己记下的那个错。
        """
        return not self.failure

    @property
    def severity(self) -> str:
        """答错的性质。**保守错、乱报、说反了，是三件事。**"""
        if self.failure:
            return f"⚠️ 没取到读数（{self.failure}）"
        if self.ok:
            return ""
        if not self.got:
            return "没给判定（格式没对上，不是判断错）"
        # 答 undecidable 一律是保守——它承认自己不知道，没有说错任何事。
        # ⚠️ 早先 holds→undecidable 被打成「说反了」，是三分里落在分类外的那一格。
        if self.got == "undecidable":
            return "保守（漏了但没说错）"
        if self.case.kind == "undecidable":
            return "⚠️ 硬判（判不了却下了结论）"
        if self.case.kind == "holds" and self.got == "broken":
            # holds 类存在的唯一理由就是测这个，它必须有自己的标签
            return "⚠️ 乱报（假阳性：把好的说成坏的）"
        return "⚠️ 说反了（把已失效的说成仍然成立）"


@dataclass
class Run:
    backend: str
    model: str
    started: str
    answers: list[Answer] = field(default_factory=list)

    secs: float = 0.0           # 真墙钟。⚠️ 不是各题耗时之和——并发下那是另一回事
    reports_dir: str = ""       # 一次性目录：⛔ 回执绝不落进被考的仓库

    def by_kind(self) -> dict[str, tuple[int, int]]:
        """⚠️ 分母只含**取到读数**的题：派单失败不该记在被测后端头上。"""
        out = {}
        for k in KINDS:
            sub = [a for a in self.answers if a.case.kind == k and a.counted]
            out[k] = (sum(1 for a in sub if a.ok), len(sub))
        return out

    @property
    def failures(self) -> list[Answer]:
        return [a for a in self.answers if a.failure]

    def confusion(self) -> dict[tuple[str, str], int]:
        """3×3 混淆矩阵。`runner` 的判分口径第 2 条承诺过「混淆矩阵全出」，
        而此前只印得出 by_kind + severity 两个投影。"""
        return Counter((a.case.kind, a.got or "—") for a in self.answers if a.counted)


def _extract(text: str) -> str:
    """取最后一个 VERDICT 行。**取不到就是空串，不猜。**

    ⚠️ 不做「正文里出现了 broken 这个词就算」的模糊匹配——那会把
    「我一开始以为 broken，后来发现 holds」判成 broken。
    """
    m = _VERDICT.findall(text or "")
    return m[-1].lower() if m else ""


class ModelMismatch(RuntimeError):
    """整批作废。与 `check_baseline` 拒跑同源：读数不可信时**不出分**。"""


def run_one(case: Case, backend, paths, *, max_turns: int,
            reports_dir: Path | None = None) -> Answer:
    tmp = Path(naming.stamp() + f"-{case.id}.md")
    spec = TaskSpec(path=tmp, body=(
        f"# 角色\n事实核查员。\n\n# 任务\n{case.prompt()}\n\n"
        f"# 禁令\n- ⛔ 不许创建、修改、删除任何文件\n"
        f"- ⛔ 判不了就答 undecidable，不许猜\n"))
    t0 = time.time()
    res = dispatch_one(spec, paths, backend.worker_config(),
                       tools="readonly", max_turns=max_turns, cwd=BASE_REPO,
                       reports_dir=reports_dir)
    secs = time.time() - t0
    r = res.receipt

    # ⛔ 派单失败的信号一个都不能丢。它们此前被整体降级成「这题答错了」，
    #    记在被测后端头上——而 `--backend X` 的全部理由就是「换后端质量掉没掉」。
    if res.model_mismatch:
        raise ModelMismatch(
            f"⛔ 拒绝出分：{case.id} 实际跑在 {r.models_used if r else '?'}，"
            f"不是声称的 {backend.model}。\n"
            f"   跑在别的模型上的分数不是这个后端的分数——**而它看起来仍像个正常分数**。")
    failure = res.error or (r.why_failed if r else "没有回执")

    text = (r.result if r else "") or ""
    got = "" if failure else _extract(text)

    from ..pricing import real_cost_usd
    return Answer(case=case, got=got, ok=(bool(got) and got == case.kind),
                  cost=real_cost_usd(backend.price_lookup, r.usage) if r else None,
                  secs=secs, turns=(r.num_turns if r else 0),
                  raw_tail=text[-200:] if not got else "",
                  failure=failure)


def run(backend_name: str | None, *, parallel: int = 4,
        max_turns: int = 30, only: str = "") -> Run:
    # ⛔ **纯入参校验排在最前**。题号写错不该等基准检查、后端解析都跑完才报，
    #    与 `_load_specs` 预校验任务书同一条纪律：能在零成本时刻失败的，
    #    就不要留到有成本的时刻。
    #    ⚠️ 这条顺序是 2026-07-28 改默认后端时暴露的——默认从 api 改成
    #    subagent 之后，「题号写错」被「后端 kind 不对」挡在前面，报错答非所问。
    all_cases = load()
    if only:
        want = [x.strip() for x in only.split(",") if x.strip()]
        unknown = sorted(set(want) - {c.id for c in all_cases})
        if unknown:
            # 静默降级会印「总计 0/0」、写进历史、返回 0——再叠加 compare()
            # 只比交集，就成了污染源。照 check_baseline 的写法直接拒跑。
            raise RuntimeError(f"⛔ 没有这些题号：{'、'.join(unknown)}\n"
                               f"   现有题号：{'、'.join(c.id for c in all_cases)}")
        cases = [c for c in all_cases if c.id in want]
    else:
        cases = all_cases
    if not cases:
        raise RuntimeError("⛔ 一题都没选中，不跑。")

    drift = check_baseline()
    if drift:
        raise RuntimeError(
            f"⛔ 拒绝跑分：{drift}\n"
            f"   评测集的答案全部锚在 {BASE_REPO} @ 基准提交上。基准一漂，\n"
            f"   分数就不再是「模型判断力」的度量——**而它看起来仍然像个正常分数**。")

    from ..config import ProjectPaths
    reg = backends.load()
    b = reg.resolve(backend_name)
    if b.kind != "api":
        raise RuntimeError(
            f"⛔ 后端 {b.name} 的 kind={b.kind} 不能用于评测：\n"
            f"   评测要求同一批题在**同一条执行路径**上跑完才可比，\n"
            f"   而 subagent 走的是交接协议（人工参与），两者的结果不可比。")

    paths = ProjectPaths(BASE_REPO)

    # ⛔ 回执落到**一次性目录**，不落基准仓库。工人的 cwd 就是那个仓库、
    #    readonly 预设（Read/Grep/Glob）没有路径白名单——回执落在那里，
    #    上一轮的 20 份答案就是下一轮的可读材料。**这不能靠禁令挡**，
    #    禁令本身还会把答案的位置告诉工人。
    import tempfile
    run_id = naming.stamp()
    reports_dir = Path(tempfile.gettempdir()) / "devloop-eval" / run_id
    reports_dir.mkdir(parents=True, exist_ok=True)

    out = Run(backend=b.name, model=b.model, started=time.strftime("%Y-%m-%dT%H:%M:%S"))
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = [pool.submit(run_one, c, b, paths, max_turns=max_turns,
                            reports_dir=reports_dir) for c in cases]
        for f in cf.as_completed(futs):
            out.answers.append(f.result())
    out.secs = time.time() - t0        # 真墙钟
    out.answers.sort(key=lambda a: a.case.id)
    out.reports_dir = str(reports_dir)
    return out


def format_run(r: Run) -> str:
    L = [f"评测集 · 后端 {r.backend}（{r.model}） · {r.started}", ""]
    L.append(f"{'题':5s} {'应答':12s} {'实答':12s} {'':3s} {'轮':>3s} {'秒':>5s}  性质")
    L.append("─" * 72)
    for a in r.answers:
        mark = "✓" if a.ok else "✗"
        L.append(f"{a.case.id:5s} {a.case.kind:12s} {(a.got or '—'):12s} {mark:3s} "
                 f"{a.turns:>3} {a.secs:>5.0f}  {a.severity}")
    L.append("─" * 72)

    if r.failures:
        L.append("")
        L.append(f"⚠️ **{len(r.failures)} 题没取到读数**（派单本身失败，"
                 f"不计入下面任何分母）：")
        for a in r.failures:
            L.append(f"  {a.case.id}  {a.failure}")

    bk = r.by_kind()
    tot_ok = sum(v[0] for v in bk.values())
    tot = sum(v[1] for v in bk.values())
    L.append("")
    L.append(f"总计 {tot_ok}/{tot}")
    L.append("")
    L.append("⚠️ **总计不是主指标**——20 题里 11 题答案是 broken，"
             "「一律答 broken」也能拿 55%。分类看：")
    for k in KINDS:
        ok, n = bk[k]
        meaning = {"broken": "能不能找出腐烂",
                   "holds": "会不会乱报（假阳性）",
                   "undecidable": "知不知道自己不知道"}[k]
        line = f"  {k:12s} {ok}/{n}   ← {meaning}"
        # ⛔ 小样本必须当场说破，不能让读者自己去把握。实测教训：曾拿
        #    undecidable 上的 2/3 → 1/3 说「质量掉了」，而 Fisher 精确检验 p=1.0、
        #    Clopper-Pearson 区间几乎完全重叠——那句话根本不成立。
        if 0 < n < 5:
            line += f"   ⚠️ n={n}，此类不产生可比结论"
        L.append(line)

    conf = r.confusion()
    if conf:
        L.append("")
        L.append("混淆矩阵（行=应答，列=实答）：")
        cols = list(KINDS) + ["—"]
        L.append("  " + " " * 13 + "".join(f"{c:>13s}" for c in cols))
        for want in KINDS:
            L.append(f"  {want:13s}"
                     + "".join(f"{conf.get((want, g), 0):>13d}" for g in cols))

    sev = Counter(a.severity for a in r.answers if not a.ok)
    if sev:
        L.append("")
        L.append("答错的性质（**保守错和说反了不是一回事**）：")
        for s, n in sev.most_common():
            L.append(f"  {s}  {n} 题")

    costs = [a.cost for a in r.answers if a.cost is not None]
    if costs:
        L.append("")
        # ⚠️ 「各题耗时合计」不是墙钟：并发 4 跑的时候两者差 3 倍以上。
        #    曾把前者印成「墙钟」，于是 9 分 05 秒被记成 29 分钟，还进了 BACKLOG。
        #    一个自己算错的读数比没有读数更坏。
        L.append(f"成本 ${sum(costs):.4f}（{len(costs)}/{len(r.answers)} 题可算）"
                 f" · 单题 ${sum(costs) / len(costs):.4f}"
                 f" · 各题耗时合计 {sum(a.secs for a in r.answers) / 60:.1f} 分钟"
                 + (f" · **墙钟 {r.secs / 60:.1f} 分钟**" if r.secs else ""))
    if r.reports_dir:
        L.append("")
        L.append(f"回执 {r.reports_dir}"
                 "（⛔ 一次性目录，不落基准仓库——否则本轮答案会成为下轮的可读材料）")

    L.append("")
    L.append("⚠️ n=20，只够区分数量级。⛔ 不许把准确率报到小数点后一位，"
             "也不许拿 60% vs 65% 说「变好了」。")
    return "\n".join(L)


def save(r: Run, path: Path, *, only: str = "") -> None:
    """一行一次跑分，供比对趋势。

    ⚠️ 必须记下**这次跑了哪几题**：否则 `compare()` 只能拿两次的交集比，
    而一次 `--only` 的调试跑分会让下一次全量比对静默缩到那一题（见 compare）。
    """
    if not r.answers:
        return                  # 0 题的跑分不进历史——它只会成为污染源
    row = {
        "ts": r.started, "backend": r.backend, "model": r.model,
        "n": len(r.answers), "only": only, "secs": round(r.secs, 1),
        "by_kind": {k: list(v) for k, v in r.by_kind().items()},
        "answers": [{"id": a.case.id, "want": a.case.kind, "got": a.got,
                     "ok": a.ok, "severity": a.severity, "turns": a.turns,
                     "cost": a.cost, "failure": a.failure} for a in r.answers],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def compare(path: Path, n: int = 2) -> str:
    """比最近 n 次跑分。⚠️ 只报**逐题的变化**，不报「平均分涨了多少」。

    ⛔ 两次跑的题目集合不同时，**必须把差集单独说出来**。
    实测过的失效：历史里夹一次 `--only c01`，随后的全量跑分只跟那一题比，
    19 题真回归被原样印成「逐题结果完全一致——没有回归」。
    **这套东西存在的唯一理由就是那句话，而它当时是错的。**
    """
    if not path.exists():
        return "还没有历史跑分。"
    allrows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # ⛔ 被判作废的行不参与趋势比对——它们是在**已知配置错误或脏环境**下跑出来的。
    #    让它们参与，等于拿一个已知不可信的读数当基准。
    #    ⚠️ 但必须**说出来跳过了几行**：静默截断会读成「历史就这么长」（G-51 的纪律）。
    rows = [r for r in allrows if not r.get("invalidated")]
    skipped = len(allrows) - len(rows)
    head = ([f"⚠️ 跳过 {skipped} 次已作废的跑分（在已知配置错误或脏环境下跑的，不可用作基准）", ""]
            if skipped else [])
    if len(rows) < 2:
        return "\n".join(head + [f"只有 {len(rows)} 次可用跑分，无从比对。"])
    a, b = rows[-2], rows[-1]
    L = head + [f"上次 {a['ts']} · {a['backend']}（{len(a['answers'])} 题）",
                f"本次 {b['ts']} · {b['backend']}（{len(b['answers'])} 题）", ""]
    pa = {x["id"]: x for x in a["answers"]}
    pb = {x["id"]: x for x in b["answers"]}

    only_a = sorted(set(pa) - set(pb))
    only_b = sorted(set(pb) - set(pa))
    if only_a or only_b:
        L.append("⚠️ **两次跑的题目不一样，下面只比得了重合的部分**：")
        if only_a:
            L.append(f"  本次没跑，无从比对：{'、'.join(only_a)}")
        if only_b:
            L.append(f"  上次没跑，无从比对：{'、'.join(only_b)}")
        L.append("")

    both = [x for x in b["answers"] if x["id"] in pa]
    changed = [(x["id"], pa[x["id"]], x) for x in both if pa[x["id"]]["ok"] != x["ok"]]
    if not changed:
        L.append(f"重合的 {len(both)} 题结果完全一致——没有回归，也没有改善。"
                 if (only_a or only_b) else
                 "逐题结果完全一致——没有回归，也没有改善。")
    else:
        L.append(f"{len(changed)} 题变了：")
        for cid, old, new in changed:
            arrow = "✓→✗ **回归**" if old["ok"] else "✗→✓ 改善"
            L.append(f"  {cid}  {arrow}  应答 {new['want']}，"
                     f"上次答 {old['got'] or '—'}，本次答 {new['got'] or '—'}")
    return "\n".join(L)
