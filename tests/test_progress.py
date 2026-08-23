"""实时心跳的测试。

⛔ 这东西存在的唯一理由是「夜里挂着跑，中途看得出它卡在哪」。
所以测的重点不是格式好不好看，是**它真的会在事情发生的当下就写出来**
——攒到最后一起吐，等于没有。
"""

from __future__ import annotations

import io
import time

from devloop import progress as P


def _sink() -> io.StringIO:
    return io.StringIO()


def test_标记当场就写出来() -> None:
    """⛔ 攒着不写 = 没有心跳。判据落在「调用返回时字节已经在流里」。"""
    s = _sink()
    p = P.Progress("u1", stream=s)
    p.mark("建好 worktree")
    assert "建好 worktree" in s.getvalue(), "⛔ mark() 返回了但流里没有——被缓冲了"
    assert "u1" in s.getvalue(), "没带单元名，并行时分不清哪行属于哪单"


def test_带时间戳和累计秒数() -> None:
    """⚠️ 夜里看日志，「现在几点」和「已经跑了多久」缺一不可。"""
    s = _sink()
    p = P.Progress("u1", stream=s)
    p.mark("开始")
    line = s.getvalue()
    assert ":" in line, f"没有时间戳：{line}"
    assert "s]" in line or "s " in line, f"没有累计秒数：{line}"


def test_关掉之后一个字都不写() -> None:
    """⚠️ 并行 8 单时心跳会互相绞——要留一个关得掉的开关。"""
    s = _sink()
    P.Progress("u1", stream=s, enabled=False).mark("x")
    assert s.getvalue() == ""


# ── 心跳：长阶段里定时报「我还活着」 ──────────────────────────────

def test_长阶段会定时报还活着() -> None:
    """⭐ 这是整个模块的存在理由。闸要跑 900 秒，而它是全捕获的子进程，
    ⛔ 中途拿不到任何输出——只能从外面定时报数。"""
    s = _sink()
    p = P.Progress("u1", stream=s)
    with p.phase("闸", every=0.05):
        time.sleep(0.28)
    out = s.getvalue()
    beats = [l for l in out.splitlines() if "仍在" in l]
    assert len(beats) >= 3, f"0.28 秒里每 0.05 秒该报一次，实得 {len(beats)} 条：\n{out}"
    assert "闸" in out


def test_阶段结束会报总耗时() -> None:
    s = _sink()
    p = P.Progress("u1", stream=s)
    with p.phase("闸", every=10):        # every 很大 = 不会有心跳
        pass
    out = s.getvalue()
    assert "闸" in out and "完" in out, f"没报阶段结束：{out}"


def test_阶段里抛异常也要停掉心跳线程() -> None:
    """⛔ 心跳线程漏掉不停，整个进程就退不掉——夜跑时表现为「跑完了但不返回」。"""
    s = _sink()
    p = P.Progress("u1", stream=s)
    try:
        with p.phase("闸", every=0.02):
            raise RuntimeError("炸了")
    except RuntimeError:
        pass
    time.sleep(0.12)
    n1 = s.getvalue().count("仍在")
    time.sleep(0.12)
    assert s.getvalue().count("仍在") == n1, "⛔ 异常退出后心跳还在跳——线程没停"


def test_预计耗时会写进心跳() -> None:
    """⚠️ 「已跑 700 秒」本身没意义，要和「预计 900 秒」并排才知道是不是卡了。"""
    s = _sink()
    p = P.Progress("u1", stream=s)
    with p.phase("闸", every=0.03, expect_s=900):
        time.sleep(0.08)
    assert "900" in s.getvalue(), f"预计耗时没写出来：{s.getvalue()}"


def test_不吞异常() -> None:
    """⛔ 心跳是观测手段，绝不能改变控制流。"""
    p = P.Progress("u1", stream=_sink())
    try:
        with p.phase("x", every=10):
            raise ValueError("必须原样抛出去")
    except ValueError as exc:
        assert str(exc) == "必须原样抛出去"
    else:
        raise AssertionError("⛔ 心跳把异常吞了")


# ── 接线：⛔ 光有模块没用，要证明生产路径真的在用 ──────────────────

def test_run_unit里真的用了心跳() -> None:
    """⛔ 这个项目栽过两次「实现了但生产路径没调」。判据落在源码上。"""
    import ast
    import inspect

    from devloop import cli

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    src = ast.dump(fn)
    assert "Progress" in src, "⛔ `_run_unit` 里没建 Progress——心跳是死的"
    #  闸占整单 87% 的时间，它那一段**必须**被 phase() 圈起来，否则等于没修
    phases = [n for n in ast.walk(fn)
              if isinstance(n, ast.withitem)
              and isinstance(n.context_expr, ast.Call)
              and getattr(n.context_expr.func, "attr", "") == "phase"]
    names = [a.value for c in phases for a in c.context_expr.args
             if isinstance(a, ast.Constant)]
    assert "闸" in names, (
        f"⛔ 闸那一段没被心跳圈起来，而它占整单 87% 的时间。当前圈了：{names}")


def test_分段耗时真的进了台账(tmp_path) -> None:
    """⛔ 不落盘的测量等于没测量（第 5 种假绿）。"""
    import ast
    import inspect
    import json

    from devloop import cli, telemetry
    from devloop.dispatch import DispatchResult

    # ① 源码上：`_run_unit` 得把 phases 传给 telemetry.record
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    rec = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
           and getattr(n.func, "attr", "") == "record"]
    assert rec, "⛔ `_run_unit` 里找不到 telemetry.record 调用"
    assert any("phases" in {k.arg for k in c.keywords} for c in rec), \
        "⛔ record() 没收到 phases——量了但没落盘"

    # ② 行为上：传进去的三个键要真的出现在台账行里
    p = tmp_path / "t.jsonl"
    telemetry.record(p, DispatchResult(task="t", receipt=None, report_path=tmp_path / "r.json", error="x"),
                     model="m", tools="readonly",
                     phases={"setup_s": 1.5, "worker_s": 93.3, "gate_s": 905.0})
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert row["gate_s"] == 905.0 and row["worker_s"] == 93.3, row


def test_没给分段耗时时记null而不是零(tmp_path) -> None:
    """⚠️ 「没量」和「量到 0 秒」必须分得开——只读任务压根不跑闸。"""
    import json

    from devloop import telemetry
    from devloop.dispatch import DispatchResult

    p = tmp_path / "t.jsonl"
    telemetry.record(p, DispatchResult(task="t", receipt=None, report_path=tmp_path / "r.json", error="x"),
                     model="m", tools="readonly")
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert row["gate_s"] is None, f"没量到却记了 {row['gate_s']}"
