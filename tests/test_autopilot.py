"""阶段计划与自动驾驶的红测。

⚠️ 这套东西最危险的失效不是「跑错了」，是**跑飞了没人知道**。
所以测试重点全在**停下来的理由**上，不在「能不能跑起来」上。
"""

from __future__ import annotations

import json
import time

import pytest

from devloop import autopilot as A
from devloop import plan as P
from devloop.config import ConfigError, ProjectPaths

BASE = """\
[plan]
version = 1

[stage]
id       = 's1'
goal     = '把散落的临时产物挡进 .gitignore'
task_dir = '.devloop/tasks'
base     = 'deadbeef'

[stage.budget]
total_usd      = 0.20
reserve_usd    = 0.05
max_dispatches = 8
max_wall_min   = 45

[stage.accept]
require_pass = ['基线守卫']

[[task]]
id = 'a'
tools = 'implement'
  [task.accept]
  require_pass = ['基线守卫']

[[task]]
id    = 'b'
tools = 'implement'
needs = ['a']
  [task.accept]
  require_pass = ['基线守卫']
"""
# ⚠️ 上面那两行 `tools = 'implement'` 是 2026-07-28 补的，值得记一笔：
#    这份夹具原本两单都省略 tools（默认 readonly）却写了 require_pass ——
#    **正是「点名了永不执行的闸」那个 CRITICAL 的形状**。
#    于是本文件 24 条测试没有一条能抓到它：**夹具本身就是那个 bug**。
#    这是「自检存在、判据正确，但测的是一份坏样本」的活标本。


def _plan(tmp_path, text=BASE, tasks=("a", "b")):
    proj = tmp_path / "proj"
    (proj / ".devloop" / "tasks").mkdir(parents=True)
    (proj / ".devloop" / "plans").mkdir(parents=True)
    for t in tasks:
        (proj / ".devloop" / "tasks" / f"{t}.md").write_text(
            "# 角色\nx\n\n# 任务\ny\n\n# 禁令\nz\n", encoding="utf-8")
    f = proj / ".devloop" / "plans" / "s1.toml"
    f.write_text(text, encoding="utf-8")
    return proj, f


# ══ 计划的校验：全部要在花第一分钱之前 ═══════════════════════════

def test_正常计划能读出来(tmp_path):
    _, f = _plan(tmp_path)
    sp = P.load(f)
    assert sp.id == "s1" and len(sp.tasks) == 2
    assert sp.by_id("b").needs == ("a",)


def test_依赖成环必须拒跑(tmp_path):
    bad = BASE.replace("id = 'a'\ntools = 'implement'\n",
                       "id = 'a'\ntools = 'implement'\nneeds = ['b']\n")
    _, f = _plan(tmp_path, bad)
    with pytest.raises(ConfigError) as e:
        P.load(f)
    assert "环" in str(e.value) and "a" in str(e.value)


def test_悬空依赖必须拒跑(tmp_path):
    """⚠️ 悬空依赖会让那几单**永远轮不到**，而循环只会静静地什么都不干。"""
    _, f = _plan(tmp_path, BASE.replace("needs = ['a']", "needs = ['压根没有这单']"))
    with pytest.raises(ConfigError) as e:
        P.load(f)
    assert "压根没有这单" in str(e.value)


def test_任务书缺失必须在花钱前查出来(tmp_path):
    _, f = _plan(tmp_path, tasks=("a",))     # b.md 没造
    with pytest.raises(ConfigError) as e:
        P.load(f)
    assert "b" in str(e.value)


def test_重复任务id必须拒跑(tmp_path):
    _, f = _plan(tmp_path, BASE + """
[[task]]
id = 'a'
  [task.accept]
  require_pass = ['基线守卫']
""")
    with pytest.raises(ConfigError) as e:
        P.load(f)
    assert "重复" in str(e.value)


def test_预算四个上限一个都不能省(tmp_path):
    """⛔ 无人值守时它们是唯一挡在「一夜烧光」前面的东西。"""
    for k in ("total_usd", "reserve_usd", "max_dispatches", "max_wall_min"):
        text = "\n".join(l for l in BASE.splitlines() if not l.startswith(k))
        _, f = _plan(tmp_path / k, text)
        with pytest.raises(ConfigError) as e:
            P.load(f)
        assert k in str(e.value)


