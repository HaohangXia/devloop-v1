"""`nightly.report()` 给第二天早上一份简报。

判据（两块都要在，且「没记录」必须明说）：
- 有待处理分支时，分支块要列出来；没有时明说「无」，⛔ 不能空白。
- 台账有记录时，报出「派单 N 次，合格 K」；没有时明说「没有记录」，
  ⛔ 不许返回空串——空白会让人以为是工具坏了，而不是昨夜没跑过。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from devloop import nightly


def _repo(tmp_path: Path) -> Path:
    p = tmp_path / "r"
    p.mkdir()
    for a in (["init", "-q", "-b", "master", "."],
              ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    (p / "base.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=p, check=True,
                   capture_output=True)
    return p


def _make_work_branch(repo: Path, name: str) -> None:
    subprocess.run(["git", "checkout", "-q", "-b", name], cwd=repo, check=True,
                   capture_output=True)
    slug = name.replace("/", "_")
    (repo / f"{slug}.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", f"work({name}): 干活\n\n分支：{name}"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True,
                   capture_output=True)


def _write_ledger(repo: Path, rows: list[dict]) -> None:
    d = repo / ".devloop"
    d.mkdir(exist_ok=True)
    p = d / "telemetry.jsonl"
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_空仓库空台账_也要明说而不是返回空串(tmp_path):
    repo = _repo(tmp_path)

    out = nightly.report(repo)

    assert out.strip(), "⛔ 不许返回空串——空白会被误当成工具坏了"
    assert "无" in out or "没有" in out, f"该明说没有内容：{out!r}"
    # 两块都要出现（分支块 + 台账块）
    assert "分支" in out and ("24" in out or "台账" in out), \
        f"两块信息都要在：{out!r}"


def test_有分支有台账_都要出现在报告里(tmp_path):
    repo = _repo(tmp_path)
    _make_work_branch(repo, "devloop/pending")

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_ledger(repo, [
        {"ts": now, "task": "t1", "ok": True},
        {"ts": now, "task": "t2", "ok": False},
        {"ts": now, "task": "t3", "ok": True},
    ])

    out = nightly.report(repo)

    assert "devloop/pending" in out, f"分支该被列出来：{out!r}"
    assert "3" in out, f"总单数 3 该出现：{out!r}"
    assert "2" in out, f"合格数 2 该出现：{out!r}"


def test_老记录被24小时窗口排掉(tmp_path):
    """⚠️ 台账里若只有过老记录，报告要说「没有记录」，而不是把老数据算进来。"""
    repo = _repo(tmp_path)
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    _write_ledger(repo, [{"ts": old, "task": "old", "ok": True}])

    out = nightly.report(repo)

    assert "没有记录" in out, f"3 天前的记录不该被算进 24h 窗口：{out!r}"
