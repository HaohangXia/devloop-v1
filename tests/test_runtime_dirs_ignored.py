"""⛔ 工具往项目里写的每一样东西，都必须在忽略名单里——**这条已经踩过三次**。

## 同一个形状，三次

| 时间 | 漏的是谁 | 后果 |
|---|---|---|
| 2026-07-?? | `.devloop/handoff/` | 一次 `git add -A` 把探针留下的目录扫进提交 `f974cb2`（3 文件 124 行） |
| 2026-08-02 | `.devloop/findings.jsonl`（G-93） | `cmd_audit` 一写它活工作区就变 → 从第 2 单起每单命中宪法 A-2 → **全阶段连坐** |
| 2026-08-03 | `.devloop/dossier/` | 同上。⭐ 而 `dossier.py` 的开头注释还专门写着「我避开了 G-93」 |

## ⭐ 为什么每次都会漏

「运行产物」这件事登记在**两个地方**，而它们没有任何机械关系：

- `devloop/records.py` 的 `RUNTIME_DIRS` —— 代码用它判「这是工具自己写的」
- `.gitignore` —— git 用它判「这个不算改动」

⚠️ 加新功能的人只会想到前一个（因为写代码要用它），**后一个全靠记得**。
⛔ 三次教训说明：靠记得是不行的。

## ⛔ 后果为什么这么重

工具往项目里写一个文件 → `git status` 看得见 → 宪法的「活工作区变了」判据命中
→ 而 `ws_before` **整个阶段只采一次**（`cli.py`）→ 于是**从第二单起每单都命中**。

无人值守跑一夜，早上十条「越线，等你批准」，⭐ **一条真的都没有**。
项目自己的原话：**天天红的守卫等于没有守卫。**

## 判据

⛔ 不判「dossier 在不在里面」——那只钉住这一次。
⭐ 判**两份清单的对应关系**：`RUNTIME_DIRS` 里的每一项，`.gitignore` 里都得有。
下一个人加第五个目录时，这条会当场转红。
"""

from __future__ import annotations

import pathlib
import subprocess

from devloop import records

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_IGNORE = _ROOT / ".gitignore"


def _ignore_lines() -> list[str]:
    return [ln.strip() for ln in _IGNORE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def test_每个运行产物目录都必须在忽略名单里() -> None:
    """⭐ **本文件的核心。** 两份清单必须对得上。"""
    lines = _ignore_lines()
    missing = [d for d in records.RUNTIME_DIRS
               if f".devloop/{d}/" not in lines]
    assert not missing, (
        f"⛔ 这几个运行产物目录不在 .gitignore 里：{missing}\n"
        f"   ⚠️ 后果不是「文件被提交进去」那么轻——工具一写它，`git status`\n"
        f"      就变，宪法「活工作区变了」从**第二单起每单命中**，一夜全是误报。\n"
        f"   ⭐ 在 .gitignore 里各加一行 `.devloop/<名字>/`。")


def test_忽略名单里不许有已经不存在的产物目录() -> None:
    """⚠️ 反方向也要对：`.gitignore` 里挂着一个早就没了的目录，
    会让下一个读的人以为工具还在往那儿写。

    ⛔ 只判一个方向的话，两份清单会慢慢漂开。
    """
    known = set(records.RUNTIME_DIRS) | {"reports"}   # reports 不在 RUNTIME_DIRS，但真实存在
    stale = [ln for ln in _ignore_lines()
             if ln.startswith(".devloop/") and ln.endswith("/")
             and ln[len(".devloop/"):-1] not in known]
    assert not stale, (
        f"⛔ .gitignore 里这几行指向的目录，工具已经不往那儿写了：{stale}\n"
        f"   ⭐ 要么从 RUNTIME_DIRS 里补上，要么从 .gitignore 里删掉。")


def test_真跑一次卷宗不许弄脏工作区(tmp_path) -> None:
    """⛔ 上面两条判的是**清单**，这条判**后果**——⭐ 两个维度都要。

    ⚠️ 只判清单的话，将来有人换了写盘位置（比如写进 `.devloop/` 根下的
    一个文件而不是目录），清单对得上而工作区照样变脏。
    """
    from devloop import dossier
    from devloop.config import ProjectPaths
    from devloop.models import TaskSpec

    p = tmp_path / "proj"
    (p / ".devloop" / "tasks").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("exit 0\n", encoding="utf-8")
    (p / ".devloop" / "tasks" / "u1.md").write_text(
        "# 角色\n\nx\n\n# 任务\n\ny\n\n# 禁令\n\n- z\n", encoding="utf-8")
    #  ⭐ 把真仓的 .gitignore 抄过来——⚠️ 判的就是它够不够
    (p / ".gitignore").write_text(_IGNORE.read_text(encoding="utf-8"),
                                  encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)

    def status() -> str:
        return subprocess.run(
            ["git", "status", "--porcelain", "-uall"], cwd=p,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace").stdout

    assert not status().strip(), f"夹具前提变了，一开始就不干净：{status()}"

    paths = ProjectPaths(p)
    dossier.write(paths, "s1",
                  TaskSpec.load(p / ".devloop" / "tasks" / "u1.md"),
                  {"task": "u1", "gate_code": 0},
                  gate_names=["pytest"], required=["pytest"],
                  changed=[], base="a", sha="b")

    dirty = status().strip()
    assert not dirty, (
        f"⛔ 写了一份卷宗，工作区就脏了：\n{dirty}\n"
        f"   ⚠️ 宪法的「活工作区变了」会从**第二单起每单命中**——"
        f"一夜十单，后九单全是误报。")
