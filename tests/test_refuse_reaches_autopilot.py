"""⛔ 硬拒必须**两条路都跑到**，不能只挂在一个命令行开关上。

## 缺陷的形状（2026-08-03 对抗审计实测）

`constitution.toml` 里有一条：

```toml
[refuse]
full_tools_when_detached = true
```

意思是「无人值守时不许给 `full` 档」——因为 `full` 比 `implement` 多的
**就是 WebFetch**，那是一条**对外通道**，而「对外发送」事后零痕迹：
diff 里没有、台账里没有、回执里也没有。**判不了，只能事前不给能力。**

⛔ 而 `refuse_preflight` 的**唯一调用点**在 `cmd_dispatch` 里。
`cmd_autopilot` 走的是另一条路：

```
cmd_autopilot → autopilot.preflight()   ← 只 load + verify_anchor
              → _run_unit(tools="full")
              → dispatch.py 给 claude 传 --allowedTools ...,WebFetch
```

⚠️ 于是在计划里写 `tools = "full"` 就能让工人**整夜**握着 WebFetch，
而那行配置在这条路上是**纯装饰**。`plan.load()` 也不校验 `tools` 的取值。

## ⭐ 根因：判据挂在开关上，不挂在事实上

参数原来叫 `detach`——一个**只存在于 dispatch 那条路**的命令行开关。
自动驾驶没有这个开关，于是判据在那条路上无从谈起。

⭐ 改名为 `unattended`（「有没有人在看」），自动驾驶恒传 `True`。
⚠️ 这不是措辞问题：**概念错了，接线就一定漏**。
"""

from __future__ import annotations

import ast
import inspect
import subprocess

import pytest

from devloop import autopilot, plan as plan_mod
from devloop.config import ConfigError, ProjectPaths

_TOML = """schema = 1

[[protected_file]]
clause = "A-1"
title  = "不得修改宪法本身"
path   = ".devloop/constitution.toml"

[[unjudged]]
clause = "X-9"
why    = "判不了"

[tree]
coverage = "none"
why      = "夹具项目没有树内基线"

[refuse]
full_tools_when_detached = true
"""

_PLAN = """[plan]
version = 1

[stage]
id       = '夜跑'
goal     = '演示无人值守的硬拒'
task_dir = '.devloop/tasks'
base     = 'HEAD'
parallel = 1

[stage.budget]
total_usd      = 5.0
reserve_usd    = 1.0
max_dispatches = 3
max_wall_min   = 60
watchdog_k     = 2

[[task]]
id      = 't1'
tools   = '{tools}'
retries = 1
  [task.accept]
{accept}
"""

#  ⚠️ 只读单**不建 worktree、不跑闸**，点名闸会被 `plan.load` 当场拒绝
#     （「点名等于空守卫，而结果会被记成绿」）。⭐ 夹具必须尊重这条不变量，
#     ⛔ 不许为了让测试跑起来去放宽它。
def _accept(tools: str) -> str:
    return ("  why = '只读单，没有机器判据'" if tools == "readonly"
            else "  require_pass = ['pytest']")

_TASK = """# 角色

x

# 任务

y

# 验收

- pytest
"""


def _proj(tmp_path, tools: str) -> ProjectPaths:
    p = tmp_path / "proj"
    (p / ".devloop" / "tasks").mkdir(parents=True)
    (p / ".devloop" / "plans").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                             encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(_TOML, encoding="utf-8")
    (p / ".devloop" / "tasks" / "t1.md").write_text(_TASK, encoding="utf-8")
    (p / ".devloop" / "plans" / "夜跑.toml").write_text(
        _PLAN.format(tools=tools, accept=_accept(tools)),
        encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=p, check=True,
                   capture_output=True)
    return ProjectPaths(p)


def _preflight(paths):
    from devloop import constitution as C
    con = C.load(paths)
    C.write_anchor(paths, con)
    sp = plan_mod.load(paths.project / ".devloop" / "plans" / "夜跑.toml")
    return autopilot.preflight(paths, sp)


# ── ① 自动驾驶路径上，硬拒必须真的挡住 ────────────────────────────

