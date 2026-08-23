"""闸的第四档 VOID：**我这一道没有可验的东西**。

## ⛔ 为什么需要它

2026-08-01 eco-ob 首跑，闸的输出里有这么一行：

    ✓ 闸·禁改清单: worktree 内无 .devloop/（该项目未跟踪它，符合预期）

它报 **PASS**，而它**验了 0 条**。而我把它写进了 `--require-pass`
——⛔ **点名一道恒过的闸，等于没点名。**

⚠️ 已有的 SKIP 表达的是「我被开关跳过了」（`DEVLOOP_SKIP_TESTS=1` 那种），
语义是「本来能验，这次没验」。而这里是「**我本来就没东西可验**」——
两者对「能不能放行」的含义相同（都不算数），但对**排查**的含义完全不同：
SKIP 要去查谁把开关打开了，VOID 要去查为什么这个项目里它是空的。

⛔ 判据放在闸自己身上，不靠外面猜。**只有闸知道它有没有东西可验。**
外面用启发式（比如「detail 里没有数字就算空过」）会误伤
`基线守卫: ref_*.json 未被改动` 那种真验过的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devloop.config import ProjectPaths
from devloop.gates import run_gates

GATES = """#!/bin/sh
printf 'PASS\t真验过的\t通过 38 失败 0\n'
printf 'VOID\t没东西可验的\t本项目没有 .devloop/，无可查\n'
exit 0
"""


@pytest.fixture
def proj(tmp_path: Path) -> ProjectPaths:
    (tmp_path / ".devloop").mkdir(parents=True)
    (tmp_path / ".devloop" / "gates.sh").write_text(GATES, encoding="utf-8")
    return ProjectPaths(tmp_path)


def test_VOID_被解析出来(proj: ProjectPaths) -> None:
    r = run_gates(proj)
    assert [l.verdict for l in r.lines] == ["PASS", "VOID"], r.lines


def test_VOID_不算通过(proj: ProjectPaths) -> None:
    """⛔ 它不该混进 `passed_count`——那个数是「验过几道」的读数。"""
    r = run_gates(proj)
    assert r.verified == 1, f"VOID 被算成通过了：{r.verified}"


def test_点名一道VOID的闸必须拒绝放行(proj: ProjectPaths) -> None:
    """⭐ 这是整条的存在理由。"""
    r = run_gates(proj, require_pass=["没东西可验的"])
    assert not r.passed, "⛔ 点名了一道 VOID 的闸却放行了——那正是首跑踩到的"
    assert "VOID" in r.summary(), r.summary()


def test_点名真验过的那道仍然放行(proj: ProjectPaths) -> None:
    """⚠️ 别把正常路径也拦了。"""
    assert run_gates(proj, require_pass=["真验过的"]).passed


def test_没点名时VOID不导致整体失败(proj: ProjectPaths) -> None:
    """⚠️ VOID 本身不是错误——「这个项目里这道确实没东西可查」是合法状态。
    ⛔ 它只在**被点名**时才致命。"""
    assert run_gates(proj).passed


def test_VOID_与SKIP在摘要里分得开(proj: ProjectPaths, tmp_path: Path) -> None:
    """⚠️ 两者都不算通过，但排查方向完全不同——摘要里必须能区分。"""
    (tmp_path / ".devloop" / "gates.sh").write_text(
        "#!/bin/sh\n"
        "printf 'SKIP\t被开关跳过的\t\n'\n"
        "printf 'VOID\t没东西可验的\t\n'\n"
        "exit 0\n", encoding="utf-8")
    s = run_gates(proj).summary()
    assert "SKIP" in s and "VOID" in s, s


# ── ⛔ 显示路径（2026-08-02 实测撞到的阻断） ──────────────────────

def test_VOID_在CLI的打印路径上不许崩(proj: ProjectPaths, capsys) -> None:
    """⛔ **上面六条全打在 `run_gates` 的解析层——测了错的那一层（第 4 种假绿）。**

    真实事故：`gates.py` 把 VOID 解析好了、测好了，而 `cli.py` 里两处
    `dict(PASS=..., FAIL=..., SKIP=...)[l.verdict]` 不认识它 → `KeyError: 'VOID'`。

    ⚠️ 崩的位置尤其糟：在 `_run_unit` 里它发生在 `wt.commit_result()` **之前**
    ——于是被兜底 except 吞成「这一单失败」，**工人干了活、闸跑完了（真花钱），
    产出却永远不会被固化成分支**。
    """
    from devloop import cli

    rc = cli.main(["gates", "--project", str(proj.project)])
    out = capsys.readouterr().out
    assert "KeyError" not in out
    assert "没东西可验的" in out, out
    assert rc in (0, 1), rc


def test_四个判定各有各的符号() -> None:
    """⛔ 用 `.get` 兜底之后，最容易出的新毛病是「全都印成 ?」——
    ⚠️ 那样不崩了，但也看不出区别，等于把问题从「炸」换成「静默降级」。"""
    from devloop.cli import _GATE_MARK

    assert set(_GATE_MARK) == {"PASS", "FAIL", "SKIP", "VOID"}, _GATE_MARK
    assert len(set(_GATE_MARK.values())) == 4, f"符号有重复：{_GATE_MARK}"


def test_判定符号表没有被同名常量覆盖() -> None:
    """⚠️ 实测撞到：第一版叫 `_MARK`，而本文件后面还有一个 `_MARK`（作业状态表），
    ⛔ **同名的模块级常量会被后定义的整个覆盖**——不报错、不警告，
    只是安静地让先定义的那个消失，于是全部判定印成 `?`。"""
    import ast
    import inspect

    from devloop import cli

    tree = ast.parse(inspect.getsource(cli))
    names = [t.id for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)]
    dup = {n for n in names if names.count(n) > 1}
    assert "_GATE_MARK" not in dup, f"⛔ `_GATE_MARK` 被重复定义了：{dup}"
