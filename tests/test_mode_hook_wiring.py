"""把 `tools/test_mode_gate.py` 接进 pytest 套件。

## ⛔ 为什么需要这个包装

两道钩子（`devloop-mode-hook.py` 决定模式、`devloop-spend-gate.py` 物理拦命令）
是**整套「花额度要用户按开关」机制的全部实现**。它们的自测写在
`tools/test_mode_gate.py`，52 条断言，写得很细——

**而 `pyproject.toml` 里 `testpaths = ["tests"]`，`tools/` 从来不被收集。**
也就是说：`pytest` 全绿的这两个月里，最该被守住的那个组件**一条都没跑过**。
⚠️ 2026-07-31 才发现。这是第 1 种假绿：守卫的目标压根不在这次运行里。

## ⛔ 为什么不直接把 `tools` 加进 testpaths

`tools/test_mode_gate.py` 里没有任何 `test_*` 函数（是自带 `check()`/`main()`
的独立脚本）。pytest 收进来会**收集到 0 个用例然后静默通过**——
换了个位置的同一种假绿。

## ⛔ 为什么它必须是独立进程

见那个文件的模块 docstring：测试数据里字面含 `devloop.cli autopilot`，
而 PreToolUse 闸匹配 Bash 命令原文，分不出「要执行的命令」和「作为数据的字符串」。
所以它只能以文件形式跑，不能内联。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "test_mode_gate.py"

#  跑了多少条的下限。⛔ 这一条是**防空转**的：只断言退出码 0 的话，
#  脚本被改成一条都不跑（或早退）时退出码同样是 0，测试照样绿。
#  ⚠️ 写下限不写等号——加断言不该让这里变红。
MIN_CHECKS = 60


def test_mode_and_spend_hooks_end_to_end() -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    assert r.returncode == 0, f"钩子自测有不通过项：\n{out[-3000:]}\n{err[-1000:]}"

    m = re.search(r"跑了 (\d+) 条", out)
    assert m, f"⛔ 自测没报出跑了多少条——可能没跑到结尾就退出了：\n{out[-2000:]}"
    n = int(m.group(1))
    assert n >= MIN_CHECKS, (
        f"⛔ 只跑了 {n} 条断言，少于下限 {MIN_CHECKS}。"
        f"要么有段落被跳过，要么脚本早退了——空转即绿。")
