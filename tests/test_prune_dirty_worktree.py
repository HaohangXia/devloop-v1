"""⛔ prune 不许把「worktree 里还有未提交改动」的分支判成「可清理」。

## 这条是怎么来的

2026-08-04 首次跨项目真派单，工人跑了 50 分钟被自己的死线掐死，产出**留在
worktree 里没提交**。事后跑 `devloop prune --project C:/pg/eco-ob`，工具原文：

    可清理：
      devloop/f1-feeding-formulas-20260804-... @ 2667fbb —— 已合并进主线
    ...
      git worktree remove --force C:/pg/.devloop-worktrees/eco-ob-f1-... && git branch -D ...

⭐ 它把那 50 分钟判成「可以删」，还把 `--force` 删除命令替人拼好了。

## ⛔ 而它这么判的理由，正是「产出没保存」这件事本身

分支显示「已合并」，是因为**一个提交都没有**——尖端 == 基准 ⇒ 从 HEAD 可达
⇒ `git branch --merged` 收录它。

> **「产出没被提交」让工具认定「产出已在主线上」。**

⭐ 这是本项目那条铁律的教科书级反例：**判据落在了代用品上**。
`merged` 是「产出已别处留存」的代用品，而它在最要命的那一种情形下**正好反过来**。
能直接量的是 worktree 里的 `git status --porcelain`。

⚠️ 与模块头那句「判定「能不能删」的唯一依据是**产出有没有别处留存**」并不冲突
——未提交的改动**就是**一份没有别处留存的产出，只是它不在提交里。

## 三种状态必须分开（⛔ 不许合并成一个布尔）

| worktree | dirty | |
|---|---|---|
| 没有 | — | 不可能有未提交改动，按老规矩走 `merged` |
| 有 | 问出来是 0 | 确认干净，按老规矩走 `merged` |
| 有 | 问出来 >0 | ⛔ 一票否决 |
| 有 | **问不出来** | ⛔ 也一票否决——「不知道」不许当成「干净」 |
"""

from __future__ import annotations

import subprocess

import pytest

from devloop import prune


