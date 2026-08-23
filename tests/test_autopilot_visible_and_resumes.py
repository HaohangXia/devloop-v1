"""⛔ 自动驾驶必须**看得见**、并且撞额度能自己睡到点续跑（G-117 / G-118）。

## 两条，同一个形状

本项目重复了七次的那个形状：**同一件事有两条路，
装好的永远是「有人盯着」的那条，漏掉的永远是「没人盯着」的那条**。
（硬拒只装手动那条 G-101 · 三道红线只传手动那条 · 模板改了副本不跟 G-114 ·
台账改了读侧不跟 G-113 · 刹车没接到正在跑的单 G-107 · 变量塞了没告诉工人 G-112 ·
锚只重了 eco-ob 忘了 devloop 自己）

⭐ 而**挂一夜走的路，恰恰全是「没人盯着」的那一条**。

### G-117 · `devloop halt` 看不见正在跑的自动驾驶

`jobs.launch` 只在 `dispatch --detach` 那条路上被调用（起一个后台进程）。
`autopilot` 跑在**前台**，从不登记 ⇒ `halt` 回你一句「**没有正在跑的作业**」。

⛔ 半夜想叫停，只能守在那个窗口按 Ctrl-C
——⚠️ 而「挂一夜」的定义就是**没人守在窗口前**。

### G-118 · 撞额度就停在半夜，不会自己接着跑

`dispatch` 有 `--wait-for-reset`（睡到额度恢复再接着派，
判据是 `quota.can_auto_resume`——⛔ 只对能算出精确恢复时刻的那种上限生效）。
⚠️ `autopilot` **没有这个开关**：撞墙即 `Stop` + `break`。

⭐ 于是「挂一夜」实际是「挂到撞墙为止」，而订阅的 5 小时窗口
多半在半夜就撞上了——早上起来是一半的进度。
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent

from devloop import cli, jobs


# ══════════════════════════════════════════════════════════════════════
#  G-117 · 看得见
# ══════════════════════════════════════════════════════════════════════

def test_能把当前进程登记成作业(tmp_path) -> None:
    """⭐ 判据：登记之后 `listing` 找得到它，且 pid 是**自己**。"""
    (tmp_path / ".devloop").mkdir(parents=True)
    j = jobs.register_self(tmp_path, ["autopilot", "--stage", "s"], ["t1"],
                           telemetry=tmp_path / ".devloop" / "telemetry.jsonl")
    assert j.meta["pid"] == os.getpid(), "⛔ 登记的不是自己的进程号，急停会杀错人"
    assert [x.id for x in jobs.listing(tmp_path)] == [j.id]


def test_登记之后急停看得见它(tmp_path) -> None:
    """⛔ 主判据落在**人真会敲的那条命令**上，不是内部数据结构。"""
    from devloop import halt as halt_mod

    (tmp_path / ".devloop").mkdir(parents=True)
    jobs.register_self(tmp_path, ["autopilot", "--stage", "s"], ["t1"],
                       telemetry=tmp_path / ".devloop" / "telemetry.jsonl")
    text, alive = halt_mod.report(tmp_path, do_kill=False)
    assert "没有正在跑的作业" not in text, (
        f"⛔ 登记了却还是看不见——半夜只能守着窗口按 Ctrl-C：\n{text}")
    assert alive == 1


def test_register_self不起新进程() -> None:
    """⛔ 它和 `launch` 的唯一区别就在这里。⚠️ 起了新进程 = 一夜跑两份。"""
    src = inspect.getsource(jobs.register_self)
    assert "Popen" not in src and "subprocess" not in src, \
        "⛔ `register_self` 里出现了起进程的代码"


def test_自动驾驶真的登记了自己() -> None:
    """⭐ 后果判据落在**调用点**——⚠️ 判据写好了没接上 = 等于没修。"""
    src = inspect.getsource(cli.cmd_autopilot)
    assert "register_self" in src, (
        "⛔ `cmd_autopilot` 没登记自己——`devloop halt` 依旧看不见它")


# ══════════════════════════════════════════════════════════════════════
#  G-118 · 撞额度能自己续跑
# ══════════════════════════════════════════════════════════════════════

def test_自动驾驶有等额度恢复的开关() -> None:
    """⛔ `dispatch` 有 `--wait-for-reset`，`autopilot` 也必须有。"""
    #  ⭐ 判据是**真让解析器解一遍**，⛔ 不是在源码里 grep
    #     ——G-113 的教训：源码文本是行为的代用品。
    #  ⚠️ 解析器建在 `main` 里没暴露出来，所以用「传全参数看它认不认」来间接触发：
    #     argparse 拒绝未知参数时退出码是 **2**；⭐ 任何别的退出码都说明它认了。
    #  ⭐ argparse 拒绝未知参数时会 **`SystemExit(2)`**；
    #     ⚠️ 参数被认下之后，这条命令会因为别的原因失败（这里是「没有 .devloop/」）
    #     并**正常返回一个退出码**——⭐ 那正是我们要的证据。
    try:
        cli.main(["autopilot", "--project", str(_HERE),
                  "--stage", "决不存在的阶段名", "--wait-for-reset", "--dry-run"])
    except SystemExit as e:                     # noqa: PT011 —— 就是要看它的 code
        assert e.code != 2, (
            "⛔ 自动驾驶不认 `--wait-for-reset`——挂一夜实际是「挂到撞墙为止」")


def test_主循环真的会去等_而且只在算得出恢复时刻时才等() -> None:
    """⭐ 用 **AST** 判，⛔ 不在源码文本里 grep。

    ⚠️ 我第一版就是 grep 的，结果在**注释**里匹配到了 `can_auto_resume`
    ——G-113 那条教训（判据落在代用品上）的又一次现场，⭐ 而且是当天第四次。

    两件事一起钉：

    - 撞额度那一段**真的会去 sleep**（不是无条件 `break`）；
    - ⛔ 而且 sleep 之前**问过** `can_auto_resume` —— 周上限是固定时间重置，
      拿 5 小时去估会一路撞墙，⚠️ **每次撞都真花额度**。
    """
    import ast

    tree = ast.parse(inspect.getsource(cli.cmd_autopilot).lstrip())

    def calls(node, name: str) -> bool:
        return any(isinstance(x, ast.Call)
                   and getattr(x.func, "attr", getattr(x.func, "id", "")) == name
                   for x in ast.walk(node))

    #  ⭐ 找**同时**满足「条件里问了 can_auto_resume」+「体内会 sleep」的那个 if。
    #  ⛔ 注释匹配不到——AST 里根本没有注释。
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and calls(n.test, "can_auto_resume") \
                and calls(n, "sleep"):
            return
    raise AssertionError(
        "⛔ 找不到「问过 can_auto_resume 之后才 sleep」的那一段。\n"
        "   ⚠️ 要么它压根不等（挂一夜 = 挂到撞墙为止），\n"
        "   ⚠️ 要么它不问就等（周上限会让它连撞十几个小时，每次都真花额度）。")
