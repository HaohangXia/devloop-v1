"""不可恢复记录的指纹与归档（T5 宪法的第四条判据）。

## ⛔ 守的是什么

`.devloop/` 被 `.gitignore` **整体忽略**（eco-ob 是 `.gitignore:35:.devloop/`），
实测 `git ls-files .devloop` → **0**。里面装的是台账 / 回执 / 任务书
——**DevLoop 跑过什么的全部证据**，删了不可再生。

⚠️ 而自动驾驶的三条失控防线（已花多少 / 派了几次 / 有没有算不出成本的）
**全都只从台账读**。台账没了，三个数都是 0，三条防线一起失效，
屏幕上还印着「花了 $0.0000 · 派了 0 次」——与 G-26 是同一个形状。

## ⛔ 它不止一份（第一版设计栽在这里）

同一个 `.git` 底下**每个工作副本各有一份**：

| | git 里有几个文件 | 实际 |
|---|---|---|
| `eco-ob/.devloop/` | 0 | 26 个文件 / 138 K ← **三条防线读的是这一份** |
| `eco-ob-p4/.devloop/` | 0 | 390 K（冻结评测基线的记录） |

⚠️ 第一版方案只盯 p4——盯的是两个一模一样的目标里**暴露较少**的那个。
两者被同一条 `.gitignore` 规则忽略，损失同样不可逆。

## ⭐ 判据是「只追加」，不是「哈希没变」

**这是本模块最要紧的一条设计。**

派单这个动作**自己就会**往 `.devloop/telemetry.jsonl` 追加一行、
往 `.devloop/reports/` 写一份新回执。判据若写成「哈希变了就命中」，
它**每一单都命中**——而一个天天喊狼来了的守卫会被人关掉，
⛔ **那比没有守卫更坏**。

所以判据分四种：

| 情况 | 判定 | 为什么 |
|---|---|---|
| 文件消失 | ⛔ 命中 | 记录只会增长，不会自己没 |
| 变短 | ⛔ 命中 | 截断——比删除更阴，文件还在，粗看没事 |
| 长度没变、内容变了 | ⛔ 命中 | 就地改写 |
| 变长、但**前 N 字节的指纹对不上** | ⛔ 命中 | 抹掉历史再补几行。⚠️ 只判「变短」的实现会放过它 |
| 变长、前 N 字节一致 | ✅ 放行 | 正常追加 |
| 新增文件 | ✅ 放行 | 新回执本来就该出现 |

## ⛔ 射程（诚实标注）

- 只在**派单前后各看一眼**。工人干活那一整段里发生又被复原的事，看不见。
- 判不了「这个改动是不是合理的」——只判「历史有没有被动过」。
- ⚠️ 不覆盖 `.devloop-worktrees/` 下的一次性隔离副本：它们天然会来会走，
  纳进来就是稳定误报源，而稳定误报源最终会让整道守卫被关掉。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#  ⚠️ 本工具自己建的一次性隔离副本放在这个目录下，⛔ 不纳入射程（见模块 docstring）。
EPHEMERAL_ROOT = ".devloop-worktrees"

#  ⛔ **运行产物不算记录。** 2026-08-01 崩溃恢复真跑抓到的误报：
#     第 3 单被这道守卫判失败，理由是
#         `devloop/autopilot/resume-drill.json：前缀被改写（抹掉历史再补）`
#     而那是 autopilot **自己的运行记录**，它每一轮都要重写（`run.save()`）。
#
#  ⚠️ 「只追加」这个判据对**账本**成立（telemetry / findings / reports / tasks），
#     对**可变状态文件**不成立。⛔ 不排除的话，每次自动驾驶跑到第 3 单
#     都会被自己的守卫判失败——而误报会让人把守卫整个关掉，那比没有守卫更坏。
#
#  ⭐ 判据是现成的、不是我现编的：本仓库 `.gitignore` 早就把这三个目录
#     定性为「工具自动在项目里建的——**运行产物，不是源码**」。
#  ⭐ `dossier` 2026-08-03 加入：复核卷宗每单一份，与 autopilot 的运行记录同类。
#     ⛔ 不进这里的话记录守卫会把它当成「记录被抹」（G-71 同款），
#     从第 2 单起全阶段连坐。
#  ⚠️ 2026-08-16 订正：原话是「**每次重跑重写**」——那半句现在不成立了。
#     文件名带上了 `unit_id`（G-135），重跑会新增一份而不是覆盖上一份。
#     ⭐ 但**剔除的理由不变**：卷宗仍是「工具自动在项目里建的运行产物」，
#     每单都会多出文件 ⇒ 不剔除照样会被判成「记录被动过」。
#  ⛔ 别据此把 `dossier` 从这里拿掉 —— 拿掉会让守卫从第 2 单起天天误报，
#     而误报会让人把守卫整个关掉，那比没有守卫更坏（见本段开头）。
RUNTIME_DIRS = ("jobs", "handoff", "autopilot", "dossier")

#  记录目录名。⚠️ 与 `ProjectPaths` 里的约定一致，改一处要改两处——
#  ⛔ 但这里不 import ProjectPaths：本模块要能对**兄弟副本**取指纹，
#     而兄弟副本不是一个 ProjectPaths（它没有 config、可能没有 gates）。
RECORD_DIR = ".devloop"

_CHUNK = 1 << 20


@dataclass(frozen=True)
class Mark:
    """一个文件在 T0 时刻的样子。"""
    size: int
    sha: str            # 全文 sha256


def _sha_prefix(p: Path, nbytes: int | None = None) -> tuple[int, str]:
    """返回 (读到的字节数, 这些字节的 sha256)。`nbytes=None` 表示读到底。"""
    h = hashlib.sha256()
    read = 0
    with p.open("rb") as f:
        while nbytes is None or read < nbytes:
            want = _CHUNK if nbytes is None else min(_CHUNK, nbytes - read)
            b = f.read(want)
            if not b:
                break
            h.update(b)
            read += len(b)
    return read, h.hexdigest()


def worktrees(project: Path) -> list[Path]:
    """同一个 `.git` 底下的所有工作副本，⛔ 剔除一次性隔离副本。

    ⚠️ 用 `--porcelain` 而不是人读格式：人读格式的对齐空格会随路径长度变，
    而路径里本来就可能有空格。
    """
    r = subprocess.run(["git", "worktree", "list", "--porcelain"],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return [project]
    out = []
    for ln in r.stdout.splitlines():
        if not ln.startswith("worktree "):
            continue
        p = Path(ln[len("worktree "):].strip())
        if EPHEMERAL_ROOT in p.parts:
            continue
        out.append(p)
    return out or [project]


def _record_files(root: Path) -> list[Path]:
    d = root / RECORD_DIR
    if not d.is_dir():
        return []
    #  ⛔ 剔除运行产物（见 RUNTIME_DIRS 处的说明）。
    #  ⚠️ 判在**相对 .devloop 的第一段**上，不用子串匹配——
    #     后者会误伤名字里恰好含 "jobs" 的回执文件。
    out = []
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(d).parts
        if rel and rel[0] in RUNTIME_DIRS:
            continue
        out.append(f)
    return out


def fingerprint(project: Path) -> dict[str, Mark]:
    """给所有工作副本的记录目录取指纹。

    键格式：`<副本目录名>/<相对 .devloop 的路径>`，⛔ 不用绝对路径——
    绝对路径会让指纹随机器/盘符变化，没法比对也没法写进档案。
    """
    fp: dict[str, Mark] = {}
    for wt in worktrees(project):
        for f in _record_files(wt):
            try:
                n, sha = _sha_prefix(f)
            except OSError:
                continue          # ⚠️ 读不到的当没有：宁可漏报，不许因为一个
                                  #    锁住的文件把整批派单打死
            fp[f"{wt.name}/{f.relative_to(wt / RECORD_DIR).as_posix()}"] = Mark(n, sha)
    return fp


def verify(project: Path, before: dict[str, Mark]) -> list[str]:
    """拿 T0 的指纹对现在的实际内容。返回命中说明（空 = 干净）。

    ⛔ 判据是**只追加**，不是「哈希没变」——理由见模块 docstring。
    """
    hits: list[str] = []
    roots = {wt.name: wt for wt in worktrees(project)}
    for key, mark in sorted(before.items()):
        wt_name, _, rel = key.partition("/")
        root = roots.get(wt_name)
        if root is None:
            hits.append(f"{key}：所在的工作副本整个消失了")
            continue
        f = root / RECORD_DIR / rel
        if not f.is_file():
            hits.append(f"{key}：消失了")
            continue
        try:
            now_size = f.stat().st_size
        except OSError as exc:
            hits.append(f"{key}：读不到（{type(exc).__name__}）")
            continue
        if now_size < mark.size:
            hits.append(f"{key}：变短了（{mark.size} → {now_size} 字节，被截断）")
            continue
        n, sha = _sha_prefix(f, mark.size)
        if n != mark.size or sha != mark.sha:
            #  ⚠️ 同长度内容变 = 就地改写；变长但前缀对不上 = 抹掉历史再补
            how = "改写" if now_size == mark.size else "前缀被改写（抹掉历史再补）"
            hits.append(f"{key}：{how}")
    return hits


def archive(project: Path, dest: Path) -> int:
    """把所有工作副本的记录目录拷进 `dest`，返回拷了几个文件。

    ⛔ **全量重来，不是增量。** 只补不删的归档会让已被删掉的文件永远留在
    保险柜里显示为「还在」——那时保险柜自己变成一份**说谎的现状快照**，
    而人会照着它以为东西还在。
    """
    n = 0
    for wt in worktrees(project):
        files = _record_files(wt)
        if not files:
            continue
        sub = dest / wt.name
        shutil.rmtree(sub, ignore_errors=True)
        for f in files:
            out = sub / f.relative_to(wt / RECORD_DIR)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            n += 1
    return n
