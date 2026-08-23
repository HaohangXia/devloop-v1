"""`subagent` 后端的交接协议。

**为什么需要交接，不能直接调**：子代理只能由编排方（Claude 会话）通过工具调用起，
Python 起不了。而编排方正是通过 Bash 工具调用 Python 的那个会话——如果 Python
阻塞等待，会话就卡在 Bash 调用里、根本开不出子代理。**死锁。**

所以拆成两段，控制权交还会话：

```
devloop dispatch --backend subagent-…   →  写工单，退出码 3「批次就绪」
        （编排方读工单，并行起子代理，把结果写进 results/）
devloop collect  --project …            →  收单：跑闸 → 固化产出 → 记台账
```

**回执格式必须与 API 后端一致** —— 这是整个设计的收敛点。谁能产出那个格式，
谁就是合法后端；台账、闸、汇总一行都不用改。

⛔ **退出码 3 不能用 0 顶替**：0 的意思是「全都成功了」，脚本看到 0 会往下走，
而此时一个字都还没干。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import naming
from .backends import Backend, BackendError
from .models import Receipt, TaskSpec

BATCH = "BATCH.json"
ORDER = "ORDER.md"


@dataclass(frozen=True)
class Batch:
    root: Path
    meta: dict

    @property
    def id(self) -> str:
        return self.meta["batch_id"]

    @property
    def units(self) -> list[str]:
        return list(self.meta["units"])

    def prompt_of(self, unit: str) -> Path:
        return self.root / "units" / f"{unit}.prompt.txt"

    def result_of(self, unit: str) -> Path:
        return self.root / "results" / f"{unit}.json"

    def pending(self) -> list[str]:
        return [u for u in self.units if not self.result_of(u).exists()]

    @property
    def done(self) -> bool:
        return not self.pending()


def spool_root(project: Path) -> Path:
    return project / ".devloop" / "handoff"


def guard_write_tools(backend: Backend, tools: str) -> None:
    """⛔ 禁止 `subagent` + 写操作 —— 这是一条静默假绿通道。

    子代理**跑在编排方的工作目录里**，无法被放进隔离 worktree。若允许写操作：
    工人的改动会落进**用户的活工作区**，而 worktree 空转、`git status` 干净、
    三道守卫全 PASS、闸全绿、台账记 `ok=true`——**而真实文件已经被改了**。

    这正是本项目存在的理由所要防的那类事（「假绿」第七种形态）。
    宁可不支持，也不给一条看起来能用、实际在骗人的路。
    """
    if backend.kind == "subagent" and tools != "readonly":
        raise BackendError(
            f"⛔ 后端 {backend.name}（kind=subagent）只支持 --tools readonly。\n"
            f"   子代理跑在编排方的工作目录里，进不了隔离 worktree。允许写操作会让改动\n"
            f"   落进你的**活工作区**，而 worktree 空转、闸全绿、台账记成功——静默假绿。\n"
            f"   写操作请用 kind=api 的后端（它们以子进程运行，能被放进 worktree）。")


def create(project: Path, backend: Backend, tasks: list[TaskSpec],
           prompts: dict[str, str], *, tools: str, max_turns: int) -> Batch:
    """写一个待办批次，返回它。⛔ 不派单——派单是编排方的事。"""
    guard_write_tools(backend, tools)

    bid = naming.stamp()
    root = spool_root(project) / bid
    (root / "units").mkdir(parents=True)
    (root / "results").mkdir()

    for t in tasks:
        (root / "units" / f"{t.name}.prompt.txt").write_text(prompts[t.name], encoding="utf-8")

    meta = {
        "batch_id": bid,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": str(project),
        "backend": backend.name,
        "kind": backend.kind,
        "model": backend.model,          # 声称值——收单时会与实际用的模型核对
        "price_key": backend.price_lookup,
        "agent_type": backend.agent_type,
        "max_parallel": backend.max_parallel,
        "tools": tools,
        "max_turns": max_turns,
        "units": [t.name for t in tasks],
    }
    (root / BATCH).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / ORDER).write_text(_order_md(root, meta), encoding="utf-8")
    return Batch(root, meta)


def _order_md(root: Path, m: dict) -> str:
    """给编排方看的工单。写清楚「怎么算干完了」，别让它自己猜。"""
    units = "\n".join(f"| `{u}` | `units/{u}.prompt.txt` | `results/{u}.json` |" for u in m["units"])
    return f"""# 待办批次 `{m['batch_id']}`

