"""自动驾驶的并行派单（G-66）。

## ⛔ 这条为什么单独立项

PLAN 长期把「并行」记在自动驾驶的「**未验**的四条」里，
而实测 `cmd_autopilot` 的派单循环是 `t = ready[0]`——**每轮只取一单，严格串行**，
`autopilot --help` 里压根没有 `--parallel`。

⚠️ 「未验」和「未实现」的处置完全不同：前者跑一次就行，后者要写代码。
⛔ 按「未验」排期会直接排错。

## ⚠️ 并行的代价全落在人身上

并行省的是闸的墙钟（eco-ob 实测占整单 **87%**）。代价两条：

1. ⛔ N 个分支要按宪法 C-2/C-4 **逐个批准**——瓶颈从机器移到人
2. ⛔ 改动范围重叠 = 合并冲突，**冲突要人来解，省下的墙钟连本带利还回去**

所以判据（`devloop/fanout.py`）要在**花第一分钱之前**执行，
而且是在 `plan.load()` 里——⭐ 那是唯一一个「还没花钱」的时刻。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devloop import plan as plan_mod
from devloop.config import ConfigError

HEAD = """\
[plan]
version = 1
[stage]
id = 's'
goal = 'g'
task_dir = '.devloop/tasks'
base = 'HEAD'
"""
BUDGET = """
[stage.budget]
total_usd = 1.0
reserve_usd = 0.1
max_dispatches = 9
max_wall_min = 30
watchdog_k = 2
default_max_turns = 60
[stage.accept]
require_pass = ['g1']
"""


def _mk(tmp: Path, stage_extra: str = "", tasks: str = "", scopes=None) -> Path:
    d = tmp / ".devloop"
    (d / "tasks").mkdir(parents=True, exist_ok=True)
    (d / "plans").mkdir(parents=True, exist_ok=True)
    if not tasks:
        #  ⚠️ 每个任务必须有 accept.require_pass 或 accept.why——加载器强制的，
        #     理由是「忘了写」和「确实没法机检」在文件里长得一模一样。
        tasks = ("\n[[task]]\nid = 'a'\ntools = 'implement'\n"
                 "  [task.accept]\n  require_pass = ['g1']\n"
                 "\n[[task]]\nid = 'b'\ntools = 'implement'\n"
                 "  [task.accept]\n  require_pass = ['g1']\n")
    for tid in ("a", "b", "c"):
        body = "# 角色\n\nx\n\n# 任务\n\ny\n\n"
        if scopes and tid in scopes:
            body += "# 改动范围\n\n" + "\n".join(f"- {s}" for s in scopes[tid]) + "\n\n"
        body += "# 禁令\n\n- z\n"
        (d / "tasks" / f"{tid}.md").write_text(body, encoding="utf-8")
    f = d / "plans" / "s.toml"
    f.write_text(HEAD + stage_extra + BUDGET + tasks, encoding="utf-8")
    return f


# ── 字段本身 ──────────────────────────────────────────────────────

def test_默认串行(tmp_path: Path) -> None:
    """⛔ 写任务默认串行——并行的代价（N 个分支要人逐个批）不该由默认值替人承担。"""
    assert plan_mod.load(_mk(tmp_path)).parallel == 1


def test_能声明并发(tmp_path: Path) -> None:
    sc = {"a": ["src/a.py"], "b": ["src/b.py"]}
    assert plan_mod.load(_mk(tmp_path, "parallel = 2\n", scopes=sc)).parallel == 2


# ── ⛔ 与接力互斥 ─────────────────────────────────────────────────

def test_并行与接力互斥(tmp_path: Path) -> None:
    """⛔ chain 的语义是「后一单从前一单已过闸的产出起」——**天然要求串行**。
    两个一起开，后一单会从一个还没跑完的起点复制。⚠️ 这不是性能问题，是正确性问题。"""
    f = _mk(tmp_path, "chain = true\nparallel = 2\n", scopes={"a": ["x"], "b": ["y"]})
    with pytest.raises(ConfigError) as e:
        plan_mod.load(f)
    assert "chain" in str(e.value) and "parallel" in str(e.value)


# ── ⭐ 判据在 load 时执行：那是唯一还没花钱的时刻 ────────────────

def test_并行但任务书没声明改动范围_直接拒绝加载(tmp_path: Path) -> None:
    """⛔ 拒绝要发生在 `plan.load()`——**花第一分钱之前**。
    留到派单时才拒，前面的单已经跑掉了。"""
    with pytest.raises(ConfigError) as e:
        plan_mod.load(_mk(tmp_path, "parallel = 2\n"))
    assert "改动范围" in str(e.value)


def test_并行但范围重叠_直接拒绝加载(tmp_path: Path) -> None:
    sc = {"a": ["src/x.py"], "b": ["src/x.py", "src/y.py"]}
    with pytest.raises(ConfigError) as e:
        plan_mod.load(_mk(tmp_path, "parallel = 2\n", scopes=sc))
    assert "src/x.py" in str(e.value)


def test_范围不重叠就放行(tmp_path: Path) -> None:
    sc = {"a": ["src/x.py"], "b": ["src/y.py"]}
    assert plan_mod.load(_mk(tmp_path, "parallel = 2\n", scopes=sc)).parallel == 2


def test_只读任务并行不要求声明范围(tmp_path: Path) -> None:
    """⭐ 只读改不了任何东西——要求它声明改动范围是纯添堵。
    而且只读单不建 worktree、不跑闸，实测 7–30 秒，并行几乎免费。"""
    tasks = ("\n[[task]]\nid = 'a'\ntools = 'readonly'\n"
             "  [task.accept]\n  why = '只读盘点，没有机器判据'\n"
             "\n[[task]]\nid = 'b'\ntools = 'readonly'\n"
             "  [task.accept]\n  why = '只读盘点，没有机器判据'\n")
    assert plan_mod.load(_mk(tmp_path, "parallel = 4\n", tasks=tasks)).parallel == 4


def test_串行时不查范围(tmp_path: Path) -> None:
    """⚠️ 判据只在并行时成立。串行下两单改同一处是合法的——那正是 chain 的用法。"""
    assert plan_mod.load(_mk(tmp_path, "parallel = 1\n")).parallel == 1


# ── 接线：⛔ 生产路径上真的按波派 ──────────────────────────────────

def test_派单循环真的取一批而不是一个() -> None:
    """⛔ 判据落在源码上：要真触发并行得跑真单，那不能拿来做测试。

    ⚠️ 这个项目栽过三次「实现了但生产路径没调」。
    """
    import ast
    import inspect

    from devloop import cli

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_autopilot")

    #  ⛔ 判据要匹配**那个反模式本身**，不是子串。
    #  ⚠️ 第一版写的是「源码里不许出现 `ready[0]`」——而它合法地出现在
    #     `head_tools = ready[0].tools`（取首个任务的权限）和一句注释里。
    #     子串判据会把正确的实现判红，那是**判据维度错**（第 3 种假绿的镜像）。
    #     现在判的是 AST 上的「把 ready[0] 整个绑给一个名字」这个动作。
    bad = [n for n in ast.walk(fn)
           if isinstance(n, ast.Assign)
           and isinstance(n.value, ast.Subscript)
           and isinstance(n.value.value, ast.Name) and n.value.value.id == "ready"
           and isinstance(n.value.slice, ast.Constant) and n.value.slice.value == 0]
    assert not bad, "⛔ 还在 `t = ready[0]`——每轮只取一单，并行没接上"

    src = ast.dump(fn)
    assert "ThreadPoolExecutor" in src, "⛔ 派单循环里没有并发池"
    assert "wave" in src, "⛔ 看不到按波提交的痕迹"


def test_并行时不许推进接力点() -> None:
    """⛔ 一波里几单同时跑完，谁的产出该当接力点？没有答案。
    所以 chain 与 parallel 互斥（上面已拒），⚠️ 但代码里也要有第二道。"""
    import inspect

    from devloop import cli

    src = inspect.getsource(cli).split("def cmd_autopilot")[1][:8000]
    assert "sp.chain" in src, "找不到 chain 判断"
