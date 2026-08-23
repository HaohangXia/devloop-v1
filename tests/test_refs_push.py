"""⛔ 工人把分支推到远端，宪法必须看得见（C-3）。

## 缺陷的形状（2026-08-03 对抗审计实测）

`check_refs` 的循环起点是 `for name, obj in before.refs.items()`
——**T0 时不存在的 ref 天然不在遍历范围里**。于是：

```
git push origin HEAD:refs/heads/leaked-branch
  → * [new branch]  HEAD -> leaked-branch      （成功）
  → 本地新增 refs/remotes/origin/leaked-branch
  → check_refs → ⛔ code = 0，零命中
```

代码已经出仓，而台账、回执、diff 里一个字都查不到。

⚠️ 而 `constitution.toml` 当时声称挡 C-3 的是
`tests/test_no_git_write_verbs.py`（一条**静态检查**，扫工具自己的
`devloop/*.py` 里有没有 `push`）。⛔ 那管的是**工具的代码**，
与工人在 worktree 里敲什么命令毫无关系——**工人手里有 Bash**。

⭐ 判据错在**主语**：条款的主语是工人的动作，检查的对象是工具的源码。

## 本文件的做法

⛔ 不构造假快照。**建一个真的 bare 仓当远端，真推一次**——
因为这条缺陷的本质就是「想象中的射程 ≠ 真实射程」，
而只有真做一遍才知道真实射程在哪。
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

[[unjudged]]
clause = "X-9"
why    = "判不了"

[tree]
coverage = "none"
why      = "夹具项目没有树内基线"
"""


def _git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _proj(tmp_path) -> tuple[ProjectPaths, "object"]:
    """一个带**真远端**的项目。⭐ 远端是本地 bare 仓，不联网。"""
    bare = tmp_path / "remote.git"
    _git(tmp_path.parent if False else tmp_path, "init", "-q", "--bare", str(bare))

    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                             encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(_TOML, encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        _git(p, *a)
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "base")
    _git(p, "remote", "add", "origin", str(bare))
    _git(p, "push", "-q", "origin", "master:master")
    return ProjectPaths(p), bare


# ── ① 真推一次，必须命中 C-3 ──────────────────────────────────────

def test_工人推一条新分支到远端必须命中(tmp_path) -> None:
    """⭐ **本文件的核心。** 这一条在修复前是 `code = 0`。"""
    paths, _bare = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)

    #  工人干的事：在 worktree 里提交，然后推一条新分支出去
    (paths.project / "leak.txt").write_text("秘密\n", encoding="utf-8")
    _git(paths.project, "add", "-A")
    _git(paths.project, "commit", "-qm", "worker work")
    r = _git(paths.project, "push", "-q", "origin",
             "HEAD:refs/heads/leaked-branch", check=False)
    assert r.returncode == 0, f"夹具前提变了，推不上去：{r.stderr}"

    after = C.snapshot(paths, con)
    res = C.check_refs(con, before, after)
    assert not res.clean, f"⛔ 推出去了却零命中：\n{res.summary()}"
    assert any(h.clause == "C-3" for h in res.hits), \
        f"⛔ 命中了但不是 C-3：{[(h.clause, h.detail) for h in res.hits]}"
    assert "leaked-branch" in res.summary(), res.summary()


def test_工人新增一个远端必须命中(tmp_path) -> None:
    """⚠️ `git remote add x <地址>` 之后推过去，`refs/remotes/origin/*`
    **一条都不会变**——光比 ref 看不见这条路。"""
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)

    _git(paths.project, "remote", "add", "外面", "https://example.invalid/x.git")
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert not res.clean and any(h.clause == "C-3" for h in res.hits), res.summary()
    assert "外面" in res.summary(), res.summary()


def test_工人改掉origin的地址必须命中(tmp_path) -> None:
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)
    _git(paths.project, "remote", "set-url", "origin",
         "https://example.invalid/别处.git")
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert not res.clean and any(h.clause == "C-3" for h in res.hits), res.summary()


def test_工人在工具命名空间外建分支要命中(tmp_path) -> None:
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)
    _git(paths.project, "branch", "工人自己的分支")
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert not res.clean and any(h.clause == "B-2" for h in res.hits), res.summary()


# ── ② ⛔ 不许假红：工具自己的动作必须放行 ──────────────────────────

def test_工具自己造的隔离分支不许命中(tmp_path) -> None:
    """⛔ **天天红的守卫等于没有守卫。**

    ⚠️ `commit_result` 每单都在 `refs/heads/devloop/` 下建分支，
    并行时还会同时建好几条。判它 = 每单必红。
    """
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)
    for n in ("devloop/单1-20260803", "devloop/单2-20260803"):
        _git(paths.project, "branch", n)
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert res.clean, f"⛔ 工具自己的分支被判红了：\n{res.summary()}"


