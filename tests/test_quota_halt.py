"""撞额度要**真的**停下整批——不是「有个 should_halt 属性」就算数。

## ⛔ 为什么单独一个文件测这件事

`test_quota.py` 测的是判据本身（认得出撞没撞、该等到几点）。
那些全绿了，**也完全不能说明批次会停**——今天已经在同一形状上栽过两次：

  · 成本函数对了，但派单路径没把价目 key 传下去 → 台账记成「未知」
  · 凭据判据对了，但判的是个与实际不符的字段 → 会拦下能跑通的活

所以这里从 `main(["dispatch", ...])` 整条路径进去，只把**真派单**换成假回执
（省钱不省路径），看批次到底停没停、退出码是几、剩下几单没派。

## 判据

  · 撞了额度 → 后面的单**一单都不许再派**
  · 退出码 **3（还没跑完）**，不是 1（活没干好）
  · 台账里那一单的 failure_class 是 `ratelimit`，⛔ 不是 `model`
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from devloop import quota
from devloop.dispatch import DispatchResult
from devloop.models import Receipt


def _receipt() -> Receipt:
    return Receipt(is_error=False, num_turns=1, total_cost_usd=0.0,
                   usage={"input_tokens": 10, "output_tokens": 5},
                   modelUsage={"claude-opus-4-7": {}})


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path: Path, monkeypatch):
    """CLI 仍执行真实凭据检查，但输入只来自临时合成文件。

    quota 用例替换了 dispatch_one，却曾漏隔离此前的凭据检查，因此本机靠
    作者的登录状态碰巧通过，干净 CI 在到达停批逻辑前就退出。此处不 mock
    check()、不接触真实 OAuth 文件，且下面另有缺凭据的拒派红检。
    """
    from devloop import credentials
    credential_file = tmp_path / "synthetic-credentials.json"
    credential_file.write_text(json.dumps({"claudeAiOauth": {
        "expiresAt": int((time.time() + 3600) * 1000),
        "subscriptionType": "test-fixture",
    }}), encoding="utf-8")
    monkeypatch.setattr(credentials, "CRED_FILE", credential_file)


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    (tmp_path / ".devloop" / "tasks").mkdir(parents=True)
    (tmp_path / ".devloop" / "rules-digest.md").write_text("规则", encoding="utf-8")
    for i in range(4):
        (tmp_path / ".devloop" / "tasks" / f"u{i}.md").write_text(
            f"# 角色\n工人\n\n# 任务\n第 {i} 单\n\n# 禁令\n无\n", encoding="utf-8")
    return tmp_path


def _run(proj: Path, monkeypatch, dispatcher, extra=()) -> tuple[int, list[str]]:
    """跑真 CLI，只换掉 dispatch_one。返回 (退出码, 派过的任务名)。"""
    from devloop import backends, cli

    seen: list[str] = []

    def fake(task, paths, cfg, **kw):
        seen.append(task.name)
        return dispatcher(task, len(seen))

    monkeypatch.setattr(cli, "dispatch_one", fake)
    monkeypatch.setattr(backends, "load", lambda *a, **k: backends.Registry(
        {"sub": backends.Backend(name="sub", kind="subscription",
                                 model="claude-opus-4-7",
                                 price_key="__subscription__", source="test")},
        {}, "sub", "test"))
    code = cli.main(["dispatch", "--project", str(proj),
                     "--task-dir", str(proj / ".devloop" / "tasks"),
                     "--tools", "readonly", *extra])
    return code, seen


def test_缺少凭据时必须在派单前拒绝(proj, monkeypatch):
    """隔离测试输入不能架空真实凭据守卫：缺文件必须拒派且不碰 worker。"""
    from devloop import credentials
    monkeypatch.setattr(credentials, "CRED_FILE", proj / "missing-credentials.json")
    code, seen = _run(
        proj, monkeypatch,
        lambda task, n: DispatchResult(task.name, _receipt(), None),
    )
    assert code == 2
    assert seen == [], "没有凭据不应到达被 mock 的工作进程"


def test_撞了额度之后一单都不许再派(proj, monkeypatch):
    """⛔ **这条是全部工作的落点。**

    实测形态（侦察确认）：串行主循环里没有 break、没有状态标志——撞了额度之后
    剩下的单会**全部继续派完**，每一单都收回同一个拒绝。台账里那一串失败
    看起来像「模型突然不行了」，而真因是额度用完了。
    """
    blocked = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")

    def dispatcher(task, n):
        return DispatchResult(task.name, _receipt(), None,
                              rate_limit=blocked if n == 2 else None)

    #  ⛔ 显式串行。⚠️ 2026-08-01 之前这里不给 `--parallel`，吃的是默认值 1；
    #     之后只读的默认改成 4（judgement 见 devloop/fanout.py），
    #     于是撞墙时**已有 4 单在飞**，这条断言从 2 变成 4。
    #     ⛔ 不许为了让它绿就把断言放宽——那正是「抬及格线」。
    #     串行下的这条性质仍然成立且仍然要守；并发下的溢出由下一条单独测。
    code, seen = _run(proj, monkeypatch, dispatcher, extra=("--parallel", "1"))
    assert len(seen) == 2, f"第 2 单就撞墙了，不该继续派。实际派了 {seen}"
    assert code == 3, f"额度耗尽是「还没跑完」(3)，不是「活没干好」(1)。实得 {code}"


def test_并发时撞墙最多溢出一波(proj, monkeypatch):
    """⚠️ 并发下**不可能**做到「第 2 单就停」——同一波里的单已经在飞了，
    收不回来。这条把**代价的上界**钉死：溢出 ≤ 并发数，⛔ 不许溢出到下一波。

    ⭐ 停批保的是**诊断清晰度**（代码注释原文：「那一串看起来像
    『模型突然不行了』，而真因是额度用完了」），不是省额度。
    一波 4 条失败仍然读得出是同一堵墙；⛔ 而 N 波就读不出了。
    """
    blocked = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")

    def dispatcher(task, n):
        return DispatchResult(task.name, _receipt(), None,
                              rate_limit=blocked if n == 2 else None)

    code, seen = _run(proj, monkeypatch, dispatcher, extra=("--parallel", "2"))
    assert len(seen) == 2, f"⛔ 溢出到了第二波：{seen}"
    assert code == 3


def test_没撞额度时四单照常跑完(proj, monkeypatch):
    """⚠️ 防回归：别为了会停就把正常批次也停了。"""
    allowed = quota.RateLimit(status="allowed", resets_at=0, kind="five_hour")
    code, seen = _run(proj, monkeypatch,
                      lambda task, n: DispatchResult(task.name, _receipt(), None,
                                                     rate_limit=allowed))
    assert len(seen) == 4 and code == 0


def test_警告不停机(proj, monkeypatch):
    """⚠️ `allowed_warning` 不是停机理由——停早了等于把剩下的额度白扔。"""
    warn = quota.RateLimit(status="allowed_warning", resets_at=0,
                           kind="five_hour", utilization=0.93)
    code, seen = _run(proj, monkeypatch,
                      lambda task, n: DispatchResult(task.name, _receipt(), None,
                                                     rate_limit=warn))
    assert len(seen) == 4, "警告不该叫停"
    assert code == 0


def test_并行时排队中的单也要停(proj, monkeypatch):
    """⛔ 并行比串行更容易漏：原实现把**全部**单元一次性 submit 进池，
    撞了额度也停不掉排队中的——它们会一个接一个撞同一堵墙。

    ⚠️ 判据要留余量：同一波里已经在飞的单**停不掉也不该停**（钱已经花了）。
    要守的是「不再开新的波」。并行度 2、共 4 单 → 最多跑完第一波 2 单。
    """
    blocked = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")

    def dispatcher(task, n):
        return DispatchResult(task.name, _receipt(), None,
                              rate_limit=blocked if n == 1 else None)

    code, seen = _run(proj, monkeypatch, dispatcher, extra=("--parallel", "2"))
    assert len(seen) <= 2, f"第一波就撞墙了，不该再开新波。实际派了 {seen}"
    assert code == 3


def test_台账把撞额度归成ratelimit而不是model(proj, monkeypatch):
    """⛔ 归因决定「便宜模型够不够用」的答案。

    ⚠️ `failure_class` 原有四类（model / taskspec / tooling / gate）里
    **没有一个对**，而 `model` 是最顺手也最错的选择——它会把额度问题
    算进模型的账上，直接污染成本实验的结论。
    """
    blocked = quota.RateLimit(status="rejected", resets_at=1785332400,
                              kind="five_hour")
    _run(proj, monkeypatch,
         lambda task, n: DispatchResult(task.name, _receipt(), None,
                                        rate_limit=blocked))
    rows = [json.loads(l) for l in
            (proj / ".devloop" / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows, "撞了额度也要留账——钱/额度已经花掉了"
    r = rows[-1]
    assert r["failure_class"] == "ratelimit", \
        f"⛔ 撞额度不是模型不行。实得 {r['failure_class']}"
    assert r["quota_status"] == "rejected"
    assert r["quota_resets_at"] == 1785332400, "恢复时刻要留下来，否则事后判不了该等多久"


def test_不加开关就不许自己睡几个小时(proj, monkeypatch):
    """⛔ 自动续跑必须**显式要**。默认睡几小时会很突然，而且睡的时候
    这个进程一直占着——那该是操作者的决定，不是工具替他做。"""
    from devloop import cli
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    blocked = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")
    _run(proj, monkeypatch,
         lambda task, n: DispatchResult(task.name, _receipt(), None,
                                        rate_limit=blocked))
    assert not slept, f"没加 --wait-for-reset 就不该睡，实际睡了 {slept}"


def test_加了开关会等到恢复时刻再接着派(proj, monkeypatch):
    """⭐ 用户要的就是这个：撞了就停，到点自己接着干。

    ⚠️ 但等待时长**用 API 给的精确时刻算**，不是猜 5 小时。
    """
    import time as _t
    from devloop import cli
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))

    target = _t.time() + 1800
    blocked = quota.RateLimit(status="rejected", resets_at=target, kind="five_hour")

    def dispatcher(task, n):
        return DispatchResult(task.name, _receipt(), None,
                              rate_limit=blocked if n == 1 else None)

    code, seen = _run(proj, monkeypatch, dispatcher, extra=("--wait-for-reset",))
    assert len(slept) == 1, f"该睡一次，实际 {slept}"
    assert 1800 <= slept[0] <= 1800 + quota.RESET_MARGIN_S + 5, \
        f"要按 resetsAt 算，不是猜 5 小时。实得 {slept[0]}s"
    assert len(seen) == 4, f"睡醒要把剩下的派完，实际只派了 {seen}"
    assert code == 0


def test_周上限拿不到恢复时刻时不许自动等(proj, monkeypatch):
    """⛔ 官方文档明写周上限是**固定时间**重置。拿 5 小时去估会一路撞墙：
    每次醒来再撞一次，连撞十几个小时，而每次撞都真花额度。那种情况交给人。"""
    from devloop import cli
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    blocked = quota.RateLimit(status="rejected", resets_at=0, kind="seven_day")
    code, _ = _run(proj, monkeypatch,
                   lambda task, n: DispatchResult(task.name, _receipt(), None,
                                                  rate_limit=blocked),
                   extra=("--wait-for-reset",))
    assert not slept, "没有精确恢复时刻的周上限不许自动等"
    assert code == 3


# ══ ⛔ 自动驾驶路径：无人值守恰恰是最需要这些防线的场合 ═══════════

def test_自动驾驶必须把停批信号和T5宪法都接上():
    """⛔ **这条是 2026-07-29 独立审计抓到的最严重一条。**

    `cmd_dispatch` 调 `_run_unit` 时传了 `before=` / `ws_before=` / `halt=`，
    而 `cmd_autopilot` 只传了 `con=` / `base=`——于是在自动驾驶模式下：

      · 宪法 **T5 三道检查**（既有引用 / 受保护文件 / 活工作区）恒不执行
      · **撞额度停整批**恒不触发，会继续一单一单撞同一堵墙

    ⚠️ 而自动驾驶正是**无人值守**那条路——今天做的额度停批，恰好在唯一
    需要它的模式上是死的。手动派单时人在旁边看着，反而不那么要紧。

    ⛔ 判据落在**源码的调用点**上而不是行为上：这两条分支要真触发，
    得撞真额度或真违宪，那是不能拿来做测试的。所以这里直接读源码，
    确认两个调用点传的关键字参数**没有分叉**。
    """
    import ast
    import inspect
    from devloop import cli

    src = inspect.getsource(cli)
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_run_unit"]
    assert len(calls) >= 2, f"应当有派单与自动驾驶两个调用点，实得 {len(calls)}"

    需要的 = {"con", "base", "before", "ws_before", "halt"}
    for c in calls:
        got = {k.arg for k in c.keywords}
        缺 = 需要的 - got
        assert not 缺, (
            f"⛔ `_run_unit` 有个调用点少传了 {sorted(缺)}——"
            "少哪个哪道防线就恒不触发。两个调用点必须一致。")


def test_自动驾驶传给T5的必须是快照不是计数():
    """⛔ **2026-07-29 首次真跑当场炸的那个 bug。**

    我把宪法快照命名成 `before` 放在循环外，而循环体里**早就有**一句
    `before = len(prog.done_ok)`（看门狗用的完成计数）。第一轮就把快照
    覆盖成了 int，于是 `before=before` 把一个整数传进 T5：

        AttributeError: 'int' object has no attribute 'refs'

    ⚠️ `--dry-run` 走不到这段；已有单测也抓不到——它们**直接调 `_run_unit`**，
    绕过了发生覆盖的那个作用域。只有真跑会炸，而它真的炸了两次
    （两单产出都是好的、闸全绿，纯粹是编排方自己摔的）。

    ⛔ 判据落在**源码的名字**上：只要循环外那个快照还叫 `before`，
    就随时可能被再覆盖一次。
    """
    import ast
    import inspect
    from devloop import cli

    tree = ast.parse(inspect.getsource(cli))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_autopilot")

    # 循环外赋值给谁 / 循环内赋值给谁
    loops = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
    in_loop = {t.id for lp in loops for n in ast.walk(lp)
               if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
    snaps = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
             and isinstance(n.value, ast.IfExp)
             and "snapshot" in ast.dump(n.value)
             for t in n.targets if isinstance(t, ast.Name)}

    assert snaps, "没找到宪法快照的赋值——接线可能又被拆了"
    撞车 = snaps & in_loop
    assert not 撞车, (
        f"⛔ 宪法快照的变量名 {sorted(撞车)} 在循环体里被重新赋值了——"
        "第一轮之后传给 T5 的就不是快照。真跑会报 "
        "'int' object has no attribute 'refs'。")


def test_派单路径传下去的必须是真快照而不是None(tmp_path, monkeypatch):
    """⛔ **2026-07-30 审计抓到：我自己引入的回归，而两条守卫它的测试都绿。**

    `cmd_dispatch` 在宪法前置里采了 `ws_before = workspace_state(...)`，
    然后我为了修一个 NameError，在**后面**又写了一句 `ws_before = None`
    ——把快照覆盖了。于是「活工作区」这道宪法检查在手动派单路径上恒不执行
    （`_run_unit` 判的是 `if ws_before is not None`）。

    ⚠️ 原有两条测试为什么抓不到：
    · `test_wiring.py` 那条**自己**传 `ws_before=`，测的是 `_run_unit` 内部
    · `test_自动驾驶必须把停批信号和T5宪法都接上` 只用 AST 检查**关键字名在不在**

    ⛔ 两条都是「判据的维度错了」：它们验「传没传」，不验「传的是不是真快照」。
    这一条落在**值**上——从真 CLI 进去，看 `_run_unit` 实际收到了什么。
    """
    import subprocess
    from devloop import backends, cli, constitution
    from devloop.models import Receipt
    from devloop.dispatch import DispatchResult

    for cmd in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-q", "--allow-empty", "-m", "b"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".devloop" / "tasks").mkdir(parents=True)
    (tmp_path / ".devloop" / "rules-digest.md").write_text("x", encoding="utf-8")
    (tmp_path / ".devloop" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                                    encoding="utf-8")
    (tmp_path / ".devloop" / "tasks" / "u.md").write_text(
        "# 角色\n工人\n\n# 任务\n干活\n\n# 禁令\n无\n", encoding="utf-8")
    cli.main(["constitution", "init", "--project", str(tmp_path)])
    cli.main(["constitution", "anchor", "--project", str(tmp_path)])

    seen: dict = {}
    real = cli._run_unit

    def spy(spec, paths, cfg, **kw):
        seen.update(kw)
        return True, ["  ✓ u"]

    monkeypatch.setattr(cli, "_run_unit", spy)
    monkeypatch.setattr(cli, "dispatch_one",
                        lambda *a, **k: DispatchResult("u", Receipt(), None))
    monkeypatch.setattr(backends, "load", lambda *a, **k: backends.Registry(
        {"s": backends.Backend(name="s", kind="subscription", model="m",
                               source="t")}, {}, "s", "t"))

    cli.main(["dispatch", "--project", str(tmp_path),
              "--task", str(tmp_path / ".devloop" / "tasks" / "u.md"),
              "--tools", "readonly"])

    assert seen.get("ws_before") is not None, (
        "⛔ 传下去的 ws_before 是 None——活工作区那道宪法检查恒不执行。"
        "⚠️ 别只检查「关键字传没传」，要检查**传的值**。")
    assert hasattr(seen["ws_before"], "__len__") or hasattr(seen["ws_before"], "files"), \
        f"ws_before 该是个快照对象，实得 {type(seen['ws_before'])}"


def test_额度在最后一波撞上也要报3(proj, monkeypatch):
    """⛔ 2026-08-01 发现的真 bug（改动之前就在，被默认串行掩盖着）。

    退出码原来判的是 `todo`（还剩几单没派），而不是 `halt.tripped`。
    于是**额度在最后一波撞上时** todo 已空 → 退出码 0 或 1
    ——**「撞了额度」被报成了成功**，而上游会据此判定「这批活干完了」。

    ⚠️ 串行默认下够不着这个洞（撞墙必然留下未派的单）。
    只读默认改成并发（devloop/fanout.py）之后才暴露出来。**判据的维度一直是错的。**
    """
    blocked = quota.RateLimit(status="rejected", resets_at=0, kind="five_hour")
    #  并发 4 = 4 单一波跑完，撞墙时 todo 已空
    code, seen = _run(proj, monkeypatch,
                      lambda task, n: DispatchResult(task.name, _receipt(), None,
                                                     rate_limit=blocked),
                      extra=("--parallel", "4"))
    assert len(seen) == 4, seen
    assert code == 3, f"⛔ 撞了额度却报 {code}——上游会以为这批活干完了"
