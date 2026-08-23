"""审计型任务：把工人的**发现**变成可聚合、可复核、可派单的东西。

## ⛔ 这个模块补的是哪一块

2026-08-01 eco-ob 首跑的最大结论（`_trials/eco-ob-01/50_关于工具的结论.md` T-7）：

> **这一天最值钱的产出不是那次派单，是围绕它跑的几轮审计。**

土蚁被冻结 8 天、三物种在第 200/210/370 tick 灭绝、性能超标 21 倍、
M1 的标准答案变成引擎自录、「4 物种」这个过期数字还写在 6 份文档里
——**一条都不来自那次派单**。

⚠️ 而扇出机制**本来就有**：`--tools readonly` 预设（Read/Grep/Glob）、
`--task-dir` 批量、`--parallel` 并发。⛔ 所以本模块**不重造扇出**，只补缺的三样：

| 缺什么 | 后果 |
|---|---|
| 发现是**自由文本** | 没法聚合、没法去重、没法追踪一条发现的下场 |
| 没有**独立复核** | ⭐ 首跑当天它抓出了我 5 条判断里的 4 条错 |
| 发现**变不成任务书** | 查出来的问题要人手抄一遍才能派出去修 |

## ⭐ 为什么复核那一层最要紧

首跑当天我判断错了至少四次，**五条里四条是「把结果当成原因」或
「测量方法本身是错的」**——这类错**自己查不出来**，只有独立视角能抓。

⛔ 所以 `verify_spec()` 生成的任务书**要求推翻，不是要求确认**。
「请确认这条对不对」拿到的永远是「对」。

## 工人怎么交发现

任务书里贴 `FINDINGS_FORMAT`，工人在报告里吐这么一段：

    <<<DEVLOOP-FINDINGS
    - claim: 一句话说清是什么问题
      evidence: 文件路径+符号名，或你实际跑过的命令与真实输出
      confidence: 已核实 | 推断 | 存疑
      severity: 关键 | 重要 | 一般 | 仅记录
    DEVLOOP-FINDINGS>>>

⛔ 四个字段**缺一不可**，缺了就抛 `MalformedFinding`——
静默补默认值等于让一条没有证据的发现混进台账，看起来和真的一样。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace

from . import telemetry as _tele
from pathlib import Path

CONFIDENCE = ("已核实", "推断", "存疑")
SEVERITY = ("关键", "重要", "一般", "仅记录")

_BLOCK = re.compile(r"<<<DEVLOOP-FINDINGS(.*?)DEVLOOP-FINDINGS>>>", re.S)
_ITEM = re.compile(r"^\s*-\s+claim:\s*(.*)$")
_KV = re.compile(r"^\s+(claim|evidence|confidence|severity):\s*(.*)$")

FINDINGS_FORMAT = f"""\
# 怎么交发现

⛔ 报告末尾**必须**有这么一段（没有发现就交一个空块，⛔ 不许省略整段）：

```
<<<DEVLOOP-FINDINGS
- claim: 一句话说清是什么问题
  evidence: 文件路径+符号名，或你**实际跑过**的命令与真实输出
  confidence: {' | '.join(CONFIDENCE)}
  severity: {' | '.join(SEVERITY)}
DEVLOOP-FINDINGS>>>
```

