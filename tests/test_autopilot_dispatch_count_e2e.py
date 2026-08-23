"""⭐ 一次真跑到底派了几次——**判据落在这个数上，不落在源码形状上**。

## ⛔ 为什么必须有这一条

`test_autopilot_wave_cap.py` 的 docstring 里写着：

> 要验它得跑真单，那**花额度**，不能拿来做测试。于是判据只能退化成
> 「读源码里有没有 `ThreadPoolExecutor`」，那是**代理指标**。

⛔ **那个前提是错的。** 2026-08-02 的对抗复核当场证伪：把 `cli._run_unit`
换成一个只写台账的桩，就能零额度真跑整个 `cmd_autopilot` 循环并数出实际次数。

它同时证明了那批代理判据**可以被绕过**——三种「形似而神不似」的实现
都能让 11 条测试全绿、而缺陷 100% 复活：

| 绕法 | 为什么绕得过 |
|---|---|
| `remaining=b.max_dispatches`（删掉减号后半截） | AST 判据只查 `"max_dispatches" in dump(...)` |
| 调了 `plan_wave`、参数全对，**把结果丢掉**再自己切一次 | AST 判据只查「有这次调用」 |
| `n = ... if remaining < 3 else max(1, parallel)` | 纯函数测试只探了 remaining ∈ {0,1,2,99} |

⚠️ 还有一条更难堪的：`test_循环体里不许自己再切一次波` 断言
`"sp.parallel]" not in src`，而真实历史里那行写的是 `[:max(1, sp.parallel)]`
——源码里是 `sp.parallel)]`。⛔ **它是一条为从未存在过的字符串写的守卫。**

## ⭐ 这里量的是什么

`fired`（编排方自己数的）与台账行数（真写下的），两个都数，两个都断言。
⚠️ 提交信息通篇在讲「max=10 / parallel=4 实际派 12 次」，而在这条测试之前
**全仓没有任何一条测试跑出过 12 或 10 这个数**。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devloop import cli

PLAN = """\
[plan]
version = 1
[stage]
id = 'e2e'
goal = '数派单次数'
task_dir = '.devloop/tasks'
base = 'HEAD'
parallel = {parallel}
[stage.budget]
total_usd = 100.0
reserve_usd = 0.1
max_dispatches = {maxd}
max_wall_min = 60
watchdog_k = 99
default_max_turns = 5
[stage.accept]
require_pass = ['g1']
"""

TASK = "# 角色\n\nx\n\n# 任务\n\ny\n\n# 改动范围\n\n- src/{tid}.py\n\n# 禁令\n\n- z\n"


def _project(tmp_path, *, maxd: int, parallel: int, n_tasks: int = 40):
    proj = tmp_path / "p"
    (proj / ".devloop" / "tasks").mkdir(parents=True)
    (proj / ".devloop" / "plans").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'PASS\\tg1\\tok\\n'\nexit 0\n", encoding="utf-8")
    #  ⛔ 必须给真宪法——`cmd_autopilot` 没宪法会拒跑，⭐ 那条纪律是对的，
    #     不能为了让测试跑起来把它绕掉（绕掉就等于测了一条生产上不存在的路径）。
    #  ⚠️ 树内条款也必须有——`constitution` 会拒绝「树内判据覆盖 0 条路径」的配置，
    #     它自己的说法是「与『闸一条都没验却报全过』是同一种假绿」。⭐ 那条也对。
    (proj / ".devloop" / "constitution.toml").write_text(
        'schema = 1\n\n[[protected_file]]\nclause = "A-1"\n'
        'title = "不得修改闸"\npath = ".devloop/gates.sh"\n\n'
        '[[protected_tree]]\nclause = "C-2"\n'
        'title = "不得改基线"\nglob = "baseline/**"\n\n'
        #  ⚠️ 还必须登记「判不了的条款」——宪法拒绝一条都不登记的配置，
        #     ⭐ 理由是「宪法里必然有判不了的条款，漏写等于假装全覆盖」。
        '[[unjudged]]\nclause = "X-1"\ntitle = "对外发送"\n'
        'why = "机器判不了：这是设计决策，不是文件改动"\n', encoding="utf-8")
    (proj / ".devloop" / "telemetry.jsonl").write_text("", encoding="utf-8")
    #  ⛔ 受保护的 glob 必须真能匹配到**被 git 跟踪的**文件——宪法会拒绝
    #     匹配 0 个的模式，理由是「写错的模式会永远报『无命中』」。⭐ 那条也对，不绕。
    #  ⚠️ 2026-08-03：判据从「盘上有没有」改成「git 认不认得」之后，这个夹具
    #     **当场转红**——它建了 `baseline/ref.json` 却从没 `git add`，
    #     ⛔ 于是那条守卫一直是**空的**（`check_tree` 从 `ls-tree base` 取清单，
    #     没进版本库的文件一个都看不见）。⭐ 新判据抓到的第一个真目标是我们自己。
    (proj / "baseline").mkdir()
    (proj / "baseline" / "ref.json").write_text("{}", encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "夹具基线"]):
        subprocess.run(["git", *a], cwd=proj, check=True, capture_output=True)
    body = PLAN.format(parallel=parallel, maxd=maxd)
    for i in range(n_tasks):
        tid = f"t{i:02d}"
        (proj / ".devloop" / "tasks" / f"{tid}.md").write_text(
            TASK.format(tid=tid), encoding="utf-8")
        #  ⚠️ 每单都点名同一道闸，且互不依赖——这样「就绪的单」永远够多，
        #     ⛔ 测出来的上限才是配额造成的，不是任务不够造成的。
        body += (f"\n[[task]]\nid = '{tid}'\ntools = 'implement'\n"
                 f"  [task.accept]\n  require_pass = ['g1']\n")
    (proj / ".devloop" / "plans" / "e2e.toml").write_text(body, encoding="utf-8")
    #  ⛔ 锚定。⚠️ 在**一次性的 tmp 测试项目**里调它是夹具搭建——
    #     与「重新锚定真实项目的宪法必须由人亲自执行」是两回事，别混。
    from devloop import constitution as _con
    from devloop.config import ProjectPaths as _PP
    _pp = _PP(proj)
    _con.write_anchor(_pp, _con.load(_pp))
    return proj


def _run(monkeypatch, proj, *, count: list) -> int:
    """跑 `cmd_autopilot`，把 `_run_unit` 换成**只写一行台账**的桩。

    ⛔ 零额度：不起任何子进程、不连任何后端、不建 worktree。
    ⚠️ 桩必须真写台账——`read_progress` 每轮都从台账重算，不写的话
    「跨轮次的进度」这条链根本没被验到。
    """
    from devloop.config import ProjectPaths

    paths = ProjectPaths(proj)

    import threading

    from devloop import telemetry as _tele

    lock = threading.Lock()

    def fake(spec, p, cfg, **kw):
        #  ⛔ **必须走 `telemetry._append`**，⚠️ 不许自己 `open("a")`。
        #     第一版就是自己开的文件，4 路并发直接交错——这条测试因此偶发变红
        #     （实测：全量跑一次红、单跑三次全绿）。⭐ 而「并发写没加锁会丢行」
        #     正是本项目文档里记着的东西，`_append` 的存在就是为了这个。
        #  ⚠️ 偶发测试本身就是缺陷：它把「判据」变成了「概率」。
        with lock:
            count.append(spec.name)
            n = len(count)
        _tele._append(paths.telemetry, json.dumps({
            "ts": "2026-08-02T%02d:00:00" % min(23, n),
            "task": spec.name, "ok": True, "gate_ok": True,
            "cost_usd_real": 0.0, "duration_s": 1, "turns": 1,
        }, ensure_ascii=False) + "\n")
        return True, [f"  ✓ {spec.name}"]

    monkeypatch.setattr(cli, "_run_unit", fake)
    monkeypatch.setattr(cli.wt_mod, "resolve_base", lambda p: "0" * 40)
    monkeypatch.setattr(cli.backends, "load", lambda: _FakeReg())
    return cli.main(["autopilot", "--project", str(proj), "--stage", "e2e"])


class _FakeCfg:
    model = "__subscription__"
    backend = "fake"


class _FakeBackend:
    def worker_config(self):
        return _FakeCfg()


class _FakeReg:
    def resolve(self, *a, **k):
        return _FakeBackend()


# ── ⭐ 主判据：实际派出的次数 ──────────────────────────────────────

@pytest.mark.parametrize("maxd,parallel", [
    (10, 4),   # ⭐ 提交信息里那一组：改前实际派 12 次
    (10, 1),   # 串行基线
    (10, 3),
    (9, 4),    # ⛔ 最坏：溢出 = 并发数 − 1
    (7, 4),
    (5, 5),
    (1, 4),    # ⛔ 相对倍数最坏：改前派 4 次，上限 1
    (11, 4),
    (2, 8),
])
def test_实际派单次数不许超过上限(tmp_path, monkeypatch, maxd, parallel) -> None:
    """⛔ 量的是**真跑出来的次数**，不是源码形状。"""
    proj = _project(tmp_path, maxd=maxd, parallel=parallel)
    fired: list[str] = []
    _run(monkeypatch, proj, count=fired)
    assert len(fired) <= maxd, (
        f"⛔ max_dispatches={maxd} · parallel={parallel} 实际派了 {len(fired)} 次"
        f"（溢出 {len(fired) - maxd}）")


@pytest.mark.parametrize("maxd,parallel", [(10, 4), (9, 4), (7, 4), (2, 8)])
def test_额度也要用满不许一刀切成串行(tmp_path, monkeypatch, maxd, parallel) -> None:
    """⚠️ 只断言上界会被 `wave = ready[:1]` 这种作弊实现骗过
    ——那是「修成了永远只派一单」。⛔ 上下界都要钉。"""
    proj = _project(tmp_path, maxd=maxd, parallel=parallel)
    fired: list[str] = []
    _run(monkeypatch, proj, count=fired)
    assert len(fired) == maxd, \
        f"⛔ 任务足够多却只派了 {len(fired)}/{maxd} 次——额度没用满"


def test_台账行数与编排方自己数的一致(tmp_path, monkeypatch) -> None:
    """⛔ 两个来源必须对得上。⚠️ 对不上说明「派了几次」这件事有两个真相，
    而失控防线读的是台账那个。"""
    from devloop.config import ProjectPaths

    proj = _project(tmp_path, maxd=10, parallel=4)
    fired: list[str] = []
    _run(monkeypatch, proj, count=fired)
    lines = [l for l in
             ProjectPaths(proj).telemetry.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    assert len(lines) == len(fired) == 10


def test_并行确实在并行不是假的(tmp_path, monkeypatch) -> None:
    """⚠️ 反向钉住：上面几条若被「永远串行」的实现满足，这条要红。
    ⭐ 判据是**轮数**——10 次派单 parallel=4 应该 3 轮跑完，串行要 10 轮。"""
    import re

    proj = _project(tmp_path, maxd=10, parallel=4)
    fired: list[str] = []
    _run(monkeypatch, proj, count=fired)
    state = json.loads((proj / ".devloop" / "autopilot" / "e2e.json")
                       .read_text(encoding="utf-8")) \
        if (proj / ".devloop" / "autopilot" / "e2e.json").exists() else None
    assert state is not None, "⛔ 没落盘运行记录"
    assert len(fired) == 10
    #  ⚠️ 轮数从「一波几单」反推：并行时 10 单最多 3 波，串行要 10 波。
    assert re.match(r"^t\d\d$", fired[0])
