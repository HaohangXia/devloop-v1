"""并发安全的时间戳。

**为什么单独一个模块**：分支名与回执文件名各自 `time.strftime("%Y%m%d-%H%M%S")`，
两处独立实现、同一个缺陷。并行派单时**同名任务同秒并发会撞**——2026-07-27 实测：
4 个线程为同名任务建 worktree，只成 1 路，其余报
`fatal: cannot lock ref 'refs/heads/devloop/same-20260727-001154'`；
回执文件名则直接互相覆盖（BACKLOG G-41）。

⚠️ **不同任务名并发是安全的**（实测 8/8 成功）——名字本身把它们区分开了。
撞的只有「同名任务并发」，也就是**重试同一单**、或两份任务书文件名 stem 相同时。

**为什么不用随机数**：随机让文件名不可预测，排查时对不上号；而且两次随机仍有概率相撞。
进程内单调计数器在单进程多线程下**保证唯一**，跨进程再靠 pid 兜底。
"""

from __future__ import annotations

import itertools
import os
import threading
import time

_counter = itertools.count()
_lock = threading.Lock()


def stamp(when: float | None = None) -> str:
    """`YYYYmmdd-HHMMSS-<pid><序号>` —— 同秒并发也不会重复。

    后缀两段各有分工，缺一不可：
    - `pid`：跨进程（两个终端同时派单）
    - 单调序号：同进程内多线程（`--parallel N`）

    ⚠️ 序号取模 1000 只为控制长度；同一进程一秒内派 1000 单以上才可能回绕，
    而单单最快也要几十秒——真撞上说明有别的问题，不是这里该防的。
    """
    t = time.strftime("%Y%m%d-%H%M%S", time.localtime(when) if when else time.localtime())
    with _lock:
        n = next(_counter) % 1000
    return f"{t}-{os.getpid() % 10000}{n:03d}"
