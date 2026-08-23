"""宪法执行层的红测。

⚠️ 本文件里最重要的不是「命中能不能抓到」，而是三条**反测**：

1. **守卫的目标不存在**（第一种假绿）—— 清单写错路径，必须当场炸，
   ⛔ 不许安静地保护一个空集合然后每次报「无命中」。
2. **只覆盖了一部分输入**（G-42 那种，第六种假绿）—— 判不了的条款必须
   显式登记，且**每次都印出来**。「零命中」不带尾巴就是假绿。
3. **宪法自己被改**（自指类）—— 宪法在自己的清单里，锚在项目外面，
   ⛔ 且绝不许「以现值为新基准」自愈。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devloop import constitution as C
from devloop.config import ConfigError, ProjectPaths

MIN = """\
schema = 1

[[protected_file]]
clause = "A-1"
title  = "不得修改闸"
path   = ".devloop/gates.sh"

[[unjudged]]
clause = "B-4"

[tree]
coverage = "none"
why = "本夹具只演示文件级保护"
"""
# ⚠️ 上面 `[tree] coverage = "none"` 是 2026-07-28 补的：
#    一条 [[protected_tree]] 都没有 = 树内判据覆盖 **0 条路径**，
#    而它原本会安静地每单报「无宪法命中」——与 G-53（闸一条没验却报全过）
#    完全同形。现在必须**显式声明**，且声明会被每次印出来。


def _proj(tmp_path, toml_text=MIN, *, gates=True, git_init=True):
    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    if gates:
        (p / ".devloop" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                                 encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(toml_text, encoding="utf-8")
    if git_init:
        for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    return ProjectPaths(p)


# ══ 反测 1 · 守卫的目标不存在 ══════════════════════════════════

def test_受保护文件不存在必须当场炸(tmp_path):
    """⛔ 清单写错路径**比没有清单更坏**——它会每次都报「无命中」，
    看起来一切正常，而实际上守卫保护的是一个空集合。"""
    paths = _proj(tmp_path, MIN.replace(".devloop/gates.sh", ".devloop/根本没有这个文件"))
    with pytest.raises(ConfigError) as e:
        C.load(paths)
    assert "守卫的目标不存在" in str(e.value)
    assert "根本没有这个文件" in str(e.value), "必须点名是哪条写错了"


def test_标了optional的可以不存在(tmp_path):
    """⚠️ 防回归：确实有些项目没有 config.toml（devloop 自己就没有）。"""
    paths = _proj(tmp_path, MIN + """
[[protected_file]]
clause = "A-4"
path   = ".devloop/config.toml"
optional = true
""")
    con = C.load(paths)
    assert len(con.files) == 2


# ══ 反测 2 · 判不了的条款必须显式登记且每次都印 ══════════════════

def test_没登记任何判不了的条款要拒绝加载(tmp_path):
    """⛔ 宪法里必然有判不了的条款（对外发送、设计决策变更、例外层自认拿不准…）。
    一条都不登记，几乎一定是漏写——而后果是 `summary()` 会印出一句
    **没有尾巴的「无宪法命中」**，那句话本身就是假绿。"""
    # ⚠️ 只去掉 [[unjudged]]，**保留** [tree] 声明——
    #    否则会先撞上「一条受保护目录都没有」那条，测不到本条。
    no_unjudged = MIN.replace('[[unjudged]]\nclause = "B-4"\n', "")
    paths = _proj(tmp_path, no_unjudged)
    with pytest.raises(ConfigError) as e:
        C.load(paths)
    assert "unjudged" in str(e.value)


def test_零命中也必须带着未覆盖条款的尾巴(tmp_path):
    """⭐ 本文件最要紧的一条。「零命中」= 「查过了没事」是**误读**——
    真相是「查过的那部分没事，还有 N 条根本没查」。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    r = C.ConstitutionResult(0, [], con.unjudged)
    s = r.summary()
    assert "无宪法命中" in s
    assert "未覆盖条款" in s and "B-4" in s, "⛔ 未覆盖的条款必须每次都点名"


