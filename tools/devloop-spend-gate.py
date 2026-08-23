#!/usr/bin/env python
"""PreToolUse(Bash) 钩子：没有令牌，花额度的命令**物理跑不起来**。

## ⛔ 为什么光有「注入指令」不够

`devloop-mode-hook.py` 会往模型上下文注入一条明确指令（「本轮只许 dispatch」）。
但那仍然是**提示**——它依赖 Claude 遵守。而用户否掉的正是「靠 Claude」这一环，
理由很硬：2026-07-29 当天 Claude 在这个项目上判断错了四次。

所以这一道用 `permissionDecision: "deny"` **物理否掉**命令。
判据是**字符串匹配 + 读一个文件**，⛔ 零语义判断。

## 放行规则

| 命令 | 要什么 |
|---|---|
| `--dry-run` 的一切 | 永远放行（⭐ 鼓励先演练） |
| `doctor` / `stats` / `prune` / `backends` / `constitution` / `status` / `halt` | 永远放行（不花额度） |
| `dispatch`（真跑） | 令牌 mode ∈ {B, C}，且没过期 |
| `autopilot`（真跑） | 令牌 mode == C **且 approved**，且没过期 |

## ⛔ 一条防自我授权的规则

Claude 有 Bash，理论上能自己写令牌文件把门打开。所以**任何碰状态目录的
Bash 命令一律 deny**。

⚠️ 诚实说：这是很高的成本，**不是绝对不可能**（换个写法绕开字符串匹配是可想象的）。
它挡的是「顺手就把门打开了」，不是蓄意规避。⛔ 别把它当密封舱宣传。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

STATE = Path.home() / ".claude" / "state" / "devloop-mode"
B_TTL = 10 * 60
C_TTL = 30 * 60

#  ⚠️ 只认真正花额度的两个子命令。其余 devloop 子命令都不花钱，别拦——
#     拦了只会让人觉得这套东西碍事，然后想办法关掉它。
_DISPATCH = re.compile(r"devloop[.\s]*cli\s+dispatch\b|devloop\s+dispatch\b")
_AUTOPILOT = re.compile(r"devloop[.\s]*cli\s+autopilot\b|devloop\s+autopilot\b")
#  ⛔ `doctor` 带 `--project` 或 `--probe` 时**真派一单**
#     （`doctor._dispatch_smoke` / `credentials.probe`），走默认后端 = 订阅额度。
#     ⚠️ 2026-07-30 之前它被闸和文档双双写成「不花钱」，而它恰恰是
#     「夜跑标准流程第 1 步」——于是每次体检都在悄悄吃掉当前那个 5 小时窗口，
#     而且**一行台账都不留**（三条失控防线全都只从台账读，看不见它）。
_DOCTOR_SPEND = re.compile(
    r"(?:devloop[.\s]*cli\s+doctor\b|devloop\s+doctor\b)(?=.*(?:--project|--probe))")
#  ⚠️ `--dry-run` 之外，这几个也是**不花一分钱**的形态，一并放行：
#  · `--help` / `-h` —— 只打印用法。⛔ 拦它纯属添堵，而添堵的代价是
#    人会想办法把整套关掉（这条在「别把工具自己锁死」上已经栽过一次）。
#  ⚠️ 这是一张**枚举表**，而不是一条规则——⛔ 每加一个「只打印就退出」的开关，
#     这里就得跟着加一条，否则那个免费命令会被拦下来（2026-08-01 实测撞到两次：
#     `--why-parallel` 加上当天就被拦，而它是 print + return 0）。
#     判据没法从命令文本推出「这个开关会不会花钱」，所以只能枚举。
#     ⭐ 代价是维护负担；收益是**误拦的方向永远偏严**（漏加只会多拦，不会放过真跑）。
_FREE = re.compile(r"--dry-run\b|--help\b|--why-parallel\b|(?<![\w-])-h(?![\w-])")
_DRYRUN = _FREE
#  把复合命令切成段：`&&` `||` `;` `|` 换行 都是分隔符。
#  ⚠️ 这不是完整的 shell 解析（引号里的分隔符会被误切），但方向是**偏严**——
#     多切几段只会让更多段被要求带 --dry-run，⛔ 不会放过真跑。
#  ⭐⭐ 2026-08-12 · 触发代号被当成 shell 命令执行（故障 13 的**第二种形态**）。
#
#  ⛔ 第一种（2026-07-30）：`!` 是 CLI 的内置 shell 模式前缀，在**输入分类层**就被截走。
#  ⛔ 第二种（今天）：`%task ...` 被写进了一个 ```bash 代码块 —— 那种块**带「运行」按钮**，
#     用户一点就送进 PowerShell，于是 `%task : The term '%task' is not recognized...`。
#
#  ⚠️ 两种的**失效完全相同**：钩子从没收到那条消息，钱一分没花，活一件没派，
#     而用户看到的是一句 command not found —— **看上去像工具坏了**。
#
#  ⭐ 判据落在能直接量的东西上：**一条 Bash 命令的开头出现了触发代号**。
#     代号只在聊天消息里有意义，⛔ 出现在 shell 里**永远**是搞错了，没有例外。
#  ⚠️ 半角 % 与全角 ％ 都要认（故障 13 的学费：同一个字符两种宽度，肉眼看不出来）。
_TRIGGER_IN_SHELL = re.compile(r"^\s*[%％](task|auto|go|stop|mode)\b")

_SPLIT = re.compile(r"&&|\|\||[;\n|]")
#  shell 注释：行首的 `#`，或空白之后的 `#`，一直到行尾。
_COMMENT = re.compile(r"(?m)(?:^|\s)#.*$")
#  ⛔ 防自我授权：碰**状态目录**的一律拦下。
#  ⚠️ 判据必须锚在**路径**上，不能只匹配 `devloop-mode` 这个词——
#     第一版就是那样写的，结果把脚本自己的文件名 `devloop-mode-hook.py`
#     也匹配了，于是连读一下、跑一下自检都被 deny。装上去第一条命令就绊住了我。
#     ⛔ 「宁可拦错也不放过」在这里是错的：把工具本身锁死，人只会想办法关掉整套。
_TOUCH_STATE = re.compile(r"state[/\\]+devloop-mode|\.claude[/\\]+state\b")


def _deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }, ensure_ascii=False))
    return 0


def _token(sid: str) -> dict | None:
    """**本会话**的令牌。⚠️ 过期的当没有。

    ⛔ 必须按 `session_id` 取，不能 glob 整个目录。
    第一版就是 glob 全目录取第一个命中的——那样 **A 会话打的标记会放行
    B 会话的命令**。两个会话同时开着（这在本机是常态）就漏了。
    ⚠️ 已核实 `PreToolUse` 的 stdin 带 `session_id`（CLI 里所有钩子的公共字段
    都来自同一个 `D1()`，里面第一项就是它），所以拿得到。
    """
    f = STATE / f"{sid}.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    #  ⛔ 2026-08-01 修：模式 C 的过期从 **`granted_at`（按 `%go` 那一刻）** 起算，
    #     不是 `armed_at`（`%auto` 那一刻）。
    #
    #  ⚠️ 原来两件事共用一个时钟：
    #       ① 「待批准请求还有效吗」—— 从 `%auto` 算，⭐ 它什么都没授权，可以宽松
    #       ② 「批准之后还能用多久」—— 从 `%go` 算，⛔ 必须收紧
    #     而钩子把 `armed_at` 从 pending **抄**进令牌，闸又拿它判过期，于是
    #     **编排方演练得越认真，用户批准的有效期越短**。实测撞到：
    #     一次认真的 `--dry-run`（写任务书 + 跑闸）把 30 分钟窗口烧光，
    #     用户随后打的 `%go` 当场失效。**判据的维度错了。**
    #
    #  ⛔ 这**不是放宽**：两种设计下「批准后可用多久」的上界都是 30 分钟。
    #     变的只是「慢慢想」不再被惩罚。
    #  ⚠️ 唯一变松的一处：慢批准能多拿几分钟可用期。对价是钩子那边
    #     `%go` 的回显现在会**印出批准的是什么**——把「随手打 %go」
    #     从看不见变成看得见。⛔ 只做这一半不做那一半，是净变松。
    #
    #  ⚠️ 回退兼容：老令牌没有 `granted_at`，退回 `armed_at`。
    #     ⛔ 不能退回 0——那会让老令牌**立刻失效**，而它们是用户真按过的。
    if d.get("mode") == "C":
        stamp = float(d.get("granted_at") or d.get("armed_at", 0))
        return d if time.time() - stamp < C_TTL else None
    return d if time.time() - float(d.get("armed_at", 0)) < B_TTL else None


def main() -> int:
    try:
        inp = json.load(sys.stdin)
    except Exception:
        return 0                       # ⛔ 钩子坏了不许挡住一切
    if inp.get("tool_name") != "Bash":
        return 0
    sid = str(inp.get("session_id") or "nosession")
    cmd = str((inp.get("tool_input") or {}).get("command") or "")

    #  ⭐ 最靠前：它压根不是「花不花钱」的问题，是「这条根本不该进 shell」。
    m_trig = _TRIGGER_IN_SHELL.match(cmd)
    if m_trig:
        code = m_trig.group(1)
        return _deny(
            f"⛔ `%{code}` **不是 shell 命令，是一条聊天消息**——送进终端只会得到"
            f" command not found，⚠️ 而钩子压根收不到，钱没花、活没派。\n"
            f"   ⭐ 正确做法：把这一行**直接打进对话框**（⛔ 不是终端），"
            f"而且必须在**整条消息的第一行行首**。\n"
            f"   ⚠️ 判别：真生效时会有一行回显（〔派一单〕/〔无人值守·已唤起〕…）。"
            f"**没有回显 = 没生效。**\n"
            f"   ⛔ 附带一条给模型的纪律：**永远不许把 `%…` 写进 ```bash 代码块**"
            f"——那种块带「运行」按钮，等于替用户按下了错误的那个键。"
            f"（TROUBLESHOOTING 故障 13）")

    if _TOUCH_STATE.search(cmd):
        return _deny(
            "⛔ 不许碰 devloop 模式状态目录——那是用户按下的开关，"
            "自己去改等于自我授权。要放行请让用户打代号（`%task` / `%auto`+`%go`）。")

    # ⛔ **必须分段判，不能拿整条命令去搜 `--dry-run`。**
    #    第一版是 `if _DRYRUN.search(cmd): return 0`，判据落在整条 Bash 命令上，
    #    于是这些全部被放行：
    #        autopilot --dry-run && autopilot          ← 后半截是真跑
    #        autopilot            # 先 --dry-run 看看   ← 注释里的字也算
    #        echo --dry-run; dispatch ...
    #    ⚠️ 而实测记录里 206 条提到这两个子命令的行有 204 条是**复合命令**——
    #    复合是常态，不是边角情况。这一条直接废掉了整道闸的全部卖点。
    #  ⛔ 先剥掉 shell 注释再分段。不剥的话 `autopilot  # 先 --dry-run 看看`
    #     整段含 `--dry-run`，会被当演练放行——注释里的字不该有授权效力。
    #     ⚠️ 这是启发式（引号里的 `#` 会被误剥），但方向**偏严**：
    #     剥掉之后 `--dry-run` 少了，只会让更多命令需要令牌，⛔ 不会放过真跑。
    segs = [s for s in _SPLIT.split(_COMMENT.sub("", cmd)) if s.strip()]
    need_d = any((_DISPATCH.search(s) or _DOCTOR_SPEND.search(s))
                 and not _DRYRUN.search(s) for s in segs)
    need_a = any(_AUTOPILOT.search(s) and not _DRYRUN.search(s) for s in segs)
    if not (need_d or need_a):
        return 0                       # ⭐ 只演练、或压根没碰这两个子命令
    is_d, is_a = need_d, need_a

    tok = _token(sid)
    if is_a:
        if not (tok and tok.get("mode") == "C" and tok.get("approved")):
            return _deny(
                "⛔ `autopilot` 真跑要用户明确批准，当前没有有效令牌。\n"
                "   让用户先打 `%auto <项目> <要干的事>`（或 `/devloop`），你演练（--dry-run）\n"
                "   并把派几单/跑哪几道闸/上限/预计时长报给他，然后**他单独发一条 `%go`**。\n"
                "   ⚠️ 令牌 30 分钟有效。⛔ 你不能替他打 `%go`——钩子只认真实用户消息。")
        return 0
    if not (tok and tok.get("mode") in ("B", "C")):
        return _deny(
            "⛔ `dispatch` 真跑要用户打代号，当前没有有效令牌。\n"
            "   让用户打 `%task <要干的事>`（第一行行首）。\n"
            "   ⚠️ 代号是一次性的，上一轮打过这一轮不算。\n"
            # ⛔ 这里原本写着「想看看会跑什么？加 --dry-run」——**那句话是假的**：
            #    `--dry-run` 只有 `autopilot` 有，`dispatch` / `eval` / `gates`
            #    都没有（2026-07-31 实测四个子命令的 --help）。
            #    ⚠️ 给一条跑不通的建议比不给更坏：人照做，撞一个
            #    `unrecognized arguments`，然后开始怀疑整道闸是不是坏了。
            "   ⚠️ `dispatch` **没有** `--dry-run`（只有 `autopilot` 有）。\n"
            "   想先看看会跑什么，用这两条不花额度的：\n"
            "     python -m devloop.cli gates --project <项目>      # 只跑闸\n"
            "     python -m devloop.cli constitution --project <项目>  # 只看宪法")
    return 0


if __name__ == "__main__":
    sys.exit(main())
