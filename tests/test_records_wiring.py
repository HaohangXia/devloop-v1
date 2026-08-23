"""证明「不可恢复记录」这道判据**真的接上了**，不是写完躺在那儿。

## ⛔ 为什么要单独一个文件钉接线

`tests/test_records.py` 证的是判据本身对不对。⚠️ 那是**第 2 种假绿**最爱的形状：
实现了、单测全绿、但生产路径上根本没人调它。这个项目已经栽过一次同类——
`cmd_autopilot` 漏传 `before/ws_before/halt`，T5 三道宪法在自动驾驶路径上
**全是死的**，而单测全绿（它们直接调 `_run_unit`，绕过了那个作用域）。

所以这里钉三层，缺一层就可能悄悄失效：

1. `snapshot()` 真的把记录指纹装进去了（不装 = 判据拿到空字典 = 恒不命中）
2. `_run_unit` 里真的调了 `check_records`（不调 = 判据永远不跑）
3. 端到端：删一个记录文件，`check_records` 真的命中
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path

import pytest

from devloop import cli, constitution, records
from devloop.config import ProjectPaths

#  ⚠️ 这份必须是**合规的**宪法：加载器强制要求至少一条树内判据和至少一条
#     [[unjudged]]，否则直接 ConfigError。⛔ 它拒绝的正是「覆盖 0 条却每单
#     报无命中」那种假绿——夹具照着真实要求写，别去绕它。
CON_TOML = """\
schema = 1

[[protected_file]]
clause = "A-1"
title  = "闸"
path   = ".devloop/gates.sh"

#  ⚠️ 模式必须在夹具里真匹配到文件——加载器会拒绝空匹配的模式，
#     理由是「写错的模式会永远报无命中」。所以指向夹具里确实存在的 x.txt。
[[protected_tree]]
clause = "C-2"
title  = "不得改受保护文件"
glob   = "x.txt"

[[unjudged]]
clause = "A-2"
status = "partial"
why    = "工人有 Bash，写到白名单之外不可枚举"
"""


def _git(cwd: Path, *a: str) -> None:
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"git {' '.join(a)}: {r.stderr}"


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".devloop" / "reports").mkdir(parents=True)
    (p / ".gitignore").write_text(".devloop/\n", encoding="utf-8")
    (p / ".devloop" / "gates.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(CON_TOML, encoding="utf-8")
    (p / ".devloop" / "telemetry.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (p / "x.txt").write_text("x", encoding="utf-8")
    _git(p.parent, "init", "-q", str(p))
    _git(p, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(p, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    return p


# ── 第 1 层：快照真的装了 ────────────────────────────────────────

def test_快照里真的有记录指纹(proj: Path) -> None:
    """⛔ 装不进去 = 判据永远拿到空字典 = 恒不命中 = 一个可信的绿。"""
    paths = ProjectPaths(proj)
    con = constitution.load(paths)
    snap = constitution.snapshot(paths, con)
    assert snap.records, "⛔ snapshot() 没把记录指纹装进去——判据会恒不命中"
    assert any("telemetry.jsonl" in k for k in snap.records), list(snap.records)


# ── 第 2 层：生产路径上真的调了 ──────────────────────────────────

def test_run_unit里真的调了check_records() -> None:
    """⛔ 判据落在源码调用点上：要真触发它得有工人真去删记录，那不能拿来做测试。"""
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "check_records" in called, (
        "⛔ `_run_unit` 里没有调用 `check_records`——这道判据写完就没人跑。"
        "⚠️ 它必须和 check_refs / check_files 在同一段 T5 里，"
        "那样两个调用点（cmd_dispatch / cmd_autopilot）自动都覆盖。")


def test_它和另外两道判据在同一段里() -> None:
    """⚠️ 挂在同一个 `checks` 列表里，才能共享那两条已经钉死的接线。
    ⛔ 单独另起一段就得自己再接一遍两个调用点——那正是栽过的坑。"""
    src = inspect.getsource(cli)
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "checks" for t in node.targets)):
            continue
        seg = ast.get_source_segment(src, node) or ""
        assert "check_records" in seg, (
            f"⛔ check_records 不在 `checks` 列表里。当前 checks =\n{seg}")
        return
    pytest.fail("⛔ `_run_unit` 里找不到 `checks = [...]` —— T5 那一段被改掉了？")


# ── 第 3 层：端到端真的会命中 ────────────────────────────────────

def test_删掉记录文件_check_records真的命中(proj: Path) -> None:
    paths = ProjectPaths(proj)
    con = constitution.load(paths)
    before = constitution.snapshot(paths, con)

    (proj / ".devloop" / "telemetry.jsonl").unlink()

    res = constitution.check_records(con, paths, before)
    assert not res.clean, "⛔ 台账被删了，check_records 却报干净"
    assert res.halts, "命中了却不是停机级别"
    assert "telemetry.jsonl" in res.summary(), res.summary()


def test_正常追加台账不命中(proj: Path) -> None:
    """⭐ **误报比漏报更致命**：派单自己就会往台账追加一行，
    判据若写成「哈希变了就报」，它每一单都报——而天天喊狼来了的守卫会被关掉。"""
    paths = ProjectPaths(proj)
    con = constitution.load(paths)
    before = constitution.snapshot(paths, con)

    with (proj / ".devloop" / "telemetry.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"a":2}\n')
    (proj / ".devloop" / "reports" / "new.json").write_text("{}", encoding="utf-8")

    res = constitution.check_records(con, paths, before)
    assert res.clean, f"⛔ 正常派单产生的追加被误报成命中：{res.summary()}"


def test_归档与指纹看的是同一批文件(proj: Path, tmp_path: Path) -> None:
    """⛔ 保险柜存的和守卫看的必须是同一批。两边各扫各的，
    迟早会出现「守卫报了但柜子里没有」或者反过来。"""
    fp = set(records.fingerprint(proj))
    dest = tmp_path / "vault"
    records.archive(proj, dest)
    #  归档落在 `dest/<副本目录名>/...`，指纹的键是 `<副本目录名>/...`——
    #  所以要相对 `dest` 取路径，⛔ 不是相对那个子目录（第一版就写错了）。
    archived = {f.relative_to(dest).as_posix()
                for f in dest.rglob("*") if f.is_file()}
    assert archived == fp, f"归档 {sorted(archived)} ≠ 指纹 {sorted(fp)}"
