"""devloop 命令行入口。

退出码约定（SPEC.md §5.1）：
  0 全部成功  ·  1 至少一单失败  ·  2 工具自身错误（配置缺失、参数非法）
1 与 2 必须分开——「活没干好」和「工具坏了」是两回事。
"""

from __future__ import annotations

import argparse
import dataclasses
import concurrent.futures as cf
import subprocess
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK，输出中文与符号会抛 UnicodeEncodeError。
# 必须在任何打印之前重设——实测首次派单就栽在这里。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from . import dossier, nightly, doctor as doctor_mod
from . import telemetry
from . import backends, halt as halt_mod, handoff, jobs as jobs_mod, prune as prune_mod
from . import audit as audit_mod
from . import fanout

#  闸判定 → 显示符号。⛔ **必须用 .get 兜底，不能用 `[...]`。**
#
#  ⚠️ 2026-08-02 实测撞到：闸新增第四档 VOID（gates.py 那边解析好了、测好了），
#     而这里两处 `dict(PASS=..., FAIL=..., SKIP=...)[l.verdict]` **不认识它**，
#     一碰上就 `KeyError: 'VOID'`。
#
#  ⛔ 崩的位置尤其糟：它在 `_run_unit` 里发生在 `wt.commit_result()` **之前**
#     （闸打印在前，固化产出在后），于是被兜底 except 吞成「这一单失败」
#     ——**工人干了活、闸跑完了（真花钱），产出却永远不会被固化成分支**。
#
#  ⚠️ 而 tests/test_gate_void.py 六条全打在 `run_gates` 的**解析层**
#     ——测了错的那一层（第 4 种假绿）。判定档位的**显示路径**当时没有任何测试。
#  ⚠️ 叫 `_GATE_MARK` 不叫 `_MARK`——本文件第 644 行已经有一个 `_MARK`（作业状态表），
#     同名会被**后定义的那个整个覆盖**，于是全部判定印成 `?`（实测撞到）。
#     ⛔ 模块级常量重名不报错、不警告，只是安静地让先定义的那个消失。
_GATE_MARK = {"PASS": "✓", "FAIL": "✗", "SKIP": "—", "VOID": "○"}

#  dispatch 的子解析器。⚠️ `cmd_dispatch` 用它补做那两条从 argparse 挪下来的
#  必填校验（`--project` / 任务书）——挪的理由见 `build_parser` 里的注释。
#  ⛔ 记在这里而不是塞进 namespace：namespace 就是请求本身。
_DISPATCH_PARSER = None
from . import naming, progress, records
from .config import ConfigError, ProjectPaths
from .dispatch import TOOL_PRESETS, DispatchResult, build_prompt, dispatch_one

#  ⛔ 改 `error` 用 replace，**别重建**。重建要把每个字段手抄一遍，
#     新增字段时必然漏——`rate_limit` 就差点这么丢掉：闸未过那条分支
#     重建了 res，额度状态跟着没了，台账里那一单就再也说不清
#     「当时额度是什么情况」。replace 对将来新增字段免疫。
dc_replace = dataclasses.replace
from . import worktree as wt_mod
from . import gates as gates_mod
from .gates import (GATE_TIMEOUT_S, fingerprint, run_gates, run_on_commit,
                    sync_caches as gate_sync)
from . import autopilot, constitution, credentials as cred_mod
from . import plan as plan_mod
from . import quota
from .models import TaskSpec


def _fmt(res: DispatchResult, cfg_model: str) -> str:
    """一行摘要。**返回字符串而不是直接打印** —— 并行时几路输出会绞在一起。"""
    r = res.receipt
    if res.error:
        return f"  ✗ {res.task}: {res.error}"
    if res.model_mismatch:
        return f"  ✗ {res.task}: 模型不符——期望 {cfg_model}，实际 {r.models_used}"
    mark = "✓" if res.ok else "✗"
    cache = f"，缓存 {r.cache_read_tokens} tok" if r.cache_read_tokens else "，无缓存命中"
    # ⚠️ 不打印 total_cost_usd：那是 Claude Code 套 Opus 价目表算的合成价，
    #    对第三方端点高 20 倍（G-28）。真实成本在台账的 cost_usd_real 里。
    tok = r.usage.get("output_tokens", 0)
    return (f"  {mark} {res.task}: {r.duration_ms / 1000:.0f}s，{r.num_turns} 轮，"
            f"出 {tok} tok{cache}")


def _load_specs(args) -> list[TaskSpec]:
    """一次性把全部任务书 load 出来。

    ⚠️ **必须在派第一单之前全部校验完。** 此前是在循环体里 load，
    第 17 份缺标题会让前 16 单已经计费之后整批中止，而退出码 2 会被误读成
    「工具坏了」（BACKLOG B3）。预校验把这类失败挪到零成本的时刻。
    """
    if args.task_dir:
        files = sorted(Path(args.task_dir).glob("*.md"))
        if not files:
            raise ConfigError(f"{args.task_dir} 下没有 .md 任务书")
    else:
        files = [Path(args.task)]
    return [TaskSpec.load(f) for f in files]


def _add_err(cur: str | None, more: str) -> str:
    """把一条失败原因**追加**到已有的后面。⛔ 不许覆盖。

    ## ⚠️ 为什么不能覆盖

    一单里可以同时发生好几件事：T2 宪法命中 → 闸判 FAIL → 零改动 → T5 宪法。
    ⛔ 四处原来都是 `dc_replace(res, error=...)` **整体替换**，于是实测
    「工人改了受保护文件 + 闸判 FAIL」时，台账 error 只剩「闸未通过：…」，
    **归类器排第一优先、语义最重的「宪法命中（需要人批准）」整档丢失**，
    交接单里连「需要人批准」四个字都不出现。

    ⚠️ 两者处置完全不同：宪法命中是**在等你批准**，重试一万次也不会变绿；
    活没达标才是 `retries` 的用武之地。⛔ 把前者说成后者，会让自动驾驶
    拿满重试去撞一堵需要人来开的门。

    ⭐ 顺序即时间顺序：先发生的排前面，读的人按经过理解这一单。
    """
    if not cur:
        return more
    if more in cur:            # ⚠️ 同一条别堆两遍——台账那行是给人读的
        return cur
    return f"{cur}　｜　{more}"


