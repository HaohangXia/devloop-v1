"""⛔ 进程被强杀 / Ctrl-C 时，花过的钱也必须在台账上留痕（G-108）。

## 这条是怎么来的

2026-08-04 首次跨项目真派单，烧掉 50 分钟订阅额度。
`.devloop/telemetry.jsonl` 跑之前 10 行，跑之后**还是 10 行**。

原因不是「兜底没兜住」，是**两条记账路径都排在整单最后**：

| 路径 | 位置 | 这一跑走到了吗 |
|---|---|---|
| 主路径 `telemetry.record` | `cli.py`，在工人 + 闸 + 固化**全部做完之后** | ⛔ 没有（死在闸里） |
| 补记 | `cli.py` 的 `except Exception` 里 | ⛔ 没有（强杀不走 except） |

⚠️ 而且 `except Exception` **接不住 `KeyboardInterrupt`**（它是 `BaseException`）
——⭐ 而 Ctrl-C 正是人停掉一个夜跑最常用的方式。

## ⛔ 改成 `finally` 没有用

强杀时 Python 的 `finally` / `atexit` **一行都不执行**。
⭐ 所以要动的是**记账的时刻**，不是记账写在哪个块里：
**钱一开始花，就先落一行。**

## ⭐ 后果不只是「查不到」

四条失控防线**全都只从台账读**（`autopilot.read_progress`）。账本空
⇒ `dispatches = 0` ⇒ 原样重跑等于 `max_dispatches` **重新发牌**，
刚烧掉的额度白烧且不算数，而屏幕上还印着「已花 $0.0000 · 已派 0 次」。

## 判据落在能直接量的东西上

⛔ 不是「代码里有没有写某几个字」——现有的
`test_audit_fixes_0730.py` 就是那么断言的（`inspect.getsource` + 字符串），
⚠️ 于是这条铁律**一次都没被机器验证过**，直到这次真派单才发现它是漏的。

⭐ 这里**真的杀一个进程**，然后数台账。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from devloop import telemetry as T


def _rows(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ══════════════════════════════════════════════════════════════════════
#  ① 开跑行本身
# ══════════════════════════════════════════════════════════════════════

def test_开跑行在花钱之前就落盘(tmp_path):
    p = tmp_path / "t.jsonl"
    T.record_open(p, task="t1", unit_id="u1", model="m", tools="readonly",
                  price_key="pk", stage="s")
    rows = _rows(p)
    assert len(rows) == 1
    assert rows[0]["event"] == "open"
    assert rows[0]["task"] == "t1" and rows[0]["unit_id"] == "u1"
    #  ⛔ 开跑行不许自称有结论——它只说「开始花钱了」。
    assert rows[0].get("ok") is None and rows[0].get("gate_code") is None


def test_只有开跑行时算作被中断(tmp_path):
    """⭐ 主判据：被杀之后台账上看得出「有一单花过钱、没等到结果」。"""
    p = tmp_path / "t.jsonl"
    T.record_open(p, task="t1", unit_id="u1", model="m", tools="readonly",
                  price_key="pk", stage="s")
    us = T.units(_rows(p))
    assert len(us) == 1
    assert us[0]["interrupted"] is True


def test_收工行到了就配成一单不许数成两次(tmp_path):
    """⛔ 这条是第 ①条的**必备配套**。

    ⚠️ 开跑 + 收工两行，若按行数算「今天派了几次单」，一单会被数成两次
    ——计划里写最多派 2 次，跑完**一单**就被自己的刹车停掉，活少干一半。
    """
    p = tmp_path / "t.jsonl"
    T.record_open(p, task="t1", unit_id="u1", model="m", tools="readonly",
                  price_key="pk", stage="s")
    _close(p, task="t1", unit_id="u1")
    us = T.units(_rows(p))
    assert len(us) == 1, f"一单被数成了 {len(us)} 单"
    assert not us[0].get("interrupted")
    assert us[0]["ok"] is True, "配对之后要以收工行为准"


def test_老行没有event字段也照旧算一单(tmp_path):
    """⛔ 台账里已有 10 行老格式。⚠️ 改读法不许把历史读没了。"""
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"ts": "2026-08-01T00:00:00", "task": "old",
                             "ok": True}, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    us = T.units(_rows(p))
    assert len(us) == 1 and not us[0].get("interrupted")


def test_同一单重试两次算两单(tmp_path):
    """⚠️ 配对判据是 `unit_id` 不是 `task`——⛔ 按任务名配会把重试吞掉。"""
    p = tmp_path / "t.jsonl"
    for u in ("u1", "u2"):
        T.record_open(p, task="t1", unit_id=u, model="m", tools="readonly",
                      price_key="pk", stage="s")
        _close(p, task="t1", unit_id=u)
    assert len(T.units(_rows(p))) == 2


# ══════════════════════════════════════════════════════════════════════
#  ② 被中断的单要在人已经在看的那张表里露面
# ══════════════════════════════════════════════════════════════════════

def test_被中断的单在闸的分档里单列一档(tmp_path):
    """⛔ 不许折进「没跑闸」或「无判定记录」——那两档说的是别的事。

    ⭐ 而「各档相加恒等于单元数」这条不变式必须保住：
       它是唯一能抓住「有东西掉进没人统计的缝里」的判据。
    """
    p = tmp_path / "t.jsonl"
    T.record_open(p, task="a", unit_id="u1", model="m", tools="readonly",
                  price_key="pk", stage="s")
    T.record_open(p, task="b", unit_id="u2", model="m", tools="readonly",
                  price_key="pk", stage="s")
    _close(p, task="b", unit_id="u2")

    us = T.units(_rows(p))
    counts, _ = T.gate_buckets(us)
    assert counts.get("被中断") == 1, f"被中断没单列：{counts}"
    assert sum(counts.values()) == len(us), \
        f"⛔ 各档相加 {sum(counts.values())} ≠ 单元数 {len(us)}——有东西掉进缝里了"


# ══════════════════════════════════════════════════════════════════════
#  ③ ⭐ 真杀一个进程 —— ⛔ 这才是这条铁律第一次被机器验证
# ══════════════════════════════════════════════════════════════════════

def test_真杀掉写台账的进程之后账还在(tmp_path):
    """⭐ 判据落在「杀完之后文件里有没有那一行」，⛔ 不是「源码里有没有那几个字」。

    ⚠️ 这里用 `Popen.kill()`（Windows 上就是 TerminateProcess），
       与 2026-08-04 被外部 `timeout` 掐断是同一种杀法：
       ⛔ except / finally / atexit **一行都不执行**。
    """
    led = tmp_path / "t.jsonl"
    script = tmp_path / "victim.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from pathlib import Path\n"
        "from devloop import telemetry as T\n"
        "try:\n"
        f"    T.record_open(Path({str(led)!r}), task='t1', unit_id='u1',\n"
        "                  model='m', tools='readonly', price_key='pk', stage='s')\n"
        "    print('OPENED', flush=True)\n"
        "    time.sleep(60)\n"
        "finally:\n"
        "    Path(r'" + str(tmp_path / "finally_ran.txt") + "').write_text('x')\n",
        encoding="utf-8")

    pr = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8")
    try:
        assert pr.stdout.readline().strip() == "OPENED", "桩进程没起来"
        pr.kill()
        pr.wait(timeout=10)
    finally:
        if pr.poll() is None:
            pr.kill()

    #  ⚠️ 先钉住前提：这确实是一种「收尾代码不执行」的杀法。
    #     ⛔ 否则这条测的就成了别的东西。
    assert not (tmp_path / "finally_ran.txt").exists(), \
        "前提没成立：这个杀法居然让 finally 跑了，换一种杀法再测"

    assert led.exists(), "⛔ 被杀之后台账文件都不存在——钱花了一个字没留"
    us = T.units(_rows(led))
    assert len(us) == 1 and us[0]["interrupted"] is True, \
        f"⛔ 被杀之后台账里读不出「有一单被中断」：{us}"


# ══════════════════════════════════════════════════════════════════════
#  ④ ⭐ 后果判据：真派单路径上，开跑行在 dispatch_one **之前**
# ══════════════════════════════════════════════════════════════════════

def test_开跑行确实排在花钱之前() -> None:
    """⛔ 落在 `dispatch_one` 之后 = 白改：那正是原来的毛病。"""
    import ast
    import inspect

    from devloop import cli

    tree = ast.parse(inspect.getsource(cli._run_unit).lstrip())
    opens = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", getattr(n.func, "id", "")) == "record_open"]
    spends = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", getattr(n.func, "id", "")) == "dispatch_one"]
    assert opens, "⛔ `_run_unit` 从没落过开跑行"
    assert spends, "找不到 `dispatch_one`，先看是不是重构过"
    assert min(opens) < min(spends), (
        f"⛔ 开跑行在第 {min(opens)} 行，而钱在第 {min(spends)} 行就花了"
        f"——顺序反了等于没修")


def _close(p: Path, *, task: str, unit_id: str) -> None:
    """写一条收工行。⚠️ 直接拼字典而不是走 `record`——本文件测的是**读法**，
    ⛔ 不该被 `DispatchResult` 的构造细节拖住。"""
    time.sleep(0.01)          # 让 ts 严格递增，⚠️ 别让配对依赖写入顺序之外的东西
    T._append(p, json.dumps(
        {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "close",
         "unit_id": unit_id, "task": task, "ok": True, "gate_code": 0,
         "cost_usd_real": 0.0}, ensure_ascii=False) + "\n")