def test_没有机器判据的单必须显式写明理由(tmp_path):
    """⛔ 不强制这一条的话，「忘了写验收标准」和「这单确实没法机检」
    在文件里长得**一模一样**——而前者是漏洞，后者是已知代价。"""
    _, f = _plan(tmp_path, BASE.replace("  require_pass = ['基线守卫']\n\n[[task]]\nid    = 'b'",
                                        "\n[[task]]\nid    = 'b'", 1))
    with pytest.raises(ConfigError) as e:
        P.load(f)
    assert "why" in str(e.value)


def test_写明了理由就放行(tmp_path):
    """⚠️ 防回归：确实有些单没法机检（报告是自然语言表格），
    那是已知代价，不是漏洞——但必须写下来。"""
    text = BASE.replace(
        "  require_pass = ['基线守卫']\n\n[[task]]",
        "  why = '本单产出是一张 markdown 表，没有可机检的结构'\n\n[[task]]", 1)
    _, f = _plan(tmp_path, text)
    sp = P.load(f)
    assert sp.by_id("a").require_pass == ()
    assert "markdown" in sp.by_id("a").accept_none_why


def test_版本号不认识就拒跑(tmp_path):
    _, f = _plan(tmp_path, BASE.replace("version = 1", "version = 99"))
    with pytest.raises(ConfigError) as e:
        P.load(f)
    assert "99" in str(e.value)


# ══ 依赖调度 ═══════════════════════════════════════════════════

def test_依赖没满足的单轮不到(tmp_path):
    _, f = _plan(tmp_path)
    sp = P.load(f)
    assert [t.id for t in sp.ready(set())] == ["a"]
    assert [t.id for t in sp.ready({"a"})] == ["b"]


def test_失败的单不解锁下游(tmp_path):
    """⚠️ 依赖一个**失败**的产物继续往下干，只会把错误铺开。

    ⚠️ 2026-07-28：`attempts` 必须一起给。`done_bad` 说的是「结果如何」，
    `attempts` 说的是「花了几次钱」——**两件事**，而 `retries` 看的是后者。
    只给前者是一个真实路径里不会出现的状态（`read_progress` 两个都填）。
    """
    _, f = _plan(tmp_path)
    sp = P.load(f)
    # retries 默认 1，试过 1 次 = 用完了
    prog = A.Progress(done_ok=set(), done_bad={"a"}, attempts={"a": 1})
    ready, stop = A.plan_next(sp, prog)
    assert not ready, "a 用完重试、b 依赖它——两个都不该轮到"
    assert stop.kind == "escalation" and "a" in stop.detail
    # ⭐ 本条真正要守的：**b 绝不能因为 a 跑过就被解锁**
    assert "b" not in [t.id for t in sp.ready(prog.done_ok, attempted={"a"})]


# ══ ⛔ 失控防线 ═════════════════════════════════════════════════

def _sp(tmp_path):
    _, f = _plan(tmp_path)
    return P.load(f)


def test_派单次数上限不依赖任何成本计算(tmp_path):
    """⭐ 这条是兜底。价目表里没有某个模型时成本是 None——
    若把 None 当 0，预算永远不会耗尽。**次数上限不看成本**。"""
    sp = _sp(tmp_path)
    prog = A.Progress(dispatches=8, spent=0.0)
    s = A.check_limits(sp, prog, started=time.time(), dry_rounds=0)
    assert s and s.kind == "dispatches"


def test_算不出成本必须停而不是当成零元(tmp_path):
    """⛔ 「算不出成本」≠「花了 0 元」。不拦住的话预算永远不会耗尽。"""
    sp = _sp(tmp_path)
    prog = A.Progress(dispatches=1, spent=0.0, unknown_cost=1)
    s = A.check_limits(sp, prog, started=time.time(), dry_rounds=0)
    assert s and s.kind == "budget" and "null" in s.detail


def test_预算要留出余量再派(tmp_path):
    """⚠️ 派完才发现超了，钱已经花了。所以判据是「已花 + 预留 > 上限」。"""
    sp = _sp(tmp_path)
    assert A.check_limits(sp, A.Progress(dispatches=1, spent=0.10),
                          started=time.time(), dry_rounds=0) is None
    s = A.check_limits(sp, A.Progress(dispatches=1, spent=0.16),
                       started=time.time(), dry_rounds=0)
    assert s and s.kind == "budget"


