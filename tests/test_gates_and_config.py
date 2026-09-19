"""闸与配置。

本项目最贵的几次教训都出在这两处，且**每一条都有对应的测试**：
- 闸把「与 HEAD 不同」当成「工人改的」→ 三条误伤
- 「禁改清单」守卫恒 PASS，什么都没守住 → 虚假安全感
- 找不到配置时静默退回默认值 → 你以为在用 A，实际在用 B
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devloop.config import ConfigError, ProjectPaths
from devloop.gates import GateLine, GateResult, _parse, fingerprint


class TestGateOutputParsing:
    def test_解析三种判定(self):
        out = "PASS\t闸A\t好了\nFAIL\t闸B\t坏了\nSKIP\t闸C\t跳过"
        assert [l.verdict for l in _parse(out)] == ["PASS", "FAIL", "SKIP"]

    def test_忽略非判定行(self):
        """闸脚本会混入 godot 的告警等噪声；把它们当判定会污染结论。"""
        assert len(_parse("WARNING: 20 objects leaked\nPASS\t闸\t好")) == 1

    def test_缺说明列不应崩(self):
        assert _parse("PASS\t闸")[0].detail == ""


class TestGateResultSemantics:
    def test_退出码2是闸自身故障_不是活没干好(self):
        """这个区分救过场：闸脚本语法错时报的是 2，否则我会去查工人的代码。

        环境故障被误判成质量问题，会让人往完全错误的方向排查。
        """
        r = GateResult(2, stderr="bash: syntax error")
        assert r.gate_broken and not r.passed

    def test_退出码1是正常的不合格(self):
        r = GateResult(1, [GateLine("FAIL", "基线守卫", "被改了")])
        assert not r.gate_broken and not r.passed
        assert "基线守卫" in r.summary()

    def test_退出码0为通过(self):
        assert GateResult(0, [GateLine("PASS", "x", "")]).passed

    def test_闸故障时摘要必须点明是闸坏了(self):
        assert "闸自身故障" in GateResult(2, stderr="找不到 bash").summary()


class TestFingerprint:
    def test_内容变则指纹变(self, tmp_path):
        """防篡改第 2 道：工人若在干活途中改闸，闸必须拒绝执行而不是执行被改过的版本。"""
        f = tmp_path / "g.sh"
        f.write_text("echo a", encoding="utf-8")
        before = fingerprint(f)
        f.write_text("echo a # 偷偷加一句", encoding="utf-8")
        assert fingerprint(f) != before

    def test_同内容指纹稳定(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.write_text("same", encoding="utf-8")
        b.write_text("same", encoding="utf-8")
        assert fingerprint(a) == fingerprint(b)


class TestProjectPaths:
    def test_缺_devloop_目录必须报错而非静默降级(self, tmp_path):
        """沿用项目教训：「忘传参数就悄悄退回旧行为」是个坑。
        静默降级会让你以为跑了闸，其实没跑。"""
        with pytest.raises(ConfigError, match=r"没有 \.devloop"):
            ProjectPaths(tmp_path)

    def test_缺规则摘要必须报错(self, tmp_path):
        """--bare 下摘要是工人唯一的规则来源；缺了还派单 = 没护栏就上路。"""
        (tmp_path / ".devloop").mkdir()
        with pytest.raises(ConfigError, match="缺"):
            ProjectPaths(tmp_path).read_rules_digest()

    def test_无_config_toml_时同步清单为空而非报错(self, tmp_path):
        (tmp_path / ".devloop").mkdir()
        assert ProjectPaths(tmp_path).synced_paths() == []

    def test_读取需同步的构建缓存清单(self, tmp_path):
        """干净检出缺被 gitignore 的构建缓存 → 工具链失败 → 闸假失败。
        实测：eco-ob 缺 game/.godot 导致两次写任务被误判为工人失败。"""
        d = tmp_path / ".devloop"
        d.mkdir()
        (d / "config.toml").write_text(
            '[gates]\nsync_ignored_paths = ["game/.godot", "node_modules"]\n',
            encoding="utf-8")
        assert ProjectPaths(tmp_path).synced_paths() == ["game/.godot", "node_modules"]


# ── worktree.commit_result ─────────────────────────────────────
# 回归 G-26：此前完全不提交，成果只活在 worktree 未提交区，而派单收尾却打印
# 「分支保留供你审查」。L3 的产出因此在删 worktree 时真的丢失。这组测试锁住
# 「跑完闸后产出必须落到分支上」，以及「顺序不能反」。

def _repo(tmp_path):
    import subprocess
    p = tmp_path / "r"
    p.mkdir()
    (p / "a.txt").write_text("v1\n", encoding="utf-8")
    for c in (["init", "-q", "-b", "master", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *c], cwd=p, capture_output=True)
    return p


def test_commit_result_固化产出_删掉worktree也不丢(tmp_path):
    import subprocess
    from devloop import worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")

    sha = wt.commit_result("T", gate_ok=True)
    assert sha, "有改动就必须固化，不能返回 None"

    # 真的把工作目录删掉——这正是 L3 产出丢失时执行的那条命令
    subprocess.run(["git", "worktree", "remove", "--force", str(wt.path)],
                   cwd=proj, capture_output=True)
    # 关键断言：worktree 没了，产出仍能从分支上取回来
    out = subprocess.run(["git", "show", f"{wt.branch}:new.py"], cwd=proj,
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert out.strip() == "x = 1"


def test_commit_result_闸没过也固化_但提交信息写明(tmp_path):
    import subprocess
    from devloop import worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")

    assert wt.commit_result("T", gate_ok=False)
    msg = subprocess.run(["git", "log", "-1", "--format=%s%n%b", wt.branch], cwd=proj,
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert "闸未过" in msg, "红的必须在提交信息里写明，否则日后会被误当成绿的"


def test_commit_result_没改东西不造空提交(tmp_path):
    from devloop import worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    assert wt.commit_result("T", gate_ok=True) is None


def test_commit_result_不碰主线(tmp_path):
    import subprocess
    from devloop import worktree as wt_mod
    proj = _repo(tmp_path)
    before = subprocess.run(["git", "rev-parse", "master"], cwd=proj,
                            capture_output=True, text=True, encoding="utf-8").stdout
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)
    after = subprocess.run(["git", "rev-parse", "master"], cwd=proj,
                           capture_output=True, text=True, encoding="utf-8").stdout
    assert before == after, "隔离分支上的提交绝不能移动主线"


def test_commit_result_身份是机器不是用户(tmp_path):
    import subprocess
    from devloop import worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)
    who = subprocess.run(["git", "log", "-1", "--format=%an <%ae>", wt.branch], cwd=proj,
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert "devloop-worker" in who and "worker@devloop.local" in who


# ── 闸退出码归一化（G-36 的代码面病根）────────────────────────
# 此前直接透传 returncode：127 会落进 passed=False + gate_broken=False，
# 报出来是「质量不合格，但一道失败的闸都没有」——把「闸坏了」说成「活没干好」。
# 这正是 SPEC §5.2 明令必须区分的两件事。

def _gate_returning(tmp_path, code: int, stdout: str = ""):
    """造一个以指定退出码结束的闸，跑一遍，返回 GateResult。"""
    import textwrap
    from devloop import gates as g
    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s' "{stdout}"
        exit {code}
        """), encoding="utf-8")
    (proj / "src").mkdir()
    from devloop.config import ProjectPaths
    return g.run_gates(ProjectPaths(proj), target=proj)


