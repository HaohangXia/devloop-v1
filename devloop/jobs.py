"""非阻塞派单：派出去就走，回头再看。

## 为什么需要（触发条件已被实测远超）

PLAN 给 Phase 6 定的触发条件是「**被『等工人』真正卡住时**（当前同步派 3 单需干等约 2 分钟）」。
实测：

| 批次 | 干等多久 |
|---|---|
| Phase 4 首批 14 单 | 74 分钟 |
| Phase 4 第二批 10 单（p4-u02…u11 串行） | 54.6 分钟 |
| Phase 5 那 7 单前台（--parallel 3） | 18.1 分钟 |
| 评测集 20 题（--parallel 4，两次） | 9 分 05 秒 / 7 分 20 秒（墙钟） |

⚠️ 上表是**干等时长**，口径已于 2026-07-27 订正：早先记的「50 分钟 / 29 分钟 / 32 分钟」
分别是张冠李戴（另一批）与「各题耗时之和」被误标成墙钟。
⛔ `--detach` **不缩短执行时间**，只把等待从前台挪走——两者并排写会读成「派单变快了」。

**真实后果不只是等**：第二批那次 10 分钟超时被切，`u17` 那单**丢失、要重跑**。
而 Phase 7 的自动驾驶必须能「派出去就走」——否则一个长任务会把整条链堵死。

## 设计要害三条

1. **进度信号读台账，不读输出文件、不猜进程名。**
   这是 BACKLOG G-37 的教训：我曾用「输出文件 0 字节 + `ps` 没匹配到」断定任务没跑，
   实际三次全跑了，多花两单。台账每单跑完追加一行，**那是最直接的证据**。
   ⚠️ 但要按**去重后的单元名**算，不能按行数算——同名两行不是两单（G-51）。
2. **Windows 上必须真脱离父进程。** `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`——
   否则父进程一退，子进程跟着死，「非阻塞」就成了「静默丢活」。
   非 Windows 上对应的是 `start_new_session=True`，**它同时是急停能整组杀的前提**。
3. **⛔ 作业记录只登记「派了什么」，不登记「结果如何」。**
   结果的唯一真源是台账与回执。两处都记会分叉，而分叉的账本比没有账本更坏
   ——它看起来是权威的。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import naming

JOBS_DIR = ".devloop/jobs"
HALTED = "halted.json"


class JobError(RuntimeError):
    """作业记录读不出来。⛔ 绝不能降级成「就看上一个作业吧」。"""


def _is_windows() -> bool:
    """⚠️ 单独拎成函数是为了能在测试里替换——直接改 `os.name` 会连 pathlib 一起弄坏。"""
    return os.name == "nt"


@dataclass(frozen=True)
class Job:
    id: str
    root: Path
    meta: dict

    @property
    def log(self) -> Path:
        return self.root / "console.log"

    @property
    def expected(self) -> list[str]:
        return list(self.meta["units"])

    @property
    def halted(self) -> dict | None:
        """被人急停过吗。⚠️ 「被叫停」和「自己崩了」从外面看必须能分开。"""
        f = self.root / HALTED
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            return {"halted_at": "?", "by": "?"}

    def mark_halted(self, by: str = "devloop halt --kill") -> None:
        (self.root / HALTED).write_text(
            json.dumps({"halted_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": by},
                       ensure_ascii=False), encoding="utf-8")

    def pid_alive(self) -> bool:
        """进程还在吗。**只用来区分「跑着」与「死了」，不用来判断成败。**

        ⚠️ 光看 pid 存不存在是不够的：pid 会被系统回收再分配给别的进程，
        那时死作业会永远显示 running，而 `halt --kill` 会拿 `taskkill /T /F`
        去杀一棵**无辜的进程树**。所以同时比对映像名。
        残余风险（另一个 python 进程恰好复用了这个 pid）见 SPEC，是已知代价。
        """
        pid = self.meta.get("pid")
        if not pid:
            return False
        want = (self.meta.get("exe") or Path(sys.executable).name).lower()
        if _is_windows():
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stdout
            for line in out.splitlines():
                parts = [p.strip('" ') for p in line.split('","')]
                if len(parts) >= 2 and parts[1].strip('"') == str(pid):
                    return parts[0].strip('"').lower() == want
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def done_units(self) -> dict[str, dict]:
        """从**台账**读已完成的单——这是唯一可信的进度信号（G-37）。

        ⛔ 按单元名去重，**后写的行覆盖先写的**（重跑的意义就是覆盖旧结果）。
        按行数算是假绿：同一单元两行会把「跑完 2 单」判成真，而另一单一次都没跑。
        """
        tel = Path(self.meta["telemetry"])
        if not tel.exists():
            return {}
        #  ⛔ 走 `telemetry.load`，不许自己逐行 `json.loads`。
        #     ⚠️ 这是第三份裸解析（`autopilot.read_progress` / `_rows_for` 之外），
        #     2026-08-02 独立复核抓到——⭐ 三份分别演化的后果是
        #     「同一个台账，三个进度」，而这三处分别服务着自动驾驶的刹车、
        #     交接单、以及 `--detach` 后台作业的进度判定。
        from . import telemetry as _tele
        #  ⭐ 过 `units()`（G-108）：一单是「开跑行 + 收工行」两行。
        #  ⛔ 漏掉它的后果实测过：一个被强杀的作业会被报成「2 单跑完了」，
        #     而它本该报「进程已不在，只完成 1/2」——**急停从此看不见还在烧钱的活**。
        rows = _tele.units(_tele.load(tel))
        want = set(self.expected)
        # 只认本作业开始之后写入的行——同一任务名可能被派过多次
        #
        #  ⛔⛔ **「开跑了」不是「干完了」**（G-121，2026-08-05 真跑当场抓到）。
        #     ⚠️ G-108 加的开跑行会被 `units()` 当成一个单元返回（带 `interrupted`），
        #     而本函数叫 `done_units` —— 不排掉它的后果**实测过**：
        #     一个**正在跑、正在烧额度**的作业，`status()` 会算出「没有 missing」
        #     ⇒ 判 `done`「1 单跑完」⇒ ⛔ **`devloop halt` 从此看不见它**，
        #     回你一句「没有正在跑的作业」。
        #  ⭐ 又是那个形状：**加了一种新的账本行，只教会了一部分读者认它。**
        #     （第九次；前八次见 BACKLOG G-120 顶部那段）
        #  ⚠️ 判据落在 `interrupted` 这个结构化字段上，⛔ 不看 `event` 字符串
        #     ——`units()` 才是唯一知道「这行有没有配对」的地方。
        return {r["task"]: r for r in rows
                if r.get("task") in want and not r.get("interrupted")
                and r.get("ts", "") >= self.meta["started"]}

    def status(self) -> tuple[str, str]:
        """返回 (状态, 一句话)。状态：running / done / halted / died。"""
        done = self.done_units()
        missing = sorted(set(self.expected) - set(done))
        total = len(set(self.expected))
        if not missing:
            bad = sum(1 for r in done.values() if not r.get("ok"))
            return "done", f"{total} 单跑完" + (f"，其中 {bad} 单未通过" if bad else "，全部通过")
        n = len(done)
        h = self.halted
        if h and not self.pid_alive():
            return "halted", (f"⛔ 已被叫停（{h.get('halted_at', '?')}），完成 {n}/{total}。"
                              f"未完成：{'、'.join(missing)}"
                              f"\n      ⚠️ 已经花掉的钱不会退。")
        if self.pid_alive():
            return "running", f"进行中 {n}/{total}"
        # ⚠️ 进程没了但活没干完 —— 明确报出来，不含糊成「可能还在跑」
        return "died", (f"⚠️ 进程已不在，但只完成 {n}/{total}。"
                        f"未完成：{'、'.join(missing)}")


def _dir(project: Path) -> Path:
    return project / JOBS_DIR


def register_self(project: Path, argv: list[str], units: list[str], *,
                  telemetry: Path) -> Job:
    """把**当前进程**登记成一个作业。⛔ 不起任何新进程。

    ## ⛔ 为什么需要它（G-117）

    `launch` 是给 `dispatch --detach` 用的——它**起一个后台进程**。
    而 `autopilot` 跑在**前台**，于是它从来不登记，⚠️ 后果是：

    - `devloop halt` 看不见正在跑的自动驾驶，回你一句「**没有正在跑的作业**」；
    - 半夜想叫停，只能守在那个窗口按 Ctrl-C；
    - `status` 也看不见它。

    ⭐ 而「挂一夜」正是**没人守在窗口前**的那种场景——
    急停恰好在最需要它的形态上失效。

    ⚠️ 这是本项目重复了七次的同一个形状：**同一件事有两条路，
    装好的永远是「有人盯着」的那条**（硬拒只装手动那条、三道红线只传手动那条、
    模板改了副本不跟、台账改了读侧不跟、刹车没接到正在跑的单、变量塞了没告诉工人）。

    ## ⚠️ 与 `launch` 的唯一区别

    `pid` 记的是**自己**（`os.getpid()`），且不 spawn。
    ⭐ 其余字段与 `launch` 一致，所以 `status()` / `halt` / `prune` 一行都不用改。
    """
    jid = naming.stamp()
    root = _dir(project) / jid
    root.mkdir(parents=True)
    meta = {
        "job_id": jid, "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": str(project), "telemetry": str(telemetry),
        "argv": argv, "units": units,
        "exe": Path(sys.executable).name,
        "cwd": os.getcwd(),
        #  ⚠️ 前台作业没有独立的控制台日志——输出直接在用户的终端上。
        #     ⭐ 明说，⛔ 别让人去找一个不存在的文件。
        "foreground": True,
    }
    (root / "job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return Job(jid, root, meta)


def launch(project: Path, argv: list[str], units: list[str], *, telemetry: Path) -> Job:
    """起一个脱离父进程的后台作业，立刻返回。

    `argv` 由 `cli._rebuild_argv` 从**已解析的参数**规范化重建（路径已绝对化）。
    ⚠️ 不从 `sys.argv` 反推：程序化调用 `main(argv)` 时那串跟本次请求毫无关系，
    而 argparse 的前缀缩写（`--det`）也让「减掉 --detach」这种做法必然漏网。
    重建的正确性由 `test_重建的argv喂回解析器必须得到同一个请求` 钉死。
    """
    jid = naming.stamp()
    root = _dir(project) / jid
    root.mkdir(parents=True)
    log = root / "console.log"
    metaf = root / "job.json"

    meta = {
        "job_id": jid, "pid": None,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": str(project), "telemetry": str(telemetry),
        "argv": argv, "units": units,
        "exe": Path(sys.executable).name,
        "cwd": os.getcwd(),          # 便于复现：子进程就在这里跑
    }
    # ⛔ 先落盘再起进程：反过来的话，中途被 Ctrl-C 就永久留下「有孤儿进程在跑、
    #    但 status 因为读不到记录而回退去报**上一个作业**成功」的状态。
    metaf.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    kw: dict = {}
    if _is_windows():
        # ⛔ 少了这两个标志，父进程一退子进程就死——「非阻塞」会变成「静默丢活」
        kw["creationflags"] = (subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        # 同上，且这是 halt 能整组杀的前提；缺了它 killpg 会带走调用者自己的 shell
        kw["start_new_session"] = True

    # ⚠️ cwd 留在用户当初所在的目录。曾把它设成工具包目录——那个目录自己有一份
    #    合法 `.devloop/`，于是相对 `--project` 会**静默改派到 devloop 仓库**。
    #    包路径改由 PYTHONPATH 提供。
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = (pkg_parent + os.pathsep + env["PYTHONPATH"]
                         if env.get("PYTHONPATH") else pkg_parent)

    with log.open("w", encoding="utf-8") as f:
        # ⭐ `-u`：不加的话 stdout 重定向到文件是块缓冲，**作业跑完之前 console.log
        #    恒为 0 字节**，而 CLI 把这个路径当作看进度的手段递给用户（G-37 复发）。
        p = subprocess.Popen([sys.executable, "-u", "-m", "devloop.cli", *argv],
                             stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, cwd=os.getcwd(), env=env, **kw)

    meta["pid"] = p.pid
    metaf.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return Job(jid, root, meta)


def _read(d: Path) -> Job:
    f = d / "job.json"
    try:
        return Job(d.name, d, json.loads(f.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise JobError(f"作业记录损坏：{f} —— {exc}") from exc


def load(project: Path, job_id: str | None = None) -> Job:
    d = _dir(project)
    if not d.exists():
        raise FileNotFoundError(f"没有后台作业（{d} 不存在）")
    if job_id:
        if not (d / job_id / "job.json").exists():
            raise FileNotFoundError(f"找不到作业 {job_id}")
        return _read(d / job_id)

    dirs = sorted(x for x in d.iterdir() if x.is_dir())
    if not dirs:
        raise FileNotFoundError(f"{d} 下没有作业")
    # ⛔ 最新的那个缺记录就报出来，**不许回退到更早的作业**——回退会把一个
    #    早已跑完的旧作业报成「done，全部通过，rc=0」，而真正在跑的那个没人管。
    if not (dirs[-1] / "job.json").exists():
        raise JobError(
            f"最新的作业目录 {dirs[-1].name} 里没有 job.json——它可能是起进程时被打断留下的。\n"
            f"⚠️ 不回退去看更早的作业：那会把旧作业的「全部通过」当成本次结果。\n"
            f"   看指定作业：--job <ID>   ｜ 确认无用后删掉该目录即可")
    return _read(dirs[-1])


def listing(project: Path) -> list[Job]:
    d = _dir(project)
    if not d.exists():
        return []
    return [_read(x) for x in sorted(x for x in d.iterdir() if (x / "job.json").exists())]
