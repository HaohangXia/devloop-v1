"""`dispatch --why-parallel` 必须能单独跑（D-2）。

## ⛔ 形状：守卫写了、注释解释了为什么、但**够不着**

`cli.py::cmd_dispatch` 第一句就是：

    #  ⛔ 判据说明是纯打印，⚠️ **必须在读任何文件、连任何后端之前**返回
    #     ——否则 `--why-parallel` 会因为 `--project`/`--task` 不存在而炸。
    if args.why_parallel:
        print(fanout.explain()); return 0

SPEC.md 也把这条写成了规格。⚠️ 但 argparse 上 `--project` 是 `required=True`、
`--task/--task-dir` 是 `required=True` 的互斥组——**它们在 `cmd_dispatch`
被调用之前就拒了**。实测：

    devloop dispatch --why-parallel
    → error: the following arguments are required: --project
    → 退出码 2

⛔ 于是那个提前返回是**死代码**，而文档里印着的命令**照抄就报错**。
这是第②种假绿：实现了、注释齐全、**但生产路径够不着**。

## ⚠️ 改法的风险

把 `required=True` 从 argparse 挪进函数体，代价是**正常用法的报错质量**
——argparse 的报错带 usage 行，手写的容易更差。⛔ 所以下面同时钉住
「漏了 --project 时仍然报得清楚」，不许为了修这条把那条弄坏。
"""

from __future__ import annotations

import pytest

from devloop.cli import main


def test_why_parallel能单独跑(capsys) -> None:
    """⛔ 文档里印的就是这一条命令，照抄必须能跑。"""
    assert main(["dispatch", "--why-parallel"]) == 0
    out = capsys.readouterr().out
    assert "并行" in out or "parallel" in out.lower(), out


def test_why_parallel不许去读项目目录(capsys) -> None:
    """⚠️ 传一个**不存在**的项目也要照常打印——证明它真的在读文件之前就返回了。"""
    assert main(["dispatch", "--why-parallel",
                 "--project", "C:/根本没有这个目录", "--task", "也没有.md"]) == 0


def test_漏了project仍然报得清楚(capsys) -> None:
    """⛔ 不许为了修上面那条，把正常用法的报错质量弄坏。"""
    with pytest.raises(SystemExit) as e:
        main(["dispatch", "--task", "x.md"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--project" in err, f"⛔ 报错里没点名缺了哪个参数：{err}"


def test_漏了任务书仍然报得清楚(capsys) -> None:
    with pytest.raises(SystemExit) as e:
        main(["dispatch", "--project", "."])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--task" in err, f"⛔ 报错里没说要 --task/--task-dir：{err}"


def test_文档里印的那条命令与实现一致() -> None:
    """⛔ 判据落在**文档原文**上：出错信息里教人跑的命令必须真能跑。

    ⚠️ 本项目栽过：`cmd_audit` 打印的派单示例里子命令名是拆开拼的
    （`_D = "dis" + "patch"`），⭐ 那不是为了绕闸——是因为 PreToolUse 闸匹配
    的是 Bash 命令原文，分不出「要执行的命令」和「作为数据的字符串」。
    """
    import inspect

    from devloop import cli

    src = inspect.getsource(cli)
    assert "--why-parallel" in src
    #  ⛔ 若哪天把提前返回删了，这条会连同上面两条一起红。
    fn = inspect.getsource(cli.cmd_dispatch)
    head = fn.split("\n")[:12]
    assert any("why_parallel" in l for l in head), \
        "⛔ 提前返回被挪走了——它必须在读任何文件之前"
