"""自动驾驶读台账必须走 `telemetry.load`（M-2）。

## ⛔ 形状：修了一处，而**刹车读的是另外两处**

`telemetry.load` 的 docstring 自己写着：

> ⚠️ 原实现是一句裸列表推导 `[json.loads(ln) for ln in ...]`，一行坏行
> 直接抛 `JSONDecodeError`，而上层 `except ValueError` 把它显示成
> 「输入错误：Unterminated string」——**不提文件名、不说是台账**。

⛔ **那个被认定为 bug 的实现，在 `autopilot.py` 里原样活着两份**：
`read_progress`（:108）与 `_rows_for`（:321）。
⚠️ 而 `read_progress` 正是自动驾驶四条失控防线的**唯一数据来源**。

## ⭐ 坏行到底该抛还是该跳过

答案就在 `load` 自己的 docstring 里，⚠️ 而且它是**专门为刹车写的**：

> ⛔ **坏行必须报出来，不许静默 skip。** 静默跳过等于让「已花多少」
> 「派了几次」偷偷变小，而那正是失控防线读的数——防线会因此形同虚设。

所以 `strict=True`（它的默认值）。⚠️ 代价是一行坏日志会停掉一夜的活
——但停机是**可解释、可恢复**的（台账仍在，修完 `--resume` 续上），
而刹车静默变松是**不可察觉**的。⛔ 两者不对称。

⚠️ 注意这与**写**侧的取舍相反：`telemetry._append` 写着「⛔ 拿不到文件锁也
照写——丢一行日志远好过丢一单活」。⭐ 写侧宁可脏也要留下痕迹，
读侧（尤其刹车读的时候）宁可停也不许把脏当干净。**同一份台账，两个方向。**

## ⚠️ 「丢行」这一种它管不了

坏行能被发现，**整行消失**不能——`json.loads` 全部成功，刹车读到偏小的数、
一声不吭。⭐ 那是 `cmd_autopilot` 里进程内计数 `fired` +
`prog.dispatches = max(prog.dispatches, fired)` 兜的（2026-08-02 加）。
⛔ 但它只在**同一个进程内**有效，跨进程/跨次运行兜不住。这条是残留风险，不装作没有。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from devloop import autopilot, telemetry
from devloop.config import ProjectPaths
from devloop.plan import Budget, StagePlan, Task

GOOD = ('{"ts": "2026-08-02T01:00:00", "task": "t", "ok": true, '
        '"gate_ok": true, "cost_usd_real": 0.0}')


def _mk(tmp_path, body: str) -> tuple[ProjectPaths, StagePlan]:
    proj = tmp_path / "p"
    (proj / ".devloop" / "tasks").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    paths = ProjectPaths(proj)
    paths.telemetry.parent.mkdir(parents=True, exist_ok=True)
    paths.telemetry.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    plan = StagePlan(
        id="s", goal="g", task_dir=proj / ".devloop" / "tasks", base="HEAD",
        budget=Budget(total_usd=1.0, reserve_usd=0.1, max_dispatches=9,
                      max_wall_min=30),
        tasks=(Task(id="t", tools="implement", require_pass=("g1",)),),
        require_pass=("g1",), source=proj / ".devloop" / "plans" / "s.toml")
    return paths, plan


# ── ⛔ 坏行：要报得清楚，不许只说「输入错误」 ─────────────────────

def test_台账有坏行时报得出是台账和第几行(tmp_path) -> None:
    """⛔ 裸列表推导抛的是 `Expecting value: line 1 column 39`
    ——不提文件名、不说是台账、不说是哪一行。⚠️ 无人值守跑一夜之后，
    早上看到这句话的人无从下手。"""
    paths, plan = _mk(tmp_path, GOOD + "\n{这不是 JSON}\n" + GOOD + "\n")
    with pytest.raises(telemetry.LedgerCorrupted) as e:
        autopilot.read_progress(paths, plan, "2026-08-01T00:00:00")
    msg = str(e.value)
    assert str(paths.telemetry) in msg, f"⛔ 报错里没有台账路径：{msg}"
    assert "2" in msg, f"⛔ 报错里没有行号：{msg}"


def test_坏行不许被静默跳过(tmp_path) -> None:
    """⛔ 静默跳过 = 「派了几次」偷偷变小，而那正是失控防线读的数。"""
    paths, plan = _mk(tmp_path, "\n".join([GOOD] * 3 + ["{坏}"] + [GOOD] * 2) + "\n")
    with pytest.raises(telemetry.LedgerCorrupted):
        autopilot.read_progress(paths, plan, "2026-08-01T00:00:00")


def test_非法UTF8字节不许让读操作直接炸_撕裂写(tmp_path) -> None:
    """⚠️ G-41 复跑抓到过并发写留下非法 UTF-8。
    ⛔ 裸的 `read_text(encoding="utf-8")` 抛 `UnicodeDecodeError`——**连行号都没有**。
    `telemetry.load` 用 `errors="replace"`：坏字节变 `\\ufffd`，行变成非法 JSON，
    ⭐ 于是**有行号可报**。"""
    paths, plan = _mk(tmp_path, (GOOD + "\n").encode() + b'{"ts": \xff}\n')
    with pytest.raises(telemetry.LedgerCorrupted) as e:
        autopilot.read_progress(paths, plan, "2026-08-01T00:00:00")
    assert "2" in str(e.value)


def test_非法字节落在字符串里时会静默通过_这是已知残留(tmp_path) -> None:
    """⛔ **这条钉的是一个仍然存在的洞，不是一个已修的缺陷。**

    `errors="replace"` 把 `\\xff` 变成 `\\ufffd`，而 `{"ts": "\\ufffd\\ufffd"}`
    **是合法 JSON**——它会静默解析成功，字段内容却已经损坏。

    ⚠️ 后果：`ts` 坏了会让「只认本阶段之后的行」这条筛选判错；
    `task` 坏了会让那一行归不到任何任务上——两者都让刹车读到偏小的数。
    ⛔ 而 `load` 看不见它：对 `load` 来说这就是一行好行。

    ⭐ 留这条测试是为了**别让人以为 M-2 修完这个洞就没了**。
    要真堵住得给每行加校验和，⚠️ 那会改台账的形状——独立的一条，不在这里顺手做。
    """
    paths, plan = _mk(tmp_path, (GOOD + "\n").encode() + b'{"ts": "\xff\xfe"}\n')
    prog = autopilot.read_progress(paths, plan, "2026-08-01T00:00:00")
    assert prog.dispatches == 1, \
        "⚠️ 若这条变了，说明已经能识破字符串内的坏字节了——那是好事，请更新本测试"


def test_干净台账照常读得出来(tmp_path) -> None:
    """⚠️ 反向钉住：别为了严格把正常路径弄坏。"""
    paths, plan = _mk(tmp_path, "\n".join([GOOD] * 3) + "\n")
    prog = autopilot.read_progress(paths, plan, "2026-08-01T00:00:00")
    assert prog.dispatches == 3 and "t" in prog.done_ok


def test_例外层读台账走同一条路(tmp_path) -> None:
    """⛔ 交接单那处（`_rows_for`）是**第二份**裸列表推导。
    ⚠️ 两份分别修必然分叉，而这里分叉的后果是「同一个台账，两个进度」。"""
    paths, plan = _mk(tmp_path, GOOD + "\n{坏}\n")
    with pytest.raises(telemetry.LedgerCorrupted):
        autopilot.escalation(paths, plan, plan.tasks[0], "2026-08-01T00:00:00")


# ── 接线：⛔ 裸列表推导不许再出现 ─────────────────────────────────

def test_autopilot里不许再有裸的json_loads逐行解析() -> None:
    """⛔ 判据落在 AST 上：`[json.loads(x) for x in ...]` 这个**形状**不许再出现。

    ⚠️ 判子串「json.loads」会误伤 `resume_chain_head` 里合法的**整文件**解析
    （那是读一个 JSON 文件，不是逐行读台账）——那是判据维度错。
    ⭐ 判的是「推导式里调 json.loads」这个动作。
    """
    tree = ast.parse(inspect.getsource(autopilot))
    bad = [n for n in ast.walk(tree)
           if isinstance(n, ast.ListComp)
           and isinstance(n.elt, ast.Call)
           and isinstance(n.elt.func, ast.Attribute)
           and n.elt.func.attr == "loads"]
    assert not bad, ("⛔ autopilot 里还有裸的逐行 json.loads——"
                     "刹车读的台账必须走 telemetry.load")


def test_读台账真的调了telemetry_load() -> None:
    for fn in (autopilot.read_progress, autopilot._rows_for):
        src = inspect.getsource(fn)
        assert "telemetry.load" in src, f"⛔ {fn.__name__} 没走 telemetry.load"


# ── ⛔ 第三份裸解析：`jobs.py`（复核抓到的，M-2 第一版漏了）─────────

def test_后台作业读台账也走同一条路(tmp_path) -> None:
    """⛔ `Job.done_units()` 是 `--detach` 后台作业判进度的**唯一来源**。

    ⚠️ 它是第三份逐行裸 `json.loads`——`read_progress` / `_rows_for` 之外。
    ⭐ 三份分别演化的后果是「同一个台账，三个进度」。
    """
    import inspect

    from devloop import jobs

    src = inspect.getsource(jobs.Job.done_units)
    assert "telemetry.load" in src or "_tele.load" in src, \
        "⛔ done_units 还在自己逐行 json.loads——台账坏了它抛裸 JSONDecodeError"


def test_jobs里不许再有裸的逐行json_loads() -> None:
    import ast
    import inspect

    from devloop import jobs

    tree = ast.parse(inspect.getsource(jobs))
    bad = [n for n in ast.walk(tree)
           if isinstance(n, ast.ListComp)
           and isinstance(n.elt, ast.Call)
           and isinstance(n.elt.func, ast.Attribute)
           and n.elt.func.attr == "loads"]
    assert not bad, "⛔ jobs.py 里还有裸的逐行 json.loads"
