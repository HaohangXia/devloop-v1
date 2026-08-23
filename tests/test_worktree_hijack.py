"""分支劫持：产出会落到用户主线上，而工具报「已固化到隔离分支」（G-54）。

⛔ **今天就活着的缺陷**，与 G-53 同级。

`commit_result` 用 `git commit` 提交，而 **`git commit` 落到哪个分支完全取决于
此刻的 HEAD**；`self.branch` 只是建 worktree 时记下的一个字符串，
提交时**没有任何一处拿它和 HEAD 对过**。

工人对 worktree 有写权限，在里面跑一句 `git symbolic-ref HEAD refs/heads/master`
（⚠️ 比 `git checkout master` 强得多：主 worktree 占着 master 时它照样成功，
且一个文件都不动），产出就落到**用户主线**上。

实测读数（临时仓库 + 真 `commit_result`）：

    master 初始      1b17820
    commit_result 返回  8a56130
    master 现在      8a56130   ← 工人的提交落在主线上了
    隔离分支现在      1b17820   ← 一个提交都没有

而 CLI 随后打印「分支 devloop/… @ 8a56130（产出已固化，删 worktree 不会丢）」
——**一句假话**。且 `prune` 会把那个空分支判成可删。

同时这直接违反宪法 C-4：**任何触及用户主线或集成分支的 `git commit`**
都必须停下来问人。
"""

from __future__ import annotations

import subprocess

import pytest


def _repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    (p / "f.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=p, check=True,
                   capture_output=True)
    return p


def _sha(repo, rev):
    return subprocess.run(["git", "rev-parse", "--short", rev], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


def test_HEAD被改到主线时必须拒绝提交(tmp_path):
    """⛔ 核心断言：提交前必须确认 HEAD 就是本 worktree 的隔离分支。"""
    from devloop import worktree as W
    repo = _repo(tmp_path)
    before = _sha(repo, "HEAD")
    wt = W.create(repo, "hijack")

    # 模拟工人：把 worktree 的 HEAD 指到主线
    head_name = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"],
                               cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{head_name}"],
                   cwd=wt.path, check=True, capture_output=True)
    (wt.path / "f.txt").write_text("工人改的\n", encoding="utf-8")

    with pytest.raises(Exception) as e:
        wt.commit_result("hijack", gate_ok=True)

    msg = str(e.value)
    assert "分支" in msg and head_name in msg, "必须点名 HEAD 实际在哪"
    assert _sha(repo, head_name) == before, "⛔ 主线一个字节都不许动"
    assert (wt.path / "f.txt").read_text(encoding="utf-8") == "工人改的\n", \
        "⛔ 产出必须留在磁盘上——拒绝提交不等于丢掉工人的活（G-26 的教训）"


def test_拒绝提交时不许伪装成没有产出(tmp_path):
    """⛔ 不许 `return None`：那会让 CLI 打印「工人没有产出，无可固化」，
    而工人明明有产出，且整批还会返回 0。
    那是 G-26（产出静默丢失）+「静默降级」双重复发。"""
    from devloop import worktree as W
    repo = _repo(tmp_path)
    wt = W.create(repo, "hijack2")
    head_name = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"],
                               cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{head_name}"],
                   cwd=wt.path, check=True, capture_output=True)
    (wt.path / "f.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(Exception) as e:
        wt.commit_result("hijack2", gate_ok=True)
    assert "未丢失" in str(e.value) or "保留" in str(e.value), \
        "必须明确告诉人：产出还在，在哪里"


def test_detached状态同样拒绝(tmp_path):
    """detached HEAD 时 `rev-parse --abbrev-ref HEAD` 返回字面量 `HEAD`，
    与隔离分支名不等——同样必须拦下，别让它掉进「碰巧不等于主线所以放行」。"""
    from devloop import worktree as W
    repo = _repo(tmp_path)
    wt = W.create(repo, "det")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt.path,
                         capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "--detach", sha], cwd=wt.path,
                   check=True, capture_output=True)
    (wt.path / "f.txt").write_text("y\n", encoding="utf-8")
    with pytest.raises(Exception):
        wt.commit_result("det", gate_ok=True)


def test_正常路径照常固化到隔离分支(tmp_path):
    """⚠️ 防回归：没被劫持时必须照常提交，且**只**提交到隔离分支。"""
    from devloop import worktree as W
    repo = _repo(tmp_path)
    main = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    before = _sha(repo, main)
    wt = W.create(repo, "normal")
    (wt.path / "f.txt").write_text("工人的正常产出\n", encoding="utf-8")

    sha = wt.commit_result("normal", gate_ok=True)
    assert sha, "正常路径必须真的提交"
    assert _sha(repo, main) == before, "主线不许动"
    assert _sha(repo, wt.branch) == sha[:7], "产出必须在隔离分支上"


def test_没有改动时仍然返回None(tmp_path):
    """⚠️ 防回归：工人什么都没改 → None，这是原有语义，不能被新断言改掉。"""
    from devloop import worktree as W
    repo = _repo(tmp_path)
    wt = W.create(repo, "empty")
    assert wt.commit_result("empty", gate_ok=True) is None


def test_工人提交不许改主仓库的git身份(tmp_path):
    """⛔ **linked worktree 与主仓库共用 `.git/config`。**

    所以在 worktree 里跑 `git config user.name` 是**改主仓库的身份**，改完不还原。

    实测后果（2026-07-29 审计抓到，已发生）：主仓库 user.name 被写成
    `devloop-worker[sub-smoke]`，此后**维护者亲手写的 24 个提交**全挂了工人身份。
    于是这个身份机制存在的唯一理由（一眼分清人和机器）被它自己毁掉；
    连带 `prune.py` 按作者邮箱判分支空不空的逻辑也永久失效——
    master 尖端带着工人邮箱，今后从它开的每个分支都会被判「有产出，别删」。

    ⚠️ 判据必须落在**主仓库的 config 上**，不是「提交作者对不对」——
    提交作者本来就是对的，坏的是它留下的副作用。
    """
    import subprocess
    from devloop import worktree as wt_mod

    def git(*a, cwd=tmp_path):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    git("init", "-q")
    git("config", "user.name", "真人")
    git("config", "user.email", "human@example.com")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    git("add", "-A"); git("commit", "-q", "-m", "base")

    wt = wt_mod.create(tmp_path, "某单", base=git("rev-parse", "HEAD").stdout.strip())
    (wt.path / "b.txt").write_text("y", encoding="utf-8")
    sha = wt.commit_result("某单", gate_ok=True)
    assert sha, "该有产出"

    # 提交作者是工人 —— 这部分本来就对
    author = git("log", "-1", "--format=%an <%ae>", wt.branch).stdout.strip()
    assert "worker@devloop.local" in author, f"提交作者该是工人：{author}"

    # ⛔ 而主仓库的身份**一个字都不许变**
    assert git("config", "--get", "user.name").stdout.strip() == "真人", \
        "主仓库 user.name 被工人身份污染了"
    assert git("config", "--get", "user.email").stdout.strip() == "human@example.com", \
        "主仓库 user.email 被工人身份污染了"
