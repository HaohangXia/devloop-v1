"""⛔ 墙钟把闸掐了，不许在早报里说成「闸自己坏了」（G-116）。

## 这条是怎么来的

G-113 洞五：墙钟收窄之后被掐断的闸走 `gate_code=2`（闸自身故障），
而 `gate_code=2` 的下游处置是「**先修环境**，别看下面的绿」。
⛔ 于是第二天早上人被指去修一个**根本没坏**的环境。

⭐ 当时的修法只做了一半：那句更正（「这不是闸坏了，是墙钟到点了」）
**只写进了屏幕输出**（`out` 那个块），⛔ **没写进台账**。

⚠️ 而挂一夜的定义就是**没人看屏幕**。屏幕上那句话第二天早上已经不在了，
留下的只有台账里一个孤零零的 `gate_code=2`，以及早报据它印的
「⛔ 闸自身故障 N 单——先修环境」。

## ⭐ 判据：台账那一行要自己说清楚

⛔ 不许改 `gate_code` 的语义（2 就是「闸没能给出可用结论」，这没错）。
⭐ 要加的是**为什么**：台账里新增一个字段，说明这次的 2 是墙钟掐的。
⚠️ 早报读到它就不许再说「先修环境」。
"""

from __future__ import annotations

from devloop import nightly, telemetry


def _rows(*, gate_code, wall_killed=None):
    #  ⛔ 时间戳必须**从「现在」算**，不许写死。
    #     ⚠️ `nightly.report` 只看最近 24 小时——写死的日期过一天就掉出窗口，
    #     测试会在某个午夜自己变红，而代码一个字都没改。
    #     ⭐ 实测撞到过：08-04 写的常量，08-05 晚上就红了。
    import time as _t
    _now = _t.strftime("%Y-%m-%dT%H:%M:%S")
    r = {"ts": _now, "event": "close", "unit_id": "u1",
         "task": "t", "ok": False, "gate_code": gate_code,
         "cost_usd_real": 0.0, "duration_s": 1.0, "cache_read_tokens": 0}
    if wall_killed is not None:
        r["gate_wall_killed"] = wall_killed
    return [r]


def test_台账里能分出墙钟掐的和闸真坏了() -> None:
    """⛔ 光看 `gate_code=2` 分不出这两件事——它们的处置完全相反。"""
    a = _rows(gate_code=2, wall_killed=True)[0]
    b = _rows(gate_code=2, wall_killed=False)[0]
    assert a["gate_wall_killed"] is True and b["gate_wall_killed"] is False


def test_早报读到墙钟掐的就不许说先修环境(tmp_path) -> None:
    """⭐ 主判据：落在**人早上真会读到的那句话**上。"""
    led = tmp_path / ".devloop" / "telemetry.jsonl"
    led.parent.mkdir(parents=True)
    import json
    led.write_text(json.dumps(_rows(gate_code=2, wall_killed=True)[0],
                              ensure_ascii=False) + "\n", encoding="utf-8")

    out = nightly.report(tmp_path)
    assert "先修环境" not in out, (
        f"⛔ 墙钟掐的被说成了环境坏了——人会去修一个没坏的东西：\n{out}")
    assert "墙钟" in out, f"⭐ 但必须说清真因：\n{out}"


def test_闸真的坏了照旧说先修环境(tmp_path) -> None:
    """⛔ 防回归：别为了修这条把真正的环境故障也吞掉。"""
    led = tmp_path / ".devloop" / "telemetry.jsonl"
    led.parent.mkdir(parents=True)
    import json
    led.write_text(json.dumps(_rows(gate_code=2, wall_killed=False)[0],
                              ensure_ascii=False) + "\n", encoding="utf-8")
    out = nightly.report(tmp_path)
    assert "先修环境" in out, f"真故障时那句提醒不许丢：\n{out}"


def test_老行没有这个字段时按闸故障处置(tmp_path) -> None:
    """⚠️ 台账里已有的行都没有这个字段。⛔ 缺省必须落在**保守**那边
    ——「可能是环境坏了，去看一眼」远好过「没事，接着睡」。"""
    led = tmp_path / ".devloop" / "telemetry.jsonl"
    led.parent.mkdir(parents=True)
    import json
    led.write_text(json.dumps(_rows(gate_code=2)[0],
                              ensure_ascii=False) + "\n", encoding="utf-8")
    out = nightly.report(tmp_path)
    assert "先修环境" in out


def test_真派单路径把这个事实记进了台账() -> None:
    """⛔ 判据落在**调用点**：`_run_unit` 必须把它传给 `telemetry.record`。

    ⚠️ G-113 的教训：判据写好了没接上 = 等于没修，今天已经踩过两次。
    """
    import ast
    import inspect

    from devloop import cli

    tree = ast.parse(inspect.getsource(cli._run_unit).lstrip())
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "record"
                and any(k.arg == "gate_wall_killed" for k in n.keywords)):
            return
    raise AssertionError(
        "⛔ `_run_unit` 没把「这次是墙钟掐的」传进台账——"
        "那句更正依旧只活在屏幕上，而挂一夜的定义就是没人看屏幕")
