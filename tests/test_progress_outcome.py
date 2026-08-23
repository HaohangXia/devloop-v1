"""⛔ 心跳的收尾行不许把「被杀」「炸了」印成「完」（G-106）。

## 这条是怎么来的

2026-08-04 首次跨项目真派单。屏幕上滚出来的是：

    [16:08:38 +3002s] f1-feeding-formulas · 工人 完 · 用了 3000s

⭐ 主控读到「完」，判定工人干完了，于是把随后那 19 分钟的闸解释成
「工具卡住了」，**动手掐掉了整跑**。

⛔ 真相：3000 秒正好是工人的硬死线（`models.py::timeout_s` 默认 3000），
工人是被 `subprocess.run(timeout=...)` **打死的**，一个字的回执都没交
（决定性证据：`.devloop/reports/` 里 08-04 零文件，而 `dispatch._finish`
每个分支都会落盘 ⇒ 走的是提前 return 的超时分支）。

## ⭐ 这是第一种假绿，长在**给人看的实时视图**上

`progress.py::phase` 的收尾行写在 `finally:` 里，**无条件**印「完」。
于是三种完全不同的结局共用同一个字：

| 真实结局 | 屏幕上 |
|---|---|
| 干完了 | 完 |
| 子进程超时被杀（异常被上游吞成返回值） | 完 |
| 阶段里抛异常 | 完 |

⚠️ 「守卫的目标不存在」的变体：这里**判据本身不存在**——那个字与任何
可观测的事实都不对应。⛔ 它骗的不是测试，是操作员，而且当场骗到了。

## ⛔ 两条路都得堵

1. **抛异常**（含 `KeyboardInterrupt`——它是 `BaseException`，
   `except Exception` 接不住，而 Ctrl-C 是人停掉夜跑最常用的方式）。
2. **异常被上游吞成返回值**——`dispatch.py:145` 把 `TimeoutExpired`
   转成 `DispatchResult(error=...)` 返回，`phase()` 看来一切正常。
   ⭐ 这条只能由**调用方主动报**，所以 `phase()` 必须给它一个报的地方。
"""

from __future__ import annotations

import io
import subprocess
import sys

import pytest

from devloop import progress as P


def _sink() -> io.StringIO:
    return io.StringIO()


# ══════════════════════════════════════════════════════════════════════
#  ① 抛异常 —— 不许印「完」
# ══════════════════════════════════════════════════════════════════════

def test_阶段里抛异常不许印完() -> None:
    s = _sink()
    p = P.Progress("u", stream=s)
    with pytest.raises(RuntimeError):
        with p.phase("闸", every=10):
            raise RuntimeError("boom")
    out = s.getvalue()
    assert "完" not in out, f"⛔ 炸了却印「完」：{out}"
    assert "断" in out, f"收尾行要说清是断的：{out}"
    assert "RuntimeError" in out, f"⭐ 要带上是什么炸的，否则人还得去翻别处：{out}"


def test_按下Ctrl_C不许印完() -> None:
    """⛔ `KeyboardInterrupt` 是 `BaseException`——`except Exception` 接不住它。

    ⚠️ 而 Ctrl-C 正是人中断一个夜跑最常用的方式。
    """
    s = _sink()
    p = P.Progress("u", stream=s)
    with pytest.raises(KeyboardInterrupt):
        with p.phase("工人", every=10):
            raise KeyboardInterrupt
    out = s.getvalue()
    assert "完" not in out, f"⛔ 被 Ctrl-C 掐了却印「完」：{out}"
    assert "断" in out


# ══════════════════════════════════════════════════════════════════════
#  ② 异常被上游吞成返回值 —— 调用方必须有地方报，且报了要生效
# ══════════════════════════════════════════════════════════════════════

