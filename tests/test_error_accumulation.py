"""⛔ 一单里多个失败原因时，`error` 不许后写覆盖前面全部。

## 形状（对抗复核抓到）

`cli._run_unit` 里有四处 `dc_replace(res, error=...)`，**每一处都是整体替换**：

    183  宪法命中：{T2 判定}
    202  闸未通过：{闸摘要}          ← ⛔ 无条件覆盖上面那句
    224  写任务零改动：…             ← ⛔ 再覆盖
    247  宪法命中（T5）：…           ← ⛔ 再覆盖

实测：工人改了受保护文件（T2 命中）**同时**闸判 FAIL（禁改清单那道），
台账 error 只剩「闸未通过：…」→ 归类成「活没达标」，
⛔ **归类器排第一优先、语义最重的「宪法命中（需要人批准）」整档丢失**，
交接单里连「需要人批准」四个字都不出现。

⚠️ 两者的处置完全不同：
  · 宪法命中 = **在等你批准**，重试一万次也不会变绿
  · 活没达标 = 重试有意义，那正是 `retries` 的用处
把前者说成后者，会让自动驾驶拿满 retries 去撞一堵需要人来开的门。
"""

from __future__ import annotations

import ast
import inspect

from devloop import cli


def test_error是累积的不是覆盖的() -> None:
    """⛔ 判据落在 AST 上：四处 error= 里，除第一处外都必须是**追加**。

    ⚠️ 判子串「+=」不行——`dc_replace` 是函数调用，追加要体现在传进去的
    表达式里（`_add_err(res, ...)` 或 `error=res.error + ...`）。
    ⭐ 所以判的是「没有任何一处把 error 赋成一个光秃秃的字面量/f-string」。
    """
    fn = next((n for n in ast.walk(ast.parse(inspect.getsource(cli)))
               if isinstance(n, ast.FunctionDef) and n.name == "_run_unit"), None)
    assert fn is not None, "⛔ 找不到 _run_unit"
    naked = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "dc_replace"):
            continue
        for kw in n.keywords:
            if kw.arg == "error" and isinstance(kw.value, (ast.Constant, ast.JoinedStr)):
                naked.append(ast.unparse(kw.value)[:60])
    assert not naked, (
        "⛔ 这几处把 error 整体覆盖掉了，前面的失败原因会消失：\n  "
        + "\n  ".join(naked))


def test_累积函数把宪法命中排在前面() -> None:
    """⭐ 归类器按顺序匹配「宪法命中」优先。⛔ 追加时若把它甩到后面，
    虽然字还在，但被 `in` 匹配到的仍然是它——这条钉的是**语义**：
    宪法命中必须仍然能被识别出来。"""
    add = getattr(cli, "_add_err", None)
    assert add is not None, "⛔ 没有累积失败原因的函数"
    e = add("", "宪法命中：改了受保护文件")
    e = add(e, "闸未通过：1 过 / 1 未过")
    assert "宪法命中" in e and "闸未通过" in e
    assert e.index("宪法命中") < e.index("闸未通过"), \
        "⚠️ 先发生的原因要排在前面——读的人按时间顺序理解一单的经过"


def test_只有一个原因时不许加多余的分隔符() -> None:
    add = cli._add_err
    assert add("", "闸未通过：x") == "闸未通过：x"
    assert add(None, "闸未通过：x") == "闸未通过：x"


def test_重复的同一条原因不许堆两遍() -> None:
    """⚠️ 台账那一行会被人读，⛔ 堆两遍等于噪音。"""
    add = cli._add_err
    assert add("闸未通过：x", "闸未通过：x") == "闸未通过：x"
