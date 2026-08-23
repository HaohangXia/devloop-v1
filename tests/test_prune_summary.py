"""prune 报告要在头行下面加一条「合计待处理产出」。

**为什么要这行**：无人值守跑一夜后会攒几十个 `devloop/*` 分支，人来接管的
第一个问题是「一共有多少产出待处理」。原报告只说「几条建议保留」，不说
里面装了多少东西——每条都得点开数一遍。

⚠️ 只统计「建议保留」的分支；「可清理」那些要么已合并、要么没产出，
   算进去会虚报。
⚠️ 没有待处理分支时这行不打——避免刷噪音。
⚠️ 数不出来的分支跳过并注明，不许当成 0（会把「没数据」和「零改动」混成一件事）。
"""

from __future__ import annotations

import subprocess

from devloop import prune


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


def _make_work_branch(repo, name, num_files):
    """在当前 HEAD 上开 `name` 分支，加一条 `work(...)` 工人提交，改 num_files 个文件。

    ⚠️ 主题必须 `work(` 开头且正文含分支名——`scan()::empty` 就靠这两点判据。
    """
    subprocess.run(["git", "checkout", "-q", "-b", name], cwd=repo, check=True,
                   capture_output=True)
    slug = name.replace("/", "_")
    for i in range(num_files):
        (repo / f"{slug}_{i}.txt").write_text(f"x{i}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", f"work({name}): 干活\n\n分支：{name}"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)


def test_有待处理分支时报告出现合计行(tmp_path):
    """两个待处理分支合计 5 个文件——报告必须点名总数。"""
    repo = _repo(tmp_path)
    _make_work_branch(repo, "devloop/a", 2)
    _make_work_branch(repo, "devloop/b", 3)

    txt, safe = prune.report(repo)

    assert safe == 0, "两条都有产出，都不该判成可清理"
    lines = txt.splitlines()
    # 头行、合计行、然后空行 —— 合计必须紧跟在头行后面
    assert lines[0].startswith("2 个隔离分支"), f"头行错：{lines[0]}"
    assert "待处理产出：2 个分支合计改动 5 个文件" in lines[1], \
        f"合计行错或不在第二行：\n{txt}"


def test_没有待处理分支时不打合计行(tmp_path):
    """完全没有 `devloop/*` 分支时报告说「干净」——不许硬塞一条 0 合计。"""
    repo = _repo(tmp_path)

    txt, safe = prune.report(repo)

    assert safe == 0
    assert "待处理产出" not in txt, \
        f"没分支时不许打合计行（刷噪音）：\n{txt}"


def test_只有可清理分支时也不打合计行(tmp_path):
    """所有分支都可清理（已合并）——「待处理产出」= 0，那行不打。

    ⚠️ 把已合并的算进合计会虚报「还欠人几条产出」，正是这个数要防的事。
    """
    repo = _repo(tmp_path)
    _make_work_branch(repo, "devloop/done", 2)
    # 把它合进 master —— 合并后它就是「可清理」
    subprocess.run(["git", "merge", "--no-ff", "-q", "-m", "merge", "devloop/done"],
                   cwd=repo, check=True, capture_output=True)

    txt, safe = prune.report(repo)

    assert safe == 1, f"合并后应可清理：\n{txt}"
    assert "待处理产出" not in txt, \
        f"全是可清理分支时不许打合计行：\n{txt}"


# ── ⛔ merged 与 empty 同时成立时，不许只说 merged ────────────────
#
# 实测（2026-08-02，本仓 18 个隔离分支）：8 个被归到「可清理」，其中
#   · 4 个 merged=True empty=False —— 尖端是 `work(...)` 工人提交，措辞正确
#   · 4 个 merged=True empty=True  —— 尖端是**主线提交**，工人一个提交都没有
# 而 `reason` 先判 merged，于是后 4 个被印成「已合并进主线，**产出已在主线上**」。
#
# ⚠️ 模块自己在 `scan()` 的注释里就写着这个隐患：
#   「两种情况的处置恰好都是「可删」，所以不会丢东西，**但会把原因说反，
#     而原因正是人决定删不删的依据**。」
#
# ⛔ **陷阱**：那 4 个里有一个（`L4-20260726-172258`）的 `empty` 是**误报**
#    ——它尖端是 `feat(L4): ...`，是真产出，只是不叫 `work(`。
#    所以合并措辞时**不许**断言「这一单没产出」，⭐ 那会造出一句新的假话。
#
# ⚠️ 曾考虑用「尖端提交时间 vs 分支名里的时间戳」来判别（实测在 4 例上全对），
#    ⛔ 但没采用：`prune.py` 自己记着两次「聪明判据」翻车（按作者邮箱、
#    按「分支上有没有主线没有的提交」）。第三个带时钟假设的判据是同一个形状。

def _branch(merged: bool, empty: bool):
    from devloop.prune import Branch
    return Branch("devloop/x-1", "abc1234", merged, empty, "")