⛔ 四个字段缺一不可。⚠️ `evidence` 里不许写「大概」「应该」——
写不出具体出处就把 `confidence` 标成 `存疑`，⛔ 别编。
⛔ 锚点用符号名不用行号（行号会腐烂）。
"""


class MalformedFinding(ValueError):
    """发现块格式不对。⛔ 必须抛，不许静默跳过——见模块 docstring。"""


@dataclass(frozen=True)
class Finding:
    claim: str
    evidence: str
    confidence: str
    severity: str
    source: str = ""
    #  ⭐ 谁报过这一条。两个独立视角撞上同一条**本身就是证据强度**，⛔ 别丢。
    sources: tuple[str, ...] = ()
    #  None = 还没复核过。⚠️ 「找到 12 条」与「12 条经复核成立」是两回事。
    verified: bool | None = None

    @property
    def id(self) -> str:
        """稳定 id —— 去重和引用都靠它。

        ⚠️ 只吃 `claim`：同一个问题被不同角度描述时证据往往不同，
        拿证据一起算会把同一条算成两条。
        """
        return hashlib.sha256(self.claim.strip().encode("utf-8")).hexdigest()[:12]


def parse(text: str) -> list[Finding]:
    """从工人报告里抠出发现块。找不到块 = 没有发现（合法），返回空表。"""
    m = _BLOCK.search(text or "")
    if not m:
        return []
    out: list[Finding] = []
    cur: dict[str, str] = {}

    def flush() -> None:
        if not cur:
            return
        missing = [k for k in ("claim", "evidence", "confidence", "severity")
                   if not cur.get(k)]
        if missing:
            raise MalformedFinding(
                f"发现条目缺字段 {missing}：{cur.get('claim', '(无 claim)')[:60]}\n"
                f"   ⛔ 四个字段缺一不可。静默补默认值 = 一条没有证据的发现"
                f"混进台账，看起来和真的一样。")
        if cur["confidence"] not in CONFIDENCE:
            raise MalformedFinding(
                f"confidence 只能是 {CONFIDENCE}，实得 `{cur['confidence']}`")
        if cur["severity"] not in SEVERITY:
            raise MalformedFinding(
                f"severity 只能是 {SEVERITY}，实得 `{cur['severity']}`")
        out.append(Finding(**cur))
        cur.clear()

    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        head = _ITEM.match(line)
        if head:
            flush()
            cur["claim"] = head.group(1).strip()
            continue
        kv = _KV.match(line)
        if kv and cur:
            cur[kv.group(1)] = kv.group(2).strip()
    flush()
    return out


def load(path: Path) -> list[Finding]:
    """读台账并**在读侧折叠**重复条目。

    ⭐ 台账是**只追加**的（见 `append`），所以同一条 claim 可能有多行——
    每行带一个 `source`。这里按 id 折叠，把 `sources` 合起来。
    ⚠️ 与 telemetry 同一条纪律：**账本只追加，聚合在读侧做。**
    """
    if not path.exists():
        return []
    merged: dict[str, Finding] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        d.pop("id", None)
        srcs = tuple(d.pop("sources", None) or ())
        f = Finding(**d)
        old = merged.get(f.id)
        if old is None:
            merged[f.id] = replace(f, sources=srcs or ((f.source,) if f.source else ()))
        else:
            add = srcs or ((f.source,) if f.source else ())
            keep = old.sources + tuple(x for x in add if x not in old.sources)
            #  ⚠️ 复核结论以**最后写入**的为准——它是最新的判断。
            merged[f.id] = replace(old, sources=keep,
                                   verified=f.verified if f.verified is not None
                                   else old.verified)
    return list(merged.values())


def append(path: Path, findings: list[Finding], *, source: str) -> int:
    """写台账。⛔ **真追加，不是整文件重写。** 返回这次新增了几条**不重复的**。

    ## ⛔ 为什么必须是真追加（2026-08-02 实测撞到的阻断）

    第一版是「读全文 → 内存合并 → `write_text` 整个覆盖」。而宪法 T5 的第四道
    判据（`records.verify`）判的是「**只追加**」——前 N 字节的指纹必须一模一样。

    于是第二个审计角度报出**同一条 claim** 时（⚠️ 本模块 docstring 自己写着
    「撞上同一条是**常态**」），那行的 `sources` 变长 → 后面全部移位 →
    守卫判「前缀被改写（抹掉历史再补）」。

    ⛔ 后果：一批里第 1 单收了条重复发现，**第 2 单就被宪法判失败**；
    自动驾驶下 `snap_before` 整个阶段只采一次，**剩下的单全部连坐**。
    ⚠️ 与刚修掉的 G-71 是同一个形状，只是这次的肇事者是 findings 自己。

    ## ⚠️ 并发

    只读审计的**默认形态就是并发 4**（`fanout.DEFAULT_READONLY`），
    多个工人同时往这里写。⛔ 第一版没有锁，实测 8 路并发只活下来 1 条。
    ⭐ 直接复用 `telemetry._append`：进程内线程锁 + 跨进程文件锁 + fsync。
    """
    have = {f.id for f in load(path)}
    added = 0
    lines = []
    for f in findings:
        f = replace(f, source=f.source or source, sources=(source,))
        lines.append(json.dumps(
            {**f.__dict__, "sources": list(f.sources), "id": f.id},
            ensure_ascii=False) + "\n")
        if f.id not in have:
            added += 1
            have.add(f.id)
    if lines:
        _tele._append(path, "".join(lines))
    return added


def summary(findings: list[Finding]) -> str:
    """⛔ 未复核的一定要标出来——「找到 N 条」不等于「N 条成立」。"""
    if not findings:
        return "没有发现。⚠️ 那可能意味着真没问题，也可能意味着**问的角度不对**。"
    by = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY}
    head = " · ".join(f"{s} {n}" for s, n in by.items() if n)
    un = sum(1 for f in findings if f.verified is None)
    ok = sum(1 for f in findings if f.verified is True)
    no = sum(1 for f in findings if f.verified is False)
    tail = f"⚠️ **未复核 {un} 条**" if un else ""
    if ok or no:
        tail += f"（复核成立 {ok} · 被推翻 {no}）"
    both = [f for f in findings if len(f.sources) > 1]
    if both:
        tail += f" · ⭐ {len(both)} 条被两个以上角度独立报出"
    return f"{len(findings)} 条：{head}　{tail}"


def _spec(role: str, task: str, bans: list[str]) -> str:
    """按 `TaskSpec.load` 认的三段式拼任务书。⛔ 三个整行标题缺一不可。"""
    return (f"# 角色\n\n{role}\n\n# 任务\n\n{task}\n\n# 禁令\n\n"
            + "\n".join(f"- {b}" for b in bans) + "\n")


def verify_spec(f: Finding) -> str:
    """生成**复核**任务书。⛔ 要求推翻，不是要求核对。

    ⭐ 首跑当天我 5 条判断错了 4 条，全是被「要求推翻」的复核抓出来的；
    而「请核对这条对不对」拿到的永远是「对」。
    """
    return _spec(
        role="你是一个独立的事实核查员。⛔ 你的默认立场是「下面这条结论是错的」。",
        task=(f"有人在审计中报了这么一条：\n\n"
              f"> **结论**：{f.claim}\n"
              f"> **他给的证据**：{f.evidence}\n"
              f"> **他标的把握**：{f.confidence} · **严重度**：{f.severity}\n\n"
              f"去**推翻**它。打开文件、跑 grep、跑命令，自己找反证。\n\n"
              f"⚠️ 只有当你查过之后**确实找不到任何反证**，才判它站得住。\n"
              f"⛔ 不许因为「听起来合理」就放过。\n"
              f"⛔ 不许复述他的证据当作自己的验证——那不是核查，是转述。\n\n"
              f"报告里必须写清：**你实际查了什么、看到了什么**；"
              f"若它错了，**正确的说法是什么**。\n\n{FINDINGS_FORMAT}"),
        bans=["⛔ **只读。** 不许改任何文件，不许 git commit / add / checkout。",
              "⛔ 不许猜。查不到就写「查不到」，⚠️ 不许用「大概」「应该」填空。",
              "⛔ 锚点用符号名不用行号（行号会腐烂）。",
              "⛔ 不许把原结论的措辞抄一遍当成核查过程。"])


def fix_spec(f: Finding) -> str:
    """把一条发现变成**修复**任务书。⭐ 证据原样带进去，工人不用重查一遍。"""
    return _spec(
        role="你是这个项目的维护者。你在一个基于干净检出的独立 worktree 里干活，"
             "改完之后会有一组闸自动裁决。",
        task=(f"审计发现了下面这个问题，去修它。\n\n"
              f"> **问题**：{f.claim}\n"
              f"> **证据**：{f.evidence}\n"
              f"> **把握**：{f.confidence} · **严重度**：{f.severity}\n\n"
              f"⛔ **动手之前先自己复验一遍上面的证据。**\n"
              f"   证据对不上就**立刻停下**，什么都不要改，在报告里写明哪里对不上"
              f"——**那样这一单算完成，不算失败**。\n\n"
              f"⚠️ 只修这一条。改动范围越小越好。\n\n{FINDINGS_FORMAT}"),
        bans=["⛔ **不许「顺手」修别的东西。** 你一定会看到别的问题"
              "——看到就写进发现块，**一个字都不许改**。",
              "⛔ 不许改测试来让自己过关（改测试让自己变绿不算通过）。",
              "⛔ 不许 git commit / push / add——产出由编排方在跑完闸之后代为固化。"
              "你自己提交会让闸的守卫退化成空守卫。",
              "⛔ 不许静默降级：命令跑不通、文件找不到、条件确认不了，"
              "**停下并在报告里写明**，不许退回默认值继续跑。"])
