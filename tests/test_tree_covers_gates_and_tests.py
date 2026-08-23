"""⛔ 工人改闸、改测试、然后提交——闸和宪法**一起转绿**（CRITICAL）。

## 缺陷的形状（2026-08-03 对抗审计实测复现）

工人在 worktree 里做三件事，然后 `git add -A && git commit`：

1. 把 `tests/test_constitution_coverage.py` 的 10 条断言全改成 `assert True or ...`
2. 往 `.devloop/gates.sh` 末尾追一行 `fail=0`
3. 往 `.devloop/rules-digest.md` 追两行

结果：

```
PASS  语法        devloop/ 下全部 .py 可编译
PASS  测试守卫    tests/ 未被改动              ← ⛔ 字面为假
PASS  禁改清单    .devloop/ 已跟踪且未被改动    ← ⛔ 字面为假
PASS  pytest      ...
宪法：无宪法命中
```

单判 `ok=True`，分支被推荐给人 `git merge`。**合入后闸恒绿、覆盖率测试恒绿。**

## ⛔ 两层守卫为什么同时瞎

| 守卫 | 判据 | 为什么瞎 |
|---|---|---|
| `gates.sh` 的「测试守卫」「禁改清单」 | `git status --porcelain` | ⛔ 工人**一提交**，工作区就干净了 |
| 宪法 `check_tree`（base 锚定） | 与 base 提交比对 | ⛔ `.devloop/**` 与 `tests/**` **一条都没登记** |

⭐ 而 `constitution.toml` 开头白纸黑字写着树内判据存在的**唯一理由**：

> ⚠️ 为什么不是 `git status`：工人把改动**提交掉**，工作区就干净了，
> 任何基于 status 的判据都会说「没事」。base 锚定照样抓得到。

**该被它接住的那两条，此前一条都没进来。**

⚠️ `[[protected_file]]` 也接不住：那一档锚的是**活项目**里的文件，
而工人干活在 **worktree** 里——两个不同的地方。

## ⚠️ 修这条最容易踩的坑：把守卫做成天天红

`.devloop/` 下有大量**工具自己写的运行产物**（`reports/`、`autopilot/`、
`telemetry.jsonl`…）。实测本仓：174 个「base 里没有的新增文件」里
**144 个是产物**，其中 **90 个**会被这几条模式命中。

⛔ 不排除它们，`.devloop/**` 上线第一单就红，然后被人永久忽略——
⭐ **天天红的守卫等于没有守卫**，那比不加还糟。
"""

from __future__ import annotations

import subprocess

from devloop import constitution as C
from devloop.config import ProjectPaths

_TOML = """schema = 1

[[protected_file]]
clause = "A-1"
title  = "不得修改宪法本身"
path   = ".devloop/constitution.toml"

[[protected_tree]]
clause = "A-1"
title  = "不得修改闸与宪法（worktree 内）"
glob   = ".devloop/**"

[[protected_tree]]
clause = "A-1"
title  = "不得改动已有测试"
glob   = "tests/**"
allow_added = true

[[unjudged]]
clause = "X-9"
why    = "判不了"
"""


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _proj(tmp_path):
    """主项目 + 一个 worktree。返回 `(paths, worktree, base_sha)`。

    ⛔ **必须分成两个目录**——生产里就是这样：`load()` 读**主项目**的宪法，
    而工人干活在 `git worktree add` 出来的**独立检出**里。
    ⚠️ 第一版夹具把两者混成同一个目录，于是「工人删光测试」那条
    在 `load()` 阶段就炸了（模式匹配不到任何文件），根本走不到判据——
    ⭐ **夹具的失真掩盖了被测行为。**
    """
    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / "tests").mkdir()
    (p / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\nfail=0\npytest || fail=1\nexit $fail\n",
        encoding="utf-8")
    (p / ".devloop" / "rules-digest.md").write_text("# 规矩\n\n不许改闸。\n",
                                                    encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(_TOML, encoding="utf-8")
    (p / "tests" / "test_核心.py").write_text(
        "def test_x():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        _git(p, *a)
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "base")
    base = _git(p, "rev-parse", "HEAD").stdout.strip()
    wt = tmp_path / "wt"
    _git(p, "worktree", "add", "-q", "--detach", str(wt), base)
    return ProjectPaths(p), wt, base


def _check(paths, wt, base):
    con = C.load(paths)                      # ⭐ 宪法读**主项目**
    return C.check_tree(con, worktree=wt, base=base)   # ⭐ 判的是 **worktree**


# ── ① ⛔ 那个攻击必须被抓住 ────────────────────────────────────────

def test_工人改闸并提交必须命中(tmp_path) -> None:
    """⭐ **本文件的核心。** 修复前这条是 `code = 0`。"""
    paths, wt, base = _proj(tmp_path)
    g = wt / ".devloop" / "gates.sh"
    g.write_text(g.read_text(encoding="utf-8") + "\nfail=0  # 工人加的\n",
                 encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "worker self-commit")

    r = _check(paths, wt, base)
    assert not r.clean, "⛔ 闸被改了还提交了，宪法却零命中"
    assert any("gates.sh" in h.detail for h in r.hits), \
        [h.detail for h in r.hits]


def test_工人把已有测试的断言废掉并提交必须命中(tmp_path) -> None:
    """⛔ `assert True or ...` —— 断言还在，只是永远为真。
    ⚠️ 这是「改自己的考卷」最省事的写法。"""
    paths, wt, base = _proj(tmp_path)
    t = wt / "tests" / "test_核心.py"
    t.write_text("def test_x():\n    assert True or 1 + 1 == 2\n",
                 encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "worker self-commit")

    r = _check(paths, wt, base)
    assert not r.clean, "⛔ 已有测试被废掉还提交了，宪法却零命中"
    assert any("test_核心.py" in h.detail for h in r.hits), \
        [h.detail for h in r.hits]


def test_工人删掉整个测试目录并提交必须命中(tmp_path) -> None:
    paths, wt, base = _proj(tmp_path)
    (wt / "tests" / "test_核心.py").unlink()
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "worker self-commit")

    r = _check(paths, wt, base)
    assert not r.clean and any("被删除" in h.detail for h in r.hits), \
        [h.detail for h in r.hits]


