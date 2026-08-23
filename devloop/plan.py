"""阶段计划：自动驾驶要吃的那份东西。

自动驾驶的输入三件套之一（另两件是宪法与预算）。它回答四个问题：

1. **这个阶段要干哪几单**，谁依赖谁
2. **每单花多少钱封顶**，整个阶段花多少钱封顶
3. **怎么算干完了** —— ⛔ 必须是机器能裁决的，不许「模型说做完了」
4. **基准是什么** —— 基准漂了就拒跑，与评测集同源

## 为什么是 TOML

**主论据：计划是人写的，必须能写注释。** JSON 做不到。
`eco-ob/.devloop/config.toml` 里那 5 行解释「为什么必须同步 game/.godot」的注释
就是活证据——**没有那几行，下一个人会把它当成无用配置删掉**。

次论据：`tomllib` 是标准库，本包已经在用它读 `config.toml`，零新依赖。
⚠️ 但「零新依赖」只排除得掉 YAML，选不出 TOML vs JSON——所以主论据是注释。

⚠️ 顺带：本机装了 PyYAML，**选 YAML 会「本地导入成功、干净安装炸掉」**，
教科书级的假绿。这一条加强了不选 YAML。

## 字符串纪律

⚠️ TOML 的基本串（双引号）里反斜杠要转义，Windows 路径必炸：

    path = "C:\\pg\\eco-ob"      ✗ Unescaped '\\' in a string
    path = 'C:\\pg\\eco-ob'      ✓ 字面串，逐字节原样

⛔ 但**不按引号判，按值内容判**：现有能用的 `sync_ignored_paths = ["game/.godot"]`
用的是双引号且完全合法，一刀切「见双引号就报错」会把它判红。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError


@dataclass(frozen=True)
class Task:
    id: str
    tools: str = "readonly"
    needs: tuple[str, ...] = ()
    retries: int = 1
    max_turns: int = 0            # 0 = 用阶段默认
    #  ⛔ 验收：点名哪几道闸必须是 PASS。空 = 这单没有机器判据（必须显式认领）
    require_pass: tuple[str, ...] = ()
    accept_none_why: str = ""     # require_pass 为空时**必须**写明理由


@dataclass(frozen=True)
class Budget:
    total_usd: float
    reserve_usd: float            # 派下一单前必须还剩这么多
    max_dispatches: int           # ⭐ 绝对上限：成本算不出来时它仍然能停住
    max_wall_min: int
    default_max_turns: int = 120
    watchdog_k: int = 3


def clamp_to_wall(nominal_s: int, *, deadline: float | None,
                  now: float) -> int:
    """把一个子进程的死线收窄到**剩余墙钟**之内（G-107）。

    ## ⛔ 为什么必须有它

    2026-08-04：计划写 `max_wall_min = 60`，实跑 80 分钟。
    根因**不是**「刹车太松」，是刹车**从来没被接到正在跑的那一单上**：

    - `autopilot.check_limits` 的墙钟那条在「派下一单**之前**」问（docstring 原文）；
    - 一单开跑之后，工人 3000s + 闸 1800s = 80 分钟，期间没有任何东西看表。

    ⭐ 而 `dispatch_one` 和 `run_gates` **本来就都收 timeout 参数**——
    刹车装得上，只是没人接线。接上之后 `max_wall_min` 才从一句
    「之后不再开新单」变成一个**真的**上界。

    ## 判据

        实际死线 = min(自己的死线, 到墙钟为止还剩多少)

    ⛔ 收窄后**至少留 1 秒**：给 0 或负数会让 `subprocess.run` 立刻抛超时，
       那等于「没开始就判超时」，账上会记成一单花过钱的活——⚠️ 而它没花。
       ⭐ 真正「时间不够别开工」的判断在上层（`check_limits`），不在这里。

    ⚠️ `deadline is None` = 不限墙钟（手动 `dispatch` 就是这样）——⛔ 原样返回，
       不许当成 0。
    """
    if deadline is None:
        return nominal_s
    left = int(deadline - now)
    return max(1, min(nominal_s, left))


def wall_report(budget: Budget, *, worker_timeout_s: int | None,
                gate_timeout_s: int | None) -> str:
    """开跑前把墙钟的**真实**含义印给人看。⭐ 纯信息，不拒绝任何东西。

    ## ⛔ 为什么不做成硬拒

    我 2026-08-04 的第一版是「装不下一单就拒绝开跑」。⚠️ 那个方向错了：
    它在**否决一个数**，而正确的做法是**让那个数变成真的**（见 `clamp_to_wall`）。
    ⭐ 而且硬拒会误伤——闸的 1800s 是**天花板**不是**预期**，
    一个 20 秒就跑完闸的项目照样会被判死。

    ## ⭐ 那这里还印什么

    印「这一单实际能拿到多少时间」。⚠️ 当墙钟比标称死线还紧时，
    工人拿到的**不是**它以为的 3000 秒——这件事必须说出来，
    否则一个被墙钟提前掐断的工人会被误读成「模型不行」。
    """
    wall_min = budget.max_wall_min
    if worker_timeout_s is None or gate_timeout_s is None:
        return (f"墙钟 {wall_min} 分钟（⚠️ 读不到子进程死线，"
                f"这次没法说清一单实际能拿到多少时间）")

    wall_s = wall_min * 60
    w = min(worker_timeout_s, wall_s)
    g = min(gate_timeout_s, max(1, wall_s - w))
    line = (f"墙钟 {wall_min} 分钟 —— ⭐ **它会真的刹住正在跑的单**："
            f"工人与闸的死线按剩余时间收窄")
    if worker_timeout_s + gate_timeout_s > wall_s:
        line += (f"\n   ⚠️ 这一档墙钟比标称死线紧：第一单里工人最多拿 {w}s"
                 f"（标称 {worker_timeout_s}s）、闸最多拿 {g}s"
                 f"（标称 {gate_timeout_s}s）。"
                 f"\n   ⛔ 于是工人可能是**被墙钟掐的**，不是干不完"
                 f"——别把它读成「模型不行」。")
    return line


def effective_require_pass(stage: "StagePlan", task: "Task") -> tuple[str, ...]:
    """这一单实际要点名哪几道闸 = 阶段级 ∪ 任务级。

    ## ⛔ 阶段级点名曾经是**一行代码都没读过的死配置**

    2026-07-29 独立审计抓到：`plan.py` 定义并解析了 `[stage.accept]
    require_pass`，`demo.toml` 里配着它、旁边还写着「⛔ SKIP 不算过（G-53）」，
    然后**全仓没有任何一处读它**——只有任务级的被传下去。

    ⚠️ 这是 G-53 那种**空守卫**的教科书复刻，而且就写在引用 G-53 的文件里：
    配置摆在那儿、`--dry-run` 还会把闸名念给人听，**主动确认一个永不生效的守卫**。
    `templates.toml` 侥幸没出事，只因为它在任务级又抄了一遍同样四道闸。

    ## ⚠️ 只读单不并

    只读单不建 worktree、不跑闸，闸结果恒为空。把阶段级点名并进去，
    就又造出一个空守卫——正是本函数要消灭的那种东西。
    """
    if task.tools == "readonly":
        return ()
    return tuple(sorted(set(stage.require_pass) | set(task.require_pass)))


@dataclass(frozen=True)
class StagePlan:
    id: str
    goal: str
    task_dir: Path
    base: str
    budget: Budget
    tasks: tuple[Task, ...]
    require_pass: tuple[str, ...]     # 阶段级验收：点名的闸必须全 PASS
    source: Path
    #  ⭐ 接力：后一单的工作副本从**前一单已过闸的产出**起（G-59）。
    #     ⛔ 默认关。开了之后几单会绑在一起：后一单的分支含着前一单的提交，
    #     合并时合最后一个就全拿到——但**不能只否掉前一单**。
    #     而「合回主线要人逐单批」是宪法条款，这个代价必须由人明确接受。
    chain: bool = False
    #  ⭐ 一波同时派几单（G-66）。⛔ 默认 1（串行）——并行的代价全落在**人**身上：
    #     N 个分支要按宪法 C-2/C-4 逐个批准，范围重叠还要人解冲突。
    #     ⚠️ 收益是摊薄闸的墙钟（eco-ob 实测闸占整单 **87%**）。
    #  ⛔ 与 `chain` **互斥**：接力的语义是「后一单从前一单**已过闸的**产出起」，
    #     天然要求串行。两个一起开，后一单会从一个还没跑完的起点复制
    #     ——⚠️ 那不是性能问题，是正确性问题。
    parallel: int = 1

    def by_id(self, tid: str) -> Task | None:
        return next((t for t in self.tasks if t.id == tid), None)

    def ready(self, done_ok: set[str], attempted: set[str] | None = None) -> list[Task]:
        """可以派的单。

        ⚠️ 两个集合的分工不能混：
        - `done_ok`   —— **绿了**的单。只有它能解锁下游：依赖一个失败的产物
                        继续往下干，只会把错误铺开。
        - `attempted` —— 已经派过的单（含失败）。不给就等于 `done_ok`。
                        失败的单本身**是可以重试的**——那正是 retries 的用处，
                        它只是不解锁下游。
        """
        tried = done_ok if attempted is None else attempted
        return [t for t in self.tasks
                if t.id not in tried and set(t.needs) <= done_ok]


def _cycles(tasks: tuple[Task, ...]) -> list[str]:
    """返回环里的任务 id。用 Kahn 剥层，剥不掉的就在环里。"""
    left = {t.id: set(t.needs) for t in tasks}
    changed = True
    while changed:
        changed = False
        for tid in [k for k, v in left.items() if not v]:
            del left[tid]
            for v in left.values():
                v.discard(tid)
            changed = True
    return sorted(left)


def load(path: Path) -> StagePlan:
    """读阶段计划。**任何问题一律 raise ConfigError（退出码 2），不跑。**

    ⚠️ 全部校验必须在**花第一分钱之前**完成——这与 `_load_specs` 预校验
    任务书是同一条理由：第 17 单缺标题不该让前 16 单已经计费之后才整批中止。
    """
    if not path.exists():
        raise ConfigError(f"没有阶段计划 {path}")
    try:
        # utf-8-sig：人手写的文件，Windows 编辑器加 BOM 是现实输入，
        # 而 tomllib 撞 BOM 只报「line 1 column 1」，既不提 BOM 也不提文件名。
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{path} 不是合法 TOML：{exc}\n"
            f"   ⚠️ 这是人手写的文件——先看编辑器有没有加 BOM，"
            f"以及 Windows 路径是不是该改用单引号字面串。") from exc

    if data.get("plan", {}).get("version") != 1:
        raise ConfigError(
            f"{path} 的 plan.version = {data.get('plan', {}).get('version')!r}，"
            f"本工具只认 1。版本不同意味着语义可能变了，在人确认之前拒绝执行。")

    st = data.get("stage", {})
    for k in ("id", "goal", "task_dir", "base"):
        if not st.get(k):
            raise ConfigError(f"{path} 的 [stage] 缺 {k}")

    b = st.get("budget", {})
    for k in ("total_usd", "reserve_usd", "max_dispatches", "max_wall_min"):
        if k not in b:
            raise ConfigError(
                f"{path} 的 [stage.budget] 缺 {k}。\n"
                f"   ⛔ 四个上限一个都不能省——无人值守时它们是唯一挡在"
                f"「一夜烧光」前面的东西。")
    if b["max_dispatches"] < 1 or b["total_usd"] <= 0 or b["max_wall_min"] < 1:
        raise ConfigError(f"{path} 的预算里有非正数，那样等于禁止一切派单")
    budget = Budget(
        float(b["total_usd"]), float(b["reserve_usd"]), int(b["max_dispatches"]),
        int(b["max_wall_min"]), int(b.get("default_max_turns", 120)),
        int(b.get("watchdog_k", 3)))

    tasks: list[Task] = []
    for i, row in enumerate(data.get("task", [])):
        tid = row.get("id")
        if not tid:
            raise ConfigError(f"{path} 的第 {i + 1} 个 [[task]] 没有 id")
        acc = row.get("accept", {})
        req = tuple(acc.get("require_pass", []))
        why = acc.get("why", "")
        # ⛔ 没有机器判据必须**显式认领**，写明为什么。
        #    不强制这一条的话，「忘了写验收标准」和「这单确实没法机检」
        #    在文件里长得一模一样——而前者是漏洞，后者是已知代价。
        if not req and not why:
            raise ConfigError(
                f"{path} 的任务 {tid} 既没有 accept.require_pass，也没有 accept.why。\n"
                f"   ⛔ 没有机器判据的单必须写明理由（why），否则「忘了写」和"
                f"「确实没法机检」在文件里长得一模一样。\n"
                f"   ⚠️ 模型自述「我做完了」**不是**验收标准。")
        # ⛔ 只读单点名闸 = **空守卫**。链条：readonly → 不建 worktree、不跑闸
        #    → 闸结果为空（gate_ok=None）→ 而「绿不绿」判的是「不等于失败」
        #    → 空 ≠ 失败 → 算绿 → 阶段报「全绿」退出 0。
        #    而 `--dry-run` 还会把这些闸名念给人听，**主动确认一个永不生效的守卫**。
        #    ⚠️ 修在加载时而不是改判绿的那行：合法的只读单（配 why）本来就该是
        #    None，动那里会误伤它们。
        if req and row.get("tools", "readonly") == "readonly":
            raise ConfigError(
                f"{path} 的任务 {tid} 是只读单（tools = readonly），"
                f"却点名了闸：{'、'.join(req)}。\n"
                f"   ⛔ 只读单不建 worktree、**不跑闸**——点名等于空守卫，"
                f"而结果会被记成绿。\n"
                f"   两条出路：改成 tools = 'implement'（真跑闸），"
                f"或改用 accept.why 写明这单为什么没有机器判据。\n"
                f"   ⚠️ 这是一条不变量，不是可配置策略——别放宽它。")
        tasks.append(Task(
            id=tid, tools=row.get("tools", "readonly"),
            needs=tuple(row.get("needs", [])), retries=int(row.get("retries", 1)),
            max_turns=int(row.get("max_turns", 0)),
            require_pass=req, accept_none_why=why))

    if not tasks:
        raise ConfigError(f"{path} 一个 [[task]] 都没有")

    ids = [t.id for t in tasks]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        raise ConfigError(f"{path} 有重复的任务 id：{'、'.join(dup)}")

    dangling = sorted({n for t in tasks for n in t.needs} - set(ids))
    if dangling:
        raise ConfigError(
            f"{path} 里这些依赖指向不存在的任务：{'、'.join(dangling)}\n"
            f"   ⚠️ 悬空依赖会让那几单**永远轮不到**，而循环只会静静地什么都不干。")

    cyc = _cycles(tuple(tasks))
    if cyc:
        raise ConfigError(
            f"{path} 的依赖里有环，这几单永远轮不到：{'、'.join(cyc)}")

    # 计划文件的位置是固定的：`<项目>/.devloop/plans/<阶段>.toml`，
    # 所以项目根 = 往上三层。`task_dir` 是相对**项目根**写的，不是相对计划文件。
    task_dir = Path(st["task_dir"])
    if not task_dir.is_absolute():
        task_dir = path.parent.parent.parent / st["task_dir"]
    missing = [t.id for t in tasks if not (task_dir / f"{t.id}.md").exists()]
    if missing:
        raise ConfigError(
            f"{task_dir} 下缺这些任务书：{'、'.join(missing)}\n"
            f"   ⚠️ 必须在花第一分钱之前查出来。")

    chain = bool(st.get("chain", False))
    parallel = int(st.get("parallel", 1))
    if parallel < 1:
        raise ConfigError(f"{path} 的 [stage] parallel 必须 ≥ 1，实得 {parallel}")
    if chain and parallel > 1:
        raise ConfigError(
            f"{path}：`chain = true` 与 `parallel = {parallel}` **互斥**。\n"
            f"   ⛔ 接力的语义是「后一单从前一单**已过闸的**产出起」，天然要求串行。\n"
            f"      两个一起开，后一单会从一个还没跑完的起点复制"
            f"——⚠️ 那不是性能问题，是正确性问题。\n"
            f"   要并行就把 chain 关掉；要接力就把 parallel 设回 1。")

    #  ⛔ **多派单的判据在这里执行，因为这是唯一「还没花一分钱」的时刻。**
    #     留到派单循环里再拒，前面的单已经跑掉了。判据见 devloop/fanout.py。
    #  ⚠️ 按 `tools` 分组各查一次：只读与写的判据完全不同（成本差 35 倍），
    #     而一份计划里两种任务可以并存。
    if parallel > 1:
        from . import fanout
        from .models import TaskSpec
        by_tools: dict[str, list] = {}
        for t in tasks:
            by_tools.setdefault(t.tools, []).append(
                TaskSpec.load(task_dir / f"{t.id}.md"))
        for tools, specs in by_tools.items():
            bad = fanout.check(specs, tools=tools, parallel=parallel)
            if bad:
                sep = "\n\n"
                raise ConfigError(
                    f"{path} 声明了 parallel = {parallel}，但这批任务（tools={tools}）"
                    f"不能并行：{sep}{sep.join(bad)}{sep}"
                    f"   判据全文：dispatch --why-parallel")

    return StagePlan(
        id=st["id"], goal=st["goal"], task_dir=task_dir, base=st["base"],
        budget=budget, tasks=tuple(tasks),
        require_pass=tuple(st.get("accept", {}).get("require_pass", [])),
        source=path, chain=chain, parallel=parallel)