def test_闸协议外退出码一律按闸自身故障处理(tmp_path):
    for code in (127, 3, 126, 137):
        r = _gate_returning(tmp_path / f"c{code}", code)
        assert r.gate_broken, f"退出码 {code} 必须判为闸自身故障，不能说成活没干好"
        assert not r.passed
        assert str(code) in r.stderr, "必须把原始退出码告诉人，否则无从排查"


def test_闸协议内退出码原样保留(tmp_path):
    r0 = _gate_returning(tmp_path / "a", 0, "PASS\tx\tok")
    assert r0.passed and not r0.gate_broken
    r1 = _gate_returning(tmp_path / "b", 1, "FAIL\tx\tbad")
    assert not r1.passed and not r1.gate_broken, "1 = 活没干好，不是闸坏了"
    r2 = _gate_returning(tmp_path / "c", 2)
    assert r2.gate_broken, "2 = 闸自身故障"


def test_协议外退出码仍保留已解析出的闸行(tmp_path):
    # 闸可能跑了几道才崩——那几道的结论不该丢，它们是排查线索
    r = _gate_returning(tmp_path / "d", 127, "PASS\t语法\tok")
    assert r.gate_broken
    assert any(l.name == "语法" for l in r.lines), "崩之前跑出来的结论要留着"