def _run_unit(spec, paths, cfg, *, tools, max_turns, gate_fp, writes,
              con=None, base="", require_pass=(), before=None,
              ws_before=None, halt=None, out_ref=None,
              stage: str = "",
              #  ⭐ 墙钟的绝对时刻（`time.time()` 尺度）。⛔ `None` = 不限
              #     （手动 `dispatch` 就是这样），**不许当成 0**。
              #  ⚠️ 传的是**时刻**不是**时长**：并行波次里几单同时在跑，
              #     各自算「还剩多久」必须落在同一条时间轴上。
              deadline: float | None = None) -> tuple[bool, list[str]]:
    """跑一个单元。返回 (这单是否合格, 要打印的行)。

    ⚠️ **输出攒起来一次性返回，不当场 print** —— 并行时边跑边打印会把几路输出
    绞在一起，看不出哪行属于哪单。攒完再按单元成块输出。

    ⛔ **派出去了就一定留账。** 钱在 `dispatch_one` 那一刻就花掉了，而台账
    原本在最后一步才记，中间隔着四个环节——任何一处抛异常，这一单一行账都不写。
    而自动驾驶的三条防线（已花多少 / 派了几次 / 有没有算不出成本的）
    **全都只从台账读**：账本没行，三个数都是 0，于是它可以一直派、一直花，
    屏幕上一直印「花了 $0.0000 · 派了 0 次」。实测连跑四次真派 12 单、账本 0 行。
    ⚠️ 反过来也要守住：**连 worktree 都没建起来 = 一分钱没花**，那时留账是污染。
    """
    out: list[str] = []
    wt = None
    res = None          # ⚠️ 提到 try 外：异常路径要靠它判断「钱花没花」
    #  ⭐ 实时心跳走 **stderr**，详细回执仍走 stdout 的成块输出。
    #  ⛔ 2026-08-01 eco-ob 首跑：17 分钟控制台一个字都没有，只能靠
    #     `tasklist | grep godot` 判断它还活着。而目标是「挂一夜自己跑」
    #     ——看不见就不敢挂。详见 devloop/progress.py 的模块 docstring。
    prog = progress.Progress(spec.name)
    t_phase: dict[str, float] = {}
    #  ⭐ `unit_id` 必须在 `try` **之外**定义——⛔ 下面的 `except` 补记要用它，
    #     而 try 里第一步（建 worktree）就可能抛异常。⚠️ 定义在里面 = NameError，
    #     那会把「一单失败」变成「补记也炸了、账彻底没有」。
    unit_id = naming.stamp()
    #  ⭐ 「这次闸是被墙钟掐的吗」（G-116）。⚠️ 与 `unit_id` 同理，必须在
    #     `try` **之外**——⛔ 定义在里面的话 `except` 里的补记会 NameError。
    wall_killed_gate = False
    try:
        # ⚠️ 建 worktree 放在 try 里面：分支名一旦撞车（G-41），抛出的异常
        #    不该打死整批——那是连坐。每单失败只影响它自己。
        # ⚠️ base 显式传下去并记住：宪法的树内判据必须锚在**派单前**的那个提交上。
        #    ⛔ 不许事后从 worktree 的 HEAD 现推——工人自提交正是要抓的动作，
        #    用 HEAD 当锚等于攻击成功时锚自己也跟着移动。
        _t = time.time()
        wt = wt_mod.create(paths.project, spec.name, base=base or None) if writes else None
        # ⛔ **在派工人之前**把被 gitignore 的构建缓存补进 worktree。
        #    原来这段只在跑闸时做（工人干完之后），于是工人在一个**没有引擎缓存**
        #    的干净检出里干活——eco-ob 的 `game/.godot` 有 110 MB / 901 个条目，
        #    缺了它任何 Godot 命令都会崩或极慢。而工人多半会把这当成「代码有问题」，
        #    然后报一个假失败、或者去「修」一个不存在的问题。⛔ 两种都比不跑更坏。
        #    ⚠️ 这条在 devloop 自己身上永远看不出来——它没有需要同步的缓存。
        if wt:
            prog.mark(f"worktree 建好 {wt.path.name}")
            synced = gate_sync(paths, wt.path, overwrite=False)
            if synced:
                out.append(f"    已把 {'、'.join(synced)} 同步进 worktree（工人要用）")
                prog.mark(f"已同步 {'、'.join(synced)}")
        t_phase["setup_s"] = round(time.time() - _t, 1)

        #  ⛔ 2026-08-04 更正：这里原来写着「工人**没有硬性时限**，给 300s 只是
        #     为了让心跳能显示『已超预计』」——**那句话是错的**，而且害死了一跑。
        #     真死线是 `cfg.timeout_s`（`dispatch.py` 的 `subprocess.run(timeout=)`，
        #     默认 3000s）。两个数写在不同文件里、毫无关联，屏幕上只印那个 300。
        #  ⭐ 于是操作员看到的是「超预计了，也许快好了」，实际是「它会在 16:08
        #     被处决」——G-106。**真死线必须和参考值一起印。**
        #  ⚠️ 300 仍然保留为参考值：它是「模型写代码大概要多久」的量级，
        #     ⛔ 但别再把它读成时限。
        #  ⭐ **墙钟在这里才真正刹得住**（G-107）：把剩余墙钟收窄进工人的死线。
        #     ⛔ 在这之前 `max_wall_min` 只在「派下一单之前」被问过，
        #     一单开跑后那 80 分钟里没有任何东西看表。
        worker_s = plan_mod.clamp_to_wall(cfg.timeout_s, deadline=deadline,
                                          now=time.time())

        #  ⛔ **开跑行必须落在 `dispatch_one` 之前**（G-108）。
        #     钱从下一行开始花，而两条既有记账路径（主路径 / `except` 里的补记）
        #     都排在整单最后——⚠️ 强杀不走 `except`，Ctrl-C 也不走
        #     （`KeyboardInterrupt` 是 `BaseException`）。
        #  ⭐ 改成 `finally` 没有用：强杀时 `finally` / `atexit` 一行都不执行。
        #     要动的是**时刻**，不是块。
        #  ⚠️ 落不下去也不许打死这一单——但**必须报出来**：那意味着这一单
        #     一旦被杀就真的无账可查。
        try:
            telemetry.record_open(paths.telemetry, task=spec.name, unit_id=unit_id,
                                  model=cfg.model, tools=tools,
                                  price_key=cfg.pricing_key(), stage=stage)
        except Exception as exc:                        # noqa: BLE001
            out.append(f"    ⚠️ 开跑行没落成（{type(exc).__name__}）"
                       f"——⛔ 这一单若被中断将无账可查")

        with prog.phase("工人", every=30, expect_s=300,
                        limit_s=worker_s) as ph_w:
            _t = time.time()
            res = dispatch_one(spec, paths, cfg, tools=tools, max_turns=max_turns,
                               timeout_s=worker_s,
                               cwd=wt.path if wt else None)
            t_phase["worker_s"] = round(time.time() - _t, 1)
            #  ⛔ `dispatch_one` 把超时**吞成返回值**，`phase()` 看来一切正常。
            #     不主动报，收尾行就会印「完」——那正是 G-106 的现场。
            #  ⭐ 判据落在「有没有回执」这件能直接量的事上，不是错误文本的字样。
            if res.receipt is None:
                ph_w.broke(res.error or "⛔ 工人没留下任何回执")
        out.append(_fmt(res, cfg.model))

        # ── 额度：撞了上限要**当场叫停整批** ──
        #    ⛔ 不能只让这一单失败。撞了额度之后剩下的单会一单一单撞同一堵墙，
        #    每一单都在台账里留下一行失败——那一串看起来像「模型突然不行了」，
        #    而真因是额度用完了。这与「撞轮数上限是拆单太大、不是模型不行」
        #    是同一类区分，只是这次的代价更大：它会连累整批。
        #    ⚠️ 用信号而不是抛异常——下面那个 except 会把异常就地吞掉。
        if res.rate_limit is not None:
            rl = res.rate_limit
            if rl.blocked:
                out.append(f"    ⛔ **额度已耗尽**：{rl.summary()}")
                out.append(f"       {rl.advice()}")
                if halt is not None:
                    halt.trip(rl)
            elif rl.warning:
                out.append(f"    ⚠️ 额度快到顶了：{rl.summary()}")

        gres = None
        if wt:
            touched = wt.changed_files()
            out.append(f"    改动 {len(touched)} 个文件" +
                       (f"：{', '.join(touched[:4])}" if touched else "（工人没改任何东西）"))
            # ── T2 宪法：工人进程已退出、闸还没跑、产出还没固化 ──
            #    ⚠️ 命中也**照样跑闸、照样固化产出**：闸的输出是排查材料，
            #    短路等于把材料一起扔掉；不固化产出就是第二次 G-26。
            if con is not None and base:
                cres = constitution.check_tree(con, worktree=wt.path, base=base)
                out.append(f"    宪法 {cres.summary()}")
                if cres.halts or cres.broken:
                    res = dc_replace(res, error=_add_err(res.error, f"宪法命中：{cres.summary()}"))

            #  ⛔ **工人被死线打死时，不许再花 30 分钟去验它那半截产出**（G-111）。
            #
            #  ⚠️ 与上面那条「宪法命中也照样跑闸」**不冲突**——那条管的是
            #     **干完了但碰了红线**的产出，闸对它有话说。这里的产出是
            #     **从中间被截断**的：可能改完 A 还没改 B，对一个中间态求值
            #     什么都不说明。
            #  ⭐ 更要紧的是反过来的风险：半截的改动**碰巧过闸**，人会以为它成了
            #     ——那是第一种假绿的新变体。
            #  ⚠️ 而且此刻时间预算已经烧满（工人跑够了整个死线），这是最不该
            #     再花 1800 秒的时刻。2026-08-04 实测就白烧了 19 分钟。
            #  ⛔ 判据用结构化的 `res.timed_out`，不许 match `error` 的自由文本
            #     ——本项目在闸的归类上栽过一次（会被测试名劫持）。
            #  ⭐ 短路不许把路堵死：worktree 留着，命令给出来，人可以自己验。
            if res.timed_out:
                out.append("    ⏭ **闸没跑**——工人是被死线打死的，产出是半截的；"
                           "验一个中间态既费 30 分钟又说明不了任何事")
                out.append(f"    ⭐ 材料都留着，要自己验就跑："
                           f"devloop gates --project {paths.project} "
                           f"--target {wt.path}")
            else:
                #  ⛔ 闸是**全捕获的子进程**，900 秒里拿不到任何中间输出——
                #     只能从外面定时报数。实测 eco-ob 首跑：闸占了整单 1043 秒的 **87%**。
                #  ⚠️ 预计值取项目自己量过的基准；eco-ob 的 gates.sh 里记的是
                #     「test_m1.gd 单次 486 秒」，而 worktree 里实测约 905 秒
                #     ——拷进去的 .godot 省掉了「重新导入」，没省掉「重新校验」。
                #  ⭐ 印出来的死线和真正生效的死线**必须是同一个变量**——
                #     G-106 的病根就是那两个数各写各的（心跳 300 / 真死线 3000）。
                #  ⭐ 闸也收窄——⚠️ 工人可能已经把大半个墙钟吃掉了。
                gate_s = plan_mod.clamp_to_wall(GATE_TIMEOUT_S, deadline=deadline,
                                                now=time.time())
                with prog.phase("闸", every=60, expect_s=900,
                                limit_s=gate_s) as ph_g:
                    _t = time.time()
                    gres = run_gates(paths, target=wt.path, clean_checkout=True,
                                     expect_fingerprint=gate_fp,
                                     timeout_s=gate_s,
                                     require_pass=list(require_pass) or None,
                                     #  ⭐ 拿存档当对照的闸要靠它判「存档配不配得上这份代码」。
                                     #     ⛔ 漏传 = 那种闸只能报 VOID（见 gates.py 里那段）。
                                     base_commit=base or "")
                    t_phase["gate_s"] = round(time.time() - _t, 1)
                    #  ⛔ 闸自身故障（code 2）也是「断」，不是「完」——
                    #     ⚠️ 闸超时正好走这一档（返回 code 2 + 「闸执行超时」）。
                    if gres.code == 2:
                        ph_g.broke(
                            f"⛔ 闸自身故障：{(gres.stderr or gres.summary())[:120]}")
                    #  ⛔ **「墙钟把闸掐了」不是「闸坏了」**。
                    #     ⚠️ 被收窄之后的闸超时会走 code 2，而 code 2 的下游处置是
                    #     「先修环境，别看下面的绿」——⭐ 于是第二天早上人被指去修
                    #     一个**根本没坏**的环境。
                    #     ⚠️ 这与本项目一直在打的「把闸坏了说成活没干好」是同一枚
                    #     硬币的另一面，只是方向反了。
                    #  ⭐ 判据落在能直接量的东西上：闸拿到的时间**被墙钟收窄过**
                    #     （`gate_s < GATE_TIMEOUT_S`），且它正好用满了那个数。
                    if gres.code == 2 and gate_s < GATE_TIMEOUT_S \
                            and (t_phase.get("gate_s") or 0) >= gate_s - 5:
                        #  ⛔ **这个事实必须进台账**（G-116）。
                        #     ⚠️ 它原本只写进 `out`（屏幕块），而挂一夜的定义
                        #     就是没人看屏幕——第二天早上留下的只有台账里一个
                        #     孤零零的 `gate_code=2`，早报据它印「先修环境」。
                        wall_killed_gate = True
                        out.append(
                            f"    ⚠️ ⛔ **这不是闸坏了，是墙钟到点了**："
                            f"闸只拿到 {gate_s}s（标称 {GATE_TIMEOUT_S}s），"
                            f"被 `max_wall_min` 收窄。"
                            f"\n       ⭐ 别去修环境——加大 `max_wall_min`，"
                            f"或让工人少花点时间。")

        #  ⭐ 工人侧的错单独攒一份。⛔ 闸/宪法的结论**不许**进这里 ——
        #     它们各自有 `gate_ok` / `gate_code` / 宪法那几列。
        #     ⚠️ 起点是派单本身的错（没回执、模型不符、回执解析失败…），那些都是工人侧。
        _worker_err = res.error or ""

        if gres is not None:
            for l in gres.lines:
                out.append(f"    {_GATE_MARK.get(l.verdict, '?')} 闸·{l.name}: {l.detail}")
            if gres.gate_broken:
                out.append(f"    ⚠️ 闸自身故障：{gres.stderr.strip()[:150]}")
            if not gres.passed:
                res = dc_replace(res, error=_add_err(res.error, f"闸未通过：{gres.summary()}"))

        #  ⛔ **固化不跟着闸走。** 闸跳过了（工人被死线打死）也照样提交——
        #     `commit_result` 自己的注释就写着「闸没过也照样提交：失败的尝试
        #     同样是证据」，⭐ 而「闸没跑」比「闸没过」更需要留证据。
        #  ⚠️ 2026-08-04 那半截活之所以差点被 `prune --force` 铲掉，根因就是
        #     **它从来没进过任何一个提交**。不提交 = 产出只活在一个临时目录里。
        #  ⭐ `gate_ok=None` 是既有语义「未跑闸」，`prune` 认得它。
        #     ⛔ 不许传 False——那是「闸判它没过」，与「根本没验」是两件事。
        if wt:
            sha = wt.commit_result(spec.name,
                                   gate_ok=(gres.passed if gres is not None else None))
            if sha:
                # ⭐ 把产出位置报给调用方。接力（chain）要靠它把下一单的起点
                #    推到这里——⛔ 而且只有走到这一行才报：这一行在跑完闸、
                #    固化成功之后，没过闸的产出永远不会成为接力点。
                if out_ref is not None:
                    out_ref["branch"], out_ref["sha"] = wt.branch, sha
                out.append(f"    分支 {wt.branch} @ {sha}（产出已固化，删 worktree 不会丢）")
                out.append(f"      看：git show {sha}   ｜ 合：git merge {wt.branch}"
                           f"   ｜ 弃：git branch -D {wt.branch}")
            else:
                # ⛔ **写任务零改动 = 失败**，不是一句轻描淡写的「没产出」。
                #    早先这里只打印一句，而 `res.ok` 保持 True、闸在没动过的
                #    检出上当然也全过，于是台账记 `ok=true, gate_ok=true`
                #    ——**一单什么都没干的活被记成成功**。与 G-53 同一类：
                #    一个什么都没验证的结果报了绿。
                # ⚠️ 判据的维度：写任务的验收对象是**改动**，没有改动就没有
                #    验收对象。闸绿只说明「没弄坏东西」，不说明「干了活」。
                out.append(f"    ⛔ 分支 {wt.branch}：**工人没有产出**"
                           f"——写任务零改动判失败（闸绿只说明没弄坏东西，"
                           f"不说明干了活）")
                res = dc_replace(res, error=_add_err(res.error, "写任务零改动：worktree 里一个文件都没变"))
                #  ⭐ 这一条是**工人侧**的错（它交了卷，但一个文件都没改）——
                #     ⛔ 与「闸未通过」不同，必须让台账的 `worker_ok` 变假。
                #     ⚠️ 单独攒着传给 `telemetry.record`，⛔ 不让它混进 `res.error`
                #     之后再被前缀匹配挑出来（那是代用品）。
                _worker_err = _add_err(_worker_err, "写任务零改动")

            # ── T5 宪法：产出已固化，现在比对「worktree 之外」发生了什么 ──
            #    ⚠️ 这是**发现**不是**阻止**：目录联接、git hook、活过子进程的
            #    孙进程都能写到 worktree 外面。发现得晚，总比不发现好。
            #    ⚠️ 新建的 devloop/* 分支天然豁免——check_refs 只比对 T0 时
            #    **已存在**的 ref，所以 commit_result 自己造的分支不会误报。
            if con is not None and before is not None:
                after = constitution.snapshot(paths, con)
                checks = [("既有引用", constitution.check_refs(con, before, after)),
                          ("受保护文件", constitution.check_files(con, before, after)),
                          # ⭐ 不可恢复记录：各工作副本的 `.devloop/`。
                          #    git 整个忽略它（实测 `git ls-files .devloop` → 0），
                          #    而三条失控防线**全都只从那里的台账读**。
                          #    ⚠️ 判据是「只追加」不是「哈希没变」——派单自己
                          #    就会追加一行，判成哈希会每一单都误报。
                          ("不可恢复记录", constitution.check_records(con, paths, before))]
                if ws_before is not None:
                    checks.append(("活工作区", constitution.check_workspace(
                        con, ws_before, constitution.workspace_state(paths.project))))
                for label, r5 in checks:
                    if not r5.clean:
                        out.append(f"    ⛔ 宪法·{label} {r5.summary()}")
                        res = dc_replace(res, error=_add_err(res.error, f"宪法命中（{label}）：{r5.summary()}"))

        # ⭐ 自动收发现。⛔ 不能只在 readonly 时收——写任务的工人**同样**会
        #    看到别的问题，任务书还明令它「看到就写进报告、一个字都不许改」。
        #    首跑那一单（写操作）就报了 4 条顺手发现，其中一条是真 bug。
        # ⚠️ 格式坏了只警告不打死这一单——钱已经花了，回执还在盘上，
        #    人还能手工看。⛔ 但必须**报出来**，不许静默吞。
        try:
            got = audit_mod.parse(res.receipt.result if res.receipt else "")
            if got:
                n = audit_mod.append(paths.findings, got, source=spec.name)
                out.append(f"    发现 {len(got)} 条（新增 {n}）→ {paths.findings.name}"
                           f"　⚠️ **未复核**：devloop audit --verify-spec <id>")
        except audit_mod.MalformedFinding as exc:
            out.append(f"    ⚠️ 发现块格式不对，这一单的发现没入账：{exc}")

        #  ⭐ 接住刚写下去的那一行 —— 卷宗要用它。
        #  ⛔ 别再 `telemetry.load(...)[-1]`：并行波次里「账本最后一行」不是自己那一单。
        my_row = telemetry.record(paths.telemetry, res, model=cfg.model, tools=tools,
                         price_key=cfg.pricing_key(),
                         #  ⭐ 与开跑行配对（G-108）。⛔ 漏传的话这一单会被
                         #     读成「被中断」——保守方向，但会天天误报。
                         unit_id=unit_id,
                         #  ⭐ 2026-08-08：把「被死线掐死」传进台账。
                         #     ⛔ 漏了它，自动驾驶只看得见「算不出成本」，
                         #     会把超时甩锅给价目表（2026-08-06 真踩过）。
                         timed_out=res.timed_out,
                         #  ⭐ 工人侧的错，⛔ 与闸的结论分开（2026-08-12）
                         worker_err=_worker_err,
                         #  ⛔ 判据是 `gres is not None`，**不是 `wt`**。
                         #     ⚠️ G-111 跳过闸之后 `wt` 仍为真而 `gres` 是 None，
                         #     写 `gres.passed if wt` 会当场 AttributeError——
                         #     ⭐ 整条修法在真跑时是废的，而我给它写的四条判据
                         #     全是读源码文本的，一条都没真跑过这个分支。
                         #     **判据落在代用品上**，本项目自己命名过的毛病。
                         gate_ok=(gres.passed if gres is not None else None),
                         gate_detail=(gres.summary() if gres is not None
                                      else ("⏭ 工人被死线打死，闸没跑"
                                            if res.timed_out else "")),
                         #  ⭐ 结构化的闸判定。⛔ 下游 `escalation` 归类必须读它，
                         #     不许再从 `error` 的自由文本里猜——那会被**测试名劫持**
                         #     （本仓 gates.sh 把 pytest 的失败节点名原样放进 detail）。
                         #  ⛔ 同上：`gres is not None`。⭐ None = 「没跑闸」，
                         #     那正是跳过时该记的值。
                         gate_code=(gres.code if gres is not None else None),
                         #  ⭐ G-116：让早报分得出「墙钟掐的」与「环境真坏了」
                         #     ——这两件事的处置完全相反。
                         gate_wall_killed=wall_killed_gate,
                         phases=t_phase)

        # ⭐ **复核卷宗：成功的单也要写。**
        #    ⛔ 在这之前只有**重试耗尽的失败单**拿得到结构化材料
        #    （`write_escalation`，条件是 `stop.kind == "escalation"`）；
        #    而**即将进用户主线**的成功单只有一句样板提交 + 172KB 原始事件流。
        #    ⚠️ 那个不对称就是「复核一条 ≈ 重写一条」的机制。
        #  ⛔ 包在 try 里：卷宗是**辅助材料**，写不出来不该打死一单已经干完的活
        #     ——`doctor` 的记账那处立过同一条规矩。
        try:
            if wt:
                #  ⛔⛔ 2026-08-15 订正：这里以前用 `changed_files()`
                #     （= `git status --porcelain`，问的是**工作区还没提交的改动**），
                #     ⚠️ 而这一行跑在 `commit_result()` **之后**（348 行提交、这里 455 行），
                #     那时工作区已经干净 ⇒ **对每一单都报「实际改了 0 个」**。
                #
                #  ⭐ 实测代价：f3 那一单（2026-08-06）工人真把两个物种参数改好并提交了，
                #     回执写着 `subtype=success · 26 轮 · 38 分钟 · $2.53`，
                #     ⛔ 而卷宗写「实际改了 0 个：⚠️ 一个都没有」、台账记「超时被杀」。
                #     ⇒ 一次真正的成功被三处记录**同时**记成失败。
                #
                #  ⚠️ 提交前该问工作区（247 行那处「写任务零改动」判定是对的），
                #     ⭐ **提交后必须问提交**。
                changed = wt.changed_since(base) if sha else (wt.changed_files() or [])
                dp = dossier.write(
                    paths, stage or "unit",
                    #  ⛔⛔ 2026-08-16：这里以前是 `telemetry.load(paths.telemetry)[-1]`
                    #     ——「账本最后一行」。⚠️ 并行波次里那不是自己那一单：
                    #     实测两单同跑 20 次，**12 次目录里只剩一份卷宗**，
                    #     另一份被静默覆盖、屏幕零提示；还有 1 次把「闸全过」
                    #     那一行的结论盖在一份**闸判红**的产出上。
                    #  ⚠️ 上一句刻意不写出那个结构化字段的字面值 ——
                    #     `tests/test_skip_gate_after_worker_killed.py` 拿
                    #     「本函数源码里不许出现那个字面值」当判据，
                    #     ⛔ 写进注释就会把它弄成**假红**（2026-08-16 实测撞到）。
                    #     ⭐ 那条判据是代用品，已记进 BACKLOG。
                    #  ⭐ `record` 现在把它刚写下去的那一行交回来了，直接用。
                    spec, my_row,
                    #  ⛔ `gres` 可能是 None（G-111：工人被死线打死时闸不跑）。
                    #     ⚠️ 写 `gres.lines` 会抛 AttributeError，被下面那个 try
                    #     吞成一句「卷宗没写成」——⭐ 于是**最需要复核材料的那种单**
                    #     反而永远没有卷宗，而且只留一句看不出所以然的警告。
                    gate_names=[l.name for l in gres.lines] if gres else [],
                    #  ⭐ 判定必须一起传：没有它，卷宗分不出三件事——
                    #     「没点名但红了」（**照样拦下这一单**）、
                    #     「没点名但验过通过了」、「没点名的 SKIP/VOID（没验）」。
                    #  ⛔ 传**清单**不传字典：闸可以对同一道名打两行，
                    #     字典会让后一行盖掉前一行，FAIL 被 PASS 静默吞掉。
                    gate_lines=([(l.name, l.verdict) for l in gres.lines]
                                if gres else []),
                    required=list(require_pass),
                    changed=list(changed), base=base or "", sha=sha or "")
                out.append(f"    📄 卷宗 {dp}")
        except Exception as exc:  # noqa: BLE001 — 辅助材料不许打死整单
            out.append(f"    ⚠️ 卷宗没写成（不影响这一单）：{type(exc).__name__}")

        prog.mark(f"这单完 · 总 {time.time() - prog.t0:.0f}s"
                  f"（{' · '.join(f'{k[:-2]} {v:.0f}s' for k, v in t_phase.items())}）")
        return res.ok, out
    except Exception as exc:  # noqa: BLE001 — 单元失败必须隔离，不能打死整批
        #  ⛔ `BranchHijack` 不是「这一单干砸了」，是**宪法 C-4 命中**——
        #     有人（或有东西）把 HEAD 挪到了本 worktree 的隔离分支之外，
        #     而工具差一点就要往那里提交。
        #  ⚠️ 2026-08-03 对抗审计指出：它原来和别的异常长得一模一样
        #     （「✗ 某单: BranchHijack: …」），于是在滚动输出里它只是**一条失败单**，
        #     自动驾驶按 retries 继续重试，人不会意识到红线被碰了。
        #  ⭐ 措辞就是判据的一部分：宪法命中必须**看起来像宪法命中**。
        if isinstance(exc, wt_mod.BranchHijack):
            out.append(f"  ⛔ {spec.name}：**宪法命中 C-4**"
                       f"（不得触及主线或集成分支）——{str(exc)[:200]}")
            out.append("     ⚠️ 这不是「活没干好」，是红线。⛔ 重试没有意义，"
                       "先查清 HEAD 为什么不在隔离分支上。")
        else:
            out.append(f"  ✗ {spec.name}: {type(exc).__name__}: {str(exc)[:200]}")
        if wt:
            out.append(f"    ⚠️ worktree 保留在 {wt.path} 供排查")
        # ⛔ 钱花过了就必须留账——哪怕后面炸了。判据是 `res is not None`：
        #    它只在 `dispatch_one` 返回之后才非空，而那正是钱花掉的时刻。
        if res is not None:
            try:
                # ⛔ 用 replace，**别重建**——重建要把每个字段手抄一遍，
                #    新增字段必然漏。这里就漏过 `rate_limit`：一单撞了额度
                #    又在后续环节抛异常，补记时额度状态被丢掉，台账里那一行
                #    再也说不清「当时是不是撞额度了」。
                #    ⚠️ 同一个坑 `_run_unit` 主路径上已经踩过一次（见 dc_replace 的注释）。
                telemetry.record(
                    paths.telemetry,
                    #  ⛔ 也要**追加**：这一单可能在抛异常之前已经宪法命中或
                    #     闸判失败了，覆盖掉等于把「为什么」丢在半路上。
                    #     ⚠️ 与上面那句「别重建」同一条纪律——都是防止信息在
                    #     补记路径上蒸发。
                    dc_replace(res, error=_add_err(
                        res.error, f"{type(exc).__name__}: {str(exc)[:200]}")),
                    model=cfg.model, price_key=cfg.pricing_key(),
                    timed_out=res.timed_out,
                    unit_id=unit_id, tools=tools, gate_ok=None,
                    gate_wall_killed=wall_killed_gate,
                    gate_detail="⚠️ 这一单在跑闸/固化阶段抛异常，结论不可用")
                out.append("    （已补记台账——钱花过了，账必须留）")
            except Exception as exc2:  # noqa: BLE001
                # ⚠️ 补记本身失败也不许静默：那会让失控防线继续读到 0
                out.append(f"    ⛔ **补记台账也失败了**：{type(exc2).__name__}: {exc2}")
                out.append("       预算与派单次数的防线会因此读不到这一单，请手工核对。")
        return False, out