def test_命中时同样要带尾巴(tmp_path):
    r = C.ConstitutionResult(1, [C.Hit("A-1", "不得修改闸", "gates.sh 变了")], ["B-4"])
    s = r.summary()
    assert "宪法命中" in s and "A-1" in s
    assert "未覆盖条款" in s, "有命中也不代表覆盖全了"


def test_只记账的条款要与停机的分开报():
    """B-1（删除）判得了，但工人在一次性 worktree 里删东西只损失它自己那一单，
    闸会裁决。**每次删除都上报会把宪法上报变成噪音，而噪音会训练人去忽略上报**
    ——那比不上报更糟。所以它是 note，不是 halt。"""
    r = C.ConstitutionResult(1, [
        C.Hit("A-1", "改闸", "x", action="halt"),
        C.Hit("B-1", "删除", "y", action="note")], ["B-4"])
    assert len(r.halts) == 1
    assert "仅记账" in r.summary() and "B-1" in r.summary()


# ══ 反测 3 · 宪法自己被改（自指类）════════════════════════════

def test_宪法自身被改必须被发现(tmp_path):
    """⭐ 自指类的闭合点：宪法在自己的清单里，指纹存在**项目外**的锚里。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    assert C.verify_anchor(paths, con).clean

    con.source.write_text(MIN + "\n# 工人偷偷加的一行\n", encoding="utf-8")
    r = C.verify_anchor(paths, C.load(paths))
    assert not r.clean
    assert any(h.clause == "A-1" for h in r.hits)


def test_受保护文件被改必须被发现(tmp_path):
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    paths.gates.write_text("#!/usr/bin/env bash\necho 我改了闸\nexit 0\n", encoding="utf-8")
    r = C.verify_anchor(paths, con)
    assert not r.clean and "gates.sh" in r.summary()


def test_受保护文件被删也算(tmp_path):
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    paths.gates.unlink()
    r = C.verify_anchor(paths, con)
    assert not r.clean and "被删除" in r.summary()


def test_锚不符归为判定器故障而不是活没干好(tmp_path):
    """⚠️ 锚对不上不是「这单活干得不好」，是**判定基准本身不可信了**。
    退出码 2，在人查明之前不该继续派单——与闸的 gate_broken 同源。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    paths.gates.write_text("x", encoding="utf-8")
    assert C.verify_anchor(paths, con).broken


def test_没有锚文件时要指出该跑哪条命令(tmp_path):
    """⛔ 绝不自动以现值为新基准——锚一旦能自愈，它就不是锚。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    r = C.verify_anchor(paths, con)
    assert r.broken and "anchor" in r.stderr


def test_锚放在项目外面(tmp_path):
    """工人的可写面是项目与 worktree。锚放项目外**提高篡改成本**——
    ⚠️ 但它不是安全边界（工人有 Bash，理论上够得到），这一点不许含糊。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    a = C.write_anchor(paths, con)
    assert C.ANCHOR_DIR in str(a)
    assert paths.project not in a.parents, "锚不能落在被保护的项目里"


# ══ T0 硬拒 ═══════════════════════════════════════════════════

