"""不可恢复记录守卫的测试。

⛔ 先写测试再写实现。本模块守的是「删了拿不回来」的那批文件，
而它最容易写坏的地方不是漏报，是**误报**——见 `test_追加不算命中`。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devloop import records


def _git(cwd: Path, *a: str) -> str:
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"git {' '.join(a)} 失败：{r.stderr}"
    return r.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个带 .devloop/ 的仓库，.devloop 被 gitignore（复刻 eco-ob 的真实形态）。"""
    p = tmp_path / "proj"
    (p / ".devloop" / "reports").mkdir(parents=True)
    (p / ".gitignore").write_text(".devloop/\n", encoding="utf-8")
    (p / ".devloop" / "telemetry.jsonl").write_text(
        '{"task":"a","ok":true}\n', encoding="utf-8")
    (p / ".devloop" / "reports" / "r1.json").write_text('{"x":1}', encoding="utf-8")
    (p / "code.txt").write_text("hello", encoding="utf-8")
    _git(p.parent, "init", "-q", str(p))
    _git(p, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(p, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return p


# ── 前提：这批文件确实不在 git 里 ─────────────────────────────────

def test_守的确实是git管不到的文件(repo: Path) -> None:
    """⛔ 守卫的目标必须真的存在且真的不可恢复——否则就是第 1 种假绿。"""
    tracked = _git(repo, "ls-files", ".devloop").strip()
    assert tracked == "", f".devloop 居然被 git 跟踪了：{tracked}"
    fp = records.fingerprint(repo)
    assert any("telemetry.jsonl" in k for k in fp), f"没扫到台账：{list(fp)}"


# ── 该报的要报 ────────────────────────────────────────────────────

def test_文件被删要命中(repo: Path) -> None:
    before = records.fingerprint(repo)
    (repo / ".devloop" / "telemetry.jsonl").unlink()
    hits = records.verify(repo, before)
    assert hits, "台账被删了却没报"
    assert any("telemetry.jsonl" in h and "消失" in h for h in hits), hits


def test_整个目录被删要命中(repo: Path) -> None:
    before = records.fingerprint(repo)
    import shutil
    shutil.rmtree(repo / ".devloop")
    hits = records.verify(repo, before)
    assert len(hits) >= 2, f"整个目录没了却只报了 {hits}"


def test_台账被截断要命中(repo: Path) -> None:
    """⚠️ 截断比删除更阴——文件还在，粗看没事。"""
    before = records.fingerprint(repo)
    (repo / ".devloop" / "telemetry.jsonl").write_text("", encoding="utf-8")
    hits = records.verify(repo, before)
    assert any("telemetry.jsonl" in h and "变短" in h for h in hits), hits


def test_台账被改写要命中(repo: Path) -> None:
    """长度没变、内容变了——⛔ 只比长度的实现会漏掉这个。"""
    before = records.fingerprint(repo)
    (repo / ".devloop" / "telemetry.jsonl").write_text(
        '{"task":"X","ok":true}\n', encoding="utf-8")
    hits = records.verify(repo, before)
    assert any("telemetry.jsonl" in h and "改写" in h for h in hits), hits


def test_前缀被改写但长度变长也要命中(repo: Path) -> None:
    """⛔ 最阴的一种：把历史抹掉再补几行，长度还变长了。
    只判「变短」的实现会把这个当成正常追加放过去。"""
    before = records.fingerprint(repo)
    (repo / ".devloop" / "telemetry.jsonl").write_text(
        '{"task":"Z","ok":false}\n{"task":"Z2","ok":false}\n', encoding="utf-8")
    hits = records.verify(repo, before)
    assert any("telemetry.jsonl" in h and "改写" in h for h in hits), hits


# ── ⛔ 不该报的一定不能报（误报会让人把守卫关掉） ──────────────────

def test_追加不算命中(repo: Path) -> None:
    """⭐ 这一单自己就会往台账追加一行。判据若是「哈希变了就报」，
    它**每一单都报警**——而喊狼来了的守卫会被关掉，那比没有守卫更坏。"""
    before = records.fingerprint(repo)
    with (repo / ".devloop" / "telemetry.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"task":"b","ok":true}\n')
    assert records.verify(repo, before) == []


def test_新增回执不算命中(repo: Path) -> None:
    """派单本来就会写一份新回执进 .devloop/reports/。"""
    before = records.fingerprint(repo)
    (repo / ".devloop" / "reports" / "r2.json").write_text('{"y":2}', encoding="utf-8")
    assert records.verify(repo, before) == []


def test_没动过就零命中(repo: Path) -> None:
    before = records.fingerprint(repo)
    assert records.verify(repo, before) == []


# ── 兄弟工作副本 ──────────────────────────────────────────────────

def test_兄弟工作副本的记录也在射程里(repo: Path, tmp_path: Path) -> None:
    """⛔ 第一版设计只盯冻结基线那一个，而同一个 .git 底下每个副本各有一份。"""
    sib = tmp_path / "proj-p4"
    _git(repo, "worktree", "add", "-q", "--detach", str(sib), "HEAD")
    (sib / ".devloop").mkdir(exist_ok=True)
    (sib / ".devloop" / "telemetry.jsonl").write_text('{"t":"p4"}\n', encoding="utf-8")

    before = records.fingerprint(repo)
    assert any("proj-p4" in k for k in before), f"没扫到兄弟副本：{list(before)}"

    import shutil
    shutil.rmtree(sib)
    hits = records.verify(repo, before)
    assert any("proj-p4" in h for h in hits), f"兄弟副本没了却没报：{hits}"


def test_派单用的隔离副本不算兄弟(repo: Path, tmp_path: Path) -> None:
    """⚠️ `.devloop-worktrees/` 下的是本工具自己建的一次性副本，
    它天然会来会走，纳入射程就是稳定误报源。"""
    wt = tmp_path / ".devloop-worktrees" / "proj-task-1"
    wt.parent.mkdir(exist_ok=True)
    _git(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    fp = records.fingerprint(repo)
    assert not any(".devloop-worktrees" in k for k in fp), \
        f"一次性副本被纳入射程了：{[k for k in fp if '.devloop-worktrees' in k]}"


# ── 归档 ──────────────────────────────────────────────────────────

def test_归档能把记录拷出来(repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "vault"
    n = records.archive(repo, dest)
    assert n >= 2, f"只归档了 {n} 个文件"
    assert (dest / "proj" / "telemetry.jsonl").exists()
    assert (dest / "proj" / "reports" / "r1.json").exists()


def test_归档是全量重来不是增量(repo: Path, tmp_path: Path) -> None:
    """⛔ 只补不删的归档会让「已被删掉的文件」永远留在保险柜里显示为「还在」，
    于是保险柜自己变成一份说谎的现状快照。"""
    dest = tmp_path / "vault"
    records.archive(repo, dest)
    (repo / ".devloop" / "reports" / "r1.json").unlink()
    records.archive(repo, dest)
    assert not (dest / "proj" / "reports" / "r1.json").exists(), \
        "归档没有反映删除——它变成了一份只增不减的假现状"


# ── ⛔ 运行产物不进射程（2026-08-01 真跑抓到的误报） ────────────────

def test_工具自己的运行状态不算记录(repo: Path) -> None:
    """⛔ **真跑抓到的误报，不是假想。**

    2026-08-01 崩溃恢复演练：第 3 单被**我自己装的这道守卫**判失败——

        宪法命中 A-2 不得写到隔离区之外（记录被抹）
        （devloop/autopilot/resume-drill.json：前缀被改写）

    而那是 autopilot **自己的运行记录**，它每一轮都要重写（`run.save()`）。
    ⚠️ 「只追加」这个判据对**账本**成立，对**可变状态文件**不成立。

    ⛔ 后果不轻：每次自动驾驶跑到第 3 单都会被自己的守卫判失败。

    ⭐ 判据是现成的：本仓库 `.gitignore` 早就把这三个目录定性为
    「工具自动在项目里建的两个目录——**运行产物，不是源码**」。
    记录（台账/回执/任务书）与运行状态（jobs/handoff/autopilot）是两类东西。
    """
    for d in ("jobs", "handoff", "autopilot"):
        (repo / ".devloop" / d).mkdir(parents=True, exist_ok=True)
        (repo / ".devloop" / d / "run.json").write_text('{"rounds":[]}', encoding="utf-8")
    fp = records.fingerprint(repo)
    leaked = [k for k in fp if any(f"/{d}/" in k for d in ("jobs", "handoff", "autopilot"))]
    assert not leaked, f"⛔ 运行状态被纳入射程了：{leaked}"


def test_运行状态被重写不算命中(repo: Path) -> None:
    """⭐ 端到端：正是真跑里发生的那一幕。"""
    d = repo / ".devloop" / "autopilot"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.json").write_text('{"rounds":[{"task":"a"}]}', encoding="utf-8")
    before = records.fingerprint(repo)
    #  autopilot 每轮重写自己的记录——⛔ 前缀必然变
    (d / "s.json").write_text('{"rounds":[{"task":"b"},{"task":"c"}]}', encoding="utf-8")
    assert records.verify(repo, before) == [], "⛔ 正常的运行状态重写被误判成抹除"


def test_台账被改写仍然要命中(repo: Path) -> None:
    """⚠️ 防止上面两条把守卫整个掏空——**账本**的判据一个字都不许松。"""
    before = records.fingerprint(repo)
    (repo / ".devloop" / "telemetry.jsonl").write_text('{"x":9}\n', encoding="utf-8")
    assert records.verify(repo, before), "⛔ 排除运行状态时把台账也放过了"