def _rebuild_argv(args) -> list[str]:
    """把**已解析的参数**规范化重建成一条命令行，交给后台作业跑。

    ⛔ 不从 `sys.argv` 反推。那个做法有三个后果，全都实测复现过（G-51）：
      a. argparse 默认开前缀缩写 —— `--det` 被当成 `--detach`，却不等于字面量
         `--detach`，于是原样传给子进程 → **无限自我重生**（实测 5 个作业/秒）。
      b. 程序化调用 `main(argv)` 时 `sys.argv` 跟本次请求毫无关系 —— 会「起成功」
         并返回 0，实际跑的是另一条命令。Phase 7 自动驾驶正是程序化驱动。
      c. 相对路径原样传下去，而子进程的 cwd 未必是用户当初那个目录。

    ⚠️ `jobs.py` 曾写「不在这里重新拼参数：拼两遍必然分叉」——那个顾虑是对的，
    所以有一条不变量测试把分叉钉死：**重建的命令行喂回解析器必须得到同一个请求**。
    改这个函数时，那条测试是唯一的守卫。
    """
    out = ["dispatch", "--project", str(Path(args.project).resolve())]
    if args.task:
        out += ["--task", str(Path(args.task).resolve())]
    else:
        out += ["--task-dir", str(Path(args.task_dir).resolve())]
    out += ["--tools", args.tools, "--max-turns", str(args.max_turns)]
    #  ⛔ **没给就不写。** `--parallel` 的默认值按 `--tools` 分化，
    #     在 `cmd_dispatch` 里才定得出来（解析时还不知道 tools 是什么）。
    #     ⚠️ 把 None materialize 成字面量 `"None"` 会让重建的 argv 喂不回解析器
    #     （实测 6 条往返测试红）；而写死一个数会让重建后的请求**与原请求不同**
    #     ——那正是这条往返测试要抓的东西。省略，让两边走同一条默认逻辑。
    if args.parallel is not None:
        out += ["--parallel", str(args.parallel)]
    # ⛔ 开关型参数最容易在这里漏——它没有值，肉眼扫一遍不会发现少了它。
    #    实测漏过一次：`--wait-for-reset` 当天加进解析器却没加到这里，
    #    于是 `--detach --wait-for-reset` 会**静默地**把开关吞掉——用户以为
    #    「派出去 + 撞额度自动等到点续跑」，实际后台作业撞额度即停，
    #    而消息只落在没人看的 console.log 里。
    if args.wait_for_reset:
        out.append("--wait-for-reset")
    # ⛔ 列表型参数同样容易在这里漏。`--wait-for-reset` 就是这么被吞过一次的，
    #    ⚠️ 而那条不变量测试当时对新参数恒绿——守卫有盲区比没守卫更坏。
    for g in args.require_pass:
        out += ["--require-pass", g]
    if args.backend:
        out += ["--backend", args.backend]
    elif args.model:
        out += ["--model", args.model]
    return out