def test_工人改规则摘要并提交必须命中(tmp_path) -> None:
    paths, wt, base = _proj(tmp_path)
    d = wt / ".devloop" / "rules-digest.md"
    d.write_text(d.read_text(encoding="utf-8") + "\n（工人加的）\n",
                 encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "worker self-commit")
    assert not _check(paths, wt, base).clean


def test_工人往devloop里塞新文件并提交必须命中(tmp_path) -> None:
    """⚠️ 「加一个」和「改一个」都要抓——⛔ 只判改动会漏掉塞进来的东西。"""
    paths, wt, base = _proj(tmp_path)
    (wt / ".devloop" / "gates-helper.sh").write_text(
        "exit 0\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "worker self-commit")

    r = _check(paths, wt, base)
    assert not r.clean and any("gates-helper" in h.detail for h in r.hits), \
        [h.detail for h in r.hits]


# ── ② ⛔ 不许天天红 ───────────────────────────────────────────────

def test_新建测试文件必须放行(tmp_path) -> None:
    """⭐ 分级语义：改已有 = 命中，**新建 `test_*.py` = 放行**。

    ⚠️ 任务书经常**要求**工人写新测试——一刀切会每单必红，
    ⛔ 而天天红的守卫等于没有守卫。
    """
    paths, wt, base = _proj(tmp_path)
    (wt / "tests" / "test_新写的.py").write_text(
        "def test_y():\n    assert True\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "worker adds a test")

    r = _check(paths, wt, base)
    assert r.clean, f"⛔ 新建测试被判红了：{[h.detail for h in r.hits]}"


def test_运行产物一律不算新增文件(tmp_path) -> None:
    """⛔ 实测真仓：174 个「base 里没有的新增文件」中 **144 个是产物**，
    其中 **90 个**会被这几条模式命中。不排除 = 上线第一单就红。

    ⭐ 与 G-93（`findings.jsonl` 让 A-2 全阶段连坐）同一个形状：
    **工具自己写进项目的东西，被守卫当成了人为改动。**
    """
    paths, wt, base = _proj(tmp_path)
    d = wt / ".devloop"
    for rel in ("reports/x.json", "autopilot/夜跑.json", "jobs/a.json",
                "handoff/b.md", "dossier/c.md", "telemetry.jsonl",
                "findings.jsonl", "tasks/fix-1.md", "tasks/verify-1.md"):
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n", encoding="utf-8")
    (wt / "tests" / "__pycache__").mkdir()
    (wt / "tests" / "__pycache__" / "x.pyc").write_bytes(b"\x00")

    r = _check(paths, wt, base)
    assert r.clean, f"⛔ 运行产物被判红了：{[h.detail for h in r.hits]}"


def test_什么都不做必须干净(tmp_path) -> None:
    paths, wt, base = _proj(tmp_path)
    assert _check(paths, wt, base).clean


# ── ③ 产物名单本身 ────────────────────────────────────────────────

def test_产物判据认得出各类缓存与运行目录() -> None:
    for rel in (".devloop/reports/a.json", ".devloop/jobs/a.json",
                ".devloop/handoff/a.md", ".devloop/autopilot/a.json",
                ".devloop/dossier/a.md", ".devloop/telemetry.jsonl",
                ".devloop/telemetry.jsonl.pre-g28.bak",
                ".devloop/findings.jsonl", ".devloop/tasks/fix-x.md",
                ".devloop/tasks/verify-x.md",
                "devloop/__pycache__/x.pyc", "tests/__pycache__/y.pyc",
                ".ruff_cache/z", "node_modules/pkg/index.js"):
        assert C._is_artifact(rel), f"⛔ 没认出产物：{rel}"


def test_产物判据不许把真东西当产物() -> None:
    """⛔ 过宽和过窄一样坏——把 `gates.sh` 当产物就等于守卫失效。"""
    for rel in (".devloop/gates.sh", ".devloop/constitution.toml",
                ".devloop/constitution.md", ".devloop/rules-digest.md",
                ".devloop/tasks/v1-ledger-fields.md",
                ".devloop/plans/nightly.toml",
                "tests/test_核心.py", "devloop/constitution.py",
                "pyproject.toml", "_review/answer_keys/L4.py"):
        assert not C._is_artifact(rel), f"⛔ 把真东西当成产物了：{rel}"


def test_产物名单不许从gitignore读() -> None:
    """⛔ `check_tree` 取未跟踪文件时刻意不带 `--exclude-standard`，理由是
    `.gitignore` 与 `info/exclude` **工人都写得到**。

    ⚠️ 排除名单要是从那里读，等于把守卫的开关交给被守卫的人。

    ⛔ **判据不能是子串。** 第一版写的是 `"--exclude-standard" not in 源码`，
    当场被**解释它为什么不用**的那句注释判红：

    > `-o` 仍不带 `--exclude-standard`：带了就受 `.gitignore` …支配

    ⚠️ 那是「判据维度错」——注释里提到一个字符串，与代码真的传了它，是两回事。
    ⭐ 改判 AST：任何字符串字面量**作为实参**出现在调用里才算数。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(C))
    bad = [n.value for n in ast.walk(tree)
           if isinstance(n, ast.Call)
           for a in n.args
           if isinstance(a, ast.Constant) and isinstance(a.value, str)
           and a.value in ("--exclude-standard", ".gitignore",
                           "info/exclude")
           for n in [a]]
    assert not bad, f"⛔ 这些被当成实参传出去了：{bad}——排除名单落到工人手里了"


def test_本仓的配置真的把闸和测试盖住了() -> None:
    """⛔ **这条才是钉住那个 CRITICAL 洞的那一条。**

    ⭐ 红检时发现的一件事值得写下来：上面那 5 条「攻击必须被抓住」
    在**修复前的代码上是绿的**——因为它们自带的夹具 TOML 已经登记了
    `.devloop/**` 与 `tests/**`。

    ⚠️ 也就是说：**判据代码一直都在，洞纯粹在配置里。**
    ⛔ 那正是这个项目反复栽的那一类——`rules-digest.md` 写着「`.devloop/`
    任何文件禁改」，而真正执行它的是别处的脚本；这次是反过来：
    执行的代码在，**清单里没有目标**。

    ⭐ 所以判据必须落在**本仓的 constitution.toml** 上，
    ⚠️ 落在代码上抓不到（代码是对的）。
    """
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parent.parent
    d = tomllib.loads((root / ".devloop" / "constitution.toml")
                      .read_text(encoding="utf-8"))
    globs = {t.get("glob", "") for t in d.get("protected_tree", [])}
    assert any(g.startswith(".devloop/") for g in globs), \
        f"⛔ .devloop/ 没进受保护路径：{globs}"
    assert any(g.startswith("tests/") for g in globs), \
        f"⛔ tests/ 没进受保护路径：{globs}"
    t = next(x for x in d["protected_tree"] if x.get("glob", "").startswith("tests/"))
    assert t.get("allow_added"), \
        "⛔ tests/ 没开 allow_added——新建测试会被判红，守卫会天天红"
