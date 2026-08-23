"""每单一行 JSONL 台账。

不判分，只记账（R-C 决议）：不预设「省 X% 算成功」，攒够样本让趋势自己说话。
真正要盯的信号是二元的——返工成本有没有吃掉省下的钱。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from .dispatch import DispatchResult
from .pricing import price_source, real_cost_usd
from . import quota as _quota


#  ⭐ 台账行的两种形态。⛔ 老行**两个都没有**——那是「一单完整记完」的旧格式，
#     读的时候按 `close` 处理（见 `units`）。
_OPEN, _CLOSE = "open", "close"


def record_open(path: Path, *, task: str, unit_id: str, model: str, tools: str,
                price_key: str = "", stage: str = "") -> None:
    """**开跑之前**先落一行「这一单开始花钱了」（G-108）。

    ## ⛔ 为什么记账时刻必须提前，而不是把补记写进 `finally`

    2026-08-04：一单烧掉 50 分钟订阅额度，台账**一行都没有**。
    两条记账路径都排在整单最后：主路径在工人+闸+固化全做完之后，
    补记在 `except Exception` 里。⚠️ 而

    - 强杀（外部 `timeout`、`taskkill /F`、关机）→ `except` / `finally` /
      `atexit` **一行都不执行**；
    - Ctrl-C → `KeyboardInterrupt` 是 `BaseException`，`except Exception`
      **接不住**——⭐ 而它正是人停掉夜跑最常用的方式。

    ⛔ 所以改成 `finally` 没有用。⭐ 要动的是**时刻**：钱一开始花就先落一行。

    ## ⚠️ 这一行**不许带任何结论**

    没有 `ok`、没有 `gate_code`、没有成本。它只说一件事：
    「有一单开始花钱了」。⛔ 带上乐观的默认值就成了假绿——一单被杀在半路，
    账上却写着 `ok=true`。

    ## ⭐ 配对靠 `unit_id`，⛔ 不许靠任务名

    同一个任务会重试多次，并行波次里还会有几单同时在跑。
    """
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "event": _OPEN,
        "unit_id": unit_id,
        "task": task,
        "model": model,
        "tools": tools,
        "price_key": price_key or model,
        "stage": stage,
        #  ⚠️ 给人看的：翻到这一行的人多半正在问「昨晚出了什么事」。
        "note": "⛔ 这一单开始花钱了，还没有结果。"
                "只有这一行没有 close 行 = **被中断**（进程被杀 / Ctrl-C / 断电）。",
    }
    _append(path, json.dumps(row, ensure_ascii=False) + "\n")


def units(rows: list[dict]) -> list[dict]:
    """把原始行收成**一单一条**。⭐ 一切按单元统计的地方都要先过它。

    | 输入 | 输出 |
    |---|---|
    | 开跑行 + 收工行（同 `unit_id`） | 一条，取收工行 |
    | 只有开跑行 | 一条，带 `interrupted=True` |
    | 老行（没有 `event` 字段） | 一条，原样 |

    ⛔ **不许按行数统计**：开跑+收工两行，按行数算「今天派了几次单」
    会把一单数成两次——计划里写最多派 2 次，跑完**一单**就被自己的
    刹车停掉，活少干一半。
    """
    closed = {r.get("unit_id") for r in rows
              if r.get("event") == _CLOSE and r.get("unit_id")}
    out: list[dict] = []
    for r in rows:
        ev = r.get("event")
        if ev == _OPEN:
            if r.get("unit_id") in closed:
                continue                       # 收工行会替它出场
            out.append({**r, "interrupted": True})
        else:
            out.append(r)                      # 收工行与老行都原样
    return out


def record(path: Path, result: DispatchResult, *, model: str, tools: str,
           gate_ok: bool | None = None, gate_detail: str = "",
           gate_code: int | None = None, unit_id: str = "",
           gate_wall_killed: bool | None = None,
           price_key: str = "", phases: dict[str, float] | None = None,
           timed_out: bool = False, worker_err: str = "") -> dict:
    """记一单。

    ⛔ **`model` 与 `price_key` 是两件事，别再挤进一个参数。**
    `model` 记的是真跑了什么（台账要能回答「谁干的」），`price_key` 是查价目表
    用的键。此前两者共用 `model` 一个参数，于是订阅单拿 `claude-opus-4-7`
    去查价目表查不到，成本记成 None——见 test_订阅派单记的账里成本必须是零而不是未知。

    ⚠️ **工人成败与闸成败必须分开记**：
    「工人跑完了」≠「这单合格」，而「这单不合格」≠「模型不行」。
    失败可能来自四个方向——模型能力、任务书判据错误、工具/环境、闸误报。
    混在一个 ok 字段里，就永远回答不了「**工人这一档够不够用**」这个核心问题。
    ⚠️ 2026-08-15：原文写的是「便宜模型够不够用」。便宜模型已跳过（D-COST-01），
    ⛔ 但这条道理**跟工人是谁完全无关** —— 工人换成 opus 之后照样要分开记，
    否则「闸红了」会被读成「工人不行」（实测：2026-08 那 15 单就是这么被读错的）。
    （首个写任务实测：工人执行完全正确，错的是任务书的前提。）
    """
    r = result.receipt
    pk = price_key or model          # 缺省退回模型名：绝大多数后端没设 price_key
    #  ⭐⭐ 2026-08-12 · **`worker_ok` 不许再读 `result.error`。**
    #
    #  ⛔ 那是个混装字段：`cli.py::_run_unit` 把「闸未通过：…」「宪法命中：…」
    #     全都拼进去（`_add_err`）。⇒ **闸一红，`worker_ok` 必假** ——
    #     而这个函数自己的说明里就写着「工人成败与闸成败必须分开记」，
    #     ⚠️ 代码正在做它自己禁止的事。
    #
    #  ⭐ 实测代价：2026-08-05 f2 第二单，台账 error 里**只有**一句「闸未通过」、
    #     没有任何工人侧的错，`worker_ok` 照样 false。
    #     ⇒ 「连败 4 单」这个数字里，有几单其实是闸的锅、不是工人的锅，
    #     ⛔ 而这正是「**工人这一档够不够用**」那个核心问题要的答案。
    #
    #  ⭐ 现在只看**工人侧**的五样：有没有回执 / 回执自己报没报错 /
    #     跑的是不是约定的模型 / 是不是真被死线打死没交卷 /
    #     ⭐ 以及调用方**显式传进来**的工人侧错（`worker_err`）。
    #
    #  ⚠️ 最后那一项为什么必须是显式参数、⛔ 不许从 `result.error` 里挑：
    #     「写任务零改动」确实是**工人的锅**（它交了卷但一个文件没改），
    #     必须让 `worker_ok` 变假；而「闸未通过」是**闸的结论**，不许。
    #     两者今天都躺在同一个 `result.error` 字符串里 ——
    #     ⛔ 靠前缀匹配去分是代用品，本仓为「判据会被文本劫持」栽过一次。
    #     ⭐ 所以由知道来源的那一方（`cli.py::_run_unit`）直接告诉它。
    worker_ok = (r is not None and not r.is_error
                 and not result.model_mismatch and not result.timed_out
                 and not worker_err)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        #  ⭐ 收工行。⚠️ 与开跑行按 `unit_id` 配对（`units`）。
        #  ⛔ `unit_id` 缺省空串时配不上，那一单会被当成「被中断」——
        #     那是**保守**方向（宁可多报一次中断，不许把中断读成完成）。
        "event": _CLOSE,
        "unit_id": unit_id,
        "task": result.task,
        "model": model,
        "tools": tools,
        "worker_ok": worker_ok,      # 工人自身跑完没报错
        "gate_ok": gate_ok,          # 闸判定；None = 没跑闸（只读任务）
        #  ⭐ `ok` 与 `worker_ok` 问的是**两个不同的问题**（2026-08-12 拆开）：
        #     `worker_ok` = **工人这一档够不够用**（只看工人侧）；
        #     `ok`        = **这一单合格吗**（受一切错影响：闸、宪法、固化失败…）。
        #  ⚠️ 所以 `ok` 仍然读 `result.error` —— 那里装着「固化产出时炸了」
        #     这类既不是工人、也不是闸的失败（实测：BranchHijack 分支劫持）。
        #  ⛔ 别把这一行也改成只看 worker_ok：那会让一单**产出没落地**的活记成成功。
        "ok": worker_ok and (gate_ok is not False) and result.error is None,
        "failure_class": None,       # 事后人工标注：model / taskspec / tooling / gate
        "gate_detail": gate_detail,
        #  ⭐ 闸的**结构化**判定：0 全过 · 1 有未过（活没干好）· 2 闸自身故障。
        #     None = 没跑闸。⛔ 加它是因为下游 `autopilot.escalation` 原来靠
        #     对 `error` 做子串匹配来归类，而 2026-08-02 实测那会被**测试名劫持**：
        #     本仓 gates.sh 把 pytest 的失败节点名原样放进 FAIL 的 detail，
        #     于是一个叫 `test_闸自身故障要归到闸自身故障` 的测试挂掉，
        #     会让「活没干好」被归类成「闸自身故障 → 先修环境」。
        #     ⚠️ 而五份计划全都点名 pytest——那是自动驾驶的主路径，不是边角。
        #  ⭐ 2026-08-08：工人是不是**被死线掐死**的。⛔ 不写进台账的话，
        #     下游只看得见「cost_usd_real 是 null」，然后去猜价目表——
        #     2026-08-06 实测就这么把「工人超时」印成了「价目表里没有那个模型」，
        #     ⚠️ 指着人去修一个**根本没坏**的东西。
        #  ⭐ `DispatchResult.timed_out` 这个结构化字段 G-111 就加了，
        #     ⛔ 只是一直没接进台账 —— 又是「判据有了、没接上」那个形状。
        "timed_out": timed_out,
        "gate_code": gate_code,
        #  ⭐ 这次的 `gate_code=2` 是**墙钟掐的**吗（G-116）。
        #  ⛔ 不许改 `gate_code` 的语义（2 = 闸没能给出可用结论，那是对的），
        #     ⚠️ 这里加的是**为什么**：墙钟掐的和环境坏了，处置完全相反
        #     ——前者「加大 max_wall_min」，后者「先修环境」。
        #  ⚠️ `None` = 不知道 → 下游按**保守**处置（当成环境可能坏了）。
        #     ⛔ 台账里已有的行都没有这个字段，缺省必须落在保守那边。
        #  ⭐ 存在的理由：那句更正原本只印在屏幕上，而挂一夜的定义就是没人看屏幕。
        "gate_wall_killed": gate_wall_killed,
        # ⚠️ 两个成本字段必须并存，且名字要能一眼看出区别：
        #    `cost_usd_synthetic` 是回执原样带来的——Claude Code 套 Opus 价目表
        #    算出来的，对第三方端点高 19.6–27 倍（G-28）。留着它只为可追溯。
        #    `cost_usd_real` 才是按 prices.json 重算的估值；价目表里没有该模型时
        #    为 null，**此时一律呈现「未知」，不许拿合成价顶替**。
        "cost_usd_synthetic": round(r.total_cost_usd, 6) if r else 0.0,
        "cost_usd_real": (lambda c: round(c, 6) if c is not None else None)(
            real_cost_usd(pk, r.usage) if r else None),
        "price_source": price_source(pk) if r else "",
        "duration_s": round(r.duration_ms / 1000, 1) if r else 0.0,
        # ⭐ 分段墙钟：setup_s（建 worktree + 同步缓存）/ worker_s / gate_s。
        #    ⛔ **不要拿 duration_s 去估一单要多久**——它只是工人那一段。
        #    实测 eco-ob 首跑：工人 93s，而整单 1043s，**闸占 87%**。
        #    此前没有这三个数，于是「一单 141 秒」那个历史数字在台账里
        #    怎么也复现不出来（三种口径分别是 61.4 / 113.9 / 164.05，分母不明）。
        #    ⚠️ 缺项记 null，与「量到 0 秒」区分。
        **{k: (phases or {}).get(k) for k in ("setup_s", "worker_s", "gate_s")},
        "turns": r.num_turns if r else 0,
        "cache_read_tokens": r.cache_read_tokens if r else 0,
        "models_used": r.models_used if r else [],
        # ⚠️ `error` 此前只装编排侧发现的问题（闸未过、模型不符），
        #    工人自己报的失败原因（撞轮数上限、执行中断）一个字都没进来——
        #    于是台账能说「失败了」却说不出「为什么」。见 models.py::Receipt.why_failed。
        "error": (result.error
                  or ("model_mismatch" if result.model_mismatch else None)
                  or (r.why_failed if r else None) or None),
        # 撞轮数上限是**拆单太大**，不是模型不行。成本实验里这两类必须分开，
        # 否则会把编排方的失误算进「工人不够用」的账上。
        "truncated": bool(r and r.subtype == "error_max_turns"),
        "input_tokens": int(r.usage.get("input_tokens", 0)) if r else 0,
        "output_tokens": int(r.usage.get("output_tokens", 0)) if r else 0,
        "report": str(result.report_path) if result.report_path else None,
        # ── 订阅额度：**每单都记**，不只是撞墙那次 ──
        #    ⚠️ 记的是「离开时的状态」。攒起来才能回答「这批活把额度用掉了多少」，
        #    以及事后判断某次失败到底是不是撞额度。
        #    ⛔ None ≠ 充足，是**不知道**（付费端点拿不到这些字段）。
        "quota_status": (result.rate_limit.status if result.rate_limit else None),
        "quota_kind": (result.rate_limit.kind if result.rate_limit else None),
        "quota_resets_at": (result.rate_limit.resets_at if result.rate_limit else None),
        "quota_utilization": (result.rate_limit.utilization if result.rate_limit else None),
    }
    # ⛔ 撞额度**不是**「模型不行」，直接在这里定死归因，不等人工事后标。
    #    原有四类（model / taskspec / tooling / gate）里没有对的那个，
    #    而「model」是最顺手也最错的选择——它会把额度问题算进
    #    「工人这一档够不够用」的账上，直接污染结论。
    if result.rate_limit is not None and result.rate_limit.blocked:
        row["failure_class"] = _quota.FAILURE_CLASS
    _append(path, json.dumps(row, ensure_ascii=False) + "\n")
    #  ⭐⭐ 2026-08-16：把刚写下去的那一行**交回给调用方**。
    #
    #  ⛔ 以前返回 None，于是要用这一行的地方（卷宗）只能回头 `load(...)[-1]`
    #     ——「账本最后一行」。⚠️ 并行波次里那不是自己那一单：实测两单同跑时，
    #     卷宗会抓到隔壁的花费、轮数、闸结论，连文件名都跟着写错人。
    #  ⭐ 记账这一刻手里就攥着它，交回来比回头去挑省掉一整个失败模式：
    #     「挑不到自己那行怎么办」——而最省事的定义（那就还用最后一行吧）
    #     **恰好就是今天这个毛病本身**，写下去以后谁也看不出来。
    #  ⚠️ 顺带少读一次整本账，而账本只增不减。
    return row


#  ⛔ 并发写台账**必须加锁**，不加会丢行、写坏行、甚至写出非法 UTF-8。
#
#  ⚠️ BACKLOG G-41 曾记「16 线程并发写 → 16 行齐全、0 行无法解析 ✅ 未损坏」。
#     2026-07-30 复跑推翻了它：那是**一次**试验，而且用的是等长短行。
#     换成真实形态（8 线程 × 80 行、**不等长**——带 error 文本的行长好几倍）：
#         40 次试验 **40 次都丢行且出坏行**，其中一次写出非法 UTF-8 字节，
#         连 `read_text` 都抛 UnicodeDecodeError。
#     复现脚本：`tools/probe_ledger_concurrency.py`
#
#  ⛔ 后果不是「少了几行日志」：自动驾驶的每一条防线（花了多少 / 派了几次 /
#     哪些绿了）**全都只从台账读**。丢行 → 防线读到偏小的数 → 预算永不到顶、
#     已绿的单被重派（重复花钱）；坏行 → 整个台账读不出来 → 防线全部归零。
_LOCK = threading.Lock()


def _append(path: Path, text: str) -> None:
    """把一行追加进台账。**进程内用线程锁，跨进程用文件锁。**

    ⚠️ 两层都要：`--parallel` 是同进程多线程；而 `--detach` 的后台作业与
    前台命令是**两个进程**，它们写同一个台账。
    ⛔ 拿不到文件锁也照写——丢一行日志远好过丢一单活。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with _LOCK:
        fd = None
        for _ in range(50):                     # 最多等 ~1 秒
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                time.sleep(0.02)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        finally:
            if fd is not None:
                os.close(fd)
                try:
                    os.unlink(lock)
                except OSError:
                    pass