def _run(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.fixture()
def repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()
    _run(p, "init", "-q", "-b", "master", ".")
    _run(p, "config", "user.email", "t@t")
    _run(p, "config", "user.name", "t")
    (p / "base.txt").write_text("v1\n", encoding="utf-8")
    _run(p, "add", "-A")
    _run(p, "commit", "-qm", "init")
    return p


def _worktree_at_base(repo, tmp_path, name: str):
    """按 devloop 真实做法开一条隔离分支 + worktree，⭐ **不提交任何东西**。

    ⚠️ 于是尖端 == 基准，`git branch --merged` 会收录它——
       这正是 2026-08-04 现场的形状。
    """
    wt = tmp_path / name.replace("/", "_")
    _run(repo, "worktree", "add", "-q", "-b", name, str(wt))
    return wt


# ══════════════════════════════════════════════════════════════════════
#  ⭐ 主判据：脏 worktree 一票否决
# ══════════════════════════════════════════════════════════════════════

def test_worktree里有未提交改动时不许判可清理(repo, tmp_path):
    """2026-08-04 现场的**逐字复现**。⛔ 今天这条必红。"""
    wt = _worktree_at_base(repo, tmp_path, "devloop/f1-20260804")
    # 工人干了活但没提交——就是那 50 分钟
    (wt / "base.txt").write_text("工人改过的内容\n", encoding="utf-8")
    (wt / "新文件.gd").write_text("func _ready(): pass\n", encoding="utf-8")

    b = next(x for x in prune.scan(repo) if x.name == "devloop/f1-20260804")

    #  ⚠️ 先钉住前提：它**确实**被 git 当成已合并——否则这条测试测的是别的东西
    assert b.merged, "前提没成立：尖端应等于基准，git 应认为它已合并"
    assert b.safe_to_delete is False, (
        "⛔ worktree 里有未提交改动，绝不许判「可清理」"
        "——那正是 2026-08-04 差点被 --force 铲掉的那 50 分钟")


def test_原因里要报出未提交的条数(repo, tmp_path):
    """⭐ 光不删不够——人要知道**为什么**别删，否则他会以为工具在犯傻。"""
    wt = _worktree_at_base(repo, tmp_path, "devloop/f2-20260804")
    (wt / "a.txt").write_text("x\n", encoding="utf-8")
    (wt / "b.txt").write_text("y\n", encoding="utf-8")

    b = next(x for x in prune.scan(repo) if x.name == "devloop/f2-20260804")
    assert "2" in b.reason, f"原因里要有条数，实际：{b.reason}"
    assert "未提交" in b.reason


def test_报告不许把脏分支列进可清理也不许给出删除命令(repo, tmp_path, capsys):
    """⛔ 判据落在**人真正会去复制的那一行**上，不是内部布尔。

    ⚠️ 这条与上面那条不重复：`safe_to_delete` 对了而报告仍把删除命令印出来，
       人照样会把它粘进终端。**账要在人看得见的那一层配平。**
    """
    wt = _worktree_at_base(repo, tmp_path, "devloop/f3-20260804")
    (wt / "base.txt").write_text("工人的活\n", encoding="utf-8")

    print(prune.report(prune.scan(repo)))
    out = capsys.readouterr().out

    head, _, tail = out.partition("可清理：")
    assert "devloop/f3-20260804" not in tail.split("⛔ 本命令不删")[0], \
        "⛔ 脏分支出现在「可清理」名单里"
    for ln in out.splitlines():
        if "worktree remove" in ln or "branch -D" in ln:
            assert "f3-20260804" not in ln, \
                f"⛔ 报告给出了会铲掉未提交产出的命令：{ln}"


def test_头行的待处理数不许漏掉未提交的那部分(repo, tmp_path):
    """⭐ `_files_changed` 自己写着用途是「让人估复核成本，**少报等于让人低估**」。

    ⛔ 而它只数**已提交**的差异。工人被掐死那种情形提交数为 0，于是
       「待处理产出 N 个文件」把那 50 分钟算成了 0。
    ⚠️ 两种东西不许合并成一个数（提交里的 vs 还没进提交的），
       ⭐ 但也不许把后者从账上抹掉——**各档相加要等于总数**。
    """
    wt = _worktree_at_base(repo, tmp_path, "devloop/f4-20260804")
    for n in ("a.gd", "b.gd", "c.gd"):
        (wt / n).write_text("x\n", encoding="utf-8")

    out, _ = prune.report(repo)
    head = out.splitlines()[1] if len(out.splitlines()) > 1 else ""
    assert "3" in head and "未提交" in head, (
        f"⛔ 头行没报出那 3 个未提交改动，人会低估要复核的量。实际：{head!r}")


# ══════════════════════════════════════════════════════════════════════
#  ⛔ 防回归：别把「保守」写成「什么都不敢删」——那样这个命令就废了
# ══════════════════════════════════════════════════════════════════════

def test_干净的worktree照旧可清理(repo, tmp_path):
    """⚠️ 已合并 + worktree 干净 → 仍然可清理。⛔ 修完不能把这条也拦下。"""
    _worktree_at_base(repo, tmp_path, "devloop/clean-20260804")
    b = next(x for x in prune.scan(repo) if x.name == "devloop/clean-20260804")
    assert b.merged
    assert b.safe_to_delete is True, "⛔ 干净的已合并分支被误拦了，命令会变得没用"


def test_没有worktree的已合并分支照旧可清理(repo, tmp_path):
    """⚠️ 绝大多数历史分支的 worktree 早就删了——它们不该受这条新规矩影响。"""
    wt = _worktree_at_base(repo, tmp_path, "devloop/gone-20260726")
    _run(repo, "worktree", "remove", "--force", str(wt))
    b = next(x for x in prune.scan(repo) if x.name == "devloop/gone-20260726")
    assert b.worktree == "", "前提没成立：worktree 应已不存在"
    assert b.safe_to_delete is True


def test_未提交的改动只在暂存区也算(repo, tmp_path):
    """⚠️ `git add` 过但没 commit 同样是「没别处留存」。

    ⛔ 判据若只看未暂存的改动，工人只要 `git add` 一下就绕过去了。
    """
    wt = _worktree_at_base(repo, tmp_path, "devloop/staged-20260804")
    (wt / "只暂存了.txt").write_text("x\n", encoding="utf-8")
    _run(wt, "add", "-A")
    b = next(x for x in prune.scan(repo) if x.name == "devloop/staged-20260804")
    assert b.safe_to_delete is False


def test_worktree路径没了但git还记着时不许说安心(repo, tmp_path):
    """⛔ 「问不出来」不许当成「干净」。

    ⚠️ 目录被人手工删掉、git 还记着这条 worktree——`git status` 跑不起来。
    ⭐ 那时唯一诚实的答案是「不知道」，而「不知道」在这个判定里等于「别删」。
    """
    import shutil
    wt = _worktree_at_base(repo, tmp_path, "devloop/vanished-20260804")
    shutil.rmtree(wt)                       # ⛔ 只删目录，不跑 worktree remove

    b = next(x for x in prune.scan(repo) if x.name == "devloop/vanished-20260804")
    if not b.worktree:
        pytest.skip("这个 git 版本在目录消失后就不再列出该 worktree，本条不适用")
    assert b.safe_to_delete is False, "⛔ 问不出状态时说了「安心可删」"