def test_stash不许命中(tmp_path) -> None:
    """⚠️ 工人干活中途 `git stash` 是正常动作。"""
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    (paths.project / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(paths.project, "add", "-A")
    _git(paths.project, "commit", "-qm", "a")
    before = C.snapshot(paths, con)
    (paths.project / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(paths.project, "stash", "-q")
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert res.clean, f"⛔ stash 被判红了：\n{res.summary()}"


def test_什么都不做必须干净(tmp_path) -> None:
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)
    assert C.check_refs(con, before, C.snapshot(paths, con)).clean


# ── ③ 原有判据不许被削弱 ──────────────────────────────────────────

def test_既有引用被改写仍然命中(tmp_path) -> None:
    """⚠️ 加新射程时把老射程弄丢，是这个项目栽过的形态。"""
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    (paths.project / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(paths.project, "add", "-A")
    _git(paths.project, "commit", "-qm", "a")
    before = C.snapshot(paths, con)
    _git(paths.project, "reset", "-q", "--hard", "HEAD~1")
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert any(h.clause == "B-2" for h in res.hits), res.summary()


def test_既有引用消失仍然命中(tmp_path) -> None:
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    _git(paths.project, "branch", "devloop/临时")
    before = C.snapshot(paths, con)
    _git(paths.project, "branch", "-D", "devloop/临时")
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert any("消失" in h.detail for h in res.hits), res.summary()


# ── ④ ⛔ 判不了的那部分必须被登记，不许含糊 ────────────────────────

def test_推到裸URL判不了这件事必须写在judged之外() -> None:
    """⛔ `git push <完整URL> HEAD:refs/heads/x` 不经过具名远端，
    **本地零痕迹**：不建 `refs/remotes/*`、不改 `remote.*.url`、reflog 也没有。

    ⚠️ 本判据对这一形态完全无效。⭐ 那就必须**显式登记**，
    让每次判定的尾巴都把它报出来——⛔ 而不是让「无宪法命中」把它盖过去。
    """
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parent.parent
    d = tomllib.loads((root / ".devloop" / "constitution.toml")
                      .read_text(encoding="utf-8"))
    ids = {u["clause"] for u in d.get("unjudged", [])}
    assert "C-3b" in ids, (
        "⛔ 「推到裸 URL」判不了这件事没登记进 [[unjudged]]。\n"
        "   ⚠️ 不登记 = 那句「无宪法命中」把一整条出仓路径盖过去了。")
    why = next(u for u in d["unjudged"] if u["clause"] == "C-3b").get("why", "")
    assert "URL" in why or "url" in why, f"⛔ 理由没说清是哪一形态：{why}"


# ── ⑤ 对抗复核（2026-08-03）证伪的四条：⛔ 判据抓不到，那就必须登记 ────

def test_推tag和任意命名空间确实抓不到而这件事必须登记(tmp_path) -> None:
    """⛔ **这条测试钉的是一句「我们做不到」。**

    对抗复核实测：`git push origin HEAD:refs/tags/x` 走的是**具名远端**、
    用的是最普通的 `git push`，而本地**一条 ref 都不动**——git 只在 refspec
    命中 `remote.<name>.fetch`（默认 `+refs/heads/*:refs/remotes/origin/*`）
    时才写跟踪 ref。

    ⚠️ 而我当天写的登记说「check_refs 覆盖住了经过**具名远端**的推送」——
    ⛔ **那句是假的**，我用挡住的一小半去描述了整条。

    ⭐ 判据分两半：
    ① 先**实跑证明抓不到**（⚠️ 免得哪天真抓到了，这条登记又变成多余的悲观）
    ② 再断言这件事**登记在案**，让每次判定的尾巴都把它报出来
    """
    paths, bare = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)

    #  ⚠️ 工人在**隔离分支**上干活（`devloop/*`），⛔ 不在 master 上——
    #     否则 `refs/heads/master` 一动就撞 B-2，测不到本条要测的东西。
    _git(paths.project, "checkout", "-qb", "devloop/u1")
    (paths.project / "leak.txt").write_text("秘密\n", encoding="utf-8")
    _git(paths.project, "add", "-A")
    _git(paths.project, "commit", "-qm", "worker work")
    r = _git(paths.project, "push", "-q", "origin", "HEAD:refs/tags/泄漏",
             check=False)
    assert r.returncode == 0, f"夹具前提变了：{r.stderr}"

    #  ① 东西**真的**出去了
    out = _git(bare, "for-each-ref", "--format=%(refname)").stdout
    assert "refs/tags/泄漏" in out, f"夹具前提变了，远端没收到：{out}"
    #  ② 而判据看不见
    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert res.clean, (
        "⭐ 抓到了？那就把 C-3b 的登记改窄——⛔ 但先确认不是误报。\n"
        f"   {res.summary()}")

    #  ③ 抓不到 → 必须登记，且理由里要写明**这一形态**
    import pathlib
    import tomllib
    root = pathlib.Path(__file__).resolve().parent.parent
    d = tomllib.loads((root / ".devloop" / "constitution.toml")
                      .read_text(encoding="utf-8"))
    why = next(u for u in d["unjudged"] if u["clause"] == "C-3b")["why"]
    assert "tags" in why or "命名空间" in why, \
        f"⛔ 登记里没写「推 tag / 任意命名空间」这一形态：{why}"


def test_推完把跟踪ref抹掉也抓不到而这件事必须登记(tmp_path) -> None:
    """⛔ `check_refs` 是**两点状态差**，而它看的每一处痕迹工人都写得动。

    ⚠️ 推完加一句 `git update-ref -d refs/remotes/origin/x`，
    本地状态与 T0 逐字节相同 → code=0，而东西已经在别人的服务器上。
    ⭐ 与 `check_records` 判「只追加」不同，这里没有任何只追加的证据源。
    """
    paths, bare = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)

    _git(paths.project, "checkout", "-qb", "devloop/u1")
    (paths.project / "leak.txt").write_text("秘密\n", encoding="utf-8")
    _git(paths.project, "add", "-A")
    _git(paths.project, "commit", "-qm", "worker work")
    _git(paths.project, "push", "-q", "origin", "HEAD:refs/heads/泄漏")
    _git(paths.project, "update-ref", "-d", "refs/remotes/origin/泄漏")

    assert "refs/heads/泄漏" in _git(bare, "for-each-ref",
                                     "--format=%(refname)").stdout
    assert C.check_refs(con, before, C.snapshot(paths, con)).clean

    import pathlib
    import tomllib
    root = pathlib.Path(__file__).resolve().parent.parent
    d = tomllib.loads((root / ".devloop" / "constitution.toml")
                      .read_text(encoding="utf-8"))
    why = next(u for u in d["unjudged"] if u["clause"] == "C-3b")["why"]
    assert "抹除" in why or "update-ref" in why, \
        f"⛔ 登记里没写「推完自己抹掉」这一形态：{why}"


