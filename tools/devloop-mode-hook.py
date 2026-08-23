#!/usr/bin/env python
"""UserPromptSubmit 钩子：用**正则**判定干活模式，⛔ 不经过模型判断。

## 为什么要有这个东西

用户原话：「不行 我觉得这个判断方法也不好，我觉得需要一些特殊的字段来进行触发」。

他否掉的是「Claude 读你的话、猜你想要哪种模式」。而他是对的：
2026-07-29 当天 Claude 在这个项目上判断错了四次，四次都是真跑才抓到的。
**靠理解意图这一环本身就是不可靠的那一环。**

⭐ `UserPromptSubmit` 钩子在**模型看到你的话之前**运行，stdout 会被注入模型
上下文（CLI 里的原话：`additionalContext` — "Text injected into model context"）。
判定由下面这段正则做，模型只负责执行一条明确指令。

## ⛔ 光注入指令还不够——那还是「靠 Claude 自觉」

所以本钩子还会**落盘一个令牌**，由 `devloop-spend-gate.py`（PreToolUse）去读：
没有令牌，花额度的命令**物理上跑不起来**（`permissionDecision: "deny"`）。

    你打的字面量 ──正则──▶ 令牌文件 ──PreToolUse──▶ 命令放行 / deny

⚠️ 这是很强的护栏，**不是密封舱**：Claude 有 Bash，理论上能自己写令牌文件。
所以 gate 里另有一条——碰状态目录的 Bash 一律 deny。仍不是绝对，但成本很高。

## 字面量：`%` + 英文代号（2026-07-30 定）

| 你打的 | 模式 | 要不要跟内容 |
|---|---|---|
| （什么都不打） | **A 直接干** | — |
| `%task <要干的事>` | **B 派一单** | 要 |
| `%auto <项目> <要干的事>` | **C 唤起**（不放行） | 要 |
| `%go` | **C 放行** | 不要 |
| `%stop` | 撤销全部令牌 | 不要 |
| `%mode` | 只看当前状态 | 不要 |

⛔ 为什么是 `%` 而不是别的（第一版用过 `。。` `//`，第二版用过 `!`，都已废）：
- **`!` —— 它是 Claude Code 内置的 shell 模式前缀**（见下方 `_CMD` 处的实测）
- `/` —— 斜杠命令，且会弹命令菜单
- `@` —— 在 Claude Code 里**任意位置**都弹文件补全，会撞车
- 行首 `#` —— 那是内置的「写进 CLAUDE.md」快捷键
- `++` / `--` —— 会被 `C++` 和 `--dry-run` 打中
- `//` / `..` —— ⚠️ 粘贴 JS/C 代码时片段常以 `// 注释` 开头，误触发
- `。。` —— 中文口语的 `。。。` 是省略号，要靠「不许多一个」的规则去区分，脆
- `跑::` —— 中文标点模式下 `:` 被改写成全角，**根本打不出来**

⭐ `%` + **单词**的好处：正常句子不会这样开头；代号自解释、可扩展；
而且它是**一个词而不是一串符号**，读日志时一眼能看出是命令不是标点。

## ⛔ 判定规则（每条都对应一种具体的误触发）

1. **只认整条消息的第一行行首**。粘贴日志、句中提到代号，都不触发。
2. **一次性，不继承。** 下一条不带代号就回到 A。
   ⚠️ 「模式粘着」很危险：你说「继续」时，粘着的模式会替你做决定。
3. **`%task` / `%auto` 后面必须有内容**，光一个代号不触发（那多半是手滑）。
4. **认不出的代号不触发**，只回一行提示——⛔ 别猜他想干什么。
5. **回显。** ⛔ 没有回显的开关等于没有开关。
   回显由**脚本**打（`systemMessage`），不由 Claude 写：Claude 会判错，也就会写错。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

# ⛔ Windows 控制台默认 GBK，而 CLI 按 **UTF-8** 读钩子的 stdout。
#    不重设的话注入的中文会变乱码，而钩子「跑了」——⚠️ 失败是**静默**的。
#    devloop 自己的 cli.py 在同一个坑上栽过，这里照抄那条教训。
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

STATE = Path.home() / ".claude" / "state" / "devloop-mode"

#  B 的令牌**一轮一撤**，另加硬上限兜底（进程异常退出时不至于长期 armed）。
B_TTL = 10 * 60

#  ⛔ 2026-08-01：C 模式的两个时钟**必须分开**，它们保的不是同一件事。
#
#  · `PEND_TTL` —— 「`%auto` 唤起之后，还能不能批」。
#    ⭐ 它**什么都没授权**，所以可以宽松：这段时间正是编排方跑 `--dry-run`、
#    写任务书、把数字摆给你看的时间。⚠️ 演练得越认真，这段越长——**那是好事**。
#
#  · `C_TTL` —— 「批准之后还能用多久」，从 `%go` 那一刻（`granted_at`）起算。
#    ⛔ 这条必须收紧：一个批准不该长期挂在那里。
#
#  ⚠️ 原来两者共用一个时钟，且令牌的 `armed_at` 是从 pending **抄**过来的，
#  于是 **编排方演练花掉的时间被从用户的批准有效期里扣掉**。实测撞到：
#  一次认真的演练（写 3 份任务书 + 1 份计划 + 跑闸）把 30 分钟烧光，
#  用户随后打的 `%go` 当场失效，而屏幕上只说「没有待批准的阶段」。
#  **判据的维度错了。**
PEND_TTL = 30 * 60
C_TTL = 30 * 60

#  ⛔ **前缀是 `%`，不是 `!`。**
#     2026-07-30 实测：半角 `!` 是 Claude Code **内置的 shell 模式前缀**。
#     CLI 里就一行：
#         function vG(H){if(H.startsWith("!"))return"bash";return"prompt"}
#     界面上还印着 `! for shell mode`。所以打半角 `!task 干活` 会被当 shell
#     命令去执行 `task 干活`——**根本到不了模型，钩子也看不见**。
#     ⚠️ 而全角 `！` 能用（`startsWith("!")` 只匹配半角）——同一个字符
#     半角失效、全角生效，这个区别肉眼看不出来，⛔ 不能拿它当主字面量。
#
#  已核实被占的前缀：`!` shell 模式 · `/` 斜杠命令 · `#` 写进 CLAUDE.md ·
#  `@` 文件补全。而 `%` / `,` / `;` 在 CLI 里 `startsWith` 零命中。
#  ⭐ 选 `%`：中文输入法**不改写**它（逗号句号会被改成全角，百分号不会），
#     而且行首出现在自然语句里几乎为零。
#  ⚠️ 仍然认全角 `％` 和 `！`：前者是有人切了全角，后者是中文标点模式下
#     打 `!` 的实际产物——它能用，不认才是坑。
_CMD = re.compile(r"^[%％!！]([A-Za-z][A-Za-z0-9_-]{0,15})\b[ \t]*(.*)$")

KNOWN = ("task", "auto", "go", "stop", "mode")

_A = """\
【模式 A｜你直接干】本轮没有 DevLoop 触发代号。

