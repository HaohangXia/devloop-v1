"""每一单跑完都留一份**复核卷宗**（B④ 的真需求，2026-08-03）。

## ⛔ 缺陷：工具只在「要扔掉」的时候才写好材料

`cli.py` 里 `write_escalation` 的唯一调用点条件是 `stop.kind == "escalation"`
——**只有重试耗尽的失败单**才拿到一份结构化交接单（每次尝试、失败模式、
成本、闸的原文、任务书路径）。

⛔ 而**即将进你主线**的那些成功单，留下的只有：

| | |
|---|---|
| 提交正文 | **纯样板**：「由 DevLoop 编排方代为提交，仅落在隔离分支…未触碰主线」——干了什么、为什么，一个字没有 |
| 回执 | `.devloop/reports/*.json` 实测 **172 KB / 47 行**原始 SDK 事件流（`hook_started`、`hook_response`…）——agent 逐帧记录，不是复核材料 |

⭐ 这个不对称就是「复核一条 ≈ 重写一条」的机制：人被迫从样板提交
+ 172 KB 逐帧流里，**自己重建**「闸到底验了什么、没验什么」。

实证：2026-08-02 复核 `v1-ledger-fields`（**闸五道全绿**）用了变异测试 ×3、
边界探针 ×2、真台账对账、红检 ×3，**抓出 3 个洞**；工人产出 +156 行，
落到主线 +306 行（`telemetry.py` 那部分 2.9×）。合议票型 **0 票直接合**。

## ⛔ 卷宗只列事实，一个裁决词都不许有

⚠️ 一份写得漂亮的卷宗让人**跳过**真正的复核，比没有更坏——那就是新的假绿。
措辞纪律照抄交接单现成的那句：**「判据只有闸和宪法，交接单不裁决任何东西」**。

⭐ 所以卷宗回答的是**可证伪的事实**：
闸实际报出了哪几道 · 验收点名的是哪几道 · 两者差在哪 ·
这条分支碰了哪些文件 · 任务书声明的 `# 改动范围` 是什么 · 声明与实际差在哪。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devloop import dossier
from devloop.config import ProjectPaths
from devloop.models import TaskSpec

_ROW = {
    "ts": "2026-08-03T01:00:00", "task": "u1", "model": "claude-opus-4-7",
    "tools": "implement", "worker_ok": True, "gate_ok": True, "ok": True,
    "gate_code": 0, "gate_detail": "5 过 / 0 未过", "error": None,
    "setup_s": 0.2, "worker_s": 415.2, "gate_s": 58.9, "duration_s": 410.9,
    "turns": 13, "cost_usd_real": 0.0, "cache_read_tokens": 1050024,
}

_SPEC = """# 角色

x

# 任务

y

# 改动范围

- devloop/a.py
- tests/test_a.py

# 禁令

