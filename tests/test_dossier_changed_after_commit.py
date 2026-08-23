"""⛔ 卷宗的「实际改了 N 个」，在产出**提交之后**必须还数得出来。

## 这条是怎么来的（2026-08-15）

真跑残骸里挖出来的：`f3-pyramid-calibrate`（2026-08-06）那一单——

| | |
|---|---|
| 工人回执 | `subtype=success` · `num_turns=26` · `duration_ms=2299277`（38 分钟）· `$2.53` |
| 工人真改了 | `game/data/species.json`：`graze_max` 0.18→0.72、`herbivore_efficiency` 0.35→0.0875 |
| 产出 | **已提交**，至今躺在 `devloop/f3-pyramid-calibrate-…` 分支上 |
| ⛔ 台账记 | `worker_ok=False` · 「工人超时被杀」· `gate_code=None` |
| ⛔ 卷宗记 | **「实际改了 0 个：⚠️ 一个都没有」** |
| ⛔ 卷宗记 | 「成本：⚠️ 算不出（价目表里没有这个模型）」 |

⇒ **一次真正的成功，被三处记录同时记成了失败。**

## ⭐ 根因：判据的维度错了 —— 提交前该问工作区，提交后必须问提交

`cli.py::_run_unit` 的顺序是：

```
247  touched = wt.changed_files()   ← 提交**之前**量，⭐ 这一处是对的
348  sha = wt.commit_result(...)    ← 产出提交，工作区从此干净
455  changed = wt.changed_files()   ← ⛔ 提交**之后**再量，恒为空
456  dossier.write(..., changed)    ← 于是卷宗对**每一单**都报 0
```

⚠️ 这不是 f3 独有的 —— **只要产出被提交了，卷宗那一行就是假的。**

⭐ 实测反证（2026-08-15，盘上现成的那条分支）：
`git status --porcelain` → **0 行**；`git diff --name-only 基点..HEAD` → `game/data/species.json`。

## ⛔ 为什么这条测试必须存在

「实际改了几个文件」是**人放行前唯一能一眼看懂的产出证据**。
它恒为 0，等于把「工人干了活」这件事从卷宗里抹掉——
⚠️ 而卷宗的全部意义就是让人不必重读 172KB 事件流。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devloop.worktree import Worktree


def _git(cwd: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, f"git {' '.join(a)} 失败：{p.stderr}"
    return p.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """造一个真 git 仓，返回 (路径, 基点 sha)。"""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r, _git(r, "rev-parse", "HEAD")


def _wt(path: Path) -> Worktree:
    w = Worktree.__new__(Worktree)      # ⚠️ 不走建 worktree 那套，只要 .path
    w.path = path
    return w


def test_提交之后还数得出工人改了几个文件(tmp_path: Path) -> None:
    """⭐ 这是那个真事故的最小复现。"""
    r, base = _repo(tmp_path)
    (r / "a.txt").write_text("2\n", encoding="utf-8")     # 工人改了东西
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "worker output")             # ⭐ 提交了

    w = _wt(r)
    #  ⛔ 旧量法在这一刻恒为空 —— 这一行是**反证**，不是要求
    assert w.changed_files() == [], (
        "⚠️ 如果这一行红了，说明测试自己造错了场景（工作区应该是干净的）")
    #  ⭐ 新量法必须还数得出来
    assert w.changed_since(base) == ["a.txt"], (
        "⛔ 提交之后数不出工人改了什么 —— 卷宗那一行会对每一单都报 0，"
        "而那正是 2026-08-06 f3 那一单被记成失败的原因之一")


def test_工人真没改东西时必须是空的(tmp_path: Path) -> None:
    """⭐ 绿检（⛔ 缺了就是只做过红检的判据）。

    ⚠️ 防的是「改成永远报一堆文件」—— 那会让「写任务零改动」这条真判据失灵，
    ⛔ 而 f2 的两单、g1c 那单**确实**是零改动（实测分支 diff = 0 个文件）。
    """
    r, base = _repo(tmp_path)
    assert _wt(r).changed_since(base) == []


def test_没有基点时不许假装工人啥也没改(tmp_path: Path) -> None:
    """⛔ 「不知道」与「零」是两件事。

    ⚠️ 本项目为这个区分栽过多次（`void()` 那一整档就是为它加的）。
    这里的处置是**返回空并由调用方兜底**，⭐ 而调用方那一侧留着老量法。
    """
    r, _ = _repo(tmp_path)
    assert _wt(r).changed_since("") == []


def test_基点是坏的时候要炸不要静默返回空(tmp_path: Path) -> None:
    """⛔ 静默返回空 = 把「查不了」印成「没改动」——七种假绿的第一种。"""
    import pytest
    r, _ = _repo(tmp_path)
    with pytest.raises(RuntimeError):
        _wt(r).changed_since("这不是一个提交号")
