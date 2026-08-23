"""⛔ 工人**超时被杀**，不许在停机理由里说成「价目表里没有那个模型」（G-122）。

## 这条是怎么来的

2026-08-06 第三次真派单（`h-pyramid`）：工人在标定时反复跑全量回归，
撞上 3000 秒死线**被杀**，已烧掉 **7,209,744 tokens**。

⛔ 而屏幕上印的停机理由是：

> 「1 单的 cost_usd_real 是 null（**多半是价目表里没有那个模型**）。」

⚠️ 于是人被指去修 `prices.json` —— 一个**根本没坏**的东西。
而真问题（活太大 / 死线太短）一个字都没提。

## ⭐ 又是那个形状：判据有了，没接上

`DispatchResult.timed_out` 这个**结构化字段 G-111 就加了**
（`dispatch.py` 的 `subprocess.TimeoutExpired` 分支里设的），
⛔ 只是**从来没写进台账**。于是 `autopilot.read_progress` 读台账时
只看得见 `cost_usd_real is None`，剩下的只能靠猜。

> ⭐ **同一件事有两条路，修好的永远是「有人盯着」的那条。**
> 这已经是第 N 次 —— G-101 · G-107 · G-112 · G-113 · G-114 · G-117 · G-120 · G-121 · 本条。

## 判据

⛔ 落在**人真会读到的那句话**上，不看代码长什么样。
"""

from __future__ import annotations

import json
import time

from devloop import autopilot, telemetry
from devloop.autopilot import Progress, check_limits
from devloop.plan import Budget


class _P:
    """⚠️ 只造 `check_limits` 真会读的那点东西，⛔ 别造一整个假计划。"""
    budget = Budget(total_usd=10.0, reserve_usd=1.0, max_dispatches=9,
                    max_wall_min=90, default_max_turns=120, watchdog_k=3)


def _limits(**kw):
    prog = Progress(**kw)
    return check_limits(_P(), prog, started=time.time(), dry_rounds=0)


def test_超时那一单不许被说成价目表问题() -> None:
    """⭐ 主判据：这一句是人半夜看到的唯一线索。"""
    stop = _limits(unknown_cost=1, timed_out=1, dispatches=1)
    assert stop is not None, "⛔ 烧了钱又算不出成本，必须停批"
    body = f"{stop.why} {stop.detail}"
    #  ⚠️ 判据钉在**那句真会误导人的话**上，⛔ 不是「出现过价目表三个字」——
    #     停机理由里写「⛔ 不是价目表」是在**把人推离**它，那是对的。
    #  ⭐ 第一版判据写成 `"价目表" not in body`，⛔ 把这句正确的提醒也判红了
    #     —— **判据自己成了代用品**，正是本项目最贵的那种错法（G-113）。
    assert "多半是价目表" not in body, (
        f"⛔ 超时被说成了价目表缺项——人会去修一个没坏的东西：\n{body}")
    assert "不是价目表" in body, (
        f"⭐ 光「不提」还不够：要**明说别去修价目表**，否则人凭直觉还是会去：\n{body}")
    assert "超时" in body, f"⭐ 必须说清真因是超时：\n{body}"
    assert "死线" in body, f"⭐ 还要指出该动的是死线或活的大小：\n{body}"


def test_真的查不到价时照旧说价目表() -> None:
    """⛔ 防回归：别为了修这条把真正的价目表缺项也吞掉。"""
    stop = _limits(unknown_cost=1, timed_out=0, dispatches=1)
    assert stop is not None
    assert "价目表" in stop.detail, f"真缺价时那句提醒不许丢：\n{stop.detail}"


def test_两种混在一起时两句都要有() -> None:
    """⚠️ 一夜里既有超时又有缺价是可能的。⛔ 只报一种 = 另一种被吞。"""
    stop = _limits(unknown_cost=3, timed_out=1, dispatches=3)
    body = f"{stop.why} {stop.detail}"
    assert "超时" in body and "价目表" in body, f"两种真因都要露头：\n{body}"
    assert "2 单" in body, f"⭐ 剩下几单是缺价要数得出来：\n{body}"


def test_老台账没有这个字段时按缺价处置() -> None:
    """⚠️ 台账里已有的 16 行都没有 `timed_out`。

    ⛔ 缺省必须**与改之前一致**（当成缺价），不许因为读不到就当成超时
    —— 那会把一个没发生的结论塞给人。
    """
    rows = telemetry.units([{
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "close",
        "unit_id": "u1", "task": "t", "ok": False, "cost_usd_real": None,
    }])
    assert rows, "⚠️ 前提：只有收工行时 units() 也要吐出来"
    assert not rows[0].get("timed_out"), "⛔ 老行读不到这个字段时必须是假"


def test_台账真的写了这个字段(tmp_path) -> None:
    """⛔ 判据落在**写侧**：`telemetry.record` 必须把它落盘。

    ⚠️ G-113 的教训：判据写好了没接上 = 等于没修。
    ⭐ 所以这里不看签名，**真写一行再读回来**。
    """
    from devloop.dispatch import DispatchResult

    led = tmp_path / "t.jsonl"
    res = DispatchResult(task="t", receipt=None, report_path=None,
                         error="⛔ 工人**超时被杀**", timed_out=True)
    telemetry.record(led, res, model="m", tools="", timed_out=res.timed_out)
    row = json.loads(led.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["timed_out"] is True, (
        f"⛔ `timed_out` 没进台账——那 `autopilot` 就永远只能靠猜：\n{row}")


def test_派单路径把这个事实传给了台账() -> None:
    """⛔ 判据落在**调用点**：`_run_unit` 必须把 `res.timed_out` 传进去。

    ⭐ 用 AST，⛔ 不 grep 源码文本（注释会骗人，本项目骗过一次）。
    """
    import ast
    import inspect

    from devloop import cli

    tree = ast.parse(inspect.getsource(cli._run_unit).lstrip())
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "record"
                and any(k.arg == "timed_out" for k in n.keywords)):
            return
    raise AssertionError(
        "⛔ `_run_unit` 没把 `res.timed_out` 传进台账——"
        "那个字段 G-111 就存在了，⚠️ 而它一直没接上，"
        "于是 2026-08-06 把「工人超时」印成了「价目表缺项」。")
