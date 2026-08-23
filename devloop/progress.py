"""派单过程的实时心跳。

## ⛔ 为什么需要它

2026-08-01 首次在 eco-ob 上真跑：**17 分钟里控制台一个字都没有**，
全部在进程退出时一次性吐出来。当时只能靠 `tasklist | grep godot` 判断它还活着。

⚠️ 而目标是「挂一夜自己跑」——**看不见就不敢挂**。这是最直接卡住那个目标的一条，
而且最便宜。

## ⛔ 为什么不能改成「边跑边 print」

`_run_unit` 攒输出再一次性返回是**故意的**：并行派单时边跑边打印会把几路
输出绞在一起，看不出哪行属于哪单（那条注释就写在 `_run_unit` 的 docstring 里）。

所以这里走**两条流**：

| 流 | 内容 | 何时写 |
|---|---|---|
| stdout | 详细回执，按单元成块 | 单元跑完 |
| **stderr** | 短标记 + 心跳，带单元名 | **当场** |

短标记就算绞在一起也读得懂（每行自带单元名），而详细回执仍然是干净的块。

## ⛔ 为什么要心跳线程

闸是**全捕获的子进程**（`gates.py` 里 `subprocess.run(capture_output=True)`），
900 秒里拿不到任何中间输出。只能从外面定时报数。

⚠️ 实测：闸占了整单 1043 秒里的 **87%**。不给它心跳，等于整单 87% 的时间是黑的。
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from typing import IO, Iterator


def _hms(t: float) -> str:
    lt = time.localtime(t)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"


class Phase:
    """一个阶段的**结局槽**。⛔ 默认「没人说过坏消息」，收尾才印「完」。

    ⭐ 存在的理由：有些坏结局**不以异常的形式到达 `phase()`**。
    `dispatch.py:145` 把 `subprocess.TimeoutExpired` 吞成一个带 error 的返回值
    ——从 `phase()` 看来那次调用**正常返回**了，于是收尾行印「完」。
    2026-08-04 真派单就死在这上面（G-106）。

    ⚠️ 于是「阶段外面看得见的」与「阶段里面知道的」之间必须有一条通道，
    ⛔ 而通道的方向只能是「里面主动报」——外面猜不出来。
    """

    __slots__ = ("_why",)

    def __init__(self) -> None:
        self._why: str = ""

    def broke(self, why: str) -> None:
        """报一个**被吞掉的**坏结局。⚠️ 报了之后收尾行不再印「完」。

        ⛔ 只报第一次：后面的覆盖不掉前面的。第一个坏消息通常是根因，
        后面那些多半是它的连锁反应。
        """
        if not self._why:
            self._why = why

    @property
    def broken(self) -> str:
        return self._why


class Progress:
    """一个单元的心跳发生器。

    ⚠️ `stream` 默认 stderr——⛔ 不要改成 stdout，那会把详细回执的块结构冲散。
    """

    def __init__(self, unit: str, *, stream: IO[str] | None = None,
                 enabled: bool = True) -> None:
        self.unit = unit
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled
        self.t0 = time.time()

    def mark(self, msg: str) -> None:
        """当场写一行。⛔ 必须 flush——不 flush 就等于没有心跳。"""
        if not self.enabled:
            return
        now = time.time()
        line = f"[{_hms(now)} +{now - self.t0:.0f}s] {self.unit} · {msg}\n"
        try:
            self.stream.write(line)
            self.stream.flush()
        except Exception:
            # ⛔ 观测手段绝不能弄死被观测的东西。流坏了就闭嘴，不抛。
            self.enabled = False

    @contextlib.contextmanager
    def phase(self, name: str, *, every: float = 60.0,
                    expect_s: float | None = None,
                    limit_s: float | None = None) -> Iterator[Phase]:
        """把一个长阶段圈起来，期间定时报「我还活着」。

        ## 参数

        - `expect_s` —— **参考值**，「大概要多久」。⚠️ 要给：「已跑 700 秒」
          本身没意义，和「预计 900 秒」并排才看得出是不是卡了。
        - `limit_s` —— ⭐ **真死线**：到点这一阶段会被**强制终止**。
          ⛔ 与 `expect_s` 是两件完全不同的事，绝不许混：
          2026-08-04 那一跑屏幕上只有「预计 300s」，而真正会杀进程的是 3000s，
          两个数写在不同文件里、毫无关联（`cli.py` 的字面量 vs `models.py` 的默认值）。
          **人盯着屏幕看到的是「超预计了，也许快好了」，实际是「它会在 16:08 被处决」。**
          ⚠️ 不知道死线就别传——`None` 表示「不知道」，⛔ 不许编一个。

        ## 收尾行的三种结局（⛔ 不许再共用一个字）

        | 结局 | 印什么 |
        |---|---|
        | 正常走完，没人报坏消息 | `完` |
        | 里面抛了异常（含 `KeyboardInterrupt`） | `⛔ 断 · <异常类型>` |
        | 里面把异常吞了、主动 `ph.broke(...)` | `⛔ 断 · <原因>` |

        ⛔ 异常路径也必须停掉线程——漏掉不停，进程就退不掉，
        夜跑时表现为「跑完了但不返回」。所以收尾在 `finally` 里。
        ⚠️ 但**接**用的是 `except BaseException`：`KeyboardInterrupt` 不是
        `Exception` 的子类，而 Ctrl-C 正是人中断夜跑最常用的方式。
        """
        started = time.time()
        bits = []
        if expect_s:
            bits.append(f"预计 {expect_s:.0f}s")
        if limit_s:
            #  ⭐ 措辞要让人一眼分清「参考」和「到点就杀」。
            bits.append(f"**{limit_s:.0f}s 死线，到点强制终止**")
        self.mark(f"{name} 开始" + (f"（{' · '.join(bits)}）" if bits else ""))
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(every):
                el = time.time() - started
                tail = f" / 预计 {expect_s:.0f}s" if expect_s else ""
                over = " ⚠️ 已超预计" if expect_s and el > expect_s else ""
                #  ⭐ 死线也要进心跳：开始那一行会滚出屏幕，人真正盯着的是这一行。
                lim = f" · 距 {limit_s:.0f}s 死线还剩 {limit_s - el:.0f}s" if limit_s else ""
                self.mark(f"{name} 仍在跑 · 已 {el:.0f}s{tail}{over}{lim}")

        th = threading.Thread(target=beat, daemon=True, name=f"beat-{self.unit}")
        if self.enabled:
            th.start()
        ph = Phase()
        exc_name = ""
        try:
            yield ph
        except BaseException as exc:            # noqa: BLE001 —— 只观测，立刻原样抛回
            exc_name = type(exc).__name__
            raise
        finally:
            stop.set()
            if th.is_alive():
                th.join(timeout=1.0)
            used = time.time() - started
            if exc_name:
                self.mark(f"{name} ⛔ **断** · {exc_name} · 用了 {used:.0f}s")
            elif ph.broken:
                self.mark(f"{name} ⛔ **断** · {ph.broken} · 用了 {used:.0f}s")
            else:
                self.mark(f"{name} 完 · 用了 {used:.0f}s")