def test_计划里写full档时自动驾驶必须拒绝开跑(tmp_path) -> None:
    """⛔ **本文件的核心。** 这条在修复前是**通过**的——整夜的 WebFetch。"""
    paths = _proj(tmp_path, "full")
    with pytest.raises(ConfigError) as e:
        _preflight(paths)
    msg = str(e.value)
    assert "full" in msg, msg
    assert "WebFetch" in msg, f"⛔ 没说清拒的是什么能力：{msg}"
    assert "t1" in msg, f"⛔ 没说是哪一单过不了：{msg}"


def test_implement档照常放行(tmp_path) -> None:
    """⚠️ 拒绝不许扩大射程——`implement` 没有 WebFetch，本来就该放行。
    ⛔ 判据只写「full 被拒」会被一个「什么都拒」的实现通过。"""
    paths = _proj(tmp_path, "implement")
    assert _preflight(paths) is not None


def test_readonly档照常放行(tmp_path) -> None:
    paths = _proj(tmp_path, "readonly")
    assert _preflight(paths) is not None


def test_项目关掉这条时full档放行(tmp_path) -> None:
    """⭐ 配置得说话算话：写 `false` 就该真的放行。
    ⚠️ 否则那行配置又变成装饰——只是方向反了。"""
    paths = _proj(tmp_path, "full")
    f = paths.project / ".devloop" / "constitution.toml"
    f.write_text(f.read_text(encoding="utf-8").replace(
        "full_tools_when_detached = true",
        "full_tools_when_detached = false"), encoding="utf-8")
    assert _preflight(paths) is not None


# ── ② 接线：⛔ 判据落在 AST 上，注释里提一句不算 ───────────────────

def test_autopilot的preflight里真的调了硬拒() -> None:
    """⛔ 这个项目栽过多次「实现了但生产路径没调」。"""
    fn = next((n for n in ast.walk(ast.parse(inspect.getsource(autopilot)))
               if isinstance(n, ast.FunctionDef) and n.name == "preflight"), None)
    assert fn is not None
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "refuse_preflight"]
    assert calls, "⛔ autopilot.preflight 里没有 refuse_preflight——那行配置还是装饰"


def test_自动驾驶恒传无人值守为真() -> None:
    """⭐ 传 `unattended=True` 是这条修复的**全部内容**——
    ⚠️ 传 `False` 或忘了传，硬拒就等于没接。"""
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(autopilot)))
              if isinstance(n, ast.FunctionDef) and n.name == "preflight")
    call = next(n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "refuse_preflight")
    kw = {k.arg: k.value for k in call.keywords}
    assert "unattended" in kw, f"⛔ 没传 unattended：{list(kw)}"
    assert isinstance(kw["unattended"], ast.Constant) and kw["unattended"].value is True, \
        "⛔ 自动驾驶必须恒传 unattended=True——它按定义就是没人在看"


def test_判据不许再挂在detach这个开关名上() -> None:
    """⚠️ 参数原来叫 `detach`——一个**只存在于 dispatch 那条路**的开关。
    ⭐ 概念错了，接线就一定漏。判据钉在签名上。"""
    from devloop import constitution as C

    sig = inspect.signature(C.refuse_preflight)
    assert "unattended" in sig.parameters, sig
    assert "detach" not in sig.parameters, \
        "⛔ `detach` 回来了——判据又挂回开关上了"


def test_开跑前就拒不许跑到一半才炸(tmp_path) -> None:
    """⭐ 逐单查且在**开跑之前**查：一单被拒 → 整个计划不开跑。
    ⚠️ 跑到第 7 单才炸的话，前 6 单的钱已经花了。"""
    paths = _proj(tmp_path, "readonly")
    f = paths.project / ".devloop" / "plans" / "夜跑.toml"
    f.write_text(f.read_text(encoding="utf-8")
                 + "\n[[task]]\nid = 't2'\ntools = 'full'\nretries = 1\n"
                 + "  [task.accept]\n" + _accept("full") + "\n",
                 encoding="utf-8")
    (paths.project / ".devloop" / "tasks" / "t2.md").write_text(
        _TASK, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        _preflight(paths)
    assert "t2" in str(e.value), str(e.value)