- z
"""


def _proj(tmp_path):
    p = tmp_path / "p"
    (p / ".devloop" / "tasks").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (p / ".devloop" / "tasks" / "u1.md").write_text(_SPEC, encoding="utf-8")
    return ProjectPaths(p)


def _spec(paths):
    return TaskSpec.load(paths.project / ".devloop" / "tasks" / "u1.md")


# ── ⛔ 四块事实必须都在 ────────────────────────────────────────────

def test_闸实际报出的与点名的都要列出来(tmp_path):
    """⭐ 「验收点名了 5 道」与「闸实际报出 5 道」是两件事——
    ⚠️ 点名一道闸**没报的**，那道就没验过（第①种假绿）。"""
    paths = _proj(tmp_path)
    md = dossier.build(paths, _spec(paths), _ROW,
                       gate_names=["语法", "测试守卫", "pytest"],
                       required=["语法", "pytest"],
                       changed=["devloop/a.py"], base="abc1234", sha="def5678")
    assert "语法" in md and "测试守卫" in md
    assert "点名" in md


def test_声明的改动范围与实际改动都要列出来(tmp_path):
    paths = _proj(tmp_path)
    md = dossier.build(paths, _spec(paths), _ROW,
                       gate_names=["pytest"], required=["pytest"],
                       changed=["devloop/a.py", "devloop/b.py"],
                       base="abc1234", sha="def5678")
    assert "devloop/a.py" in md and "tests/test_a.py" in md   # 声明的
    assert "devloop/b.py" in md                                # 实际多出来的


def test_越界的文件要单独标出来(tmp_path):
    """⭐ 这是卷宗唯一「算」出来的东西——⛔ 而它仍然是事实不是判断。"""
    paths = _proj(tmp_path)
    md = dossier.build(paths, _spec(paths), _ROW,
                       gate_names=["pytest"], required=["pytest"],
                       changed=["devloop/a.py", "devloop/b.py"],
                       base="abc1234", sha="def5678")
    assert "越界" in md and "devloop/b.py" in md


def test_没写改动范围的要说无声明而不是越界零个(tmp_path):
    """⛔ 把「没数据」和「零」混成一件事——那是判据维度错。

    ⚠️ `# 改动范围` 是**可选段**（`models.py`），本仓 15 份任务书里只有 3 份写了。
    ⛔ 不许因为没写就判红。
    """
    paths = _proj(tmp_path)
    (paths.project / ".devloop" / "tasks" / "u1.md").write_text(
        "# 角色\n\nx\n\n# 任务\n\ny\n\n# 禁令\n\n- z\n", encoding="utf-8")
    md = dossier.build(paths, _spec(paths), _ROW,
                       gate_names=["pytest"], required=["pytest"],
                       changed=["devloop/a.py"], base="abc1234", sha="def5678")
    assert "无声明" in md
    #  ⚠️ 判据不能是「文案里不许出现『越界 0』」——⛔ 那是**子串判据**，
    #     会把说明句「…不是「越界 0 个」」也判红（第一版就这么错的）。
    #  ⭐ 判的是「有没有真的印出一条越界**计数行**」。
    counted = [l for l in md.splitlines()
               if l.startswith("- ") and "越界" in l and "不是" not in l]
    assert not counted, f"⛔ 没声明却算出了越界计数：{counted}"


def test_分段耗时与轮数要在里面(tmp_path):
    paths = _proj(tmp_path)
    md = dossier.build(paths, _spec(paths), _ROW,
                       gate_names=["pytest"], required=["pytest"],
                       changed=[], base="abc1234", sha="def5678")
    assert "415" in md and "58" in md and "13" in md


# ── ⛔ 一个裁决词都不许有 ──────────────────────────────────────────

_VERDICT_WORDS = ("建议合并", "可以合", "应该合", "推荐合", "值得合",
                  "不建议", "别合", "质量好", "质量高", "看起来不错")


def test_卷宗里不许出现任何裁决词(tmp_path):
    """⛔ **这条是卷宗的存在前提。**

    ⚠️ 一份写得漂亮的卷宗让人跳过真正的复核，比没有更坏——那就是新的假绿。
    ⭐ 判据落在**禁词表**上：裁决词的有无是能直接量的。
    """
    paths = _proj(tmp_path)
    for changed in ([], ["devloop/a.py"], ["devloop/a.py", "x.py"]):
        for row in (_ROW, {**_ROW, "gate_code": 1, "ok": False},
                    {**_ROW, "gate_code": 2, "ok": False}):
            md = dossier.build(paths, _spec(paths), row,
                               gate_names=["pytest"], required=["pytest"],
                               changed=changed, base="a", sha="b")
            for w in _VERDICT_WORDS:
                assert w not in md, f"⛔ 卷宗里出现了裁决词「{w}」"


def test_卷宗明说自己不裁决(tmp_path):
    """⚠️ 光是不写裁决词不够——要**明说**，否则读的人自己会把它当结论。"""
    paths = _proj(tmp_path)
    md = dossier.build(paths, _spec(paths), _ROW,
                       gate_names=["pytest"], required=["pytest"],
                       changed=[], base="a", sha="b")
    assert "不裁决" in md or "判据只有闸和宪法" in md


# ── 接线：⛔ 成功的单也要写 ────────────────────────────────────────

def test_成功的单也要落盘卷宗(tmp_path):
    paths = _proj(tmp_path)
    p = dossier.write(paths, "s1", _spec(paths), _ROW,
                      gate_names=["pytest"], required=["pytest"],
                      changed=["devloop/a.py"], base="a", sha="b")
    assert p.exists() and p.read_text(encoding="utf-8").strip()


def test_卷宗写在运行产物目录里(tmp_path):
    """⛔ 不许写进受 `records` 守卫的路径，也不许污染活工作区。

    ⚠️ `.devloop/findings.jsonl` 就因为没进 gitignore 而让宪法 A-2 全阶段连坐；
    ⭐ `RUNTIME_DIRS`（jobs/handoff/autopilot）是已经被定性为「运行产物」的地方。
    """
    from devloop import records

    paths = _proj(tmp_path)
    p = dossier.write(paths, "s1", _spec(paths), _ROW,
                      gate_names=["pytest"], required=["pytest"],
                      changed=[], base="a", sha="b")
    rel = p.relative_to(paths.project / ".devloop")
    assert rel.parts[0] in records.RUNTIME_DIRS, \
        f"⛔ 卷宗写到了 {rel.parts[0]}/——那不是运行产物目录，会被记录守卫盯上"


def test_生产路径真的会写卷宗() -> None:
    """⛔ 这个项目栽过三次「实现了但生产路径没调」。

    ⚠️ 判据落在 AST 的「`_run_unit` 里有一次 `dossier.write` 调用」上，
    ⛔ 不落在子串——注释里提一句不算接上了。
    """
    import ast
    import inspect

    from devloop import cli

    fn = next((n for n in ast.walk(ast.parse(inspect.getsource(cli)))
               if isinstance(n, ast.FunctionDef) and n.name == "_run_unit"), None)
    assert fn is not None, "⛔ 找不到 _run_unit"
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "write"
             and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "dossier"]
    assert calls, "⛔ `_run_unit` 里没有 dossier.write——卷宗没接进生产路径"


def test_写卷宗失败不许打死整单(tmp_path):
    """⛔ 卷宗是**辅助材料**。⚠️ 因为写不出材料而让一单已经干完的活失败，
    是本末倒置——`doctor` 的记账那处已经立过同一条规矩。"""
    import ast
    import inspect

    from devloop import cli

    src = inspect.getsource(cli._run_unit)
    i = src.index("dossier.write")
    #  ⚠️ 判据：调用点**在一个 try 里**。⛔ 判子串「try」会误命中别处的 try。
    fn = ast.parse(inspect.getsource(cli)).body
    found = False
    for n in ast.walk(ast.parse(src.replace("def _run_unit", "def f", 1))):
        if isinstance(n, ast.Try):
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "write"
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "dossier"):
                    found = True
    assert found, "⛔ dossier.write 没包在 try 里——写卷宗失败会打死整单"
