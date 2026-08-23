"""自动驾驶：无人值守跑完一个阶段。

## 三层，谁管什么

| 层 | 谁 | 触发 | 干什么 |
|---|---|---|---|
| 自动层 | 本模块（零模型成本） | 每单跑完 | 过闸 → 按依赖派下一单；失败 → 带上下文重试 |
| 例外层 | **交接单 + 停机** | 重试耗尽 | 归类失败、写清证据、给出建议方向。⛔ **无权改验收标准** |
| 上报层 | **你** | 宪法命中 / 预算耗尽 / 看门狗 | 决策 |

**方法自由，目标锁死**：怎么做随它试；「做成什么样」由计划里点名的闸把守，
自动层与例外层都改不了。想改验收 = 宪法上报。

⚠️ **例外层不是 PLAN 原本设想的样子，这一点必须说清。**
PLAN 写的是「重试耗尽 → 贵模型读报告与 diff → **换法重派**」，
但自动驾驶是一个 Python 循环，**它调不动贵模型**——`--bare` 子进程读不到
订阅凭据（Phase 0 已确认）。所以诚实的实现是：
**把失败归类、产出可操作的交接单、然后停。**
与「直接停」的区别在于：直接停只说一句 blocked，等于什么都没说。

## ⛔ 失控防线（无人值守时，它们是唯一挡在「一夜烧光」前面的东西）

四条，缺一不可，且**都在花钱之前检查**：

1. **总预算**：已花 + 预留 > 上限 → 停。
   ⚠️ 派单后才发现超了 = 钱已经花了，所以必须留 `reserve_usd`。
   ⚠️ 那个预留值该等于**单单成本的高分位**，不是平均——按平均留，
   一单贵的就能冲过头。
2. **⭐ 派单次数绝对上限**：`max_dispatches`。
   这条是**兜底**：价目表里没有某个模型时 `cost_usd_real` 是 `None`，
   若把 None 当 0，预算永远不会耗尽。**次数上限不依赖任何成本计算。**
3. **墙钟上限**：跑太久就停，哪怕钱还没花完。
4. **空转看门狗**：连续 K 单没有新的绿 → 停。

⚠️ **K 的取值没有数据支撑，我说没有。** 真实台账里「写路径 + 闸」的样本
只有 3 行，定不出统计意义上的 K。默认 3 是一个保守的可解释初值
（够一次「失败 → 重试 → 换法」的完整循环），⛔ 不是标定出来的。

## 崩溃恢复

进程随时可能被杀（断电、`halt --kill`、蓝屏）。**唯一可信的进度来源是台账**
——与 `jobs.py` 同一条纪律：

> ⛔ 自己的记录只登记「派了什么」，不登记「结果如何」。
> 结果的唯一真源是台账与回执。两处都记会分叉，而**分叉的账本比没有账本更坏
> ——它看起来是权威的**。

所以恢复不靠 checkpoint 里的「我跑到第几单了」，而是**每轮从台账重算**
哪几单已经跑完。checkpoint 只存「这个阶段起于何时、派过几次」这类
台账里没有的东西。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import constitution, telemetry
from .config import ConfigError, ProjectPaths
from .plan import StagePlan, Task

STATE_DIR = ".devloop/autopilot"


@dataclass
class Stop:
    """停下来的原因。⚠️ 每一种都必须能一句话说清，且能对应到一个动作。"""
    why: str
    kind: str            # done / budget / dispatches / wall / watchdog
                         # / constitution / blocked / escalation
    detail: str = ""

    @property
    def needs_human(self) -> bool:
        """要不要找人。⛔ `done` 之外**全都要**——包括「不知道为什么停的」。"""
        return self.kind != "done"


@dataclass
class Progress:
    """从**台账**重算出来的进度。⛔ 不从自己的记录里读结果。"""
    done_ok: set[str] = field(default_factory=set)
    done_bad: set[str] = field(default_factory=set)
    spent: float = 0.0
    unknown_cost: int = 0        # 算不出成本的单数 —— ⛔ 绝不当成 0 元
    #  ⭐ 这些「算不出成本」里，有几单是**被死线掐死**的。
    #  ⛔ 两者的处置完全不同：超时 = 把死线放宽 / 把活拆小；
    #     查不到价 = 补 `prices.json`。⚠️ 印错一个字，人就去修一个没坏的东西。
    timed_out: int = 0
    dispatches: int = 0
    #  每单**试过几次**（台账行数，不去重）。⚠️ 与 done_* 的去重口径不同：
    #  那两个回答「结果如何」，这个回答「花了几次钱」——两件事。
    attempts: dict[str, int] = field(default_factory=dict)

    @property
    def done(self) -> set[str]:
        return self.done_ok | self.done_bad


def read_progress(paths: ProjectPaths, plan: StagePlan, since: str) -> Progress:
    """从台账重算进度。

    ⚠️ `since` 是本阶段开始的时刻——只认这之后的行，否则上一次跑的结果
    会被算成本次进度（`jobs.py` 踩过同款）。
    """
    p = Progress()
    if not paths.telemetry.exists():
        return p
    want = {t.id for t in plan.tasks}
    #  ⛔ **必须走 `telemetry.load`，不许自己逐行 `json.loads`。**
    #     ⚠️ `load` 的 docstring 点名说「原实现是一句裸列表推导，一行坏行直接抛
    #     JSONDecodeError，上层显示成「输入错误：Unterminated string」——
    #     **不提文件名、不说是台账**」。⛔ 那个被认定为 bug 的实现，
    #     2026-08-02 之前在本函数里原样活着——而**本函数正是四条失控防线的
    #     唯一数据来源**，修的是别处，漏的是刹车这处。
    #  ⭐ `strict=True`（默认）是对的，而且那条纪律本来就是为刹车写的：
    #     「静默跳过等于让『已花多少』『派了几次』偷偷变小，而那正是失控防线读的数」。
    #  ⚠️ 代价是一行坏日志会停掉一夜的活——但停机**可解释、可恢复**（台账还在，
    #     修完 `--resume` 续上），而刹车静默变松**不可察觉**。⛔ 两者不对称。
    #  ⚠️ 与**写**侧的取舍正好相反（`_append`：「拿不到文件锁也照写——丢一行日志
    #     远好过丢一单活」）。⭐ 同一份台账：写侧宁可脏也要留痕，读侧宁可停也不许把脏当干净。
    #  ⭐ **必须过 `units()`**（G-108）：台账里一单是「开跑行 + 收工行」两行。
    #  ⛔ 按行数算的话一单会被数成两次——计划写最多派 2 次，跑完**一单**
    #     就被自己的刹车停掉，活少干一半。
    #  ⭐ 而只有开跑行没有收工行的那种（进程被杀 / Ctrl-C）会带 `interrupted`，
    #     它的 `cost_usd_real` 是 None ⇒ 落进 `unknown_cost` ⇒ 停批让人看。
    #     ⚠️ 那正是对的：钱花过了，花了多少不知道，⛔ 不知道不等于零。
    rows = telemetry.units(telemetry.load(paths.telemetry))
    # ⛔ 按任务名去重、后写覆盖先写：同一单重试多次只算一次，
    #    否则「跑完 N 单」会被行数顶成真（G-51 同款）。
    latest: dict[str, dict] = {}
    for r in rows:
        if r.get("task") in want and r.get("ts", "") >= since:
            latest[r["task"]] = r
            p.dispatches += 1
            # ⚠️ 试过几次按**行数**算，与 done_* 的去重口径不同：
            #    那两个回答「结果如何」，这个回答「花了几次钱」——两件事。
            p.attempts[r["task"]] = p.attempts.get(r["task"], 0) + 1
    for tid, r in latest.items():
        (p.done_ok if r.get("ok") and r.get("gate_ok") is not False
         else p.done_bad).add(tid)
        c = r.get("cost_usd_real")
        if c is None:
            p.unknown_cost += 1
            #  ⚠️ 老台账没有这个字段 → `.get` 缺省 False → 走「查不到价」那条，
            #     与改之前的措辞一致。⛔ 保守方向：不许把未知说成已知。
            if r.get("timed_out"):
                p.timed_out += 1
        else:
            p.spent += c
    return p


def check_limits(plan: StagePlan, prog: Progress, *, started: float,
                 dry_rounds: int) -> Stop | None:
    """派下一单**之前**问一次：还能不能派。返回 None 表示可以。

    ⛔ 顺序有讲究：先查不依赖成本的那几条（次数、墙钟），再查成本。
       因为成本这条**本身可能算不出来**。
    """
    b = plan.budget
    if prog.dispatches >= b.max_dispatches:
        return Stop("派单次数到顶", "dispatches",
                    f"已派 {prog.dispatches} 次，上限 {b.max_dispatches}。"
                    f"⚠️ 这是**不依赖成本计算**的兜底闸——它到顶说明活没按预期收敛。")

    mins = (time.time() - started) / 60
    if mins >= b.max_wall_min:
        return Stop("墙钟到顶", "wall",
                    f"已跑 {mins:.0f} 分钟，上限 {b.max_wall_min} 分钟。钱可能还没花完。")

    if prog.unknown_cost:
        # ⛔ 算不出成本 ≠ 花了 0 元。不拦住的话预算**永远不会耗尽**。
        #
        # ⭐ 2026-08-08：措辞按**真因**分岔。
        # ⛔ 2026-08-06 实测：`h-pyramid` 的工人被 3000 秒死线掐死、烧掉 720 万 tokens，
        #    而屏幕上印的是「多半是价目表里没有那个模型」——
        #    ⚠️ 指着人去修一个**根本没坏**的东西，真问题（活太大 / 死线太短）没人看见。
        # ⭐ 判据用台账里结构化的 `timed_out`，⛔ 不 match 自由文本
        #    （G-113 学费：靠子串匹配归类会被测试名劫持）。
        if prog.timed_out:
            rest = prog.unknown_cost - prog.timed_out
            return Stop("工人被死线掐死", "timeout",
                        f"{prog.timed_out} 单**超时被杀**"
                        + (f"（另有 {rest} 单算不出成本，多半是价目表缺项）" if rest else "")
                        + "。⛔ 钱已经花了，成本却记不上——"
                        "⚠️ 该动的是**死线或活的大小**，"
                        "⛔ **不是价目表**。")
        return Stop("有单算不出成本", "budget",
                    f"{prog.unknown_cost} 单的 cost_usd_real 是 null"
                    f"（多半是价目表里没有那个模型）。"
                    f"⛔ 算不出成本不等于没花钱——在补上价目表之前不继续派单。")

    if prog.spent + b.reserve_usd > b.total_usd:
        return Stop("预算不够再派一单", "budget",
                    f"已花 ${prog.spent:.4f}，预留 ${b.reserve_usd:.4f}，"
                    f"上限 ${b.total_usd:.4f}。"
                    f"⚠️ 预留是必须的：派完才发现超了，钱已经花了。")

    if dry_rounds >= b.watchdog_k:
        return Stop("空转", "watchdog",
                    f"连续 {dry_rounds} 轮没有新的绿。"
                    f"⚠️ K={b.watchdog_k} 是保守的可解释初值，**不是标定出来的**"
                    f"——真实样本不足以定 K。")
    return None


def plan_wave(ready: list[Task], *, parallel: int, remaining: int) -> list[Task]:
    """这一波派哪几单。⛔ **纯函数**——判据要能直接量，不能靠读源码。

    三条约束，⚠️ 缺一条都会让某一侧失效：

    1. **同一种权限**。只读与写的并行判据完全不同（成本差 35 倍，见 `fanout.py`），
       混在一波里没有统一判据可用。
    2. **不超过 `parallel`**。计划声明的并发度。
    3. ⭐ **不超过 `remaining`**——距 `max_dispatches` 还剩几次。

    ## ⛔ 第 3 条为什么是新加的（2026-08-02 审计）

    上限原来是在**切波之前**判的，而 `fired` 一次加 N：
    `max_dispatches=10` + `parallel=4` 时，第 3 轮 `fired=8 < 10` 通过 →
    又派 4 单 → 实际派了 **12** 次。溢出量 = 并发数 − 1。

    ⚠️ 而 `max_dispatches` 是订阅制后端上**「烧了多少」这个维度上唯一还活着的
    刹车**：订阅的美元成本恒为 0.0（`pricing.py` 对 `__subscription__` 返回
    `0.0` 而非 `None`），于是 `check_limits` 里两条按成本判的防线结构性恒假。
    ⛔ 措辞不许再写成「唯一还活着的刹车」——`max_wall_min` 管时间、
    `watchdog_k` 管停滞，两条都不依赖成本，**都还活着**；它们只是管不住
    「一夜派了多少单」。⚠️ 把三条说成一条，会让人以为改坏了它就全没防线了。

    ⚠️ `remaining <= 0` 在调用点走不到（`check_limits` 会先停），但这里仍然
    返回空表而不是抛——⛔ 把「不可能」赌在无人值守的一夜上不是工程。
    """
    if remaining <= 0 or not ready:
        return []
    head_tools = ready[0].tools
    n = min(max(1, parallel), remaining)
    return [t for t in ready if t.tools == head_tools][:n]


@dataclass
class Run:
    plan: StagePlan
    started_at: str
    started: float
    rounds: list[dict] = field(default_factory=list)
    stop: Stop | None = None
    #  ⭐ 接力点（`[stage] chain = true` 时才有值）。
    #     ⛔ **必须落盘**：它原来只活在进程内存里，于是 `--resume` 之后
    #     `base` 被重算回项目 HEAD，前几单的产出只在未合并的隔离分支上、
    #     树里根本没有——下游单要么必然失败，要么工人「自己重写一个顶上」，
    #     **产出静默分叉**。而崩溃恢复正是无人值守的立项理由。
    chain_head: str = ""

    def state_path(self, paths: ProjectPaths) -> Path:
        return paths.project / STATE_DIR / f"{self.plan.id}.json"

    def save(self, paths: ProjectPaths, prog: Progress) -> None:
        """⛔ 只存台账里没有的东西：起始时刻、轮次记录。
        **不存「哪几单成功了」**——那是台账的职责，两处都记必然分叉。"""
        f = self.state_path(paths)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "stage": self.plan.id, "started_at": self.started_at,
            "rounds": self.rounds,
            # ⚠️ 这**不是**「哪几单成功了」（那是台账的职责）——它是
            #    「下一单该从哪个提交起」，台账里没有这个信息。
            "chain_head": self.chain_head,
            "stop": {"why": self.stop.why, "kind": self.stop.kind,
                     "detail": self.stop.detail} if self.stop else None,
            "_note": "⛔ 这里不记「哪几单成功了」——结果的唯一真源是台账与回执。",
        }, ensure_ascii=False, indent=1), encoding="utf-8")


def _state(paths: ProjectPaths, plan: StagePlan) -> dict:
    f = paths.project / STATE_DIR / f"{plan.id}.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def resume_point(paths: ProjectPaths, plan: StagePlan) -> str | None:
    """上次这个阶段是什么时候起的。用来接着上次的台账窗口算进度。"""
    return _state(paths, plan).get("started_at")


def resume_chain_head(paths: ProjectPaths, plan: StagePlan) -> str:
    """上次跑到哪个接力点。⛔ 没有就返回空串（那时该用阶段的 base）。

    ⚠️ 这条是 `--resume` 与 `chain = true` 一起用时的**正确性前提**：
    不读回来的话，下游单会从项目 HEAD 建工作副本，看不到前几单的产出。
    """
    return str(_state(paths, plan).get("chain_head") or "")


def plan_next(plan: StagePlan, prog: Progress) -> tuple[list[Task], Stop | None]:
    """挑出这一轮能派的单。

    ⚠️ 已跑但没过的单**不算 done**，所以它会一直挡着下游——这是对的：
    依赖一个失败的产物继续往下干，只会把错误铺开。
    """
    # ⚠️ 三个集合，分工不能混：
    #    · done_ok    —— 绿了的。**只有它解锁下游**（依赖失败的产物往下干会把错误铺开）
    #    · exhausted  —— 试够 retries 次仍未绿的。它才真正出局
    #    · 其余失败的 —— **还有重试余额，照样轮得到**（那正是 retries 的用处）
    exhausted = {t.id for t in plan.tasks
                 if t.id not in prog.done_ok
                 and prog.attempts.get(t.id, 0) >= max(1, t.retries)}
    ready = plan.ready(prog.done_ok, attempted=prog.done_ok | exhausted)
    if not ready:
        if len(prog.done_ok) == len(plan.tasks):
            return [], Stop("阶段跑完了", "done",
                            f"{len(prog.done_ok)}/{len(plan.tasks)} 单全绿")
        if exhausted:
            # ⭐ 升到**例外层**：重试都用完了还没绿，不是「没单可派」，
            #    是「这几单需要换个方法」——而换方法要人（或编排方）来判断。
            return [], Stop(
                "重试耗尽，升到例外层", "escalation",
                f"{'、'.join(sorted(exhausted))} 试满了 retries 次仍未通过。\n"
                f"      已生成交接单（写清每次尝试的失败模式与证据）。\n"
                f"      ⛔ 例外层**无权改验收标准**——想改验收是宪法上报。")
        blocked = sorted({t.id for t in plan.tasks} - prog.done_ok)
        return [], Stop("没有可派的单了", "blocked",
                        f"未完成：{'、'.join(blocked)}。"
                        f"其中失败的：{'、'.join(sorted(prog.done_bad)) or '无'}。"
                        f"⚠️ 依赖一个失败的产物继续往下干只会把错误铺开，所以停。")
    return ready, None


def preflight(paths: ProjectPaths, plan: StagePlan) -> constitution.Constitution:
    """开跑之前：宪法必须在位且锚对得上。

    ⛔ **无人值守没有宪法 = 没有任何东西挡着模型改自己的规矩。**
    前台派单时宪法缺失只是警告（人在看），自动驾驶必须硬性要求。
    """
    try:
        con = constitution.load(paths)
    except ConfigError as exc:
        raise ConfigError(
            f"⛔ 自动驾驶拒绝开跑：{exc}\n"
            f"   前台派单可以没有宪法（你在看着），无人值守不行——\n"
            f"   没有宪法就没有任何东西挡着它改自己的规矩。") from exc
    anc = constitution.verify_anchor(paths, con)
    if not anc.clean:
        raise ConfigError(
            f"⛔ 自动驾驶拒绝开跑：{anc.summary()}\n"
            f"   判定基准本身不可信了，先查明再谈无人值守。")

    #  ⭐ 硬拒也要在这条路上跑一遍。⛔ 2026-08-03 之前**一次都没跑过**：
    #     `refuse_preflight` 的唯一调用点在 `cmd_dispatch` 里，而自动驾驶
    #     走的是另一条路——于是计划里写 `tools = "full"` 就能让工人整夜
    #     握着 WebFetch，`[refuse] full_tools_when_detached = true` 那行
    #     配置在这条路上是**纯装饰**。
    #  ⚠️ 恒传 `unattended=True`：自动驾驶按定义就是没人在看。
    #     ⛔ 判据不许挂在 `--detach` 那个开关上——它只存在于另一条路。
    #  ⭐ 逐单查、且在**开跑之前**查：一单被拒就整个计划不开跑，
    #     而不是跑到第 7 单才炸（那时前 6 单的钱已经花了）。
    for t in plan.tasks:
        try:
            constitution.refuse_preflight(con, tools=t.tools, unattended=True)
        except ConfigError as exc:
            raise ConfigError(
                f"⛔ 自动驾驶拒绝开跑——计划里的任务 `{t.id}` 过不了硬拒：\n"
                f"{exc}") from exc
    return con


# ── 例外层：交接单 ────────────────────────────────────────────

def _rows_for(paths: ProjectPaths, task_id: str, since: str) -> list[dict]:
    #  ⛔ 与 `read_progress` 走**同一条**读法。⚠️ 这里原来是第二份裸列表推导，
    #     两份分别修必然分叉——而这里分叉的后果是「同一个台账，两个进度」，
    #     ⭐ 而交接单的全部价值就是把每次尝试的事实摆准。
    if not paths.telemetry.exists():
        return []
    rows = telemetry.load(paths.telemetry)
    return [r for r in rows if r.get("task") == task_id and r.get("ts", "") >= since]


def escalation(paths: ProjectPaths, plan: StagePlan, task: Task, since: str) -> str:
    """重试耗尽时产出的交接单。

    ## ⚠️ 这不是 PLAN 原本设想的例外层，说清楚为什么

    PLAN 写的是「重试耗尽 → 贵模型读报告与 diff → **换法重派**」。
    但自动驾驶是一个 Python 循环，**它调不动贵模型**——`--bare` 子进程读不到
    订阅凭据（Phase 0 已确认的事实）。所以诚实的实现是：
    **把失败归类，产出一份可操作的交接单，然后停。**

    与「直接停」的区别在于：直接停只说「blocked」，等于什么都没说；
    交接单要说清**为什么失败、试过什么、建议往哪个方向换**。

    ## ⛔ 三条硬约束

    1. **例外层无权改验收标准。** 否则「自己换方法」会退化成「自己降低标准」。
    2. **不许把「模型说它拿不准」当判据**——那撞第一铁律（完成由退出码裁决，
       不由模型自述）。硬对偶是数数：试满 N 次即停。
    3. **只写证据，不写结论。** 「建议」必须标明是建议。
    """
    rows = _rows_for(paths, task.id, since)
    spent = sum(r.get("cost_usd_real") or 0 for r in rows)
    unknown = sum(1 for r in rows if r.get("cost_usd_real") is None)

    L = [f"# 例外层交接单 · {plan.id} / {task.id}", "",
         f"> 试满 **{len(rows)}** 次仍未通过（计划给的 retries = {task.retries}）。",
         f"> 这单已花 **${spent:.4f}**"
         + (f"（另有 {unknown} 次算不出成本）" if unknown else "") + "。", "",
         "⛔ **例外层无权改验收标准。** 想改验收标准是宪法上报，不是例外层的事。",
         "⚠️ 下面「建议」两个字开头的都是**建议**，不是结论——",
         "　 判据只有闸和宪法，交接单不裁决任何东西。", "",
         "---", "", "## 每一次尝试"]

    for i, r in enumerate(rows, 1):
        L.append(f"\n### 第 {i} 次 · {r.get('ts', '?')}")
        L.append(f"- 结果：`ok={r.get('ok')}` · `gate_ok={r.get('gate_ok')}`")
        c = r.get("cost_usd_real")
        L.append(f"- 成本：{'$%.4f' % c if c is not None else '**算不出**（价目表里没有这个模型）'}"
                 f" · 耗时 {r.get('duration_s', 0):.0f}s · {r.get('turns', r.get('num_turns', 0))} 轮")
        if r.get("error"):
            L.append(f"- 失败原因（台账原文）：\n  ```\n  {r['error']}\n  ```")
        if r.get("gate_detail"):
            L.append(f"- 闸的结论：\n  ```\n  {r['gate_detail']}\n  ```")

    # ── 失败模式归类 ─────────────────────────────────────────────
    #
    # ⚠️ **判据是对台账 `error` 字段做子串匹配**——那是自由文本，不是结构化字段。
    #    ⛔ 这行标题原来自称只认结构化字段，而实现从来就是 `"宪法命中" in e`。
    #    ⚠️ 留一句好听的假话比说实话更坏：它会让下一个人以为这里已经是结构化的了。
    #    要真结构化得改台账字段形状，而台账是失控防线的唯一真源，
    #    那是独立的一条，不在这里顺手做。
    #
    # ⛔ **顺序即判据。** `cli.py::_run_unit` 写进台账的 error 前缀**恒为**
    #    「闸未通过：」，不管闸返回 1 还是 2；code 2 的 `summary()` 才以
    #    「闸自身故障：」开头。所以「闸自身故障」必须排在「闸未通过」**前面**
    #    ——排后面它就是死代码，实测两条构造台账（一条闸真坏、一条活真没干好）
    #    **都**归到了「闸未通过」，配套建议「先修环境再谈验收」一次都没印出来。
    # ⚠️ 这条顺序纪律只对**没有 `gate_code` 的历史行**生效了。
    #    ⭐ 新行走结构化字段，不再依赖「code 1 的说辞里不出现某四个字」——
    #    那条纪律实测会被测试名劫持（见下面 `gc` 那几支的注释）。
    kinds: dict[str, int] = {}
    for r in rows:
        e = str(r.get("error") or "")
        gc = r.get("gate_code")
        if "宪法命中" in e:
            k = "宪法命中（需要人批准，不是活没干好）"
        #  ⭐ **先读结构化的 `gate_code`，读不到才退回猜文本。**
        #     ⛔ 2026-08-02 对抗复核实测：本仓 gates.sh 把 pytest 的失败节点名
        #     原样放进 FAIL 的 detail，于是一个叫
        #     `test_闸自身故障要归到闸自身故障` 的测试挂掉，会让「活没干好」
        #     被归类成「闸自身故障 → 先修环境」，⚠️ 建议方向整个反了。
        #     而五份计划全都点名 pytest——那是自动驾驶跑本项目的**主路径**。
        #  ⚠️ 退路必须留：台账只追加，历史行没有这个字段。
        elif gc == 2:
            k = "⚠️ 闸自身故障（先修环境，不是活的问题）"
        elif gc == 1:
            k = "闸未通过（活没达标）"
        elif gc is None and ("闸自身故障" in e or "gate_broken" in e):
            k = "⚠️ 闸自身故障（先修环境，不是活的问题）"
        elif "零改动" in e:
            #  ⭐ 本项目**唯一一份真实交接单**里发生的事：写任务跑完，闸全绿，
            #     但 worktree 里一个文件都没变。它原来落进兜底档「其它」，
            #     拿到的建议是「这单可能不该由工人做，改成只读调查单」
            #     ——⛔ 而零改动恰恰说明工人读懂了但**没动手**，改成只读只会更不动手。
            k = "工人零改动（读懂了但没动手——⚠️ 是任务书的问题，不是能力问题）"
        elif "闸未通过" in e:
            k = "闸未通过（活没达标）"
        elif "max_turns" in e:
            k = "撞轮数上限被截断（⚠️ 是拆单/配置问题，不是模型能力问题）"
        elif "超时" in e:
            k = "工人超时"
        elif not e:
            k = "无失败原因记录（⚠️ 台账里 error 是空的，这本身该查）"
        else:
            k = "其它（见上方原文）"
        kinds[k] = kinds.get(k, 0) + 1

    L += ["", "---", "",
          "## 失败模式",
          "",
          "⭐ 闸相关的两档（**闸自身故障** / **闸未通过**）读台账里的结构化字段"
          " `gate_code`（0 全过 · 1 有未过 · 2 闸自身故障）。"
          "⚠️ 其余各档、以及**没有 `gate_code` 的历史行**，仍是对 `error` 做"
          "**子串匹配**（自由文本）——⛔ 那条路会被文本内容劫持"
          "（2026-08-02 实测：闸把失败的测试名原样放进说明，"
          "而测试名里含「闸自身故障」四个字就够了）。"
          "落进「其它」时以上面的原文为准。"]
    for k, n in sorted(kinds.items(), key=lambda x: -x[1]):
        L.append(f"- **{k}** × {n}")

    L += ["", "---", "", "## 建议的换法方向（⚠️ 建议，不是结论）"]
    if any("轮数上限" in k for k in kinds):
        L.append("- 提高该单的 `max_turns`，或把任务拆小。**没用掉的轮数不花钱**（G-46）。")
    if any("宪法命中" in k for k in kinds):
        L.append("- 宪法命中不是「干得不好」——它是**在等你批准**。"
                 "要么批准这次改动（改宪法并重新锚定），要么改任务书绕开受保护路径。")
    if any("闸自身故障" in k for k in kinds):
        L.append("- **先修环境再谈验收**。闸自己坏了的时候，任何结论都不可信。")
    if any("闸未通过" in k for k in kinds):
        L.append("- 读上面闸的结论，看是哪一道没过。"
                 "⛔ 不许通过放宽验收标准来「解决」它。")
    if any("零改动" in k for k in kinds):
        L.append("- **任务书没让工人知道要动哪个文件。** 零改动不是能力问题"
                 "——工人跑满了轮数、闸也全绿，它只是**没找到落笔的地方**。"
                 "⚠️ 先读任务书的「任务」一节：里面有没有具体的文件路径与符号名？"
                 "⛔ 「审查一下 X 并改进」这种写法拿到零改动是必然的。")
        L.append("- 若任务书已经很具体，那多半是**它认为不需要改**。"
                 "⭐ 让它把「为什么不需要改」写进报告比逼它改点什么有用得多"
                 "——后者会得到一个为了交差而做的改动。")
    if any("算不出" in str(r.get("cost_usd_real")) or r.get("cost_usd_real") is None
           for r in rows):
        L.append("- ⚠️ 有尝试算不出成本——补 `prices.json` 里那个模型，"
                 "否则预算防线读不到这些花费。")
    L.append("- 若以上都不适用：**这单可能不该由工人做**。"
             "考虑改成只读的调查单，先把事实弄清楚。")

    L += ["", "---", "",
          f"任务书：`{plan.task_dir / (task.id + '.md')}`",
          f"验收标准（⛔ 例外层不许动）："
          + (("点名闸 " + "、".join(task.require_pass)) if task.require_pass
             else f"无机器判据（已认领：{task.accept_none_why}）")]
    return "\n".join(L)


def write_escalation(paths: ProjectPaths, plan: StagePlan, task: Task,
                     since: str) -> Path:
    """把交接单落盘。路径可预期，便于编排方与人直接打开。"""
    d = paths.project / STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{plan.id}-escalation-{task.id}.md"
    f.write_text(escalation(paths, plan, task, since), encoding="utf-8")
    return f