⛔ 不许跑 `devloop dispatch` / `autopilot` 的真跑形态——PreToolUse 会 deny，跑也白跑。
要派单请用户自己打 `%task <要干的事>`；要无人值守请他打 `%auto` 或 `/devloop`。
⚠️ 拿不准就问，⛔ 不许替他决定花额度。"""

_B = """\
⛔ 本条由钩子判定为 **模式 B：派一单**（用户打了 `%task`，不是你猜的）。

你**不要自己动手改代码**。要做的是：
1. 写一份任务书（`# 角色` `# 任务` `# 禁令` 三段，整行标题）
2. `python -m devloop.cli dispatch --project <项目> --task <任务书> --tools implement`
3. 如实报告：过了哪几道闸、产出落在哪个分支、退出码是几

⛔ 合并进主线要用户逐个批准（宪法 C-2/C-4），你不能自己合。
⛔ 不许 `autopilot`——那是模式 C，要 `%auto` + `%go`。
⚠️ 授权**只在本轮有效**。
⚠️ 如果用户其实是在**讨论这个代号本身**而不是要派单，说明一下，别硬派。"""

_CK = """\
【模式 C｜已唤起，**未批准**】用户打了 `%auto`（或 `/devloop`），但还没打 `%go`。

⛔ 现在只许 `--dry-run`。把这几个数报给他，然后**等他单独发一条 `%go`**：
派几单、每单跑哪几道闸、上限多少、预计多久、用哪个后端。
⛔ `--dry-run` 绿了不代表能跑——它走不到 check_tree，也走不到 T5。
⛔ 你不能替他打 `%go`：钩子只在**真实用户消息**上触发。"""

_C = """\
【模式 C｜无人值守·**已批准**】用户已打 `%go`，`autopilot` 真跑放行。

