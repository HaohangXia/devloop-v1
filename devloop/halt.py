"""急停：停掉跑飞的后台作业。

**为什么现在就要**：Phase 6 之前，一批活跑在前台，Ctrl-C 就停了。有了 `--detach`
之后，作业脱离父进程活着——**没有急停，你就没有任何办法叫停它**。
SPEC 把 `halt` 标成「Phase 7 前置」，位置是对的（无人值守之前必须有急停），
但它已经从「将来要做」变成「现在就缺」。

## 四条设计纪律

1. **默认只列不杀。** 与 `prune` 同源：杀进程是不可逆的，而正在跑的作业里
   **可能有已经花了钱、快要产出的单**。要杀就显式 `--kill`。
2. **⛔ 杀掉之后必须说清「已经花掉的钱不会退」。** 台账里那些跑完的单是真金白银，
   急停不撤销它们。不说清楚，人会以为「停了 = 没花钱」。
3. **杀进程组，不是杀单个 pid。** 作业本身只是个调度进程，真正干活的是它派生的
   `claude` 子进程。只杀父进程会留下一地孤儿——它们还在烧钱，而你以为已经停了。
   这正是 `launch` 用 `CREATE_NEW_PROCESS_GROUP` / `start_new_session` 的原因之一：
   **为了能整组杀掉**。
4. **⚖️ 「还在跑」和「已经死了」要分开列。** 死作业是**要清理的残骸**，不是要杀的目标。
   混在一起会让一个陈旧的死作业使 `halt` 永远返回非 0、且永远清不掉（G-51）。
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import jobs as jobs_mod


@dataclass(frozen=True)
class Victim:
    job: jobs_mod.Job
    state: str
    detail: str


def survey(project: Path) -> tuple[list[Victim], list[Victim]]:
    """返回 (还在跑的, 已经死掉/被叫停的残骸)。⛔ 不杀任何东西。"""
    alive, wreck = [], []
    for j in jobs_mod.listing(project):
        st, msg = j.status()
        if st == "running":
            alive.append(Victim(j, st, msg))
        elif st in ("died", "halted"):
            wreck.append(Victim(j, st, msg))
    return alive, wreck


def _terminate(pid: int) -> tuple[bool, str]:
    """真正下手的那一步。⚠️ 单独拎出来是为了能在测试里替换——
    真去 `taskkill` 需要先起一个真进程，平台强绑定且成本高。"""
    if os.name == "nt":
        # /T = 连同子进程树一起终止，/F = 强制
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return True, "已终止（含子进程树）"
        return False, (r.stdout + r.stderr).strip()[:200]
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True, "已发 SIGTERM 给整个进程组"
    except (ProcessLookupError, PermissionError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def kill(job: jobs_mod.Job) -> tuple[bool, str]:
    """杀掉一个作业**及其整个进程树**，并留痕。返回 (成功与否, 说明)。

    ⚠️ 必须杀进程树：作业进程只是调度器，真正烧钱的是它派生的 `claude` 子进程。
    只杀父进程会留下一地孤儿——**它们还在跑、还在花钱，而你以为已经停了**。
    ⚠️ 必须留痕：不写记录的话，被人叫停和自己崩了从外面看完全一样。
    """
    pid = job.meta.get("pid")
    if not pid:
        return False, "作业记录里没有 pid"
    if not job.pid_alive():
        return False, "进程已经不在了"
    ok, msg = _terminate(pid)
    if ok:
        job.mark_halted()
    return ok, msg


def _spent(job: jobs_mod.Job) -> tuple[int, float, int]:
    done = job.done_units().values()
    spent = sum(r.get("cost_usd_real") or 0 for r in done)
    unknown = sum(1 for r in done if r.get("cost_usd_real") is None)
    return len(done), spent, unknown


def report(project: Path, *, do_kill: bool) -> tuple[str, int]:
    """返回 (给人看的报告, **还在跑的**作业数)。

    ⚠️ 退出码只由「还在跑」的数量决定——残骸不该让 `halt` 永远喊「还有活干」。
    """
    alive, wreck = survey(project)
    if not alive and not wreck:
        return "没有正在跑的作业。", 0

    L: list[str] = []
    if alive:
        L += [f"{len(alive)} 个作业还活着：", ""]
        for v in alive:
            n, spent, unknown = _spent(v.job)
            L.append(f"  {v.job.id}  [{v.state}]  {v.detail}")
            L.append(f"      pid {v.job.meta['pid']} · devloop "
                     f"{' '.join(v.job.meta['argv'][:4])}…")
            L.append(f"      已完成 {n} 单，已花 ${spent:.4f}"
                     + (f"（另有 {unknown} 单成本未知）" if unknown else ""))
        L.append("")

    if wreck:
        L += [f"另有 {len(wreck)} 个残骸（进程已不在，**没有东西可杀**）：", ""]
        for v in wreck:
            L.append(f"  {v.job.id}  [{v.state}]  {v.detail.splitlines()[0]}")
        L.append("   确认无用后手工删掉这些目录即可："
                 f"{project / jobs_mod.JOBS_DIR}")
        L.append("")

    if not alive:
        L.append("⚠️ 没有还在跑的作业，急停无事可做。")
        return "\n".join(L), 0

    if not do_kill:
        L.append("⛔ 本命令默认**只列不杀**——正在跑的作业里可能有已经花了钱、快要产出的单。")
        L.append(f"   确认要停：python -m devloop.cli halt --project {project} --kill")
        return "\n".join(L), len(alive)

    L.append("正在终止（连同子进程树——只杀调度进程会留下还在烧钱的孤儿）：")
    killed = 0
    for v in alive:
        ok, msg = kill(v.job)
        L.append(f"  {'✓' if ok else '✗'} {v.job.id}  {msg}")
        killed += ok
    L.append("")
    L.append(f"终止 {killed}/{len(alive)} 个。")
    L.append("⚠️ **已经花掉的钱不会退。** 台账里那些跑完的单是真金白银，急停不撤销它们；")
    L.append("   正在跑但没跑完的那些单，钱花了、产出没有——那部分是纯损失。")
    L.append(f"   看清单：python -m devloop.cli status --project {project} --all")
    return "\n".join(L), len(alive) - killed