class LedgerCorrupted(ValueError):
    """台账里有读不出来的行。⛔ 不许静默跳过——那会让防线读到偏小的数。"""


def load(path: Path, since_days: float | None = None, *,
         strict: bool = True) -> list[dict]:
    """读台账。

    ⛔ **坏行必须报出来，不许静默 skip。** 静默跳过等于让「已花多少」
    「派了几次」偷偷变小，而那正是失控防线读的数——防线会因此形同虚设。
    ⚠️ 原实现是一句裸列表推导 `[json.loads(ln) for ln in ...]`，一行坏行
    直接抛 `JSONDecodeError`，而上层 `except ValueError` 把它显示成
    「输入错误：Unterminated string」——**不提文件名、不说是台账**。

    `strict=False` 给排查用：跳过坏行但**返回时仍然会有人知道**（见 stderr）。
    """
    if not path.exists():
        return []
    # ⚠️ errors="replace"：并发写坏时可能出现非法 UTF-8，不能让读操作直接炸。
    raw = path.read_text(encoding="utf-8", errors="replace")
    rows: list[dict] = []
    bad: list[int] = []
    for i, ln in enumerate(raw.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError:
            bad.append(i)
    if bad:
        msg = (f"⛔ 台账 {path} 有 {len(bad)} 行读不出来（行号 {bad[:5]}…）。\n"
               f"   多半是并发写没加锁留下的（见 telemetry._append 的注释）。\n"
               f"   ⚠️ **不许当没看见**：这些行里的花费与派单次数会从统计里消失，\n"
               f"   而失控防线读的就是那些数。先把坏行挑出来再继续。")
        if strict:
            raise LedgerCorrupted(msg)
        print(msg, file=sys.stderr)
    if since_days is None:
        return rows
    cutoff = time.time() - since_days * 86400
    return [r for r in rows
            if time.mktime(time.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S")) >= cutoff]


_MISSING = object()

#  ⭐ `gate_code` → 档名的**唯一**映射。⛔ 别在别处再抄一份。
_GATE_BUCKET = {0: "全过", 1: "有未过", 2: "闸自身故障"}


def gate_buckets(rows: list[dict]) -> tuple[dict[str, int], int]:
    """把每一单归进闸判定的六档，返回 (计数, 其中靠退路推出来的单数)。

    ⭐ **抽成纯函数**是为了判据能直接量：不变式是
    **各档相加恒等于 `len(rows)`** —— 那是唯一能抓住「有单元掉进
    没人统计的缝里」的判据，⛔ 逐档断言永远抓不到。

    ## ⚠️ 三个坑，每一个都是实测撞到的

    **① 老行退回读 `gate_ok`，⛔ 不许一股脑丢进「无判定记录」。**
    `gate_code` 是 2026-08-02 才加的，实测台账 31 行里 **29 行没有它**。
    一股脑归到「无判定记录」的后果是那一行印「**没跑闸 0**」——
    而台账里 `gate_ok is None`（只读单，真的没跑闸）有 **10 条**，
    ⚠️ 且它与同屏上一行 `过闸 19/21` **当面矛盾**。
    ⭐ `cli.py` 里 `gate_ok` 与 `gate_code` 共用同一个 `if wt` 条件写入，
    所以老行的 `gate_ok` 一条不缺，拿它兜底是有据的。

    **② 协议外的值不许静默消失。**
    实测 `gate_code=7` → 各档全 0 而实际有 1 单，且**没有任何提示**。
    ⚠️ 这正是本项目最怕的形态：一个**看起来正常的数字**。

    **③ ⛔ 类型敏感——这条最狠。**
    实测 `gate_code=2`（int）→ 报警档 1；`gate_code="2"`（**字符串**）→ 报警档 **0**。
    台账是 JSON 文件，序列化只要漂一次，**这个报警就永远读 0**。
    ⚠️ 一个在它该报警的输入上静默归零的报警器，比不装更坏。
    """
    counts = {"全过": 0, "有未过": 0, "闸自身故障": 0, "没跑闸": 0,
              #  ⭐ 「被中断」必须**单列一档**（G-108）：一单开始花钱、
              #     进程被杀在半路，它既不是「没跑闸」（那是只读单，正常收工），
              #     也不是「无判定记录」（那是格式老/字段缺）。
              #  ⛔ 折进任何一档都会让「昨晚被杀过一单」这件事消失在别的数里。
              "被中断": 0,
              "无判定记录": 0, "判不了": 0}
    coarse = 0
    for r in rows:
        #  ⛔ 先看被中断——它压过一切其它判定：那一单**根本没有结论**，
        #     而不是「结论是某某」。⚠️ 顺序反了会让它掉进「无判定记录」。
        if r.get("interrupted"):
            counts["被中断"] += 1
            continue
        gc = r.get("gate_code", _MISSING)
        if gc is _MISSING:
            #  ⚠️ 老行：退回读 `gate_ok`（布尔，只知道过没过）。
            go = r.get("gate_ok", _MISSING)
            if go is _MISSING:
                counts["无判定记录"] += 1
            elif go is None:
                counts["没跑闸"] += 1
            elif go:
                counts["全过"] += 1
                coarse += 1
            else:
                counts["有未过"] += 1
                coarse += 1
            continue
        counts[_bucket_of(gc)] += 1
    return counts, coarse


def _bucket_of(gc: object) -> str:
    """一个 `gate_code` 值属于哪一档。⛔ 认不出来的一律进「判不了」，不许丢。"""
    if gc is None:
        return "没跑闸"
    #  ⚠️ Python 里 `True` 是 `int` 的子类，`True == 1` 成立——
    #  ⛔ 不先挡住它，一个布尔会被当成「有未过」。
    if isinstance(gc, bool):
        return "判不了"
    if isinstance(gc, (int, float)) and gc in _GATE_BUCKET:
        return _GATE_BUCKET[int(gc)]
    #  ⭐ 字符串形态也认——序列化漂移不该让**报警档静默归零**（坑③）。
    if isinstance(gc, str) and gc.strip().isdigit()             and int(gc.strip()) in _GATE_BUCKET:
        return _GATE_BUCKET[int(gc.strip())]
    return "判不了"


def summarize(rows: list[dict]) -> str:
    if not rows:
        return "台账为空——还没派过单。"
    n = len(rows)
    ok = sum(1 for r in rows if r.get("ok"))
    wok = sum(1 for r in rows if r.get("worker_ok", r.get("ok")))
    gated = [r for r in rows if r.get("gate_ok") is not None]
    gok = sum(1 for r in gated if r.get("gate_ok"))
    # ⚠️ 成本必须报真实价，且**必须说清有多少单算不出真实价**。
    #    旧台账（2026-07-26 之前）只有 `cost_usd`，那是 Opus 合成价（G-28）。
    #    把两种价混在一个总和里，会得出一个既不是合成价也不是真实价的数——
    #    比报错更糟，因为它看起来像个正常数字。
    real = [r["cost_usd_real"] for r in rows if r.get("cost_usd_real") is not None]
    synth = [r.get("cost_usd_synthetic", r.get("cost_usd", 0.0)) for r in rows]
    #  ⛔ **被中断的单没有这些字段**（开跑行只说「开始花钱了」，不带任何结论）。
    #     ⚠️ 用 `r["duration_s"]` 直接下标会 KeyError，把整条 `devloop stats`
    #     打崩——⭐ 而那正是出事第二天早上人最想跑的命令。G-108 复核实测到的。
    #  ⭐ 用 `.get(..., 0)`：被中断的单**耗时未知**，记 0 而不是让它炸；
    #     ⛔ 但它在下面单列一行，绝不并进「单均耗时」去稀释那个数。
    cut = sum(1 for r in rows if r.get("interrupted"))
    done = [r for r in rows if not r.get("interrupted")] or rows
    dur = sum(r.get("duration_s") or 0 for r in done)
    cached = sum(1 for r in rows if (r.get("cache_read_tokens") or 0) > 0)
    if len(real) == n:
        cost_line = (f"累计真实成本 ${sum(real):.4f}，单均 ${sum(real) / n:.4f}"
                     f"（按价目表重算，非账单）")
    elif real:
        cost_line = (f"累计真实成本 ${sum(real):.4f}（仅 {len(real)}/{n} 单可算），"
                     f"其余 {n - len(real)} 单只有合成价 ${sum(synth) - sum(r.get('cost_usd_synthetic', r.get('cost_usd', 0.0)) for r in rows if r.get('cost_usd_real') is not None):.4f}"
                     f"  ⚠️ 两者口径不同，不可相加")
    else:
        cost_line = (f"⚠️ 无法给出真实成本：{n} 单全部只有合成价 "
                     f"${sum(synth):.4f}（Opus 价目表套第三方 token，高约 20 倍，见 G-28）")
    lines = [
        f"派单 {n} 次，整体合格 {ok}（{ok / n * 100:.0f}%）"
        #  ⭐ 被中断的单必须在**人眼前这一层**也露面。⛔ 光在 `gate_buckets`
        #     里成立不算数——「各档相加恒等于总数」那条不变式，要在人真正
        #     会看的地方成立。
        + (f"　⛔ **其中 {cut} 单被中断**（进程被杀 / Ctrl-C，钱花过了）" if cut else ""),
        f"  其中 工人自身跑通 {wok}/{n}" +
        (f"，过闸 {gok}/{len(gated)}" if gated else "（无写任务，未跑闸）"),
        cost_line,
        f"累计耗时 {dur / 60:.1f} 分钟，单均 {dur / len(done):.0f} 秒"
        + (f"（⚠️ 不含 {cut} 单被中断的——它们耗时未知）" if cut else ""),
        f"缓存命中 {cached}/{n} 单" + ("" if cached else "  ⚠️ 全部未命中——检查 prompt 前缀是否稳定"),
    ]
    # ⭐ 闸的**结构化**判定汇总（gate_code）。⛔ 只认这个数字键，不许从
    #    `gate_detail` 的文字里猜——那段文字会原样带上失败的 pytest 节点名，
    #    2026-08-02 实测出现过节点名里含「闸自身故障」四个字而 gate_code == 1
    #    （活没干好）的情况；文字匹配会把它错归到「闸自身故障 → 先修环境」。
    gc_counts, coarse = gate_buckets(rows)
    gc_parts = [
        f"全过 {gc_counts['全过']}",
        f"有未过 {gc_counts['有未过']}",
        f"⚠️ 闸自身故障 {gc_counts['闸自身故障']}",
        f"没跑闸 {gc_counts['没跑闸']}",
    ]
    #  ⚠️ 后两档只在**非零**时追加——正常台账不该出现它们，出现了本身就是信号。
    #  ⛔ 但整行永远打印：省略会让人误以为「没有闸判定这回事」。
    if gc_counts["无判定记录"]:
        gc_parts.append(f"无判定记录 {gc_counts['无判定记录']}")
    if gc_counts["判不了"]:
        gc_parts.append(
            f"⚠️ 判不了 {gc_counts['判不了']}（gate_code 是协议外的值）")
    tail = ""
    if coarse:
        #  ⛔ **不许用新档名给旧数据背书。** 退路只知道「过没过」，
        #     分不出「活没干好」与「闸自己坏了」——那正是这一行存在的理由，
        #     ⚠️ 所以对退路推出来的部分必须明说它分不出来。
        tail = (f"　⚠️ 其中 {coarse} 单来自老行（只有 gate_ok），"
                f"分不出「活没干好」还是「闸自己坏了」")
    lines.append("闸判定：" + " · ".join(gc_parts) + tail)

    cls = {}
    for r in rows:
        if not r.get("ok"):
            k = r.get("failure_class") or "未分类"
            cls[k] = cls.get(k, 0) + 1
    if cls:
        lines.append("失败归因：" + "，".join(f"{k} {v} 单" for k, v in cls.items()))
        lines.append("  ⚠️ 归因决定「模型够不够用」的答案——taskspec/tooling 类失败不算模型的锅")
    return "\n".join(lines)