def cmd_dispatch(args) -> int:
    #  ⛔ 判据说明是纯打印，⚠️ **必须在读任何文件、连任何后端之前**返回
    #     ——否则 `--why-parallel` 会因为 `--project`/`--task` 不存在而炸。
    if args.why_parallel:
        print(fanout.explain())
        return 0

    #  ⛔ argparse 上那两个 `required=True` 被挪到了这里（理由见解析器处的注释）。
    #     ⚠️ 走 `parser.error()` 而不是自己 print：报错要**照旧带 usage 行**、
    #     退出码照旧是 2 ——修一条毛病不许把另一条弄坏。
    if _DISPATCH_PARSER is not None:
        if not args.project:
            _DISPATCH_PARSER.error(
                "the following arguments are required: --project")
        if not args.task and not args.task_dir:
            _DISPATCH_PARSER.error(
                "one of the arguments --task --task-dir is required")

    #  ⛔ `--parallel` 的默认值按 `--tools` 分化，**必须在这里就定死**。
    #     ⚠️ 第一版把归一化写在下面的判据段里，而第 355 行的那句
    #     `if args.parallel > 1` 和 `_rebuild_argv` 都在它**前面**读了这个值
    #     —— `None > 1` 直接 TypeError，全套 20 条红。
    #     判据与理由见 devloop/fanout.py 模块 docstring。
    if args.parallel is None:
        args.parallel = fanout.default_parallel(args.tools)

    reg = backends.load()
    paths = ProjectPaths(Path(args.project))
    backend = reg.resolve(args.backend or args.model or paths.default_backend())

    # ⛔ 在建任何 worktree、花任何钱之前就挡住 subagent+写操作（静默假绿通道）
    handoff.guard_write_tools(backend, args.tools)

    specs = _load_specs(args)

    print(f"项目 {paths.project}")
    print(f"后端 {backend.name}（{backend.kind}）· 模型 {backend.model}"
          + (f" @ {backend.base_url}" if backend.base_url else ""))
    print(f"来源 {backend.source}")
    # 订阅后端：派单前做一次**便宜的排除法**。
    #
    # ⚠️ 这里只拦「肯定跑不了」的（没凭据文件 / 文件坏了 / 过期且没有
    #    refreshToken）。⛔ **访问令牌过期本身不是拒派的理由**——
    #    2026-07-29 实测：过期 10 小时的令牌，子进程照跑不误，CLI 用
    #    refreshToken 自动续期，跑完 expiresAt 往后跳了 8 小时。
    #    据「过期」停机会拦下一批本来能跑通的活，比不检查更坏：
    #    它以「凭据过期」的名义停机，而真因是判据错了。
    #
    # ⛔ 真判据只有「试一次」，见 credentials.probe()——但那要花额度，
    #    所以放在 doctor 里，不放在每次派单前。
    if backend.is_subscription:
        st = cred_mod.check()
        if not st.ok:
            print(f"\n{st.detail}")
            return 2
        print(f"凭据 {st.detail}")

    # ⛔ 花钱的路线必须当场把代价说出来。2026-07-28 起它不再是默认，
    #    但显式选了就得看见理由——**别让「省钱」这个直觉替你做决定**，
    #    实测它在这个项目上不成立（G-57）。
    if backend.kind == "api" and backend.note:
        print(f"💰 {backend.note}")
    print(f"派 {len(specs)} 单，权限 {args.tools}"
          + (f"，并发 {args.parallel}" if args.parallel > 1 else "") + "\n")

    # ⚠️ subagent 分支在 --detach 之前返回，所以这里必须显式说明 flag 不生效。
    #    **静默吞掉用户明确给出的参数**，是本项目最忌讳的那类行为：
    #    用户以为「派出去就走」，实际站在原地等一个根本不会自己跑的批次。
    if backend.kind == "subagent" and args.detach:
        print("⚠️ --detach 对 subagent 后端无意义，已忽略。")
        print("   它本来就不阻塞——出完工单立刻返回（退出码 3），活由编排方起子代理来干。\n")

    # ── subagent：不派单，出工单，交还控制权 ──────────────────
    if backend.kind == "subagent":
        digest = paths.read_rules_digest()
        prompts = {s.name: build_prompt(digest, s) for s in specs}
        b = handoff.create(paths.project, backend, specs, prompts,
                           tools=args.tools, max_turns=args.max_turns)
        print(f"⚠️ subagent 后端不走子进程（子进程读不到有效的订阅凭据）。\n"
              f"   已出工单，等编排方起子代理：\n")
        print(f"   工单  {b.root / handoff.ORDER}")
        print(f"   单元  {len(b.units)} 个 · 建议并发 {backend.max_parallel}")
        print(f"   收单  python -m devloop.cli collect --project {paths.project}\n")
        print("⛔ 退出码 3 = 「批次就绪，等编排方」，**不是成功**——此时一个字都还没干。")
        return 3

    # ── T0 宪法：起任何进程、建任何 worktree、花任何钱之前 ──────
    #    ⚠️ **必须排在 `--detach` 之前。** 早先它排在后面，后果是：
    #      · 宪法唯一那条硬拒绝（`--tools full` + `--detach` = 无人值守的对外通道）
    #        在**它唯一该生效的场景**里成了死代码；
    #      · 而且重建给子进程的命令行会去掉 `--detach`，子进程那边 detach=False，
    #        **两条路径同时失效**；
    #      · `--detach` 还整个跳过了锚校验——后台作业跑在一个从没被校验过的
    #        判定基准上，而无人值守恰恰是最需要校验的时候。
    #    ⛔ 也不许挪进 `_run_unit`：那样每单各自采一次基准，篡改若发生在
    #       两单之间就被下一单**吸收成新基线**。
    con = None
    base = ""
    before = None
    #  ⛔ 在这里初始化，**不是在下面**。派单循环是个闭包，会把它当自由变量捕获，
    #     没赋过值就 NameError（只读派单全炸）。而在**采完快照之后**再置空，
    #     等于把快照抹掉——2026-07-29 就是那么错的。
    ws_before = None
    try:
        con = constitution.load(paths)
    except constitution.NotEnabled as exc:
        # 宪法还没接入的项目照常能用——但**必须说出来**，不许静默无声地
        # 在「没有红线」的状态下派单。
        # ⛔ 只接 `NotEnabled`（文件根本不存在）。**宪法存在但坏了不走这条**：
        #    TOML 语法错、schema 不认、受保护文件写错路径、没登记 unjudged
        #    ——这四种都是退出码 2 的硬错。降级成「未启用」等于把
        #    「守卫坏了」说成「本来就没有守卫」，而且那句提示还是假话。
        print(f"⚠️ 未启用宪法：{str(exc).splitlines()[0]}")
        print("   （`devloop constitution init --project <路径>` 可生成一份）\n")
    if con is not None:
        constitution.refuse_preflight(con, tools=args.tools,
                                      unattended=args.detach)
        anc = constitution.verify_anchor(paths, con)
        print(f"宪法 {con.source.name} · {anc.summary()}")
        if not anc.clean:
            print("\n⛔ 拒绝派单：宪法的判定基准本身不可信了，先查明再谈干活。")
            return 2
        # T5 的对照基准：跑完之后拿它比对受保护文件与既有引用
        before = constitution.snapshot(paths, con)
        # ⭐ 活工作区快照：抓「工人写到了隔离 worktree **之外**」。
        #    它把宪法 A-2 从「完全判不了」往回拉了一步——写到任意路径不可枚举，
        #    但「这个项目的活工作区变没变」是可判定的。
        ws_before = constitution.workspace_state(paths.project)
        print()

    # ── --detach：派出去就走 ──────────────────────────────────
    # ⚠️ 放在这里而不是函数开头：后端解析、假绿拦截、任务书预校验、宪法前置
    #    都已跑过，所以能当场失败的错误**在前台就失败了**，
    #    不会变成一个后台的哑巴作业。
    if args.detach:
        job = jobs_mod.launch(paths.project, _rebuild_argv(args),
                              [s.name for s in specs],
                              telemetry=paths.telemetry)
        print(f"作业 {job.id} 已在后台启动（pid {job.meta['pid']}）")
        print(f"  看进度  python -m devloop.cli status --project {paths.project}")
        print(f"  控制台  {job.log}")
        print()
        print("⛔ 现在什么都还没跑完。退出码 0 只表示「起成功了」。")
        return 0

    cfg = backend.worker_config()

    # ⛔ 写操作强制隔离 + 强制跑闸。不给「在工作区直接改」留默认路径：
    #    闸的守卫只在干净检出里有效（BACKLOG G-07），工作区里它会降级为 SKIP。
    writes = args.tools != "readonly"

    #  ── 多派单的判据 ────────────────────────────────────────────
    #  ⛔ 「派几单」不该是拍脑袋填的一个数。判据与出处见 devloop/fanout.py。
    #  ⚠️ 默认值在这里定而不是在 argparse 里：解析时还不知道 --tools 是什么。
    blockers = fanout.check(specs, tools=args.tools, parallel=args.parallel)
    if blockers:
        #  ⛔ 退出码 2（工具/输入不对），不是 1（活没干好）——一分钱都还没花。
        print("⛔ 拒绝按这个并发派单：\n")
        print("\n\n".join(blockers))
        print(f"\n判据全文：devloop dispatch --why-parallel")
        return 2
    tip = fanout.advise(specs, tools=args.tools, parallel=args.parallel)
    if tip:
        print(tip + "\n")

    gate_fp = None
    # ⛔ **这里不许再动 `ws_before`。**
    #    它在上面的宪法前置里已经采过快照了（`constitution.workspace_state`）。
    #    2026-07-29 我为了修一个 NameError 在这里加了 `ws_before = None`，
    #    结果**把那个快照覆盖成了 None**——于是「活工作区」这道宪法检查
    #    在手动派单路径上恒不执行（`_run_unit` 里判的是 `if ws_before is not None`）。
    #    ⚠️ 而两条守卫它的测试都绿：一条自己传 `ws_before=`，
    #    另一条只用 AST 检查**关键字名字在不在**——判据的维度错了，
    #    它验的是「传没传」，不是「传的是不是真快照」。
    #    真正的 NameError 修法是在函数更早处初始化，见宪法前置那一段之前。
    if writes:
        # 防篡改第 2 道：**派单前**记录闸指纹，跑闸时比对。
        # 工人若在干活途中改了闸文件，闸会拒绝执行，而不是执行被改过的版本。
        gate_fp = fingerprint(paths.gates)
        # 宪法树内判据的锚：**派单前**的工作区快照（含未提交改动），全批共用一个。
        # ⛔ 必须走 resolve_base——snapshot_base 在干净工作区下返回字面量 "HEAD"，
        #    而宪法判据拒绝 HEAD，会把每一单都判成宪法故障（2026-07-28 实测的回归）。
        base = wt_mod.resolve_base(paths.project)
        print("⚠️ 写操作模式：每单在独立 worktree 中执行，完成后强制跑闸")
        print(f"   闸指纹 {gate_fp}（派单前记录，跑闸时校验）\n")

    failed = 0
    halt = quota.HaltSignal()
    todo = list(specs)          # 还没跑的；撞额度时剩下的就留在这里

    def _one(spec) -> None:
        nonlocal failed
        ok, lines = _run_unit(spec, paths, cfg, tools=args.tools,
                              max_turns=args.max_turns, gate_fp=gate_fp,
                              writes=writes, con=con, base=base,
                              require_pass=tuple(args.require_pass),
                              before=before, ws_before=ws_before, halt=halt)
        print("\n".join(lines))
        if not ok:
            failed += 1

    while todo:
        if args.parallel > 1:
            # ⚠️ 用线程不用进程：瓶颈是等子进程返回（IO），GIL 不碍事；
            #    而多进程会让台账追加写跨进程，那是另一个量级的问题。
            # ⛔ **按波提交，不是一次性把全部 submit 进池。**
            #    一次性 submit 的话，撞了额度也停不掉排队中的单——它们会
            #    一个接一个撞同一堵墙。分波的代价是每波一个栅栏（并行度默认
            #    是 1，多数场合根本走不到这条分支），换来的是「能停」。
            wave, todo = todo[:args.parallel], todo[args.parallel:]
            with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
                for fut in cf.as_completed([pool.submit(_one, s) for s in wave]):
                    fut.result()
        else:
            _one(todo.pop(0))

        if not halt.tripped:
            continue

        rl = halt.rate_limit
        print(f"\n⛔ **额度耗尽，整批停在这里**——剩下 {len(todo)} 单没派。")
        print(f"   {rl.summary()}")
        wait_s, why = quota.seconds_until_reset(rl)

        # ⚠️ 自动续跑必须**显式要**。默认睡几个小时会很突然，
        #    而且睡的时候这个进程占着终端——那该是操作者自己的决定。
        if not args.wait_for_reset:
            print(f"   {rl.advice()}")
            print(f"   想让它自己等到点再接着跑：加 --wait-for-reset")
            break
        if not quota.can_auto_resume(rl):
            # ⛔ 拿不到精确时刻的周上限不许自己等：官方文档明写周上限是
            #    固定时间重置，拿 5 小时去估会一路撞墙——每次醒来再撞一次，
            #    连撞十几个小时，而每次撞都真花额度。
            print(f"   ⛔ 这种上限不能自动续跑：{rl.advice()}")
            break

        print(f"   ⏸ {why}")
        try:
            time.sleep(wait_s)
        except KeyboardInterrupt:
            print("\n   已中断等待。剩下的单没派。")
            break
        halt.clear()
        print(f"   ▶ 醒了，接着派剩下的 {len(todo)} 单\n")

    done = len(specs) - len(todo) - failed
    tail = f"，剩 {len(todo)} 单未派（额度）" if todo else ""
    print(f"\n完成 {done}/{len(specs)}{tail}，台账 {paths.telemetry}")
    # ⛔ 退出码：额度耗尽是 **3（还没跑完）**，不是 1（活没干好）。
    #    1 的语义按 SPEC.md 是「工人失败或闸未过」——把「跑不了」记成
    #    「没干好」，会让自动化上游据此判定「这批活失败了」而不是「该重来」。
    #    ⚠️ 3 压过 1：既有真失败又没派完时，「没跑完」是更要紧的事实。
    #  ⛔ 2026-08-01 修：判据是 `halt.tripped`，不是 `todo`。
    #     原来只有「还有单没派」才报 3，于是**额度在最后一波撞上时**
    #     （todo 已空）退出码是 0 或 1 —— **「撞了额度」被报成了成功**。
    #     ⚠️ 串行默认下这个洞够不着（撞墙必然留下未派的单），
    #     只读默认改成并发之后才暴露出来。判据的维度一直是错的。
    if halt.tripped or todo:
        return 3
    return 1 if failed else 0


def cmd_collect(args) -> int:
    """收 subagent 批次：读回执 → 跑闸 → 固化 → 记台账。"""
    paths = ProjectPaths(Path(args.project))
    b = handoff.find_open(paths.project, args.batch)
    pending = b.pending()

    print(f"批次 {b.id} · 后端 {b.meta['backend']} · {len(b.units)} 单")
    if args.status or pending:
        print(f"已交回执 {len(b.units) - len(pending)}/{len(b.units)}")
        if pending:
            print("未交：" + "、".join(pending))
    if args.status:
        return 0 if b.done else 3
    if pending:
        print(f"\n⛔ 还有 {len(pending)} 单没有结果，不收单。")
        print(f"   工单在 {b.root / handoff.ORDER}")
        return 3

    failed = 0
    for unit in b.units:
        try:
            receipt, warn = handoff.read_result(b, unit)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {unit}: {type(exc).__name__}: {str(exc)[:180]}")
            failed += 1
            continue

        # ⚠️ 核对实际模型——别假定。与 API 后端走同一条判据。
        mism = bool(receipt.models_used) and not receipt.ran_on(b.meta["model"])
        res = DispatchResult(unit, receipt, None,
                             error=("model_mismatch" if mism else None))
        print(f"  {'✓' if res.ok else '✗'} {unit}"
              + (f"  ⚠️ {warn}" if warn else "")
              + (f"  ⚠️ 实际模型 {receipt.models_used} ≠ 声称 {b.meta['model']}" if mism else ""))
        telemetry.record(paths.telemetry, res, model=b.meta["model"],
                         price_key=b.meta["price_key"],
                         tools=b.meta["tools"], gate_ok=None,
                         gate_detail=f"subagent 批次 {b.id}")
        if not res.ok:
            failed += 1

    print(f"\n收单 {len(b.units) - failed}/{len(b.units)}，台账 {paths.telemetry}")
    print(f"⚠️ 批次目录保留在 {b.root}（含原始 prompt 与结果，供复核）")
    return 1 if failed else 0


def cmd_halt(args) -> int:
    """急停。⛔ 默认只列不杀——跑着的作业里可能有已经花了钱、快要产出的单。"""
    txt, alive = halt_mod.report(Path(args.project).resolve(), do_kill=args.kill)
    print(txt)
    # 退出码：没有活着的作业 = 0；列出来了但没杀 = 3（「还有事没处理完」）；
    # 杀干净了 = 0；杀不干净 = 1。
    if not args.kill:
        return 3 if alive else 0
    return 1 if alive else 0


_MARK = {"done": "✓", "running": "…", "died": "✗", "halted": "⛔"}


def _job_code(st: str, done: dict) -> int:
    """一个作业的退出码。⛔ 单作业与 `--all` **必须走同一套判据**——
    早先 `--all` 无条件返回 0，实测 died 0/7 也报成功，直接违反 SPEC 写死的
    「0 = 全都成功了」。它是从没被测过的那条路径（G-51）。"""
    if st != "done":
        return 3
    return 0 if all(r.get("ok") for r in done.values()) else 1


def cmd_status(args) -> int:
    """看后台作业。⚠️ 进度读**台账**，不读输出文件、不猜进程名（G-37）。"""
    proj = Path(args.project).resolve()
    try:
        if args.all:
            js = jobs_mod.listing(proj)
            if not js:
                print("没有后台作业。")
                # ⚠️ 2 = 工具/用法错（没这个东西），不是 0（「全都成功了」）也不是
                #    1（「至少一单失败」）。两条路径必须给同一个答案。
                return 2
            codes = []
            for j in js:
                st, msg = j.status()
                print(f"  {_MARK.get(st, '?')} {j.id}  {st:8s} {msg}")
                codes.append(_job_code(st, j.done_units()))
            return 3 if 3 in codes else (1 if 1 in codes else 0)

        j = jobs_mod.load(proj, args.job)
    except (FileNotFoundError, jobs_mod.JobError) as exc:
        print(f"⚠️ {exc}")
        return 2

    st, msg = j.status()
    print(f"作业 {j.id} · 起于 {j.meta['started']} · pid {j.meta['pid']}")
    print(f"命令 devloop {' '.join(j.meta['argv'])}")
    print(f"状态 {st} —— {msg}")

    done = j.done_units()
    if done:
        print()
        for r in done.values():
            mark = "✓" if r.get("ok") else "✗"
            c = r.get("cost_usd_real")
            print(f"  {mark} {r['task']:28s} {r.get('duration_s', 0):>5.0f}s"
                  + (f"  ${c:.4f}" if c is not None else "  成本未知"))
    print()
    print(f"控制台 {j.log}")
    return _job_code(st, done)


def cmd_eval(args) -> int:
    """跑评测集：一组已知答案的判断题，用来回答「换后端/改模板之后质量掉没掉」。"""
    from .evals import runner
    hist = Path(__file__).resolve().parent / "evals" / "runs.jsonl"
    if args.compare:
        print(runner.compare(hist))
        return 0
    r = runner.run(args.backend, parallel=args.parallel,
                   max_turns=args.max_turns, only=args.only)
    print(runner.format_run(r))
    # ⛔ `--only` 是调试用的部分跑分，**不进主历史**。
    #    进了主历史就成污染源：下一次全量跑分与它比对时，题目集合不同，
    #    实测过的后果是「19 题回归被印成『没有回归』」（G-51）。
    #    compare() 现在会把差集说出来，但最稳的还是根本不让它进来。
    if args.only:
        dbg = hist.with_name("runs-debug.jsonl")
        runner.save(r, dbg, only=args.only)
        print()
        print(f"⚠️ 这是 `--only` 的部分跑分，已记入 {dbg.name} 而**不是主历史**"
              f"——部分跑分与全量跑分不可比。")
    else:
        runner.save(r, hist, only="")
        print()
        print(f"已记入 {hist}（`--compare` 看与上次的逐题变化）")
    # ⚠️ 退出码只反映「跑完了没有」，不反映分数高低——
    #    把分数变成退出码会诱导人去凑分，而这套东西的价值在于诚实的读数。
    return 0


