"""阶段内接力：让后一单看得见前一单的产出（G-59）。

## ⛔ 不接力的时候是什么样

每单的 worktree 都从**同一个 base** 建（`cli.py` 在 while 循环外算一次
`base`，循环里每单都用它）。于是 `needs` 只保证**先后顺序**，
后一单**看不见**前一单的产出。

这样一份自然的计划**跑不了**：

    1. 写模块  →  2. 给模块写测试（needs 1）  →  3. 修问题（needs 2）

第 2 单打开工作区会发现模块不存在。三单各自从同一张白纸开始。
⚠️ 现在能跑的只剩「一批互不相干的小改动」——而那恰恰是**最不需要**
自动驾驶的活，它没有需要编排的依赖。

## ⚠️ 为什么必须是开关，不能是默认

接力把几单**绑在了一起**：后一单的分支里含着前一单的提交。
好处是合并时合最后一个分支就全拿到了；⛔ 代价是**不能只否掉前一单**。
而「合回主线要人逐单批」是宪法条款，所以这个代价必须由人明确接受。

## ⭐ 接力点为什么是安全的

base 前移到的那个提交，是**编排方**在**闸全绿之后**写的（`commit_result`，
且提交前断言过 HEAD == 该分支）。工人自己提交不会成为接力点。
⛔ 所以这跟「拿 worktree 的 HEAD 当锚」是两回事——那个才是要防的，
因为攻击成功时锚会跟着动。这里是「只有通过验收的检查点才推进」。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _plan(tmp_path: Path, *, chain: str = "") -> Path:
    for tid in ("a", "b"):
        (tmp_path / f"{tid}.md").write_text(
            "# 角色\n工人\n\n# 任务\n干活\n\n# 禁令\n无\n", encoding="utf-8")
    p = tmp_path / "s.toml"
    p.write_text(
        "[plan]\nversion = 1\n"
        f"[stage]\nid='s'\ngoal='g'\ntask_dir='{tmp_path.as_posix()}'\n"
        f"base='HEAD'\n{chain}"
        "[stage.budget]\ntotal_usd=1.0\nreserve_usd=0.1\nmax_dispatches=6\n"
        "max_wall_min=30\nwatchdog_k=3\n"
        # ⚠️ retries=2：要验「失败不推进接力点、重试成功才推进」，就得真发生
        #    一次重试。默认 retries=1 会在第一次失败后直接升档上报，测不到。
        "[[task]]\nid='a'\ntools='implement'\nretries=2\n"
        "[task.accept]\nrequire_pass=['语法']\n"
        "[[task]]\nid='b'\ntools='implement'\nneeds=['a']\n"
        "[task.accept]\nrequire_pass=['语法']\n",
        encoding="utf-8")
    return p


def test_默认不接力(tmp_path):
    """⚠️ 防回归：接力改变了几单之间的耦合关系，⛔ 不许悄悄成为默认。"""
    from devloop import plan as plan_mod
    sp = plan_mod.load(_plan(tmp_path))
    assert sp.chain is False


def test_可以在计划里开接力(tmp_path):
    from devloop import plan as plan_mod
    sp = plan_mod.load(_plan(tmp_path, chain="chain = true\n"))
    assert sp.chain is True


# ══ 真正要验的：base 有没有跟着往前走 ═══════════════════════════

def _run_stage(tmp_path, monkeypatch, *, chain: bool, ok_seq):
    """跑 cmd_autopilot，只换掉 `_run_unit`。返回每单收到的 base。"""
    from devloop import backends, cli, constitution

    import subprocess
    # ⚠️ 宪法登记基准要读 git 引用，tmp 目录必须是个真仓库。
    for cmd in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".devloop" / "plans").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".devloop" / "rules-digest.md").write_text("x", encoding="utf-8")
    # ⚠️ 宪法模板把 gates.sh 列进受保护清单，而「守卫的目标不存在」会被
    #    登记基准那一步正确地拦下（那是六种假绿里的第一种）。所以得有一份。
    (tmp_path / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    src = _plan(tmp_path, chain="chain = true\n" if chain else "")
    # 计划固定读 .devloop/plans/<阶段>.toml，没有 --plan-dir 这种参数
    (tmp_path / ".devloop" / "plans" / "s.toml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")

    bases: list[str] = []
    seq = iter(ok_seq)

    def fake(spec, paths, cfg, **kw):
        bases.append(kw.get("base", ""))
        ok = next(seq, False)
        if ok and kw.get("out_ref") is not None:
            # 编排方在闸全绿之后写的那条提交
            kw["out_ref"]["sha"] = f"sha-{spec.name}"
            kw["out_ref"]["branch"] = f"devloop/{spec.name}-x"
        # ⛔ **必须写台账**：自动驾驶的进度是**从台账重算的**，不看自己的记录
        #    （那是它的一条硬规矩）。假的 `_run_unit` 不写账，调度器就永远
        #    以为一单都没完成，于是把第一单反复重派到看门狗停机为止——
        #    第一版这个假件正是那样，测出来 base 序列多了一项。
        from devloop import telemetry
        from devloop.dispatch import DispatchResult
        from devloop.models import Receipt
        telemetry.record(
            paths.telemetry,
            DispatchResult(spec.name,
                           Receipt(is_error=not ok, num_turns=1,
                                   usage={"input_tokens": 1, "output_tokens": 1},
                                   modelUsage={"m": {}}),
                           None, error=None if ok else "闸未通过"),
            model="m", price_key="__subscription__", tools=kw.get("tools", ""),
            gate_ok=ok)
        return ok, [f"  {'✓' if ok else '✗'} {spec.name}"]

    monkeypatch.setattr(cli, "_run_unit", fake)
    monkeypatch.setattr(cli.wt_mod, "resolve_base", lambda p: "BASE0")
    monkeypatch.setattr(cli, "fingerprint", lambda p: "fp")
    # ⚠️ 用**真**宪法，不给 preflight 塞个 None：cmd_autopilot 紧接着就读
    #    `con.source` 与 `con.unjudged`，塞 None 会在真正要测的逻辑之前就炸。
    cli.main(["constitution", "init", "--project", str(tmp_path)])
    cli.main(["constitution", "anchor", "--project", str(tmp_path)])
    #  ⚠️ `timeout_s` 必须显式给小值：本夹具的 `max_wall_min = 30`，而后端默认
    #     死线是 3000s（工人）+ 1800s（闸）= 80 分钟。⛔ 那样组合会被
    #     `wall_budget_verdict`（G-107）当场拒绝——它拒得对：一个装不下
    #     单单一单的墙钟上限是个假数。⭐ 这里的假派单本来就是瞬间返回的，
    #     给 60s 才是这个夹具的真实形状。
    monkeypatch.setattr(backends, "load", lambda *a, **k: backends.Registry(
        {"sub": backends.Backend(name="sub", kind="subscription", model="m",
                                 source="t", timeout_s=60)}, {}, "sub", "t"))

    cli.main(["autopilot", "--project", str(tmp_path), "--stage", "s"])
    return bases


def test_不接力时每单都从同一个起点(tmp_path, monkeypatch):
    """⛔ 这条钉的是**现状**——它是 G-59 的形状本身，不是 bug 的修复。
    关掉接力时行为必须一个字不变。"""
    bases = _run_stage(tmp_path, monkeypatch, chain=False, ok_seq=[True, True])
    assert bases == ["BASE0", "BASE0"], f"实得 {bases}"


def test_接力时后一单从前一单的产出起(tmp_path, monkeypatch):
    """⭐ G-59 的修复本身：第 2 单的工作副本要**含着**第 1 单的产出。"""
    bases = _run_stage(tmp_path, monkeypatch, chain=True, ok_seq=[True, True])
    assert bases == ["BASE0", "sha-a"], (
        f"第 2 单该从第 1 单的产出起，实得 {bases}")


def test_失败的单不许推进接力点(tmp_path, monkeypatch):
    """⛔ **只有通过验收的检查点才能当接力点。**

    一单没过闸就把 base 推到它头上，等于让后面所有单都建立在一份
    没验过的产出之上——而闸的全部意义就是别让没验过的东西往下传。
    """
    bases = _run_stage(tmp_path, monkeypatch, chain=True, ok_seq=[False, True])
    assert len(bases) >= 2, f"第一单失败后该重试，实得 {bases}"
    assert bases[0] == "BASE0"
    # ⚠️ 判据是「**失败的那一单之后**接力点没动」，不是「全程没动」——
    #    第一版写成了后者，而重试成功之后接力点本来就该往前走（那是对的）。
    assert bases[1] == "BASE0", f"失败的单把接力点推走了：{bases}"
