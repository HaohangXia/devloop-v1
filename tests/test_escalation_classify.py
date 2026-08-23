"""交接单的失败模式归类（H-2 的落地条件 + 唯一一次真跑暴露的洞）。

## ⛔ 两个洞，都让归类**在主路径上不工作**

### 洞一 · 判断顺序把一整类变成死代码

`autopilot.escalation` 的 elif 链是：

    if   "宪法命中"   in e: ...
    elif "闸未通过"   in e: ...
    elif "闸自身故障" in e or "gate_broken" in e: ...   # ← 永远轮不到

而 `cli.py::_run_unit` 写进台账的 error 前缀**恒为**「闸未通过：」——
不管闸返回 1 还是 2。⛔ 于是第三支在主路径上是**死代码**，
与它配套的建议「**先修环境再谈验收**。闸自己坏了的时候，任何结论都不可信」
**一次都没印出来过**。

⚠️ 这一条不是附赠：`gates.py` 那边把 FAIL 从 2 分岔成 1 之后，
**唯一**会因此改变行为的机器消费方就是这里。不改它，那个改动等于没做。

### 洞二 · 真实世界最常见的失败没有分类

2026-08-02 本项目产出的**唯一一份真实交接单**
（`.devloop/autopilot/resume-drill-escalation-r3-doc-spec-sync.md`）：

    失败原因（台账原文）：写任务零改动：worktree 里一个文件都没变
    ...
    ## 失败模式
    - **其它（见上方原文）** × 2
    ## 建议的换法方向
    - 若以上都不适用：**这单可能不该由工人做**。考虑改成只读的调查单。

⛔ 「零改动」是**写任务最常见的失败形态**，却落进兜底档，
拿到的建议还是错的——⚠️ 零改动恰恰说明工人**读懂了但没动手**，
改成只读调查单只会让它更不动手。

## ⚠️ 关于「⛔ 只从结构化字段取，不从自由文本猜」

那行标题现在还挂在代码上，而实现是 `"宪法命中" in e` ——**对自由文本做子串匹配**。
⛔ 本次不改这个机制（改它要动台账字段形状，而台账是失控防线的唯一真源），
但**注释必须与实现一致**：把话改成实话，比留一句好听的假话强。
"""

from __future__ import annotations

import json

from devloop import autopilot
from devloop.config import ProjectPaths
from devloop.plan import Budget, StagePlan, Task

_ROWS = "rows"


def _mk(tmp_path, errors: list[str]) -> tuple[ProjectPaths, StagePlan, Task]:
    proj = tmp_path / "p"
    (proj / ".devloop" / "tasks").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    paths = ProjectPaths(proj)
    paths.telemetry.parent.mkdir(parents=True, exist_ok=True)
    paths.telemetry.write_text("".join(
        json.dumps({"ts": f"2026-08-02T0{i}:00:00", "task": "t", "ok": False,
                    "gate_ok": False, "cost_usd_real": 0.0, "duration_s": 10,
                    "turns": 3, "error": e}, ensure_ascii=False) + "\n"
        for i, e in enumerate(errors)), encoding="utf-8")
    task = Task(id="t", tools="implement", retries=2, require_pass=("g1",))
    plan = StagePlan(
        id="s", goal="g", task_dir=proj / ".devloop" / "tasks", base="HEAD",
        budget=Budget(total_usd=1.0, reserve_usd=0.1, max_dispatches=9,
                      max_wall_min=30),
        tasks=(task,), require_pass=("g1",),
        source=proj / ".devloop" / "plans" / "s.toml")
    return paths, plan, task


def _classes(tmp_path, errors: list[str]) -> str:
    paths, plan, task = _mk(tmp_path, errors)
    return autopilot.escalation(paths, plan, task, "2026-08-02T00:00:00")


# ── 洞一 · 闸自身故障不许被「闸未通过」这个前缀吃掉 ──────────────

def test_闸自身故障要归到闸自身故障(tmp_path) -> None:
    """⛔ `_run_unit` 写台账的前缀恒为「闸未通过：」，而 code 2 的 summary
    以「闸自身故障：」开头——判断顺序反了，这一整类就是死代码。"""
    md = _classes(tmp_path, ["闸未通过：闸自身故障：闸以协议外的退出码 127 结束"] * 2)
    assert "闸自身故障" in md and "先修环境" in md, \
        "⛔ 闸真坏了却被归成「活没达标」，配套建议一次都没印出来"
    assert "闸未通过（活没达标）" not in md


