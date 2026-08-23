"""⛔ 早报不许把「你现在该干什么」那半句剪掉（G-115 · 同一毛病第三次）。

## 前两次

BACKLOG G-103 记着：

> 工具停着，而它印给人的**唯一一条自救命令被砍成半句**（`stderr[:200]`，
> 屏幕停在 `constitution anchor --pro`）。⚠️ `nightly.py` 犯过同一个毛病
> （砍 100 字），⭐ **两次都恰好砍在「你该干什么」那句上——那句话总在最后**。

## ⛔ 第三次：`nightly.py` 那个 `[:100]` 从来没被拿掉

挂一夜之后，人早上敲的第一条命令就是 `devloop nightly`。
而撞额度那种停机，被剪掉的正好是**唯一一句自救说明**：

    「…也可以等到 08-05 01:36。⏸ 剩余任务未派完，恢复后重跑本阶段即可续上」

⭐ **「你该干什么」这句话在中文里总在最后**，而截断总是从后面砍。
⛔ 两者相加 = 这个截断**专门**砍掉最该留的那一句。

## ⭐ 修法：这一行不截

`Stop.detail` 是**工具自己拼的**（不是用户输入、不是子进程输出），长度天然有界。
⚠️ 截断的理由（防止一行刷屏）在这里不成立，⛔ 而代价是砍掉自救说明。
"""

from __future__ import annotations

import json

from devloop import nightly


def _stage(tmp_path, detail: str, *, kind: str = "ratelimit") -> None:
    d = tmp_path / ".devloop" / "autopilot"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.json").write_text(json.dumps({
        "stage": "s", "started_at": "2026-08-04T20:00:00",
        "stop": {"why": "订阅额度耗尽", "kind": kind, "detail": detail},
    }, ensure_ascii=False), encoding="utf-8")


#  ⭐ 真实长度：按 `cli.py` 撞额度那处的拼法，实测 139 字。
_REAL = ("已用满 5 小时窗口（session）。可以等到 08-05 01:36 自动恢复，"
         "也可以换一个后端继续；⏸ 剩余任务未派完，恢复后**重跑本阶段即可续上**。")


def test_自救说明不许被剪掉(tmp_path) -> None:
    """⭐ 判据落在**那句话本身**，⛔ 不是「字数够不够」。"""
    _stage(tmp_path, _REAL)
    out = nightly.report(tmp_path)
    assert "重跑本阶段即可续上" in out, (
        f"⛔ 唯一那句自救说明被剪掉了——而挂一夜之后人敲的第一条命令就是它。\n"
        f"实际输出：\n{out}")


def test_长度不是判据_加长了也不许剪(tmp_path) -> None:
    """⛔ 防「把 100 改成 200 就算修好了」——那只是把同一个坑挪远一点。

    ⚠️ G-103 第一次的修法就是把 `[:200]` 换成一个不砍命令的截断器，
    ⭐ 而 `nightly` 这处的正解是**根本不截**：`detail` 是工具自己拼的。
    """
    _stage(tmp_path, "前" * 400 + "。⭐ 你现在该做的是：重跑本阶段。")
    out = nightly.report(tmp_path)
    assert "你现在该做的是：重跑本阶段" in out, \
        "⛔ 换了个更大的数字，坑还在原地"


def test_没有停机记录时不制造噪音(tmp_path) -> None:
    """⛔ 防回归：正常收尾的阶段不该被这条改动带出多余输出。"""
    _stage(tmp_path, "全部跑完", kind="done")
    out = nightly.report(tmp_path)
    assert "全部跑完" not in out, "收尾正常的阶段不该进「先看这里」"


def test_源码里不许再出现这种硬截断() -> None:
    """⭐ 判据落在**代码形状**上——⛔ 这毛病已经犯了三次，说明它会重犯。

    ⚠️ 与前几条不重复：那几条测的是「这一次对不对」，这条测的是
    「下一个人会不会再写一个」。
    """
    import inspect
    import re

    src = inspect.getsource(nightly)
    bad = re.findall(r"stop\.get\([^)]*\)[^\n]*\[:\d+\]", src)
    assert not bad, (
        f"⛔ `nightly.py` 里又出现了对停机说明的硬截断：{bad}\n"
        f"⭐ 那句话在中文里总在最后，而截断总是从后面砍——"
        f"两者相加 = 专门砍掉最该留的那一句。")
