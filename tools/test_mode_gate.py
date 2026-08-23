#!/usr/bin/env python
"""两道模式钩子的端到端自测。

⚠️ **必须写成文件跑，不能在 Bash 里内联。** 因为测试数据里字面包含
`devloop.cli autopilot` 这样的命令串，而 PreToolUse 闸会匹配 Bash 命令原文
——它分不出那是「要执行的命令」还是「作为数据的字符串」，于是把测试本身拦了。
⛔ 这不是闸的缺陷（它只看命令文本，本来就该保守），是测试方式要绕开它。

## 字面量：`%` + 英文代号

    %task <要干的事>          → B 派一单
    %auto <项目> <要干的事>    → C 唤起（不放行）
    %go                       → C 放行
    %stop                     → 撤销全部令牌
    %mode                     → 只看当前状态
    （什么都不打）             → A 直接干
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent

#  ⛔ **不许用真实的 HOME。**
#  两个钩子都把状态目录写死成 `Path.home()/.claude/state/devloop-mode`，
#  而本测试开头结尾各有一次 `rmtree(STATE)`——⚠️ 2026-07-31 发现：
#  **跑一次这个测试，就把用户刚打的 `%task` 令牌抹掉了**，
#  于是下一条 dispatch 被拦，看起来像「钩子坏了」，实际是测试擦的。
#
#  方向上它一直是安全的（只删不发；写的是 S1/S2 假会话号，授权不了真会话），
#  但擦掉用户按下的开关本身就不可接受。改成临时 HOME，测试从此不碰真实状态。
#
#  ⚠️ `Path.home()` 在 Windows 上读 `USERPROFILE`，POSIX 上读 `HOME`——**两个都要设**，
#     只设一个会在另一个平台上悄悄退回真实 HOME（已实测确认跟随）。
_TMP_HOME = Path(tempfile.mkdtemp(prefix="devloop-modetest-"))
STATE = _TMP_HOME / ".claude" / "state" / "devloop-mode"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8",
       "USERPROFILE": str(_TMP_HOME), "HOME": str(_TMP_HOME)}

D = "python -m devloop.cli " + "dispatch --project . --task t.md --tools implement"
A = "python -m devloop.cli " + "autopilot --project . --stage s"


def _run(script: str, payload: dict) -> str:
    p = subprocess.run([sys.executable, str(HERE / script)],
                       input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=ENV)
    return p.stdout.strip()


def say(text: str, sid: str = "S1") -> dict:
    out = _run("devloop-mode-hook.py",
               {"hook_event_name": "UserPromptSubmit", "prompt": text,
                "session_id": sid})
    return json.loads(out) if out else {}


def bash(cmd: str, sid: str = "S1") -> str:
    """⛔ 同时检查 stderr。

    两个钩子都是**故意 fail-open** 的（`except: return 0`）——钩子自己坏了
    不该挡住用户干活。⚠️ 代价是：闸里一个笔误就会把整道闸**静默关掉**。
    2026-07-30 真发生过：我引用了一个还没定义的正则，于是 15 条断言
    从 DENY 变成放行，而屏幕上什么都不报。
    所以这里把 stderr 一并看住：有 traceback 就当测试失败。
    """
    p = subprocess.run(
        [sys.executable, str(HERE / "devloop-spend-gate.py")],
        input=json.dumps({"tool_name": "Bash", "session_id": sid,
                          "tool_input": {"command": cmd}}),
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=ENV)
    if "Traceback" in (p.stderr or ""):
        return "闸自己崩了（fail-open，等于没有闸）：" + p.stderr.strip()[-120:]
    return "DENY" if p.stdout.strip() else "放行"


def _stamp(sid: str, kind: str, **fields) -> None:
    """直接改状态文件里的时间戳 —— ⛔ 只在测试里这么干。

    ⚠️ 时限类的判据没法靠「真的等 29 分钟」来测。伪造时间戳是唯一可行的办法，
    而它成立的前提是**判据只读文件里的数**（现在确实如此）。
    """
    f = STATE / (f"{sid}.pending.json" if kind == "pend" else f"{sid}.json")
    d = json.loads(f.read_text(encoding="utf-8"))
    d.update(fields)
    f.write_text(json.dumps(d), encoding="utf-8")


N = [0]
TOTAL = [0]


def check(name: str, got, want) -> None:
    #  ⛔ 失败数和总数都要记。只记失败数的话，「一条都没跑」和「全过」
    #     退出码一样是 0——空转即绿（第 1 种假绿）。外面的 pytest 包装靠
    #     TOTAL 断言「确实跑了这么多条」。
    TOTAL[0] += 1
    ok = got == want
    if not ok:
        N[0] += 1
        print(f"  ✗  {name:46} 实际={got!r}  期望={want!r}")
        return
    print(f"  ✓  {name:46} {got}")


def mode_of(r: dict) -> str:
    c = r["hookSpecificOutput"]["additionalContext"]
    for k in ("模式 A", "模式 B", "未批准", "已批准"):
        if k in c:
            return {"模式 A": "A", "模式 B": "B", "未批准": "CK", "已批准": "C"}[k]
    return "?"


def main() -> int:
    shutil.rmtree(STATE, ignore_errors=True)

    print("① 什么都没打 —— 两个都该拦")
    check("模式", mode_of(say("帮我改个注释")), "A")
    check("dispatch", bash(D), "DENY")
    check("autopilot", bash(A), "DENY")

    print("② `%task` —— dispatch 放行，autopilot 仍锁")
    r = say("%task 给 nightly.py 补 --json")
    check("模式", mode_of(r), "B")
    check("有回显", bool(r.get("systemMessage")), True)
    #  ⛔ 令牌必须落在**临时 HOME**里。
    #  ⚠️ 光看「dispatch 放行」证明不了隔离：如果 HOME 覆盖对钩子和闸
    #     **同时**失效，两边都用真实目录，照样对得上、照样全绿——
    #     而用户刚按下的开关被这次测试擦掉了。所以要直接验落盘位置。
    check("⛔ 令牌落在临时 HOME 里（没碰真实状态）",
          (STATE / "S1.json").exists(), True)
    check("dispatch", bash(D), "放行")
    check("autopilot", bash(A), "DENY")

    print("③ 下一轮没打代号 —— 令牌一次性，该撤掉")
    say("那再看看别的")
    check("dispatch", bash(D), "DENY")

    print("④ 全角 `！task` 也认（中文输入法默认全角）")
    say("％task 干点啥")
    check("dispatch", bash(D), "放行")

    print("⑤ ⛔ 打错代号 / 空内容 之后，上一轮的令牌必须**撤掉**")
    # ⛔ 2026-07-30 审计抓到：第一版撤令牌的条件是「压根没打代号」，
    #    于是打错代号（`%tsak`）时 `code` 非空，B 令牌继续活 10 分钟。
    #    ⚠️ 而自测当时全绿，是因为它在这一段之前先 rmtree 了状态目录——
    #    **正好跳过泄漏路径**。这里刻意不清理。
    say("%task 先拿到令牌")
    check("先确认有令牌", bash(D), "放行")
    say("%tsak 打错了")
    check("打错代号 → 令牌该撤掉", bash(D), "DENY")
    say("%task 再拿一次")
    say("%task")                      # 空内容
    check("空内容 → 令牌该撤掉", bash(D), "DENY")
    say("%task 再拿一次")
    say("%go")                        # 没 arm 过
    check("空放的 %go → 令牌该撤掉", bash(D), "DENY")

    print("⑤b 光一个代号 / 认不出的代号 —— 不触发，只提示")
    shutil.rmtree(STATE, ignore_errors=True)
    r = say("%task")
    check("`%task` 空内容不触发", mode_of(r), "A")
    check("有提示", "没生效" in r.get("systemMessage", ""), True)
    r = say("%frobnicate 随便")
    check("认不出的代号不触发", mode_of(r), "A")
    check("提示可用代号", "%task" in r.get("systemMessage", ""), True)

    print("⑥ ⛔ 粘贴 / 元讨论 都不该触发")
    check("第二行才有代号", mode_of(say("看日志：\n!task something\n为什么")), "A")
    check("句中提到代号", mode_of(say("我想用 %task 来触发派单")), "A")

    print("⑦ 演练放行，⛔ 但串联里的真跑那一段必须拦")
    check("单独 --dry-run", bash(A + " --dry-run"), "放行")
    # ⛔ 2026-07-30 审计抓到：判据落在**整条命令**上，于是下面全部被放行。
    #    实测记录里 206 条提到这两个子命令的行有 204 条是复合命令——复合是常态。
    check("--dry-run && 真跑", bash(A + " --dry-run && " + A), "DENY")
    check("真跑 && --dry-run", bash(A + " && " + A + " --dry-run"), "DENY")
    check("注释里写 --dry-run", bash(A + "  # 先 --dry-run 看看"), "DENY")
    check("echo --dry-run; 真跑", bash("echo --dry-run; " + D), "DENY")
    check("换行分隔", bash(A + " --dry-run\n" + A), "DENY")

    print("⑧ ⛔ 自我授权该被拦，但工具自己的脚本要能跑")
    check("写状态目录",
          bash("echo x > ~/.claude/state/devloop-mode/S1.json"), "DENY")
    check("跑钩子脚本本身",
          bash(f'python "{HERE}\\devloop-mode-hook.py"'), "放行")

    print("⑨ 不花钱的子命令不许拦")
    for c in ("doctor", "prune --project .", "backends", "stats --project ."):
        check(c, bash("python -m devloop.cli " + c), "放行")

    print("⑨b ⛔ 但 `doctor --project` / `--probe` **真花额度**，要令牌")
    # ⛔ 2026-07-30 审计抓到：它是「夜跑标准流程第 1 步」，
    #    却被闸和文档双双写成「不花钱」——每次体检都在悄悄吃掉当前的 5 小时窗口。
    shutil.rmtree(STATE, ignore_errors=True)
    check("doctor --project", bash("python -m devloop.cli doctor --project ."), "DENY")
    check("doctor --probe", bash("python -m devloop.cli doctor --probe"), "DENY")
    check("裸 doctor 仍放行", bash("python -m devloop.cli doctor"), "放行")
    say("%task 拿令牌")
    check("有令牌后 doctor --project 放行",
          bash("python -m devloop.cli doctor --project ."), "放行")

    print("⑩ 模式 C 要两把钥匙")
    shutil.rmtree(STATE, ignore_errors=True)
    check("光 `%go` 没唤起过 → 不给", mode_of(say("%go")), "A")
    r = say("%auto C:/pg/eco-ob 补边界用例")
    check("`%auto` → 已唤起未批准", mode_of(r), "CK")
    check("autopilot 仍拦", bash(A), "DENY")
    r = say("%go")
    check("`%go` → 已批准", mode_of(r), "C")
    check("autopilot 放行", bash(A), "放行")

    print("⑪ `/devloop` 与 `%auto` 等价")
    shutil.rmtree(STATE, ignore_errors=True)
    check("/devloop 也能唤起",
          mode_of(say("/devloop C:/pg/eco-ob 干点啥")), "CK")

    print("⑫ `%stop` 刹车 · `%mode` 只看不改")
    r = say("%mode")
    check("`%mode` 不改状态", mode_of(r), "CK")
    say("%stop")
    check("`%stop` 之后回 A", mode_of(say("%mode")), "A")
    check("autopilot 被锁回去", bash(A), "DENY")

    print("⑬ ⛔ 跨会话不许串号（第一版 glob 全目录，A 的令牌放行了 B）")
    shutil.rmtree(STATE, ignore_errors=True)
    say("%task 干活", sid="S1")
    check("S1 自己放行", bash(D, sid="S1"), "放行")
    check("S2 不该被 S1 的令牌放行", bash(D, sid="S2"), "DENY")

    print("⑭ ⚠️ 代号在句子中间 —— 不触发，但**必须回显**")
    #  真实事故（2026-07-31，机制第一次真用）：用户打的是
    #      「放行 %task 派 e1-dedup-prey-layer 这一单」
    #  代号在句子中间 → 不触发（对），但当时**一个字的提示都没有** →
    #  用户以为放行了，我这边收到的是模式 A。
    #  ⛔ 没有回显的失败比拦下来更坏。
    shutil.rmtree(STATE, ignore_errors=True)
    r = say("放行 %task 派 e1-dedup-prey-layer 这一单")
    check("句中的 %task 不触发", mode_of(r), "A")
    check("句中的 %task 必须回显", "没生效" in r.get("systemMessage", ""), True)
    check("⛔ 而且不许留下令牌", bash(D), "DENY")
    check("无关消息不该误报", "systemMessage" in say("现在做到哪一步了"), False)
    r = say("好的 %auto eco-ob 跑起来")
    check("句中的 %auto 也回显", "没生效" in r.get("systemMessage", ""), True)
    #  ⚠️ `%mode` 不吃参数——提示里不许编一个 `<要干的事>` 占位，
    #     照着打会得到「后面要跟内容」的第二次拒绝。
    check("%mode 的提示不带占位",
          "<要干的事>" in say("先看看 %mode").get("systemMessage", ""), False)

    print("⑮ ⛔ 两个时钟必须分开：演练花掉的时间不许扣批准的有效期")
    #  ⛔ 2026-08-01 实测撞到的真缺陷：令牌的 armed_at 是从 pending **抄**过来的，
    #     而闸拿它判过期 —— 于是「我演练得越认真，用户的批准有效期越短」。
    #     上一轮我写了 3 份任务书 + 1 份计划 + 跑了闸，把 30 分钟窗口烧光，
    #     用户打的 %go 直接失效。⚠️ **判据的维度错了**：
    #     「待批准请求还有效吗」和「批准之后还能用多久」是两件事。
    #
    #  ⚠️ 这两条断言必须**能判别新旧行为**。第一版写的是「29 分钟前唤起」，
    #     而 29 < 30，改之前也是绿的 —— ⛔ 那种测试证明不了任何事。
    #     现在直接把两个字段拆开钉：唤起很久 + 批准很新 → 必须放行。
    shutil.rmtree(STATE, ignore_errors=True)
    say("%auto C:/pg/x 验崩溃恢复")
    say("%go")
    _stamp("S1", "tok", armed_at=time.time() - 45 * 60,     # 唤起是 45 分钟前（演练很久）
                        granted_at=time.time() - 5 * 60)     # 但批准是 5 分钟前
    check("⭐ 唤起 45 分钟前 + 批准 5 分钟前 → 放行", bash(A), "放行")

    print("⑯ ⛔ 但批准本身的有效期照旧收紧（从 %go 那一刻起算）")
    _stamp("S1", "tok", armed_at=time.time() - 5 * 60,       # 唤起很新
                        granted_at=time.time() - 31 * 60)    # 但批准已满 31 分钟
    check("⛔ 批准满 31 分钟 → 拒绝（哪怕唤起很新）", bash(A), "DENY")

    print("⑰ ⛔ 待批准记录过期之后，%go 仍然要拒绝")
    #  ⚠️ 放宽的只是「批准的可用期」，⛔ 不是「唤起能挂多久」。
    shutil.rmtree(STATE, ignore_errors=True)
    say("%auto C:/pg/x 很久以前唤起的")
    _stamp("S1", "pend", armed_at=time.time() - 31 * 60)
    check("过期的唤起不许被批准", mode_of(say("%go")), "A")
    check("也不该留下令牌", bash(A), "DENY")

    print("⑱ ⭐ %go 必须回显**批准的是什么**（第 1 条放宽的对价）")
    #  ⛔ 只做「闸改读 granted_at」而不做这条，等于让「随手打 %go」更容易且仍然看不见。
    #     pending 里本来就存着 raw，回显它零成本。
    shutil.rmtree(STATE, ignore_errors=True)
    say("%auto C:/pg/eco-ob 补三个物种的繁殖参数")
    r = say("%go")
    check("回显带上了项目", "eco-ob" in r.get("systemMessage", ""), True)
    check("回显带上了要干的事", "繁殖参数" in r.get("systemMessage", ""), True)

    print("⑲ ⚠️ 两个常量必须真的用在不同地方（现在数值相同，测不出行为差）")
    #  ⛔ 诚实标注：`PEND_TTL` 与 `C_TTL` 目前都是 30 分钟，所以「把一个换成另一个」
    #     在行为上测不出区别 —— 那一半改动**当前只是命名**。
    #     ⚠️ 但命名不是白改的：它让「唤起能挂多久」和「批准能用多久」
    #     可以**各自调整而不互相牵连**。这条断言钉的正是那个前提：
    #     两个名字确实被用在了两个不同的判断上。⛔ 谁把它们合回一个，这里就红。
    src = (HERE / "devloop-mode-hook.py").read_text(encoding="utf-8")
    check("待批准记录判的是 PEND_TTL", "< PEND_TTL)" in src, True)
    check("已批准令牌判的是 C_TTL", "_granted(old) < C_TTL" in src, True)
    gsrc = (HERE / "devloop-spend-gate.py").read_text(encoding="utf-8")
    check("⛔ 闸对 C 令牌看 granted_at", 'd.get("granted_at")' in gsrc, True)

    print("⑮ ⚠️ 代号在**第二行** —— 不触发（对），但必须回显（2026-08-02 实测漏掉）")
    #  真实事故：用户打的是
    #      现在的状况如何了？
    #      %auto C:/pg/_infra/devloop 验并行派单
    #  ——先问一句再下命令。代号在第 2 行行首，不触发是**对的**（见 ⑥ 那条），
    #  ⛔ 但当时一个字的提示都没有。⚠️ 07-31 修过同类问题，只覆盖了
    #     「第一行的句子中间」——**同一个洞补了一半**。
    shutil.rmtree(STATE, ignore_errors=True)
    r = say("现在的状况如何了？\n%auto C:/pg/_infra/devloop 验并行派单")
    check("第二行的代号不触发", mode_of(r), "A")
    check("⛔ 但必须回显", "没生效" in r.get("systemMessage", ""), True)
    check("回显要指出是第几行", "第 2 行" in r.get("systemMessage", ""), True)
    check("⛔ 而且不许留下令牌", bash(A), "DENY")
    #  ⚠️ 反面：粘贴内容里含代号但**不在行首** —— 不该报「第 N 行」，
    #     否则每次贴日志都会被提醒一次，⛔ 而稳定误报会让人把整套关掉。
    r = say("看日志：\n粘贴的内容里有 %task 但不在行首\n完")
    check("句中提到的代号不算（防粘贴误报）",
          "第 2 行" in r.get("systemMessage", ""), False)

    # ── 触发代号被送进 shell（故障 13 的第二种形态，2026-08-12）────────
    #
    # ⛔ 复发经过：模型把 `%task ...` 写进了一个 ```bash 代码块 ——
    #    那种块**带「运行」按钮**，用户一点就送进 PowerShell，
    #    得到 `%task : The term '%task' is not recognized...`。
    # ⚠️ 与第一种（`!` 被 CLI 输入分类层截走）的失效**完全相同**：
    #    钩子从没收到，钱没花、活没派，⛔ 而用户看到的是「工具坏了」。
    # ⭐ 判据：一条 Bash 命令**行首**出现触发代号 —— 那永远是搞错了，没有例外。
    print("\n【触发代号误入 shell】")
    check("半角 %task 进 shell → 拦", bash("%task C:/pg/eco-ob 组4"), "DENY")
    check("带缩进也要拦", bash("   %auto C:/pg/eco-ob 跑一轮"), "DENY")
    #  ⚠️ 全角：故障 13 的学费——同一个字符两种宽度，肉眼看不出来
    check("全角 ％go 也要拦", bash("％go"), "DENY")
    check("%stop 也要拦", bash("%stop"), "DENY")
    #  ⭐ 绿检（⛔ 缺了就是只做过红检的判据）：代号不在行首 = 普通命令，一条都不许误伤
    check("代号在句中不算（绿检）", bash("echo '100%task done'"), "放行")
    check("普通命令不受影响（绿检）", bash("python -m pytest -q"), "放行")
    check("grep 里带 task 不受影响（绿检）", bash("grep -rn task ."), "放行")

    shutil.rmtree(STATE, ignore_errors=True)
    shutil.rmtree(_TMP_HOME, ignore_errors=True)
    print(f"\n  —— 跑了 {TOTAL[0]} 条 · "
          f"{'全部通过' if not N[0] else str(N[0]) + ' 项不符预期'}")
    return 1 if N[0] else 0


if __name__ == "__main__":
    sys.exit(main())
