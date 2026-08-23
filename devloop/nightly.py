"""夜间接管：列出还没合进主线的 `devloop/*` 隔离分支。

**为什么单独一个函数**：无人值守跑一夜后，人接管的第一个动作是「今天欠我
几条产出待处理」。`prune.report()` 打的是给人看的长报告；这里要的是**能被
下一单直接吃**的短清单，一行一个分支，方便管道拼接。

⚠️ 判据复用 `prune.scan()`——`safe_to_delete` 已经把「已合并」「空分支」两种
   不用管的情形排掉了，剩下的正是「有未合并产出」。⛔ 不许自己再写一遍扫描
   逻辑：`scan()` 里那三条判据（`+ ` 前缀、尖端提交主题、身份不能查）都是被
   实测抓出来的教训，重写一遍就是把坑再踩一遍。
"""

from __future__ import annotations

import json
from pathlib import Path

from devloop import prune, telemetry


def branch_lines(project: Path) -> list[str]:
    """列出 devloop/* 隔离分支里**还没合进主线**的那些，一行一个。

    每行形如 `devloop/xxx @ abc1234 · 闸全绿 · 改 3 个文件`。
    ⛔ 不要返回一个「没有」字样的字符串——调用方要按行数判定，字符串会把
    「零条」和「一条叫『没有』的分支」混成一件事。

    ⭐ **判定必须在行里**：2026-08-02 之前每行只有名字和 sha，于是
    「五道闸全绿的产出」与「闸没过的垃圾」印出来一模一样，人必须逐支
    `git show` 才分得开——那就是早上 30 秒读不完的原因。
    ⛔ 文件数数不出来时印「数不出」，**不许当 0**：把「没数据」和「零改动」
    混成一件事，排查时找不到线索。
    """
    out = []
    for b in prune.scan(project):
        if b.safe_to_delete:
            continue
        n = prune._files_changed(project, b.name)
        out.append(f"{b.name} @ {b.sha} · {b.gate or '判定读不出'} · "
                   + (f"改 {n} 个文件" if n is not None else "改了几个文件数不出"))
    return out


def report(project: Path) -> str:
    """一份「昨夜发生了什么」的简报，给第二天早上看。

    两块信息：
    1) 待处理的隔离分支（复用 `branch_lines`）——回答「今天欠我几条产出」。
    2) 台账最近 24 小时的单数与合格数——回答「过去一夜跑得怎样」。

    ⚠️ 「没记录」必须**明说**，不能返回空串：早上打开一片空白，人会以为
       报告工具坏了，而不是「昨晚真的没跑过」。两种情形要能分开。
    """
    veto = _veto_block(project)
    pending = branch_lines(project)
    if pending:
        branch_block = "待处理分支（有未合并产出）：\n" + "\n".join(
            f"  - {ln}" for ln in pending)
    else:
        branch_block = "待处理分支：无。"

    #  ⭐ 过 `units()`：一单是「开跑行 + 收工行」两行（G-108），
    #     ⛔ 按行数报「派单 N 次」会翻倍。
    rows = telemetry.units(telemetry.load(
        project / ".devloop" / "telemetry.jsonl", since_days=1))
    if not rows:
        ledger_block = "最近 24 小时：台账里没有记录。"
    else:
        n = len(rows)
        ok = sum(1 for r in rows if r.get("ok"))
        ledger_block = f"最近 24 小时：派单 {n} 次，合格 {ok}。"

    return f"{veto}\n\n{branch_block}\n\n{ledger_block}"


