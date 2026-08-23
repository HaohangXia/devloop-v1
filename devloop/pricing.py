"""按真实价目表换算成本。

**为什么需要这个模块**：回执里的 `total_cost_usd` 是 Claude Code 自己算的，
而它不认识第三方端点的价格——于是套用了 Opus 的价目表。13/13 份回执精确等于
`5e-6·input + 25e-6·output + 0.5e-6·cache_read`，而产生这些 token 的是 DeepSeek。
实测偏高 **19.6–27 倍**（倍数随缓存命中率变化，缓存读单价差 138 倍是主因）。
详见 BACKLOG G-28。

⚠️ **口径纪律**：
- 只有 `usage` 里的原始 token 数是真的，`total_cost_usd` 一概不用。
- 本模块给出的是**按公开价目表重算的估值，不是账单**。
  配置里的模型名是 `deepseek-v4-pro[1m]`（1M 上下文），而价目表只列 `deepseek-v4-pro`——
  长上下文是否加价未确认。对外报数必须带这条限定。
- 价目表里没有的模型**返回 None，不猜、不回退到任何默认价**。
  静默回退到一个「差不多」的价格，正是本项目反复栽过的那类错误：
  你以为在用 A，实际在用 B，而且没有任何提示。
"""

from __future__ import annotations

import json
from pathlib import Path

_PRICES_PATH = Path(__file__).resolve().parent.parent / "prices.json"
_cache: dict | None = None


def _table() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(_PRICES_PATH.read_text(encoding="utf-8")) \
            if _PRICES_PATH.exists() else {}
    return _cache


def _rates(model: str) -> dict | None:
    """按模型名找单价。名字里的方括号后缀（如 `[1m]`）先原样查，再去掉后缀查。"""
    t = _table()
    if model in t:
        return t[model]
    base = model.split("[", 1)[0]
    return t.get(base)


_TOKEN_FIELDS = ("input_tokens", "output_tokens",
                 "cache_read_input_tokens", "cache_creation_input_tokens")


#  ⭐ 订阅制的价目表键。它不是「某个模型」，是**一种计费方式**：
#     跑在你自己的 Claude 订阅上，**不花美元，花额度**。
SUBSCRIPTION_KEY = "__subscription__"


def real_cost_usd(model: str, usage: dict) -> float | None:
    """按真实价目表算这一单的成本。**算不出就返回 None，绝不返回 0。**

    两种「算不出」，都必须是 None：

    1. **价目表里没有这个模型** —— 不知道单价。
    2. ⚠️ **token 全为 0** —— 不知道用量。这一条容易被漏：模型在表里，
       乘出来是 `0.0`，**看起来像个正常数字**，会被求和进总账、被当成
       「这单免费」。而真相是上游没报 usage（子代理交接就常这样）。
       `0.0` 比 `None` 危险，因为它不触发任何「未知」的呈现路径。

    调用方必须如实呈现「未知」，**不许拿回执里的合成价顶替**——
    那正是这个模块存在的理由（G-28）。
    """
    # ⭐ 订阅制：美元成本**确实是 0**，那不是「算不出」，是「已包含在订阅里」。
    #    ⛔ 这里返回 None 会要了命：自动驾驶有一条防线是「算不出成本就停」
    #    （因为「算不出」被当成 0 会让预算永远不耗尽），于是第一单就停机。
    #    ⚠️ 但 0 不等于免费——额度是真实的稀缺资源，只是不按美元计。
    #    真正的防线在别处：max_dispatches（不依赖成本）与撞额度上限即停。
    if model == SUBSCRIPTION_KEY:
        return 0.0
    r = _rates(model)
    if not r:
        return None
    if not any(usage.get(k) for k in _TOKEN_FIELDS):
        return None
    return (r["input_cache_miss_per_1m"] * usage.get("input_tokens", 0)
            + r["output_per_1m"] * usage.get("output_tokens", 0)
            + r["input_cache_hit_per_1m"] * usage.get("cache_read_input_tokens", 0)
            + r.get("cache_write_5m_per_1m", 0) * usage.get("cache_creation_input_tokens", 0)
            ) / 1e6


def price_source(model: str) -> str:
    """这一单的价钱按哪张表算的——写进台账，日后换表时能分辨新旧数据。

    ⚠️ **来源必须逐模型取，不能取顶层的。** 顶层 `_source` 是 DeepSeek 的定价页；
    照那样写，Claude 系后端的每一行台账都会盖上「来源：DeepSeek 官方定价页」的戳
    ——一个**看起来有出处、实际张冠李戴**的来源，比没有来源更糟，因为它会让人
    停止追问。2026-07-27 独立审查抓出（G-43）。
    """
    if model == SUBSCRIPTION_KEY:
        return ("走你的 Claude 订阅额度——**不花美元，但不是免费**。"
                "⚠️ 回执里的 total_cost_usd 是 Opus 价目表算的合成价"
                "（实测 12 个 token 报 $0.42），对订阅完全不成立，别拿它当钱看。")
    r = _rates(model)
    if not r:
        return "未知（价目表里没有这个模型）"
    t = _table()
    src = r.get("_source") or t.get("_source", "?")
    fetched = r.get("_fetched") or t.get("_fetched", "?")
    return f"{src} @ {fetched}"
