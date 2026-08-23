"""⛔ 等额度时，**两个墙钟口径都要往后推**（G-120）。

## 这条是怎么来的

2026-08-04 当天：`--wait-for-reset` 刚给自动驾驶加上（G-118），
⛔ **同一天被复核查出它是废的**。

代码里墙钟有**两个口径**：

| 口径 | 谁读它 | 管什么 |
|---|---|---|
| `wall_deadline` | `plan.clamp_to_wall` | 收窄**子进程**的死线（工人、闸） |
| `run.started` | `autopilot.check_limits` | 判「整批跑了多久，还能不能派下一单」 |

⚠️ 第一版**只推了 `wall_deadline`**。于是睡 4 小时醒来，
`check_limits` 第一句就是「已跑 260 分钟，上限 90 分钟」→ 停机。

> ⭐ **白睡 4 小时，一个字不干。**

## ⛔ 又是那个形状

**同一件事有两条路，只改了有人盯着的那条。**
（第八次：G-101 · G-107 · G-112 · G-113 · G-114 · G-117 · 锚只重了一个 · 这次）

⚠️ 而最刺眼的是：**上一版的注释自己就写着「墙钟也要跟着推」**
——⛔ 写下那句话的人（我）只推了一半。
⭐ **知道该做什么，和真的两处都做了，是两件事。**
"""

from __future__ import annotations

import ast
import inspect

from devloop import cli


def test_两个墙钟口径必须一起往后推() -> None:
    """⭐ 用 **AST** 找那个 `if`，⛔ 不 grep 源码文本（注释会骗人，今天骗过一次）。

    判据：`can_auto_resume` 那个分支体内，
    **必须同时**出现对 `wall_deadline` 与 `run.started` 的增量赋值。
    """
    tree = ast.parse(inspect.getsource(cli.cmd_autopilot).lstrip())

    def has_aug(node, target: str) -> bool:
        for x in ast.walk(node):
            if not isinstance(x, ast.AugAssign):
                continue
            t = x.target
            name = (t.id if isinstance(t, ast.Name)
                    else f"{getattr(t.value, 'id', '')}.{t.attr}"
                    if isinstance(t, ast.Attribute) else "")
            if name == target:
                return True
        return False

    def calls(node, name: str) -> bool:
        return any(isinstance(x, ast.Call)
                   and getattr(x.func, "attr", getattr(x.func, "id", "")) == name
                   for x in ast.walk(node))

    for n in ast.walk(tree):
        if not (isinstance(n, ast.If) and calls(n.test, "can_auto_resume")
                and calls(n, "sleep")):
            continue
        missing = [t for t in ("wall_deadline", "run.started") if not has_aug(n, t)]
        assert not missing, (
            f"⛔ 等额度那一段只推了一个墙钟口径，漏了 {missing}。\n"
            f"   ⚠️ 漏 `run.started` → 睡完醒来第一句就被 `check_limits` 判超时，"
            f"**白睡一场**；\n"
            f"   ⚠️ 漏 `wall_deadline` → 醒来后工人/闸的死线被收窄成 1 秒，必然超时。")
        return
    raise AssertionError("⛔ 找不到「问过 can_auto_resume 之后才 sleep」的那一段")


def test_check_limits读的确实是run_started() -> None:
    """⚠️ 前提钉死：上面那条判据建立在「`check_limits` 读 `run.started`」之上。

    ⛔ 哪天有人把它改成读别的，上面那条会变成一句没人核对的话。
    """
    src = inspect.getsource(cli.cmd_autopilot)
    assert "check_limits(sp, prog, started=run.started" in src, (
        "⛔ `check_limits` 不再读 `run.started` 了——"
        "上一条判据的前提没了，两条一起重写")


def test_睡完之后墙钟真的还剩时间() -> None:
    """⭐ 后果判据：拿真数算一遍，⛔ 不看代码长什么样。

    场景：90 分钟墙钟，跑了 20 分钟撞额度，睡 4 小时。
    ⚠️ 醒来时若不推 `run.started`，`check_limits` 会算出「已跑 260 分钟」。
    """
    from devloop.autopilot import check_limits, Progress
    from devloop.plan import Budget

    #  ⚠️ 只造 `check_limits` 真正会读的那几样，⛔ 别造一整个假计划。
    class _P:
        budget = Budget(total_usd=10.0, reserve_usd=1.0, max_dispatches=9,
                        max_wall_min=90, default_max_turns=120, watchdog_k=3)

    import time as _t
    now = _t.time()
    started_20min_ago = now - 20 * 60
    #  ① 不推（错的做法）：睡 4 小时之后
    bad = check_limits(_P(), Progress(), started=started_20min_ago - 4 * 3600,
                       dry_rounds=0)
    assert bad is not None and bad.kind == "wall", \
        "前提没成立：不推 started 时本该被墙钟判停"
    #  ② 推了（对的做法）：等待时长不算干活时间
    good = check_limits(_P(), Progress(), started=started_20min_ago, dry_rounds=0)
    assert good is None, (
        f"⛔ 推了 `run.started` 之后仍被判停：{good}"
        f"——那说明推的口径不对")