def test_墙钟到顶也要停哪怕钱没花完(tmp_path):
    sp = _sp(tmp_path)
    s = A.check_limits(sp, A.Progress(dispatches=1),
                       started=time.time() - 46 * 60, dry_rounds=0)
    assert s and s.kind == "wall"


def test_空转看门狗要能停下来(tmp_path):
    sp = _sp(tmp_path)
    s = A.check_limits(sp, A.Progress(dispatches=1),
                       started=time.time(), dry_rounds=3)
    assert s and s.kind == "watchdog"
    assert "不是标定出来的" in s.detail, "⛔ K 没有数据支撑这件事必须写在结论里"


def test_一切正常时不拦(tmp_path):
    """⚠️ 防回归：别把「更严」修成「什么都不让干」。"""
    sp = _sp(tmp_path)
    assert A.check_limits(sp, A.Progress(dispatches=1, spent=0.01),
                          started=time.time(), dry_rounds=0) is None


def test_除了跑完之外的停都要找人(tmp_path):
    """⛔ 包括「不知道为什么停的」。"""
    assert not A.Stop("跑完了", "done").needs_human
    for k in ("budget", "dispatches", "wall", "watchdog", "constitution", "blocked"):
        assert A.Stop("x", k).needs_human


# ══ 进度只从台账重算 ═══════════════════════════════════════════

def _tel(proj, rows):
    f = proj / ".devloop" / "telemetry.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")


def test_进度从台账重算而不是从自己的记录(tmp_path):
    """⛔ 与 jobs.py 同一条纪律：自己的记录只登记「派了什么」，
    不登记「结果如何」。**分叉的账本比没有账本更坏——它看起来是权威的。**"""
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [{"ts": "2026-02-01T00:00:00", "task": "a", "ok": True,
                 "gate_ok": True, "cost_usd_real": 0.01}])
    prog = A.read_progress(ProjectPaths(proj), sp, "2026-01-01T00:00:00")
    assert prog.done_ok == {"a"} and prog.spent == 0.01


def test_只认本阶段开始之后的台账行(tmp_path):
    """上一次跑的结果被算成本次进度 = 进度虚高（jobs.py 踩过同款）。"""
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [{"ts": "2020-01-01T00:00:00", "task": "a", "ok": True,
                 "gate_ok": True, "cost_usd_real": 0.01}])
    prog = A.read_progress(ProjectPaths(proj), sp, "2026-01-01T00:00:00")
    assert prog.done_ok == set()


def test_同一单重试多次只算一单(tmp_path):
    """⛔ 按行数算会把「跑完 N 单」顶成真（G-51 同款）。"""
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [
        {"ts": "2026-02-01T00:00:00", "task": "a", "ok": False, "gate_ok": False,
         "cost_usd_real": 0.01},
        {"ts": "2026-02-01T00:05:00", "task": "a", "ok": True, "gate_ok": True,
         "cost_usd_real": 0.02}])
    prog = A.read_progress(ProjectPaths(proj), sp, "2026-01-01T00:00:00")
    assert prog.done_ok == {"a"} and prog.done_bad == set()
    assert prog.dispatches == 2, "派单次数要算全部次数——那是烧钱次数"


def test_闸没过的单算失败不算完成(tmp_path):
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [{"ts": "2026-02-01T00:00:00", "task": "a", "ok": True,
                 "gate_ok": False, "cost_usd_real": 0.01}])
    prog = A.read_progress(ProjectPaths(proj), sp, "2026-01-01T00:00:00")
    assert prog.done_bad == {"a"} and prog.done_ok == set()


def test_算不出成本的单要被数出来(tmp_path):
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [{"ts": "2026-02-01T00:00:00", "task": "a", "ok": True,
                 "gate_ok": True, "cost_usd_real": None}])
    prog = A.read_progress(ProjectPaths(proj), sp, "2026-01-01T00:00:00")
    assert prog.unknown_cost == 1 and prog.spent == 0.0


# ══ 无人值守的前置 ═════════════════════════════════════════════

def test_没有宪法时自动驾驶必须拒绝开跑(tmp_path):
    """⛔ 前台派单可以没有宪法（你在看着），无人值守不行——
    没有宪法就没有任何东西挡着它改自己的规矩。"""
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    with pytest.raises(ConfigError) as e:
        A.preflight(ProjectPaths(proj), sp)
    assert "拒绝开跑" in str(e.value)


# ══ 只读单点名闸 = 空守卫 ══════════════════════════════════════