def test_活没干好仍然归到闸未通过(tmp_path) -> None:
    """⚠️ 反向也要钉住：别为了修死代码把正常那档一起吃掉。
    ⭐ code 1 的 summary 里不含「闸自身故障」四个字——两处改动靠这条纪律咬合。"""
    md = _classes(tmp_path, ["闸未通过：1 过 / 1 未过：pytest（3 failed）"] * 2)
    assert "闸未通过（活没达标）" in md
    #  ⚠️ 判据落在**归类行**上，⛔ 不是「整份文档里不许出现这四个字」。
    #     第一版是后者，2026-08-02 被自己判红了——因为「失败模式」那节的
    #     **说明段**正当地要提到「闸自身故障」这一档（讲清哪几档读结构化字段）。
    #  ⭐ 那是判据维度错：要防的是「归错类」，不是「提到这个词」。
    kinds = [l for l in md.splitlines() if l.startswith("- **")]
    assert kinds, f"⛔ 一条归类都没有：{md[:200]}"
    assert not any("闸自身故障" in l for l in kinds), \
        f"⛔ 活没干好被归成了闸坏了：{kinds}"


# ── 洞二 · 零改动要有自己的一档 ───────────────────────────────────

def test_零改动不许落进兜底档(tmp_path) -> None:
    """⛔ 这是本项目唯一一份真实交接单里发生的事。"""
    md = _classes(tmp_path, ["写任务零改动：worktree 里一个文件都没变"] * 2)
    assert "其它（见上方原文）" not in md, \
        "⛔ 写任务最常见的失败形态落进了兜底档"
    assert "零改动" in md


def test_零改动的建议不许是改成只读调查单(tmp_path) -> None:
    """⛔ 零改动恰恰说明工人**读懂了但没动手**——改成只读调查单只会让它更不动手。
    ⚠️ 兜底建议本身没错，错在它被当成了这一类的答案。"""
    md = _classes(tmp_path, ["写任务零改动：worktree 里一个文件都没变"] * 2)
    #  ⛔ 只取「建议」这一节本身。⚠️ 第一版写的是 `split(...)[1]`，
    #     它把后面的页脚（里面有一行「任务书：`...`」）也切了进来，于是判据
    #     恒真——**一条恒过的断言比没有断言更坏**，它看起来还挺让人放心。
    tips = [l for l in md.split("## 建议的换法方向")[1].split("\n---")[0].splitlines()
            if l.startswith("- ")]
    assert len(tips) >= 2, f"⛔ 零改动只剩兜底那一条建议：{tips}"


# ── 既有分类不许回归 ──────────────────────────────────────────────

def test_宪法命中优先于一切(tmp_path) -> None:
    """⚠️ 宪法命中是「在等你批准」，⛔ 不是「干得不好」——它必须排最前。"""
    md = _classes(tmp_path, ["宪法命中：改了受保护文件"] * 2)
    assert "需要人批准" in md


def test_空error本身就该被报出来(tmp_path) -> None:
    md = _classes(tmp_path, ["", ""])
    assert "台账里 error 是空的" in md


def test_从真闸输出走完整链路两档都归对(tmp_path) -> None:
    """⛔ 上面那几条用的是**手写**的台账字符串——它会与 `_run_unit` 实际写的漂移。

    ⭐ 这一条从真的 `run_gates` 出发，按 `cli.py::_run_unit` 的原样拼出 error，
    再走归类。⚠️ 两处改动（gates 分岔 / 归类换序）是靠「code 1 的说辞里不许出现
    『闸自身故障』」这一条纪律咬合的，**只有走真链路才验得到它**。
    """
    import textwrap

    from devloop.gates import run_gates

    proj = tmp_path / "q"
    (proj / ".devloop").mkdir(parents=True)
    g = proj / ".devloop" / "gates.sh"
    paths = ProjectPaths(proj)

    def _err(body: str, rp: list[str]) -> str:
        g.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
        #  ⚠️ 这个前缀是从 cli.py::_run_unit 抄来的，⛔ 改那边要改这里。
        return f"闸未通过：{run_gates(paths, target=proj, require_pass=rp).summary()}"

    fail = _err(r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed\n'
        exit 1
    """, ["语法", "pytest"])
    assert "闸自身故障" not in fail, \
        f"⛔ 活没干好的台账行里出现了「闸自身故障」，归类会读反：{fail[:120]}"

    skip = _err(r"""
        printf 'PASS\t语法\t好\n'
        printf 'SKIP\tpytest\t被开关跳过\n'
        exit 0
    """, ["语法", "pytest"])
    assert "闸自身故障" in skip, "⛔ 没验到的东西没被标成闸不可用，归类会读成活没干好"


def test_归类的注释不许再声称只从结构化字段取() -> None:
    """⛔ 实现是对自由文本做子串匹配。⚠️ 留一句好听的假话，
    比说实话更坏——它会让下一个人以为这里已经是结构化的了。"""
    import inspect

    src = inspect.getsource(autopilot.escalation)
    assert "只从结构化字段取，不从自由文本猜" not in src, \
        "⛔ 注释还在声称一件代码没做到的事"
