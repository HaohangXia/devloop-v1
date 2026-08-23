#!/usr/bin/env python
"""复现「台账并发追加会丢行 / 写坏行」——⛔ 这条推翻了 BACKLOG G-41 的结论。

G-41 记的是「16 线程并发写台账 → 16 行齐全、0 行无法解析 ✅ 未损坏」。
⚠️ 那是**一次**试验。本脚本多次重复、并用**不等长**的行（真实台账的行长
差很多：有 error 文本的行比没有的长好几倍），看它是不是稳定成立。

⛔ 为什么这件事很贵：自动驾驶的每一条防线（花了多少 / 派了几次 / 哪些绿了）
**全都只从台账读**。丢行 = 防线读到偏小的数 = 预算永不到顶、已绿的单被重派；
坏行 = `json.loads` 抛 → 读台账的四处全是裸列表推导 → 整个台账读不出来。
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from devloop import telemetry                                    # noqa: E402
from devloop.dispatch import DispatchResult                      # noqa: E402
from devloop.models import Receipt                               # noqa: E402


def one_trial(threads: int, per: int, vary: bool) -> tuple[int, int]:
    """返回 (实际行数, 坏行数)。期望行数 = threads * per。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"

        def worker(k: int) -> None:
            for i in range(per):
                # ⚠️ 不等长：真实台账里带 error 文本的行长好几倍
                err = ("x" * (37 * (k + 1) % 400)) if vary else None
                telemetry.record(
                    p, DispatchResult(f"t{k}-{i}",
                                      Receipt(is_error=bool(err), num_turns=1,
                                              usage={"input_tokens": 1,
                                                     "output_tokens": 1}),
                                      None, error=err),
                    model="m", tools="readonly")

        ts = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        bad = 0
        for l in lines:
            try:
                json.loads(l)
            except ValueError:
                bad += 1
        return len(lines), bad


def main() -> int:
    print("试验：多次重复，看「丢行 / 坏行」是不是稳定不发生\n")
    total_bad_trials = 0
    for threads, per, vary, label in (
        (16, 1, False, "G-41 原样：16 线程 × 1 行 · 等长"),
        (8, 80, True, "8 线程 × 80 行 · 不等长"),
        (16, 40, True, "16 线程 × 40 行 · 不等长"),
    ):
        want = threads * per
        lost = wrong = 0
        TRIALS = 40
        for _ in range(TRIALS):
            n, bad = one_trial(threads, per, vary)
            if n != want:
                lost += 1
            if bad:
                wrong += 1
        flag = "⛔" if (lost or wrong) else "✓ "
        print(f"  {flag} {label}")
        print(f"       {TRIALS} 次里：丢行 {lost} 次 · 出现坏行 {wrong} 次")
        total_bad_trials += lost + wrong
    print()
    if total_bad_trials:
        print("  ⛔ 并发写台账**不安全**——G-41「未损坏」那条结论不成立。")
    else:
        print("  ✓ 本轮没复现出问题（⚠️ 不等于安全，只是没抓到）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
