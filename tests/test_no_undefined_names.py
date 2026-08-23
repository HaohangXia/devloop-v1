"""⛔ 少走的分支上不许有未定义的名字。

## 这条为什么值得单独立一道

2026-08-02 两小时内同一形状咬了**两次**：

| 什么时候 | 长什么样 | 为什么单测抓不到 |
|---|---|---|
| VOID 判决接进闸之后 | `_MARK[l.verdict]` 撞上表里没有的 `VOID` → `KeyError` | 崩在**打印路径**上 |
| 波次切分抽成 `plan_wave` 之后 | `head_tools` 被删了，两处 `print` 还在引用 → `NameError` | 同上 |

⚠️ 第一次的后果不是「少印一行」：它崩在 `wt.commit_result()` **之前**，
**工人干完的活直接丢掉**。

⛔ 这类缺陷单元测试天然抓不到——测试只跑主路径，而它专挑主路径不走的分支。

## ⛔ 但这道检查只覆盖了上面两条里的**一条**

| | 本检查抓得到吗 |
|---|---|
| `head_tools` 未定义 → `NameError` | ✅ 抓得到（名字压根没绑定） |
| `_MARK[l.verdict]` 撞上表里没有的键 → `KeyError` | ⛔ **抓不到**——`_MARK` 是定义了的，键存不存在要运行时才知道 |

⚠️ **别把它当成「打印路径已经安全了」。** 它只管「名字有没有绑定」这一件事。
⭐ KeyError 那一类的正解是**让判定集合与打印表同源**——见本文件末尾
`test_判定集合与打印符号表必须同步`，⛔ 不是往这道检查里加规则。

## ⚠️ 为什么不装 ruff / pyflakes

本项目当前零运行时依赖（只有 pydantic）、开发依赖只有 pytest。
为一道检查引入一个 linter 是范围蔓延。⭐ `symtable` 是标准库，
它给出的「引用了但本作用域没绑定、外层也没有、模块级也没有、也不是内建」
恰好就是这一类缺陷的判据。

⚠️ **它不是 linter 的替代品**，只管这一件事。别往里加别的规则。
"""

from __future__ import annotations

import builtins
import pathlib
import symtable

#  ⚠️ 模块级 dunder：symtable 不把它们算进模块符号表，但它们确实存在。
#  ⛔ 白名单只放这些——每多放一个就少抓一类。
_DUNDERS = {"__file__", "__name__", "__doc__", "__package__", "__spec__",
            "__loader__", "__builtins__", "__debug__"}
_ALLOWED = set(dir(builtins)) | _DUNDERS

_PKG = pathlib.Path(__file__).resolve().parent.parent / "devloop"


def _undefined(src: str, where: str) -> list[tuple[str, int, str]]:
    """列出「引用了但哪儿都没绑定」的名字。返回 (函数名, 行号, 名字)。"""
    top = symtable.symtable(src, where, "exec")
    modnames = {s.get_name() for s in top.get_symbols()} | _ALLOWED
    out: list[tuple[str, int, str]] = []

    def walk(tbl: symtable.SymbolTable, bound: set[str]) -> None:
        #  ⚠️ 外层已绑定的名字对内层可见（闭包）——所以要往下传。
        here = bound | {s.get_name() for s in tbl.get_symbols()
                        if s.is_assigned() or s.is_parameter() or s.is_imported()}
        for s in tbl.get_symbols():
            n = s.get_name()
            if (s.is_referenced() and not s.is_assigned() and not s.is_parameter()
                    and not s.is_imported() and n not in bound and n not in modnames):
                out.append((tbl.get_name(), tbl.get_lineno(), n))
        for c in tbl.get_children():
            walk(c, here)

    walk(top, set())
    return out


def test_包里没有未定义的名字() -> None:
    hits = []
    for f in sorted(_PKG.rglob("*.py")):
        for fn, ln, n in _undefined(f.read_text(encoding="utf-8"), str(f)):
            hits.append(f"{f.name}:{ln} {fn}() 引用了未定义的 `{n}`")
    assert not hits, (
        "⛔ 这些名字引用了但哪儿都没绑定——多半躲在少走的分支上：\n  "
        + "\n  ".join(hits))


def test_这道检查真的抓得到() -> None:
    """⛔ 红检必须留在测试里。

    ⚠️ 一道**恒过**的检查与没有检查没区别，而它看起来还挺让人放心
    ——那正是本项目反复栽的第一种假绿（守卫的目标不存在）。
    这里用当天真实撞到的那个形状：名字被删了，只剩打印分支还在引用。
    """
    bad = (
        "def f(wave, ready):\n"
        "    if len(wave) > 1:\n"
        "        print(head_tools)\n"          # ← 就是 2026-08-02 那次
        "    return wave\n"
    )
    hits = _undefined(bad, "<试>")
    assert any(n == "head_tools" for _, _, n in hits), \
        f"⛔ 抓不到 head_tools，这道检查是空的：{hits}"


def test_合法的闭包与内建不许误报() -> None:
    """⚠️ 误报比漏报更坏：它会训练人忽略这道检查。"""
    ok = (
        "import json\n"
        "TOP = 1\n"
        "def outer(a):\n"
        "    b = a + TOP\n"
        "    def inner():\n"
        "        return b + len(json.dumps({})) + TOP\n"
        "    return inner\n"
        "def g(xs):\n"
        "    return [y for y in xs if isinstance(y, int)]\n"
    )
    assert _undefined(ok, "<试>") == []


def test_判定集合与打印符号表必须同步() -> None:
    """⛔ 当天那个 KeyError 的**结构性**解法（本文件上面的静态检查抓不到它）。

    判定集合原来散在两处：`gates._parse` 里的一个元组、`cli._GATE_MARK` 的键。
    加 VOID 时只改了前者 → `_GATE_MARK[l.verdict]` **KeyError**，
    ⛔ 崩在 `wt.commit_result()` **之前**，工人干完的活直接丢掉。

    ⚠️ 后来加的 `.get(..., "?")` 只是不崩了——它会**静默印 `?`**，
    把「符号表没更新」呈现成「判定不认识」。⭐ 所以还要这一条：
    两处必须同步，加第五档时这里当场变红。
    """
    from devloop import cli
    from devloop.gates import VERDICTS

    assert set(cli._GATE_MARK) == set(VERDICTS), (
        f"⛔ 判定集合与打印符号表对不上：\n"
        f"   gates.VERDICTS  = {sorted(VERDICTS)}\n"
        f"   cli._GATE_MARK  = {sorted(cli._GATE_MARK)}\n"
        f"   ⚠️ 少的那档会静默印成 `?`")
