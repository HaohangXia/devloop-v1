"""复核卷宗：每一单跑完都留一份**只列事实**的材料。

## ⛔ 这个模块补的是哪一块

`cli.py` 里 `write_escalation` 的唯一调用点条件是 `stop.kind == "escalation"`
——**只有重试耗尽的失败单**才拿到结构化材料（每次尝试、失败模式、成本、
闸的原文、任务书路径）。

⛔ 而**即将进用户主线**的那些成功单，留下的只有：

| | |
|---|---|
| 提交正文 | **纯样板**：「由 DevLoop 编排方代为提交，仅落在隔离分支…未触碰主线」 |
| 回执 | `.devloop/reports/*.json` 实测 **172 KB / 47 行**原始 SDK 事件流 |

⭐ 这个不对称就是「复核一条 ≈ 重写一条」的机制：人被迫从样板提交
+ 172 KB 逐帧流里，**自己重建**「闸到底验了什么、没验什么」。

**实证**（2026-08-02 复核 `v1-ledger-fields`，**闸五道全绿**）：
变异测试 ×3、边界探针 ×2、真台账对账、红检 ×3 → **抓出 3 个洞**；
工人产出 +156 行，落到主线 +306 行。四视角合议 **0 票直接合**。

## ⛔ 卷宗只列事实，一个裁决词都不许有

⚠️ 一份写得漂亮的卷宗让人**跳过**真正的复核——那就是新的假绿。
措辞纪律照抄交接单现成的那句：**「判据只有闸和宪法，交接单不裁决任何东西」**。

⭐ 所以它回答的全是**可证伪的事实**：

- 闸**实际报出**了哪几道 vs 验收**点名**的是哪几道，差在哪
- 这条分支**实际碰了**哪些文件 vs 任务书**声明**的 `# 改动范围`，差在哪
- 分段耗时 / 轮数 / 成本 / 闸的结构化判定

⛔ 唯一「算」出来的是「越界文件」，⚠️ 而它仍然是事实不是判断。

## ⚠️ 落盘位置

写进 `.devloop/dossier/`——⛔ 必须是 `records.RUNTIME_DIRS` 里的目录之一。
理由：`.devloop/findings.jsonl` 就因为没进 gitignore 而让宪法 A-2
**全阶段连坐**（G-93）；而 `RUNTIME_DIRS` 是已经被定性为「运行产物」的地方，
记录守卫与 gitignore 都认它们。
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from .config import ProjectPaths

#  ⭐ 必须在 `records.RUNTIME_DIRS` 里。⛔ 换目录之前先读那份清单。
DIR = "dossier"


def _sec(title: str) -> list[str]:
    return ["", f"## {title}", ""]


def build(paths: ProjectPaths, spec, row: dict, *,
          gate_names: list[str], required: list[str],
          changed: list[str], base: str, sha: str,
          gate_lines: list[tuple[str, str]] | None = None) -> str:
    """拼一份卷宗。⛔ **纯函数**——判据要能直接喂假数据量。

    ⚠️ `spec` 是 `models.TaskSpec`；`row` 是台账那一行；
    `gate_names` 是闸**实际报出**的道名；`required` 是验收**点名**的；
    `changed` 是这条分支**实际改**的文件；
    `gate_lines` 是 **(道名, 判定) 的清单**——⛔ 不是字典。

    ⚠️ 为什么必须是清单：闸可以对**同一道名打两行**（「第一次没过→重跑→过了」
    是很自然的写法，`SPEC.md` 里没有一条禁止）。字典会让后一行盖掉前一行，
    ⛔ 前面那个 FAIL 被静默吞掉 —— 那正好把下面这段要堵的洞从侧门放回来。
    """
    declared = list(getattr(spec, "scope", None) or [])
    L = [f"# 复核卷宗 · {row.get('task', '?')}", "",
         "⛔ **本卷宗不裁决任何东西。** 判据只有闸和宪法。",
         "⚠️ 下面全是**可证伪的事实**——每一条都能自己去核。",
         "",
         f"分支尖端 `{sha[:12]}` · 基准 `{base[:12]}`"]

    # ── 闸 ────────────────────────────────────────────────────
    L += _sec("闸")
    #  ⭐ 「点名了 N 道」与「闸实际报出 N 道」是两件事：
    #     点名一道闸**没报的**，那道就**没验过**（第①种假绿）。
    missing = [n for n in required if n not in gate_names]
    extra = [n for n in gate_names if n not in required]
    L.append(f"- 闸**实际报出**：{'、'.join(gate_names) or '⚠️ 一道都没有'}")
    L.append(f"- 验收**点名**：{'、'.join(required) or '⚠️ 没点名任何闸'}")
    if missing:
        L.append(f"- ⛔ **点名了但闸没报**：{'、'.join(missing)}"
                 f"——那几道**没验过**")
    if extra:
        #  ⛔⛔ 2026-08-15 订正：这里以前写「⚠️ 它们红了不影响验收结论」——**是假的**，
        #     而且是**最贵的那种假**：它明着叫人别看一道真的会拦下这一单的闸。
        #
        #  ⭐ 从头推一遍（`gates.py::run_gates`）：
        #     ① `require_pass` 那一段满足之后，最后一行是
        #        `return GateResult(code, ...)` —— **原样透传脚本的退出码**；
        #     ② 模板的 `bad()` 置 `fail=1`，脚本 `exit $fail` ⇒ 任何一道 FAIL 都让 code=1；
        #     ③ 就算脚本印了 FAIL 却 `exit 0`，上面那段「自相矛盾」会判 **2**。
        #     ⇒ 两条路都不放行。**`require_pass` 是「至少这几道要过」，不是「只看这几道」。**
        #
        #  ⚠️ 只有 `skip()` / `void()` 不置 `fail=1` ⇒ 没点名的 SKIP/VOID 才真的不影响。
        #  ⭐ 实测（红检+绿检各一次）：同一份闸，那道**没点名**的从 FAIL 改成 PASS，
        #     gate code 1 → 0。唯一的差别就是它，而它翻转了结论。
        #  ⛔⛔ 2026-08-16 二次订正：第一版把没点名的分成**两拨**（红 / SKIP·VOID），
        #     于是**验过而且通过**的那些被归进后一拨，印成「都是 SKIP/VOID——没验」。
        #     ⚠️ 那是把上面那句假话换了个方向再说一遍：
        #     原来是「没验的说成没事」，⛔ 改完变成「**验过没事的说成没验**」。
        #     ⭐ 而 `require_pass` 缺省是空的 ⇒ **每一道闸都落进 extra** ⇒
        #     几乎每一单都会印出这句，紧挨着的下一行还写着「N 过 0 未过」，
        #     一份卷宗自己跟自己打架。**一根喊过狼的火警，真着火时没人理。**
        #  ⇒ 必须分**三拨**，因为事实本来就有三种。
        per: dict[str, list[str]] = {}
        for n, vd in (gate_lines or []):
            per.setdefault(n, []).append(vd)

        def _cls(n: str) -> str:
            vs = per.get(n)
            if not vs:
                return "unknown"          # ⚠️ 编排方没传——「不知道」，不是「没事」
            if "FAIL" in vs:
                return "fail"             # ⭐ 任一行 FAIL 就算红（同名多行不许被盖掉）
            if all(x == "PASS" for x in vs):
                return "pass"
            return "vacuous"              # SKIP/VOID（或掺着它们）＝ 没验

        red = [n for n in extra if _cls(n) == "fail"]
        unknown = [n for n in extra if _cls(n) == "unknown"]
        okd = [n for n in extra if _cls(n) == "pass"]
        vac = [n for n in extra if _cls(n) == "vacuous"]
        if red:
            L.append(f"- ⛔ **闸报了红、但验收没点名**：{'、'.join(red)}"
                     f"——⚠️ **照样判这一单不通过**。"
                     f"点名的意思是「至少这几道要过」，⛔ 不是「只看这几道」")
        if unknown:
            #  ⛔ 「不知道」不许印成「没事」，⭐ 也不许印成「确定红了」。
            L.append(f"- ⚠️ **编排方没把判定传过来**：{'、'.join(unknown)}"
                     f"——⛔ 这里**按最坏情况当红算**，"
                     f"但这是**猜的**，不是「查过没事」")
        if okd:
            L.append(f"- 闸报了但没被点名：{'、'.join(okd)}"
                     f"（⭐ **验过而且通过**——没点名不等于没验）")
        if vac:
            L.append(f"- 闸报了但没被点名：{'、'.join(vac)}"
                     f"（⚠️ SKIP/VOID＝**没验**，⛔ 不是「验过没事」）")
    gc = row.get("gate_code", "（老行，无此字段）")
    L.append(f"- 结构化判定 `gate_code` = `{gc}`"
             f"（0 全过 · 1 有未过 · 2 闸自身故障 · null 没跑闸）")
    if row.get("gate_detail"):
        L.append(f"- 闸自己说的：`{row['gate_detail']}`")

    # ── 改动范围 ──────────────────────────────────────────────
    L += _sec("改动范围：声明 vs 实际")
    if not declared:
        #  ⛔ 「没写」与「写了且没越界」是两件事。`# 改动范围` 是可选段
        #     （`models.TaskSpec`），本仓 15 份任务书里只有 3 份写了。
        #     ⚠️ 把「没数据」和「零」混成一件事，是判据维度错。
        L.append("- 声明：⚠️ **无声明**（任务书里没有 `# 改动范围` 段）"
                 "——⛔ 因此**无从判断**有没有越界，不是「越界 0 个」")
    else:
        L.append(f"- 声明：{'、'.join(f'`{d}`' for d in declared)}")
    L.append(f"- 实际改了 {len(changed)} 个："
             + ("、".join(f"`{c}`" for c in changed) or "⚠️ 一个都没有"))
    if declared:
        pats = _declared_paths(declared)
        if not pats:
            #  ⛔ 「抽不出路径」≠「没越界」——第①种假绿：守卫的目标不存在。
            L.append("- ⚠️ **越界判不了**：声明那几行里抽不出路径"
                     "（写的是给人看的话，机器比不了）"
                     "——⛔ 别读成「没越界」，请自己对着上面两行看")
        else:
            over = [c for c in changed if not _in_scope(c, pats)]
            if over:
                L.append(f"- ⛔ **越界 {len(over)} 个**："
                         + "、".join(f"`{c}`" for c in over))
            else:
                L.append(f"- 无越界（比对用的路径："
                         + "、".join(f"`{t}`" for t in pats) + "）")

    # ── 这一单花了什么 ────────────────────────────────────────
    L += _sec("这一单花了什么")
    seg = " · ".join(
        f"{k[:-2]} {row[k]:.1f}s" for k in ("setup_s", "worker_s", "gate_s")
        if isinstance(row.get(k), (int, float)))
    L.append(f"- 分段：{seg or '⚠️ 没记'}")
    L.append(f"- 轮数 {row.get('turns', '?')} · "
             f"缓存命中 {row.get('cache_read_tokens', 0)} tok")
    c = row.get("cost_usd_real")
    L.append(f"- 成本：{'$%.4f' % c if isinstance(c, (int, float)) else '⚠️ **算不出**（价目表里没有这个模型）'}")
    if row.get("error"):
        L.append(f"- 台账记的失败原因：\n\n  ```\n  {row['error']}\n  ```")

    # ── 怎么自己核 ────────────────────────────────────────────
    L += _sec("怎么自己核")
    L += ["```bash",
          f"git show {sha[:12]}                 # 看这一单的产出",
          f"git diff {base[:12]} {sha[:12]}     # 看它相对基准改了什么",
          "```", "",
          "⚠️ 闸绿只说明**它自己写的测试过了**——⛔ 不说明做对了。",
          "⭐ 2026-08-02 实测：一条**五道闸全绿**的产出，复核时抓出 3 个洞。"]
    return "\n".join(L) + "\n"


#  ⚠️ 只留「看着像路径」的：斜杠、点、字母数字、下划线、连字符、通配符。
_PATHLIKE = re.compile(r"[A-Za-z0-9_.\-*/]+")


def _declared_paths(declared: list[str]) -> list[str]:
    """从**写给人看的**声明行里抽出机器能比的路径。

    ⛔⛔ 2026-08-16：这一步以前不存在，`_in_scope` 直接拿声明**整行原文**去比。
    而真任务书里那一行长这样：

        - `game/data/species.json`（加 4 个 `breed_cost`）

    ⇒ 跟 git 吐出的裸路径 `game/data/species.json` **永远不相等**，
    ⛔ 于是**真实任务书上 100% 误报越界**——把明明允许改的文件全列成越界。

    ⚠️ 为什么以前没人发现：`changed` 恒为空表（那正是 G-130 修的毛病），
    `over` 跟着恒为空，这段代码**从没被真数据触发过**。
    ⭐ G-130 一通电，它第一枪就打在自己人身上 ——
    **修好一个洞会让下游另一个洞第一次开火**，这是本项目第二次撞到。

    ⭐ 抽法依本项目惯例：路径用反引号包。抽不出来就**明说判不了**，
    ⛔ 不许拿整行硬比 —— 假红比假绿贵：红灯亮多了人就不看它了。
    """
    out: list[str] = []
    for d in declared:
        toks = re.findall(r"`([^`]+)`", d) or [d]
        for t in toks:
            t = t.strip().replace("\\", "/")
            #  ⛔ 反引号里也可能是**符号名**（`_try_breed`、`spawn_at`）——不是路径
            if not t or " " in t or not _PATHLIKE.fullmatch(t):
                continue
            if "/" not in t and "." not in t:
                continue
            out.append(t.rstrip("/"))
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def _in_scope(path: str, declared: list[str]) -> bool:
    """⚠️ 声明可以是文件、目录前缀，也可以是通配符。⛔ 判据宽一点——
    这一格的用途是**提示人去看**，误报会训练人忽略它。

    ⚠️ 传进来的必须是 `_declared_paths` 抽过的裸路径，⛔ 不是声明原文。
    """
    p = path.replace("\\", "/")
    return any(p == d or p.startswith(d.rstrip("/") + "/") or fnmatch(p, d)
               for d in (x.replace("\\", "/") for x in declared))


def write(paths: ProjectPaths, stage: str, spec, row: dict, **kw) -> Path:
    """把卷宗落盘。返回路径。"""
    d = paths.project / ".devloop" / DIR
    d.mkdir(parents=True, exist_ok=True)
    #  ⛔⛔ 2026-08-16：文件名以前只有 `{stage}-{task}`，⇒ **同一个任务的两份卷宗
    #     互相覆盖**。⚠️ 这与并行无关，重试那条路**天天走**：
    #     同一份任务书跑两次，第一遍的卷宗被第二遍悄悄盖掉、屏幕零提示。
    #  ⭐ 加单号（`unit_id`）来分开。⛔ **不能只用单号**：那样目录里是一排时间戳，
    #     人得一份份打开才知道哪份是哪份 —— ⚠️ 而屏幕上那句「卷宗在这里」
    #     一个字都没存盘（G-133 的 6b），跑完对应关系就没了。
    #  ⚠️ 老行没有 `unit_id`（G-108 之前），那时退回旧名 —— ⛔ 别造一个假的。
    uid = str(row.get("unit_id") or "").strip()
    stem = f"{stage}-{row.get('task', 'unknown')}" + (f"-{uid}" if uid else "")
    f = d / f"{stem}.md"
    f.write_text(build(paths, spec, row, **kw), encoding="utf-8")
    return f