def test_无人值守下的对外通道必须硬拒(tmp_path):
    """`full` 比 `implement` 多的就是 WebFetch。而「对外发送」事后**零痕迹**：
    diff、台账、回执里都不留证据。判不了，就只能事前不给能力。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    with pytest.raises(ConfigError) as e:
        C.refuse_preflight(con, tools="full", unattended=True)
    assert "full" in str(e.value) and "detach" in str(e.value)


@pytest.mark.parametrize("tools,detach", [
    ("full", False), ("implement", True), ("readonly", True), ("implement", False)])
def test_其余组合照常放行(tmp_path, tools, detach):
    """⚠️ 防回归：别把「更严」修成「什么都不让干」。"""
    paths = _proj(tmp_path)
    C.refuse_preflight(C.load(paths), tools=tools, unattended=detach)


# ══ base 锚定的树内判据 ════════════════════════════════════════

def _commit(repo, name, text):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True,
                   capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


TREE = MIN + """
[[protected_tree]]
clause = "C-2"
title  = "不得改基线参考数据"
glob   = "ref/*.json"
"""


def test_base必须显式给出不许用HEAD(tmp_path):
    """⛔ 工人自提交正是本判据要抓的动作——用 HEAD 当锚，
    等于攻击成功时锚自己也跟着移动。"""
    paths = _proj(tmp_path, TREE)
    _commit(paths.project, "ref/a.json", '{"v":1}\n')   # 受保护路径要真实存在
    con = C.load(paths)
    for base in ("", "HEAD"):
        r = C.check_tree(con, worktree=paths.project, base=base)
        assert r.broken, f"base={base!r} 必须拒绝判定，而不是静默跳过一半判据"


def test_受保护的基线被改要命中(tmp_path):
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    (paths.project / "ref" / "a.json").write_text('{"v":999}\n', encoding="utf-8")
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert not r.clean and "a.json" in r.summary()


def test_工人自提交挡不住base锚定判据(tmp_path):
    """⭐ 这是 base 锚定的全部意义：工人把改动**提交掉**，工作区就干净了，
    任何基于 `git status` 的判据都会说「没事」——而 base 锚定照样抓得到。"""
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    _commit(paths.project, "ref/a.json", '{"v":999}\n')   # 工人自己提交掉
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=paths.project,
                           capture_output=True, text=True).stdout.strip()
    assert dirty == "", "前提：工作区此刻是干净的（所以 status 判据会漏）"
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert not r.clean, "⛔ base 锚定必须抓得到已被提交掉的改动"


def test_受保护路径下新增未跟踪文件也算(tmp_path):
    """⛔ 不带 `--exclude-standard`：带了就受 .gitignore 与 info/exclude 支配，
    而那两处工人都写得到。"""
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    (paths.project / "ref" / "偷偷加的.json").write_text("{}", encoding="utf-8")
    (paths.project / ".gitignore").write_text("ref/偷偷加的.json\n", encoding="utf-8")
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert not r.clean, "被 gitignore 挡住的新增文件同样要抓到"


def test_没碰受保护路径时干净(tmp_path):
    """⚠️ 防回归。"""
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    (paths.project / "别的文件.txt").write_text("随便改\n", encoding="utf-8")
    assert C.check_tree(con, worktree=paths.project, base=base).clean


# ══ 事后快照比对 ═══════════════════════════════════════════════

def test_既有引用被改写要命中(tmp_path):
    """B-2：rebase / reset --hard / 删分支 / 改历史。"""
    paths = _proj(tmp_path)
    _commit(paths.project, "a.txt", "1\n")
    con = C.load(paths)
    before = C.snapshot(paths, con)
    _commit(paths.project, "a.txt", "2\n")
    r = C.check_refs(con, before, C.snapshot(paths, con))
    assert not r.clean and "B-2" in r.summary()


def test_新建分支不算命中(tmp_path):
    """⚠️ 防回归：`commit_result` 自己造隔离分支，那是正常动作。"""
    paths = _proj(tmp_path)
    _commit(paths.project, "a.txt", "1\n")
    con = C.load(paths)
    before = C.snapshot(paths, con)
    subprocess.run(["git", "branch", "devloop/x-1"], cwd=paths.project,
                   check=True, capture_output=True)
    assert C.check_refs(con, before, C.snapshot(paths, con)).clean


def test_worktree之外的写入靠事后指纹发现(tmp_path):
    """⚠️ 这是**发现**不是**阻止**——目录联接、git hook、活过 subprocess 的
    孙进程都能写到 worktree 外面。发现得晚，总比不发现好。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)
    paths.gates.write_text("#!/usr/bin/env bash\nexit 0\n# 被外部改了\n", encoding="utf-8")
    r = C.check_files(con, before, C.snapshot(paths, con))
    assert not r.clean and "gates.sh" in r.summary()


