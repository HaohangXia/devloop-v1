"""接线层的红测（第三轮审查补，2026-07-28）。

⚠️ **审查指出 `cli.py` 零测试覆盖，而本轮三条 CRITICAL 全从这个空洞里长出来。**

宪法、计划、闸的机制都有函数级单测，但**决定真发生什么的那一层没人守**：
一个函数写得再对，没被调用就等于不存在——那是六种假绿里的第二种
（实现了但没接线），而且因为单测是绿的，**它在测试报告里长得像「已覆盖」**。

本文件测的都是「谁在什么时刻调了谁」，不测函数自身的逻辑。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devloop import backends
from devloop.config import ConfigError


CON = """\
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


def _proj(tmp_path, *, con=True):
    """一个能跑 dispatch 的最小项目（真 git 仓库 + 闸 + 规则摘要）。"""
    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'PASS\\t语法\\tok\\n'\nexit 0\n", encoding="utf-8")
    (p / ".devloop" / "rules-digest.md").write_text("# 规则\n", encoding="utf-8")
    if con:
        (p / ".devloop" / "constitution.toml").write_text(CON, encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    (p / "f.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=p, check=True,
                   capture_output=True)
    task = tmp_path / "t.md"
    task.write_text("# 角色\nx\n\n# 任务\ny\n\n# 禁令\nz\n", encoding="utf-8")
    return p, task


def _fake_backend(monkeypatch):
    reg = backends.Registry(
        {"b": backends.Backend(name="b", kind="api", model="m", source="test",
                               base_url="http://x", auth_token="t")}, {}, "b", "test")
    monkeypatch.setattr(backends, "load", lambda: reg)
    return reg


# ══ F5 · 宪法的硬拒绝必须在 --detach 之前生效 ══════════════════

def test_detach前必须先跑宪法的硬拒绝(tmp_path, monkeypatch, capsys):
    """⛔ 宪法**唯一**那条硬拒绝是「不许在没人看着的时候给工人开对外通道」
    （`--tools full` + `--detach`）。而 `--detach` 的提前 return 原本排在
    宪法块**之前**——于是那条规则在它唯一该生效的场景里是**死代码**。

    更糟的是：重建给子进程的命令行会去掉 `--detach`，所以子进程那边
    `detach=False`，也拒不了。**两条路径同时失效。**
    """
    from devloop import cli, jobs
    proj, task = _proj(tmp_path)
    _fake_backend(monkeypatch)
    monkeypatch.setattr(jobs, "launch",
                        lambda *a, **k: pytest.fail("⛔ 不该走到起后台作业这一步"))
    # ⚠️ 断言的是**用户看得到的行为**：`main` 会把 ConfigError 转成退出码 2，
    #    那是对的（工具/输入错），所以这里不该断言异常类型。
    rc = cli.main(["dispatch", "--project", str(proj), "--task", str(task),
                   "--backend", "b", "--tools", "full", "--detach"])
    out = capsys.readouterr().out
    assert rc == 2, f"宪法硬拒绝必须挡住，实得退出码 {rc}"
    assert "full" in out and "detach" in out, "必须说清是哪个组合被拒"


def test_detach也必须先校验锚(tmp_path, monkeypatch, capsys):
    """⛔ `--detach` 原本连宪法都不加载——于是后台作业跑在一个
    **从没被校验过的判定基准**上。无人值守恰恰是最需要校验的时候。"""
    from devloop import cli, constitution, jobs
    proj, task = _proj(tmp_path)
    _fake_backend(monkeypatch)
    monkeypatch.setattr(jobs, "launch",
                        lambda *a, **k: pytest.fail("⛔ 锚没校验就不该起作业"))
    # 没有锚文件 → verify_anchor 报 broken → 必须拒绝，而不是照起作业
    rc = cli.main(["dispatch", "--project", str(proj), "--task", str(task),
                   "--backend", "b", "--tools", "implement", "--detach"])
    assert rc == 2, f"锚不可信时必须拒绝派单，实得 {rc}"


def test_未启用宪法的项目照常能派(tmp_path, monkeypatch, capsys):
    """⚠️ 防回归：还没接宪法的项目不能被这条改动挡住——
    但**必须说出来**，不许静默地在「没有红线」的状态下派单。"""
    from devloop import cli, jobs
    proj, task = _proj(tmp_path, con=False)
    _fake_backend(monkeypatch)
    seen = {}

    def fake_launch(*a, **k):
        seen["起了"] = True
        return _Job(proj)

    monkeypatch.setattr(jobs, "launch", fake_launch)
    cli.main(["dispatch", "--project", str(proj), "--task", str(task),
              "--backend", "b", "--detach"])
    out = capsys.readouterr().out
    assert seen.get("起了"), "没宪法的项目应该照常能起作业"
    assert "未启用宪法" in out, "⛔ 不许静默——人有权知道现在没有红线"


class _Job:
    def __init__(self, proj):
        self.id = "J"
        self.log = proj / "console.log"
        self.meta = {"pid": 1}


# ══ F3 · 派出去了就一定留账 ═══════════════════════════════════

def test_派单后抛异常也必须留下一行台账(tmp_path, monkeypatch):
    """⛔ 钱在 `dispatch_one` 那一刻就花掉了，而台账在最后一步才记，
    中间隔着四个环节。任何一处抛异常，这一单**一行账都不写**。

    而自动驾驶的三条防线（已花多少 / 派了几次 / 有没有算不出成本的）
    **全都只从台账读**。台账没行，三个数都是 0——
    于是它可以一直派、一直花，屏幕上一直印「花了 $0.0000 · 派了 0 次」。
    """
    from devloop import cli, dispatch as D, telemetry, worktree as wt_mod
    from devloop.config import ProjectPaths
    from devloop.models import Receipt, TaskSpec, WorkerConfig

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)
    monkeypatch.setattr(
        D, "dispatch_one",
        lambda *a, **k: D.DispatchResult("t", Receipt(result="ok"), None))
    monkeypatch.setattr(cli, "dispatch_one", lambda *a, **k:
                        D.DispatchResult("t", Receipt(result="ok"), None))
    # 固化产出时炸掉（G-54 的 BranchHijack 就是这条路径）
    monkeypatch.setattr(wt_mod.Worktree, "commit_result",
                        lambda self, n, **k: (_ for _ in ()).throw(
                            wt_mod.BranchHijack("模拟分支劫持")))

    ok, lines = cli._run_unit(
        TaskSpec.load(task), paths,
        WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="implement", max_turns=10, gate_fp=None, writes=True)

    assert not ok
    assert paths.telemetry.exists(), "⛔ 派出去了就必须留账——钱已经花了"
    #  ⭐ 数**单元**不数行：一单从 G-108 起是「开跑行 + 收工行」两行。
    #     ⛔ 断言行数会把「记账时刻提前」这件正确的改动判成回归。
    rows = telemetry.units([json.loads(l) for l in
                            paths.telemetry.read_text(encoding="utf-8").splitlines()
                            if l.strip()])
    assert len(rows) == 1, f"应有 1 单，实得 {len(rows)}"
    assert rows[0]["ok"] is False
    assert "BranchHijack" in json.dumps(rows[0], ensure_ascii=False), \
        "失败原因要写进账里，否则事后查不出为什么"