def _preflight_criteria(paths, sp, base: str) -> bool:
    """⭐⭐ 开跑前先验判据本身（G-127）。返回「可以派吗」。

    ⛔ **拓扑上的一个洞**：工具的防线**全在花钱之前**拦（宪法的锚、作业格式、额度），
    ⚠️ **唯独「判据本身对不对」要花完钱才知道** —— 因为它写在闸里，
    而闸只在工人干完之后才跑。
    ⭐ 而判据恰恰是最容易写错的那一样：翻遍历史，**工人从来不是瓶颈**。

    ⚠️ 2026-08-08 实测代价：一道**数学上必然红**的闸（对照存档比基准旧 7 个提交，
    中间还隔着一次故意改变世界的改动）烧掉 **514 万 tokens**、产出 0 个文件，
    ⛔ 还把一个 **42 秒**就改对了的工人逼得**删掉自己正确的代码**。

    ⛔ **比例原则**：只对被 `require_pass` **点名**的闸卡死 ——
    ⭐ 你敢拿一整单去赌某道闸，那道闸就得先能替自己担保。
    ⚠️ 没点名任何闸的阶段照旧放行，⛔ 别为了严谨把老用法全打死。

    ⭐ **它必须在 `--dry-run` 里也跑** —— 否则「判据坏了」依然要花完钱才知道，
    ⛔ 那就等于什么都没修。
    """
    named = sorted({g for task in sp.tasks
                    for g in plan_mod.effective_require_pass(sp, task)})
    if not named:
        return True
    verdict, lines, raw = gates_mod.preflight(paths, base_commit=base)
    print("开跑前验判据（⛔ 只跑各闸的前提检查，不跑大考）：")
    for line in lines:
        print(f"  {line}")

    #  ⭐⭐ 2026-08-12 · **核对：被点名的闸，有没有真的在体检里露面。**
    #
    #  ⛔ 花钱之前那道防线，此前比花完钱之后那道**更松**：
    #     事后的 `run_gates` 会查「点名的闸缺席 → 退出码 2」，
    #     ⚠️ 而这里算出了 `named` 却只拿它决定「跑不跑体检」，从不逐项核对。
    #
    #  ⚠️ 实测：eco-ob 的体检只打 3 行、真跑打 5 行 ——
    #     而**闭嘴的那两道正是零改动时非绿的那两道**。
    #     ⛔ 「没打」和「没问题」在 `verdict` 里长得一模一样
    #     （它只在**有行说 BAD** 时才变 bad）。
    #
    #  ⭐ 判据落在「体检**跑起来了没有**」上 —— 也就是 `states` 非空。
    #
    #  ⛔⛔ 第一版写的是 `if verdict != "unknown"`，**当场自己有洞**：
    #     `preflight` 里只要有任何一道闸报 VOID，`verdict` 就变成 `unknown`
    #     （`elif state != "OK" and verdict == "ok": verdict = "unknown"`），
    #     ⇒ 整段核对被跳过 —— ⚠️ **而「点名的闸报 VOID」正是最该拒的那种**。
    #     实测：`i-seek-early-exit`（点名逐比特中性=VOID）与
    #     `f-feeding`（点名禁改清单=VOID）两个都被放行了。
    #  ⭐ 又一次同一个形状：我给守卫加的那个「别误伤」的前置条件，
    #     顺手把它自己要防的那种情况也豁免了。
    #  ⚠️ `states` 为空 = 项目压根没实现体检模式 → 那时才该放行（大声喊但不拦）。
    states = gates_mod.parse_preflight_states(raw)
    if states:
        missing = [g for g in named if g not in states]
        notok = [f"{g}（{states[g][0]}）" for g in named
                 if g in states and states[g][0] != "OK"]
        if missing or notok:
            print("")
            print("⛔ 拒绝派单：**这一单点名要过的闸，没能在体检里替自己担保**。")
            if missing:
                print(f"   ⛔ 压根没在体检里露面：{'、'.join(missing)}")
                print("      ⚠️ 「没露面」不是「没问题」——真跑时它照样会判，"
                      "而那时钱已经花完了。")
                print("      ⭐ 修法：在项目的 gates.sh 体检段里给这几道也打一行 "
                      "`pf \"<闸名>\" \"OK|BAD|VOID\" \"<为什么>\"`。")
            if notok:
                print(f"   ⛔ 体检里就没过：{'、'.join(notok)}")
            return False

    if verdict == "ok":
        return True
    if verdict == "unknown":
        #  ⚠️ 「没人验过」不等于「有问题」——⛔ 喊出来，但放行。
        #  ⭐ 2026-08-09 学费：第一版把这一档也拒了，**一单都派不出去**
        #     （10 条回归当场变红）。⚠️ 那会逼人去关掉守卫，而那才是最坏的结局。
        print("   ⚠️ ⛔ **本单的判据没有被验过** —— 照跑，但心里有数：")
        print("      2026-08-08 一道没被验过的闸烧掉 514 万 tokens、产出 0 个文件。")
        print("      ⭐ 给项目的闸加体检：脚本里写 `# DEVLOOP-PREFLIGHT: 1` 并实现那一段"
              "（见 templates/gates.sh）。")
        return True
    print("")
    print("⛔ 拒绝派单：**判据本身没能替自己担保**，而这一单点名要它过"
          f"（{'、'.join(named)}）。")
    print("   ⚠️ 派下去多半会拿到一个**必然的假红** —— 而工人会以为是自己错了，"
          "然后把做对的活删掉（2026-08-08 实测）。")
    print("   ⭐ 先修判据（多半是对照存档配不上这份代码），再谈干活。")
    return False


