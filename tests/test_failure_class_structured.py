"""⛔ 失败归类必须读**结构化字段**，不许猜自由文本。

## 复现（2026-08-02 对抗复核抓到，比它要修的那条更严重）

本仓 `.devloop/gates.sh` 的 pytest 闸写的是
`bad "pytest" "$(... grep -E '^(FAILED|ERROR)' ...)"`
——⭐ **FAIL 的 detail 就是 pytest 的测试节点名**。

而同一天新增的测试叫 `test_闸自身故障要归到闸自身故障`。于是工人把它改挂：

    闸判 code=1（正确：活没干好）
    台账 error = 闸未通过：1 过 / 1 未过：pytest（FAILED …::test_闸自身故障要归到闸自身故障）
    ⛔ 归类成「闸自身故障」→ 建议「先修环境再谈验收」

⛔ 那条「code 1 的说辞里不许出现『闸自身故障』四个字」的纪律，
**被这次提交自己新增的测试名破掉了**。而 `.devloop/plans/` 里五份计划
全都点名 `pytest`，所以这是自动驾驶跑本项目的**主路径**，不是边角。

⚠️ 第二个受害者同形：`test_零改动不许落进兜底档` 挂掉 → detail 含「零改动」
→ 归类成「工人零改动」→ 建议「任务书没让工人知道要动哪个文件」。

## ⭐ 结构性修复：台账落 `gate_code`

`escalation` 的标题一直自称「只从结构化字段取」，而实现是 `in` 匹配。
现在真的给它一个结构化字段：`telemetry.record(..., gate_code=)`。
⚠️ 字符串匹配保留为**没有 gate_code 时**的退路（历史台账行没有这个字段）。
"""

from __future__ import annotations

import json

from devloop import autopilot
from devloop.config import ProjectPaths
from devloop.plan import Budget, StagePlan, Task

HIJACK = ("闸未通过：1 过 / 1 未过：pytest（FAILED "
          "tests/test_escalation_classify.py::test_闸自身故障要归到闸自身故障）")
HIJACK2 = ("闸未通过：1 过 / 1 未过：pytest（FAILED "
           "tests/test_escalation_classify.py::test_零改动不许落进兜底档）")


def _mk(tmp_path, rows: list[dict]):
    proj = tmp_path / "p"
    (proj / ".devloop" / "tasks").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    paths = ProjectPaths(proj)
    paths.telemetry.write_text("".join(
        json.dumps({"ts": f"2026-08-02T0{i}:00:00", "task": "t", "ok": False,
                    "gate_ok": False, "cost_usd_real": 0.0, "duration_s": 1,
                    "turns": 1, **r}, ensure_ascii=False) + "\n"
        for i, r in enumerate(rows)), encoding="utf-8")
    task = Task(id="t", tools="implement", retries=2, require_pass=("g1",))
    plan = StagePlan(id="s", goal="g", task_dir=proj / ".devloop" / "tasks",
                     base="HEAD",
                     budget=Budget(total_usd=1.0, reserve_usd=0.1,
                                   max_dispatches=9, max_wall_min=30),
                     tasks=(task,), require_pass=("g1",),
                     source=proj / ".devloop" / "plans" / "s.toml")
    return autopilot.escalation(paths, plan, task, "2026-08-01T00:00:00")


def test_测试名里的关键字不许劫持归类(tmp_path) -> None:
    """⛔ 闸判 1（活没干好），归类必须是「闸未通过」。"""
    md = _mk(tmp_path, [{"error": HIJACK, "gate_code": 1}] * 2)
    assert "闸未通过（活没达标）" in md
    assert "先修环境再谈验收" not in md, \
        "⛔ 被测试名劫持成「闸自身故障」了——建议方向整个反了"


def test_零改动这个词也不许被劫持(tmp_path) -> None:
    md = _mk(tmp_path, [{"error": HIJACK2, "gate_code": 1}] * 2)
    assert "闸未通过（活没达标）" in md
    assert "工人零改动" not in md


def test_闸真坏了仍然归对(tmp_path) -> None:
    """⚠️ 反向钉住：别为了防劫持把真的闸故障也吃掉。"""
    md = _mk(tmp_path, [{"error": "闸未通过：闸自身故障：退出码 127",
                         "gate_code": 2}] * 2)
    assert "闸自身故障" in md and "先修环境" in md


def test_没有gate_code的历史行退回字符串匹配(tmp_path) -> None:
    """⚠️ 台账是只追加的，历史行没有这个字段。⛔ 不许因此崩，也不许全归到「其它」。"""
    md = _mk(tmp_path, [{"error": "闸未通过：闸自身故障：退出码 127"}] * 2)
    assert "闸自身故障" in md


def test_零改动仍然有自己一档(tmp_path) -> None:
    """⚠️ 它不带 gate_code（闸是绿的，失败在编排侧）。"""
    md = _mk(tmp_path, [{"error": "写任务零改动：worktree 里一个文件都没变",
                         "gate_ok": True}] * 2)
    assert "零改动" in md and "其它（见上方原文）" not in md


def test_宪法命中优先于gate_code(tmp_path) -> None:
    """⛔ 宪法命中是「在等你批准」，⚠️ 排最前的地位不许被 gate_code 抢走。"""
    md = _mk(tmp_path, [{"error": "宪法命中 A-1：改了受保护文件",
                         "gate_code": 1}] * 2)
    assert "需要人批准" in md


def test_record真的把gate_code写进台账() -> None:
    """⛔ 这个项目栽过三次「实现了但生产路径没调」。"""
    import inspect

    from devloop import cli, telemetry

    assert "gate_code" in inspect.signature(telemetry.record).parameters, \
        "⛔ telemetry.record 没有 gate_code 参数"
    src = inspect.getsource(cli._run_unit)
    assert "gate_code=" in src, "⛔ _run_unit 没把 gate_code 传给台账"
