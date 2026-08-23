"""⛔ 三档的账必须出现在**人读到的那句话**里，不只是出现在 TOML 里。

## 缺陷的形状（2026-08-03 实测）

前一天新增了第三档 `[[judged_elsewhere]]`（判据在代码里的条款），
理由是「`summary()` 那句尾巴少报了覆盖面」。做完的状态是：

| 在哪 | 状态 |
|---|---|
| `.devloop/constitution.toml` | ✅ 6 条登记好了 |
| `tests/test_constitution_coverage.py` | ✅ 全绿 |
| ⛔ **`devloop constitution check` 印出来的那句话** | **一次都没提过第三档** |

原因：`constitution.py` 里有 **10 个** `ConstitutionResult(...)` 构造点，
新字段我**只记得给 2 个传**——而那 2 个都是 `code=2` 的故障路径。
所有正常路径（`check_files` / `check_refs` / `check_tree` / `check_records` /
`check_workspace`）一个都没传。

⭐ 加那一档的**唯一目的**就是让尾巴别少报，而它在输出里整个落空了。
与 G-94（`--discard` 实现了但控制流够不着）同形：**实现了，生产路径够不着**。

## ⛔ 为什么已有的测试抓不到

`test_constitution_coverage.py` 判的是「TOML 里三档相加等于散文条数」。
⚠️ 它读的是**配置文件**，而缺陷在**从配置到输出**的那一段。
⭐ 判据的维度错了——账平不平，跟人看不看得见，是两件事。

## ⭐ 本文件的两条判据

1. **结构**：`constitution.py` 里不许有裸的 `ConstitutionResult(...)`，
   一律走 `Constitution.result()`。⛔ 这是断根，不是补 8 个漏。
2. **后果**：真跑一次判定，尾巴里必须出现第三档。
   ⚠️ 只判 ① 不够——将来有人绕过 `result()` 也一样出事；
   ⚠️ 只判 ② 不够——它只覆盖跑到的那条路径。
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import subprocess

from devloop import constitution as C
from devloop.config import ProjectPaths

_SRC = pathlib.Path(C.__file__)

_TOML = """schema = 1

[[protected_file]]
clause = "A-1"
title  = "不得修改宪法本身"
path   = ".devloop/constitution.toml"

[[unjudged]]
clause = "X-9"
why    = "判不了"

[[judged_elsewhere]]
clause = "Y-1"
title  = "由代码挡的第一条"
by     = "some_module.some_guard：这里写明谁在挡"

[[judged_elsewhere]]
clause = "Y-2"
title  = "由代码挡的第二条"
by     = "another_module.another_guard：这里也写明谁在挡"

