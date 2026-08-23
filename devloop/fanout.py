"""多派单的判据。⛔ 「派几单」不该是拍脑袋填的一个数。

## 一、Anthropic 对 subagent 的规定（抄自 Claude Code 二进制里的工具描述）

> **When not to use**：If the target is already known, use the direct tool
> —— Read for a known path, Grep for a specific symbol or string.
> **Reserve this tool for open-ended questions that span the codebase.**

> When you launch multiple agents for **independent work**, send them in a
> single message with multiple tool uses so they run concurrently.

内置 `/simplify` 命令的做法是个具体样板：**同一份 diff 发给三个 agent，
三个不同的镜头**（复用 / 质量 / 效率），并发跑。

蒸馏出三条：

| 派 | 不派 |
|---|---|
| 子任务**互相独立** | 有顺序依赖（后一步要前一步的产出） |
| **开放式、跨代码库**，要的是覆盖面 | **目标已知**——直接读/搜就行 |
| 需要**独立视角**（判断类任务，多视角能抓住单视角抓不到的） | 只是「这样代码更整洁」 |

## 二、⛔ 但 DevLoop 的成本结构和 subagent **完全不同**

|  | Claude Code 的 subagent | DevLoop 的一单 |
|---|---|---|
| 启动 | ~0 | 建 250 MB worktree + 拷 107 MB 缓存 ≈ 45 s |
| 验收 | 无 | **闸 ≈ 900 s（占整单 87%）** |
| 产出 | 文本，即用即弃 | **一个分支，按宪法要人工逐个批准** |
| 失败代价 | 重跑 | 分支垃圾 + worktree 垃圾 + 人工清理 |

⭐ **而只读单根本不走这条路**：`_run_unit` 里 `wt = ... if writes else None`，
不建 worktree、**不跑闸**。eco-ob 台账 5 单只读全部 `gate_ok=None`，
耗时 **7–30 秒**；而写单是 **1043 秒**。⛔ **差 35 倍。**

## 三、于是判据分两支

### 只读（审计）→ **默认就该并行**

几乎免费，而且它天然满足 Anthropic 的三条：独立、跨代码库、要覆盖面。
⛔ 要求它声明改动范围是纯添堵——它改不了任何东西。

⚠️ 上限取 `DEFAULT_READONLY`：再多是拿额度换边际递减的覆盖面，
而**额度是共享的**，烧光了写任务也跑不成。

### 写（修改）→ **默认串行，要并行得给出理由**

并行的**收益**是摊薄那 900 秒的闸墙钟。
并行的**代价**有两条，都落在人身上：

1. ⛔ N 个分支要按宪法 C-2/C-4 **逐个批准**——并行只是把瓶颈从机器移到人
2. ⛔ 改动范围重叠 = 合并冲突，而冲突要人来解，**省下的墙钟连本带利还回去**

所以要并行，任务书必须声明 `# 改动范围`，且彼此不重叠。

⚠️ **串行时不查这个**：两单改同一个文件是合法的——后一单基于前一单的产出，
那正是 `chain` 的用法。判据只在并行时成立。
"""

from __future__ import annotations

import fnmatch

from .models import TaskSpec

#  ⚠️ 4 不是随便取的：`/simplify` 用 3 个镜头，本项目实测的审计
#     用 4–5 个角度（过去/现在/将来/文档矛盾/物种蓝图）覆盖度就够了，
#     再多是拿额度换边际递减的覆盖面。⛔ 而额度是和写任务共享的。
DEFAULT_READONLY = 4
DEFAULT_WRITE = 1


def default_parallel(tools: str) -> int:
    return DEFAULT_READONLY if tools == "readonly" else DEFAULT_WRITE


def _overlap(a: str, b: str) -> bool:
    """两个 glob 会不会撞上同一个文件。

    ⚠️ 启发式，方向**偏严**：宁可多报一次冲突让人去确认，
    也不要放两单去改同一个文件——⛔ 后者的代价是人工解冲突。
    """
    return (a == b or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)
            or a.startswith(b.rstrip("*/")) or b.startswith(a.rstrip("*/")))