def test_只读单点名闸必须拒跑(tmp_path):
    """⛔ 第三轮审查抓到的 CRITICAL。链条是：

        tools 默认 readonly → 不建 worktree、**不跑闸**
        → 闸结果为空（gate_ok=None）
        → 而判「绿不绿」的那行写的是 `不等于失败`
        → **空 ≠ 失败，所以算绿**
        → 阶段报「全绿」，退出码 0

    也就是说：计划里点名的闸**一道都不会执行**，而工具还会在 `--dry-run` 里
    把它们念给你听——**主动向人确认一个永不生效的守卫**。

    ⚠️ 最难看的是：**本文件的 BASE 夹具原本就是这个坏形状**
    （两单没写 tools、却写了 require_pass），所以 24 条测试没有一条能抓到它。

    ⚠️ 修法是在**加载时**拒绝，而不是去改 `is not False`——
    合法的只读单（配 accept.why）本来就该是 None，改那里会误伤它们。
    """
    bad = BASE.replace("id = 'a'\ntools = 'implement'\n",
                       "id = 'a'\ntools = 'readonly'\n", 1)
    _, f = _plan(tmp_path, bad)
    with pytest.raises(ConfigError) as e:
        P.load(f)
    msg = str(e.value)
    assert "readonly" in msg and "a" in msg
    assert "accept.why" in msg or "why" in msg, "必须给出另一条出路"


def test_只读单配why照常放行(tmp_path):
    """⚠️ 防回归：只读单**本来就有**合法形态——写明为什么没有机器判据。"""
    ok = BASE.replace(
        "id = 'a'\ntools = 'implement'\n", "id = 'a'\ntools = 'readonly'\n", 1
    ).replace(
        "  require_pass = ['基线守卫']\n\n[[task]]",
        "  why = '本单产出是一份自然语言报告，没有可机检的结构'\n\n[[task]]", 1)
    _, f = _plan(tmp_path, ok)
    sp = P.load(f)
    assert sp.by_id("a").require_pass == ()


def test_写操作单点名闸照常放行(tmp_path):
    """⚠️ 防回归：implement/full 单点名闸是**正常且推荐**的用法。"""
    good = BASE     # BASE 本身就是 implement + 点名闸
    _, f = _plan(tmp_path, good)
    assert P.load(f).by_id("a").require_pass == ("基线守卫",)


# ══ 例外层：重试 + 交接单 ═══════════════════════════════════════
# ⚠️ 先说清一件事：例外层**不可能是 PLAN 原本设想的样子**。
#    PLAN 写的是「重试耗尽 → 贵模型读报告与 diff → 换法重派」，
#    但自动驾驶是个 Python 循环，**它调不动贵模型**
#    （`--bare` 子进程读不到订阅凭据，Phase 0 已确认）。
#    诚实的实现是：**把失败归类，产出一份可操作的交接单，然后停。**
#    与「直接停」的区别是：现在只说 blocked，例外层要说清
#    **为什么失败、试过什么、建议怎么换**。

def test_retries不许是死配置(tmp_path):
    """⛔ 审查发现：`retries` 解析了但**一行代码都不读**，
    而计划里白纸黑字写着「2 单 × 最多 3 次尝试」——纯装饰。"""
    proj, f = _plan(tmp_path, BASE.replace("tools = 'implement'\n  [task.accept]",
                                           "tools = 'implement'\nretries = 3\n  [task.accept]", 1))
    sp = P.load(f)
    assert sp.by_id("a").retries == 3
    # 失败一次之后还该轮得到它——这才叫 retries 生效
    prog = A.Progress(done_ok=set(), done_bad={"a"}, attempts={"a": 1})
    ready, stop = A.plan_next(sp, prog)
    assert [t.id for t in ready] == ["a"], "还有重试余额时必须让它再试"
    assert stop is None


def test_重试耗尽才停(tmp_path):
    proj, f = _plan(tmp_path, BASE.replace("tools = 'implement'\n  [task.accept]",
                                           "tools = 'implement'\nretries = 2\n  [task.accept]", 1))
    sp = P.load(f)
    prog = A.Progress(done_ok=set(), done_bad={"a"}, attempts={"a": 2})
    ready, stop = A.plan_next(sp, prog)
    assert not ready
    assert stop.kind == "escalation", f"重试耗尽应升到例外层，实得 {stop.kind}"
    assert "a" in stop.detail


