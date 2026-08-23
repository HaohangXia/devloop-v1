"""订阅额度：撞了上限要**当场停整批**，并知道该等到几点。

## ⭐ 事实来源

2026-07-29 实测 `claude -p --output-format stream-json --verbose`，
**每一跑**都会吐一条 `rate_limit_event`（不只是撞墙那次）：

```json
{"status": "allowed", "resetsAt": 1785332400, "rateLimitType": "five_hour",
 "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled",
 "isUsingOverage": false}
```

⭐ 这意味着我们能**在撞墙之前**就看见自己离墙多近，而不是撞了才知道。
`--output-format json`（单结果）拿不到这些——所以派单改用 stream-json。

CLI 二进制里的 zod schema（权威取值集合，2026-07-29 从 claude.exe 抠出）：

    status:        "allowed" | "allowed_warning" | "rejected"
    rateLimitType: "five_hour" | "seven_day" | "seven_day_opus"
                   | "seven_day_sonnet" | "overage"
    resetsAt:      number ← HTTP 头 anthropic-ratelimit-unified-reset

## ⛔ 不用「等 5 小时」这种估算

用户最初的提议是「撞了就停，等 5 小时再开始」——方向对，但**不用猜**：
`resetsAt` 是精确的 unix 秒。猜早了白撞一次（再花一次额度试错），
猜晚了白等几个小时。只有真拿不到时才退回估算，**且必须说明是估算**。

## ⚠️ 两种上限的性质完全不同（官方文档口径）

  · `five_hour`  滚动 5 小时会话窗口，跨模型共享——换模型没用
  · `seven_day`  **固定时间**每周重置，跨模型共享
  · `seven_day_opus` / `seven_day_sonnet` 模型专属——**换个模型就能接着干**

⛔ 拿 5 小时去估周上限会一路撞墙：每次醒来再撞一次，连撞十几个小时，
每次都真花额度。所以没有精确时刻的周上限**不许自动续跑**，交给人。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

#  台账的失败归因里给额度单列一类。
#  ⛔ 原有四类（model / taskspec / tooling / gate）**没有一个对**，
#     而「model」是最顺手也最错的选择——那会把额度问题算进
#     「**工人这一档够不够用**」的账上，直接污染结论。
#     ⚠️ 2026-08-15：措辞从「便宜模型」改成「工人这一档」——
#     ⛔ 便宜模型已跳过（D-COST-01），但**分开记**这条道理跟工人是谁无关。
FAILURE_CLASS = "ratelimit"

#  醒来时多等这么久再重试。
#  ⚠️ 卡着 resetsAt 那一秒重试很容易差之毫厘（服务端时钟与本地未必一致），
#     再撞一次就要再等一个窗口。多等一分钟换一次确定的成功，划算。
RESET_MARGIN_S = 60

#  拿不到 resetsAt 时的兜底。⚠️ 这是**估算**，用它的地方必须说明。
FALLBACK_WAIT_S = 5 * 3600 + RESET_MARGIN_S

#  超过这个比例就提醒「快撞墙了」。⚠️ 只提醒，不停机——
#  停早了等于把剩下的额度白白扔掉。
WARN_UTILIZATION = 0.80

_KIND_NAMES = {
    "five_hour": "5 小时会话上限",
    "seven_day": "周上限",
    "seven_day_opus": "Opus 周上限",
    "seven_day_sonnet": "Sonnet 周上限",
    "overage": "超额额度",
}

#  模型专属的上限——换个模型就能接着干。
#  ⚠️ 其余的跨模型共享，换模型没用（官方 code.claude.com/docs/en/errors 明写）。
_MODEL_SPECIFIC = ("seven_day_opus", "seven_day_sonnet")


@dataclass(frozen=True)
class RateLimit:
    """一次派单结束时的额度状态。"""

    status: str                      # allowed / allowed_warning / rejected
    resets_at: float = 0.0           # ⚠️ unix **秒**（凭据那边的 expiresAt 是毫秒，别混）
    kind: str = ""                   # rateLimitType
    utilization: float | None = None  # 0..1；allowed 时可能没有
    is_overage: bool = False

    @property
    def blocked(self) -> bool:
        """真的被挡住了。"""
        return self.status == "rejected"

    @property
    def should_halt(self) -> bool:
        """要不要停整批。

        ⚠️ 目前等同于 `blocked`——**警告不停机**。
        单独留这个属性是因为「挡住了」是事实、「要不要停」是策略，
        两者混在一个名字里，将来想改策略就得改判据。
        """
        return self.blocked

    @property
    def warning(self) -> bool:
        return (self.status == "allowed_warning"
                or (self.utilization is not None
                    and self.utilization >= WARN_UTILIZATION))

    def remaining_hours(self) -> float | None:
        """距额度恢复还有几小时。⚠️ 拿不到恢复时刻时返回 None，不是 0。

        ⛔ 「不知道」和「马上就恢复」是两回事：返回 0 会让调用方以为
           可以立刻重试，撞一次真花一次额度。留 None 交给调用方决策。
        ⚠️ 恢复时刻已过（时钟漂移或停机太久）→ 0.0，不是负数。
        """
        if not self.resets_at:
            return None
        left = self.resets_at - time.time()
        return max(0.0, left / 3600)

    def kind_name(self) -> str:
        return _KIND_NAMES.get(self.kind, self.kind or "未知上限")

    def when(self) -> str:
        if not self.resets_at:
            return "恢复时刻未知"
        return time.strftime("%m-%d %H:%M", time.localtime(self.resets_at))

    def summary(self) -> str:
        pct = f"，已用 {self.utilization * 100:.0f}%" if self.utilization is not None else ""
        return f"{self.kind_name()}·{self.status}{pct}（恢复 {self.when()}）"

    def advice(self) -> str:
        """给人的下一步建议。

        ⚠️ 这里的信息差是实的：`seven_day_opus` 是**模型专属**上限，
        换个模型就能接着干；而 `five_hour` / `seven_day` 跨模型共享，
        换模型没用。不说清楚，人会白等一周。
        """
        if self.kind in _MODEL_SPECIFIC:
            return (f"{self.kind_name()}是**模型专属**的——换一个模型就能接着干"
                    f"（devloop backends 里挑一个别的）。也可以等到 {self.when()}。")
        return (f"{self.kind_name()}**跨模型共享，换模型没用**。"
                f"等到 {self.when()} 再继续。")


class HaltSignal:
    """「整批停下」的信号。**双向**：单元置位，编排方查。

    ## ⛔ 为什么不用抛异常

    `_run_unit` 里有一个 `except Exception` 兜底，注释写着「单元失败必须隔离，
    不能打死整批」——那是对的，一单的失败不该连坐。但代价是：**从单元里抛出
    的任何异常都会被就地吞掉**，批次照样一单一单撞下去。

    实测形态就是这样：串行主循环里没有 break、没有状态标志，撞了额度之后
    剩下的单会**全部继续派完**，每一单都收回同一个拒绝，而台账里那一串失败
    看起来像「模型突然不行了」。

    ⚠️ 并行更糟：所有单元是**一次性 submit** 进池的，排队中的也停不掉。
    所以并行改成**按波提交**——一波跑完查一次信号，没有排队中的活可停。

    ## 用法

    单元里发现 `rejected` → `halt.trip(rl)`；编排方每单之后查 `halt.tripped`。
    """

    __slots__ = ("_event", "rate_limit")

    def __init__(self) -> None:
        import threading
        self._event = threading.Event()
        self.rate_limit: RateLimit | None = None

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    def trip(self, rl: RateLimit) -> None:
        """置位。⚠️ 只记**第一个**触发者——它才是最接近原因的那条。"""
        if not self._event.is_set():
            self.rate_limit = rl
            self._event.set()

    def clear(self) -> None:
        """睡醒之后复位，接着干（`--wait-for-reset`）。"""
        self._event.clear()
        self.rate_limit = None


def sum_tokens(raw: str) -> int | None:
    """事件流里所有 `usage` 的 token 之和。⛔ 一条都没有时返回 None，**不许返回 0**。

    ## ⭐ 为什么要它

    2026-08-04（G-110）：工人被 3000s 死线打死，`TimeoutExpired` 手里攥着
    它已经吐出来的全部事件流——**其中就有 usage**——而代码一眼没看就扔了。
    于是「那 50 分钟花了多少」事后**永远算不出来**。

    ⚠️ 这不只是「查不到」：超时那一行台账的 `cost_usd_real` 是 null，
    而 `autopilot.check_limits` 对 null 的处置是**停批**
    （「算不出成本 ≠ 花了 0 元」）。⛔ 扔掉 usage = 把整批卡死在一个
    本来算得出来的数上。

    ⛔ `None` 与 `0` 必须分开：`0` 是「真的一个 token 都没用」，
    `None` 是「不知道」。混掉就等于把「没数据」说成「没花钱」。
    """
    total = 0
    seen = False
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        #  ⚠️ usage 可能挂在事件顶层，也可能挂在 `message` 里（不同事件形态）。
        for holder in (ev, ev.get("message") if isinstance(ev.get("message"), dict) else None):
            u = holder.get("usage") if isinstance(holder, dict) else None
            if not isinstance(u, dict):
                continue
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                v = u.get(k)
                if isinstance(v, int):
                    total += v
                    seen = True
    return total if seen else None


def parse_stream(raw: str) -> tuple[dict | None, RateLimit | None]:
    """从 stream-json 的输出里取出 (回执, 额度状态)。

    ⚠️ **坏行不许打死整个解析。** 事件流里混进非 JSON 行是常态——
    实测见过 `Warning: no stdin data received in 3s`。一行坏行让整单失败，
    等于把已经花掉的额度扔了。

    ⚠️ 额度事件取**最后一条**：一次派单可能吐多条（额度在跑的过程中变化），
    最后一条才是离开时的真实状态，用第一条会低估用量。

    ## ⭐⭐ 回执取「工人真干活的那一段」，⛔ 不是简单取最后一条（2026-08-12）

    实测：`f3-pyramid-calibrate` 那一单的输出里有**两条** result，同一个 `session_id`：

    | | num_turns | duration_ms | origin |
    |---|---|---|---|
    | 第 1 条 | **26** | **2299277**（38 分钟）| 无 |
    | 第 2 条 | 1 | 10503（10 秒）| `{"kind": "task-notification"}` |

    ⛔ 旧写法「后来的覆盖先前的」会取到第 2 条 —— 而**那一段不是工人在干活**，
    是壳自己的通知回合。⚠️ 后果：一单真干了 38 分钟 26 轮，账上记成 10 秒 1 轮。

    ⭐ 所以判据落在 `origin` 这个键上：**带 `origin` 的 result 是壳生成的段，不是工人的**。
    ⛔ 不用「挑 num_turns 最大的」——那是个代用品，而且会把累计类字段
    （`total_cost_usd` / `duration_api_ms` 都是**累计**的）取错。

    ⚠️ 只有一条 result 时（绝大多数情况）行为**完全不变**。

    ⛔ 没有额度事件时返回 None，**不许造默认值**——那会让
    「不知道额度」和「额度充足」变成同一件事。
    """
    receipt: dict | None = None       # 最后一条「工人干活」的 result（无 origin）
    fallback: dict | None = None      # ⚠️ 兜底：全都带 origin 时才用它
    rl: RateLimit | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "result":
            #  ⭐ 带 `origin` 的是壳生成的段（实测见过 `{"kind": "task-notification"}`），
            #     ⛔ 它不代表工人干了什么。⚠️ 详见本函数文档里那张表。
            if ev.get("origin") is None:
                receipt = ev      # 后来的覆盖先前的：最后一段**真干活**才是结论
            else:
                fallback = ev
        elif t == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if isinstance(info, dict) and info.get("status"):
                rl = from_info(info)
    #  ⛔ 一条不带 origin 的都没有时才退回去 —— 「有个回执」总比「没有回执」强，
    #     ⚠️ 但那说明这一单从头到尾没有真干活的段，值得怀疑。
    return (receipt if receipt is not None else fallback), rl


#  额度类文案的开头。⛔ **照抄 CLI 二进制里的常量表 `Gs9`，不是我编的**。
#  ⚠️ 中间那个分隔符是 U+00B7（`·`），不是 ASCII 句点——所以判据一律用
#     `startswith` 白名单，别去 split 那个点。
QUOTA_PREFIXES = (
    "You've hit your", "You've used", "You're close to",
    "You're now using usage credits", "You're out of usage credits",
    "You're now using your usage allocation", "Now using your usage allocation",
    "Now using usage credits", "You're now using extra usage",
    "You're out of extra usage", "Your seat type doesn't include usage",
    "Your org is out of usage", "Your usage allocation has been disabled",
    "Your group's usage limit is set to $0",
)

#  ⛔ **反判据：命中即「不是你的额度用尽」。**
#  CLI 二进制里这两句和上面那张表是并列的分支：容量型 429 是**服务端临时限流**，
#  与套餐额度无关。⚠️ 把它当额度用尽，会让整批停机、还睡上几个小时，
#  而实际上它几分钟就过去了。
NOT_QUOTA = (
    "Server is temporarily limiting requests (not your usage limit)",
    "Request rejected (429)",
)


def classify(result: dict, rl: RateLimit | None) -> RateLimit | None:
    """综合判定这一单是不是撞了额度。返回 None = 不是。

    三层，任一层命中即判限流；层级只影响**信息丰富度**，不影响判定：

      A `rate_limit_event.status == "rejected"` —— 唯一带**种类和精确恢复时刻**的
      B 回执的 `is_error` + `api_error_status == 429` —— 兜底，两样都拿不到
      C 文案前缀 —— 只用来**定性**，⛔ 不用来定时

    ⛔ **不许拿 `subtype` 当判据**：CLI 二进制原文显示限流回执的 subtype
       仍然是 `"success"`。这个项目已经因为「选了顺手但错的字段」栽过三次。

    ⚠️ A 层的 rejected 形态**本项目没有实测样本**（不许为取样去真撞一次），
       所以 B/C 两层不是冗余，是必需。
    """
    if rl is not None and rl.blocked:
        return rl                                  # A 层最好，直接用

    text = str(result.get("result") or "")
    if any(p in text for p in NOT_QUOTA):
        return None                                # ⛔ 反判据优先于一切正判据

    hit_429 = (bool(result.get("is_error"))
               and result.get("api_error_status") == 429)
    hit_text = any(text.startswith(p) for p in QUOTA_PREFIXES)
    if not (hit_429 or hit_text):
        return rl if rl is not None else None

    # ⚠️ 种类与恢复时刻**如实留空**——兜底层真的判不出来。
    #    ⛔ 编一个「大概 5 小时」出来，会让周上限一路撞墙。
    #    留空 → can_auto_resume() 返回 False → 交给人，这是保守的正确方向。
    return RateLimit(status="rejected", resets_at=0.0, kind="")


def from_info(info: dict) -> RateLimit:
    """把 CLI 的 `rate_limit_info` 转成本地形态。

    ⛔ `resetsAt` 按**秒**解读。实测值 1785332400 按秒是 2026-07-29 23:40，
       按毫秒是 1970-01-21——单位搞错不会报错，只会静默地错四十年。
       ⚠️ 而凭据那边的 `expiresAt` **恰恰是毫秒**，两个来源单位不同。
    """
    u = info.get("utilization")
    return RateLimit(
        status=str(info.get("status", "")),
        resets_at=float(info.get("resetsAt") or 0),
        kind=str(info.get("rateLimitType") or ""),
        utilization=(float(u) if isinstance(u, (int, float)) else None),
        is_overage=bool(info.get("isUsingOverage")),
    )


def seconds_until_reset(rl: RateLimit) -> tuple[float, str]:
    """要等多久才值得重试。返回 (秒数, 给人看的理由)。

    ⛔ 有 `resetsAt` 就用它，**不许猜 5 小时**。
    ⚠️ `resetsAt` 落在过去时（时钟漂移、或停机太久才回来看）要**立刻重试**，
       不是睡负数（会连轴空转），也不是睡一个完整窗口。
    """
    if rl.resets_at:
        left = rl.resets_at - time.time() + RESET_MARGIN_S
        if left <= 0:
            return 0.0, f"恢复时刻 {rl.when()} 已经过去，直接重试"
        return left, f"等到 {rl.when()}（{left / 3600:.1f} 小时后，含 {RESET_MARGIN_S}s 余量）"
    return (FALLBACK_WAIT_S,
            f"⚠️ 拿不到精确恢复时刻，按 5 小时**估算**等待"
            f"（{FALLBACK_WAIT_S / 3600:.1f} 小时）——醒来可能还没恢复")


def can_auto_resume(rl: RateLimit) -> bool:
    """能不能自己睡醒接着干。

    ⛔ 没有精确时刻的**周上限**不许自动续跑：官方文档明写周上限是
       固定时间重置，拿 5 小时去估会一路撞墙——每次醒来再撞一次，
       连撞十几个小时，而每次撞都真花额度。那种情况交给人。
    """
    if rl.resets_at:
        return True
    # ⛔ 种类也判不出来时**一律不自动等**。兜底层（回执 429 + 文案）拿不到
    #    `rateLimitType`，而周上限和 5 小时上限在文案里可能长得一样
    #    （Pro 下 `seven_day_sonnet` 也渲染成 "weekly limit"）。
    #    ⚠️ 「不知道是哪种」按「可能是周上限」处理——猜错的代价不对称：
    #    多问一次人只是麻烦，自动睡错了是连撞十几个小时、每次真花额度。
    return bool(rl.kind) and not rl.kind.startswith("seven_day")