def check(specs: list[TaskSpec], *, tools: str, parallel: int) -> list[str]:
    """能不能按这个并发跑。返回**阻断理由**，空表 = 可以。

    ⛔ 每条理由都要说清「为什么」和「怎么办」——
    一条只说「不行」的规则会被人绕过去或者关掉。
    """
    if parallel <= 1 or len(specs) <= 1:
        return []
    if tools == "readonly":
        return []          # ⭐ 只读并行几乎免费，见模块 docstring

    bad: list[str] = []
    no_scope = [s.name for s in specs if not s.scope]
    if no_scope:
        bad.append(
            f"⛔ 写任务要并行，每份任务书都得声明 `# 改动范围`，这几份没有："
            f"{'、'.join(no_scope)}。\n"
            f"   为什么：并行的两单若改到同一个文件就会合并冲突，"
            f"而冲突要人来解——**省下的墙钟连本带利还回去**。\n"
            f"   怎么办：在任务书里加一段（整行标题）\n"
            f"       # 改动范围\n"
            f"       - game/src/sim/sim_world.gd\n"
            f"   ⚠️ 或者就用 `--parallel 1` 串行跑——写任务的默认本来就是串行。")

    scoped = [s for s in specs if s.scope]
    for i, a in enumerate(scoped):
        for b in scoped[i + 1:]:
            hit = [(x, y) for x in a.scope for y in b.scope if _overlap(x, y)]
            if hit:
                bad.append(
                    f"⛔ `{a.name}` 与 `{b.name}` 的改动范围重叠："
                    f"{'、'.join(sorted({x for x, _ in hit}))}。\n"
                    f"   并行改同一处 = 合并冲突。⚠️ 要么拆开范围，"
                    f"要么串行（`--parallel 1`），要么用 `chain` 让后一单接着前一单跑。")
    return bad


def advise(specs: list[TaskSpec], *, tools: str, parallel: int) -> str:
    """放行时的一句提醒。⛔ 并行不是白拿的，代价要说出来。"""
    if parallel <= 1 or len(specs) <= 1:
        return ""
    if tools == "readonly":
        return (f"⭐ {len(specs)} 单只读并发 {parallel}——不建 worktree、不跑闸，"
                f"实测单单 7–30 秒。")
    return (f"⚠️ {len(specs)} 单写操作并发 {parallel}：省的是闸的墙钟"
            f"（单单约 900 秒，占 87%），⛔ **但会产生 {len(specs)} 个分支，"
            f"按宪法要你逐个批准**——瓶颈从机器移到了你身上。")


def explain() -> str:
    """把判据打印给人看。⭐ 用户问的正是「条件是什么」。"""
    return f"""\
# 什么时候该多派单

## 通用三条（Anthropic 对 subagent 的规定，DevLoop 沿用）

| 派 | 不派 |
|---|---|
| 子任务**互相独立** | 有顺序依赖 —— 用 `chain`，不是 `--parallel` |
| **开放式、跨代码库**，要的是覆盖面 | **目标已知** —— 直接读/搜就行，别派 |
| 需要**独立视角**（判断类任务） | 只是「这样更整洁」 —— 不是理由 |

## ⛔ DevLoop 独有的一条：只读和写，成本差 35 倍

|  | 只读（审计） | 写（修改） |
|---|---|---|
| worktree | **不建** | 250 MB |
| 闸 | **不跑** | ≈ 900 秒（占整单 87%） |
| 实测单单耗时 | **7–30 秒** | **1043 秒** |
| 默认并发 | **{DEFAULT_READONLY}** | **{DEFAULT_WRITE}（串行）** |

⭐ 只读并行几乎免费，而且天然满足上面三条 —— **默认就该并行**。

⛔ 写并行的代价落在**人**身上：N 个分支要按宪法逐个批准，
范围重叠还要人解冲突。所以要并行必须在任务书里声明 `# 改动范围`，且彼此不重叠。

⚠️ **串行时不查范围** —— 两单改同一个文件是合法的（后一单基于前一单的产出），
那正是 `chain` 的用法。判据只在并行时成立。
"""
