"""建 worktree 之前的磁盘估算（H-5）。

## ⛔ 缺陷不是「空壳目录」，是 `if s > 0` 把三种状态压成了一种

`check_disk` 的原逻辑：拿 `same[-1]`（同项目最后一个 worktree 目录）量大小，
`if s > 0` 就用它，否则退回写死的 `_FALLBACK_SIZE_MB = 200`。
而 `_dir_size_mb` 有三种返回：

| 返回 | 意思 | 原来的下场 |
|---|---|---|
| `0.0` | 目录是**真的空**（删一半留下的壳） | `s > 0` 假 → 退回 200 |
| `-1.0` | 条目超上限，**量不出来** | `s > 0` 假 → 退回 200 |
| 合法小值 | 真的很小 | 用它 ✅ |

⛔ 前两种是完全不同的事实，却走同一条路，一起掉进那个**模块自己用 ⛔ 警告过
「不能用」的写死常数**。空壳只是今天触发它的那一种。

实测（2026-08-02）：`C:/pg/.devloop-worktrees/` 下 eco-ob 的唯一残壳是 0.0 MB
→ est = 200；而 eco-ob 一个真 worktree 是 **251 MB**，`gate_sync` 把
gitignored 的 `game/.godot`（110 MB）拷进去后稳态约 **361 MB**。
⚠️ 偏差是低估 20–45%（⛔ 不是我原先审计里写的「3 倍」——610 MB 那个数含
135 MB 的 `.git`，worktree 不含它）。
另测：同机另外三个仓 >20000 条目，
`_dir_size_mb` 撞上限返回 -1.0 → **同样退回 200**。

## ⭐ 三个假设一起塌了

`same[-1]` 的注释写的是「最近那个，形态最接近」。三条全不成立：

1. `Path.iterdir` 文档明写 arbitrary order；本机 NTFS 实测是**按文件名排序**。
   在 devloop 自己的 root 上看着对，纯属任务 id `r1<r2<r3` 恰好与时间同序。
2. 最近建的不等于最完整——`Worktree.remove()` 在 Windows 上文件被占用时
   会留下空壳或半删壳，现场那个就是。
3. 「形态最接近」对**容量检查**而言本来就该取 `max`，不是取 latest。

⭐ 改成 `max` 一次解决三件事：空壳（0 被滤掉）、半删壳（被完整的压住）、
iterdir 无序（max 与顺序无关）——**「该怎么排序」这个问题因此自然消失**。
"""

from __future__ import annotations

import pytest

from devloop import worktree as wt


# ── 哨兵：「量不出来」不许被读成「0」 ─────────────────────────────

def test_量不出来返回None而不是负数(tmp_path) -> None:
    """⛔ `-1.0` 只有在调用方显式区分它时才是哨兵，否则它就是个负数
    ——而调用方写的是 `if s > 0`。"""
    d = tmp_path / "many"
    d.mkdir()
    for i in range(12):
        (d / f"f{i}").write_text("x", encoding="utf-8")
    assert wt._dir_size_mb(d, cap_entries=5) is None


def test_真的空返回零而不是None(tmp_path) -> None:
    """⚠️ 空和量不出来是**两件事**，判据要能分开它们。"""
    d = tmp_path / "empty"
    d.mkdir()
    assert wt._dir_size_mb(d) == 0.0


def test_不存在的目录也是量不出来(tmp_path) -> None:
    assert wt._dir_size_mb(tmp_path / "没有这个") is None


# ── ⛔ 写死的 200 不许再存在 ──────────────────────────────────────

def test_没有写死的兜底常数了() -> None:
    """⛔ 一个用得上的场景都没有：有同项目 worktree → 取 max；第一单 →
    按 checkout 实测；连它都量不出 → 那正是「判不出来」，按 docstring 该放行，
    ⚠️ 不是该编一个数。

    它唯一一次在生产路径上真正生效就是 2026-08-02 eco-ob 那次，而它当时是错的。
    """
    assert not hasattr(wt, "_FALLBACK_SIZE_MB"), \
        "⛔ 写死的兜底常数回来了——模块自己的注释就写着「不能用一个写死的常数」"


# ── 估算：取 max，不取「最后一个」 ────────────────────────────────

def _root_with(tmp_path, sizes: dict[str, int]):
    """造一个 worktree root，每个目录塞指定 KB。"""
    root = tmp_path / ".devloop-worktrees"
    root.mkdir()
    for name, kb in sizes.items():
        d = root / name
        d.mkdir()
        if kb:
            (d / "blob").write_bytes(b"x" * (kb * 1024))
    return root


def test_空壳不许把估算拉到零(tmp_path) -> None:
    """⛔ 这是缺陷本身：名字排最后的那个恰好是空壳。"""
    root = _root_with(tmp_path, {"p-a-1": 3000, "p-zzz-shell": 0})
    proj = tmp_path / "p"
    proj.mkdir()
    est = wt._estimate_mb(proj, root)
    assert est is not None and est > 2.0, \
        f"⛔ 空壳把估算吃掉了：{est}——同项目还有一个 3MB 的完整壳在那儿"


