"""`RateLimit.remaining_hours` 的行为覆盖。

⭐ 这里最关键的一条是 `resets_at == 0 → None`：⛔ 不许退回 0，
   否则调用方会把「不知道」当「马上恢复」，撞一次真花一次额度。
"""

from __future__ import annotations

import time

from devloop.quota import RateLimit


def test_returns_none_when_reset_unknown() -> None:
    """resets_at 为 0（兜底层拿不到时刻）→ None，⛔ 不是 0.0。"""
    rl = RateLimit(status="rejected", resets_at=0.0, kind="")
    assert rl.remaining_hours() is None


def test_returns_zero_when_reset_in_past() -> None:
    """恢复时刻已过 → 0.0，不是负数（避免调用方睡负值空转）。"""
    rl = RateLimit(status="rejected", resets_at=time.time() - 3600, kind="five_hour")
    result = rl.remaining_hours()
    assert result == 0.0


def test_returns_hours_when_reset_in_future() -> None:
    """恢复时刻在未来 → 剩余小时数（float）。"""
    rl = RateLimit(
        status="rejected",
        resets_at=time.time() + 2 * 3600,
        kind="five_hour",
    )
    result = rl.remaining_hours()
    assert result is not None
    #  ⚠️ 时间会流逝，取范围而不是精确值：约 2 小时，允许几秒抖动。
    assert 1.99 < result <= 2.0
