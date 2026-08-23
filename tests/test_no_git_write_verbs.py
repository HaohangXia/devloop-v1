"""⛔ 工具**不许**移动主工作区的 HEAD、不许对外推送。

## 这条为什么值得一道静态检查

`prune.scan()` 的 `merged = git branch --merged` **不带任何 committish 参数**
——它的含义是「合进**当前 HEAD**」。而 `safe_to_delete == merged`，
且 `prune --archive --delete` 现在会**真删分支 + 真 `worktree remove --force`**。

⛔ 所以任何一次主树 `checkout`，都会同时做到两件事：

1. 改掉「哪些分支可删」的集合
2. 让 `wt_mod.resolve_base()` 返回错的基准 → **宪法的树内判据从此锚在错基线上**
   —— 每一单都被拿错误基线判，产出**可信的绿**

⚠️ 而 `halt --kill` 走 `taskkill /PID <pid> /T /F`（无 `finally`、无 `atexit`），
进程完全可能死在 `checkout -b` 与 `checkout master` 之间，
**主仓永久停在一条工具自造的分支上**。

## ⛔ 关于 push

全仓从无 `push`。加它等于装第一条对外写通道，而：

- 报告/台账里的 `error`、`gate_detail`、`findings.evidence` **是工人（LLM）写的自由文本**
- 宪法 B-4「对外发送」的登记状态是 `out-of-reach`，处置写死为「**事前不给能力**」
- ⚠️ `origin` 是 HTTPS，凭据过期时 `git push` 会弹交互并**永久挂住**
  ——把「失败」变成「什么都没发生」，⛔ 而那恰是要治的症状本身

⭐ 要保底用 `prune --archive`：`git bundle` + 真验 + MANIFEST，
不碰主线、不碰远端、不需要凭据。
"""

from __future__ import annotations

import ast
import pathlib

_PKG = pathlib.Path(__file__).resolve().parent.parent / "devloop"

#  ⛔ 这几个是「会改动主工作区 HEAD」或「对外写」的 git 子命令。
_BANNED = {"checkout", "switch", "push", "reset", "rebase", "cherry-pick"}


def _git_subcommands(src: str) -> list[tuple[int, str]]:
    """找出源码里所有形如 `[... "git" ..., "<子命令>", ...]` 的字面量列表。

    ⚠️ 判据落在 **AST 的字面量列表**上，不落在子串——
    ⛔ 判子串会把注释、docstring 里说明用的 `git push` 也判红，
    而那些正是解释「为什么不许」的地方（误报会训练人忽略这道检查）。
    """
    out: list[tuple[int, str]] = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.List):
            continue
        vals = [e.value for e in n.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if "git" not in vals:
            continue
        for v in vals:
            if v in _BANNED:
                out.append((getattr(n, "lineno", 0), v))
    return out


def test_不许有会移动主树HEAD或对外写的git子命令() -> None:
    hits = []
    for f in sorted(_PKG.rglob("*.py")):
        for ln, verb in _git_subcommands(f.read_text(encoding="utf-8")):
            hits.append(f"{f.name}:{ln} → git {verb}")
    assert not hits, (
        "⛔ 这些地方会移动主工作区 HEAD 或对外写：\n  " + "\n  ".join(hits)
        + "\n  ⚠️ 主树 checkout 会改掉 `git branch --merged` 的含义"
          "（prune 据此真删分支），并让宪法的树内判据锚在错基线上。")


def test_这道检查真的抓得到() -> None:
    """⛔ 红检留在测试里：一道恒过的检查与没有检查没区别。"""
    bad = 'subprocess.run(["git", "-C", str(p), "checkout", "-b", "x"])\n'
    assert _git_subcommands(bad), "⛔ 抓不到 checkout，这道检查是空的"
    bad2 = 'subprocess.run(["git", "push", "origin", "master"])\n'
    assert _git_subcommands(bad2), "⛔ 抓不到 push"


def test_文档里讲解用的git_push不许误报() -> None:
    """⚠️ 误报比漏报更坏：它会训练人忽略这道检查。
    ⛔ 而解释「为什么不许 push」的地方**必然**要写出 `git push` 这几个字。"""
    ok = ('"""⛔ 不许 git push——见 B-4。"""\n'
          '#  git checkout 会移动 HEAD\n'
          'MSG = "要合就自己跑 git merge <分支>"\n')
    assert _git_subcommands(ok) == []


def test_允许的git子命令不受影响() -> None:
    """⚠️ 反向钉住：`commit` / `bundle` / `worktree` / `branch -D` 都是要用的。"""
    ok = ('subprocess.run(["git", "-C", p, "commit", "-m", "x"])\n'
          'subprocess.run(["git", "-C", p, "bundle", "verify", f])\n'
          'subprocess.run(["git", "-C", p, "worktree", "remove", "--force", w])\n'
          'subprocess.run(["git", "-C", p, "branch", "-D", n])\n')
    assert _git_subcommands(ok) == []