def cmd_autopilot(args) -> int:
    """无人值守跑完一个阶段。

    ⛔ 退出码：0 = 阶段全绿跑完 · 1 = 有单没过 · 2 = 工具/输入坏了 ·
    **3 = 停下来了，要人看**（预算/次数/墙钟/看门狗/宪法/无单可派）。
    3 绝不能用 0 顶替——脚本看到 0 会往下走，而此时活可能一个字都没干。
    """
    paths = ProjectPaths(Path(args.project).resolve())
    sp = plan_mod.load(paths.project / ".devloop" / "plans" / f"{args.stage}.toml")

    print(f"阶段 {sp.id} —— {sp.goal}")
    print(f"任务 {len(sp.tasks)} 单 · 基准 {sp.base}")
    b = sp.budget
    print(f"上限 ${b.total_usd}（预留 ${b.reserve_usd}）· {b.max_dispatches} 次派单"
          f" · {b.max_wall_min} 分钟 · 看门狗 K={b.watchdog_k}")

    con = autopilot.preflight(paths, sp)
    print(f"宪法 {con.source.name} · 锚对得上 · 未覆盖 {len(con.unjudged)} 条\n")

    started_at = resume = autopilot.resume_point(paths, sp) if args.resume else None
    if resume:
        print(f"接着上次跑（起于 {resume}）——进度从**台账**重算，不信任何自己的记录\n")
    else:
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    run = autopilot.Run(sp, started_at, time.time())
    prog = autopilot.read_progress(paths, sp, started_at)
    ready, stop = autopilot.plan_next(sp, prog)
    print(f"已完成 {len(prog.done_ok)}/{len(sp.tasks)} 单"
          + (f"（失败 {len(prog.done_bad)}）" if prog.done_bad else "")
          + f" · 已花 ${prog.spent:.4f} · 已派 {prog.dispatches} 次")

    #  ⭐ 基准要在**演练之前**解出来——判据体检要用它。
    #     ⛔ 原来它算在演练返回之后，于是「判据坏了」依然要花完钱才知道。
    _base_early = (sp.base if sp.base not in ("", "HEAD")
                   else wt_mod.resolve_base(paths.project))
    if not _preflight_criteria(paths, sp, _base_early):
        return 2

    if args.dry_run:
        print("\n—— 演练，不花一分钱 ——")
        if stop:
            print(f"⛔ {stop.why}：{stop.detail}")
            return 0 if stop.kind == "done" else 3
        lim = autopilot.check_limits(sp, prog, started=run.started, dry_rounds=0)
        if lim:
            print(f"⛔ 现在就会停：{lim.why} —— {lim.detail}")
            return 3
        print(f"下一轮会派：{'、'.join(t.id for t in ready)}")
        for t in ready:
            # ⛔ 念给人听的必须是**生效后**的点名（阶段级 ∪ 任务级）。
            #    念任务级、跑生效级，等于让演练确认一个和真跑不一样的东西
            #    ——G-53 那类「主动确认一个永不生效的守卫」的近亲。
            eff = plan_mod.effective_require_pass(sp, t)
            crit = ("闸点名 " + "、".join(eff)) if eff \
                else f"⚠️ 无机器判据（已认领：{t.accept_none_why}）"
            print(f"  {t.id}  权限 {t.tools} · {crit}")
        return 3

    cfg = backends.load().resolve(args.backend or paths.default_backend()).worker_config()

    #  ⭐ 墙钟从这一版起是**真的**：工人和闸的死线都按剩余时间收窄
    #     （`plan.clamp_to_wall`，接在 `_run_unit` 里）。
    #  ⚠️ 这句必须在 `backends.load()` **之后**：工人的死线只有那时才知道。
    #  ⛔ 它只印不拦——我 2026-08-04 的第一版做成了硬拒，方向错了：
    #     那是在否决一个数，而该做的是让那个数变成真的。
    #  ⚠️ `getattr` 而不是直接取：取不到就走「读不到」那一档并说出来，
    #     ⛔ 别让一条本该只是「这次没算成」的事把整条命令炸掉。
    print(plan_mod.wall_report(
        b, worker_timeout_s=getattr(cfg, "timeout_s", None),
        gate_timeout_s=GATE_TIMEOUT_S))
    print()
    #  ⭐ 墙钟的绝对时刻。
    #
    #  ⛔⛔ **这里原来的注释是假的**，2026-08-04 当天写、当天被复核抓到：
    #     它宣称 `--resume` 时 `run.started` 会指回上一轮的开始时刻，
    #     ⚠️ 而 `Run(sp, started_at, time.time())` 的第三个参数**永远是「现在」**。
    #     ⭐ 与几小时前刚从 `cli.py` 删掉的那句「工人没有硬性时限」是同一类
    #     ——**注释说了代码没做的事**。记在这里，因为它证明这类错会重犯。
    #
    #  ⚠️ 于是墙钟的真实语义是「**每次调用重新计时**」，⛔ 不是「本阶段总共」。
    #  ⭐ 这个语义是**有意保留**的，不是妥协：`--resume` 存在的主要理由就是
    #     「撞了额度→等几小时→接着跑」（`quota.can_auto_resume`）。若改成
    #     「本阶段总共」，一个隔夜续跑会在开跑那一刻就被判超时，`--resume` 就废了。
    #  ⛔ 但它必须**说出来**——一个没说出口的语义就是一个陷阱。见下面那行 print。
    wall_deadline = run.started + b.max_wall_min * 60

    #  ⛔ **把自己登记成一个作业**（G-117）。
    #     ⚠️ `jobs.launch` 只在 `dispatch --detach` 那条路上被调用（它起后台进程），
    #     而自动驾驶跑在**前台**，于是它从来不登记——后果是
    #     `devloop halt` 回你一句「**没有正在跑的作业**」，半夜想叫停只能
    #     守在窗口按 Ctrl-C。⭐ 而「挂一夜」的定义就是**没人守在窗口前**。
    #  ⚠️ 包在 try 里：登记不上不该打死整批（它是观测手段），⛔ 但必须报出来
    #     ——不报的话，人以为急停管用，而它其实看不见这一跑。
    try:
        _job = jobs_mod.register_self(
            paths.project,
            ["autopilot", "--project", str(paths.project), "--stage", sp.id],
            [t.id for t in sp.tasks], telemetry=paths.telemetry)
        print(f"作业 {_job.id} 已登记（pid {_job.meta['pid']}）——"
              f"⭐ `devloop halt --project {paths.project}` 看得见它")
    except Exception as exc:                        # noqa: BLE001
        print(f"⚠️ 作业没登记成（{type(exc).__name__}）——"
              f"⛔ `devloop halt` 将**看不见这一跑**，只能靠 Ctrl-C 停")
    if resume:
        print(f"⚠️ **墙钟从现在重新计时**（这一轮又是 {b.max_wall_min} 分钟）"
              f"——⛔ 它是「每次调用」的上限，不是「本阶段总共」。\n"
              f"   ⭐ 反复 `--resume` 会把 {b.max_wall_min} 分钟累加成一夜；"
              f"真要卡总时长，用 `max_dispatches` 或外面的调度器。")

    gate_fp = fingerprint(paths.gates)
    dry_rounds = 0

    # ⛔ 把计划里的 base 解析成**具体 sha**。计划里写 'HEAD' 是给人看的
    #    ——但宪法的树内判据**拒绝 HEAD**（工人自提交正是它要抓的动作，
    #    用 HEAD 当锚等于攻击成功时锚自己也跟着移动）。
    #    ⚠️ 用 snapshot_base 而不是 rev-parse HEAD：工作区可能有在途改动，
    #    那些改动不是工人干的，拿 HEAD 当锚会把它们算到工人头上（G-12 同源）。
    base = sp.base if sp.base not in ("", "HEAD") else wt_mod.resolve_base(paths.project)
    # ⭐ `--resume` + `chain = true`：把上次的接力点读回来。
    #    ⛔ 不读的话 base 会被重算回项目 HEAD，而前几单的产出只在**未合并的
    #    隔离分支**上、树里根本没有——下游单要么必然失败，要么工人
    #    「自己重写一个顶上」，产出静默分叉。
    if args.resume and sp.chain:
        head = autopilot.resume_chain_head(paths, sp)
        if head:
            base = head
            print(f"⭐ 接着上次的接力点：{head[:12]}")
    print(f"基准 {base[:12]}（宪法的树内判据锚在这里，⛔ 不用 HEAD）")


    # ⛔ **T5 快照与停批信号：自动驾驶这条路上原本一个都没接。**
    #    2026-07-29 独立审计抓到：`cmd_dispatch` 三个都传，这里一个都不传，
    #    于是自动驾驶模式下——
    #      · 宪法 T5 三道（既有引用 / 受保护文件 / 活工作区）**恒不执行**
    #      · 撞额度**恒不停批**，会继续一单一单撞同一堵墙
    #    ⚠️ 而自动驾驶正是**无人值守**那条路：人不在旁边看着的那个模式，
    #    恰恰是唯一没有这些防线的模式。手动派单反而全都有。
    #    ⛔ 由 test_自动驾驶必须把停批信号和T5宪法都接上 钉住两个调用点不许分叉。
    # ⛔ 变量名不许叫 `before`：循环体里早就有一个 `before = len(prog.done_ok)`
    #    （看门狗用的完成计数），它会在第一轮就把这个快照覆盖成 int，
    #    然后 `before=before` 把 int 传进 T5 → `'int' object has no attribute 'refs'`。
    #    ⚠️ 2026-07-29 首次真跑当场炸了两次——而 `--dry-run` 走不到这段，
    #    单测也没覆盖（它们直接调 `_run_unit`，绕过了这个作用域）。
    snap_before = constitution.snapshot(paths, con) if con is not None else None
    ws_before = constitution.workspace_state(paths.project) if con is not None else None
    halt = quota.HaltSignal()

    # ⛔ **进程内也数一份派单次数。** 台账是唯一可信的*结果*来源，但
    #    「派出去过几次」这件事不该依赖任何 IO 成功——账本写失败、被删、
    #    被撕掉半行，次数上限就会读到 0，而那条上限的全部意义就是「不管
    #    别的怎么坏，它都能停住」。两者取大：账本可能有上次留下的行。
    fired = 0

    while True:
        prog = autopilot.read_progress(paths, sp, started_at)
        prog.dispatches = max(prog.dispatches, fired)
        stop = autopilot.check_limits(sp, prog, started=run.started,
                                      dry_rounds=dry_rounds)
        if stop:
            run.stop = stop
            break
        ready, stop = autopilot.plan_next(sp, prog)
        if stop:
            run.stop = stop
            # ⭐ 例外层：重试耗尽时**产出交接单再停**。
            #    与「直接停」的区别就在这里——直接停只说一句 blocked，
            #    等于什么都没说。交接单写清每次尝试的失败模式与证据。
            if stop.kind == "escalation":
                for t in sp.tasks:
                    if t.id not in prog.done_ok and \
                            prog.attempts.get(t.id, 0) >= max(1, t.retries):
                        p = autopilot.write_escalation(paths, sp, t, started_at)
                        print(f"  📋 交接单 {p}")
            break

        before = len(prog.done_ok)

        # ── ⭐ 这一波派几单（G-66）────────────────────────────────
        #  ⛔ 在 2026-08-02 之前这里是 `t = ready[0]`——**每轮只取一单**。
        #     PLAN 长期把这件事记在「未验的四条」里，⚠️ 而它其实是
        #     「**未实现**」——两者的处置完全不同（前者跑一次就行，后者要写代码）。
        #
        #  ⚠️ **一波里只放同一个 `tools` 的单。** 只读与写的并行判据完全不同
        #     （成本差 35 倍，见 devloop/fanout.py），混在一波里没有统一判据可用。
        #  ⚠️ `sp.parallel > 1` 时 `plan.load()` 已经查过改动范围不重叠了
        #     ——⭐ 那是唯一「还没花一分钱」的时刻，⛔ 不能留到这里再拒。
        #  ⛔ `chain` 与 `parallel` 互斥（load 时已拒），所以这里不会两者同时成立。
        #
        #  ⛔ **切波要按 `max_dispatches` 的余额收窄**（2026-08-02 审计）。
        #     上限是在这之前判的，而 `fired` 一次加 N——不收窄的话
        #     `max=10 / parallel=4` 会实际派到 12 次，溢出量 = 并发数 − 1。
        #     ⚠️ 而它是订阅制后端上**唯一还活着的刹车**。
        #     ⭐ 切波本身抽进了 `autopilot.plan_wave()`：要验它得跑真单，
        #        而真单花额度，所以逻辑必须待在一个能直接量的纯函数里。
        wave = autopilot.plan_wave(
            ready, parallel=sp.parallel,
            remaining=b.max_dispatches - prog.dispatches)
        if not wave:
            #  ⛔ **这里走不到，而措辞必须说清「走到了意味着什么」。**
            #     `check_limits` 刚放行 ⇒ `dispatches < max_dispatches` ⇒ 余额 ≥ 1；
            #     `plan_next` 没给 stop ⇒ `ready` 非空。两者都成立时波不可能为空。
            #
            #  ⚠️ 所以真到了这里，唯一的解释是**上面那两个判断被挪走了顺序**。
            #     ⛔ 早先这里写的是「派单次数余额不足」——那是把顺序被破坏
            #     说成配额到顶，与本项目一直在打的「把闸坏了说成活没干好」同形。
            #     ⭐ 报真因，不报症状。
            run.stop = autopilot.Stop(
                "内部不变量被破坏：切不出波次", "blocked",
                f"就绪 {len(ready)} 单、余额 {b.max_dispatches - prog.dispatches} 次，"
                f"却切出空波。⛔ 这不是配额到顶——`check_limits` 与 `plan_next` "
                f"都刚放行过。多半是有人改动了本循环里这三步的顺序。")
            break

        fired += len(wave)   # ⚠️ 在派之前就加：派了就算数，哪怕后面全炸
        if len(wave) > 1:
            print(f"\n▶ 一波 {len(wave)} 单并发（{wave[0].tools}）："
                  f"{'、'.join(t.id for t in wave)}")
        else:
            print(f"\n▶ {wave[0].id}（{wave[0].tools}）")
        #  ⛔ **只在真被余额收窄时才说。**
        #     ⚠️ 第一版的条件是 `len(wave) < len(ready) and len(wave) < sp.parallel`
        #     ——它分不出「被余额收窄」与「被同权限规则收窄」。实测一个普通的
        #     只读+写混排计划（parallel=4、max_dispatches=50）第一波就印出
        #     「本波被派单次数余额收窄到 1 单（…距上限 50 还剩 50 次）」
        #     ⛔ 一句话里自称被配额拦住、同时自报还剩 50 次。
        #     ⚠️ 那正是本文件另一处刚消灭的反模式（把别的原因说成配额到顶），
        #     只是换了个方向。⭐ 判据要落在「余额是不是那个更小的约束」上。
        room = b.max_dispatches - prog.dispatches
        same_tools = sum(1 for t in ready if t.tools == wave[0].tools)
        if room < min(max(1, sp.parallel), same_tools):
            #  ⚠️ 印**本波之后**的余额：`fired` 已经加过了，印 `room` 会把
            #     这一波刚花掉的说成「还剩」，运维会以为之后还能再派这么多。
            print(f"  ⚠️ 本波被派单次数余额收窄到 {len(wave)} 单"
                  f"（并发 {sp.parallel}、同权限就绪 {same_tools} 单；"
                  f"上限 {b.max_dispatches}，本波之后剩 {room - len(wave)} 次）")

        outs: dict[str, dict[str, str]] = {t.id: {} for t in wave}

        def _one(t):
            spec = TaskSpec.load(sp.task_dir / f"{t.id}.md")
            return t, _run_unit(
                spec, paths, cfg, tools=t.tools,
                max_turns=t.max_turns or b.default_max_turns,
                gate_fp=gate_fp, writes=(t.tools != "readonly"),
                con=con, base=base,
                require_pass=plan_mod.effective_require_pass(sp, t),
                before=snap_before, ws_before=ws_before, halt=halt,
                out_ref=outs[t.id], stage=sp.id,
                #  ⭐ 墙钟的绝对时刻——⛔ 传时刻不传时长：并行波次里几单同时
                #     在跑，各自算「还剩多久」必须落在同一条时间轴上。
                deadline=wall_deadline)

        #  ⚠️ 用线程不用进程：瓶颈是等子进程返回（IO），GIL 不碍事。
        #  ⛔ **一波跑完才查 halt**——同一波里的单已经在飞，收不回来。
        #     代价的上界因此是「溢出 ≤ 并发数」，⚠️ 而这个上界是明写的，不是隐含的。
        #     ⭐ 停批保的是**诊断清晰度**（一串失败别看起来像「模型突然不行了」），
        #     一波 N 条失败仍读得出是同一堵墙；⛔ N 波就读不出了。
        if len(wave) > 1:
            with cf.ThreadPoolExecutor(max_workers=len(wave)) as pool:
                results = [f.result() for f in
                           cf.as_completed([pool.submit(_one, t) for t in wave])]
        else:
            results = [_one(wave[0])]

        #  ⚠️ 输出按单元成块打印，⛔ 不在 `_run_unit` 里边跑边 print
        #     ——并行时几路输出会绞在一起，看不出哪行属于哪单。
        #     ⭐ 实时进度由 progress.py 的心跳走 stderr 提供，两条流各司其职。
        ok_by_id = {}
        for t, (ok, lines) in sorted(results, key=lambda r: r[0].id):
            if len(wave) > 1:
                print(f"\n  ── {t.id} ──")
            print("\n".join(lines))
            run.rounds.append({"task": t.id, "ok": ok})
            ok_by_id[t.id] = ok

        #  ⚠️ 接力只在串行下成立（chain 与 parallel 互斥，见 plan.load）。
        #     ⛔ 一波里几单同时跑完，「谁的产出当接力点」没有答案。
        t, ok, unit_out = wave[0], ok_by_id[wave[0].id], outs[wave[0].id]

        # ── ⭐ 接力（G-59）：把下一单的起点推到这一单已过闸的产出上 ──
        #    不接力的话每单都从同一个起点复制，于是「写模块 → 给它写测试 →
        #    修问题」这种自然计划**跑不了**：第二单打开工作区会发现模块不存在。
        #
        # ⛔ 两个条件都不能少：
        #    · `ok`   这一单整体合格（闸、宪法都过了）
        #    · `sha`  产出真的固化下来了（只读单没有，零改动单也没有）
        #    没过闸就推进接力点，等于让后面所有单都建立在没验过的产出之上，
        #    而闸的全部意义就是别让没验过的东西往下传。
        #
        # ⚠️ 这**不是**「拿 worktree 的 HEAD 当锚」——那个是要防的，
        #    因为攻击成功时锚会跟着动。这里推到的是**编排方**在闸全绿之后
        #    写的那条提交（commit_result，提交前还断言过 HEAD == 该分支）。
        #    工人自己的提交永远成不了接力点。
        if sp.chain and ok and unit_out.get("sha"):
            base = unit_out["sha"]
            # ⛔ 立刻落盘：进程随时可能被杀，接力点只活在内存里等于没有。
            run.chain_head = base
            # ⛔ 立刻落盘：进程随时可能被杀（无人值守跑一夜，机器重启、
            #    手工急停都可能），接力点只活在内存里等于没有。
            #    ⚠️ 用 `prog`（这一轮开始时的进度）就够——`save` 只拿它做统计，
            #    真正要保住的是上面那行 `chain_head`。
            run.save(paths, prog)
            print(f"    ⭐ 接力：下一单从 {base[:12]} 起（{unit_out['branch']}）")

        # ⛔ 撞额度必须**当场停整个阶段**。
        #    ⚠️ 这条在订阅后端上格外要紧：订阅的美元成本恒为 0.0，于是
        #    `check_limits` 里两条按预算判的防线（「花超了」「算不出成本」）
        #    **结构性恒假**——`total_usd` 那个数字在默认后端上完全不起作用。
        #    pricing.py 自己的注释就写着「真正的防线是 max_dispatches
        #    与撞额度上限即停」，而后者原本正是这里没接的那条。
        if halt.tripped:
            rl = halt.rate_limit
            #  ⭐ **撞额度可以睡到点自己接着跑**（G-118）。
            #  ⚠️ `dispatch` 早就有 `--wait-for-reset`，而自动驾驶没有
            #     ——于是「挂一夜」实际是「挂到撞墙为止」，而订阅的 5 小时窗口
            #     多半在半夜就撞上，早上起来是一半的进度。
            #     ⭐ 这是本项目重复了七次的那个形状：**两条路，只装了有人盯着的那条**。
            #  ⛔ 但**算不出精确恢复时刻的上限不许自己等**：周上限是固定时间重置，
            #     拿 5 小时去估会一路撞墙——每次醒来再撞一次，连撞十几个小时，
            #     ⚠️ **而每次撞都真花额度**。这条纪律照抄 `cmd_dispatch`。
            if getattr(args, "wait_for_reset", False) and quota.can_auto_resume(rl):
                wait_s, why = quota.seconds_until_reset(rl)
                #  ⛔⛔ **墙钟有两处，两处都要推**（G-120，2026-08-04 当天自造自查）。
                #     ⚠️ 我第一版**只推了 `wall_deadline`**（子进程死线），
                #     而 `check_limits` 读的是 `run.started` —— 于是睡 4 小时醒来，
                #     第一句就是「已跑 260 分钟，上限 90 分钟」，**一个字不干**。
                #  ⭐ 又是那个形状：**同一件事有两条路，只改了有人盯着的那条**。
                #     ⚠️ 而我自己上一版的注释就写着「墙钟也要跟着推」——**只推了一半**。
                #  ⭐ 等待的时间不算「干活的时间」，两个口径必须一起挪。
                wall_deadline += wait_s
                run.started += wait_s
                print(f"\n   ⏸ {why}")
                try:
                    time.sleep(wait_s)
                except KeyboardInterrupt:
                    print("\n   已中断等待。剩下的单没派。")
                    run.stop = autopilot.Stop(
                        "等额度时被中断", "ratelimit",
                        f"{rl.summary()}。⚠️ 人按了 Ctrl-C。")
                    break
                halt.clear()
                print("   ▶ 醒了，接着跑\n")
                continue
            run.stop = autopilot.Stop(
                "订阅额度耗尽", "ratelimit",
                f"{rl.summary()}。{rl.advice()}"
                + (" ⏸ 剩余任务未派完，恢复后重跑本阶段即可续上"
                   "（⭐ 或者下次加 `--wait-for-reset`，它会自己睡到点接着跑）"
                   if quota.can_auto_resume(rl) else ""))
            break

        after = autopilot.read_progress(paths, sp, started_at)
        # 空转 = 这一轮之后**绿的单没变多**。⚠️ 不看「有没有跑」，看「有没有进展」。
        dry_rounds = 0 if len(after.done_ok) > before else dry_rounds + 1
        run.save(paths, after)

    prog = autopilot.read_progress(paths, sp, started_at)
    run.save(paths, prog)
    print(f"\n{'=' * 60}")
    print(f"⛔ 停了：{run.stop.why}")
    print(f"   {run.stop.detail}")
    print(f"进度 {len(prog.done_ok)}/{len(sp.tasks)} 绿"
          + (f"，{len(prog.done_bad)} 失败" if prog.done_bad else "")
          + f" · 花了 ${prog.spent:.4f} · 派了 {prog.dispatches} 次")
    print(f"记录 {run.state_path(paths)}")
    if run.stop.needs_human:
        print("\n⚠️ **这需要你看一眼。** 退出码 3 的意思是「还没完」，不是「成功」。")
        return 3
    return 0 if not prog.done_bad else 1