def test_建worktree就失败时不留账(tmp_path, monkeypatch):
    """⚠️ 边界：连 worktree 都没建起来 = **一分钱都没花**，
    这时留账反而是污染——账本记的是「花过钱的单」。"""
    from devloop import cli, worktree as wt_mod
    from devloop.config import ProjectPaths
    from devloop.models import TaskSpec, WorkerConfig

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)
    monkeypatch.setattr(wt_mod, "create", lambda *a, **k: (_ for _ in ()).throw(
        ConfigError("建 worktree 失败")))
    ok, _ = cli._run_unit(
        TaskSpec.load(task), paths,
        WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="implement", max_turns=10, gate_fp=None, writes=True)
    assert not ok
    rows = ([] if not paths.telemetry.exists() else
            [l for l in paths.telemetry.read_text(encoding="utf-8").splitlines()
             if l.strip()])
    assert not rows, "没花钱就不该记账"


# ══ F4 · T5 必须真的被调用 ════════════════════════════════════

def test_T5的既有引用检查必须被调用(tmp_path, monkeypatch):
    """⛔ `check_refs` / `check_files` 写了、测了，**生产代码里一次都没调用**。
    宪法里「不得删除或改写既有引用」（B-2）因此完全不设防——
    而因为它们有绿测，在测试报告里长得像「已覆盖的机制」。"""
    from devloop import cli, constitution, dispatch as D, worktree as wt_mod
    from devloop.config import ProjectPaths
    from devloop.models import Receipt, TaskSpec, WorkerConfig

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)
    con = constitution.load(paths)
    called = []
    monkeypatch.setattr(constitution, "check_refs",
                        lambda *a, **k: called.append("refs") or
                        constitution.ConstitutionResult(0, [], con.unjudged))
    monkeypatch.setattr(constitution, "check_files",
                        lambda *a, **k: called.append("files") or
                        constitution.ConstitutionResult(0, [], con.unjudged))
    monkeypatch.setattr(cli, "dispatch_one", lambda *a, **k:
                        D.DispatchResult("t", Receipt(result="ok"), None))

    before = constitution.snapshot(paths, con)
    cli._run_unit(TaskSpec.load(task), paths,
                  WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
                  tools="implement", max_turns=10, gate_fp=None, writes=True,
                  con=con, base=wt_mod.resolve_base(proj), before=before)
    assert "refs" in called, "⛔ T5 的 check_refs 必须被调用"
    assert "files" in called, "⛔ T5 的 check_files 必须被调用"


