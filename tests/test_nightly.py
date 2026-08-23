"""`nightly.branch_lines()` 要给出**能被下一单吃**的短清单。

判据：
- 只列 `safe_to_delete == False`（有未合并产出）的分支
- 每行 `devloop/xxx @ abc1234`
- 没有待处理分支时返回**空列表**，不是「没有」字样的字符串
"""

from __future__ import annotations

import subprocess

from devloop import nightly


def _repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()
    for a in (["init", "-q", "-b", "master", "."],
              ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    (p / "base.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=p, check=True,
                   capture_output=True)
    return p


def _make_work_branch(repo, name):
    """在当前 HEAD 上开 `name` 分支，加一条 `work(...)` 工人提交。

    ⚠️ 主题必须 `work(` 开头且正文含分支名——`prune.scan()::empty` 判据。
    """
    subprocess.run(["git", "checkout", "-q", "-b", name], cwd=repo, check=True,
                   capture_output=True)
    slug = name.replace("/", "_")
    (repo / f"{slug}.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", f"work({name}): 干活\n\n分支：{name}"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)


def test_只列未合并的分支_已合并的和空的都不出现(tmp_path):
    repo = _repo(tmp_path)
    # 有产出，没合并 —— 该出现
    _make_work_branch(repo, "devloop/pending")
    # 有产出，已合并 —— 不该出现
    _make_work_branch(repo, "devloop/done")
    subprocess.run(["git", "merge", "--no-ff", "-q", "-m", "merge", "devloop/done"],
                   cwd=repo, check=True, capture_output=True)
    # 空分支（没工人提交） —— 不该出现
    subprocess.run(["git", "branch", "devloop/empty"], cwd=repo, check=True,
                   capture_output=True)

    lines = nightly.branch_lines(repo)

    assert len(lines) == 1, f"只有 pending 该列：{lines}"
    assert lines[0].startswith("devloop/pending @ "), f"行格式错：{lines[0]}"
    # sha 短哈希：至少 7 位十六进制
    #  ⚠️ 2026-08-02 起每行后面还挂着「· 闸判定 · 改了几个文件」——
    #     ⭐ 那是为了让早上 30 秒分诊看得出「五道全绿」与「闸没过」的区别。
    #     ⛔ 这里只把 sha 取准，**断言本身一个字没放松**。
    sha = lines[0].split(" @ ", 1)[1].split(" · ", 1)[0]
    assert len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha), \
        f"短哈希不对：{sha!r}"
    #  ⚠️ 本文件的夹具写的是简化提交信息（没有 `· 闸全绿` 那一段），
    #     所以这里判定读不出来——⭐ 那是**正确**的输出：读不出就说读不出，
    #     ⛔ 不许猜成「未跑闸」。真格式的覆盖在 tests/test_triage.py。
    assert "判定读不出" in lines[0], lines[0]


def test_没有待处理分支时返回空列表(tmp_path):
    """⛔ 不许返回一个「没有」字样的字符串——调用方按行数判定，会出错。"""
    repo = _repo(tmp_path)

    result = nightly.branch_lines(repo)

    assert result == [], f"该返回空列表，得到：{result!r}"
    assert isinstance(result, list), f"必须是 list，得到 {type(result).__name__}"