def test_例外层的停机也要找人(tmp_path):
    """⛔ 除了「跑完了」，一切停机都要找人——包括例外层。"""
    assert A.Stop("x", "escalation").needs_human


def test_交接单要写清失败模式与证据(tmp_path):
    """⚠️ 与「直接停」的全部区别就在这里。只说 blocked 等于什么都没说。"""
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [
        {"ts": "2026-02-01T00:00:00", "task": "a", "ok": False, "gate_ok": False,
         "cost_usd_real": 0.01, "error": "闸未通过：1 过 / 1 未过：基线守卫（被改了）"},
        {"ts": "2026-02-01T00:05:00", "task": "a", "ok": False, "gate_ok": None,
         "cost_usd_real": 0.02, "error": "error_max_turns"},
    ])
    md = A.escalation(ProjectPaths(proj), sp, sp.by_id("a"), "2026-01-01T00:00:00")
    assert "a" in md
    assert "基线守卫" in md and "error_max_turns" in md, "每次尝试的证据都要在"
    assert "$0.03" in md or "0.0300" in md, "要说清这单已经花了多少"
    # ⛔ 三条硬约束
    assert "无权改验收标准" in md
    assert "建议" in md, "建议要标明是建议，不是结论"


def test_交接单要落盘且路径可预期(tmp_path):
    proj, f = _plan(tmp_path)
    sp = P.load(f)
    _tel(proj, [{"ts": "2026-02-01T00:00:00", "task": "a", "ok": False,
                 "gate_ok": False, "cost_usd_real": 0.01, "error": "x"}])
    p = A.write_escalation(ProjectPaths(proj), sp, sp.by_id("a"), "2026-01-01T00:00:00")
    assert p.exists() and p.suffix == ".md"
    assert sp.id in p.name and "a" in p.name


# ══ ⛔ 阶段级 require_pass 曾是死配置（G-53 复刻） ═══════════════

def test_阶段级点名闸必须真的生效(tmp_path):
    """⛔ **2026-07-29 审计抓到：`[stage.accept] require_pass` 一行代码都没读过。**

    `plan.py` 定义了它、解析了它，`demo.toml` 里配着它、旁边还写着
    「⛔ SKIP 不算过（G-53）」——然后全仓没有任何一处读 `sp.require_pass`。
    只有任务级的 `t.require_pass` 被传进 `_run_unit`。

    ⚠️ 这就是 G-53 那种**空守卫**的教科书复刻，而且写在引用 G-53 的文件里：
    配置摆在那儿、`--dry-run` 还会把闸名念给人听，主动确认一个永不生效的守卫。
    `templates.toml` 没出事只是因为任务级又抄了一遍。

    新语义：阶段级点名**并进每个写任务**（并集，不是覆盖）。
    ⚠️ 只读单不并——只读不跑闸，并进去就又造出一个空守卫。
    """
    from devloop import plan as plan_mod
    for tid in ("w", "r"):
        (tmp_path / f"{tid}.md").write_text(
            "# 角色\n工人\n\n# 任务\n干活\n\n# 禁令\n无\n", encoding="utf-8")
    p = tmp_path / "s.toml"
    p.write_text(
        "[plan]\nversion = 1\n"
        f"[stage]\nid='s'\ngoal='g'\ntask_dir='{tmp_path.as_posix()}'\nbase='HEAD'\n"
        "[stage.budget]\ntotal_usd=1.0\nreserve_usd=0.1\nmax_dispatches=4\n"
        "max_wall_min=10\nwatchdog_k=2\n"
        "[stage.accept]\nrequire_pass=['语法','pytest']\n"
        "[[task]]\nid='w'\ntools='implement'\n"
        "[task.accept]\nrequire_pass=['禁改清单']\n"
        "[[task]]\nid='r'\ntools='readonly'\n"
        "[task.accept]\nwhy='只读调查，没有机器判据'\n",
        encoding="utf-8")
    sp = plan_mod.load(p)

    w = sp.by_id("w")
    eff = plan_mod.effective_require_pass(sp, w)
    assert set(eff) == {"语法", "pytest", "禁改清单"}, \
        f"写任务要吃到阶段级 + 任务级的并集，实得 {eff}"

    r = sp.by_id("r")
    assert plan_mod.effective_require_pass(sp, r) == (), \
        "⛔ 只读单不跑闸，并进阶段级点名会造出又一个空守卫"
