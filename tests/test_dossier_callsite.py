"""⛔ 判据必须装在**调用点**上，⛔ 不是装在零件上。

## 这条是怎么来的（2026-08-16，对抗式验收把它揪出来的）

2026-08-15 修了两件事，各配了 4 条测试，全绿：

| 修的东西 | 配的测试 |
|---|---|
| `worktree.py::changed_since`（提交之后还数得出改了什么） | `test_dossier_changed_after_commit.py` |
| `dossier.py` 不许说「没点名的红闸不影响结论」 | `test_dossier_unnamed_red_gate.py` |

⛔ **可那 8 条全都直接调零件，没有一条落在 `cli.py` 的调用点上。**
验收员做变异测试，实测两次：

```
# 变异①：cli.py 那行改回旧写法（= 把 f3 那次事故原封不动放回去）
-  changed = wt.changed_since(base) if sha else (wt.changed_files() or [])
+  changed = wt.changed_files() or []
$ python -m pytest -q     →  ⛔ 818 passed，一条都没红

# 变异②：把 `gate_lines=` 那整段传参删掉
$ python -m pytest -q     →  ⛔ 818 passed，一条都没红
```

⇒ **这两处修好的东西，下一个人随手就能改回去，而且屏幕全绿。**

## ⭐ 又是那个形状（第 20 次）

> **同一件事有两条路。修好的永远是「有人盯着」的那条。**

这次两条路是：**零件本身**（`changed_since` / `build`，有测试盯着）
与**零件有没有被装上去**（`cli.py` 那两行，没人盯）。
⚠️ 而 2026-08-06 真出事的**恰恰是后一截** —— 零件那时压根不存在，
出事的是「填卷宗的人拿错了尺子」。

## ⭐ 判据分两层，缺一不可

1. **端到端**（`test_跑完一单卷宗里的改动数必须是真的`）：
   真跑 `cli._run_unit`，让假工人真改文件、真提交，然后**读生成的卷宗**。
   ⭐ 这一条抓得住任何改法，⛔ 不认写法。
2. **调用点形状**（AST）：便宜，专抓「整段删掉」。
   ⚠️ 它是代用品，⛔ 所以不许单独存在——只作为第 1 条的补充。
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path

import pytest

from devloop import cli, dossier


# ── 第一层：端到端 ────────────────────────────────────────────────

def _git(cwd: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, f"git {' '.join(a)} 失败：{p.stderr}"
    return p.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".devloop" / "tasks").mkdir(parents=True)
    #  ⭐ 两道闸都绿，⛔ 但下面只点名「语法」——「回归」因此落进「没点名」那一堆。
    #     这正是 2026-08-15 第一版说错话的形状：它会被写成「都是 SKIP/VOID，没验」。
    (r / ".devloop" / "gates.sh").write_text(
        "printf 'PASS\\t语法\\t好\\n'\n"
        "printf 'PASS\\t回归\\t也好\\n'\n"
        "exit 0\n", encoding="utf-8", newline="\n")
    (r / ".devloop" / "tasks" / "u1.md").write_text(
        "# 角色\n\nx\n\n# 任务\n\ny\n\n# 改动范围\n\n- `f.txt`（随便改点什么）\n\n# 禁令\n\n- z\n",
        encoding="utf-8")
    (r / "f.txt").write_text("1\n", encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def test_跑完一单卷宗里的改动数必须是真的(tmp_path: Path, monkeypatch) -> None:
    """⭐⭐ 这一条就是 2026-08-06 那次事故的端到端复现。

    ⛔ 它**不认任何写法**——只问「最后交到人手里的那张纸上，数字对不对」。
    """
    from devloop import cli as C, dispatch as D, worktree as wt_mod
    from devloop.config import ProjectPaths
    from devloop.models import Receipt, TaskSpec, WorkerConfig

    r = _repo(tmp_path)
    paths = ProjectPaths(r)
    spec = TaskSpec.load(r / ".devloop" / "tasks" / "u1.md")
    base = _git(r, "rev-parse", "HEAD")

    #  ⭐ 假工人：在 worktree 里**真改一个文件**。
    #  ⛔ 它自己不提交——提交由编排方做，而**提交之后再数**正是出事的那一步。
    def _fake(spec_, paths_, cfg_, **kw):
        (Path(kw["cwd"]) / "f.txt").write_text("2\n", encoding="utf-8")
        return D.DispatchResult(
            spec_.name,
            Receipt(is_error=False, result="ok", num_turns=3, duration_ms=900),
            None)

    monkeypatch.setattr(C, "dispatch_one", _fake)
    #  ⛔ **不桩掉闸**——让真闸跑。桩掉就又变成「只验零件不验接线」。

    ok, lines = C._run_unit(
        spec, paths, WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="implement", max_turns=5, gate_fp=None, writes=True,
        base=base, require_pass=("语法",), stage="u")

    #  ⚠️ 2026-08-16：⛔ 别把路径写死成 `u-u1.md`。
    #     卷宗文件名现在带单号（`{stage}-{task}-{unit_id}.md`），
    #     ⭐ 那正是「同一份任务书跑两次，第一遍被第二遍悄悄盖掉」的修法。
    #     写死名字会让这条测试**假红**，喊的还是「卷宗根本没生成」
    #     ——而实际上卷宗生成得好好的。
    #  ⭐ 这不是「改测试迁就实现」：它断言的是**改动数**，路径只是取材方式。
    found = sorted((r / ".devloop" / "dossier").glob("u-u1*.md"))
    assert found, (
        f"⛔ 卷宗根本没生成——人放行前唯一看得懂的材料没了。屏幕上说的是：{lines}")
    assert len(found) == 1, f"⛔ 一单跑出了 {len(found)} 份卷宗：{[p.name for p in found]}"
    md = found[0].read_text(encoding="utf-8")
    assert "实际改了 1 个" in md and "f.txt" in md, (
        "⛔ 卷宗把一次真干成了的活记成「实际改了 0 个」——"
        "一模一样的字 2026-08-06 出现在 f3 那一单的卷宗上。\n"
        f"卷宗原文：\n{md}")
    #  ⭐ 顺带钉死越界那一格：任务书声明的是 `` `f.txt`（随便改点什么） ``，
    #     ⛔ 拿整行去比会判「越界 1 个」——真任务书 100% 误报就是这么来的。
    assert "**越界" not in md, (
        "⛔ 工人只改了声明允许改的文件，卷宗却盖了越界的红章。"
        "⚠️ 真任务书里声明写成 ``f.txt`（说明）` 这种给人看的话，"
        "拿整行去跟裸路径比**永远不相等** ⇒ 100% 误报。\n"
        f"卷宗原文：\n{md}")
    #  ⭐⭐ 端到端钉死 2026-08-15 第一版引进的那句假话：
    #     「回归」这道闸**真跑了、真通过了**，只是没被点名。
    #     ⛔ 卷宗不许把它说成「没验」，⛔ 也不许对它喊红。
    #  ⚠️ 只看**说到「回归」的那几行**，⛔ 别全文匹配「没验」——
    #     卷宗别处的说明文字里也有这两个字，全文匹配会变成一条假红判据。
    say = [ln for ln in md.splitlines() if "回归" in ln and ln.startswith("-")]
    assert say, f"⛔ 卷宗里根本没提「回归」这道闸\n{md}"
    joined = "\n".join(say)
    assert "验过而且通过" in joined, (
        "⛔ 卷宗把一道**验过而且通过**的闸说成别的——"
        "第一版把它归进「都是 SKIP/VOID，没验」，"
        "那是把「没验的说成没事」换个方向再说一遍。\n"
        f"说到「回归」的那几行：\n{joined}\n\n卷宗原文：\n{md}")
    assert "照样判这一单不通过" not in md, (
        "⛔ 闸全绿，卷宗却对没点名的那道喊红。⚠️ 假红比假绿贵："
        "一根天天响的火警，真着火时没人理。\n"
        f"卷宗原文：\n{md}")


# ── 第二层：调用点形状（AST，便宜，专抓「整段删掉」）────────────────

def _dossier_write_call() -> ast.Call:
    src = inspect.getsource(cli._run_unit)
    tree = ast.parse(src.lstrip())
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "write"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "dossier"):
            return n
    pytest.fail("⛔ `_run_unit` 里找不到 `dossier.write(...)` —— 卷宗压根没在生成")


def test_调用点必须把闸的判定传下去() -> None:
    """⛔ 少了它，卷宗对**每一道**没点名的闸都按最坏情况喊红——一根天天响的火警。"""
    call = _dossier_write_call()
    kw = {k.arg for k in call.keywords}
    assert "gate_lines" in kw, (
        "⛔ `dossier.write` 没收到 `gate_lines`。后果：卷宗分不出"
        "「没点名但红了」「没点名但验过通过」「没点名的 SKIP/VOID」，"
        "对每一道都喊「按最坏情况当红算」。⚠️ 假红比假绿贵。")
    v = next(k.value for k in call.keywords if k.arg == "gate_lines")
    names = {x.attr for x in ast.walk(v) if isinstance(x, ast.Attribute)}
    assert {"name", "verdict"} <= names, (
        "⛔ 传下去的不是闸自己报的 (道名, 判定) —— 卷宗拿到的是个编出来的东西")


def test_调用点必须用提交后数得出来的那把尺子() -> None:
    """⛔ 专抓「改回 `changed_files()`」这一种改法。

    ⚠️ 这是代用品判据（只认写法），⭐ 真正承重的是上面那条端到端。
    """
    src = inspect.getsource(cli._run_unit)
    assert "changed_since" in src, (
        "⛔ `_run_unit` 不再用 `changed_since` —— 产出一提交，"
        "卷宗的「实际改了 N 个」就会对每一单都报 0（2026-08-06 f3 那次事故）")


def test_卷宗收得下闸的判定清单而不是字典() -> None:
    """⛔ 字典会让同名的两行后盖前：`FAIL 语法` 被随后的 `PASS 语法` 静默吞掉。"""
    p = inspect.signature(dossier.build).parameters
    assert "gate_lines" in p, "⛔ `build` 收不下 (道名, 判定) 清单"
    assert "gate_verdicts" not in p, (
        "⛔ 又改回字典了——闸对同一道名打两行时，前面那个 FAIL 会被吞掉，"
        "而「没点名的红闸照样拦单」这句话就从侧门失效了")