[tree]
coverage = "none"
why      = "夹具项目没有树内基线"
"""


def _proj(tmp_path) -> ProjectPaths:
    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                             encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(_TOML, encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    return ProjectPaths(p)


# ── ① 结构：结论只许从 Constitution 长出来 ────────────────────────

def test_模块里不许有裸的结果构造() -> None:
    """⛔ 断根判据。

    ⚠️ 「把 8 处补齐」不是修复——那只是把同一个错**再抄一遍**的机会往后挪。
    ⭐ 下一个给 `ConstitutionResult` 加字段的人，不该需要记住去改 10 个地方。
    """
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    #  ⚠️ 按**节点身份**排除，⛔ 不按行号——`ast.arguments` 之类的节点没有 lineno，
    #     写成行号集合会当场 AttributeError（第一版就这么错的）。
    inside = {
        id(n)
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef) and cls.name == "Constitution"
        for fn in cls.body
        if isinstance(fn, ast.FunctionDef) and fn.name == "result"
        for n in ast.walk(fn)
    }
    naked = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "ConstitutionResult" and id(n) not in inside]
    assert not naked, (
        f"⛔ {_SRC.name} 第 {naked} 行直接构造了 ConstitutionResult。\n"
        f"   ⚠️ 每个裸构造点都是一次「新字段忘了传」的机会——2026-08-03 就是\n"
        f"      10 个点里漏了 8 个，导致第三档在输出里整个不见。\n"
        f"   ⭐ 改用 `con.result(code, hits, stderr)`。")


def test_每一条判定路径都要带上三档() -> None:
    """⭐ ① 判「不许裸构造」，这条判「`result()` 自己有没有把三档都装进去」。

    ⚠️ 缺了这条，把 `result()` 写成只传两档一样能过第一条。
    """
    con = C.Constitution(pathlib.Path("."), [], [], ["U-1"], ["E-1", "E-2"],
                         {}, pathlib.Path("x.toml"))
    r = con.result(0)
    assert r.unjudged == ["U-1"], r.unjudged
    assert r.elsewhere == ["E-1", "E-2"], r.elsewhere
    #  ⛔ 额外的「判不了」要**追加**不要顶掉——check_tree 覆盖 0 条路径时靠这个
    r2 = con.result(0, extra_unjudged=("树内覆盖 0 条",))
    assert r2.unjudged == ["U-1", "树内覆盖 0 条"], r2.unjudged
    assert r2.elsewhere == ["E-1", "E-2"], r2.elsewhere


# ── ② 后果：真跑一次，尾巴里必须有第三档 ──────────────────────────

def test_真跑一次判定尾巴里要有第三档(tmp_path) -> None:
    """⛔ **这条是本文件的核心。** 判据落在人真正读到的那句话上。

    ⚠️ 已有的 `test_constitution_coverage.py` 判的是 TOML——账平不平，
    跟人看不看得见，是两件事。缺陷正好长在两者之间。
    """
    paths = _proj(tmp_path)
    con = C.load(paths)
    assert con.elsewhere == ["Y-1", "Y-2"], f"夹具没解析出来：{con.elsewhere}"

    C.write_anchor(paths, con)
    s = C.verify_anchor(paths, con).summary()
    assert "另有 2 条判据在代码里" in s, f"⛔ 尾巴里没有第三档：\n   {s}"
    assert "Y-1" in s and "Y-2" in s, s
    #  ⚠️ 第二档也不许因此消失
    assert "未覆盖条款 1 条" in s, s


def test_活工作区那条也要带上三档(tmp_path) -> None:
    """⛔ `check_workspace` 是**最后一个**漏网的：它连 `Constitution` 都不收，
    传的是 `[]`，于是尾巴印出「⚠️ 未登记任何『判不了』的条款」——**一句假话**。

    ⚠️ 而 `cli.py` 只在 `not r.clean` 时才印它——也就是**真命中、人最认真读
    的那一刻**，尾巴是假的。⭐ 严重度不在「少一行信息」，在「那一行是错的」。
    """
    paths = _proj(tmp_path)
    con = C.load(paths)
    #  ⚠️ 先提交再改。⛔ 未跟踪文件改内容，`git status` 两次都是 `?? a.py`，
    #     两次快照相同、判不出变化——夹具必须真的造出「已跟踪文件被改」。
    (paths.project / "a.py").write_text("x = 1\n", encoding="utf-8")
    for a in (["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=paths.project, check=True,
                       capture_output=True)
    before = C.workspace_state(paths.project)
    (paths.project / "a.py").write_text("x = 2\n", encoding="utf-8")
    r = C.check_workspace(con, before, C.workspace_state(paths.project))

    assert not r.clean, "夹具前提变了：改了文件却没命中"
    s = r.summary()
    assert "未登记任何" not in s, f"⛔ 命中时印出了假话：\n   {s}"
    assert "未覆盖条款 1 条" in s, s
    assert "另有 2 条判据在代码里" in s, s


def test_接线上真的把con传给了活工作区那条() -> None:
    """⛔ 这个项目栽过三次「实现了但生产路径没调」。判据落在 AST 上。"""
    from devloop import cli

    call = next(
        (n for n in ast.walk(ast.parse(inspect.getsource(cli)))
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and n.func.attr == "check_workspace"), None)
    assert call is not None, "⛔ cli.py 里找不到 check_workspace 调用"
    assert call.args and isinstance(call.args[0], ast.Name) \
        and call.args[0].id == "con", \
        "⛔ cli.py 调 check_workspace 时第一个参数不是 con——尾巴又会变成假话"