⛔ 跑完不许自己合分支——逐个列给用户批。
⚠️ 退出码 3 是「还没跑完」，**不是成功**；停在哪条防线上要说清楚。
⚠️ 令牌 30 分钟后失效。"""

CTX = {"A": _A, "B": _B, "CK": _CK, "C": _C}


#  ⭐⭐ 2026-08-11 · 「回到工具」那块牌子。
#
#  ⛔ 它要治的毛病（用户 2026-08-11 原话）：
#     「我们的主要目标是这个工具，eco-ob 是我们的手段。我感觉你每次都在忘这一条。」
#
#  ⚠️ 为什么牌子只能立在这里：主模型跨轮次没有持久注意力，每轮上下文重建。
#     能穿过重建的只有「自动加载的文件」和「钩子注入的文字」两样。
#     ⛔ 而「忘了目的」的那一轮，恰恰是**不会主动去读讲目的那份文档**的那一轮。
#     ⭐ 这个函数是全仓**唯一**同时满足三条的位置：
#        ① 每条消息必到 ② 能往上下文里说话 ③ 说的话能被 stdout 断言（可红检）。
#
#  ⭐⭐ 判据为什么落在**闸版号**上，而不是落在「有没有写反思」上：
#     要让这个数归零，**必须真的去编辑 `templates/gates.sh` 的内容** ——
#     ⛔ 在任何文档里写多漂亮的字都改不动它。
#     ⚠️ 而「写下来」这条路已被实测证伪两次（`_trials/` 的 T-7、`BACKLOG.md` 的 G-124
#     都白纸黑字写过同一条，然后原样复发）。
#
#  ⭐ 而且它**会自己闭嘴**：搬完就不响了。
#     ⛔ 会自己闭嘴的告警才不是噪音；一上来就在拦、且拦不完的告警，
#     两周内必被关掉（G-127 那次「闸太严 → 10 个回归全红」的学费）。
def _drift_line(inp: dict) -> str:
    """靶子项目的闸比模板新 → 回两行；其余一律回**空串**。

    ⛔ 四条缺一不可（每一条都在防「变成天天出现、然后被无视的噪音」）：
      ① 当前目录没有 `.devloop/gates.sh` → 空串
         （本钩子挂在全局 `UserPromptSubmit` 上、**没有 matcher**，
         `C:/pg` 下十几个仓都会跑到它；在别的仓里喊 eco-ob 的账是纯噪音）；
      ② 两个版号任一读不出 → 空串
         （⚠️ 「不知道」在这里选择闭嘴，因为体检那一侧已经会喊 `ok=None`，
         ⛔ 别在每轮注入里把同一句话再喊一遍）；
      ③ 项目版号 ≤ 模板版号 → 空串（⭐ 不欠账的日子**一个字都不多**）；
      ④ 任何异常 → 空串（⛔ 钩子自己坏了绝不阻断用户 —— 本文件既有纪律）。
    """
    try:
        import os
        here = Path(str(inp.get("cwd") or "") or os.getcwd())
        if not (here / ".devloop" / "gates.sh").exists():
            return ""
        #  ⚠️ 延迟导入：钩子跑在任意 cwd 下，⛔ 顶层导入 devloop 会让整个钩子挂掉
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from devloop.doctor import gates_rev_pair
        a, b = gates_rev_pair(here)
        if a is None or b is None or a <= b:
            return ""
        cmd = "devloop " + "doctor --" + "project " + here.as_posix()
        return (f"\u26d4 这个项目的闸比模板新 **{a - b} 版**"
                f"（本项目 rev {a} / `templates/gates.sh` rev {b}）"
                f"——改进闸的办法**卡在这儿没回** `C:/pg/_infra/devloop`。\n"
                f"\u2b50 **这个仓是靶子，DevLoop 才是目的。**"
                f"对表：`{cmd}` 的「闸模板版本」那一行。\n\n")
    except Exception:
        return ""


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(p: Path, o: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


def _rm(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def parse(prompt: str) -> tuple[str, str]:
    """认代号。返回 (代号, 后面的内容)；认不出返回 ("", "")。

    ⚠️ 只看**第一行**：粘贴多行日志时，第二行以 `!` 开头不该触发。
    """
    first = prompt.replace("﻿", "").split("\n", 1)[0].strip()
    m = _CMD.match(first)
    if not m:
        return "", ""
    return m.group(1).lower(), m.group(2).strip()


def _granted(tok: dict) -> float:
    """令牌的计时起点。⛔ 模式 C 看 `granted_at`（`%go` 那一刻），不看 `armed_at`。

    ⚠️ 回退到 `armed_at` 只为兼容改动之前写下的老令牌 ——
    ⛔ 不能退回 0，那会让用户真按过的令牌立刻失效。
    """
    return float(tok.get("granted_at") or tok.get("armed_at", 0))


def main() -> int:
    try:
        inp = json.load(sys.stdin)
    except Exception:
        return 0                      # ⛔ 钩子自己坏了，绝不阻断用户
    if inp.get("hook_event_name") != "UserPromptSubmit":
        return 0

    sid = str(inp.get("session_id") or "nosession")
    tok = STATE / f"{sid}.json"
    pend = STATE / f"{sid}.pending.json"
    prompt = str(inp.get("prompt") or "")
    now = time.time()

    code, rest = parse(prompt)
    #  ⚠️ `/devloop` 与 `%auto` 等价：钩子拿到的是**展开前**的原文，看得见斜杠命令。
    #     ⭐ 两个入口都留着：`/devloop` 在斜杠菜单里看得见（可发现性），
    #     `%auto` 打字更少。⛔ 但都只**唤起**，不放行。
    if prompt.lstrip().startswith("/devloop"):
        code, rest = "auto", prompt.lstrip()[8:].strip()

    old, p = _read(tok), _read(pend)
    #  ⚠️ 待批准记录用 PEND_TTL —— 它什么都没授权，与「批准的有效期」是两件事。
    p_live = bool(p) and (now - float(p.get("armed_at", 0)) < PEND_TTL)
    mode, msg = "A", ""

    if code == "stop":
        _rm(tok), _rm(pend)
        msg = "〔已刹车〕全部令牌撤销 · 回到模式 A（你直接干）"
    elif code == "mode":
        # ⚠️ 只看，不改状态——查状态本身不该有副作用
        if old and old.get("mode") == "C" and now - _granted(old) < C_TTL:
            mode = "C" if old.get("approved") else "CK"
        elif p_live:
            mode = "CK"
        left = ""
        if mode != "A" and old:
            left = f" · 还剩 {(C_TTL - (now - _granted(old))) / 60:.0f} 分钟"
        msg = f"〔当前模式〕{mode}{left}"
    elif code == "task":
        if not rest:
            msg = "⚠️ `%task` 后面要跟要干的事，这条没生效。"
        else:
            _write(tok, {"mode": "B", "approved": False, "armed_at": now})
            mode, msg = "B", "〔派一单〕已识别 · 本轮只放行 dispatch 一单 · autopilot 仍锁着"
    elif code == "auto":
        if not rest:
            msg = "⚠️ `%auto` 后面要跟项目和要干的事，这条没生效。"
        else:
            _write(pend, {"armed_at": now, "raw": rest[:200]})
            mode, msg = "CK", "〔无人值守·已唤起〕先演练给你看 · 要真跑请单独发一条 `%go`"
    elif code == "go":
        if p_live:
            #  ⚠️ `armed_at` 仍然抄过来（留作可追溯：这次批准对应哪次唤起），
            #     ⛔ 但闸判过期看的是 `granted_at` —— 见 devloop-spend-gate.py::_token。
            #     两个时钟分开之后，编排方演练花掉的时间不再扣批准的有效期。
            _write(tok, {"mode": "C", "approved": True,
                         "armed_at": p.get("armed_at"), "granted_at": now})
            #  ⭐ **回显批准的是什么。** 这是「慢批准能多拿几分钟可用期」的对价：
            #     pending 里本来就存着用户打的原话，回显它零成本，
            #     而它把「随手打 `%go`、其实忘了自己唤起过什么」
            #     从看不见变成看得见。⛔ 只放宽时限不加这条，是净变松。
            what = str(p.get("raw") or "").strip()
            mode = "C"
            msg = ("〔无人值守·已批准〕autopilot 已放行 · 令牌 30 分钟后失效\n"
                   + (f"   批准的是：{what}" if what
                      else "   ⚠️ 这次唤起没记下内容——查不到你批准的是什么，建议 `%stop` 重来"))
        else:
            #  ⚠️ 待批准记录的时限**没有放宽**（仍是 PEND_TTL）。
            #     放宽的只是「批准之后能用多久」，⛔ 不是「唤起能挂多久」。
            msg = (f"⚠️ 没有待批准的阶段（或已超过 {PEND_TTL // 60} 分钟）。"
                   f"先打 `%auto <项目> <要干的事>`。")
    elif code:
        # ⛔ 认不出的代号一律不触发，只提示。别猜他想干什么。
        msg = f"⚠️ 不认识 `%{code}`。可用：{'、'.join('%' + k for k in KNOWN)}"

    #  本轮没产生新授权时：沿用未过期的 C 令牌，否则撤掉 B（一次性）
    #
    # ⛔ 撤令牌的条件是「**落回 A**」，不是「压根没打代号」。
    #    第一版写的是 `elif not code: _rm(tok)`，于是打错代号（`%tsak`）、
    #    空内容（`%task` 后面什么都没跟）、`%go` 但没 arm 过——这些情况
    #    `code` 都非空，**上一轮的 B 令牌继续活 10 分钟**，闸照样放行真跑。
    #    ⚠️ 最难受的是 `%auto`：注入给模型的上下文说「现在只许演练」，
    #    而物理闸在放行真跑——**分叉时闸是松的那一边**。
    # ⚠️ `%mode` 排除在外：查状态不该有副作用。
    if mode == "A" and code != "mode":
        if old and old.get("mode") == "C" and now - _granted(old) < C_TTL:
            mode = "C" if old.get("approved") else "CK"
        elif p_live:
            mode = "CK"
        else:
            _rm(tok)

    # ── 近似误击的提醒 ────────────────────────────────────────────
    # ⛔ **没有回显的失败比拦下来更坏**：用户以为放行了，实际什么都没发生。
    #
    # ⚠️ 实测（2026-07-31，这套机制第一次真用）：用户打的是
    #        「放行 %task 派 e1-dedup-prey-layer 这一单」
    #    ——自然得不能再自然的说法。代号在句子中间，于是不触发；
    #    而当时**一个字的提示都没有**，他以为放行了，我这边收到的是模式 A。
    #
    # ⚠️ 更早的那版有一小段近似检测，但在把前缀从 `。。` 换成 `%` 的重写里
    #    **整段丢了**——⛔ 重写时最容易掉的就是这种「边角但关键」的分支。
    if mode == "A" and not msg:
        clean = prompt.replace("﻿", "")
        lines = clean.split("\n")
        first_line = lines[0]
        m = re.search(r"[%％!！](task|auto|go|stop|mode)\b", first_line)

        #  ⛔ **第二种漏法：代号在后面的行。**
        #  ⚠️ 实测（2026-08-02）：用户打的是
        #        现在的状况如何了？
        #        %auto C:/pg/_infra/devloop 验并行派单
        #    ——先问一句再下命令，同样自然。代号在第 2 行行首于是不触发
        #    （**这是对的**：「第二行才有代号不触发」明写在自测里，
        #    那是「只认真实按键、不认粘贴内容」这个属性的根基），
        #    ⛔ **而他一个字的提示都没收到**。
        #
        #  ⚠️ 2026-07-31 修过一次同类问题，但只覆盖了「第一行的句子中间」
        #    ——**同一个洞补了一半**。⛔ 这就是为什么补完洞要追问「还有别的形状吗」。
        if not m:
            for k, ln in enumerate(lines[1:], start=2):
                m2 = _CMD.match(ln.strip())
                if m2:
                    msg = (f"⚠️ **这条没生效**：`%{m2.group(1)}` 在**第 {k} 行**，"
                           f"而代号必须在**整条消息的第一行行首**。\n"
                           f"   ⭐ 拆成两条发：先问你的问题，再单独发"
                           f"「{ln.strip()[:50]}」。\n"
                           f"   ⛔ 这条限制不是找茬——它是「只认真实按键、"
                           f"不认粘贴内容」的根基。")
                    break

        if m and not _CMD.match(first_line.strip()):
            code2 = m.group(1)
            #  ⚠️ `mode`/`go`/`stop` 不吃参数——别给它们编一个 `<要干的事>` 占位，
            #     照着提示打反而会得到「后面要跟内容」的第二次拒绝。
            tail = "" if code2 in ("mode", "go", "stop") else (
                " " + (first_line[m.end():].strip()[:40] or "<要干的事>"))
            msg = (f"⚠️ **这条没生效**：`%{code2}` 在句子中间，"
                   f"而代号必须在**第一行行首**。\n"
                   f"   照抄重发：%{code2}{tail}")
        elif first_line.lstrip().startswith(("／", "、、")):
            msg = "⚠️ 这条没触发。要派单：第一行行首打 `%task` 再接内容。"

    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                  "additionalContext": _drift_line(inp) + CTX[mode]}}
    if msg:
        out["systemMessage"] = msg
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