def test_派单次数不许只依赖台账(tmp_path):
    """⛔ 台账是唯一可信的**结果**来源，但「派出去过几次」不该依赖任何 IO：
    账本写失败、被删、被撕半行，次数上限就读到 0——
    而那条上限的全部意义就是「不管别的怎么坏，它都能停住」。"""
    from devloop import autopilot as A
    prog = A.Progress(dispatches=0)
    prog.dispatches = max(prog.dispatches, 3)      # 进程内数到 3，账本 0
    assert prog.dispatches == 3, "Progress.dispatches 必须可被进程内计数覆盖"


def test_宪法坏了不许降级成未启用(tmp_path, monkeypatch, capsys):
    """⛔ 「宪法坏了」和「没有宪法」是两回事。

    早先两者都被降级成一句「⚠️ 未启用宪法」然后照常派单——于是
    TOML 语法错 / schema 不认 / 受保护文件写错路径 / 没登记 unjudged
    这四种**本该退出码 2** 的硬错全变成一行警告，之后 T0、T2、硬拒绝
    一个都不跑。而那句提示本身还是假话：**宪法在，只是坏了。**
    """
    from devloop import cli, jobs
    proj, task = _proj(tmp_path)
    _fake_backend(monkeypatch)
    (proj / ".devloop" / "constitution.toml").write_text(
        "schema = 1\n这不是合法 TOML [[[\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "launch",
                        lambda *a, **k: pytest.fail("⛔ 宪法坏了就不该派单"))
    rc = cli.main(["dispatch", "--project", str(proj), "--task", str(task),
                   "--backend", "b", "--detach"])
    out = capsys.readouterr().out
    assert rc == 2, f"宪法坏了必须退出码 2，实得 {rc}"
    assert "未启用宪法" not in out, "⛔ 不许说成「未启用」——它在，只是坏了"


# ══ 写任务零改动 = 失败，不是「没产出」════════════════════════

def test_写任务什么都没改必须判失败(tmp_path, monkeypatch):
    """⛔ 与 G-53 同一类：**一个什么都没验证的结果报了绿**。

    现状：写任务的 worktree 零改动 → 只打印一句「工人没有产出，无可固化」，
    而 `res.ok` **保持 True**；闸在没动过的检出上当然也全过；
    台账于是记 `ok=true, gate_ok=true` —— **一单什么都没干的活被记成成功**。

    ⚠️ 这不是「宽容」，是**判据的维度错了**：写任务的验收对象是「改动」，
    没有改动就没有验收对象。闸绿只说明「没弄坏东西」，不说明「干了活」。
    """
    from devloop import cli, dispatch as D
    from devloop.config import ProjectPaths
    from devloop.models import Receipt, TaskSpec, WorkerConfig

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)
    # 工人「成功」返回，但一个字节都没改
    monkeypatch.setattr(cli, "dispatch_one", lambda *a, **k:
                        D.DispatchResult("t", Receipt(result="我看了看，没什么要改的"), None))

    ok, lines = cli._run_unit(
        TaskSpec.load(task), paths,
        WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="implement", max_turns=10, gate_fp=None, writes=True)

    assert not ok, "⛔ 写任务零改动必须判失败"
    txt = "\n".join(lines)
    assert "没有产出" in txt
    rows = [json.loads(l) for l in
            paths.telemetry.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[-1]["ok"] is False, "台账里也必须是失败"


def test_只读任务零改动照常算成功(tmp_path, monkeypatch):
    """⚠️ 防回归：只读任务**本来就不该有改动**——那是正常，不是失败。"""
    from devloop import cli, dispatch as D
    from devloop.config import ProjectPaths
    from devloop.models import Receipt, TaskSpec, WorkerConfig

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)
    monkeypatch.setattr(cli, "dispatch_one", lambda *a, **k:
                        D.DispatchResult("t", Receipt(result="报告正文"), None))
    ok, _ = cli._run_unit(
        TaskSpec.load(task), paths,
        WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="readonly", max_turns=10, gate_fp=None, writes=False)
    assert ok


def test_活工作区守卫必须被真的调用(tmp_path, monkeypatch):
    """⛔ 本轮最该记住的教训：**没接线的守卫等于不存在**，
    而且因为它有绿测，在测试报告里长得像「已覆盖」。"""
    from devloop import cli, constitution, dispatch as D, worktree as wt_mod
    from devloop.config import ProjectPaths
    from devloop.models import Receipt, TaskSpec, WorkerConfig

    proj, task = _proj(tmp_path)
    paths = ProjectPaths(proj)
    con = constitution.load(paths)
    called = []
    monkeypatch.setattr(constitution, "check_workspace",
                        lambda *a, **k: called.append(1) or
                        constitution.ConstitutionResult(0, [], []))
    monkeypatch.setattr(cli, "dispatch_one", lambda *a, **k:
                        D.DispatchResult("t", Receipt(result="ok"), None))
    cli._run_unit(TaskSpec.load(task), paths,
                  WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
                  tools="implement", max_turns=10, gate_fp=None, writes=True,
                  con=con, base=wt_mod.resolve_base(proj),
                  before=constitution.snapshot(paths, con),
                  ws_before=constitution.workspace_state(proj))
    assert called, "⛔ check_workspace 必须被调用"
