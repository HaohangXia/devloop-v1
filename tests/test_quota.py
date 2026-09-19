"""额度自检：撞订阅上限要**当场停整批**，而不是继续把剩下的单撞完。

## ⭐ 事实来源：不是猜的，是实测 + CLI 自己的 schema

2026-07-29 实测 `claude -p --output-format stream-json --verbose`，
每一跑都会吐一条 `rate_limit_event`（**不只是撞墙那次**）：

```json
{"status": "allowed", "resetsAt": 1785332400, "rateLimitType": "five_hour",
 "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled",
 "isUsingOverage": false}
```

`resetsAt` = 1785332400 → 2026-07-29 23:40:00 本地时间，**unix 秒**。
真实样本中与解析契约有关的字段保存在
`tests/fixtures/stream_allowed.jsonl`。本机路径、账号连接状态、插件、工具和命令清单等
与测试无关的环境元数据均已删除；正文、标识符、模型名和用量替换为示意值。

CLI 二进制里的 zod schema（权威取值集合）：

    status:        "allowed" | "allowed_warning" | "rejected"
    rateLimitType: "five_hour" | "seven_day" | "seven_day_opus"
                   | "seven_day_sonnet" | "overage"
    resetsAt:      number（来自 HTTP 头 anthropic-ratelimit-unified-reset）

官方文档口径（已核对 code.claude.com/docs/en/errors）：
撞上限的报错**自带恢复时刻**（`You've hit your session limit · resets 3:45pm`），
且 session/weekly 上限**跨模型共享**——换模型没用。

## ⛔ 所以不需要「等 5 小时」这种估算

`resetsAt` 是精确时刻。猜早了白撞一次（再花一次额度试错），
猜晚了白等几小时。既然 API 直接给，就不许猜。
⚠️ 只有**拿不到** `resetsAt` 时才退回估算，且必须说明用的是估算。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ══ 解析：从 stream-json 里同时取出回执与额度状态 ═══════════════

def test_从最小化真实事件流里同时取出回执和额度():
    """⚠️ 用的是从**真实抓到的**事件流中保留的契约字段，不是猜出的形状。

    编造夹具的风险在这个项目上已经出过事：判据按想象中的形状写，
    真数据一到就对不上。这份夹具保留 2026-07-29 实跑样本中的相关事件与字段，
    删除与解析断言无关的环境指纹，正文、标识符、模型名和用量使用示意值。
    """
    from devloop import quota
    raw = (FIXTURES / "stream_allowed.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert {event["type"] for event in events} == {"rate_limit_event", "result"}
    assert not any(
        key in event
        for event in events
        for key in ("cwd", "tools", "mcp_servers", "slash_commands", "agents")
    ), "公开夹具不能重新带入宿主环境元数据"
    receipt, rl = quota.parse_stream(raw)

    assert receipt is not None, "必须能取出 result 事件当回执"
    assert receipt["subtype"] == "success"
    assert receipt["is_error"] is False
    assert "usage" in receipt and "modelUsage" in receipt, \
        "回执字段必须和 --output-format json 那版一致，否则换格式会打断下游"

    assert rl is not None, "真实流里就是有 rate_limit_event，取不到说明解析错了"
    assert rl.status == "allowed"
    assert rl.kind == "five_hour"


def test_resetsAt必须按秒解读而不是毫秒():
    """⛔ 单位搞错不会报错，只会**静默地错四十年**。

    实测值 1785332400 按秒解读是 2026-07-29 23:40，按毫秒解读是 1970-01-21。
    ⚠️ 而项目里另一个时间戳（凭据的 expiresAt）**恰恰是毫秒**——
    两个来源单位不同，混用是迟早的事，所以这条要钉死。
    """
    from devloop import quota
    raw = (FIXTURES / "stream_allowed.jsonl").read_text(encoding="utf-8")
    _, rl = quota.parse_stream(raw)
    assert rl.resets_at == 1785332400
    assert time.gmtime(rl.resets_at).tm_year == 2026, \
        "按秒解读应落在 2026；落到 1970 就是当成毫秒了"


def test_没有额度事件时不许假装有():
    """⚠️ 老格式（--output-format json）没有 rate_limit_event。
    那时必须返回 None，⛔ 不许造一个「看起来正常」的默认值——
    那会让「不知道额度」和「额度充足」变成同一件事。"""
    from devloop import quota
    receipt, rl = quota.parse_stream(
        json.dumps({"type": "result", "subtype": "success", "is_error": False}))
    assert receipt is not None
    assert rl is None


def test_坏行不许打死整个解析():
    """⚠️ 事件流里混进一行非 JSON（警告、进度条）是常态，
    实测就见过 `Warning: no stdin data received in 3s`。
    ⛔ 一行坏行让整单失败，等于把已经花掉的额度扔了。"""
    from devloop import quota
    raw = ("Warning: no stdin data received in 3s\n"
           + json.dumps({"type": "rate_limit_event", "rate_limit_info": {
               "status": "allowed", "resetsAt": 1785332400,
               "rateLimitType": "five_hour"}}) + "\n"
           + "\x00\x01 二进制垃圾\n"
           + json.dumps({"type": "result", "subtype": "success", "is_error": False}) + "\n")
    receipt, rl = quota.parse_stream(raw)
    assert receipt is not None and rl is not None


def test_多条额度事件取最后一条():
    """⚠️ 一次派单可能吐多条（额度在跑的过程中变化）。
    最后一条才是**离开时**的真实状态——用第一条会低估用量。"""
    from devloop import quota
    ev = lambda st, u: json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": st, "resetsAt": 1785332400, "rateLimitType": "five_hour",
        "utilization": u}})
    raw = (ev("allowed", 0.1) + "\n" + ev("allowed_warning", 0.95) + "\n"
           + json.dumps({"type": "result", "subtype": "success", "is_error": False}))
    _, rl = quota.parse_stream(raw)
    assert rl.status == "allowed_warning" and rl.utilization == 0.95


# ══ 判定：撞了没有、该不该停 ═══════════════════════════════════

def test_rejected就是撞了必须停():
    from devloop import quota
    rl = quota.RateLimit(status="rejected", resets_at=1785332400,
                         kind="five_hour")
    assert rl.blocked
    assert rl.should_halt


def test_allowed_warning不停但要报():
    """⚠️ 警告不是停机理由——停早了等于白白浪费剩下的额度。
    但必须报出来，否则人永远不知道自己离墙有多近。"""
    from devloop import quota
    rl = quota.RateLimit(status="allowed_warning", resets_at=1785332400,
                         kind="five_hour", utilization=0.92)
    assert not rl.blocked
    assert not rl.should_halt
    assert "92" in rl.summary()


def test_额度耗尽不是模型不行():
    """⛔ **这条是本模块存在的核心理由。**

    项目里已有明写的规矩：撞轮数上限是「拆单太大」，不是「模型不行」，
    两者在成本实验里必须分开记，否则会把编排方的失误算进模型的账上。

    ⚠️ 撞额度是同一类问题，而 `failure_class` 原本只有四个取值
    （model / taskspec / tooling / gate）——**没有一个对**，
    而「model」是最顺手也最错的那个选择。
    """
    from devloop import quota
    assert quota.FAILURE_CLASS == "ratelimit"
    assert quota.FAILURE_CLASS not in ("model", "taskspec", "tooling", "gate")


# ══ 该等到什么时候 ═══════════════════════════════════════════════

def test_有resetsAt就用它不许猜五小时():
    """⛔ 用户最初的提议是「撞了就等 5 小时」。方向对，但**不用猜**：
    API 直接给精确时刻。猜早了白撞一次（再花一次额度试错），
    猜晚了白等几小时——两种错代价都是实的。"""
    from devloop import quota
    target = time.time() + 3600
    rl = quota.RateLimit(status="rejected", resets_at=target, kind="five_hour")
    secs, why = quota.seconds_until_reset(rl)
    assert 3600 <= secs <= 3600 + quota.RESET_MARGIN_S + 5
    assert "估算" not in why, f"有精确时刻时不该说估算：{why}"


def test_没有resetsAt才退回估算并且要说明():
    """⚠️ 退回估算本身可以，⛔ 但**不许假装它是精确的**。
    「我在猜」这件事必须出现在给人看的字里。"""
    from devloop import quota
    rl = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")
    secs, why = quota.seconds_until_reset(rl)
    assert secs > 0
    assert "估算" in why, f"必须写明这是估算：{why}"


def test_resetsAt已经过去时不许睡负数():
    """⚠️ 时钟漂移、或者停机太久才回来看，都会让 resetsAt 落在过去。
    ⛔ 那时该立刻重试，不是睡负数（会立即返回、连轴空转）也不是睡五小时。"""
    from devloop import quota
    rl = quota.RateLimit(status="rejected", resets_at=time.time() - 9999,
                         kind="five_hour")
    secs, _ = quota.seconds_until_reset(rl)
    assert 0 <= secs <= quota.RESET_MARGIN_S + 5


def test_周上限不能用五小时估算兜底():
    """⛔ 官方文档明写：周上限**固定时间重置**，且跨模型共享。
    拿 5 小时去估周上限会一路撞墙——每次醒来都再撞一次，
    连撞十几个小时，每次都真花额度。

    ⚠️ 所以拿不到 resetsAt 的周上限**必须交给人**，不许自动续跑。
    """
    from devloop import quota
    rl = quota.RateLimit(status="rejected", resets_at=0, kind="seven_day")
    assert not quota.can_auto_resume(rl), "没有精确时刻的周上限不许自动续跑"
    rl2 = quota.RateLimit(status="rejected", resets_at=time.time() + 3600,
                          kind="seven_day")
    assert quota.can_auto_resume(rl2), "有精确时刻就可以等"


def test_opus专属上限要提示可以换模型():
    """⚠️ 官方文档：session/weekly 上限跨模型共享，换模型没用；
    但 `seven_day_opus` 是**模型专属**的——换个模型就能接着干。
    这条信息不给出来，人会白等一周。"""
    from devloop import quota
    rl = quota.RateLimit(status="rejected", resets_at=0, kind="seven_day_opus")
    assert "换一个模型" in rl.advice(), f"该提示换模型：{rl.advice()}"

    # ⚠️ 反面同样要钉住，而且判据要落在**语义**上：
    #    第一版这里写的是 `"换" not in advice`，可实现里那句是
    #    「换模型**没用**」——含「换」字但意思正好相反，判据的维度错了。
    rl2 = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")
    assert "没用" in rl2.advice(), \
        f"五小时上限跨模型共享，必须明说换模型没用：{rl2.advice()}"
    assert "换一个模型" not in rl2.advice(), "别给出换模型这种错建议"


def test_最小化真实result事件能直接构造回执():
    """⛔ 换输出格式最容易踩的坑：**回执契约悄悄变了**。

    stream-json 的 result 事件比 json 版多了 `type` / `uuid` 等字段。
    如果 `Receipt` 对多余字段是严格的，换格式会让**每一单**都解析失败——
    而那要到真派单时才发现，那时钱已经花了。

    ⚠️ 所以拿**真抓到并按契约字段最小化的** result 事件直接构造一次 Receipt；
    不保留本机或账户环境元数据。
    """
    from devloop.models import Receipt
    from devloop import quota
    raw = (FIXTURES / "stream_allowed.jsonl").read_text(encoding="utf-8")
    result, _ = quota.parse_stream(raw)
    assert result["uuid"] == "00000000-0000-4000-8000-000000000001", \
        "保留合成 UUID 扩展字段，才能继续检查 Receipt 接受 stream-json 多余字段"
    r = Receipt(**result)                      # 不该抛
    assert r.is_error is False
    assert r.models_used, "modelUsage 必须解析出来——模型核对靠它"
    assert r.usage, "usage 必须解析出来——成本与缓存统计靠它"


def test_派单命令必须带verbose():
    """⛔ 实测：`--print --output-format=stream-json` 不加 `--verbose` 会直接报
    「requires --verbose」、退出码 1、**零输出**。

    ⚠️ 这不是调试开关，是必需项。漏了它每一单都会以「事件流里没有 result 事件」
    失败，而失败原因看起来像模型问题。
    """
    from devloop import backends
    from devloop.dispatch import build_cmd
    b = backends.Backend(name="s", kind="subscription", model="m", source="t")
    cmd = build_cmd(b.worker_config(), tools="readonly", max_turns=5,
                    prompt="x", exe="claude")
    assert "stream-json" in cmd and "--verbose" in cmd


# ══ ⛔ 第二/三层判据：只靠 rate_limit_event 不够 ═══════════════════

def test_撞额度时subtype仍然是success():
    """⛔ **不许拿 `subtype` 当限流判据。**

    CLI 二进制原文：`yield{type:"result",subtype:"success",is_error:F8,
    api_error_status:DH,...}`——限流回执的 subtype **仍然是 "success"**，
    带信号的是 `is_error: true` + `api_error_status: 429`。

    ⚠️ 这条钉的是「别用顺手但错的字段」。项目里已经因为选错字段栽过三次
    （凭据那边 mtime → auth status → expiresAt）。
    """
    from devloop import quota
    rl = quota.classify(
        {"subtype": "success", "is_error": True, "api_error_status": 429,
         "result": "You've hit your session limit · resets 3:45pm"}, None)
    assert rl is not None and rl.blocked


def test_没有额度事件时靠429兜底():
    """⚠️ `rate_limit_event` 的 `rejected` 形态本项目**没有实测样本**
    （不许为了取样去真撞一次）。所以必须有第二层：回执自己的 429。"""
    from devloop import quota
    rl = quota.classify({"is_error": True, "api_error_status": 429,
                         "result": "You've used your weekly limit"}, None)
    assert rl is not None and rl.blocked
    assert rl.resets_at == 0 and rl.kind == "", \
        "兜底层拿不到种类与时刻——必须如实留空，⛔ 不许编一个"


def test_兜底判出来的限流不许自动续跑():
    """⛔ 兜底层判不出是 5 小时还是周上限。而周上限拿 5 小时去估会一路撞墙。
    ⚠️ 判不出种类时**一律按最保守的处理**。"""
    from devloop import quota
    rl = quota.classify({"is_error": True, "api_error_status": 429,
                         "result": "You've hit your weekly limit"}, None)
    assert not quota.can_auto_resume(rl)


def test_容量型429不是额度用尽():
    """⛔ **这条是反判据，比正判据更容易漏。**

    CLI 二进制里两句话是并列的：
        "Server is temporarily limiting requests (not your usage limit)"
        "Request rejected (429)"
    前者是**服务端容量限流**，与你的套餐额度无关。

    ⚠️ 把它当额度用尽，会让整批停机、还睡上几个小时——而实际上它是暂时的。
    """
    from devloop import quota
    rl = quota.classify(
        {"is_error": True, "api_error_status": 429,
         "result": "API Error: Server is temporarily limiting requests "
                   "(not your usage limit)"}, None)
    assert rl is None, "容量型 429 不该被判成额度用尽"


def test_额度事件优先于兜底():
    """⚠️ 有 `rate_limit_event` 就用它——它带**种类和精确恢复时刻**，
    兜底层两样都没有。⛔ 别让兜底覆盖掉更好的信息。"""
    from devloop import quota
    good = quota.RateLimit(status="rejected", resets_at=1785332400,
                           kind="five_hour")
    rl = quota.classify({"is_error": True, "api_error_status": 429,
                         "result": "You've hit your session limit"}, good)
    assert rl.resets_at == 1785332400 and rl.kind == "five_hour"


def test_正常回执不许被判成限流():
    """⚠️ 防误报：误报的代价是整批白停 + 睡几小时。"""
    from devloop import quota
    assert quota.classify({"is_error": False, "api_error_status": None,
                           "result": "干完了"}, None) is None
    ok = quota.RateLimit(status="allowed", resets_at=1785332400, kind="five_hour")
    assert quota.classify({"is_error": False, "result": "干完了"}, ok) is ok


def test_派单必须关掉重试看门狗(monkeypatch):
    """⛔ **这条防的是一个会让整套设计失效的环境变量。**

    CLI 二进制：`function goH(){return xH(process.env.CLAUDE_CODE_RETRY_WATCHDOG)}`，
    置位后 429 会走**重试**路径而不是立刻返回。

    ⚠️ 而 `dispatch_one` 是 `env = {**os.environ, **cfg.env()}`——**全量继承**
    操作者环境。哪天有人为了 CI 设了它，撞额度的子进程就不再秒退，而是自己重试到
    `timeout_s`（默认 3000 秒 = 50 分钟）才被超时打死。那时：
      · 「秒退 → 当场停批」的前提没了
      · 失败原因记成「工人超时」，⛔ 而不是限流——归因直接错
    """
    from devloop.models import WorkerConfig
    cfg = WorkerConfig(base_url="", model="m", auth_token="", subscription=True)
    env = cfg.env()
    assert env.get("CLAUDE_CODE_RETRY_WATCHDOG") == "", \
        "必须显式置空覆盖掉继承来的值，⛔ 不能只是「不设置」"