def test_取最大而不是取名字最后那个(tmp_path) -> None:
    """⭐ 取 max 之后「iterdir 有没有顺序保证」这个问题就不存在了。"""
    root = _root_with(tmp_path, {"p-big-1": 5000, "p-zzz-small": 100})
    proj = tmp_path / "p"
    proj.mkdir()
    est = wt._estimate_mb(proj, root)
    assert est is not None and est > 4.0, f"⛔ 取到了小的那个：{est}"


def test_别的项目的worktree不许参与估算(tmp_path) -> None:
    """⛔ 实测 devloop 一个壳 4MB、eco-ob 一个 251MB，差 60 倍。
    拿错项目的来估等于没估。"""
    root = _root_with(tmp_path, {"other-huge-1": 9000, "p-mine-1": 200})
    proj = tmp_path / "p"
    proj.mkdir()
    est = wt._estimate_mb(proj, root)
    assert est is not None and est < 1.0, f"⛔ 把别的项目的壳算进来了：{est}"


def test_gitignore的同步目录必须算进估算(tmp_path) -> None:
    """⛔ 这一段是本次差点漏掉的：`synced_paths()` 返回的是**相对项目根的字符串**
    （如 `'game/.godot'`），不是 Path。第一版直接把 str 丢进 `_dir_size_mb`，
    `p.is_dir()` 抛 AttributeError，又被一个裸 `except Exception: pass` 吞掉
    ——**520 条测试全绿**，而 eco-ob 的估算静默从 256 掉到 146（低估 43%）。

    ⭐ `gate_sync` 待会儿真的会把这些目录拷进 worktree，它们**占的是同一块盘**。
    ⚠️ eco-ob 的 `game/.godot` 就有 110 MB，比 tracked 部分的 3/4 还多。
    """
    import subprocess

    proj = tmp_path / "g"
    (proj / ".devloop").mkdir(parents=True)
    (proj / ".devloop" / "config.toml").write_text(
        '[gates]\nsync_ignored_paths = ["cache"]\n', encoding="utf-8")
    (proj / "cache").mkdir()
    (proj / "cache" / "big").write_bytes(b"x" * (4 * 1024 * 1024))   # 4 MB
    (proj / "tracked.txt").write_bytes(b"y" * (1024 * 1024))         # 1 MB
    for a in (["init"], ["add", "tracked.txt"]):
        subprocess.run(["git", *a], cwd=proj, capture_output=True)

    est = wt._checkout_size_mb(proj)
    assert est is not None and est > 4.5, \
        f"⛔ 同步目录（4 MB）没算进去，只量到 tracked 的 1 MB：{est}"


def test_同项目一个壳都没有时回退到按checkout量(tmp_path) -> None:
    """⚠️ 「这个项目的第一单」这条路必须有个**实测**的数，
    ⛔ 不许回到写死的常数。"""
    root = tmp_path / ".devloop-worktrees"
    root.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    #  不是 git 仓库 → 量不出来 → None（⛔ 而不是编一个数）
    assert wt._estimate_mb(proj, root) is None


# ── ⛔ 量不出来时只守余量，不假装知道要多少 ────────────────────────

def test_量不出来时报错文案不许出现约零MB(tmp_path, monkeypatch) -> None:
    """⛔ 别让日志里出现「一个 worktree 约 0 MB」这种自相矛盾的话。"""
    root = tmp_path / ".devloop-worktrees"
    root.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    monkeypatch.setattr(wt, "_free_mb", lambda p: 10.0)   # 盘几乎满了
    with pytest.raises(OSError) as e:
        wt.check_disk(proj, root)
    msg = str(e.value)
    assert "约 0 MB" not in msg, f"⛔ 自相矛盾的文案：{msg}"
    assert "量不出来" in msg


def test_量不出来但盘还很空就放行(tmp_path, monkeypatch) -> None:
    """⛔ docstring 写着「判不出来时**放行**——检查本身不该挡住干活」。
    ⚠️ 加了写死常数之后这句话一直是假的：它总能编出个 200 来挡。"""
    root = tmp_path / ".devloop-worktrees"
    root.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    monkeypatch.setattr(wt, "_free_mb", lambda p: 50_000.0)
    wt.check_disk(proj, root)          # ⛔ 不许抛


def test_余量这条底线不依赖任何估算(tmp_path, monkeypatch) -> None:
    """⭐ 「我不知道它多大，但少于 500 MB 空闲一律不开工」是站得住的判断；
    「它大约 200 MB」不是。"""
    root = tmp_path / ".devloop-worktrees"
    root.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    monkeypatch.setattr(wt, "_free_mb", lambda p: wt._HEADROOM_MB - 1)
    with pytest.raises(OSError):
        wt.check_disk(proj, root)
