"""⛔ 工人被死线打死之后，不许再花 30 分钟去验它那半截产出（G-111）。

## 这条是怎么来的

2026-08-04 真派单的时间线：

```
16:08:38  工人 完 · 用了 3000s     ← 其实是被 3000s 死线打死，没交回执
16:08:39  闸 开始（预计 900s）      ← ⛔ 一秒之后照样起闸
16:27:39  闸 仍在跑 · 已 1140s      ← 19 分钟，还没验完就被外部掐断
```

⭐ 16:08:38 那一刻，工具**已经知道**工人没留下任何回执。它仍然起了一道
最长 1800 秒的闸，去验一份被从中间截断的产出。

## ⛔ 为什么这里该短路，而宪法命中时不该

代码里原本有一条明确规矩：「宪法命中也**照样跑闸、照样固化产出**：
闸的输出是排查材料，短路等于把材料一起扔掉」。⭐ 那条是对的，**但管的不是这一种**：

| | 宪法命中 | 工人被死线打死 |
|---|---|---|
| 工人的活 | **干完了**，只是碰了红线 | **从中间被截断**——可能改完 A 还没改 B |
| 闸的结论 | 有意义（这份完整产出达标没有） | ⛔ 对一个中间态求值，**什么都不说明** |
| 时间预算 | 正常 | ⛔ 已经烧满（工人跑够了整个死线） |
| 绿了怎么办 | 可以合 | ⚠️ **更危险**——半截的活闸绿了，人会以为它成了 |

⭐ 最后那一格是关键：一份被截断的改动碰巧过闸，是**第一种假绿**的新变体。

## ⛔ 判据必须是结构化的，不许 match 错误文本

本项目在闸的归类上已经栽过一次：「不许再从 `error` 的自由文本里猜——
那会被**测试名劫持**」。所以这里用 `DispatchResult.timed_out` 这个布尔，
⚠️ 而不是 `"超时" in res.error`。

## ⭐ 短路不等于放弃：材料要留，路要留

- worktree **保留**（本来就保留）
- 输出里必须给出**人自己跑闸的那条命令**
- 台账里 `gate_code` 记 `null`（没跑闸），⛔ 不许记 0（全过）

## ⛔⛔ 2026-08-04 补记：这个文件第一版**全是读源码的判据**

四条测试全在 `inspect.getsource` + AST 上做文章，⛔ **一条都没真跑过那条分支**。
于是它们全绿，而真实现是崩的：`telemetry.record(..., gate_ok=gres.passed if wt ...)`
——跳过闸时 `wt` 为真而 `gres` 是 None，**当场 AttributeError**。

⭐ 整条修法在真跑时是废的，而守着它的判据一个都没响。
**这就是「判据落在代用品上」**——源码文本是「行为」的代用品，而这次
源码看着完全正确。⛔ 下面那条 `test_跳过闸的整条路真的跑得通` 才是真判据。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from devloop import cli
from devloop.dispatch import DispatchResult


def test_超时的结果自己带一个结构化标记() -> None:
    """⛔ 下游判「要不要跳过闸」必须读这个布尔，不许读错误文本。"""
    r = DispatchResult("t", None, None, error="⛔ 工人**超时被杀**（3000s 死线）",
                       timed_out=True)
    assert r.timed_out is True
    #  ⚠️ 默认必须是 False——「不知道」的默认值不许是 True，
    #     否则一个新失败形态会静默地把闸全部跳过。
    assert DispatchResult("t", None, None).timed_out is False


def test_run_unit里跑闸之前真的看了那个标记() -> None:
    """⭐ 判据落在**调用点**：`_run_unit` 起闸之前必须读过 `timed_out`。

    ⚠️ 用 AST 而不是 grep：`timed_out` 这个词出现在注释里也会被 grep 匹配到，
       而注释拦不住任何东西。
    """
    tree = ast.parse(inspect.getsource(cli._run_unit).lstrip())

    #  找到「闸」那个 with 语句的行号
    gate_line = None
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "phase" and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "闸"):
            gate_line = n.lineno
    assert gate_line, "⛔ 找不到「闸」那个阶段，先看是不是重构过"

    reads = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "timed_out"]
    assert reads, "⛔ `_run_unit` 从没读过 `timed_out`——那个标记等于没接上"
    assert min(reads) < gate_line, (
        f"⛔ 读 `timed_out` 在起闸**之后**（第 {min(reads)} 行 vs 闸在第 {gate_line} 行）"
        f"——那 19 分钟照样会烧掉")


def test_短路时必须给出人自己跑闸的命令() -> None:
    """⛔ 「跳过」不许等于「这条路没了」。

    ⚠️ 人手里还有那个 worktree，他有权自己验一遍；工具必须把命令给他，
       ⭐ 否则短路省下的 30 分钟会变成他翻文档的 30 分钟。
    """
    src = inspect.getsource(cli._run_unit)
    i = src.find("timed_out")
    assert i > 0
    near = src[i:i + 1500]
    assert "devloop gates" in near or "gates --" in near, (
        "⛔ 跳过闸的那一段没告诉人怎么自己跑——"
        "把路堵死了，而本项目的规矩是「短路不许把材料一起扔掉」")


def test_跳过闸的整条路真的跑得通(tmp_path, monkeypatch):
    """⭐⭐ **这一条才是真判据**：把 `_run_unit` 整条跑一遍，别读源码。

    ⛔ 本文件上面那几条 AST 判据在第一版全绿，而真实现是崩的
    （`gate_ok=gres.passed if wt` → 跳过闸时 `gres` 是 None → AttributeError）。
    ⚠️ **源码文本是行为的代用品**，而这次源码看着完全正确。
    """
    from devloop import cli as C, dispatch as D, worktree as wt_mod
    from devloop.config import ProjectPaths
    from devloop.models import TaskSpec, WorkerConfig

    from test_wiring import _proj                     # 复用既有的最小项目夹具

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)

    #  ⭐ 工人**被死线打死**的那种返回：没有回执、timed_out=True。
    killed = D.DispatchResult("t", None, None, timed_out=True,
                              error="⛔ 工人**超时被杀**（10s 死线）")
    monkeypatch.setattr(C, "dispatch_one", lambda *a, **k: killed)
    #  ⛔ 闸若被跑到就当场炸——那正是这条修法要防的（不该跑它）。
    monkeypatch.setattr(C, "run_gates", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("⛔ 工人已被死线打死，闸不该被跑起来")))
    monkeypatch.setattr(wt_mod.Worktree, "changed_files",
                        lambda self: ["game/src/sim/wyrm_layer.gd"])
    committed: dict = {}
    monkeypatch.setattr(wt_mod.Worktree, "commit_result",
                        lambda self, n, **k: committed.update(k) or "deadbee")

    ok, lines = C._run_unit(
        TaskSpec.load(task), paths,
        WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="implement", max_turns=10, gate_fp=None, writes=True)

    body = "\n".join(lines)

    #  ⛔⛔ **这一条是本测试的命门**，别删。
    #     第一版的断言（`not ok` / 输出里有「闸没跑」/ `committed.get(...) is None`）
    #     在**有 bug 的实现上照样全过**，实测确认过。两个原因：
    #     ① `AttributeError` 被 `_run_unit` 外层的 `except Exception` **吞掉**，
    #        这一单照样被记成失败 —— `assert not ok` 分不出「跳过闸后正常失败」
    #        与「跳过闸后炸了」；
    #     ② `committed.get("gate_ok") is None` —— ⛔ **「压根没调用过」与
    #        「调用了并传 None」返回同一个值**，判据落在代用品上。
    #     ⭐ 能分辨的只有这两条：出没出异常、`commit_result` 到底调没调。
    assert "Error" not in body and "Traceback" not in body, (
        f"⛔ 跳过闸这条路上抛异常了（被外层 except 吞掉，肉眼看不出来）：\n{body}")

    assert not ok, "⛔ 工人被打死了，这一单不许算合格"
    assert "闸没跑" in body, f"没告诉人闸被跳过了：{body}"
    assert "devloop gates" in body, "⛔ 没给出人自己跑闸的命令，等于把路堵死"

    #  ⭐ 产出**必须照样固化**——不提交正是 G-109 那 50 分钟差点被铲的成因。
    #  ⛔ 判据是「键在不在」而不是「值是不是 None」：见上面 ②。
    assert "gate_ok" in committed, (
        "⛔ `commit_result` 根本没被调用——工人那半截活没进任何提交，"
        "而那正是 G-109 里 50 分钟差点被 `prune --force` 铲掉的成因")
    assert committed["gate_ok"] is None, (
        f"⛔ 跳过闸时 `gate_ok` 应是 None（未跑闸），实得 {committed['gate_ok']!r}"
        f"——False 的含义是「闸判它没过」，与「根本没验」是两件事")

    #  ⭐ 台账那一行必须写成，且 `gate_code` 是 null 不是 0。
    rows = [json.loads(l) for l in
            paths.telemetry.read_text(encoding="utf-8").splitlines() if l.strip()]
    close = [r for r in rows if r.get("event") == "close"]
    assert close, f"⛔ 跳过闸之后台账没有收工行——钱花过了却没留账：{rows}"
    assert close[-1]["gate_code"] is None, \
        f"⛔ 没跑闸却记了 gate_code={close[-1]['gate_code']}（0 = 全过 = 假绿）"
    assert close[-1]["ok"] is False


def test_跳过闸时台账记null不是零() -> None:
    """⛔ `gate_code` 的语义：0 = 全过、1 = 有未过、2 = 闸自身故障、null = 没跑闸。

    ⚠️ 跳过了却记 0，就是**第一种假绿**——一道没跑的闸报「全过」。
    ⭐ 判据用 AST：`gate_code=` 的实参在短路分支里必须是 `None`。
    """
    src = inspect.getsource(cli._run_unit)
    assert "gate_code=0" not in src.replace(" ", ""), \
        "⛔ 代码里出现了写死的 `gate_code=0`——没跑闸时它会变成假绿"
