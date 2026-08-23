"""删分支之前先把产出**打包并验证**（2026-08-02）。

## ⛔ 为什么需要这一层

`prune.py` 模块头写着：

> ⚠️ 判定「能不能删」的唯一依据是**产出有没有别处留存**……
> 一个三天前的分支若从未被合并、产出也没归档，删了就是第二次 G-26。

⭐ 注意「或**产出也没归档**」——⛔ 而在这之前，**归档这条路根本不存在**。
判定只能二选一：要么合并了（安全），要么别删。

⚠️ 而「能不能删」的判定用了启发式（`empty` 按提交主题猜），2026-08-02 实测
它会误报（`L4` 尖端是 `feat(L4)` 而非 `work(`，是真产出却被判 empty）。
⛔ 那条已经不参与删除判定了，但**判定终究是判断，判断会错**。

## ⭐ 这一层要保证的一件事

**判错了也拿得回来。**

做法：`git bundle create <包> <分支>^..<分支>` —— 只装分支尖端那一个提交
（实测 **2KB**，全历史要 944KB），⛔ 然后**用 `git bundle verify` 真验一遍**，
验过才允许删。

⚠️ 「打了包」不等于「包是好的」——那正是本项目一直在防的形态
（宣称有的能力，没有实际证据）。所以判据落在 `verify` 的退出码上。
"""

from __future__ import annotations

import subprocess

import pytest

from devloop import prune


