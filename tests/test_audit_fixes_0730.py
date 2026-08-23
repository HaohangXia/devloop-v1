"""2026-07-30 审查抓到的六条阻断，各钉一条测试。

⚠️ 这些**全都不是「功能不对」，而是「守卫看起来在、实际不在」**——
本项目一直在防的那类东西。每条都写清「原来是什么形状」，
因为形状比结论更有用：同一个形状还会在别处再出现。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from devloop import telemetry
from devloop.dispatch import DispatchResult
from devloop.models import Receipt


# ══ B5 · 台账并发写 ═══════════════════════════════════════════

def test_台账并发追加不许丢行也不许写坏行(tmp_path):
    """⛔ **这条推翻了 BACKLOG G-41 的结论。**

    G-41 记的是「16 线程并发写 → 16 行齐全、0 行无法解析 ✅ 未损坏」。
    2026-07-30 复跑：那是**一次**试验，而且用的是等长短行。
    换成真实形态（不等长——带 error 文本的行长好几倍）：
        40 次试验 **40 次都丢行且出坏行**，其中一次写出非法 UTF-8 字节，
        连 `read_text` 都抛 UnicodeDecodeError。

    ⛔ 后果不是「少了几行日志」：自动驾驶的每一条防线都只从台账读。
    丢行 → 防线读到偏小的数 → 预算永不到顶、已绿的单被重派；
    坏行 → 整个台账读不出来 → 防线全部归零。

    ⚠️ 判据要用**不等长**的行：等长短行几乎不会撞，那正是 G-41 侥幸的原因。
    """
    p = tmp_path / "t.jsonl"
    THREADS, PER = 8, 60

    def worker(k: int) -> None:
        for i in range(PER):
            telemetry.record(
                p, DispatchResult(
                    f"t{k}-{i}",
                    Receipt(is_error=True, num_turns=1,
                            usage={"input_tokens": 1, "output_tokens": 1}),
                    None, error="x" * (37 * (k + 1) % 400)),
                model="m", tools="readonly")

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    rows = telemetry.load(p)              # ⛔ strict：有坏行就抛
    assert len(rows) == THREADS * PER, \
        f"丢行了：期望 {THREADS * PER}，实得 {len(rows)}"


def test_台账有坏行时必须报出来不许静默跳过(tmp_path):
    """⛔ 静默跳过 = 让「已花多少」「派了几次」偷偷变小，
    而那正是失控防线读的数——防线会因此形同虚设。

    ⚠️ 原实现是裸列表推导，一行坏行直接抛 `JSONDecodeError`，
    而上层 `except ValueError` 把它显示成「输入错误：Unterminated string」
    ——**不提文件名、不说是台账**，人根本不知道该去看哪。
    """
    p = tmp_path / "t.jsonl"
    p.write_text('{"ts":"2026-07-30T00:00:00","ok":true}\n{坏行\n',
                 encoding="utf-8")
    with pytest.raises(telemetry.LedgerCorrupted) as e:
        telemetry.load(p)
    assert "台账" in str(e.value) and str(p) in str(e.value), \
        "报错必须说清是哪个文件的台账坏了"


# ══ H2 · 钱花了但台账零行 ═════════════════════════════════════

def test_花完钱之后出错也要返回结果而不是抛出去(tmp_path, monkeypatch):
    """⛔ 上层判「钱花没花」的依据是 `dispatch_one` **有没有返回**。

    它把异常抛出去，上层的补记逻辑（`if res is not None`）就永远读不到，
    于是**一单花过钱的活台账零行**——而失控防线全都只从台账读。

    ⚠️ 实测复现过：把 `.devloop/reports` 造成一个**文件**（mkdir 抛
    FileExistsError），输出只有一行 `✗ t: FileExistsError`，
    台账 0 行、一个字都没提钱花过。
    """
    import subprocess
    from devloop import dispatch as dsp
    from devloop.config import ProjectPaths
    from devloop.models import TaskSpec, WorkerConfig

    (tmp_path / ".devloop").mkdir()
    (tmp_path / ".devloop" / "rules-digest.md").write_text("x", encoding="utf-8")
    # ⛔ 把 reports 造成文件：mkdir 必炸
    (tmp_path / ".devloop" / "reports").write_text("我是个文件", encoding="utf-8")
    task = tmp_path / "t.md"
    task.write_text("# 角色\n工人\n\n# 任务\n干活\n\n# 禁令\n无\n", encoding="utf-8")

    class _P:
        stdout = json.dumps({"type": "result", "subtype": "success",
                             "is_error": False})
        stderr = ""
    monkeypatch.setattr(dsp.subprocess, "run", lambda *a, **k: _P())

    res = dsp.dispatch_one(TaskSpec.load(task), ProjectPaths(tmp_path),
                           WorkerConfig(base_url="", model="m", auth_token=""))
    assert res is not None, "⛔ 钱花过了就必须返回结果，不许把异常抛出去"
    assert res.error and "钱已花掉" in res.error, \
        f"错误信息要说清钱已经花了，实得 {res.error}"


# ══ H4 · 例外路径补记丢字段 ═══════════════════════════════════

def test_例外补记台账不许丢掉额度状态():
    """⛔ 用 `replace` 而不是重建。重建要把每个字段手抄一遍，新增字段必然漏。

    ⚠️ 这里漏掉的是 `rate_limit`：一单撞了额度又在后续环节抛异常，
    补记时额度状态被丢掉，台账里那一行就再也说不清「当时是不是撞额度了」。
    ⛔ 同一个坑主路径上已经踩过一次。
    """
    import inspect
    from devloop import cli
    src = inspect.getsource(cli._run_unit)
    tail = src[src.index("except Exception"):]
    assert "dc_replace(res," in tail, \
        "⛔ 例外路径补记台账要用 dc_replace，别重建 DispatchResult"
    assert "DispatchResult(res.task" not in tail, \
        "⛔ 还在重建——新增字段会被静默丢掉"


# ══ B6 · 接力点要落盘 ════════════════════════════════════════

def test_接力点必须落盘否则重启就丢(tmp_path):
    """⛔ 接力点原来只活在进程内存里。

    于是 `--resume` 之后 `base` 被重算回项目 HEAD，而前几单的产出只在
    **未合并的隔离分支**上、树里根本没有——下游单要么必然失败，
    要么工人「自己重写一个顶上」，**产出静默分叉**。
    ⚠️ 而崩溃恢复正是无人值守的立项理由。
    """
    from devloop import autopilot, plan as plan_mod
    from devloop.config import ProjectPaths

    (tmp_path / ".devloop").mkdir()
    for tid in ("a",):
        (tmp_path / f"{tid}.md").write_text(
            "# 角色\n工人\n\n# 任务\n干活\n\n# 禁令\n无\n", encoding="utf-8")
    sp_file = tmp_path / "s.toml"
    sp_file.write_text(
        "[plan]\nversion = 1\n"
        f"[stage]\nid='s'\ngoal='g'\ntask_dir='{tmp_path.as_posix()}'\n"
        "base='HEAD'\nchain = true\n"
        "[stage.budget]\ntotal_usd=1.0\nreserve_usd=0.1\nmax_dispatches=4\n"
        "max_wall_min=10\nwatchdog_k=2\n"
        "[[task]]\nid='a'\ntools='implement'\n"
        "[task.accept]\nrequire_pass=['语法']\n", encoding="utf-8")
    sp = plan_mod.load(sp_file)
    paths = ProjectPaths(tmp_path)

    run = autopilot.Run(plan=sp, started_at="2026-07-30T00:00:00", started=0.0)
    run.chain_head = "abc123def456"
    run.save(paths, autopilot.Progress())

    assert autopilot.resume_chain_head(paths, sp) == "abc123def456", \
        "⛔ 接力点没落盘——重启之后下游单会从项目 HEAD 建，看不到前几单的产出"


# ══ H3 · 磁盘余量 ════════════════════════════════════════════

def test_盘不够时拒绝建worktree(tmp_path, monkeypatch):
    """⚠️ 手动跑一单没人会写满盘；跑一夜十几单、每单几百 MB 就会。

    ⛔ 写满盘不是「少跑一单」——`snapshot_base` 会造提交对象，
    git 写到一半，仓库可能进入需要手工救的状态。
    """
    import shutil
    from devloop import worktree as wt

    class _U:
        free = 1 * 1e6                    # 只剩 1 MB
    monkeypatch.setattr(shutil, "disk_usage", lambda p: _U())
    with pytest.raises(OSError) as e:
        wt.check_disk(tmp_path, tmp_path)
    assert "prune" in str(e.value), "报错要告诉人怎么清"


def test_盘够时不许拦(tmp_path, monkeypatch):
    """⚠️ 防回归：检查本身不该挡住干活。判不出来也放行。"""
    import shutil
    from devloop import worktree as wt

    class _U:
        free = 500_000 * 1e6              # 很富裕
    monkeypatch.setattr(shutil, "disk_usage", lambda p: _U())
    wt.check_disk(tmp_path, tmp_path)     # 不该抛


def test_两条探针路径都必须留台账():
    """⛔ **G-64：第一版只补了一条，漏了另一条。**

    `doctor` 有两条会**真花额度**的路：
      · `--project` → `_dispatch_smoke()` 真派一单
      · `--probe`   → `credentials.probe()` 真起子进程

    2026-07-30 我给前者补了 `_record_smoke`，**漏了后者**——于是 `--probe`
    花掉的窗口仍然对三条失控防线完全隐形。

    ⚠️ 判据落在**源码**上：要真验「留没留账」得真花一次额度，那不能拿来做测试。
    ⛔ 但只检查「有没有 import telemetry」是不够的（那是第一种假绿：
    守卫的目标不对）——所以两条路都要各自确认调用点存在。
    """
    import inspect
    from devloop import credentials, doctor

    smoke = inspect.getsource(doctor._dispatch_smoke)
    assert "_record_smoke(" in smoke, "⛔ --project 那条路没留账"

    pr = inspect.getsource(credentials.probe)
    assert "telemetry.record(" in pr, "⛔ --probe 那条路没留账"
    assert "project is not None" in pr, \
        "⚠️ 没有 project 就没地方留账，那时该跳过而不是报错"

    # ⛔ 而且 doctor 必须真的把 project 传下去——不传的话上面那两条形同虚设
    creds = inspect.getsource(doctor._credentials)
    assert "project=project" in creds, "⛔ doctor 没把 project 传给探针"


def test_磁盘估算必须按同一个项目来估(tmp_path, monkeypatch):
    """⛔ **拿别的项目的 worktree 去估，会偏小两个数量级。**

    实测（2026-07-30）：`.devloop-worktrees/` 里 7 个全是 devloop 自己的
    **4MB** 小目录，而 eco-ob 一个 worktree 要 **610MB**——差 150 倍。
    第一版 `check_disk` 取的是 `existing[0]`（目录里第一个，不管属于谁），
    ⛔ 于是在 eco-ob 上它会用 4MB 去估 610MB 的需求：盘快满时照样放行。

    ⚠️ 一个偏小 150 倍的估计**等于没有估计**——比没有更坏，因为它看起来像有。
    """
    import shutil
    from devloop import worktree as wt

    root = tmp_path / "wts"
    root.mkdir()
    # 别的项目留下的小 worktree
    small = root / "otherproj-t-1"
    small.mkdir()
    (small / "a.txt").write_text("x", encoding="utf-8")
    # 本项目自己的大 worktree
    proj = tmp_path / "bigproj"
    proj.mkdir()
    big = root / "bigproj-t-1"
    big.mkdir()
    (big / "blob.bin").write_bytes(b"0" * 3_000_000)      # 3 MB

    class _U:
        free = 2.0 * 1e6 + 500 * 1e6      # 不够「本项目 3MB + 500MB 余量」
    monkeypatch.setattr(shutil, "disk_usage", lambda p: _U())

    with pytest.raises(OSError) as e:
        wt.check_disk(proj, root)
    assert "3" in str(e.value), \
        f"⛔ 该按本项目那个 3MB 的 worktree 估，不是别人的 1 字节：{e.value}"