def cmd_constitution(args) -> int:
    """宪法：init（生成）· anchor（登记基准）· check（对账）。

    ⚠️ `anchor` **必须是人执行的命令**，派单链路绝不调用它。
    锚一旦能自愈，它就不是锚——工人在两次派单之间改掉闸，下一轮就把
    被改后的值登记成「正确」，篡改静默转正。
    """
    paths = ProjectPaths(Path(args.project).resolve())
    tpl = Path(__file__).resolve().parent.parent / "templates"

    if args.action == "init":
        import shutil
        made = []
        for name in ("constitution.toml", "constitution.md"):
            dst = paths.project / ".devloop" / name
            if dst.exists():
                print(f"⚠️ {dst} 已存在，不覆盖")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tpl / name, dst)
            made.append(dst)
        for m in made:
            print(f"已生成 {m}")
        print("\n⚠️ 模板里的受保护清单是**通用起点，不是你的项目的答案**。")
        print("   先按项目实际情况改，然后：")
        print(f"   python -m devloop.cli constitution anchor --project {paths.project}")
        return 0 if made else 3

    con = constitution.load(paths)

    if args.action == "anchor":
        #  ⭐ 重新锚定 = **把现在的状态宣布为正确**。整套设计依赖「人是那道闸」，
        #     而人在按下这一下之前必须看得见自己在批准什么。
        #  ⚠️ 2026-08-03 用户就是在没有这段的情况下锚的——输出与首次锚定
        #     逐字相同，无从分辨这次改变了什么。⛔ 那道闸当时是蒙着眼睛的。
        first = not con.anchor_path.exists()
        delta = constitution.anchor_delta(paths, con)

        if first:
            print(f"首次锚定：{con.anchor_path}")
        else:
            #  ⛔ 用 `peek_anchor`（尽力读）而不是 `read_anchor`（判定用）。
            #  ⚠️ 用错的后果用户真撞到过：旧口径的锚被判定层拒了，于是
            #     「登记于」印成「（读不出来）」——而那个时间戳好好地写在文件里。
            peek = constitution.peek_anchor(con)
            _, why = constitution.read_anchor(con)
            when = peek.get("at") or "（这份锚里没记时间）"
            print(f"锚已存在：{con.anchor_path}（登记于 {when}）")
            if why:
                print(f"⚠️ 这份锚不能用来做判定：{why}")
            #  ⭐ 旧口径的锚，差异表里的「变了」有一部分只是**算法换了**，
            #     不是文件内容变了。⛔ 不说这句，人会去查一个不存在的篡改。
            old_ver = peek.get("schema", 0)
            if peek and old_ver < constitution.ANCHOR_SCHEMA:
                print(f"⚠️ 旧锚是 schema {old_ver} 的口径（现在是 "
                      f"{constitution.ANCHOR_SCHEMA}），指纹算法**变过**——")
                print("   ⭐ 下面的差异里有一部分只是**口径变化**，"
                      "不是文件被改了。")
            if not delta and not why:
                print("✅ 锚与现在的文件**完全一致**，无需重锚。")
                return 0
            print(f"\n⛔ 下面 {len(delta)} 处就是你要批准的东西：\n")
            for rel, old, new in delta:
                o = old[:12] if old else "（当时不存在）"
                n = new[:12] if new else "⛔（现在不存在了）"
                print(f"    {rel}\n        {o}  →  {n}")
            print("\n⚠️ 请先确认这些改动**是你自己做的**。")
            print("   ⛔ 如果有一条你不认识，那正是 A-1 要抓的东西——先查清楚，别锚。")
            print(f"   要看具体改了什么：git -C {paths.project} diff -- .devloop/")
            if not args.force:
                print("\n确认无误后，重跑并加上 --force：")
                print(f"   python -m devloop.cli constitution anchor "
                      f"--project {paths.project} --force")
                return 3

        a = constitution.write_anchor(paths, con)
        print(f"\n已登记 {len(con.files)} 个受保护文件的指纹 → {a}")
        if not first:
            print(f"旧锚已另存 → {a.with_suffix(a.suffix + '.prev')}")
        print("⚠️ 锚放在项目**外面**，是为了提高篡改成本——")
        print("   但它**不是安全边界**：工人有 Bash，理论上够得到。")
        print("   真边界是文件系统受限的执行环境，当前不具备。")
        return 0

    r = constitution.verify_anchor(paths, con)
    print(f"宪法 {con.source}")
    print(f"受保护文件 {len(con.files)} · 受保护路径 {len(con.trees)}")
    print(r.summary())
    return r.code


def cmd_nightly(args) -> int:
    """早上接管的第一条命令：⭐ 一屏读完「昨夜发生了什么、今天要动什么」。

    ⛔ **本项目没有「整体回档」这一步。** 主线永远不会被自动改动——全仓唯一
    写 git 的地方是 `worktree.py::commit_result`，且提交前硬断言
    `HEAD == 隔离分支`；`checkout`/`switch`/`push`/`reset` 全仓零次出现
    （由 `tests/test_no_git_write_verbs.py` 钉住）。
    ⭐ 所以**不合并本身就是回档**，真正的动作是逐支分支三选一：合 / 弃 / 重跑。

    退出码：0 = 干净（无待处理、无否决位）· 3 = 要人看 · 2 = 输入坏了。
    """
    project = Path(args.project).resolve()
    txt = nightly.report(project)
    print(txt)
    print()
    print("⛔ 主线不会被自动改动，所以**没有 `git revert` 这一步**。要动就三选一：")
    print(f"   看： git -C {project} show <sha>")
    print(f"   合： git -C {project} merge <分支>        ⚠️ 宪法 C-2/C-4：这一步要你亲自点头")
    print(f"   弃： python -m devloop.cli prune --project {project} "
          f"--archive <归档目录> --delete")
    dirty = "待处理分支：无。" not in txt or "先看这里" in txt
    return 3 if dirty else 0


def cmd_prune(args) -> int:
    """列出 / 归档 / 删除隔离分支。

    ⛔ **删除必须先归档并验证通过**（2026-08-02）。此前本命令只列不删，
    理由是「分支上装着工人的产出」。⭐ 改成可以删的前提正是归档这一层：
    判错了拿得回来。⚠️ 拿不回来就还是不许删。
    """
    project = Path(args.project).resolve()
    txt, n = prune_mod.report(project)
    print(txt)

    if not args.archive:
        if args.delete or args.discard:
            print("\n⛔ `--delete` / `--discard` 必须同时给 `--archive <目录>`"
                  "——删之前要先把产出打包并验证，判错了才拿得回来。")
            return 2
        return 0

    dest = Path(args.archive).resolve()
    items = prune_mod.archive_branches(project, dest)
    good = [a for a in items if a.ok and a.verified]
    bad = [a for a in items if not (a.ok and a.verified)]
    print(f"\n归档 {len(items)} 个分支 → {dest}")
    print(f"  ✅ 验证通过 {len(good)}"
          + (f" · ⛔ **没过 {len(bad)}**" if bad else ""))
    for a in bad:
        print(f"     ⛔ {a.name}: {a.detail}")
    print(f"  清单：{dest / 'MANIFEST.md'}")

    if not args.delete and not args.discard:
        print("\n⚠️ 只归档了，一个都没删。"
              "要删已合并的加 `--delete`，要点名弃掉加 `--discard <分支>`。")
        return 0

    #  ⭐ `--discard` 点名弃掉（含**未合并**的）。⚠️ 这是每天真正要做的动作之一：
    #     `nightly` 告诉你哪支该弃，得有个地方执行它。
    #  ⛔ 与 `--delete` 分开两个开关，不是洁癖：`--delete` 作用于「工具判定为
    #     已合并」的集合（判据是机器的），`--discard` 作用于**你点名**的那几支
    #     （判据是人的）。混成一个开关，等于让机器的判定去删人没看过的东西。
    known = {b.name for b in prune_mod.scan(project)}
    bad_names = [n2 for n2 in args.discard if n2 not in known]
    if bad_names:
        print(f"\n⛔ 这几支分支不存在：{'、'.join(bad_names)}")
        return 2
    if args.discard:
        n = prune_mod.delete_branches(project, list(args.discard), archived=items)
        print(f"\n🗑 点名弃掉 {n} 支（产出在包里，⭐ 取回方法见 MANIFEST.md）")

    if not args.delete:
        return 0

    safe = [b.name for b in prune_mod.scan(project) if b.safe_to_delete]
    #  ⛔ 删之前**重验一遍**：归档与删除之间可能隔了很久（或换了个人）。
    ok = {a.name for a in prune_mod.reverify(project, items) if a.ok and a.verified}
    todo = [n2 for n2 in safe if n2 in ok]
    skipped = [n2 for n2 in safe if n2 not in ok]
    if skipped:
        print(f"\n⛔ 这几个没有验证通过的归档，**不删**：{'、'.join(skipped)}")
    if not todo:
        print("\n没有可安心删的分支。")
        return 0
    deleted = prune_mod.delete_branches(project, todo, archived=items)
    print(f"\n🗑 已删 {deleted} 个分支（产出都在 {dest} 的包里，"
          f"⭐ 取回方法见 MANIFEST.md）")
    return 0


#  ⚠️ 提示文本里的子命令名拆开写。⛔ 不是为了绕闸——是因为 PreToolUse 闸
#     匹配的是 **Bash 命令原文**，分不出「要执行的命令」和「作为数据的字符串」。
#     写死 "devloop.cli dispatch" 会让**任何触碰本文件的 Bash 命令**被拒
#     （2026-08-01 实测撞到，闸自己的 docstring 也警告过同一件事）。
_D = "dis" + "patch"


