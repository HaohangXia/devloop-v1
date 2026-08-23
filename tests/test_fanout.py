"""多派单的判据。⛔ 「派几单」不该是拍脑袋填的一个数。

判据的两个来源，见 `devloop/fanout.py` 模块 docstring：
Anthropic 自己对 subagent 的规定，加上 DevLoop **独有的成本结构**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devloop import fanout
from devloop.models import TaskSpec


def _spec(tmp: Path, name: str, scope: list[str] | None = None) -> TaskSpec:
    body = "# 角色\n\nx\n\n# 任务\n\ny\n\n"
    if scope is not None:
        body += "# 改动范围\n\n" + "\n".join(f"- {s}" for s in scope) + "\n\n"
    body += "# 禁令\n\n- z\n"
    f = tmp / f"{name}.md"
    f.write_text(body, encoding="utf-8")
    return TaskSpec.load(f)


# ── 改动范围的解析 ────────────────────────────────────────────────

def test_解析出改动范围(tmp_path: Path) -> None:
    assert _spec(tmp_path, "a", ["src/a.py", "src/b/**"]).scope == ["src/a.py", "src/b/**"]


def test_没写改动范围返回空表而不是崩(tmp_path: Path) -> None:
    """⚠️ 28 份既有任务书都没有这一段——加了这个字段不许把它们判红。"""
    assert _spec(tmp_path, "a").scope == []


# ── 只读：几乎免费，默认就该并行 ──────────────────────────────────

def test_只读的默认并发大于一() -> None:
    """⭐ 实测（eco-ob 台账 5 单）：只读单 **不建 worktree、不跑闸**，
    7–30 秒就完；而写单是 1043 秒（闸占 87%）。⛔ 差 35 倍。"""
    assert fanout.default_parallel("readonly") > 1


def test_写的默认并发是一() -> None:
    """⛔ 写单并行的代价是**人**：N 个分支要按宪法逐个批准。默认不替他做主。"""
    assert fanout.default_parallel("implement") == 1


def test_只读并行不需要声明改动范围(tmp_path: Path) -> None:
    """只读改不了任何东西，⛔ 要求它声明改动范围是纯添堵。"""
    specs = [_spec(tmp_path, "a"), _spec(tmp_path, "b")]
    assert fanout.check(specs, tools="readonly", parallel=4) == []


# ── 写：并行要有理由 ──────────────────────────────────────────────

def test_写并行必须声明改动范围(tmp_path: Path) -> None:
    specs = [_spec(tmp_path, "a"), _spec(tmp_path, "b")]
    bad = fanout.check(specs, tools="implement", parallel=2)
    assert bad and any("改动范围" in b for b in bad), bad


def test_写并行的范围重叠要拦下来(tmp_path: Path) -> None:
    """⛔ 两单同时改一个文件 = 合并冲突，而冲突要人来解——
    并行省下的墙钟会连本带利还回去。"""
    specs = [_spec(tmp_path, "a", ["src/x.py"]),
             _spec(tmp_path, "b", ["src/x.py", "src/y.py"])]
    bad = fanout.check(specs, tools="implement", parallel=2)
    assert bad and any("src/x.py" in b for b in bad), bad


def test_范围不重叠就放行(tmp_path: Path) -> None:
    specs = [_spec(tmp_path, "a", ["src/x.py"]), _spec(tmp_path, "b", ["src/y.py"])]
    assert fanout.check(specs, tools="implement", parallel=2) == []


def test_通配符重叠也要抓到(tmp_path: Path) -> None:
    """⚠️ `src/**` 和 `src/x.py` 字面不同，但它们会撞车。"""
    specs = [_spec(tmp_path, "a", ["src/**"]), _spec(tmp_path, "b", ["src/x.py"])]
    bad = fanout.check(specs, tools="implement", parallel=2)
    assert bad, "通配符重叠没被抓到"


def test_串行时不查范围(tmp_path: Path) -> None:
    """⚠️ 判据只在**并行**时才成立。串行跑，两单改同一个文件是合法的
    （后一单基于前一单的结果——那正是 chain 的用法）。"""
    specs = [_spec(tmp_path, "a", ["src/x.py"]), _spec(tmp_path, "b", ["src/x.py"])]
    assert fanout.check(specs, tools="implement", parallel=1) == []


# ── 说明必须能自我解释 ────────────────────────────────────────────

def test_拦下来时要说清为什么和怎么办(tmp_path: Path) -> None:
    """⛔ 一条只说「不行」的规则会被人绕过去或关掉。"""
    specs = [_spec(tmp_path, "a"), _spec(tmp_path, "b")]
    txt = " ".join(fanout.check(specs, tools="implement", parallel=2))
    assert "改动范围" in txt and ("--parallel 1" in txt or "串行" in txt), txt


def test_判据本身能被打印出来给人看() -> None:
    """⭐ 用户问的正是「条件是什么」——那就得答得出来。"""
    txt = fanout.explain()
    for k in ("独立", "只读", "闸"):
        assert k in txt, f"判据说明里没提「{k}」：{txt[:200]}"