def test_调用方能把被吞掉的坏结局报上来() -> None:
    """⭐ 2026-08-04 的**逐字复现**：子进程超时，异常在阶段内部被吞。"""
    s = _sink()
    p = P.Progress("f1", stream=s)
    with p.phase("工人", every=10, limit_s=0.3) as ph:
        try:
            subprocess.run([sys.executable, "-c", "import time;time.sleep(9)"],
                           timeout=0.3)
        except subprocess.TimeoutExpired:
            ph.broke("⛔ 超时被杀")        # ← dispatch.py 的吞法对应的报法
    out = s.getvalue()
    assert "完" not in out, f"⛔ 超时被杀却印「完」——这正是那次误判：{out}"
    assert "超时被杀" in out


def test_没人报坏消息且没异常时照旧印完() -> None:
    """⛔ 防回归：正常路径的措辞不许改——别的测试和人的眼睛都认这个字。"""
    s = _sink()
    p = P.Progress("u", stream=s)
    with p.phase("闸", every=10):
        pass
    out = s.getvalue()
    assert "完" in out and "断" not in out, out


# ══════════════════════════════════════════════════════════════════════
#  ③ 真死线必须印出来 —— 只印「预计」等于把要命的那个数藏起来
# ══════════════════════════════════════════════════════════════════════

def test_真死线要印在开始那一行() -> None:
    """⚠️ 那一跑屏幕上只有「预计 300s」，而真正会杀进程的是 3000s。

    ⭐ 人盯着屏幕看到的是「超预计了，也许快好了」，
       实际是「它会在 16:08 被处决」，而这件事**一个字都没显示**。
    """
    s = _sink()
    p = P.Progress("u", stream=s)
    with p.phase("工人", every=10, expect_s=300, limit_s=3000):
        pass
    first = s.getvalue().splitlines()[0]
    assert "3000" in first, f"⛔ 真死线没印出来：{first}"


def test_心跳里也要带真死线() -> None:
    """⭐ 开始那一行会滚出屏幕；人真正盯着的是心跳行。"""
    s = _sink()
    p = P.Progress("u", stream=s)
    import time
    with p.phase("工人", every=0.03, expect_s=300, limit_s=3000):
        time.sleep(0.12)
    beats = [l for l in s.getvalue().splitlines() if "仍在跑" in l]
    assert beats, "没有心跳行"
    assert any("3000" in l for l in beats), f"⛔ 心跳里没有真死线：{beats[0]}"


def test_没给死线时不许凭空编一个() -> None:
    """⛔ 「不知道死线」与「死线是某个数」是两件事，不许混。"""
    s = _sink()
    p = P.Progress("u", stream=s)
    with p.phase("某阶段", every=10, expect_s=300):
        pass
    first = s.getvalue().splitlines()[0]
    assert "死线" not in first and "强制" not in first, \
        f"⛔ 没给 limit_s 却印了死线：{first}"


# ══════════════════════════════════════════════════════════════════════
#  ④ ⭐ 后果判据：真派单路径上必须把真死线传进去
#     ⛔ 光 phase() 支持不算数——2026-08-04 那一跑就是「支持了但没传」
# ══════════════════════════════════════════════════════════════════════

def test_真派单路径把工人的真死线传进了心跳() -> None:
    """⛔ 判据落在**调用点**，不是 `phase()` 的签名。

    ⚠️ `phase()` 加了 `limit_s` 而 `cli.py` 不传，屏幕上照样只有「预计 300s」
    ——那等于没修。⭐ 这条测的是「传了没有」。
    """
    import ast
    import inspect

    from devloop import cli

    src = inspect.getsource(cli._run_unit)
    tree = ast.parse(src.lstrip())
    hits = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "phase"):
            continue
        name = n.args[0].value if n.args and isinstance(n.args[0], ast.Constant) else "?"
        hits.append((name, {k.arg for k in n.keywords}))

    assert hits, "⛔ `_run_unit` 里一个 `phase(` 都没有？先看是不是重构过"
    for name, kws in hits:
        assert "limit_s" in kws, (
            f"⛔ 阶段「{name}」没把真死线传进心跳——"
            f"人在屏幕上依旧看不到什么时候会被强杀。实际传了：{sorted(kws)}")
