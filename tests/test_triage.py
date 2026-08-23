"""早上 30 秒分诊：⛔ 判定必须**在清单上直接看得见**（2026-08-02）。

## ⛔ 卡住分诊的是一个 3 行的丢弃

`prune.scan()` **已经在跑** `git log -1 --format=%s%n%b <分支>`，而
`worktree.py::commit_result` 写进那条提交信息的正是：

    work(<任务>): 工人产出 · 闸全绿 | 闸未过 | 未跑闸

⛔ 而 `scan()` 只从这个字符串里取了一个 `empty` 布尔值，**把闸的判定扔掉了**。

后果：`prune.report()` 的「别删」块对**五道闸全绿的产出**和**闸没过的垃圾**
打印的是**一模一样的一行**：

    ⚠️ 有未合并的产出——删了就没了

⚠️ 人今天必须逐支 `git show` 才分得开 —— **这就是为什么 30 秒读不完**。

## ⭐ 顺便说清「回档」这件事

本项目的主线**永远不会被自动改动**：全仓唯一的 git 写动词是
`worktree.py::commit_result`，且提交前硬断言 `HEAD == 隔离分支`；
`checkout` / `switch` / `push` / `reset` 全仓零次出现（有静态检查钉住，见
`test_no_git_write_verbs.py`）。

⛔ 所以「整体回档」没有对象——**不合并本身就是回档**。
真正的每日动作是：**逐支分支三选一（合 / 弃 / 重跑）**，
外加一个整批级的一票否决。
"""

from __future__ import annotations

import subprocess

from devloop import nightly, prune


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


def _branch(repo, name, verdict: str, files: int = 1):
    """⚠️ 提交信息**逐字**照 `commit_result` 的格式写——判据不能靠想象的格式。"""
    subprocess.run(["git", "checkout", "-q", "-b", name], cwd=repo, check=True,
                   capture_output=True)
    for i in range(files):
        (repo / f"{name.replace('/', '_')}_{i}.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    msg = (f"work({name}): 工人产出 · {verdict}\n\n"
           f"由 DevLoop 编排方代为提交，仅落在隔离分支 {name}，未触碰主线。")
    subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)


# ── ⛔ 闸的判定要读得出来 ─────────────────────────────────────────

def test_三种判定必须读得出来(tmp_path):
    repo = _repo(tmp_path)
    for n, v in (("devloop/a", "闸全绿"), ("devloop/b", "闸未过"),
                 ("devloop/c", "未跑闸")):
        _branch(repo, n, v)
    got = {b.name: b.gate for b in prune.scan(repo)}
    assert got == {"devloop/a": "闸全绿", "devloop/b": "闸未过",
                   "devloop/c": "未跑闸"}, got


def test_读不出判定时留空不许当成未跑闸(tmp_path):
    """⛔ 「读不出来」与「跑了闸但没验到东西」是两件事。
    ⚠️ 把前者当后者，会让一条**无从判断**的分支看起来像有结论。"""
    repo = _repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "devloop/x"], cwd=repo,
                   check=True, capture_output=True)
    (repo / "y.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "手写的提交，没按格式"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)
    assert prune.scan(repo)[0].gate == ""


def test_三种判定的说明必须两两不同(tmp_path):
    """⛔ 这就是缺陷本身：改前三支印的是同一句话。"""
    repo = _repo(tmp_path)
    for n, v in (("devloop/a", "闸全绿"), ("devloop/b", "闸未过"),
                 ("devloop/c", "未跑闸")):
        _branch(repo, n, v)
    reasons = [b.reason for b in prune.scan(repo)]
    assert len(set(reasons)) == 3, f"⛔ 还是分不开：{reasons}"


def test_闸没过的那支要明说合之前必须看(tmp_path):
    """⚠️ 「有产出」与「有**能用**的产出」是两件事。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/bad", "闸未过")
    assert "闸没过" in prune.scan(repo)[0].reason


# ── 清单一行要能自己说清楚 ────────────────────────────────────────

def test_每行带上判定和改了几个文件(tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", "闸全绿", files=3)
    _branch(repo, "devloop/b", "闸未过", files=1)
    lines = nightly.branch_lines(repo)
    a = next(l for l in lines if "devloop/a" in l)
    b = next(l for l in lines if "devloop/b" in l)
    assert "闸全绿" in a and "3" in a, a
    assert "闸未过" in b, b


def test_数不出文件数时印数不出而不是零(tmp_path):
    """⛔ 把「没数据」和「零改动」混成一件事，排查时找不到线索。"""
    repo = _repo(tmp_path)
    _branch(repo, "devloop/a", "闸全绿")
    lines = nightly.branch_lines(repo)
    assert "0 个文件" not in lines[0]
