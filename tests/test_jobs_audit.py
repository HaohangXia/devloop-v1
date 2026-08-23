"""Phase 6 三路独立审查 + 证伪后补的红测（2026-07-27）。

自查的四条之外，审查实测出更重的七条。**每条都对应一次真跑复现**，
不是推演——审查方给了命令与读数，我逐条自己复验过才落成测试。

⚠️ 本文件与 test_gates_and_config.py 里的 job 测试互补：那边测
`Job.status()` 的语义，这边测**进程边界**（argv 怎么重建、子进程怎么起、
退出码怎么出）——97 条老测试里没有一条覆盖 `cmd_status`。
"""

from __future__ import annotations

import json
import subprocess

import pytest


# ── 造场景（不真起进程、不花钱）──────────────────────────────

def _mkjob(proj, jid, units, *, pid=999999, started="2026-01-01T00:00:00"):
    d = proj / ".devloop" / "jobs" / jid
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps(
        {"job_id": jid, "pid": pid, "started": started, "project": str(proj),
         "telemetry": str(proj / ".devloop" / "telemetry.jsonl"),
         "argv": [], "units": units}, ensure_ascii=False), encoding="utf-8")
    return d


def _proj(tmp_path, specs, rows=()):
    """specs: [(job_id, units)]；rows: 台账行。"""
    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    for jid, units in specs:
        _mkjob(proj, jid, units)
    (proj / ".devloop" / "telemetry.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return proj


def _one(tmp_path, units, rows=()):
    from devloop import jobs
    proj = _proj(tmp_path, [("J1", units)], rows)
    return jobs.load(proj, "J1")


class FakeProc:
    pid = 4242


# ══ C2 · 进度按去重单元名计，不按台账行数计 ════════════════════

def test_同一单元在台账里出现两行不算两单跑完(tmp_path):
    """⛔ 假绿。实测：units=[a,b]、两行都是 a（b 一次没跑）→ 旧实现报
    「done，2 单跑完，全部通过」，CLI 退出码 0。

    **单作业内手工重跑一单即可触发**，不依赖并发串台——是独立于
    「两作业串台账」（那条记为已知取舍）的另一刀。
    """
    j = _one(tmp_path, ["a", "b"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": True},
        {"ts": "2026-01-02T00:01:00", "task": "a", "ok": True}])
    st, msg = j.status()
    assert st != "done", "两行同名不该算两单跑完"
    assert "b" in msg, "必须点名 b 没跑"


def test_同一单元重跑以最后一行为准(tmp_path):
    """去重保留**后写的那行**：重跑的意义就是用新结果覆盖旧结果。"""
    j = _one(tmp_path, ["a"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": False},
        {"ts": "2026-01-02T00:01:00", "task": "a", "ok": True}])
    st, msg = j.status()
    assert st == "done" and "全部通过" in msg


# ══ C3 · status --all 的退出码 ═════════════════════════════════
# ⛔ SPEC 白纸黑字：「0 的意思是『全都成功了』，脚本看到 0 会往下走，而此时活
#    可能一个字都没干——这是自动驾驶最危险的失效模式」。单作业路径守住了，
#    `--all` 是从没被测过的那条：真项目实测 died 0/7 仍返回 0。

def test_status全量模式有作业还在跑必须返回3(tmp_path, monkeypatch, capsys):
    from devloop import cli, jobs
    proj = _proj(tmp_path, [("J1", ["a", "b"])],
                 [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    monkeypatch.setattr(jobs.Job, "pid_alive", lambda self: True)
    assert cli.main(["status", "--project", str(proj), "--all"]) == 3


def test_status全量模式有作业死了必须返回3(tmp_path, capsys):
    from devloop import cli
    proj = _proj(tmp_path, [("J1", ["a", "b"])],
                 [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    assert cli.main(["status", "--project", str(proj), "--all"]) == 3


def test_status全量模式全跑完但有失败返回1(tmp_path, capsys):
    from devloop import cli
    proj = _proj(tmp_path, [("J1", ["a"])],
                 [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": False}])
    assert cli.main(["status", "--project", str(proj), "--all"]) == 1


def test_status全量模式全绿才返回0(tmp_path, capsys):
    from devloop import cli
    proj = _proj(tmp_path, [("J1", ["a"])],
                 [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    assert cli.main(["status", "--project", str(proj), "--all"]) == 0


def test_没有后台作业时两条路径退出码一致(tmp_path, capsys):
    """实测旧实现：`status` 抛未捕获 FileNotFoundError → rc=1（而约定里
    1 = 「至少一单失败」）；`status --all` → rc=0。**同一件事两个相反的退出码**，
    且都不对。正解是 2 = 工具/用法错，不是「活没干好」。"""
    from devloop import cli
    proj = tmp_path / "empty"
    proj.mkdir()
    a = cli.main(["status", "--project", str(proj)])
    b = cli.main(["status", "--project", str(proj), "--all"])
    assert a == b == 2, f"同一事实两条路径必须同码且为 2，实得 {a} / {b}"


# ══ C1 · --detach 的 argv 来源 ════════════════════════════════
# 一处根因（从进程 argv 反推参数），三个后果。

def test_detach的缩写形式不得被接受(tmp_path):
    """⛔ argparse 默认开前缀缩写：`--d`/`--de`/`--det` 全被当成 `--detach`，
    而过滤器只比对字面量 `--detach` → 缩写原样传给子进程 → **无限自我重生**。
    审查实测：3 秒内 16 个作业目录，约 5 个/秒，一单活都不干。
    """
    from devloop import cli
    with pytest.raises(SystemExit):
        cli.main(["dispatch", "--project", str(tmp_path), "--task", "x", "--det"])


class _Stop(Exception):
    """把解析结果劫出来就停，不真跑派单。"""


def _parse(argv):
    import argparse
    from devloop import cli
    holder = {}
    orig = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        holder["ns"] = orig(self, args, namespace)
        raise _Stop()

    argparse.ArgumentParser.parse_args = spy
    try:
        cli.main(argv)
    except _Stop:
        pass
    finally:
        argparse.ArgumentParser.parse_args = orig
    return holder["ns"]


@pytest.mark.parametrize("extra", [
    [],
    ["--parallel", "3"],
    ["--tools", "implement"],
    ["--max-turns", "55"],
    ["--parallel", "2", "--tools", "full", "--max-turns", "7"],
    # ⛔ 2026-07-29 审计抓到：`--wait-for-reset` 当天加进解析器，
    #    `_rebuild_argv` 没跟着改，而这份参数化列表也没跟着加——于是这条
    #    「唯一的守卫」对新参数**恒绿**。守卫本身有盲区，比没有守卫更坏：
    #    它让人以为已经守住了。⚠️ 今后每加一个 dispatch 参数，这里必须同步加一行。
    ["--wait-for-reset"],
    # ⚠️ 列表型参数同样要钉：`--require-pass` 2026-07-30 新加，
    #    而 `--wait-for-reset` 就是这么被 `_rebuild_argv` 吞过一次的。
    ["--require-pass", "语法"],
    ["--require-pass", "语法", "--require-pass", "pytest"],
    ["--wait-for-reset", "--parallel", "2", "--tools", "implement"],
])
def test_重建的argv喂回解析器必须得到同一个请求(tmp_path, extra):
    """⚠️ **本条是这次修法的主测试。**

    `jobs.py` 那句「⛔ 不在这里重新拼参数：拼两遍必然分叉」的顾虑是对的，
    而重建 argv 正是它警告的做法。所以必须有一条不变量测试把分叉钉死，
    否则这个修法比它要修的 bug 更难查。
    """
    from devloop import cli
    task = tmp_path / "t.md"
    task.write_text("x", encoding="utf-8")
    base = ["dispatch", "--project", str(tmp_path), "--task", str(task)]
    ns1 = _parse(base + extra + ["--detach"])
    rebuilt = cli._rebuild_argv(ns1)
    assert "--detach" not in rebuilt, "重建的命令行绝不能再带 --detach"
    ns2 = _parse(rebuilt)
    drop = ("detach", "fn")
    d1 = {k: v for k, v in vars(ns1).items() if k not in drop}
    d2 = {k: v for k, v in vars(ns2).items() if k not in drop}
    assert d1 == d2, f"重建后请求变了：\n{d1}\n{d2}"


def test_重建的argv里路径必须是绝对的(tmp_path, monkeypatch):
    """⛔ 相对 `--project` 会被静默改派到 devloop 包目录——那个目录自己就有
    一份合法 `.devloop/`，于是子进程不报错，只是拿着钱去干了另一个仓库的活。"""
    import os
    from devloop import cli
    task = tmp_path / "t.md"
    task.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ns = _parse(["dispatch", "--project", ".", "--task", "t.md", "--detach"])
    rebuilt = cli._rebuild_argv(ns)
    for flag in ("--project", "--task"):
        v = rebuilt[rebuilt.index(flag) + 1]
        assert os.path.isabs(v), f"{flag} 必须绝对化，实得 {v}"


def test_程序化调用main时派出去的是本次请求(tmp_path, monkeypatch, capsys):
    """⛔ 旧实现取 `sys.argv[1:]`，与传入的 argv 毫无关系。审查实测：
    `sys.argv=["pytest","-q","tests/"]` 时抓到的子进程真实命令行是
    `python -m devloop.cli -q tests/`，而 stdout 照样打印「已在后台启动」、rc=0。

    Phase 7 自动驾驶几乎必然是程序化驱动 `main()`——这条会炸在自动驾驶第一天。
    """
    import sys
    from devloop import backends, cli, jobs
    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    (proj / ".devloop" / "rules-digest.md").write_text("# 规则\n", encoding="utf-8")
    task = tmp_path / "t.md"
    task.write_text("# 角色\nx\n\n# 任务\ny\n\n# 禁令\nz\n", encoding="utf-8")

    reg = backends.Registry(
        {"b": backends.Backend(name="b", kind="api", model="m", source="test",
                               base_url="http://x", auth_token="t")}, {}, "b", "test")
    monkeypatch.setattr(backends, "load", lambda: reg)
    monkeypatch.setattr(sys, "argv", ["pytest", "-q", "tests/"])

    seen = {}

    def fake_launch(project, argv, units, *, telemetry, **kw):
        seen["argv"] = argv
        return jobs.Job("J", project, {"job_id": "J", "pid": 1, "argv": argv,
                                       "units": units, "started": "t",
                                       "telemetry": str(telemetry)})

    monkeypatch.setattr(jobs, "launch", fake_launch)
    cli.main(["dispatch", "--project", str(proj), "--task", str(task),
              "--backend", "b", "--detach"])
    assert "-q" not in seen["argv"] and "tests/" not in seen["argv"], \
        f"派出去的是 sys.argv 而不是本次请求：{seen['argv']}"
    assert str(task) in seen["argv"]


# ══ 后台作业的可诊断性 ═════════════════════════════════════════

def test_后台作业必须以无缓冲模式启动(tmp_path, monkeypatch):
    """⛔ 少了 `-u`，stdout 重定向到文件就是块缓冲：**作业跑完之前 console.log
    恒为 0 字节**，而 CLI 恰恰把这个路径当作「看进度」的手段递给用户。
    这是 G-37 第 2 条（管道缓冲导致后台进度不可见）在工具内部原样复发。
    审查实测对照：无 -u 时 5 秒后 0 B；加 -u 后 5 秒 11,690 B。
    """
    from devloop import jobs
    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: (seen.update(cmd=cmd, **kw), FakeProc())[1])
    proj = tmp_path / "p"
    proj.mkdir()
    jobs.launch(proj, ["dispatch"], ["a"], telemetry=proj / "t.jsonl")
    assert "-u" in seen["cmd"], f"必须无缓冲启动，实得 {seen['cmd']}"


def test_后台作业不得把工作目录改到工具自己的包目录(tmp_path, monkeypatch):
    """包目录自己有一份合法 `.devloop/`——把 cwd 设到那里，相对路径会静默
    解析成 devloop 仓库；写模式下 worktree、分支、闸、台账会全部落错地方。"""
    from pathlib import Path
    from devloop import jobs
    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: (seen.update(kw), FakeProc())[1])
    proj = tmp_path / "p"
    proj.mkdir()
    # ⚠️ 必须换个 cwd 再测：跑测试时的 cwd 恰好就是包目录，
    #    留在原地的话这条测试会因为「碰巧相等」而误报。
    monkeypatch.chdir(tmp_path)
    jobs.launch(proj, ["dispatch"], ["a"], telemetry=proj / "t.jsonl")
    pkg = Path(jobs.__file__).resolve().parent.parent
    assert Path(seen.get("cwd", ".")).resolve() != pkg, "cwd 不许是工具包目录"
    assert Path(seen["cwd"]).resolve() == tmp_path.resolve(), "cwd 应留在用户所在目录"


def test_pid被别的进程占用时不得判为还在跑(tmp_path, monkeypatch):
    """pid 复用：进程死了、pid 被系统分给别的进程 → 死作业永远显示 running，
    而 `halt --kill` 会拿 `taskkill /T /F` 去杀那个**无辜的进程树**。
    审查实测：把真实 explorer.exe 的 pid 塞进 job.json → running，rc 恒 3。
    """
    from devloop import jobs
    j = _one(tmp_path, ["a"])

    class R:
        stdout = '"explorer.exe","999999","Console","3","296,888 K"\n'

    monkeypatch.setattr(jobs, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert j.pid_alive() is False, "映像名对不上，不能算作本作业还活着"


def test_pid还在且映像名对得上才算活着(tmp_path, monkeypatch):
    """防回归：别把「更严」修成「一律判死」——那会让 status 永远报 died。"""
    from devloop import jobs
    j = _one(tmp_path, ["a"])

    class R:
        stdout = '"python.exe","999999","Console","3","30,000 K"\n'

    monkeypatch.setattr(jobs, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert j.pid_alive() is True


def test_最新作业缺记录时必须报错而不是回退(tmp_path):
    """⛔ 旧实现静默跳过没有 job.json 的目录，把**更早的作业**当成最新的报出来
    ——包括报成 done、全部通过、rc=0。而这个状态是 `launch` 自己制造的：
    mkdir → Popen → 最后才写 job.json，中途被打断就永久留下它。"""
    from devloop import jobs
    proj = _proj(tmp_path, [("20260101-000003-1000", ["a"])],
                 [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    (proj / ".devloop" / "jobs" / "20260101-000009-1000").mkdir()
    with pytest.raises(Exception) as e:
        jobs.load(proj)
    assert "20260101-000009" in str(e.value), "必须点名是哪个目录缺记录"


def test_作业记录先于起进程落盘(tmp_path, monkeypatch):
    """把上一条的可达路径直接堵死：Popen 之前 job.json 就该在盘上。"""
    from devloop import jobs
    proj = tmp_path / "p"
    proj.mkdir()
    seen = {}

    def fake_popen(cmd, **kw):
        d = proj / ".devloop" / "jobs"
        seen["existed"] = any((x / "job.json").exists() for x in d.iterdir())
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    jobs.launch(proj, ["dispatch"], ["a"], telemetry=proj / "t.jsonl")
    assert seen["existed"], "起进程之前必须已经有作业记录，否则中途被打断就成孤儿"


# ══ 急停 ══════════════════════════════════════════════════════

def test_只有死作业时急停不算还有活干(tmp_path):
    """died 是「要清理的残骸」，不是「要杀的目标」。旧实现把两者混在
    「N 个作业还活着」里 → 陈旧死作业让 halt 永远返回非 0、且永远清不掉。"""
    from devloop import halt
    proj = _proj(tmp_path, [("J1", ["a", "b"])])
    txt, alive = halt.report(proj, do_kill=False)
    assert alive == 0, "死作业不该计进「还活着」"
    assert "残骸" in txt


def test_被急停的作业要与自己崩掉区分开(tmp_path, monkeypatch):
    """与「失败就报失败，不许猜」同源：被人叫停和自己崩了，从外面看必须能分开。
    旧实现杀完不留痕，两者的 status 完全一样。"""
    from devloop import halt, jobs
    proj = _proj(tmp_path, [("J1", ["a", "b"])])
    monkeypatch.setattr(jobs.Job, "pid_alive", lambda self: True)
    monkeypatch.setattr(halt, "_terminate", lambda pid: (True, "已终止（测试桩）"))
    halt.report(proj, do_kill=True)
    monkeypatch.setattr(jobs.Job, "pid_alive", lambda self: False)
    st, msg = jobs.load(proj, "J1").status()
    assert st == "halted", f"被叫停的作业状态应为 halted，实得 {st}"
    assert "叫停" in msg or "急停" in msg


def test_非windows下后台作业必须新开会话(tmp_path, monkeypatch):
    """POSIX 上 `launch` 没有 `start_new_session` → 子进程仍在父进程组里，
    而 `halt` 的 `os.killpg` 会把**调用者自己的 shell** 一起带走。"""
    from devloop import jobs
    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: (seen.update(kw), FakeProc())[1])
    # ⚠️ 换 `jobs._is_windows` 而不是改 `os.name`——后者会把 pathlib 一起弄坏
    #    （实测：`cannot instantiate 'PosixPath' on your system`）。
    monkeypatch.setattr(jobs, "_is_windows", lambda: False)
    proj = tmp_path / "p"
    proj.mkdir()
    jobs.launch(proj, ["dispatch"], ["a"], telemetry=proj / "t.jsonl")
    assert seen.get("start_new_session") is True
