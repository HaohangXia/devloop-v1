"""通道自检。

**为什么需要它**：2026-07-26 的故障排查证明，「读配置」查不出问题，「跑命令」三条
就定了案。可执行的检查胜过可阅读的文档——本模块把 TROUBLESHOOTING.md 里那份
检查清单变成一条退出码。

触发时机：任何配置改动之后、换机器之后、以及「感觉哪里不对」的时候。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CLAUDE_HOME, SETTINGS_FILE, WORKER_FILE, ConfigError, load_worker_config

# 这些键一旦出现在全局 env，会强制桌面端的辅助通道使用第三方模型名，
# 使子代理／网页抓取／权限判定全部报「模型不存在」。实测撞过四次。
POISON_KEYS = {
    "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
}


@dataclass
class Check:
    ok: bool | None  # None = 无法在此处判定
    name: str
    detail: str


def _global_env() -> Check:
    if not SETTINGS_FILE.exists():
        return Check(None, "全局 env 洁净度", "settings.json 不存在")
    env = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("env", {})
    bad = sorted(POISON_KEYS & set(env))
    if bad:
        return Check(False, "全局 env 洁净度",
                     f"残留会打死辅助通道的键：{', '.join(bad)} —— 移到 worker-deepseek.json")
    return Check(True, "全局 env 洁净度", f"无污染键（保留 {len(env)} 项无关配置）")


def _worker_cfg() -> tuple[Check, object | None]:
    """自检要用的工人配置。

    ⛔ **必须取注册表里的默认后端，不是那份旧的 `worker-*.json`。**

    2026-07-29 实测：注册表默认后端已是 `subscription`（走订阅额度，$0），
    而这里读的是旧配置里的 deepseek（**真花美元**）。于是 `doctor --project`
    的「派单通道」实测：
      · 验的是一条**当前根本不用**的通道——绿了不说明默认后端能跑
      · 而且**真花了 $0.0082**，操作者以为自己只是做了次体检

    ⚠️ 这与刚修的「命令行拼两遍」是同一个病的两层：一层是命令怎么拼，
    一层是拿谁的配置去拼。两层都得指向真派单实际走的那条路。
    """
    from . import backends
    try:
        reg = backends.load()
        b = reg.resolve(reg.default)
        if b.can_subprocess:
            return (Check(True, "工人配置",
                          f"{b.name}（{b.kind}）· {b.model}"
                          + (f" @ {b.base_url}" if b.base_url else "")
                          + "  ← 注册表默认后端"),
                    b.worker_config())
        # 子代理形态起不了子进程，派单自检无从做起——如实说，⛔ 不许退回旧配置
        #    顶上：那会让自检报「通道正常」，而它验的是另一条路。
        return (Check(None, "工人配置",
                      f"默认后端 {b.name} 是 {b.kind}，起不了子进程，"
                      f"派单自检跳过（走交接协议，见 SPEC §5.7）"), None)
    except Exception:
        pass
    # 注册表读不出来才退回旧配置——⚠️ 并且必须**说出来**
    try:
        cfg = load_worker_config()
    except ConfigError as exc:
        return Check(False, "工人配置", str(exc).splitlines()[0]), None
    src = "独立文件" if str(WORKER_FILE) in cfg.source else "⚠️ 回退自 settings.json"
    return Check(False, "工人配置",
                 f"⚠️ 读不到后端注册表，回退到旧配置 {cfg.model} @ {cfg.base_url}"
                 f"（{src}）——⛔ 自检验的可能不是你实际在用的通道"), cfg


def _credentials(probe: bool = False, project: Path | None = None) -> list[Check]:
    """凭据自检。⛔ **不问 `claude auth status`——它会撒谎。**

    这个检查被自己的判据坑过两次，两次都是「拿间接读数替代真实行为」：

    1. 早先看 `.credentials.json` 的 **mtime** 猜过期 → 错：令牌可能在内存里刷新。
    2. 改成问 **`claude auth status`** → 也错：2026-07-29 同一时刻实测，
       它说 `loggedIn: true`，而 `claude -p` 报 **401 令牌已过期**。
       它只看凭据文件在不在，**不看过没过期**。
    3. 再改成读文件里的 `expiresAt` → **还是错**：同日 18:46 实测，
       显示「10 小时前过期」的令牌，子进程照跑不误——CLI 用 refreshToken
       自动续了期。据此拒派会拦下本来能跑通的活。

    ⭐ 结论：**读哪个字段都可能与实际不符，唯一可靠的判据是真起一次子进程。**
    所以这里给两条：便宜的排除法总是跑；真探针要 `--probe` 显式要（它花额度）。
    """
    from . import credentials as cred

    st = cred.check()
    out = [Check(st.ok, "订阅凭据", st.detail.splitlines()[0])]
    if not probe:
        out.append(Check(None, "凭据真探针",
                         "没跑（要花额度）。想要确定答案：devloop doctor --probe"))
        return out

    # ⚠️ 把 project 传下去，探针那一单才有地方留账（G-64）。
    ok, detail = cred.probe(project=project)
    out.append(Check(ok, "凭据真探针", detail))
    # ⚠️ 两者不一致要**明说**，别让人自己去对——不一致恰恰是最该看见的信号。
    if ok != st.ok:
        out.append(Check(None, "⚠️ 读数与实际不符",
                         f"排除法说 {'能跑' if st.ok else '跑不了'}，"
                         f"真探针说 {'能跑' if ok else '跑不了'}"
                         "——以探针为准，并去修 credentials.check() 的判据"))
    return out


def _binaries() -> list[Check]:
    out = []
    claude = os.environ.get("DEVLOOP_CLAUDE_BIN") or shutil.which("claude")
    out.append(Check(bool(claude), "claude 可执行文件",
                     claude or "PATH 中找不到 claude"))
    try:
        from .gates import find_bash
        out.append(Check(True, "Git Bash", find_bash()))
    except ConfigError as exc:
        out.append(Check(False, "Git Bash", str(exc).splitlines()[0]))
    return out


def _record_smoke(project: Path | None, cfg, raw: dict | None, rl) -> None:
    """把体检那一单记进台账。⛔ 失败也要记——钱是照花的。

    ⚠️ 单独一个函数、且整个包在 try 里：**记账失败绝不能让体检本身报错**。
    体检的用途是「告诉你哪里坏了」，它自己因为记账失败而红，等于帮倒忙。
    """
    if project is None:
        return
    try:
        from .config import ProjectPaths
        from .dispatch import DispatchResult
        from .models import Receipt
        from . import telemetry
        paths = ProjectPaths(project)
        receipt = Receipt(**raw) if raw else None
        telemetry.record(
            paths.telemetry,
            DispatchResult("doctor-smoke", receipt, None, rate_limit=rl,
                           error=None if raw else "体检探针：事件流里没有 result"),
            model=cfg.model, price_key=cfg.pricing_key(), tools="readonly",
            gate_ok=None,
            gate_detail="⚠️ devloop doctor 的通道探针——真花额度，不是干跑")
    except Exception:                                       # noqa: BLE001
        pass


def _dispatch_smoke(cfg, project: Path | None) -> Check:
    """真派一单最小任务——这是唯一能证明派单通道活着的方式。

    ⛔ **命令行必须复用 `dispatch.build_cmd`，不许在这里再拼一遍。**

    2026-07-29 交叉核对抓到：这里原本写死了 `--bare` + `--output-format json`，
    而真派单早已改成 `stream-json --verbose`，且订阅后端**绝不加 `--bare`**。
    于是这个自称「唯一能证明派单通道活着」的检查，验的是**另一条路**——
    它绿了不说明默认后端能跑，红了也不说明默认后端不能跑。

    ⚠️ 根因是同一件事写了两遍，两遍必然分叉。这个项目在 `_rebuild_argv`
    上栽过同一个坑，当时的结论是「必须有一条不变量测试把分叉钉死」。
    """
    if project is None:
        return Check(None, "派单通道", "未指定 --project，跳过实测")
    import subprocess

    from .dispatch import build_cmd
    from .quota import parse_stream
    exe = os.environ.get("DEVLOOP_CLAUDE_BIN") or "claude"
    env = {**os.environ, **cfg.env()}
    cmd = build_cmd(cfg, tools="readonly", max_turns=1,
                    prompt="只回复两个字：正常", exe=exe)
    try:
        p = subprocess.run(
            cmd, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, stdin=subprocess.DEVNULL,
        )
        r, _rl = parse_stream(p.stdout)
        # ⛔ **这一单真花了额度，必须留账。**
        #    ⚠️ 三条失控防线（已花多少 / 派了几次 / 有没有算不出成本的）
        #    全都只从台账读。体检花掉的窗口不记账 = 对它们完全隐形，
        #    于是夜跑会在一个已经被吃掉的 5 小时窗口上起跑。
        #    2026-07-30 之前这里一行账都不写，而它是「夜跑第 1 步」。
        _record_smoke(project, cfg, r, _rl)
        if r is None:
            return Check(False, "派单通道",
                         f"事件流里没有 result 事件；stderr：{p.stderr.strip()[:120]}")
    except Exception as exc:
        return Check(False, "派单通道", f"实测失败：{str(exc)[:120]}")
    if r.get("is_error"):
        return Check(False, "派单通道", str(r.get("result"))[:150])
    used = list(r.get("modelUsage", {}))
    if not any(cfg.model in m for m in used):
        return Check(False, "派单通道", f"模型不符：期望 {cfg.model}，实际 {used}")
    return Check(True, "派单通道",
                 f"${r.get('total_cost_usd', 0):.4f}，模型 {used}")


_GATES_REV_RE = re.compile(r"DEVLOOP-GATES-REV:\s*(\d+)")


def gates_rev_pair(project: Path) -> tuple[int | None, int | None]:
    """读出「项目装的闸版号」和「模板的闸版号」。读不出就给 None。

    ⭐ 抽出来是为了让**体检**和**每轮注入的钩子**共用同一处判据 ——
    ⛔ 两边各写一遍 = 修一边漏一边，那正是本仓反复吃亏的形状。
    """
    g = project / ".devloop" / "gates.sh"
    tpl = Path(__file__).resolve().parent.parent / "templates" / "gates.sh"
    m_prj = (_GATES_REV_RE.search(g.read_text(encoding="utf-8", errors="replace"))
             if g.exists() else None)
    m_tpl = (_GATES_REV_RE.search(tpl.read_text(encoding="utf-8", errors="replace"))
             if tpl.exists() else None)
    return (int(m_prj.group(1)) if m_prj else None,
            int(m_tpl.group(1)) if m_tpl else None)


def gates_template_drift(project: Path) -> Check:
    """装到项目上的 `gates.sh` 是不是还停在老版模板上（G-114）。

    ## ⛔ 为什么需要它

    2026-08-01 修掉了「一道闸验了 0 条却报 PASS」（G-88）——**修在模板里**。
    ⚠️ 而装到 eco-ob 的那一份**从来没跟着更新**：连 `void()` 函数都没有，
    「禁改清单」照旧报 PASS。⛔ 同一个假绿多活了三天，
    而计划的 `require_pass` **点名了那道闸**。

    ⭐ 最要命的不是脱节本身，是**没有任何东西会告诉你**。

    ## ⛔ 判据不能是「必须和模板一模一样」

    ⚠️ 项目本来就该改自己的 gates.sh——那是它自己的验收命令。
    ⭐ 所以判据落在**版本代号**上，不在内容上。

    ## ⛔ 「不知道」不许当成「没问题」

    老副本没有代号。⚠️ 那时正确的回答是「不知道它基于哪一版」（`ok=None`），
    **绝不是** `ok=True`——沉默正是让那个假绿多活三天的原因。
    """
    g = project / ".devloop" / "gates.sh"
    if not g.exists():
        return Check(None, "闸模板版本", "本项目还没有 .devloop/gates.sh")

    tpl = Path(__file__).resolve().parent.parent / "templates" / "gates.sh"
    m_tpl = _GATES_REV_RE.search(tpl.read_text(encoding="utf-8")) if tpl.exists() else None
    if not m_tpl:
        return Check(None, "闸模板版本", "⚠️ 模板自己没有版本代号，比不了")

    m_prj = _GATES_REV_RE.search(g.read_text(encoding="utf-8", errors="replace"))
    if not m_prj:
        return Check(None, "闸模板版本",
                     f"⚠️ **不知道**这份 gates.sh 基于哪一版模板（里面没有代号）。"
                     f"⛔ 「不知道」不等于「没问题」——模板现在是 "
                     f"rev {m_tpl.group(1)}，⭐ 请人工比一遍再把代号补上。"
                     f"　⚠️ G-114 就是这么漏掉的：一个假绿因此多活了三天。")

    a, b = int(m_prj.group(1)), int(m_tpl.group(1))
    if a == b:
        return Check(True, "闸模板版本", f"rev {a}，与模板同版")
    if a > b:
        #  ⛔⛔ 2026-08-11 补上的另一半。**这一支以前不存在** ——
        #  `if a >= b` 把「领先」和「同版」并成一句「与模板一致」，
        #  ⚠️ 实测对 eco-ob 打的是 `ok=True, "rev 7，与模板一致"`，**那句话是假的**
        #  （模板停在 rev 4，PREFLIGHT 体检模式模板 0 处 / eco-ob 7 处）。
        #
        #  ⭐ 而它假的那个方向，恰好就是这个工具最要命的失效：
        #     **在靶子项目上改进了闸，却没搬回模板** —— 下一个接闸的项目一分好处拿不到。
        #  ⛔ 这是本项目反复出现的那个形状的第 12 次：
        #     **同一件事有两条路。修好的永远是「有人盯着」的那条。**
        #     「靶子落后于模板」有人盯着（G-114），「靶子领先于模板」没人盯着。
        #
        #  ⚠️ 判据为什么落在这里：要让这个数归零，**必须真的去编辑 `templates/gates.sh`**。
        #     ⛔ 在任何文档里写多漂亮的字都改不动它。⭐ 而一旦搬完，它自己就闭嘴了。
        return Check(False, "闸模板版本",
                     f"⛔ 本项目的 gates.sh 是 **rev {a}**，模板还停在 **rev {b}**"
                     f"（**领先 {a - b} 版**）。⭐ 这说明改进闸的办法**卡在这个项目里没回流**"
                     f"到 `templates/gates.sh` —— ⚠️ 下一个接闸的项目一分钱好处拿不到。"
                     f"　⛔ 靶子项目是手段，工具仓才是目的。")
    return Check(False, "闸模板版本",
                 f"⛔ 本项目的 gates.sh 是 **rev {a}**，而模板已经到 **rev {b}**"
                 f"（落后 {b - a} 版）。⚠️ 模板改动多半是在修**假绿**"
                 f"——⭐ 去 `templates/gates.sh` 逐条比一遍，别只看行数。")


def run(project: Path | None = None, *, probe: bool = False) -> list[Check]:
    checks = [_global_env()]
    if project is not None:
        checks.append(gates_template_drift(project))
    cfg_check, cfg = _worker_cfg()
    checks.append(cfg_check)
    checks.extend(_credentials(probe, project))
    checks += _binaries()
    if cfg:
        checks.append(_dispatch_smoke(cfg, project))
    # 子代理通道只能在桌面端内部验证，Python 侧看不到
    # 子代理通道跑在桌面 App 进程内，Python 侧看不到，只能在会话里实测。
    # 2026-07-26 重启 App 后已验证通过（此前因 CLAUDE_CODE_SUBAGENT_MODEL
    # 指向第三方模型名而全数报「模型不存在」）。
    checks.append(Check(None, "桌面子代理通道",
                        "需在桌面会话内实测（Python 侧不可见）。"
                        "2026-07-26 移除全局 env 污染键并重启 App 后已验证通过"))
    return checks
