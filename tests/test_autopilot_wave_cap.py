"""一波派几单：⛔ **不许冲过 `max_dispatches`**。

## 这条为什么要紧

`max_dispatches` 是**订阅制后端上唯一还活着的刹车**。订阅的美元成本恒为 0.0，
于是 `check_limits` 里两条按预算判的防线（「花超了」「算不出成本」）结构性恒假
——`pricing.py` 自己的注释就写着「真正的防线是 max_dispatches 与撞额度上限即停」。

## ⛔ 缺陷的形状（2026-08-02 审计抓到）

上限是**在切波之前**判的，而 `fired` 一次加 N：

    prog.dispatches = max(prog.dispatches, fired)
    stop = check_limits(...)          # ← 判「已派 >= 上限」
    wave = [...][:sp.parallel]        # ← 一次切 N 单
    fired += len(wave)                # ← 一次加 N

`max_dispatches=10` + `parallel=4` 时，第 3 轮 `fired=8 < 10` 通过 → 又派 4 单
→ 实际派了 **12** 次。⚠️ 溢出量 = 并发数 − 1，并发越大冲得越多。

## ⭐ 判据落在纯函数上，不落在读源码上

原来这一段是写在 `cmd_autopilot` 循环体里的，判据只能退化成
「读源码里有没有 `ThreadPoolExecutor`」——那是**代理指标**
（第 3 种假绿：判据的维度错了）。所以先把切波抽成 `autopilot.plan_wave()`，
⭐ 一个能直接量的纯函数，再用接线测试钉住生产路径真的在调它。

## ⛔ 订正：这里原本写着「要验它得跑真单，那花额度」——**那句话是错的**

2026-08-02 的对抗复核当场证伪：把 `cli._run_unit` 换成一个只写台账的桩，
就能**零额度**真跑整个循环并数出实际派单次数。
⚠️ 而我当时正是拿那个错误前提为「只能用 AST 判据」辩护的。

复核同时证明本文件的判据可以被绕过——三种「形似而神不似」的实现都能全绿
而缺陷 100% 复活。⭐ 现已全部收紧（每条的 docstring 里写了当初怎么被绕的），
并新增 `tests/test_autopilot_dispatch_count_e2e.py` 把判据落到**真跑出来的次数**上。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from devloop import autopilot
from devloop.plan import Task


def _t(tid: str, tools: str = "implement") -> Task:
    return Task(id=tid, tools=tools, require_pass=("g1",))


READY = [_t("a"), _t("b"), _t("c"), _t("d"), _t("e")]


# ── ⛔ 上限余额是硬顶 ──────────────────────────────────────────────

def test_余额比并发小的时候按余额切() -> None:
    """⛔ 这就是缺陷本身：`max_dispatches=10`、已派 8、并发 4 → 只能再派 2。"""
    w = autopilot.plan_wave(READY, parallel=4, remaining=2)
    assert len(w) == 2, f"⛔ 余额只剩 2 却切了 {len(w)} 单——冲过 max_dispatches 了"


def test_余额比并发大的时候按并发切() -> None:
    """⚠️ 反向也要钉住：别为了修上限把并发一起砍没了。"""
    assert len(autopilot.plan_wave(READY, parallel=3, remaining=99)) == 3


def test_余额只剩一单时退回串行() -> None:
    assert len(autopilot.plan_wave(READY, parallel=4, remaining=1)) == 1


@pytest.mark.parametrize("parallel,remaining,want", [
    (4, 3, 3), (5, 3, 3), (5, 4, 4), (8, 3, 3), (8, 7, 5), (3, 3, 3),
])
def test_余额与并发的每一种大小关系(parallel, remaining, want) -> None:
    """⛔ 第一版只探了 remaining ∈ {0,1,2,99}，`[3, parallel)` **整段没探**。

    ⚠️ 对抗复核当场用这个洞造了个绕法：
    `n = min(max(1,parallel),remaining) if remaining < 3 else max(1,parallel)`
    ——11 条全绿，而 `plan_wave(parallel=4, remaining=3)` 返回 4 单直接溢出。
    ⭐ 期望值取 `min(parallel, remaining, 同权限就绪数=5)`。
    """
    assert len(autopilot.plan_wave(READY, parallel=parallel,
                                   remaining=remaining)) == want


def test_余额为零切出空波() -> None:
    """⚠️ 调用点保证走不到这里（`check_limits` 会先停），但纯函数必须是全函数
    ——⛔ 让它在不可能的输入上抛 IndexError，等于把「不可能」赌在无人值守的一夜上。"""
    assert autopilot.plan_wave(READY, parallel=4, remaining=0) == []


# ── ⚠️ 原有的两条判据不许因此失效 ─────────────────────────────────

def test_一波里只放同一种权限的单() -> None:
    """⚠️ 只读与写的并行判据完全不同（成本差 35 倍，见 fanout.py），
    混在一波里没有统一判据可用。"""
    mixed = [_t("a", "readonly"), _t("b", "readonly"), _t("c", "implement")]
    w = autopilot.plan_wave(mixed, parallel=4, remaining=99)
    assert [t.id for t in w] == ["a", "b"], "⛔ 把 implement 混进只读波里了"


def test_串行计划一次只派一单() -> None:
    assert len(autopilot.plan_wave(READY, parallel=1, remaining=99)) == 1


def test_并发数写成零或负也至少派一单() -> None:
    """⚠️ 配置写坏不该让自动驾驶原地空转——那会撞上空转看门狗，
    ⛔ 而看门狗报出来的原因会是「连续 K 轮没有新的绿」，把配置错误说成活没干好。"""
    assert len(autopilot.plan_wave(READY, parallel=0, remaining=99)) == 1


def test_就绪的比并发少就全派() -> None:
    w = autopilot.plan_wave([_t("a")], parallel=4, remaining=99)
    assert len(w) == 1


# ── 接线：⛔ 生产路径真的在调它 ───────────────────────────────────

def test_派单循环真的调plan_wave() -> None:
    """⛔ 这个项目栽过三次「实现了但生产路径没调」。

    ⚠️ 判据落在 AST 的「有一次对 `plan_wave` 的调用」上，
    不落在子串——注释里提一句 `plan_wave` 不算接上了。
    """
    from devloop import cli

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_autopilot")
    #  ⛔ 判「调了」不够——对抗复核证明「调了、参数全对、**把结果丢掉**、
    #     紧接着自己再切一次」能让 11 条全绿而缺陷 100% 复活。
    #     ⭐ 所以判的是「`wave` 这个名字是由 plan_wave 的返回值绑定的」。
    binds = [n for n in ast.walk(fn)
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "wave" for t in n.targets)]
    assert binds, "⛔ cmd_autopilot 里没有 wave = ... 这个绑定"
    assert all(isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute)
               and n.value.func.attr == "plan_wave" for n in binds),         "⛔ wave 不是由 plan_wave 的返回值绑定的——切波逻辑又散回循环体里了"


def test_循环体里不许自己再切一次波() -> None:
    """⛔ 抽出来之后循环体里就不该再有切片——两处切波必然分叉，
    而分叉的刹车比没有刹车更坏（它看起来是权威的）。

    ⚠️ **第一版是一条为从未存在过的字符串写的守卫**：它断言
    `"sp.parallel]" not in src`，而真实历史里那行写的是 `[:max(1, sp.parallel)]`
    ——源码里是 `sp.parallel)]`。⛔ 把带缺陷的原版 `cli.py` 放回去，它照样**通过**。
    ⭐ 现在判的是 AST 上的「对列表推导或 ready/wave 做下标切片」这个动作本身。
    """
    from devloop import cli

    fn = next((n for n in ast.walk(ast.parse(inspect.getsource(cli)))
               if isinstance(n, ast.FunctionDef) and n.name == "cmd_autopilot"), None)
    assert fn is not None, "⛔ 找不到 cmd_autopilot"
    bad = [n for n in ast.walk(fn)
           if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
           and (isinstance(n.value, ast.ListComp)
                or (isinstance(n.value, ast.Name)
                    and n.value.id in ("ready", "wave")))]
    assert not bad, "⛔ 循环体里还留着一处自己切波的代码"


def test_余额是从预算上限减出来的而不是写死的() -> None:
    """⚠️ 判据要落在「余额确实来自 max_dispatches」上。

    ⛔ **第一版判的是「表达式里提到了 max_dispatches」**——对抗复核证明
    `remaining=b.max_dispatches`（只删掉减号后半截，缺陷 100% 原样复活）
    照样通过，11 条全绿、全仓结果逐位相同。
    ⭐ 现在判的是「它是一个减法，且被减数是 max_dispatches」。
    """
    from devloop import cli

    fn = next((n for n in ast.walk(ast.parse(inspect.getsource(cli)))
               if isinstance(n, ast.FunctionDef) and n.name == "cmd_autopilot"), None)
    assert fn is not None, "⛔ 找不到 cmd_autopilot"
    #  ⚠️ `next(...)` 必须带 default——不带的话判红时抛的是裸 `StopIteration`，
    #     ⛔ 排错的人读不到下面写好的那句话。对抗复核实测就是这么炸的。
    call = next((n for n in ast.walk(fn)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "plan_wave"), None)
    assert call is not None, "⛔ cmd_autopilot 没调 plan_wave"
    kw = {k.arg: k.value for k in call.keywords}
    assert "remaining" in kw, "⛔ plan_wave 没收到 remaining"
    e = kw["remaining"]
    assert isinstance(e, ast.BinOp) and isinstance(e.op, ast.Sub), \
        (f"⛔ remaining 不是一个减法（实得 {type(e).__name__}）"
         f"——多半直接把上限当余额传了，那等于没收窄")
    assert "max_dispatches" in ast.dump(e.left), \
        f"⛔ 被减数不是 max_dispatches：{ast.dump(e.left)[:120]}"