def test_中文路径不许被git的转义吃掉(tmp_path):
    """⛔ git **默认**把非 ASCII 路径转义成 `"a/\345\201\267.json"`（带引号的八进制），
    于是任何按字面比对路径的守卫都会**静默漏掉每一个中文名文件**。

    ⚠️ 这不是边角情况：eco-ob 的 `01_设计文档/` 正是中文名，正是宪法 C-1
    要保护的那批——**守卫会恰好在它最该保护的路径上变成空的**。
    """
    cn = MIN + """
[[protected_tree]]
clause = "C-1"
title  = "不得改设计文档"
glob   = "设计文档/*.md"
"""
    paths = _proj(tmp_path, cn)
    base = _commit(paths.project, "设计文档/主文档.md", "v1\n")
    con = C.load(paths)
    (paths.project / "设计文档" / "主文档.md").write_text("被改了\n", encoding="utf-8")
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert not r.clean, "⛔ 中文路径必须照样抓得到"
    assert "主文档" in r.summary()


def test_星号不许跨目录分隔符(tmp_path):
    """⚠️ 守卫**过宽和过窄一样坏**：过窄漏抓；过宽天天误报，
    而天天误报会训练人去忽略上报，那比不上报更糟。
    `fnmatch` 的 `*` 跨 `/`，所以这里自己写匹配。"""
    assert C._match("ref/a.json", "ref/*.json")
    assert not C._match("ref/sub/a.json", "ref/*.json"), "* 不该跨目录"
    assert C._match("ref/sub/a.json", "ref/**"), "** 才跨目录"
    assert C._match("ref/a.json", "ref/")


# ══ base 解析：⛔ 只许有一处实现 ═══════════════════════════════

def test_基准解析不许返回字面量HEAD(tmp_path):
    """⛔ 今天亲手引入的回归（审查第三轮抓到）：

    `snapshot_base` 在工作区干净时返回**字面量 `"HEAD"`**，而宪法的树内判据
    拒绝 HEAD（工人自提交正是它要抓的动作，用 HEAD 当锚等于锚跟着动）。
    于是 `cmd_dispatch` 在干净工作区 + 已启用宪法的项目上，**每一单都被判成
    宪法故障**——干得好的活被判失败，整批退出码 1。

    ⚠️ 根因是**同一个解析逻辑写了两处**：autopilot 那处修了，dispatch 那处没修。
    所以修法是收敛成一个函数，并由本测试钉死它的后置条件。
    """
    from devloop import worktree as W
    repo = tmp_path / "r"
    repo.mkdir()
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    (repo / "f.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True,
                   capture_output=True)

    assert W.snapshot_base(repo) == "HEAD", "前提：干净工作区下它确实返回字面量"

    base = W.resolve_base(repo)
    assert base != "HEAD", "⛔ 解析结果绝不能是字面量 HEAD"
    assert len(base) == 40, f"必须是完整 sha，实得 {base!r}"