# ── 并发安全的时间戳（G-41）────────────────────────────────
# 2026-07-27 实测：4 个线程为**同名**任务建 worktree，秒级时间戳下只成 1 路，
# 其余报 `fatal: cannot lock ref 'refs/heads/devloop/same-<戳>'`；回执文件名则直接互相覆盖。
# ⚠️ 不同任务名并发本来就是安全的（实测 8/8）——撞的只有「重试同一单」这类场景。

def test_同秒生成的时间戳互不相同():
    from devloop import naming
    s = {naming.stamp() for _ in range(200)}
    assert len(s) == 200, "同进程内必须保证唯一，否则并行派单会撞分支名"


def test_时间戳保留可读的日期时间前缀():
    """后缀是为了唯一，不是为了混淆——排查时要能一眼看出这单什么时候派的。"""
    import re
    from devloop import naming
    assert re.match(r"^\d{8}-\d{6}-\d+$", naming.stamp())


def test_多线程并发生成的时间戳不重复():
    import threading
    from devloop import naming
    out, lock = [], threading.Lock()

    def go():
        v = [naming.stamp() for _ in range(50)]
        with lock:
            out.extend(v)

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(out)) == len(out) == 400


def test_同名任务并发建worktree不再撞名(tmp_path):
    """G-41 的红测：改之前 4 路只成 1 路。"""
    import subprocess
    import threading
    from devloop import worktree as wt_mod
    proj = _repo(tmp_path)
    got, err = [], []

    def go():
        try:
            got.append(wt_mod.create(proj, "same"))
        except Exception as e:  # noqa: BLE001 — 要把失败原因带出来才好排查
            err.append(str(e)[:120])

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(got) == 4, f"4 路应全部成功，实际 {len(got)} 路；失败原因：{err}"
    assert len({w.branch for w in got}) == 4, "四个分支名必须互不相同"


# ── 分支清理（G-27）──────────────────────────────────────────
# ⛔ 判定「能不能删」的唯一依据是**产出有没有别处留存**，不是分支多老。
#    一个从未合并、产出也没归档的分支，删了就是第二次 G-26（L3 产出真实丢失）。

def test_有未合并产出的分支绝不能被判成可删(tmp_path):
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)          # 产出固化，但没合回主线

    bs = prune.scan(proj)
    assert len(bs) == 1
    assert not bs[0].safe_to_delete, "有未合并产出的分支被判成可删 = 会丢东西"
    assert not bs[0].empty
    txt, n = prune.report(proj)
    assert n == 0 and "别删" in txt


def test_已合并的分支可以清理(tmp_path):
    import subprocess
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)
    subprocess.run(["git", "merge", "--no-ff", "-m", "merge", wt.branch],
                   cwd=proj, capture_output=True)

    bs = prune.scan(proj)
    assert bs[0].safe_to_delete and bs[0].merged


def test_没有产出的分支可以清理(tmp_path):
    """工人一个字没改 → commit_result 不造空提交 → 分支尖端 == 基点。"""
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    assert wt.commit_result("T", gate_ok=True) is None
    bs = prune.scan(proj)
    assert bs[0].empty and bs[0].safe_to_delete


def test_prune不执行任何删除(tmp_path):
    """⛔ 本命令的全部价值在于「只列不删」。它若删了东西，就是在替人做决定。"""
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)
    before = {b.name for b in prune.scan(proj)}
    prune.report(proj)
    prune.report(proj)                            # 跑两次也不该有副作用
    assert {b.name for b in prune.scan(proj)} == before