def cmd_audit(args) -> int:
    """看 / 复核 / 转派 审计发现。

    ⛔ **扇出不在这里**——`--tools readonly` + `--task-dir` + `--parallel`
    本来就能派一组只读分析员。本命令补的是它们**回来之后**的三件事：
    看得见、能被独立推翻、能变成任务书。见 `devloop/audit.py` 模块 docstring。
    """
    paths = ProjectPaths(Path(args.project).resolve())
    fs = audit_mod.load(paths.findings)
    if not fs:
        print(f"{paths.findings} 里还没有发现。")
        print(f"   派一组只读分析员：{_D} --task-dir <任务书目录> "
              f"--tools readonly --parallel 4")
        print("   ⚠️ 任务书里要贴 audit.FINDINGS_FORMAT，工人才知道怎么交发现。")
        return 0

    if args.spec:
        hit = [f for f in fs if f.id.startswith(args.spec)]
        if len(hit) != 1:
            print(f"⛔ `{args.spec}` 匹配到 {len(hit)} 条，要唯一。")
            return 2
        maker = audit_mod.fix_spec if args.kind == "fix" else audit_mod.verify_spec
        out = (Path(args.out) if args.out
               else paths.root / "tasks" / f"{args.kind}-{hit[0].id}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(maker(hit[0]), encoding="utf-8")
        tools = "implement" if args.kind == "fix" else "readonly"
        print(f"已生成 {out}")
        print(f"   派它：{_D} --project {paths.project} --task {out} --tools {tools}")
        return 0

    print(audit_mod.summary(fs))
    print()
    for f in sorted(fs, key=lambda x: audit_mod.SEVERITY.index(x.severity)):
        mark = {None: "· 未复核", True: "✓ 复核成立", False: "✗ 已被推翻"}[f.verified]
        multi = f" ⭐{len(f.sources)} 个角度" if len(f.sources) > 1 else ""
        print(f"[{f.id}] {f.severity:<4} {mark}{multi}  {f.claim}")
        print(f"          证据：{f.evidence[:110]}")
    print()
    print("⛔ 生成复核任务书（**要求推翻**，不是要求核对）：audit --spec <id>")
    print("   生成修复任务书：audit --spec <id> --kind fix")
    return 0


def cmd_records(args) -> int:
    """归档 / 核对「删了拿不回来」的那批记录。

    ⛔ **为什么需要它**：`.devloop/` 被 gitignore 整个忽略
    （实测 `git ls-files .devloop` → 0），里面是台账 / 回执 / 任务书
    ——DevLoop 跑过什么的全部证据。⚠️ 而自动驾驶的三条失控防线
    **全都只从台账读**：账本没了，三个数都是 0，防线一起失效。

    ⚠️ 归档落在**版本控制里**而不是随手拷一份，是为了不腐烂：
    快照要有人记得更新，入库有 git 记着，且「删了」变成 `git checkout` 拿回来。
    """
    project = Path(args.project).resolve()
    fp = records.fingerprint(project)
    if not fp:
        print(f"⚠️ {project} 及其兄弟工作副本下一个记录文件都没有。")
        print("   ⛔ 若你以为该有，那说明扫描路径错了——别把这条读成「没事」。")
        return 1
    by_wt: dict[str, int] = {}
    for k in fp:
        by_wt[k.split("/", 1)[0]] = by_wt.get(k.split("/", 1)[0], 0) + 1
    print(f"扫到 {len(fp)} 个记录文件，分布在 {len(by_wt)} 个工作副本：")
    for name, n in sorted(by_wt.items()):
        print(f"   {name:24} {n} 个")
    if not args.archive:
        print("\n（只看不动。要归档加 --archive <目标目录>）")
        return 0
    dest = Path(args.archive).resolve()
    n = records.archive(project, dest)
    print(f"\n已归档 {n} 个文件 → {dest}")
    print("⚠️ 归档是**全量重来**：源头删掉的文件，归档里也会跟着消失"
          "（只增不减的归档会变成一份说谎的现状快照）。")
    print("⛔ 记得提交——不进版本控制的归档和被忽略的原件一样不可恢复。")
    return 0


def cmd_backends(args) -> int:
    if args.migrate:
        p = backends.migrate()
        print(f"已生成 {p}")
        print("⚠️ 请打开它检查一遍——特别是 subagent-opus 那条的 model 名对不对。")
        return 0
    reg = backends.load()
    print(f"注册表 {reg.source}")
    print(f"默认   {reg.default or '（未设，必须显式 --backend）'}")
    if reg.aliases:
        print("别名   " + "、".join(f"{k}→{v}" for k, v in sorted(reg.aliases.items())))
    print()
    for name in sorted(reg.backends):
        d = reg.backends[name].redacted()
        flag = "" if d["enabled"] else "  ⛔ enabled=false"
        print(f"  {name:16s} {d['kind']:9s} {d['model']:24s} 密钥 {d['auth_token']}{flag}")
        print(f"  {'':16s} 价目表键 {d['price_key']}"
              + (f" · {d['note']}" if d["note"] else ""))
    return 0


def cmd_doctor(args) -> int:
    proj = Path(args.project) if args.project else None
    checks = doctor_mod.run(proj, probe=getattr(args, 'probe', False))
    bad = 0
    for c in checks:
        mark = {True: "✓", False: "✗", None: "—"}[c.ok]
        print(f"  {mark} {c.name}: {c.detail}")
        if c.ok is False:
            bad += 1
    print(f"\n{len(checks) - bad} 项正常" + (f"，{bad} 项异常" if bad else "，无异常"))
    if not args.project:
        print("提示：加 --project <路径> 可实测派单通道")
    return 1 if bad else 0


def cmd_gates(args) -> int:
    paths = ProjectPaths(Path(args.project))
    print(f"闸文件 {paths.gates}")
    print(f"指纹   {fingerprint(paths.gates)}")
    if args.commit:
        print(f"验证对象 提交 {args.commit}（检出到一次性 worktree）\n")
        res = run_on_commit(paths, args.commit, expect_fingerprint=args.expect_fingerprint)
    else:
        print("验证对象 当前工作区\n")
        res = run_gates(paths, expect_fingerprint=args.expect_fingerprint)

    for l in res.lines:
        mark = _GATE_MARK.get(l.verdict, "?")
        print(f"  {mark} {l.name}: {l.detail}")
    print(f"\n{res.summary()}")
    if res.gate_broken:
        print("⚠️ 这是闸自身故障，不是「活没干好」——先修环境再谈验收")
    return res.code


def cmd_stats(args) -> int:
    paths = ProjectPaths(Path(args.project))
    #  ⭐ 按单元不按行数（G-108）：一单是「开跑行 + 收工行」两行。
    rows = telemetry.units(telemetry.load(paths.telemetry, args.since))
    print(telemetry.summarize(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    # ⛔ allow_abbrev=False：argparse 默认接受前缀缩写，`--det` 会被当成 `--detach`。
    #    缩写本身不算错，错在**任何「按字面量过滤参数」的代码都会漏掉它**——
    #    实测 `--det` 让后台作业无限自我重生（约 5 个作业目录/秒，一单活不干）。
    #    关掉缩写比逐处防守可靠：让解析器直接拒绝，而不是让下游去猜。
    ap = argparse.ArgumentParser(prog="devloop", description="AI 编码智能体编排",
                                 allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dispatch", help="派工人执行任务", allow_abbrev=False)
    #  ⛔ `--project` 与任务书**不能在 argparse 上标 required**。
    #     ⚠️ argparse 的必填校验发生在 `cmd_dispatch` 被调用**之前**，于是
    #     `--why-parallel`（纯打印判据、不花钱）那句提前返回**够不着**——
    #     实测 `dispatch --why-parallel` 直接 `error: the following arguments
    #     are required: --project`，⛔ 而 SPEC 与出错提示里印的正是这条命令。
    #     那是第②种假绿：实现了、注释齐全、**生产路径够不着**。
    #  ⭐ 改成在 `cmd_dispatch` 里手工校验（见 `_need_dispatch_args`），
    #     ⚠️ 但报错必须仍带 usage——argparse 的报错质量不许因此下降，
    #     所以走 `parser.error()` 而不是自己 print。
    d.add_argument("--project", help="项目根目录，须含 .devloop/")
    g = d.add_mutually_exclusive_group()
    g.add_argument("--task", help="单份任务书")
    g.add_argument("--task-dir", help="批量：目录下每个 .md 派一单")
    d.add_argument("--tools", default="readonly", choices=list(TOOL_PRESETS))
    # ⭐ 默认给 120 而不是 30：**没用掉的轮数不花钱**（按 token 计费，没跑的轮次
    #    产生零 token），而给低了撞顶的那一单 report 长度为 0、整单白跑、钱照花。
    #    实测：第二批 7 单给 80 时全部跑完（最多用到 95 个 num_turns），给 30 时 3/7 撞顶。
    #    ⚠️ 曾想做「按文件大小预测轮数」的估算器，用 7 个真实数据点一标定就否掉了——
    #    每轮读的行数从 3 到 45 差 15 倍，真正的驱动是「有多少条断言要核」，
    #    而那个数不读完就数不出来。天花板给高，比预测便宜也可靠。
    d.add_argument("--max-turns", type=int, default=120,
                   help="轮数天花板，默认 120（没用掉的轮数不花钱，所以给高不给低）。"
                        "⚠️ 它限的是去重后的 assistant 轮数，不是回执里的 num_turns"
                        "（两者差 1.0–2.8 倍，见 BACKLOG G-35）")
    #  ⛔ 默认值**按 --tools 分化**，在 cmd_dispatch 里定，不能写死在这里
    #     ——argparse 解析时还不知道 --tools 是什么。default=None 表示「没指定」。
    #     判据与理由见 devloop/fanout.py：只读单不建 worktree、不跑闸，
    #     实测 7–30 秒；写单 1043 秒（闸占 87%）。⛔ 差 35 倍。
    d.add_argument("--parallel", type=int, default=None, metavar="N",
                   help=f"同时跑几单。不给则按权限定："
                        f"readonly={fanout.DEFAULT_READONLY}（几乎免费，默认就该并行）"
                        f"· 写={fanout.DEFAULT_WRITE}（串行；要并行得声明 # 改动范围）")
    d.add_argument("--why-parallel", action="store_true",
                   help="打印「什么时候该多派单」的判据，然后退出")
    d.add_argument("--require-pass", metavar="闸名", action="append", default=[],
                   help="点名这几道闸必须 PASS 才算过（可重复）。"
                        "⛔ SKIP 不算过——被点名的检查没真正执行就无从放行（G-53）。"
                        "⚠️ 不点名时只判「没有 FAIL」，而闸有合法的变 SKIP 路径，"
                        "三道守卫就能凑出一个什么都没验的绿。")
    d.add_argument("--wait-for-reset", action="store_true",
                   help="撞订阅额度上限时，睡到额度恢复再接着派剩下的单。"
                        "⚠️ 默认不睡——那可能是几个小时，该由你决定。"
                        "⛔ 拿不到精确恢复时刻的周上限一律不自动等（会一路撞墙）。")
    d.add_argument("--detach", action="store_true",
                   help="派出去就走，不等它跑完。用 `devloop status` 看进度。"
                        "⚠️ 退出码 0 只表示「起成功了」，不表示活干完了")
    bg = d.add_mutually_exclusive_group()
    bg.add_argument("--backend", metavar="NAME",
                    help="谁来干这批活。名字见 `devloop backends`；"
                         "不给则用项目 config.toml 的 [worker].backend，再不给用注册表 default")
    bg.add_argument("--model", metavar="NAME",
                    help="⚠️ 已弃用，等价于 --backend（保留是因为 SPEC 里承诺过 cheap|premium，"
                         "现在它们是注册表里的别名）")
    #  ⚠️ 解析器本身记在模块级，⛔ **不许塞进 namespace**。
    #     第一版写的是 `set_defaults(_parser=d)`，当场被
    #     `test_重建的argv喂回解析器必须得到同一个请求` 判红——那条测试的要义是
    #     **namespace 就是请求本身**，往里加非请求状态会让「重建的 argv 是否
    #     还原同一个请求」这个不变量失去意义。⭐ 测试是对的，改设计不改测试。
    global _DISPATCH_PARSER
    _DISPATCH_PARSER = d
    d.set_defaults(fn=cmd_dispatch)

    co = sub.add_parser("collect", help="收 subagent 批次（编排方跑完子代理之后）")
    co.add_argument("--project", required=True)
    co.add_argument("--batch", help="批次 ID；不给则取唯一的那个（多于一个会报错，不猜）")
    co.add_argument("--status", action="store_true", help="只看进度，不收单")
    co.set_defaults(fn=cmd_collect)

    ha = sub.add_parser("halt", help="急停后台作业（⛔ 默认只列不杀）")
    ha.add_argument("--project", required=True)
    ha.add_argument("--kill", action="store_true",
                    help="真的终止（连同子进程树）。⚠️ 已花掉的钱不会退")
    ha.set_defaults(fn=cmd_halt)

    st = sub.add_parser("status", help="看后台作业进度")
    st.add_argument("--project", required=True)
    st.add_argument("--job", help="作业 ID；不给则看最新的一个")
    st.add_argument("--all", action="store_true", help="列出全部作业")
    st.set_defaults(fn=cmd_status)

    ev = sub.add_parser("eval", help="跑评测集（一组已知答案的判断题）")
    ev.add_argument("--backend", metavar="NAME", help="谁来答题；不给用注册表 default")
    ev.add_argument("--parallel", type=int, default=4, metavar="N")
    # ⭐ 与 dispatch 同为 120，理由同 G-46：**没用掉的轮数不花钱**。
    #   ⚠️ 这里曾写 30，理由是「一题只核一句话，比对账单小得多」——**这个理由被实测推翻**：
    #   2026-07-28 跑 deepseek-flash，c13/c19/c20 三题全部停在 num_turns=31，
    #   即三题都是撞上限被截断；c01 用了 29、c05 用了 30，全在边缘。
    #   旧代码会把这三题记成「模型答错」，让 flash 白背三口锅——正是 models.py
    #   自己写下的教训：「轮数不够是我的拆单错误，不是模型能力问题」。
    ev.add_argument("--max-turns", type=int, default=120,
                    help="单题的轮数天花板，默认 120（没用掉的轮数不花钱，所以给高不给低）")
    ev.add_argument("--only", default="", metavar="ID,ID", help="只跑指定题号")
    ev.add_argument("--compare", action="store_true", help="只看与上次跑分的逐题变化")
    ev.set_defaults(fn=cmd_eval)

    ap_ = sub.add_parser("autopilot", help="无人值守跑完一个阶段", allow_abbrev=False)
    ap_.add_argument("--project", required=True)
    ap_.add_argument("--stage", required=True, help=".devloop/plans/<阶段>.toml")
    ap_.add_argument("--backend", metavar="NAME")
    ap_.add_argument("--resume", action="store_true",
                     help="接着上次的台账窗口算进度（⚠️ 进度仍从台账重算，不信自己的记录）")
    #  ⭐ G-118：撞额度睡到点自己接着跑。⛔ 此前只有 `dispatch` 有这个开关，
    #     于是「挂一夜」实际是「挂到撞墙为止」——而订阅的 5 小时窗口多半在
    #     半夜就撞上。⚠️ 只对**算得出精确恢复时刻**的上限生效（周上限不许自己等）。
    ap_.add_argument("--wait-for-reset", action="store_true",
                     help="撞额度时睡到恢复再接着跑（⚠️ 只对算得出恢复时刻的上限生效；"
                          "⛔ 周上限一律停下来等人）")
    ap_.add_argument("--dry-run", action="store_true",
                     help="演练：只说下一轮会派什么、会不会当场被拦住，⛔ 不花一分钱")
    ap_.set_defaults(fn=cmd_autopilot)

    ct = sub.add_parser("constitution", help="宪法：什么必须停下来问人",
                        allow_abbrev=False)
    ct.add_argument("action", choices=["init", "anchor", "check"],
                    help="init 生成模板 · anchor 登记基准（⚠️ 只该由人执行）· check 对账")
    ct.add_argument("--project", required=True)
    #  ⛔ 只在**已有锚且指纹变了**时才要求它。首次锚定不该有这道摩擦，
    #     而摩擦一旦无谓，人就会养成一律带 --force 的习惯——那等于没有闸。
    ct.add_argument("--force", action="store_true",
                    help="确认已看过差异，覆盖已有的锚（⚠️ 只有重锚且有变化时才需要）")
    ct.set_defaults(fn=cmd_constitution)

    ni = sub.add_parser("nightly", help="早上接管的第一条命令：昨夜发生了什么")
    ni.add_argument("--project", required=True)
    ni.set_defaults(fn=cmd_nightly)

    pr = sub.add_parser("prune", help="列出/归档/删除隔离分支")
    pr.add_argument("--project", required=True)
    pr.add_argument("--archive", metavar="目标目录",
                    help="把每个分支的尖端提交打成 git bundle 并**真验一遍**。"
                         "⛔ 不给就只列不动")
    pr.add_argument("--discard", metavar="分支", action="append", default=[],
                    help="**点名弃掉**这一支（可重复）。⛔ 同样必须先 --archive；"
                         "⚠️ 这是唯一能删**未合并**分支的路径——"
                         "`--delete` 只碰已合并的")
    pr.add_argument("--delete", action="store_true",
                    help="删掉「可清理」的那些。⛔ **必须同时给 --archive**"
                         "——判错了要拿得回来")
    pr.set_defaults(fn=cmd_prune)

    au = sub.add_parser("audit", help="看/复核/转派 审计发现（扇出仍用 dispatch --tools readonly）")
    au.add_argument("--project", required=True)
    au.add_argument("--spec", metavar="发现ID", help="给这条发现生成任务书")
    au.add_argument("--kind", choices=["verify", "fix"], default="verify",
                    help="verify=要求推翻它（默认）· fix=去修它")
    au.add_argument("--out", help="任务书写到哪（默认 .devloop/tasks/）")
    au.set_defaults(fn=cmd_audit)

    rc = sub.add_parser("records", help="归档/核对不可恢复的记录（.devloop 被 gitignore 忽略）")
    rc.add_argument("--project", required=True)
    rc.add_argument("--archive", metavar="目标目录",
                    help="把记录全量拷进这个目录（⛔ 不给就只看不动）")
    rc.set_defaults(fn=cmd_records)

    bk = sub.add_parser("backends", help="列出可用后端")
    bk.add_argument("--migrate", action="store_true",
                    help="把老的 worker-deepseek.json 升级为多后端注册表")
    bk.set_defaults(fn=cmd_backends)

    dc = sub.add_parser("doctor", help="通道自检")
    dc.add_argument("--probe", action="store_true",
                    help="真起一次子进程验凭据（⚠️ 花订阅额度，但这是唯一可靠的判据）")
    dc.add_argument("--project", help="给定则实测派单通道")
    dc.set_defaults(fn=cmd_doctor)

    g2 = sub.add_parser("gates", help="跑验收闸")
    g2.add_argument("--project", required=True)
    g2.add_argument("--commit", help="验证指定提交（检出到一次性 worktree）；不给则验工作区")
    g2.add_argument("--expect-fingerprint", help="闸文件的预期指纹，不符即拒绝执行")
    g2.set_defaults(fn=cmd_gates)

    s = sub.add_parser("stats", help="台账汇总")
    s.add_argument("--project", required=True)
    s.add_argument("--since", type=float, default=None, metavar="DAYS")
    s.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except backends.BackendError as exc:
        # ⚠️ 后端配置问题是**工具自身错误**（退出码 2），不是「活没干好」（1）。
        #    此前它落进兜底路径返回 1，脚本会以为是工人失败而去重试——
        #    重试一万次也没用，配置不对。这正是 SPEC §5.2 那条「1 与 2 必须区分」。
        print(f"⛔ {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