> 这是 `subagent` 后端的**工单**。Python 已经把活准备好了，但它起不了子代理——
> 子代理只能由编排方（Claude 会话）通过工具调用来起。

| | |
|---|---|
| 项目 | `{m['project']}` |
| 后端 | `{m['backend']}`（kind=`{m['kind']}`，model=`{m['model']}`） |
| 权限 | `{m['tools']}` |
| 建议并发 | {m['max_parallel']} |
| 单元数 | {len(m['units'])} |

## 编排方要做的三件事

1. **逐个读 `units/<单元>.prompt.txt`**，把**全文原样**作为子代理的 prompt。
   ⛔ 不要改写、不要加前言、不要总结——它已经是最终形态（与 API 后端逐字节同源）。
2. **并行起子代理**，建议并发 {m['max_parallel']}。
3. **把每个子代理的返回文本写进 `results/<单元>.json`**，格式见下。

## 结果文件格式

```json
{{
  "result": "<子代理返回的完整文本，一个字不要删>",
  "is_error": false,
  "num_turns": 0,
  "duration_ms": 0,
  "modelUsage": {{ "<实际模型名>": {{}} }},
  "usage": {{ "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0 }}
}}
```

- **`result` 是唯一必填项。** 其余缺省即可——收单时会补 0，并在台账里标注
  `usage_unknown`，**不会假装知道成本**。
- `modelUsage` 的键若与 `{m['model']}` 不符，收单会判失败（**别假定用了哪个模型**）。
- token 若能从会话记录取到，请填真值——⚠️ 按 `message.id` 去重时**取最后一帧**，
  子代理会话是流式落盘的，取首帧会把输出少算约 9 倍（BACKLOG G-42）。

## 单元清单

| 单元 | prompt | 结果写到 |
|---|---|---|
{units}

## 干完之后

```bash
python -m devloop.cli collect --project {m['project']}
```

它会：跑闸 → 固化产出 → 记台账。**在此之前这批活等于没干**——
`dispatch` 返回的退出码 3 就是「批次就绪，等编排方」，不是成功。
"""


def find_open(project: Path, batch_id: str | None = None) -> Batch:
    """找待收的批次。多于一个且没指定 → 报错，不猜。"""
    root = spool_root(project)
    if not root.exists():
        raise BackendError(f"没有待收批次（{root} 不存在）")
    if batch_id:
        p = root / batch_id
        if not (p / BATCH).exists():
            raise BackendError(f"找不到批次 {batch_id}")
        return Batch(p, json.loads((p / BATCH).read_text(encoding="utf-8")))
    cands = sorted(d for d in root.iterdir() if (d / BATCH).exists())
    if not cands:
        raise BackendError(f"{root} 下没有批次")
    if len(cands) > 1:
        raise BackendError(
            "有多个待收批次，请用 --batch 指定：\n  " + "\n  ".join(d.name for d in cands))
    return Batch(cands[0], json.loads((cands[0] / BATCH).read_text(encoding="utf-8")))


def read_result(batch: Batch, unit: str) -> tuple[Receipt, str]:
    """把编排方写回的结果读成 `Receipt`，返回 (回执, 告警)。

    **归一化在这里发生**，于是下游（闸 / 台账 / 汇总）完全不知道这单是子代理干的。
    """
    f = batch.result_of(unit)
    raw = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(raw.get("result"), str) or not raw["result"].strip():
        raise BackendError(f"{f} 的 result 字段为空——子代理没产出任何东西？")

    warn = ""
    usage = raw.get("usage") or {}
    if not any(usage.get(k) for k in
               ("input_tokens", "output_tokens", "cache_read_input_tokens")):
        # ⚠️ 不猜。台账的 cost_usd_real 会是 null，摘要会如实说「算不出」。
        warn = "usage 全为 0 —— 成本无法计算，台账将标注为未知"

    return Receipt(
        is_error=bool(raw.get("is_error", False)),
        result=raw["result"],
        session_id=raw.get("session_id", ""),
        total_cost_usd=0.0,       # ⛔ 子代理没有权威成本字段，一律 0，靠 token 重算
        duration_ms=int(raw.get("duration_ms", 0)),
        num_turns=int(raw.get("num_turns", 0)),
        usage=usage,
        modelUsage=raw.get("modelUsage") or {},
        subtype=raw.get("subtype", ""),
        terminal_reason=raw.get("terminal_reason", ""),
        errors=raw.get("errors") or [],
    ), warn