def _repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()
    for a in (["init", "-q", "-b", "master", "."],
              ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    (p / "base.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=p, check=True,
                   capture_output=True)
    return p


def _branch(repo, name, *, files=1, merge=False):
    subprocess.run(["git", "checkout", "-q", "-b", name], cwd=repo, check=True,
                   capture_output=True)
    for i in range(files):
        (repo / f"{name.replace('/', '_')}_{i}.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", f"work({name}): 干活\n\n分支：{name}"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)
    if merge:
        subprocess.run(["git", "merge", "--no-ff", "-q", "-m", "m", name],
                       cwd=repo, check=True, capture_output=True)


# ── 打包 ──────────────────────────────────────────────────────────

def test_每个分支都打出一个包并验证通过(tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a")
    _branch(repo, "devloop/b", merge=True)
    dest = tmp_path / "archive"

    res = prune.archive_branches(repo, dest)

    assert len(res) == 2
    for r in res:
        assert r.ok, f"⛔ {r.name} 没归档成功：{r.detail}"
        assert r.bundle.exists() and r.bundle.stat().st_size > 0
        assert r.verified, f"⛔ {r.name} 的包没通过 git bundle verify"


def test_包里真的装着那个尖端提交(tmp_path):
    """⛔ 判据落在**包的内容**上，不是「文件存在」。
    ⚠️ 一个 0 字节的文件也「存在」。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a")
    res = prune.archive_branches(repo, tmp_path / "ar")
    sha = subprocess.run(["git", "rev-parse", "devloop/a"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    heads = subprocess.run(["git", "bundle", "list-heads", str(res[0].bundle)],
                           cwd=repo, capture_output=True, text=True).stdout
    assert sha in heads, f"⛔ 包里没有那个提交：{heads}"


def test_归档失败的分支必须报出来而不是静默跳过(tmp_path):
    """⛔ 「没归档成功」与「归档成功」的处置完全不同——
    前者绝对不许删。⚠️ 静默跳过会让它落进「已归档，可删」。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a")
    res = prune.archive_branches(repo, tmp_path / "ar", only=["devloop/根本没有"])
    assert len(res) == 1 and not res[0].ok
    assert res[0].detail, "⛔ 失败了却没说为什么"


def test_写一份人能读的清单(tmp_path):
    """⭐ 包是二进制的。⚠️ 人要能不解包就看出「这里面装的是什么」。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", files=3)
    dest = tmp_path / "ar"
    prune.archive_branches(repo, dest)
    manifest = (dest / "MANIFEST.md").read_text(encoding="utf-8")
    assert "devloop/a" in manifest
    assert "3" in manifest, "⛔ 清单里没写改了几个文件"
    assert "git bundle" in manifest, "⛔ 清单里没写怎么把它取回来"


# ── ⛔ 删除：验过才准删 ────────────────────────────────────────────

def test_没归档过的分支一律不许删(tmp_path):
    """⛔ 这是整层的意义所在。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", merge=True)
    with pytest.raises(ValueError) as e:
        prune.delete_branches(repo, ["devloop/a"], archived=None)
    assert "归档" in str(e.value)


def test_包没验过的分支也不许删(tmp_path):
    """⚠️ 「打了包」不等于「包是好的」。判据落在 `verify` 的结果上。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", merge=True)
    res = prune.archive_branches(repo, tmp_path / "ar")
    res[0].bundle.write_bytes(b"not a bundle")     # 弄坏它
    with pytest.raises(ValueError):
        prune.delete_branches(repo, ["devloop/a"],
                              archived=prune.reverify(repo, res))


def test_归档并验证通过之后才真删(tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", merge=True)
    res = prune.archive_branches(repo, tmp_path / "ar")
    n = prune.delete_branches(repo, ["devloop/a"], archived=res)
    assert n == 1
    left = subprocess.run(["git", "branch", "--list", "devloop/*"], cwd=repo,
                          capture_output=True, text=True).stdout
    assert "devloop/a" not in left


def test_删掉之后真的能从包里恢复(tmp_path):
    """⛔ **这条才是整层的判据**：不是「打了包」，是「**取得回来**」。

    ⚠️ 本项目反复栽的形态就是「宣称有的能力，生产路径上零证据」。
    一个没被真正恢复过的备份，就是那种形态。
    """
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", files=2, merge=True)
    sha = subprocess.run(["git", "rev-parse", "devloop/a"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    res = prune.archive_branches(repo, tmp_path / "ar")
    prune.delete_branches(repo, ["devloop/a"], archived=res)

    #  分支没了，提交对象还在同一个仓库里——所以要在**另一个克隆**里恢复才算数
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)],
                   check=True, capture_output=True)
    r = subprocess.run(["git", "fetch", str(res[0].bundle),
                        "refs/heads/*:refs/heads/restored/*"],
                       cwd=clone, capture_output=True, text=True)
    assert r.returncode == 0, f"⛔ 从包里取不回来：{r.stderr}"
    got = subprocess.run(["git", "cat-file", "-t", sha], cwd=clone,
                         capture_output=True, text=True).stdout.strip()
    assert got == "commit", f"⛔ 恢复出来的仓库里没有那个提交：{got}"


# ── ⛔ `--discard`：点名弃掉（含未合并的）───────────────────────────
#
# ⚠️ **这一组是补一个我当场踩到的洞。**
# `--discard` 的实现写好了，但控制流里 `if not args.delete: return 0` 排在它
# **前面**——于是单给 `--discard` 时函数提前返回，**一支都没删、还印了
# 「只归档了，一个都没删」**。⛔ 那正是第②种假绿：实现了但够不着。
#
# ⭐ 实测当场发现：只有同时给 `--delete` 时才碰巧走得到那段。


def _cli(project, *extra) -> int:
    import sys

    from devloop.cli import main
    argv = ["prune", "--project", str(project), *extra]
    old, sys.argv = sys.argv, ["devloop", *argv]
    try:
        return main(argv)
    finally:
        sys.argv = old


def test_只给discard不给delete也要真删(tmp_path, capsys):
    """⛔ 这就是那个洞：单给 `--discard` 时曾经一支都没删。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/keepme")
    _branch(repo, "devloop/dropme")
    rc = _cli(repo, "--archive", str(tmp_path / "ar"),
              "--discard", "devloop/dropme")
    assert rc == 0
    left = subprocess.run(["git", "branch", "--list", "devloop/*"], cwd=repo,
                          capture_output=True, text=True).stdout
    assert "dropme" not in left, "⛔ --discard 单独给时没生效"
    assert "keepme" in left, "⛔ 把没点名的也删了"


def test_discard能删未合并的分支(tmp_path):
    """⭐ 这是 `--discard` 存在的理由：`--delete` 只碰已合并的，
    而每天真正要做的动作是「弃掉这一支闸没过的」。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/unmerged")          # ⚠️ 没 merge
    assert not prune.scan(repo)[0].safe_to_delete
    _cli(repo, "--archive", str(tmp_path / "ar"), "--discard", "devloop/unmerged")
    assert prune.scan(repo) == []


def test_discard不给archive要拒绝(tmp_path, capsys):
    """⛔ 与 `--delete` 同一条纪律：拿不回来就不许删。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/x")
    assert _cli(repo, "--discard", "devloop/x") == 2
    assert "devloop/x" in subprocess.run(
        ["git", "branch", "--list", "devloop/*"], cwd=repo,
        capture_output=True, text=True).stdout


def test_点名一支不存在的分支要报错不许静默(tmp_path, capsys):
    """⚠️ 打错分支名时静默无事发生，人会以为删掉了。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/x")
    assert _cli(repo, "--archive", str(tmp_path / "ar"),
                "--discard", "devloop/打错了") == 2
    assert "不存在" in capsys.readouterr().out


def test_discard与delete可以一起给(tmp_path):
    """⚠️ 两个开关的判据不同（机器判定 vs 人点名），⛔ 但要能一次跑完。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/merged1", merge=True)
    _branch(repo, "devloop/unmerged1")
    _cli(repo, "--archive", str(tmp_path / "ar"),
         "--discard", "devloop/unmerged1", "--delete")
    assert prune.scan(repo) == []


# ── ⛔⛔ 多提交分支：包只装尖端 → 删完 gc 一次就取不回来了 ─────────
#
# ⚠️ **这是 2026-08-03 独立设计复核抓到的，而被抓的正是前一天刚做的归档层。**
#
# `archive_branches` 打的是 `<分支>^..<分支>`——**只装尖端那一个提交**。
# 依据是 `scan()::empty` 的判据「devloop 分支要么停在基准上，要么尖端就是
# `commit_result` 写的那条工人提交」。
#
# ⛔ 那个前提**不成立**：`commit_result` 只在 `changed_files()` 非空时追加尖端，
# 而工人**在 worktree 里可以自己先提交若干版**。于是一条分支可以有两个以上提交。
#
# 后果链（实测复现）：
#     nightly 印「改 1 个文件」   ← 实际 2 个（`_files_changed` 也只看尖端）
#     archive  → ok=True verified=True，MANIFEST 打 ✅
#     delete   → 成功
#     git gc   ← ⚠️ 日常操作，不需要任何人做错事
#     取回     → ⛔ Repository lacks these prerequisite commits
#
# ⭐ 而 MANIFEST 里那句「父提交在主线上，所以只要主线还在就接得上」
#    对多提交分支**是假的**——父提交是工人自己的中间提交，它不在主线上。
#
# ⛔ 这是**丢数据**，不是少报个数字。判据必须落在「删完之后还取不取得回」。


def _branch_multi(repo, name, n_extra=1):
    """造一条**多提交**的隔离分支：工人先自己提交 n_extra 版，编排方再追尖端。"""
    subprocess.run(["git", "checkout", "-q", "-b", name], cwd=repo, check=True,
                   capture_output=True)
    for i in range(n_extra):
        (repo / f"mid_{i}.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", f"工人自己的中间提交 {i}"],
                       cwd=repo, check=True, capture_output=True)
    (repo / "tip.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm",
                    f"work({name}): 工人产出 · 闸全绿\n\n分支：{name}"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)


def test_多提交分支删掉之后gc了还要取得回来(tmp_path):
    """⛔ **本层的判据就是这一条**：不是「打了包」，也不是「verify 过了」，
    是**删完、gc 完，还取得回来**。

    ⚠️ `git gc` 是日常操作——不需要任何人做错事就会发生。
    """
    repo = _repo(tmp_path)
    _branch_multi(repo, "devloop/multi", n_extra=2)
    sha = subprocess.run(["git", "rev-parse", "devloop/multi"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    res = prune.archive_branches(repo, tmp_path / "ar")
    assert res[0].verified, res[0].detail
    prune.delete_branches(repo, ["devloop/multi"], archived=res)

    #  ⚠️ 日常 gc：把不可达对象真的收掉
    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"],
                   cwd=repo, capture_output=True)
    subprocess.run(["git", "gc", "--prune=now", "-q"], cwd=repo, capture_output=True)

    v = subprocess.run(["git", "bundle", "verify", str(res[0].bundle)],
                       cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert v.returncode == 0, (
        f"⛔ gc 之后包验不过了——产出**真的丢了**：{v.stderr[:200]}")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)],
                   check=True, capture_output=True)
    r = subprocess.run(["git", "fetch", str(res[0].bundle),
                        "refs/heads/*:refs/heads/restored/*"],
                       cwd=clone, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"⛔ 取不回来：{r.stderr[:200]}"
    got = subprocess.run(["git", "cat-file", "-t", sha], cwd=clone,
                         capture_output=True, text=True).stdout.strip()
    assert got == "commit", f"⛔ 恢复出来的仓库里没有那个提交：{got}"


def test_多提交分支的文件数不许只数尖端(tmp_path):
    """⚠️ `nightly` 那一行印「改 N 个文件」，N 只数了尖端一个提交。

    ⛔ 少报的后果不只是数字难看：人按「改 1 个文件」估复核成本，
    实际要看 3 个——⭐ 而这一行的全部用途就是让人估成本。
    """
    repo = _repo(tmp_path)
    _branch_multi(repo, "devloop/multi", n_extra=2)   # 中间 2 个 + 尖端 1 个 = 3
    n = prune._files_changed(repo, "devloop/multi")
    assert n == 3, f"⛔ 只数了尖端：印 {n}，实际 3 个文件"


# ── ⛔⛔ 基准是悬空快照时，包必须自足 ────────────────────────────
#
# ⚠️ **2026-08-03 在真归档目录里找到 1 个坏包，这是它的根因。**
#
# `worktree.snapshot_base()` 用 `git stash create` 固化工作区——那造出的是一个
# **悬空提交**（`WIP on master: ...`），⛔ **永远不在主线上，`git gc` 必收**。
#
# 于是从脏工作区开的分支，它的分岔点就是那个悬空快照：
#     bundle create <悬空快照>..<分支>   → verify ✅、MANIFEST 打 ✅
#     git gc                             → 悬空快照被收走
#     取回                               → ⛔ Repository lacks these prerequisite commits
#
# ⭐ 实测现场：`devloop/sub-smoke-20260729-080249-1048000` 的包前置是
#    `78f754b91230 WIP on master: ...`，而分支已删。趁对象还没被 gc 抢救回来了。
#
# ⛔ 判据：**包的前置提交必须全部从 HEAD 可达**；做不到就退回打全历史。


def _prereqs(repo, bundle) -> list[str]:
    """一个包依赖哪些外部提交（`git bundle verify` 的 requires 段）。"""
    import re

    r = subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=repo,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    seg = r.stdout.split("requires")[1] if "requires" in r.stdout else ""
    return re.findall(r"^([0-9a-f]{40})", seg, re.M)


def test_基准是悬空快照时包必须自足(tmp_path):
    """⛔ 这一条钉的是真实发生过的数据丢失。"""
    repo = _repo(tmp_path)
    #  ⚠️ 造一个脏工作区，再用 `git stash create` 拿到悬空快照——
    #     那正是 `worktree.snapshot_base()` 干的事
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    snap = subprocess.run(["git", "stash", "create"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    assert snap, "⛔ 造不出悬空快照，夹具本身坏了"

    subprocess.run(["git", "checkout", "-q", "-b", "devloop/from-dirty", snap],
                   cwd=repo, check=True, capture_output=True)
    (repo / "out.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm",
                    "work(devloop/from-dirty): 工人产出 · 闸全绿\n\n"
                    "分支：devloop/from-dirty"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)

    res = prune.archive_branches(repo, tmp_path / "ar")
    got = next(a for a in res if a.name == "devloop/from-dirty")
    assert got.verified, got.detail

    #  ⭐ 判据：前置必须全部从 HEAD 可达，⛔ 否则 gc 一次就没了
    for p in _prereqs(repo, got.bundle):
        rc = subprocess.run(["git", "merge-base", "--is-ancestor", p, "HEAD"],
                            cwd=repo, capture_output=True).returncode
        assert rc == 0, (
            f"⛔ 包依赖一个**主线不可达**的提交 {p[:12]}——"
            f"`git gc` 一次就取不回来了")