def _veto_block(project: Path) -> str:
    """⭐ **整批一票否决位**——放在最上面，因为它决定「要不要逐支看」。

    ⛔ 只要有一条成立，这一批所有的「绿」全部作废，不用往下读了：

    | 否决位 | 为什么它让整批不可信 |
    |---|---|
    | `gate_code == 2` | 闸自身故障——它给出的任何结论都不能用于验收 |
    | `error` 含「宪法命中」 | 工人越线了，⚠️ 那是**在等你批准**，不是活没干好 |
    | 台账读不出来 | 失控防线读的就是这些数，读不全等于刹车松了 |
    | 上一轮 `stop` 是 null | ⚠️ **没走到收尾**——多半是崩了或被杀，进度可能不完整 |
    | `cost_usd_real is None` | 算不出成本 ≠ 花了 0 元，⛔ 单独成行不并进花费 |

    ⚠️ 「没有否决位」也要**明说**，⛔ 不许留空——空白会被读成「报告坏了」。
    """
    tel = project / ".devloop" / "telemetry.jsonl"
    L: list[str] = []
    try:
        #  ⭐ 同上：按单元不按行数（G-108）。
        rows = telemetry.units(telemetry.load(tel, since_days=1))
    except Exception as exc:                      # LedgerCorrupted 等
        return ("⛔ **整批不可信**：台账读不出来——"
                + str(exc).splitlines()[0][:160]
                + "\n   ⚠️ 失控防线读的就是这些数，在修好之前别信任何统计。")

    #  ⭐ `gate_code == 2` 有**两种**成因，处置完全相反（G-116）：
    #       · 环境真坏了      → 「先修环境」
    #       · 墙钟到点把闸掐了 → 「加大 max_wall_min，⛔ 环境没坏」
    #  ⚠️ 混在一起的后果是：第二天早上人被指去修一个根本没坏的东西。
    #  ⛔ `gate_wall_killed` 缺省 `None`（老行都没有）→ 落进**保守**那一档
    #     （当成环境可能坏了）。⭐「可能坏了，去看一眼」远好过「没事，接着睡」。
    walled = sum(1 for r in rows
                 if r.get("gate_code") == 2 and r.get("gate_wall_killed") is True)
    broken = sum(1 for r in rows
                 if r.get("gate_code") == 2 and r.get("gate_wall_killed") is not True)
    con = sum(1 for r in rows if "宪法命中" in str(r.get("error") or ""))
    unknown = sum(1 for r in rows if r.get("cost_usd_real") is None)
    if walled:
        L.append(f"⚠️ **{walled} 单的闸被墙钟掐了**——⛔ 这**不是**闸坏了，"
                 f"环境没问题。⭐ 要么加大 `max_wall_min`，"
                 f"要么让工人少占点时间。")
    if broken:
        L.append(f"⛔ **闸自身故障 {broken} 单**——闸坏了的时候，它给出的任何"
                 f"结论都不能用于验收。先修环境，⚠️ 别看下面的绿。")
    if con:
        L.append(f"⛔ **宪法命中 {con} 单**——工人越线了。⚠️ 那是**在等你批准**，"
                 f"不是活没干好；批准或改任务书，⛔ 别让它重试。")
    #  ⭐ **被中断的单要点名说**（G-108）。
    #     ⚠️ 它会顺带落进「算不出成本」那一档（开跑行没有成本字段），
    #     ⛔ 但「价目表里没这个模型」和「进程被杀在半路」是两回事——
    #     前者改配置，后者要去看 worktree 里那半截活还在不在。
    #     2026-08-04 的教训：第二天早上这条命令答的是「✅ 没问题」。
    cut = sum(1 for r in rows if r.get("interrupted"))
    if cut:
        L.append(f"⛔ **{cut} 单被中断**（只有开跑行、没有收工行）——"
                 f"进程被杀 / Ctrl-C / 断电。⚠️ 钱花过了，**花了多少不知道**；"
                 f"⭐ 先跑 `devloop prune --project <项目>` 看那几个工位里"
                 f"有没有没提交的活。")
    if unknown:
        L.append(f"⚠️ **{unknown} 单算不出成本**（价目表里没有那个模型"
                 + ("，或那一单被中断了" if cut else "") + "）。"
                 f"⛔ 算不出 ≠ 花了 0 元——预算防线读不到这些花费。")

    d = project / ".devloop" / "autopilot"
    for f in (sorted(d.glob("*.json")) if d.exists() else []):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            L.append(f"⚠️ 运行记录 {f.name} 读不出来——上一轮的结局无从判断。")
            continue
        stop = st.get("stop")
        if stop is None:
            L.append(f"⚠️ **{f.stem} 没走到收尾**（`stop` 是 null）"
                     f"——多半是崩了或被 halt 杀掉，⛔ 进度可能不完整。")
        elif stop.get("kind") != "done":
            #  ⛔ **这一行不截**（G-115 · 同一毛病第三次）。
            #  ⚠️ G-103 记着：「工具停着，而它印给人的唯一一条自救命令被砍成半句」，
            #     并且点名 `nightly.py` 犯过同款——⭐「两次都恰好砍在『你该干什么』
            #     那句上，**那句话总在最后**」。而这里的 `[:100]` 一直没拿掉。
            #  ⭐ 撞额度那种停机被剪掉的正是唯一一句自救说明
            #     （「…恢复后重跑本阶段即可续上」），而挂一夜之后人敲的第一条
            #     命令就是 `nightly`。
            #  ⚠️ 截断的理由（防一行刷屏）在这里不成立：`detail` 是**工具自己拼的**，
            #     不是用户输入也不是子进程输出，长度天然有界。
            L.append(f"· {f.stem} 停在「{stop.get('why')}」"
                     f"（{stop.get('kind')}）——{stop.get('detail', '')}")

    if not L:
        return ("✅ **没有整批否决位**：闸没坏、没宪法命中、"
                "成本都算得出、上一轮走到了收尾。")
    return "## ⛔ 先看这里\n\n" + "\n".join(L)