def test_被worktree检出的分支前缀是加号不是星号(tmp_path):
    """回归：`git branch --merged` 对被别的 worktree 检出的分支用 `+ ` 前缀。

    第一版只剥了 `* `，于是每个隔离分支（它们全被 worktree 检出）都匹配不上，
    `merged` 恒为空——已合并的分支被判成「别删」。方向安全，但命令基本没用。
    """
    import subprocess
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)
    subprocess.run(["git", "merge", "--no-ff", "-m", "m", wt.branch],
                   cwd=proj, capture_output=True)
    raw = subprocess.run(["git", "-C", str(proj), "branch", "--merged"],
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert "+ " in raw, "前提变了：git 不再用 + 标记被 worktree 检出的分支"
    assert prune.scan(proj)[0].merged, "带 + 前缀的分支没被认出来"


def test_已合并的分支原因要说对_不能说成没产出(tmp_path):
    """两种情况都可删，但**原因**是人决定删不删的依据，说反了就等于没说。"""
    import subprocess
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)
    wt = wt_mod.create(proj, "T")
    (wt.path / "new.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit_result("T", gate_ok=True)
    subprocess.run(["git", "merge", "--no-ff", "-m", "m", wt.branch],
                   cwd=proj, capture_output=True)
    b = prune.scan(proj)[0]
    assert b.merged and not b.empty, "有工人提交却被说成「没产出」"
    assert "已合并" in b.reason


# ── 非阻塞派单（Phase 6）────────────────────────────────────
# 触发条件被实测远超：Phase 4 首批干等 74 分钟、第二批 50 分钟、评测集两次各 30 分钟，
# 且第二批那次 10 分钟超时被切、u17 那单丢失要重跑。
# ⚠️ 这套东西最危险的失效模式是「还在跑」被当成「跑完了」——测试重点盯这条。

def _job(tmp_path, units, rows=()):
    """造一个作业记录 + 一本台账，不真起进程。"""
    import json
    from devloop import jobs
    proj = tmp_path / "p"
    (proj / ".devloop" / "jobs" / "J1").mkdir(parents=True)
    tel = proj / ".devloop" / "telemetry.jsonl"
    tel.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                   encoding="utf-8")
    meta = {"job_id": "J1", "pid": 999999, "started": "2026-01-01T00:00:00",
            "project": str(proj), "telemetry": str(tel), "argv": [], "units": units}
    (proj / ".devloop" / "jobs" / "J1" / "job.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return jobs.load(proj, "J1")


def test_全部跑完且都通过_状态为done(tmp_path):
    j = _job(tmp_path, ["a", "b"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": True},
        {"ts": "2026-01-02T00:01:00", "task": "b", "ok": True}])
    st, msg = j.status()
    assert st == "done" and "全部通过" in msg


def test_跑完但有失败_状态仍是done但要说出来(tmp_path):
    """⚠️ 「跑完了」和「都成功了」是两件事——混起来就是又一次假绿。"""
    j = _job(tmp_path, ["a", "b"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": True},
        {"ts": "2026-01-02T00:01:00", "task": "b", "ok": False}])
    st, msg = j.status()
    assert st == "done" and "1 单未通过" in msg


def test_进程没了但活没干完_必须明确报出来(tmp_path):
    """⛔ 不许含糊成「可能还在跑」——那会让人一直等一个已经死掉的作业。"""
    j = _job(tmp_path, ["a", "b", "c"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    st, msg = j.status()
    assert st == "died"
    assert "b" in msg and "c" in msg, "必须点名哪几单没完成"


def test_只认作业开始之后的台账行(tmp_path):
    """同一任务名可能被派过多次。拿旧行冒充本次进度 = 进度虚高。"""
    j = _job(tmp_path, ["a"], [
        {"ts": "2020-01-01T00:00:00", "task": "a", "ok": True},   # 上一次的
    ])
    assert j.done_units() == {}, "作业开始之前的台账行不该算进本次进度"


def test_进度读台账而不是输出文件(tmp_path):
    """G-37 的教训：曾用「输出文件 0 字节 + ps 没匹配到」断定任务没跑，
    实际三次全跑了，多花两单。台账是唯一直接的证据。"""
    j = _job(tmp_path, ["a"], [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    assert not j.log.exists(), "本用例故意不造 console.log"
    assert j.status()[0] == "done", "没有输出文件也该能读出进度"


def test_detach遇上subagent必须明说被忽略(capsys, tmp_path, monkeypatch):
    """⛔ **静默吞掉用户明确给出的参数**是本项目最忌讳的那类行为——
    用户以为「派出去就走」，实际站在原地等一个根本不会自己跑的批次。"""
    import json
    from devloop import backends, cli

    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    (proj / ".devloop" / "rules-digest.md").write_text("# 规则\n", encoding="utf-8")
    task = tmp_path / "t.md"
    task.write_text("# 角色\nx\n\n# 任务\ny\n\n# 禁令\nz\n", encoding="utf-8")

    reg = backends.Registry(
        {"sa": backends.Backend(name="sa", kind="subagent", model="m", source="test")},
        {}, "sa", "test")
    monkeypatch.setattr(backends, "load", lambda: reg)

    rc = cli.main(["dispatch", "--project", str(proj), "--task", str(task),
                   "--backend", "sa", "--detach"])
    out = capsys.readouterr().out
    assert rc == 3, "subagent 出完工单应返回 3（批次就绪，不是成功）"
    assert "--detach" in out and "忽略" in out, "被忽略的参数必须明说"


# ── 急停（Phase 6 补）─────────────────────────────────────────
# Phase 6 之前，活跑在前台，Ctrl-C 就停了。有了 --detach 之后作业脱离父进程活着，
# **没有急停就没有任何办法叫停它**。SPEC 把它标成「Phase 7 前置」，位置对，
# 但它已经从「将来要做」变成「现在就缺」。

def test_急停默认只列不杀(tmp_path, monkeypatch):
    """⛔ 与 prune 同源：杀进程不可逆，而跑着的作业里可能有已花钱、快产出的单。"""
    from devloop import halt, jobs
    j = _job(tmp_path, ["a", "b"], [{"ts": "2026-01-02T00:00:00", "task": "a", "ok": True}])
    monkeypatch.setattr(jobs.Job, "pid_alive", lambda self: True)
    txt, alive = halt.report(j.root.parent.parent.parent, do_kill=False)
    assert alive == 1
    assert "只列不杀" in txt and "--kill" in txt


def test_急停必须说清已花的钱不会退(tmp_path, monkeypatch):
    """不说清楚，人会以为「停了 = 没花钱」。台账里跑完的单是真金白银。"""
    from devloop import halt, jobs
    j = _job(tmp_path, ["a", "b"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": True, "cost_usd_real": 0.05}])
    monkeypatch.setattr(jobs.Job, "pid_alive", lambda self: True)
    monkeypatch.setattr(halt, "kill", lambda job: (True, "已终止"))
    txt, _ = halt.report(j.root.parent.parent.parent, do_kill=True)
    assert "不会退" in txt
    assert "$0.0500" in txt, "必须报出已经花掉多少"


def test_急停要报出成本未知的单数(tmp_path, monkeypatch):
    """算不出成本的单（子代理交接常见）不能被静默当成 0。"""
    from devloop import halt, jobs
    j = _job(tmp_path, ["a", "b"], [
        {"ts": "2026-01-02T00:00:00", "task": "a", "ok": True, "cost_usd_real": None}])
    monkeypatch.setattr(jobs.Job, "pid_alive", lambda self: True)
    txt, _ = halt.report(j.root.parent.parent.parent, do_kill=False)
    assert "成本未知" in txt


def test_没有活作业时急停不报错(tmp_path):
    from devloop import halt
    proj = tmp_path / "empty"
    proj.mkdir()
    txt, alive = halt.report(proj, do_kill=False)
    assert alive == 0 and "没有正在跑的作业" in txt


# ── 便宜路线停用（2026-07-28 用户裁决）─────────────────────────
# 依据是实测，不是感觉：窄口径便宜路线赢 48 倍，但把拆单成本算进来**贵 5%**，
# 而质量是 27% vs 84%（矛盾覆盖率）。⛔ 又花钱又差，所以不再做默认。
# ⚠️ **框架全留**——注册表、派单、隔离、闸、台账一行不动，
#    随时可以 `--backend deepseek` 重新开测。停的是「默认」，不是「能力」。

@pytest.fixture
def published_backend_registry(monkeypatch):
    """验证公开示例的政策，不读取测试者的私有配置或凭据。"""
    from devloop import backends
    sample = Path(__file__).parent / "fixtures" / "backends.example.json"
    assert sample.is_file(), "公开配置示例缺失，不能回退到本机配置"
    monkeypatch.setattr(backends, "REGISTRY_FILE", sample)
    reg = backends.load()  # 真实解析器，不替换成手造 Registry 对象
    assert any(b.kind == "api" for b in reg.backends.values()), "示例必须覆盖收费路线"
    return reg


def test_花钱的后端必须带一句说明(published_backend_registry):
    """⛔ 用 api 后端 = 真花钱。注册表里必须有一句 note 说清代价，
    否则下一个人（或半年后的自己）会以为它是免费的默认选项。"""
    reg = published_backend_registry
    for name, b in reg.backends.items():
        if b.kind == "api":
            assert b.note, f"后端 {name} 是花钱的 api 路线，却没有 note"


def test_默认后端不许是花钱的路线(published_backend_registry):
    """⛔ 2026-07-28 裁决：默认不许直接花钱。
    要花钱必须**显式**写 `--backend <名字>`——让每一次花钱都是一次有意识的选择。"""
    reg = published_backend_registry
    assert reg.resolve(None).kind != "api", (
        f"默认后端是 {reg.default}（api = 花钱）。"
        f"默认应当是不直接产生费用的那条路。")


def test_示例配置与迁移产物的默认也不许是花钱路线(tmp_path, monkeypatch):
    """⚠️ 代码里内置的示例/迁移默认值也要跟着改——
    否则新机器上装一遍，默认又回到花钱那条路。"""
    import inspect
    from devloop import backends
    src = inspect.getsource(backends)
    # 迁移产物与错误提示里的示例配置
    assert '"default": "deepseek"' not in src, (
        "backends.py 里还有把 deepseek 当默认的示例/迁移配置")


def test_分支空不空的判据不许依赖git身份(tmp_path):
    """⛔ **2026-07-29 审计抓到的连带损坏。**

    `prune` 原来用 `git log --author=worker@devloop.local` 判分支空不空。
    而 worktree 与主仓库共用 `.git/config`，工人身份被写进了主仓库，
    于是**主线尖端也带着工人邮箱**——今后从主线开的每一个隔离分支，
    往回查都能查到一条「工人提交」，全部被判成「有未合并产出，别删」。

    ⚠️ 根源已修（worktree 改用 `-c` 每命令注入），但**历史改不回来**，
    所以判据本身必须换成不依赖身份的。

    这条模拟被污染的历史：主线尖端挂着工人邮箱，从它开一个**什么都没干**的
    分支，判据必须仍然说「空」。
    """
    import subprocess
    from devloop import prune, worktree as wt_mod
    proj = _repo(tmp_path)

    # 模拟污染：主线尖端是一条挂着工人邮箱的**人写的**提交
    (proj / "human.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=proj, capture_output=True)
    subprocess.run(["git", "-c", "user.email=worker@devloop.local",
                    "-c", "user.name=devloop-worker[污染]",
                    "commit", "-q", "-m", "chore: 人写的，但挂着工人身份"],
                   cwd=proj, capture_output=True)

    wt = wt_mod.create(proj, "空单")          # 建了但工人什么都没改
    b = next(x for x in prune.scan(proj) if x.name == wt.branch)
    assert b.empty, (
        "⛔ 分支上没有任何产出，却被判成有——判据被污染的历史带偏了。"
        f" 尖端 {b.sha}")
    assert b.safe_to_delete