def test_加远端推完再删掉必须命中(tmp_path) -> None:
    """⭐ 这一条**抓得到**，而修复前抓不到。

    ⚠️ `git remote remove` 会连带删掉 `refs/remotes/<名>/*`，于是 ref 与
    远端清单**两维同时归零**。⛔ 原代码的 `if new is not None` 守卫让
    「远端消失」根本不进判据。
    """
    paths, _ = _proj(tmp_path)
    con = C.load(paths)
    _git(paths.project, "remote", "add", "外面", "https://example.invalid/x.git")
    before = C.snapshot(paths, con)
    _git(paths.project, "remote", "remove", "外面")

    res = C.check_refs(con, before, C.snapshot(paths, con))
    assert not res.clean and any(h.clause == "C-3" for h in res.hits), \
        f"⛔ 「加远端→推→删远端」这条路还是零命中：{res.summary()}"


def test_只读的fetch不许被说成有东西被推出去了(tmp_path) -> None:
    """⛔ 措辞就是判据的一部分。

    ⚠️ `refs/remotes/*` 恰恰是 **fetch** 写的。原文案「⛔ 有东西被推出去了」
    在一个**纯只读**动作上字面为假，而它是 halt 级的。
    ⭐ 本判据分不出方向——那就要说「分不出」，不许替它编一个方向。
    """
    paths, bare = _proj(tmp_path)
    con = C.load(paths)
    before = C.snapshot(paths, con)

    #  别人往上游推了一条分支，我们只是 fetch
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    (other / "x.txt").write_text("同事的活\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "teammate")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/同事的分支")
    _git(paths.project, "fetch", "-q", "origin")

    res = C.check_refs(con, before, C.snapshot(paths, con))
    s = res.summary()
    assert "有东西被推出去了" not in s, \
        f"⛔ 把只读的 fetch 说成了推送：\n   {s}"
    if not res.clean:
        assert "分不出方向" in s or "fetch" in s, \
            f"⛔ 命中了却没说清它分不出 push 还是 fetch：\n   {s}"
