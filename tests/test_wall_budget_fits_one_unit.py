"""⛔ 墙钟必须刹得住**正在跑的那一单**（G-107）。

## 这条是怎么来的

2026-08-04 首次跨项目真派单。计划文件 `f-feeding.toml` 里写着
`max_wall_min = 60`，工具开跑时把这句原样打印给人看。实跑 **80 分钟**。

| | |
|---|---|
| 工人死线 | 3000s（`models.py::WorkerConfig.timeout_s` 默认） |
| 闸死线 | 1800s（`gates.py::GATE_TIMEOUT_S`） |
| **一单最坏** | **4800s = 80 分钟** |

## ⛔ 根因不是「刹车太松」，是刹车没接线

`autopilot.check_limits` 的墙钟那条在「派下一单**之前**」问
（函数 docstring 原文）。⚠️ 一单开跑之后，那 80 分钟里**没有任何东西在看表**。

⭐ 而 `dispatch_one` 和 `run_gates` **本来就都收 timeout 参数**——
刹车装得上，只是没人接线。

## ⛔ 我第一版修错了方向，记在这里

第一版写的是「装不下一单就拒绝开跑」。⚠️ 那是在**否决一个数**，
而正确的做法是**让那个数变成真的**。而且它会误伤：闸的 1800s 是
**天花板**不是**预期**，一个 20 秒跑完闸的项目照样被判死
——实测当场把 `test_chain.py` 三条打红了，那三条测的是别的东西。

⭐ 教训：看到「配置里的数不自洽」时，先问一句**是数错了还是机制缺了**。
"""

from __future__ import annotations

import pytest

from devloop.plan import Budget, clamp_to_wall, wall_report


def _b(wall_min: int) -> Budget:
    return Budget(total_usd=10.0, reserve_usd=1.0, max_dispatches=2,
                  max_wall_min=wall_min, default_max_turns=120, watchdog_k=3)


# ══════════════════════════════════════════════════════════════════════
#  ① 收窄本身
# ══════════════════════════════════════════════════════════════════════

def test_剩余墙钟比标称死线短时按剩余收窄() -> None:
    """⭐ 2026-08-04 的形状：墙钟只剩 600 秒，而工人标称死线 3000 秒。"""
    assert clamp_to_wall(3000, deadline=1000.0, now=400.0) == 600


def test_剩余墙钟宽裕时用标称死线() -> None:
    """⛔ 别把刹车做成「永远按墙钟」——那会让工人凭空多出时间。"""
    assert clamp_to_wall(3000, deadline=99999.0, now=0.0) == 3000


def test_不限墙钟时原样返回() -> None:
    """⚠️ 手动 `dispatch` 没有墙钟。⛔ `None` 不许当成 0。"""
    assert clamp_to_wall(3000, deadline=None, now=12345.0) == 3000


def test_墙钟已经过了也至少留一秒() -> None:
    """⛔ 给 0 或负数会让 `subprocess.run` **立刻**抛超时。

    ⚠️ 那等于「没开始就判超时」，而超时那条路会记成「一单花过钱的活」
    ——⭐ 可它一分钱都没花。真正「时间不够就别开工」的判断在
    `check_limits`，不在这里。
    """
    assert clamp_to_wall(3000, deadline=100.0, now=999.0) == 1


# ══════════════════════════════════════════════════════════════════════
#  ② 开跑前那句话要说真话
# ══════════════════════════════════════════════════════════════════════

def test_墙钟比标称死线紧时要说清工人实际拿到多少() -> None:
    """⭐ 2026-08-04 的**逐字复现**：60 分钟 vs 标称 3000+1800。

    ⚠️ 不说的话，一个被墙钟提前掐断的工人会被误读成「模型不行」。
    """
    t = wall_report(_b(60), worker_timeout_s=3000, gate_timeout_s=1800)
    assert "3000" in t, f"没提标称死线：{t}"
    assert "600" in t, f"没算出闸实际能拿到的秒数（3600-3000）：{t}"
    assert "墙钟" in t


def test_宽裕时不制造噪音() -> None:
    """⛔ 每次都印一段警告 = 那段警告没人看。⚠️ 只有真被收窄时才警告。"""
    t = wall_report(_b(200), worker_timeout_s=3000, gate_timeout_s=1800)
    assert "⚠️" not in t, f"宽裕时不该有警告：{t}"
    assert "刹住" in t, "但要让人知道刹车是真的"


def test_读不到死线时说出来不许装作算得出() -> None:
    """⛔ 「不知道」与「没问题」是两件事。"""
    t = wall_report(_b(60), worker_timeout_s=None, gate_timeout_s=1800)
    assert "读不到" in t or "没法" in t, t


# ══════════════════════════════════════════════════════════════════════
#  ③ ⭐ 后果判据：刹车真的接到了两个子进程上
#     ⛔ 光有纯函数不算数——2026-08-04 缺的正是「没人调用」
# ══════════════════════════════════════════════════════════════════════

def test_工人和闸的死线都真的被收窄了() -> None:
    """⛔ 判据落在**调用点**：两处 timeout 都必须经过 `clamp_to_wall`。

    ⚠️ 用 AST 而不是 grep：`clamp_to_wall` 出现在注释里也会被 grep 匹配到，
       而注释刹不住任何东西。
    """
    import ast
    import inspect

    from devloop import cli

    tree = ast.parse(inspect.getsource(cli._run_unit).lstrip())
    clamped = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and getattr(n.func, "id", getattr(n.func, "attr", "")) == "clamp_to_wall"]
    assert len(clamped) >= 2, (
        f"⛔ `_run_unit` 里只有 {len(clamped)} 处收窄，工人和闸各要一处"
        f"——漏掉哪一处，墙钟就在那一段失效")


def test_续跑时必须说明墙钟重新计时() -> None:
    """⛔ 一个没说出口的语义就是一个陷阱。

    ⚠️ `run.started` 永远是「现在」，所以 `--resume` 会重新发一整份墙钟额度
    ——反复续跑能把 60 分钟累加成一夜。⭐ 这个语义是**有意保留**的
    （`--resume` 的主要用途就是「撞额度→等几小时→接着跑」），
    ⛔ 但必须印出来。

    ⚠️ 这条同时钉住一件更要紧的事：我 2026-08-04 在这里写过一句
    **说反了的注释**（宣称 `--resume` 时起点是上次的），当天被复核抓到。
    ⭐ 与几小时前刚删掉的「工人没有硬性时限」是同一类错——**它会重犯**。
    """
    import inspect

    from devloop import cli

    src = inspect.getsource(cli.cmd_autopilot)
    i = src.find("wall_deadline =")
    assert i > 0
    near = src[i:i + 1600]
    assert "重新计时" in near, (
        "⛔ 续跑时没告诉人墙钟从头算——那是个不说出口的陷阱")
    #  ⛔ 那句假注释不许回来
    assert "才是这一轮真正的起点" not in src, (
        "⛔ 那句假注释又回来了：`run.started` 永远是「现在」")


def test_剩余墙钟真的传到了run_unit() -> None:
    """⚠️ 收窄函数接上了，但没人把 deadline 传进来，一样等于没修。"""
    import inspect

    from devloop import cli

    sig = inspect.signature(cli._run_unit)
    assert "deadline" in sig.parameters, (
        "⛔ `_run_unit` 没有 `deadline` 参数——剩余墙钟传不进去")

    src = inspect.getsource(cli.cmd_autopilot)
    assert "deadline=" in src, (
        "⛔ `cmd_autopilot` 没把 deadline 传下去——刹车在自动驾驶里是断的")
