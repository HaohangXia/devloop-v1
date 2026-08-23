"""订阅凭据自检。

## ⛔ 这个模块被自己的判据坑过两次，两次是同一个病

**第一次**：用 `claude auth status` 判。2026-07-29 实测，同一时刻同一台机器——

```
claude auth status   →  {"loggedIn": true, "subscriptionType": "max"}
claude -p "回答一个字" →  401 OAuth access token has expired
```

`auth status` 只看凭据文件在不在，不看过没过期。于是改成读文件里的 `expiresAt`。

**第二次（就是改完之后那一版）**：读 `expiresAt` 判「过期就拒绝派单」。
2026-07-29 18:46 实测，令牌显示 **10 小时前就过期了**（08:22），然而——

```
claude -p "回答一个字：好"   →  is_error false，正常返回
```

跑完再看文件：`expiresAt` 从 08:22 跳到了次日 **02:46**。
⭐ **CLI 用 `refreshToken` 自动续期了**，全程不需要人。

于是那一版的守卫会拦下一批**本来能跑通**的活——比不检查更坏，
因为它以「凭据过期」的名义停机，而真实原因是我的判据错了。

## ⛔ 教训：读哪个字段都一样错

两次都是同一个病：**拿一个间接读数替代真实行为**。
换个字段读不解决问题，因为凭据文件里**根本没有 refreshToken 的过期时间**
（字段只有 accessToken / refreshToken / expiresAt / scopes /
subscriptionType / rateLimitTier）——续期成不成功，文件里判不出来。

所以本模块的定位改了：

  · `check()`  = **便宜的排除法**。只回答「肯定跑不了」的情况（没有凭据文件、
    文件坏了、访问令牌过期且**连 refreshToken 都没有**）。
    ⛔ 它**不**回答「一定能跑」——那要靠 `probe()` 或第一单本身。
  · `probe()`  = 真起一次子进程试。唯一可靠，但花额度，所以要显式调用。

⚠️ 「访问令牌过期」不再是拒派的理由。只要有 refreshToken 就照派——
CLI 会自己续。真续不上时，第一单会以 401 失败，那才是可信的信号。

## ⭐ 对无人值守的意义

一次登录只管 8 小时，但**每次派单都是新子进程、都会按需自动续期**，
所以 8 小时这条线**不再是跑一夜的障碍**。
⚠️ 前提是 refreshToken 本身没失效——两个月不用是会失效的（第一次那回就是）。
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CRED_FILE = Path.home() / ".claude" / ".credentials.json"

#  剩余不足这么久就提示一句。⚠️ 现在**只是提示**，不再据此拒派——
#  CLI 会自动续期，跨过这条线不影响干活。
SOON_SECONDS = 30 * 60

RELOGIN = "claude auth login --claudeai"


@dataclass(frozen=True)
class CredStatus:
    ok: bool                  # ⚠️ 语义是「**没有**已知的硬障碍」，不是「一定能跑」
    expiring_soon: bool       # 访问令牌快过期了（仅提示：CLI 会自己续）
    detail: str               # 给人看的一句话
    expires_at: float = 0.0   # unix 秒；0 = 读不出来
    will_refresh: bool = False  # 访问令牌已过期，但有 refreshToken → CLI 会自动续

    @property
    def remaining_min(self) -> float:
        return max(0.0, (self.expires_at - time.time()) / 60) if self.expires_at else 0.0


def check(path: Path | None = None) -> CredStatus:
    """便宜的排除法：只挑出**肯定跑不了**的情况。

    ⛔ `ok=True` **不保证**能跑通，只保证没有从文件里看得出来的硬障碍。
       想要真答案用 `probe()`，或者直接让第一单去撞。
    """
    f = path or CRED_FILE
    if not f.exists():
        return CredStatus(False, False,
                          f"没有凭据文件 {f}。跑一次 `{RELOGIN}` 登录。")
    try:
        oauth = json.loads(f.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
    except ValueError as exc:
        return CredStatus(False, False,
                          f"凭据文件不是合法 JSON（{exc}）。跑 `{RELOGIN}` 重登。")

    has_refresh = bool(oauth.get("refreshToken"))
    exp_ms = oauth.get("expiresAt")
    sub = oauth.get("subscriptionType", "?")

    if not exp_ms:
        # 判不出过期时间。有 refreshToken 就别拦——拦错的代价是停掉能跑的活。
        if has_refresh:
            return CredStatus(True, False,
                              "凭据文件里没有 expiresAt，判不了新旧；"
                              "但有 refreshToken，照派——真不行第一单会报 401。",
                              will_refresh=True)
        return CredStatus(False, False,
                          f"凭据文件里既没有 expiresAt 也没有 refreshToken。跑 `{RELOGIN}` 重登。")

    exp = float(exp_ms) / 1000
    left = exp - time.time()

    if left <= 0:
        mins = int(-left / 60)
        when = time.strftime("%m-%d %H:%M", time.localtime(exp))
        if has_refresh:
            # ⭐ 实测（2026-07-29 18:46）：过期 10 小时的令牌，子进程照跑不误，
            #    跑完 expiresAt 自动往后跳了 8 小时。**这不是故障，不许拦。**
            return CredStatus(
                True, False,
                f"访问令牌已过期 {mins // 60} 小时（{when}），但有 refreshToken"
                f"——CLI 会自己续，照派。",
                exp, will_refresh=True)
        return CredStatus(
            False, False,
            f"⛔ 访问令牌已过期 {mins // 1440} 天 {(mins % 1440) // 60} 小时（{when}），"
            f"**且没有 refreshToken**，续不了。\n"
            f"   ⚠️ `claude auth status` 这时仍会说「已登录」——它只看文件在不在。别信它。\n"
            f"   跑这条重登：`{RELOGIN}`",
            exp)

    soon = left < SOON_SECONDS
    return CredStatus(
        True, soon,
        (f"访问令牌还有 {left / 60:.0f} 分钟过期（{sub}）"
         f"——不影响派单，CLI 会自己续。"
         if soon else
         f"订阅令牌有效，还有 {left / 3600:.1f} 小时（{sub}）"),
        exp)


def _mk_result(name, receipt, rl):
    """造一个台账要的结果对象。

    ⚠️ 单独一个函数、且在函数体内 import：`devloop.dispatch` 会 import
    `devloop.quota`，而本模块被 `doctor` 在更早的时刻 import——放到模块顶层
    会绕出一个循环 import。
    """
    from .dispatch import DispatchResult
    return DispatchResult(name, receipt, None, rate_limit=rl)


def probe(timeout_s: int = 120, claude_bin: str | None = None,
          project: "Path | None" = None) -> tuple[bool, str]:
    """**真起一次子进程**问一个字，看凭据到底能不能用。

    ⛔ 这是唯一可靠的判据——`check()` 读文件，读什么字段都可能与实际不符
    （已经栽过两次，见模块头）。

    ⚠️ **它花额度**：实测一次最小调用会产生七万级的 cache_creation token。
    所以它不进派单前置检查，只给 `doctor` 这种「我明确要体检」的场合用。

    返回 (能不能用, 一句人话)。
    """
    import os

    exe = claude_bin or os.environ.get("DEVLOOP_CLAUDE_BIN") or "claude"
    # ⛔ 不加 --bare：那会跳过 OAuth 去读环境变量里的 key，测的就不是订阅了。
    # ⛔ 也不注入任何 ANTHROPIC_*，理由同上（见 models.WorkerConfig.env）。
    cmd = [exe, "-p", "回答一个字：好", "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"探针超时（{timeout_s}s）——判不了，别当成凭据坏了"
    except FileNotFoundError:
        return False, "找不到 claude 可执行文件，检查 PATH"

    try:
        r = json.loads(proc.stdout)
    except ValueError:
        head = (proc.stdout or proc.stderr).strip()[:200]
        return False, f"探针回执不是合法 JSON：{head}"

    # ⛔ **这一单真花了额度，必须留账。** 三条失控防线全都只从台账读，
    #    探针花掉的窗口不记账 = 对它们完全隐形，夜跑会在一个已被吃掉的窗口上起跑。
    #    ⚠️ 2026-07-30 第一版只给 `doctor._dispatch_smoke` 补了留账，**漏了这条路**
    #    ——BACKLOG G-64 当场把它指了出来。
    #    ⚠️ 整个包在 try 里：记账失败绝不能让探针本身报错（那是帮倒忙）。
    if project is not None:
        try:
            from .config import ProjectPaths
            from . import quota, telemetry
            from .models import Receipt
            _res = _mk_result("doctor-probe", Receipt(**r),
                              quota.parse_stream(proc.stdout)[1])
            telemetry.record(
                ProjectPaths(project).telemetry, _res,
                model=str((list(r.get("modelUsage") or []) or ["?"])[0]),
                price_key="__subscription__", tools="readonly", gate_ok=None,
                gate_detail="⚠️ devloop doctor --probe 的凭据探针——真花额度")
        except Exception:                                   # noqa: BLE001
            pass

    if r.get("is_error"):
        return False, f"探针失败：{str(r.get('result') or r.get('subtype'))[:200]}"
    return True, f"探针通过（{r.get('num_turns', '?')} 轮，模型 {list(r.get('modelUsage') or ['?'])}）"