def test_两者同时成立时不许断言产出已在主线():
    """⛔ 那句话对这 4 个分支是假的——工人一个提交都没有。"""
    r = _branch(merged=True, empty=True).reason
    assert "产出已在主线上" not in r, f"⛔ 说反了：{r}"


def test_两者同时成立时两个事实都要说出来():
    r = _branch(merged=True, empty=True).reason
    assert "已合并" in r, f"⛔ 丢了「已合并」这个事实：{r}"
    assert "work(" in r or "工人提交" in r, f"⛔ 没说清尖端不是工人提交：{r}"


def test_两者同时成立时不许断言这一单没产出():
    """⛔ 陷阱在这里：`empty` 判的是「尖端是不是 `work(...)`」，
    而 `L4` 的尖端是 `feat(L4): ...`——**真产出，误报成 empty**。
    ⚠️ 工具分不出「没产出」与「产出走了别的路提交」，那就**别装作分得出**。"""
    r = _branch(merged=True, empty=True).reason
    #  ⚠️ 判据不能是「文案里不许出现『没产出』」——⛔ 那是**子串判据**，
    #     会把并列两种可能的「要么…没产出，要么…」也判红（第一版就这么错的）。
    #     ⭐ 判的是「有没有把它说成唯一结论」：提到了就必须同时给出另一种可能。
    if "没产出" in r:
        assert "要么" in r or "或" in r, \
            f"⛔ 把一件工具其实分不出的事说成了唯一结论：{r}"
    assert "，产出已在主线上" not in r


def test_只merged的措辞不许被改坏():
    """⚠️ 4 个 `work(` 尖端的分支，那句话本来就是对的。"""
    r = _branch(merged=True, empty=False).reason
    assert "已合并进主线" in r and "产出已在主线上" in r


def test_只empty的措辞不许被改坏():
    r = _branch(merged=False, empty=True).reason
    assert "没有工人提交" in r or "没产出" in r


def test_两者都不成立仍然是别删():
    r = _branch(merged=False, empty=False).reason
    assert "未合并" in r and "删了就没了" in r


#  ⛔ 这里原来有一条 `test_可删判定一个字没变`，断言 `safe_to_delete == (m or e)`。
#  ⚠️ 它写于同一天稍早，当时的理由是「本次只改措辞，谁能删不许跟着动」——
#     那个理由在当时是对的。⭐ 后来一轮独立复核指出 `empty` 参与删除判定
#     **本身就是一条数据丢失通道**（见下方 M-1b），于是它被下面三条取代。
#  ⛔ 记在这里是因为：删掉一条断言必须留下为什么，否则下一个人只会看到
#     「有人把守卫拆了」。


# ── ⛔ `empty` 不许参与「能不能删」的判定（M-1b）──────────────────
#
# 模块头写得很死：「⚠️ 判定「能不能删」的唯一依据是**产出有没有别处留存**……
# 一个三天前的分支若从未被合并、产出也没归档，删了就是第二次 G-26。」
#
# ⭐ 而 `empty` 是**按提交主题猜的**（`head_msg.startswith("work(")`），
#    不是「别处留存」的证据。它对 `safe_to_delete` 的贡献分两格：
#
# | merged | empty | 说明 |
# |---|---|---|
# | True | * | 已合并——产出确实在主线上，`merged` 一条就够，⛔ `empty` 不贡献任何东西 |
# | False | True | ⛔ **唯一由 `empty` 独占的格子，而它恰好是危险格** |
#
# 危险格的含义是：「尖端从 HEAD 不可达」+「主题字符串启发式说没产出」
# → `report()` 印「可清理」并给出 `git branch -D` → **删掉唯一一份未合并产出**。
#
# ⭐ 这不是假想：`L4-20260726-172258` 就是这个误判的**活样本**——
#    尖端 `feat(L4): fingerprint 缺文件时抛 ConfigError`，改了 devloop/gates.py
#    一个文件，是**真产出**，却因为不叫 `work(` 被判 empty=True。
#    ⚠️ 它没出事，只因为它**碰巧已合并**。

def test_没合并的分支一律不算可安心删():
    """⛔ 哪怕启发式说「没产出」也不行——启发式猜错过（L4）。"""
    assert not _branch(merged=False, empty=True).safe_to_delete, (
        "⛔ 按提交主题猜出来的『没产出』被当成了删除许可"
        "——L4 证明这个猜测会错，而错的代价是丢掉唯一一份产出")


def test_已合并的照旧可删():
    """⚠️ 反向钉住：别把这条修成「什么都不敢删」，那样这个命令就没用了。"""
    assert _branch(merged=True, empty=False).safe_to_delete
    assert _branch(merged=True, empty=True).safe_to_delete


def test_可删判据只认已合并这一件事():
    """⭐ 判据落在「`empty` 完全不参与」上，不是落在几个具体组合上。"""
    from devloop.prune import Branch
    for e in (True, False):
        for m in (True, False):
            assert Branch("b", "s", m, e, "").safe_to_delete is m, \
                f"⛔ merged={m} empty={e} 时判定受了 empty 的影响"
