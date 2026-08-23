"""⛔ 拿存档当对照的闸，必须知道「基准是哪个提交」（G-125）。

## 这条是怎么来的

2026-08-08 第一次把「逐比特中性」接成一道真闸，然后真派了一单。

⛔ **那道闸数学上必然红**：它拿来当对照的存档录在 `5e746f1`，
而派单基准是 **7 个提交之后**的 `f7636381ab35`，中间隔着一次
**标题就写着「速度=出手频率」的、故意改变世界**的改动。

⚠️ 后果不是「闸报了个错」，是**它把一个做对了的工人逼疯**：

    工人原话①：「我确认逻辑无懈可击但仍破基线，说明我漏了某处副作用。」
    工人原话②：「我已把改动 revert。现在跑一次纯 baseline，验证工具的确定性。」

⭐ 它 **42 秒**就改对了（同样的改法主模型验过：快 45%、世界逐比特不变），
⛔ 然后**把自己对的代码删了**，花 49 分钟追一个不存在的 bug，
超时交白卷 —— **烧掉 5,146,226 tokens，产出 0 个文件**。

## ⭐ 第一性：这个数只有编排方知道

闸跑在 worktree 里，而那里的 `HEAD` **已经带上了工人的提交** ——
⛔ 闸自己**算不出**基准。⭐ 知道基准的是编排方，所以必须由编排方传进去。

## ⛔ 而更根本的一条（这条测试只能守住上面那半）

主模型给每个守卫都做过**红检**（故意弄坏 → 必须报警），
⛔ **却从没做过反过来的那一半**：

> **什么都不改 → 它必须是绿的。**

⚠️ 一个只验过「坏东西会报警」的守卫，可能对**所有**东西都报警 ——
那和一根坏掉的火警没区别。⭐ 这条叫**绿检**，已写进 `templates/task.md`。
"""

from __future__ import annotations

import ast
import inspect

from devloop import cli, gates


def test_run_gates_收得下基准提交() -> None:
    """⛔ 判据落在**签名**上：没有这个参数，下面两条都无从谈起。"""
    sig = inspect.signature(gates.run_gates)
    assert "base_commit" in sig.parameters, (
        "⛔ `run_gates` 收不下基准提交——拿存档当对照的闸永远不知道"
        "「这份存档配不配得上眼前这份代码」")
    p = sig.parameters["base_commit"]
    assert p.default == "", (
        "⛔ 缺省必须是空字符串（= 编排方没给）。⚠️ 别给它编一个默认提交——"
        "那会让「没传」和「传了个错的」变成同一件事，而项目侧的闸靠空值判 VOID")


def test_基准提交真的进了闸的环境变量() -> None:
    """⛔ 判据落在**实现**上，不是签名上。

    ⚠️ G-113 的教训：参数加了、没接上，等于没修。今天已经踩过两次。
    """
    src = inspect.getsource(gates.run_gates)
    assert "DEVLOOP_BASE_COMMIT" in src, (
        "⛔ `base_commit` 收下了却没写进子进程环境——闸依然拿不到它")
    tree = ast.parse(src.lstrip())
    #  ⭐ 必须是「把参数塞进 env 字典」，⛔ 不是塞了个常量进去
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == "DEVLOOP_BASE_COMMIT":
                names = {n.id for n in ast.walk(v) if isinstance(n, ast.Name)}
                assert "base_commit" in names, (
                    "⛔ `DEVLOOP_BASE_COMMIT` 塞进去的不是那个参数——"
                    "⚠️ 写死一个值，比不传更坏：闸会拿着一个假基准报绿")
                return
    raise AssertionError("⛔ 没找到把 DEVLOOP_BASE_COMMIT 放进 env 的那一处")


def test_派单路径真的把基准传下去了() -> None:
    """⭐ 判据落在**调用点**：`_run_unit` 手里就有 `base`，不传就是白加。

    ⛔ 这是这条修法最容易漏的一半 —— 也正是本项目数到第十次的那个形状：
    **同一件事有两条路，只改了有人盯着的那条。**
    """
    tree = ast.parse(inspect.getsource(cli._run_unit).lstrip())
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", "")) == "run_gates"):
            continue
        kw = {k.arg: k for k in n.keywords}
        assert "base_commit" in kw, (
            "⛔ `_run_unit` 调闸时没传基准——⚠️ 它手里明明有 `base`。\n"
            "   拿存档当对照的闸会因此只能报 VOID，或者更糟：报一个**必然的假红**，\n"
            "   而工人会以为是自己错了，然后把做对的活删掉（2026-08-08 实测）")
        #  ⭐ 传的必须是那个变量，⛔ 不是空串占位
        names = {x.id for x in ast.walk(kw["base_commit"].value) if isinstance(x, ast.Name)}
        assert "base" in names, (
            "⛔ 传进去的不是 `base` 那个变量——⚠️ 占位符会让这道守卫恒绿")
        return
    raise AssertionError("⛔ `_run_unit` 里找不到调用 run_gates 的地方")


def test_模板里立了绿检这条规矩() -> None:
    """⛔ 这条守的是**方法**，不是某一行代码。

    ⚠️ 「不许让工人自己跑大考」这条教训 2026-08-04 就总结出来了，
    ⛔ 但只抄进了两份**具体任务书**，**从没进过模板** ——
    于是 08-05 复发一次、08-08 又一次。
    ⭐ 教训只有进了模板才会被下一个人看见。
    """
    from pathlib import Path

    import devloop

    tpl = (Path(devloop.__file__).resolve().parent.parent
           / "templates" / "task.md").read_text(encoding="utf-8")
    for kw, why in (
        ("绿检", "⛔ 「判据上线前必须在『什么都不改』的输入上跑一次、必须绿」这条没进模板"),
        ("不许自己跑全量回归", "⛔ 「大考归闸、不归工人」这条没进模板——已复发两次"),
        ("不许回滚", "⛔ 「判据红而你的论证成立时，停下写报告、别删自己的活」这条没进模板"),
    ):
        assert kw in tpl, f"{why}（在 templates/task.md 里 grep 不到「{kw}」）"
