"""⛔ 卷宗必须是**这一单自己的**，⛔ 且不许被另一份悄悄盖掉。

## 这条是怎么来的（2026-08-16）

对抗式验收里，一个代理真跑了 **20 遍并行两单**：

| 现象 | 次数 |
|---|---|
| ⛔ 目录里**只剩一份**卷宗，另一份被静默覆盖、屏幕零提示 | **12 / 20** |
| ⛔ 「`gate_code=0` · 1 过/0 未过」盖在一份**闸判红**的产出上（正文写的是另一单的改动） | 1 / 20 |

## ⛔ 根因是**两件事**，不是「一件事的两处」

| | 场景 | 根因 |
|---|---|---|
| ① | **并行**串卷宗 | `cli.py` 取 `telemetry.load(...)[-1]` —— **账本最后一行** |
| ② | **重试**时第一遍被盖掉（顺序跑，与并行无关，**天天走**） | `dossier.py` 用**任务名**当文件名 |

⭐ **顺序不能反**：文件名是从取到的那一行上抄下来的。
先改文件名 = 把错抄的名字换个写法 —— 实测只改文件名，3 次里仍有串。

⭐⭐ 而 `telemetry.py` 自己的 docstring 白纸黑字写着：
> **配对靠 `unit_id`，⛔ 不许靠任务名**

**规矩早写下来了，代码在破坏它。**

## ⭐ 判据为什么不靠「跑并行看结果」

⛔ 跑并行是**碰运气**：20 次里 8 次是好的，一条 flaky 测试比没有更坏。
⭐ 下面两条都是**单跑一单、秒级、确定性**的：
- 场景①：跑一单，**在它记完账之后**必然多塞一条「隔壁那一单」的记录
- 场景②：同一份任务书**连跑两次**（= 重试那条路）

## 修法（⛔ 为什么不是「按 unit_id 去账本里挑自己那行」）

⭐ 记账那一刻手里就攥着刚写下去的那一行，让 `telemetry.record` **原样交回来**即可。
⛔ 「回头去挑」多一个失败模式：**挑不到自己那行怎么办？**
而最省事的定义（「那就还用最后一行吧」）**恰好就是今天这个毛病本身** —— 写下去以后谁也看不出来。
⚠️ 顺带少读一次整本账，而账本只增不减。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devloop import cli as C, dispatch as D, telemetry
from devloop.config import ProjectPaths
from devloop.models import Receipt, TaskSpec, WorkerConfig


def _git(cwd: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, f"git {' '.join(a)} 失败：{p.stderr}"
    return p.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".devloop" / "tasks").mkdir(parents=True)
    (r / ".devloop" / "gates.sh").write_text(
        "printf 'PASS\\t语法\\t好\\n'\nexit 0\n", encoding="utf-8", newline="\n")
    (r / ".devloop" / "tasks" / "u1.md").write_text(
        "# 角色\n\nx\n\n# 任务\n\ny\n\n# 改动范围\n\n- `f.txt`（改点什么）\n\n# 禁令\n\n- z\n",
        encoding="utf-8")
    (r / "f.txt").write_text("1\n", encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def _fake(text: str):
    """假工人：在工位里真改一个文件。⛔ 自己不提交——提交由编排方做。"""
    def _f(spec_, paths_, cfg_, **kw):
        (Path(kw["cwd"]) / "f.txt").write_text(text, encoding="utf-8")
        return D.DispatchResult(
            spec_.name,
            Receipt(is_error=False, result="ok", num_turns=3, duration_ms=900),
            None)
    return _f


def _run(r: Path, monkeypatch, worker_text: str = "2\n"):
    paths = ProjectPaths(r)
    spec = TaskSpec.load(r / ".devloop" / "tasks" / "u1.md")
    base = _git(r, "rev-parse", "HEAD")
    monkeypatch.setattr(C, "dispatch_one", _fake(worker_text))
    return C._run_unit(
        spec, paths, WorkerConfig(model="m", base_url="", auth_token="", timeout_s=10),
        tools="implement", max_turns=5, gate_fp=None, writes=True,
        base=base, require_pass=("语法",), stage="u")


def _dossiers(r: Path) -> list[Path]:
    return sorted((r / ".devloop" / "dossier").glob("*.md"))


def test_隔壁那一单抢先记账时卷宗不许抄错人(tmp_path: Path, monkeypatch) -> None:
    """⭐ 场景①的确定性复现：⛔ 不靠跑并行碰运气。

    做法：让「隔壁那一单」的收工记录**紧跟在本单之后**落进账本 ——
    这正是并行波次里发生的事，⚠️ 只是这里是必然的，不是概率的。
    """
    r = _repo(tmp_path)
    real_append = telemetry._append
    fired = {"n": 0}

    def _append_then_neighbour(path: Path, text: str) -> None:
        real_append(path, text)
        #  ⚠️ 只在**本单的收工行**之后插一次（开跑行不算）。
        if fired["n"] == 0 and '"worker_ok"' in text:
            fired["n"] = 1
            real_append(path, json.dumps({
                "ts": "2026-08-16T23:59:59", "task": "隔壁那一单",
                "unit_id": "20260816-235959-9999999", "model": "m",
                "tools": "implement", "worker_ok": True, "gate_ok": True, "ok": True,
                "gate_code": 0, "gate_detail": "9 过 / 0 未过",
                "turns": 999, "cost_usd_synthetic": 99.99, "error": None,
            }, ensure_ascii=False) + "\n")

    monkeypatch.setattr(telemetry, "_append", _append_then_neighbour)
    _run(r, monkeypatch)

    assert fired["n"] == 1, "⛔ 隔壁那一行没插进去 —— 本次红检作废，先修测试"
    got = _dossiers(r)
    assert len(got) == 1, f"⛔ 一单跑出了 {len(got)} 份卷宗：{[p.name for p in got]}"
    name, md = got[0].name, got[0].read_text(encoding="utf-8")

    assert "隔壁那一单" not in name, (
        f"⛔ 卷宗的**文件名**抄了隔壁那一单：{name}\n"
        "   ⇒ 并行时两份卷宗会重名、互相覆盖（实测 20 次丢 12 份）。")
    assert "隔壁那一单" not in md, (
        f"⛔ 卷宗**正文**抄了隔壁那一单。\n卷宗原文：\n{md}")
    assert "999" not in md and "99.99" not in md, (
        f"⛔ 卷宗抄到了隔壁的轮数/花费 —— 人会拿别人的账给这一单放行。\n{md}")
    assert "u1" in name, f"⛔ 文件名里认不出这是哪一单：{name}"


def test_同一份任务书跑两次必须留下两份卷宗(tmp_path: Path, monkeypatch) -> None:
    """⭐ 场景②：**重试**那条路，顺序跑，与并行无关 —— ⚠️ 而重试天天走。

    ⛔ 文件名只有 `{stage}-{task}` 时，第二遍会把第一遍**悄悄盖掉**、屏幕零提示。
    """
    r = _repo(tmp_path)
    _run(r, monkeypatch, "2\n")
    first = _dossiers(r)
    assert len(first) == 1

    #  ⚠️ 第二遍要从**新的**基准起（第一遍的产出已经提交了）
    _run(r, monkeypatch, "3\n")
    got = _dossiers(r)

    assert len(got) == 2, (
        f"⛔ 跑了两遍只剩 {len(got)} 份卷宗：{[p.name for p in got]}\n"
        "   ⇒ 第一遍的复核材料被第二遍静默盖掉了。"
        "⚠️ 而重试正是**天天走**的那条路。")
    assert len({p.name for p in got}) == 2, "⛔ 两份重名"


def test_记账那一刻就把自己那行交回来(tmp_path: Path, monkeypatch) -> None:
    """⛔ 判据落在**接口**上：`record` 必须交回它刚写下去的那一行。

    ⚠️ 它返回 `None` 时，唯一还能拿到那一行的办法就是回头挑「账本最后一行」
    —— 而那正是本文件要根除的东西。
    """
    r = _repo(tmp_path)
    led = r / ".devloop" / "telemetry.jsonl"
    row = telemetry.record(
        led, D.DispatchResult("t", Receipt(is_error=False, result="ok"), None),
        model="m", tools="implement", unit_id="20260816-000000-1234567")
    assert isinstance(row, dict), "⛔ `record` 没把那一行交回来"
    assert row.get("unit_id") == "20260816-000000-1234567"
    #  ⭐ 交回来的必须**就是**落盘的那一行，⛔ 不许是另造的
    last = json.loads(led.read_text(encoding="utf-8").splitlines()[-1])
    assert last == row, "⛔ 交回来的那一行与落盘的不是同一个"


def test_卷宗文件名里必须认得出是哪一单(tmp_path: Path, monkeypatch) -> None:
    """⛔ 别只用单号：那样目录里是一排时间戳，人得一份份打开才知道哪份是哪份。

    ⚠️ 而屏幕上那句「卷宗在这里」**一个字都没存盘**（G-133 的 6b），
    跑完之后单号与任务名的对应关系就没了。
    """
    r = _repo(tmp_path)
    _run(r, monkeypatch)
    name = _dossiers(r)[0].name
    assert "u1" in name, f"⛔ 文件名里没有任务名：{name}"
    assert name.startswith("u-"), f"⛔ 文件名里没有阶段名：{name}"
