"""⛔ 工人超时被杀时，它已经吐出来的东西不许扔（G-110）。

## 这条是怎么来的

2026-08-04 首次跨项目真派单：工人跑了 3000 秒撞上硬死线被 `subprocess.run`
打死。`dispatch.py` 当时的写法是：

```python
except subprocess.TimeoutExpired:
    return DispatchResult(task.name, None, None, error=f"工人超时（{cfg.timeout_s}s）")
```

⭐ `TimeoutExpired` 对象**手里就攥着** `.output` 和 `.stderr`——被杀之前
子进程写出来的全部内容，**其中就包含用了多少 token 的 usage 事件**。
⛔ 这段代码一眼没看就把它扔了。

于是：**那 50 分钟到底花了多少额度，事后永远算不出来**，连手工翻都翻不到
（`.devloop/reports/` 里 08-04 零文件——决定性证据）。

## ⛔ 最刺眼的是不对称

同一个函数里另外两种失败**都会**把原始流存成 `.raw.txt`：

| 失败形态 | 落盘？ |
|---|---|
| 事件流里没有 result 事件（`dispatch.py::_finish`） | ✅ `.raw.txt` |
| result 事件字段不合契约（同上） | ✅ `.raw.txt` |
| **子进程超时被杀** | ⛔ **什么都不存** |

⚠️ 而超时恰恰是**唯一一种花了满额时间**的失败——它最该留材料，却唯独它不留。

## ⭐ 与本项目那条铁律的关系

「钱花过了就必须留账」。⛔ 台账留下一行「工人超时」是不够的——
那一行里 `cost_usd_real` 是 null，而 `check_limits` 对 null 的处置是
**停批**（`prog.unknown_cost` 那一条）。所以扔掉 usage 事件的代价不只是
「查不到」，是**把整批自动驾驶卡死在一条本来算得出来的成本上**。
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from devloop import dispatch as D
from devloop.config import ProjectPaths
from devloop.models import TaskSpec, WorkerConfig


@pytest.fixture()
def paths(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".devloop").mkdir(parents=True)
    #  ⚠️ 派单硬要求规则摘要在场（`config.py` 明确拒绝「没有护栏就派单」）。
    (proj / ".devloop" / "rules-digest.md").write_text("规则\n", encoding="utf-8")
    return ProjectPaths(proj)


def _slow_worker(tmp_path, *, chatty: bool) -> str:
    """造一个「先说话、再赖着不走」的假工人——⭐ 与真工人被杀时的形状一致。

    ⚠️ 必须**先** flush 再 sleep，否则被杀时管道里什么都没有，
       测的就成了「空输出也不丢」这件没意义的事。
    """
    say = ""
    if chatty:
        ev = json.dumps({"type": "assistant", "usage": {
            "input_tokens": 1234, "output_tokens": 567}}, ensure_ascii=False)
        say = (f"import sys\n"
               f"sys.stdout.write({ev!r} + chr(10))\n"
               f"sys.stdout.flush()\n")
    p = tmp_path / "slow_worker.py"
    p.write_text(say + "import time\ntime.sleep(30)\n", encoding="utf-8")
    return str(p)


def _cfg(tmp_path, *, chatty: bool = True) -> WorkerConfig:
    return WorkerConfig(base_url="http://x", model="m",
                        auth_token="t", timeout_s=1)


def _run(paths, tmp_path, monkeypatch, *, chatty: bool = True):
    """真起一个会超时的子进程，⛔ 不 mock `subprocess`——被杀那一刻的行为才是被测对象。"""
    script = _slow_worker(tmp_path, chatty=chatty)
    monkeypatch.setattr(D, "build_cmd",
                        lambda cfg, **kw: [sys.executable, script])
    return D.dispatch_one(TaskSpec(path=tmp_path / "t.md", body="干活"), paths, _cfg(tmp_path),
                          tools="readonly", max_turns=5)


# ══════════════════════════════════════════════════════════════════════

def test_超时时把工人已经吐出来的内容落盘(paths, tmp_path, monkeypatch):
    """⭐ 主判据：被杀之前说过的话，盘上要找得到。"""
    res = _run(paths, tmp_path, monkeypatch)

    assert res.report_path is not None, "⛔ 超时时一个文件都没留——那 50 分钟就是黑洞"
    assert res.report_path.exists(), f"报了路径却没落盘：{res.report_path}"
    body = res.report_path.read_text(encoding="utf-8", errors="replace")
    assert "output_tokens" in body, (
        "⛔ 落盘了但没有工人真说过的内容——"
        f"存的是什么？{body[:200]!r}")


def test_超时的错误信息里要写明是被死线打死的(paths, tmp_path, monkeypatch):
    """⚠️ 「工人超时」三个字不够——人要知道**是哪个数**掐的，才知道去改哪儿。"""
    res = _run(paths, tmp_path, monkeypatch)
    assert res.error and "超时" in res.error
    assert "1" in res.error, f"没写出死线值：{res.error}"


def test_超时的说法要和自动驾驶的归类对得上(paths, tmp_path, monkeypatch):
    """⛔ 措辞是**接口**，不是文案。

    `autopilot.py` 的失败模式归类按 `"超时" in e` 分档。⚠️ 改这句话时若把
    「超时」二字换掉，那一档会静默清零、全部掉进「其它」——⭐ 而「其它」
    正是没人看的那一档。这条把这层隐式耦合钉住。
    """
    res = _run(paths, tmp_path, monkeypatch)
    assert "超时" in (res.error or ""), (
        "⛔ 超时的错误文本里没有「超时」二字——"
        "`autopilot.py` 的失败模式归类会把它扔进「其它」")


def test_超时时能把用量捞出来(paths, tmp_path, monkeypatch):
    """⭐ 这才是「留材料」的目的——⛔ 存了个人读不了的文件不算留住。

    ⚠️ 判据落在「工具自己解析出来的用量」上，不是「文件里有那几个字」。
    """
    res = _run(paths, tmp_path, monkeypatch)
    assert res.usage_tokens is not None, (
        "⛔ 事件流里有 usage 事件却没解析出来——"
        "那 50 分钟花了多少依旧算不出来")
    assert res.usage_tokens >= 1234 + 567


def test_工人一个字都没说时不许造一个空文件充数(paths, tmp_path, monkeypatch):
    """⛔ 「没东西可存」与「存了」是两件事，不许混。

    ⚠️ 造个空文件会让人以为「材料在这儿」，翻开却是空的——比没有更坏。
    """
    res = _run(paths, tmp_path, monkeypatch, chatty=False)
    assert res.report_path is None or not res.report_path.read_text(
        encoding="utf-8", errors="replace").strip(), \
        "工人没说话时不该有内容"
    #  ⭐ 但错误信息仍必须说清是超时
    assert res.error and "超时" in res.error


def test_正常路径不受影响(paths, tmp_path, monkeypatch):
    """⛔ 防回归：别为了修超时把正常的回执解析弄坏。"""
    ok = tmp_path / "ok_worker.py"
    ev = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                     "result": "干完了", "session_id": "s1",
                     "duration_ms": 10, "num_turns": 1,
                     "total_cost_usd": 0.01}, ensure_ascii=False)
    ok.write_text(f"print({ev!r})\n", encoding="utf-8")
    monkeypatch.setattr(D, "build_cmd", lambda cfg, **kw: [sys.executable, str(ok)])
    res = D.dispatch_one(TaskSpec(path=tmp_path / "t.md", body="干活"), paths,
                         WorkerConfig(base_url="http://x", model="m",
                                      auth_token="t", timeout_s=60),
                         tools="readonly", max_turns=5)
    assert res.receipt is not None, f"正常路径被打坏了：{res.error}"


def test_超时分支和另外两种失败一样都落盘(paths, tmp_path, monkeypatch):
    """⭐ 判据落在**不对称本身**上，而不是某一个分支。

    ⛔ 这条防的是「今天修了超时，明天又加一种失败形态、又忘了落盘」。
    ⚠️ 用 AST 数：`dispatch.py` 里每一个返回「没有 receipt」的 return，
       都必须带上一个非 None 的 report 路径参数（或显式说明为什么不带）。
    """
    import ast
    import inspect

    #  ⭐ **唯一的豁免**：子进程根本没起来 ⇒ 没花钱 ⇒ 确实没材料可留。
    #  ⛔ 豁免只能按「捕的是哪个异常」给，不许按行号——行号会漂。
    #  ⚠️ 往这个表里加东西之前先问一句：**那条路径上钱花了没有？**
    #     花了就不许豁免，哪怕材料只有半截。
    EXEMPT = {"FileNotFoundError"}

    src = inspect.getsource(D)
    tree = ast.parse(src)

    exempt_lines: set[int] = set()
    for h in ast.walk(tree):
        if not isinstance(h, ast.ExceptHandler) or h.type is None:
            continue
        names = {n.id for n in ast.walk(h.type) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(h.type) if isinstance(n, ast.Attribute)}
        if names & EXEMPT:
            exempt_lines |= {n.lineno for n in ast.walk(h)
                             if hasattr(n, "lineno")}

    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name not in ("_finish", "dispatch_one"):
            continue
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Return) and isinstance(n.value, ast.Call)):
                continue
            if n.lineno in exempt_lines:
                continue
            call = n.value
            if getattr(call.func, "id", "") != "DispatchResult":
                continue
            # DispatchResult(task, receipt, report_path, ...) —— 第 2 个位置是 receipt
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) \
                    and call.args[1].value is None:
                third = call.args[2] if len(call.args) >= 3 else None
                if third is None or (isinstance(third, ast.Constant)
                                     and third.value is None):
                    bad.append(n.lineno)
    assert not bad, (
        f"⛔ `dispatch.py` 里还有 {len(bad)} 处「没有回执**且**不留任何材料」的"
        f"返回（源码相对行 {bad}）——钱花过了，材料必须留。"
        f"\n⭐ 真存不下东西时，也要走「落一个空的 .raw.txt 并说明」，"
        f"别直接传 None。")