def test_基准解析要包含未提交的在途改动(tmp_path):
    """⚠️ 不能简单用 `rev-parse HEAD`：工作区可能有在途改动，
    那些不是工人干的，拿 HEAD 当锚会把它们算到工人头上（G-12 同源）。"""
    from devloop import worktree as W
    repo = tmp_path / "r2"
    repo.mkdir()
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    (repo / "f.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True,
                   capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    (repo / "f.txt").write_text("在途改动\n", encoding="utf-8")

    base = W.resolve_base(repo)
    assert base != head, "有在途改动时基准必须是快照，不是 HEAD"
    assert len(base) == 40


def test_受保护路径下的新增文件_git_add之后仍必须命中(tmp_path):
    """⛔ 第三轮审查抓到的 CRITICAL，我亲手复现过。

    新增判据原本只看 `git ls-files -o`（**未跟踪**）。工人对新增文件跑一句
    `git add`——**连提交都不用**——那个文件就同时：

        · 不在 `ls-tree <base>` 里（base 那会儿它还不存在）
        · 也不在 `ls-files -o` 里（已经进索引了，不再算「未跟踪」）

    三路判据全瞎，宪法报一句正面的「无宪法命中」。

    ⚠️ **`git add` 不是攻击**，是工人干完活再自然不过的动作。
    """
    from devloop import constitution as C
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    (paths.project / "ref" / "偷偷加的.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "ref/偷偷加的.json"], cwd=paths.project,
                   check=True, capture_output=True)
    assert not subprocess.run(["git", "ls-files", "-o", "--no-empty-directory"],
                              cwd=paths.project, capture_output=True,
                              text=True).stdout.strip(), "前提：add 之后它不再是未跟踪"
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert not r.clean, "⛔ git add 过的新增文件同样要抓到"
    assert "偷偷加的" in r.summary()


def test_受保护路径下的新增文件_工人自提交后仍必须命中(tmp_path):
    """工人把新增文件**提交掉**——工作区干净、索引干净，
    只有 base 锚定能看见它。"""
    from devloop import constitution as C
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    _commit(paths.project, "ref/工人加的.json", "{}")
    assert not subprocess.run(["git", "status", "--porcelain"], cwd=paths.project,
                              capture_output=True, text=True).stdout.strip(), \
        "前提：此刻工作区是干净的"
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert not r.clean, "⛔ 已被提交掉的新增文件同样要抓到"


def test_命中文案不许说死是未跟踪文件(tmp_path):
    """⚠️ 判据放宽之后，add/commit 过的文件也走这条分支——
    还印「是新增的未跟踪文件」就是一句字面为假的话。"""
    from devloop import constitution as C
    paths = _proj(tmp_path, TREE)
    base = _commit(paths.project, "ref/a.json", '{"v":1}\n')
    con = C.load(paths)
    _commit(paths.project, "ref/b.json", "{}")
    r = C.check_tree(con, worktree=paths.project, base=base)
    assert "未跟踪" not in r.summary(), f"文案说了假话：{r.summary()}"


# ══ 第二批 · 守卫在跑，但视野是空的 ═══════════════════════════
# 四条的共同点：**「我检查了，没问题」和「我根本没检查」，输出上一模一样。**

def test_受保护目录的模式匹配不到任何东西时要拒绝加载(tmp_path):
    """⛔ `load()` 原本只校验 `protected_file`，`[[protected_tree]]` 一条不查。
    glob 写错（繁简、多写少写一层、大小写）语法全合法 →
    加载通过 → **此后永远报「无命中」**。

    ⚠️ 但「今天匹配不到」**未必**是错的：树内判据也负责抓「受保护目录下
    冒出新文件」，而那个目录今天可以是空的、甚至不存在。
    所以判据不是「必须匹配到」，而是**必须显式认领**——与文件那侧的
    `optional = true` 同一条路子。
    """
    from devloop import constitution as C
    bad = MIN + """
[[protected_tree]]
clause = "C-1"
glob   = "根本没有这个目录/**"
"""
    paths = _proj(tmp_path, bad)
    with pytest.raises(ConfigError) as e:
        C.load(paths)
    assert "根本没有这个目录" in str(e.value)
    assert "allow_empty" in str(e.value), "必须给出显式认领的写法"


def test_显式认领之后允许匹配不到(tmp_path):
    """⚠️ 防回归：「这个目录现在是空的，我就是要防它冒出东西」是合法意图。"""
    from devloop import constitution as C
    ok = MIN + """
[[protected_tree]]
clause = "C-1"
glob   = "将来才有的目录/**"
allow_empty = true
"""
    con = C.load(_proj(tmp_path, ok))
    assert len(con.trees) == 1


def test_一条受保护目录都没有时不许静默报绿(tmp_path):
    """⛔ 与 G-53 完全同形：覆盖 0 条路径却每单报「无宪法命中」。

    ⚠️ 而出厂模板把所有 `[[protected_tree]]` 都注释掉了——
    也就是说 `constitution init` 之后的**默认状态**就是这个样子。
    """
    from devloop import constitution as C
    no_decl = MIN.split("[tree]")[0]     # ⚠️ 去掉那句显式声明
    paths = _proj(tmp_path, no_decl)
    with pytest.raises(ConfigError) as e:
        C.load(paths)
    assert "protected_tree" in str(e.value)
    assert 'coverage = "none"' in str(e.value), "必须给出显式声明的写法"


def test_声明了没有树内判据就放行但每次都要报出来(tmp_path):
    """⚠️ 「这个项目确实没有需要树内保护的路径」是合法的——
    但它必须变成一句**每次都印出来**的话，而不是一片安静的绿。"""
    from devloop import constitution as C
    con = C.load(_proj(tmp_path))        # MIN 本身就带 [tree] coverage = "none"
    assert con.trees == []
    r = C.check_tree(con, worktree=Path("."), base="deadbeef")
    assert "树内判据覆盖 0 条路径" in r.summary(), "必须每次都说出来"


def test_受保护文件指向目录必须拒绝(tmp_path):
    """⛔ 算指纹的函数对目录返回空字符串，锚里记空，复验时空等于空——
    于是**把整个目录删掉都报「无宪法命中」**。而 `.exists()` 对目录为真，
    原来的存在性校验放行了它。"""
    from devloop import constitution as C
    (tmp_path / "proj" ).mkdir(parents=True, exist_ok=True)
    bad = MIN.replace('path   = ".devloop/gates.sh"', 'path   = ".devloop"')
    paths = _proj(tmp_path, bad)
    with pytest.raises(ConfigError) as e:
        C.load(paths)
    assert "目录" in str(e.value)


def test_受保护文件里写glob要拒绝(tmp_path):
    """⛔ `[[protected_file]]` 写 `glob` 而不写 `path` 时，全链路
    （write_anchor / snapshot / verify_anchor / check_files 都带 `if p.path` 过滤）
    **静默忽略该条**，锚里是空的。"""
    from devloop import constitution as C
    bad = MIN.replace('path   = ".devloop/gates.sh"', 'glob   = ".devloop/*.sh"')
    with pytest.raises(ConfigError) as e:
        C.load(_proj(tmp_path, bad))
    assert "glob" in str(e.value) and "path" in str(e.value)


@pytest.mark.parametrize("pat,rel,want", [
    ("ref/", "ref/a.json", True),
    ("ref*/", "ref/a.json", True),        # ← 原来永远匹配不到
    ("ref*/", "refs/a.json", True),
    ("r?f/", "ref/a.json", True),         # ← 同上
    ("ref/", "other/a.json", False),
    ("ref*/", "other/a.json", False),
])
def test_目录形式的模式也要认通配符(pat, rel, want):
    """⛔ 目录分支原本走的是**纯字面 startswith**，glob 元字符完全失效：
    `ref*/` 这类模式永远匹配不到任何东西——又一个「守卫的目标不存在」。"""
    from devloop.constitution import _match
    assert _match(rel, pat) is want, f"{pat!r} vs {rel!r}"


def test_冻结基准项目必须硬拒一切写操作(tmp_path):
    """⛔ 有些项目**天生不该被写**——评测基准就是典型：
    往里写一个字，`check_baseline` 就会因为工作区变脏而拒绝跑分，
    整套评测的分母就没了。

    ⚠️ 正确的做法不是「给它补个闸」（闸是用来裁决写操作的，补了等于邀请人写），
    而是**在起任何进程之前就拒绝**。
    """
    from devloop import constitution as C
    frozen = MIN + '''
[refuse]
no_writes = true
why = "本项目是评测集的冻结基准，写一个字就毁掉分母"
'''
    con = C.load(_proj(tmp_path, frozen))
    for tools in ("implement", "full"):
        with pytest.raises(ConfigError) as e:
            C.refuse_preflight(con, tools=tools, unattended=False)
        assert "冻结" in str(e.value) or "毁掉分母" in str(e.value), \
            "必须说清为什么，不能只说「不许」"
    # 只读照常
    C.refuse_preflight(con, tools="readonly", unattended=False)


def test_没声明no_writes的项目照常能写(tmp_path):
    """⚠️ 防回归：这是可选声明，不是新的默认。"""
    from devloop import constitution as C
    C.refuse_preflight(C.load(_proj(tmp_path)), tools="implement", unattended=False)


# ══ 工人跳出 worktree 写到活工作区 ═════════════════════════════
# ⭐ 这条把 A-2 从「完全判不了」往回拉了一步：
#    工人写到 worktree **之外**不可枚举，但「项目的活工作区变没变」是可判的。
# ⚠️ 它是**发现**不是**阻止**——发现得晚，总比不发现好。

def test_活工作区被改动要能发现(tmp_path):
    """⛔ 这是允许贵模型做写操作的**前提守卫**：子代理跑在编排方的工作目录里，
    若它写到了 worktree 外面，改动会落进**用户的活文件**——
    而 worktree 空转、闸全绿、台账记绿。这道守卫就是用来抓那种情形的。"""
    from devloop import constitution as C
    paths = _proj(tmp_path)
    _commit(paths.project, "src/a.py", "x = 1\n")
    before = C.workspace_state(paths.project)
    (paths.project / "src" / "a.py").write_text("x = 999\n", encoding="utf-8")
    r = C.check_workspace(C.load(paths), before, C.workspace_state(paths.project))
    assert not r.clean and "a.py" in r.summary()


def test_活工作区新增文件也要发现(tmp_path):
    from devloop import constitution as C
    paths = _proj(tmp_path)
    _commit(paths.project, "src/a.py", "x = 1\n")
    before = C.workspace_state(paths.project)
    (paths.project / "src" / "偷偷加的.py").write_text("y = 2\n", encoding="utf-8")
    r = C.check_workspace(C.load(paths), before, C.workspace_state(paths.project))
    assert not r.clean and "偷偷加的" in r.summary()


def test_活工作区没动时干净(tmp_path):
    """⚠️ 防回归：正常派单不该天天误报。"""
    from devloop import constitution as C
    paths = _proj(tmp_path)
    _commit(paths.project, "src/a.py", "x = 1\n")
    before = C.workspace_state(paths.project)
    assert C.check_workspace(C.load(paths), before, C.workspace_state(paths.project)).clean


def test_运行产物不算改动(tmp_path):
    """⚠️ 台账、回执、作业记录每单都变——把它们算进来这道守卫会天天红，
    而天天红的守卫等于没有守卫。"""
    from devloop import constitution as C
    paths = _proj(tmp_path)
    (paths.project / ".gitignore").write_text(".devloop/telemetry.jsonl\n", encoding="utf-8")
    _commit(paths.project, "src/a.py", "x = 1\n")
    before = C.workspace_state(paths.project)
    paths.telemetry.parent.mkdir(parents=True, exist_ok=True)
    paths.telemetry.write_text('{"task":"x"}\n', encoding="utf-8")
    assert C.check_workspace(C.load(paths), before, C.workspace_state(paths.project)).clean


def test_worktree自己的改动不算活工作区被改(tmp_path):
    """⚠️ 最要紧的防回归：工人**本来就该**在 worktree 里改东西。
    那些改动不该被这道守卫抓到——它盯的是 worktree **之外**。"""
    from devloop import constitution as C
    from devloop import worktree as W
    paths = _proj(tmp_path)
    _commit(paths.project, "src/a.py", "x = 1\n")
    before = C.workspace_state(paths.project)
    wt = W.create(paths.project, "t")
    (wt.path / "src" / "a.py").write_text("工人在 worktree 里改的\n", encoding="utf-8")
    assert C.check_workspace(C.load(paths), before, C.workspace_state(paths.project)).clean, \
        "⛔ worktree 里的改动被误报成「活工作区被改」——那会让每一单都红"


def test_活工作区守卫的命中文案不许指控工人(tmp_path):
    """⛔ 实测：工人零动作、人只做日常动作（改文件/提交/再改），
    这道守卫照样命中——而文案原本写的是「工人写到了隔离 worktree 之外」，
    **把人自己的动作栽给了工人**。

    ⚠️ 判不出是谁改的，就不许在文案里指名道姓。"""
    from devloop import constitution as C
    paths = _proj(tmp_path)
    _commit(paths.project, "a.txt", "v1\n")
    before = C.workspace_state(paths.project)
    (paths.project / "b.txt").write_text("人自己改的\n", encoding="utf-8")
    r = C.check_workspace(C.load(paths), before, C.workspace_state(paths.project))
    assert not r.clean
    assert "工人" not in r.summary(), f"文案指控了工人：{r.summary()}"
    assert "判不出是谁" in r.summary()
