"""派一个工人：拼 prompt → 起进程 → 解析回执。

已实证的唯一可行写法（见 TROUBLESHOOTING.md）：
    claude --bare -p <prompt> --output-format json   + 按进程注入 env
`--bare` 不是性能优化而是必需项——没它会被过期的 OAuth 凭据劫持。
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ProjectPaths
from . import naming
from . import quota
from .models import Receipt, TaskSpec, WorkerConfig

TOOL_PRESETS = {
    "readonly": "Read,Grep,Glob",
    "implement": "Read,Write,Edit,Bash,Grep,Glob",
    "full": "Read,Write,Edit,Bash,Grep,Glob,WebFetch",
}


@dataclass
class DispatchResult:
    task: str
    receipt: Receipt | None
    report_path: Path | None
    error: str | None = None
    model_mismatch: bool = False
    #  ⭐ 这一单结束时的订阅额度状态（stream-json 的 rate_limit_event）。
    #     ⚠️ None = **不知道**，不是「充足」。付费端点拿不到它。
    rate_limit: "quota.RateLimit | None" = None
    #  ⭐ 这一单事件流里 usage 的 token 之和。⚠️ None = **不知道**，不是 0。
    #  ⛔ 存在的理由是超时那条路：正常回执里有 `total_cost_usd`，
    #     而被死线打死时只剩半截事件流——它是那时唯一算得出用量的东西。
    #  ⚠️ 加在**末尾**（G-95 的学费：中途插字段会让位置参数全部错位）。
    usage_tokens: int | None = None
    #  ⭐ 这一单是**被死线打死**的吗。⛔ 下游别再从 `error` 的自由文本里猜——
    #     本项目在闸的归类上栽过一次（「会被测试名劫持」）。
    #  ⚠️ 默认 False：一个新的失败形态不许静默地继承「被打死」的处置
    #     （那会让闸被跳过而没人发现）。
    timed_out: bool = False

    @property
    def rate_limited(self) -> bool:
        """这一单是撞了额度上限吗。

        ⛔ 撞额度**不是**「模型不行」，也不是「活没干好」——是「现在跑不了」。
        混进失败堆里会污染成本实验的结论，与「撞轮数上限是拆单太大」同一类区分。
        """
        return self.rate_limit is not None and self.rate_limit.blocked

    @property
    def ok(self) -> bool:
        return self.receipt is not None and not self.receipt.is_error \
            and not self.model_mismatch and self.error is None


def build_prompt(rules_digest: str, task: TaskSpec,
                 worker_env: dict[str, str] | None = None) -> str:
    """按**缓存友好**的顺序拼装：稳定前缀在前，易变内容在后。

    Prompt caching 只对完全一致的前缀生效，所以规则摘要（每单相同）必须
    排在具体任务（每单不同）之前。⛔ 前缀里不得含时间戳/随机 ID/会话号——
    塞进去缓存就作废。实测未做分层时缓存命中为 0。

    ## ⭐ 为什么要把环境变量**告诉**工人（G-112）

    2026-08-01 eco-ob 首跑：工人报 `godot: command not found`。
    修法是让项目在 `[worker].env` 里声明 `GODOT_BIN`，工具把它注入子进程。
    ⛔ **可是从没人告诉工人这个变量叫什么。**

    ⚠️ 于是 2026-08-04 那一跑，工人上来照样敲 `godot`、照样报找不到，
    然后手工翻目录去找——⭐ 修的是「塞变量」那一侧，漏的是「告诉它」那一侧，
    **同一个坑原样又踩一次**。

    ⚠️ 这段属于**每单相同**的稳定前缀，放在规则摘要之后不破坏缓存。
    """
    env_block = ""
    if worker_env:
        #  ⛔ 排序：⚠️ 字典顺序不稳定会让缓存前缀每单都变，缓存直接归零。
        lines = "\n".join(f"- `{k}` = `{v}`" for k, v in sorted(worker_env.items()))
        env_block = (
            "## 本环境已经给你准备好的变量\n\n"
            f"{lines}\n\n"
            "⭐ 需要调用这些工具时**用这些变量**，⛔ 别直接敲裸命令名"
            "——它们多半不在 PATH 里。\n\n"
            "---\n\n")
    return (
        "以下是本项目的规则摘要，适用于你接下来的全部工作。\n\n"
        f"{rules_digest.strip()}\n\n"
        "---\n\n"
        f"{env_block}"
        f"{task.body.strip()}\n"
    )


def build_cmd(cfg: WorkerConfig, *, tools: str, max_turns: int,
              prompt: str, exe: str) -> list[str]:
    """拼工人进程的命令行。

    ## ⛔ `--bare` 只给付费端点，**绝不给订阅**

    `--bare` 的用途是**跳过 OAuth 与 keychain**，好让环境变量里的 key 生效。
    付费端点必须要它——不加的话，磁盘上那份 OAuth 凭据会**劫持**掉环境变量里的
    key，报 401（TROUBLESHOOTING 故障 4）。

    ⭐ 而订阅形态要的**恰恰是 OAuth**。加了 `--bare` 就等于把订阅关掉，
    再掉回去读环境变量里那个（订阅形态压根没注入的）key。
    """
    cmd = [exe]
    if not cfg.subscription:
        cmd.append("--bare")
    cmd += ["-p", prompt,
            "--allowedTools", TOOL_PRESETS.get(tools, TOOL_PRESETS["readonly"]),
            "--max-turns", str(max_turns),
            # ⭐ **stream-json 而不是 json**，为的是那条 `rate_limit_event`。
            #    实测（2026-07-29）：每一跑都会吐一条额度事件，带精确的
            #    `resetsAt`（unix 秒）和 `rateLimitType`——**不用等撞墙**就能
            #    看见自己离墙多近。`--output-format json` 那版拿不到这些。
            #    ⚠️ 回执本身（result 事件）字段与 json 版一致，已实测核对。
            # ⛔ `--verbose` 是**必需的**，不是调试开关：实测不加会直接报
            #    「When using --print, --output-format=stream-json requires --verbose」
            #    然后退出码 1、零输出。
            "--output-format", "stream-json", "--verbose"]
    return cmd


def dispatch_one(
    task: TaskSpec,
    paths: ProjectPaths,
    cfg: WorkerConfig,
    *,
    tools: str = "readonly",
    max_turns: int = 30,
    env_base: dict[str, str] | None = None,
    cwd: Path | None = None,
    claude_bin: str | None = None,
    reports_dir: Path | None = None,
    #  ⭐ 覆盖这一单的死线（秒）。⛔ `None` = 用后端配置里的 `cfg.timeout_s`。
    #  ⚠️ 存在的理由是墙钟（G-107）：自动驾驶要把「到墙钟为止还剩多久」
    #     收窄进来，否则 `max_wall_min` 对正在跑的单毫无约束力。
    timeout_s: int | None = None,
) -> DispatchResult:
    import os

    #  ⭐ 把项目声明的环境变量**告诉**工人，不只是注入（G-112）。
    #     ⚠️ 与下面 env 三层叠加读的是同一个 `worker_env()`——⛔ 两处必须同源，
    #     否则「说的」和「给的」会分叉，那比不说更坏。
    wenv = paths.worker_env()
    prompt = build_prompt(paths.read_rules_digest(), task, wenv)
    #  ⭐ 三层叠加，顺序有意义：
    #    ① 当前进程环境 → ② 项目声明的 `[worker].env` → ③ 后端凭据
    #  ⛔ 后端凭据**必须最后**：它带的是认证，任何东西都不许覆盖它。
    #  ⚠️ ② 是 2026-08-01 加的：eco-ob 首跑时工人报 `godot: command not found`，
    #     而闸跑得好好的——因为闸里写死了完整路径，**两边看到的不是同一个环境**。
    #     现在项目可以自己声明（eco-ob 声明的是 GODOT_BIN）。
    #     `worker_env()` 会拒绝 ANTHROPIC_*/CLAUDE_*/PATH，见 config.py。
    env = {**(env_base if env_base is not None else os.environ),
           **wenv, **cfg.env()}

    # ⚠️ 可执行文件必须显式指定，不能只靠 env 里的 PATH：
    #    Windows 上 subprocess 用**当前进程**的 PATH 解析可执行文件，
    #    传进 env 的 PATH 不参与解析。2026-07-26 测错误路径时实测撞到。
    #    （与 gates.py::find_bash 同源问题：Windows 的可执行文件解析不能想当然。）
    exe = claude_bin or os.environ.get("DEVLOOP_CLAUDE_BIN") or "claude"

    cmd = build_cmd(cfg, tools=tools, max_turns=max_turns, prompt=prompt, exe=exe)

    #  ⭐ 实际死线：调用方给了就用它，否则用后端配置的。
    #  ⛔ 下面报错信息里印的必须是**这个**数，不是 `cfg.timeout_s`
    #     ——印错了人会去改一个根本没生效的配置。
    limit_s = cfg.timeout_s if timeout_s is None else timeout_s

    started = time.time()
    try:
        proc = subprocess.run(
            cmd, env=env, cwd=(cwd or paths.project), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=limit_s,
            # ⛔ 必须显式给空 stdin。不给的话子进程继承编排方的 stdin，实测打印
            #    `Warning: no stdin data received in 3s, proceeding without it`
            #    ——每单白等 3 秒；而在没有终端的场合（计划任务、后台派单）
            #    继承一个永不结束的 stdin 会直接把整批挂死。
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        #  ⛔ **这里钱已经花掉了，而且是花满的那一种**——工人跑够了整个死线。
        #  ⭐ `TimeoutExpired` 手里就攥着它被杀之前吐出来的全部内容
        #     （`.output` / `.stderr`），⚠️ 其中就有 usage 事件。
        #     2026-08-04（G-110）之前这里一眼没看就扔了，于是那 50 分钟
        #     花了多少**永远算不出来**——连手工翻都翻不到。
        #  ⚠️ 同一个函数里另外两种失败（无 result 事件 / 回执不合契约）
        #     **都**把原始流存成 `.raw.txt`。⛔ 唯独最该留材料的这一种不留，
        #     那个不对称本身就是缺陷。
        return _keep_scraps(
            task, started, paths, reports_dir, timed_out=True,
            out=exc.output, err=exc.stderr,
            #  ⚠️ 措辞里**必须留着「超时」二字**：`autopilot.py` 的失败模式归类
            #     按 `"超时" in e` 分档。⛔ 换成别的说法会让它静默掉进「其它」，
            #     而那一档是不看的。⭐ 判据见 `test_超时的说法要和自动驾驶的归类对得上`。
            why=f"⛔ 工人**超时被杀**（{limit_s}s 死线——不是它自己跑完的）")
    except FileNotFoundError:
        #  ⭐ 唯一「不留材料」是对的那条路：子进程根本没起来 ⇒ 没花钱 ⇒ 没材料。
        return DispatchResult(task.name, None, None,
                              error="找不到 claude 可执行文件，检查 PATH")

    # ══ 从这里往下，**钱已经花掉了** ══════════════════════════════
    # ⛔ 所以这一段的任何异常都不许把 `DispatchResult` 弄丢——上层判「钱花没花」
    #    的依据就是 `dispatch_one` 有没有返回。它抛异常出去，上层的补记逻辑
    #    （`if res is not None`）就永远读不到，于是**一单花过钱的活台账零行**，
    #    而失控防线全都只从台账读。
    # ⚠️ 实测复现过：把 `.devloop/reports` 造成一个文件（mkdir 会抛
    #    FileExistsError），输出只有一行 `✗ t: FileExistsError`，
    #    台账 0 行、一个字都没提钱花过。
    try:
        return _finish(task, cfg, proc, started, paths, reports_dir)
    except Exception as exc:                                    # noqa: BLE001
        #  ⛔ 这一处 2026-08-04 被 AST 判据一并抓出来：措辞写着「钱已花掉」，
        #     而它照样一个字的材料都不留。⭐ `proc` 就在手上。
        return _keep_scraps(
            task, started, paths, reports_dir,
            out=proc.stdout, err=proc.stderr,
            why=f"⚠️ 钱已花掉但回执处理失败：{type(exc).__name__}: {str(exc)[:160]}")


def _keep_scraps(task: TaskSpec, started: float, paths: ProjectPaths,
                 reports_dir: Path | None, *, out: object, err: object,
                 why: str, timed_out: bool = False) -> DispatchResult:
    """把半截事件流留下来，并尽力从里面把用量捞出来。

    ⭐ 用在**钱已经花掉、但拿不到合规回执**的所有路径上。

    ⛔ 落盘本身不许把这一单再炸一次：写不下去（盘满、目录被人做成文件）时
       仍然要返回一个带 `why` 的结果——⚠️ 上层判「钱花没花」的依据是
       `dispatch_one` 有没有返回，抛出去就等于那一单台账零行。

    ⛔ 工人一个字都没说时**不造空文件**：「没东西可存」与「存了」是两件事，
       造一个空文件会让人以为材料在这儿，翻开却是空的——比没有更坏。
    """
    #  ⚠️ `TimeoutExpired.output` 在 text=True 时是 str，但契约上允许 bytes。
    def _s(v: object) -> str:
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        return v if isinstance(v, str) else ""

    so, se = _s(out), _s(err)
    tokens = quota.sum_tokens(so)

    #  ⭐⭐ 2026-08-12 · **接住回执**。⛔ 旧写法是 `_, rl = ...` —— 把它扔了。
    #
    #  ⚠️ 实测代价：`f3-pyramid-calibrate`（2026-08-06）那一单，工人**真的交卷了**
    #     （`subtype=success · num_turns=26 · duration_ms=2299277`，38 分钟），
    #     ⛔ 而台账记的是「工人超时被杀」、`worker_ok=False`、闸一次都没跑。
    #     ⇒ 4 单连败里有 1 单是**工具把成功记成了失败**。
    #
    #  ⭐ 「进程被死线打死」与「工人没干完」是**两件事**：
    #     前者只说明壳没退出（后台命令挂着、输出没冲干净…），
    #     ⛔ 而它今天被当成后者，直接跳过闸、判工人失败。
    raw_result, rl = quota.parse_stream(so)
    salvaged = None
    if raw_result is not None:
        try:
            cand = Receipt(**raw_result)
            if not cand.is_error:
                salvaged = cand
        except Exception:            # noqa: BLE001
            #  ⚠️ 字段不合契约 → 当没捞到。⛔ 不许硬塞一个半残回执进台账。
            salvaged = None
    tail = ""
    if tokens is not None:
        tail += f"；这半截里已用 {tokens} tokens"
    if rl is not None:
        tail += f"；额度状态：{rl.summary()}"

    #  ⭐ 捞到了 = 工人干完了 ⇒ `timed_out` 落回 False，**让闸真的跑起来**。
    #     ⚠️ `why` 里仍然留着「进程被打死」那句话（那是真事，只是不再等于「没干完」）。
    _to = timed_out and salvaged is None
    if salvaged is not None:
        tail += "；⭐ **工人其实交卷了**（回执 subtype=success），是壳没退出——已捞回，闸照跑"

    if not (so.strip() or se.strip()):
        return DispatchResult(task.name, None, None, error=why + tail,
                              rate_limit=rl, usage_tokens=tokens,
                              timed_out=timed_out)
    try:
        out_dir = reports_dir or paths.reports
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{naming.stamp(started)}_{task.name}.raw.txt"
        p.write_text(so + "\n--- stderr ---\n" + se, encoding="utf-8")
    except Exception as exc2:                                   # noqa: BLE001
        return DispatchResult(
            task.name, salvaged, None, rate_limit=rl, usage_tokens=tokens,
            timed_out=_to,
            error=f"{why}{tail}；⛔ 而且材料也没存下来：{type(exc2).__name__}")
    return DispatchResult(task.name, salvaged, p, error=why + tail,
                          rate_limit=rl, usage_tokens=tokens,
                          timed_out=_to)


def _finish(task: TaskSpec, cfg: WorkerConfig, proc, started: float,
            paths: ProjectPaths, reports_dir: Path | None) -> DispatchResult:
    """落盘回执 + 解析 + 核对模型。⚠️ 调用它时钱已经花了，见上面那段注释。"""
    # ⚠️ 同上：同名任务同秒并发会让回执文件互相覆盖（G-41）
    stamp = naming.stamp(started)
    # ⛔ `reports_dir` 存在的唯一理由：**评测的回执不能落进被考的仓库**。
    #    工人 cwd 就是那个仓库、readonly 预设（Read/Grep/Glob）没有路径白名单，
    #    于是上一轮的 20 份答案会成为下一轮的可读材料（G-51）。
    #    默认 None = 落项目自己的 reports/，不改变任何现有调用路径。
    out_dir = reports_dir or paths.reports
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"{stamp}_{task.name}.json"

    # ⚠️ stream-json 是**多行事件流**，不是单个 JSON 文档。
    #    坏行（警告、进度）不许打死整个解析——一行坏行让整单失败，
    #    等于把已经花掉的额度扔了。
    raw_result, rl = quota.parse_stream(proc.stdout)
    if raw_result is None:
        report.with_suffix(".raw.txt").write_text(
            proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
        return DispatchResult(
            task.name, None, report.with_suffix(".raw.txt"), rate_limit=rl,
            error="事件流里没有 result 事件"
                  + (f"；额度状态：{rl.summary()}" if rl else "")
                  + f"；stderr：{proc.stderr.strip()[:150]}")
    try:
        receipt = Receipt(**raw_result)
    except Exception as exc:  # result 事件字段不合契约 —— 保留原文供人工排查
        report.with_suffix(".raw.txt").write_text(
            proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
        return DispatchResult(task.name, None, report.with_suffix(".raw.txt"),
                              rate_limit=rl, error=f"回执解析失败：{exc}")

    # ⚠️ 落盘的是**完整事件流**，不只是 result——额度事件、中间消息都在里面，
    #    事后排查「当时额度是什么状态」只能靠它。
    report.write_text(proc.stdout, encoding="utf-8")

    # 核对实际模型 —— 别假定（SPEC.md §5.1）
    mismatch = bool(receipt.models_used) and not receipt.ran_on(cfg.model)
    # ⚠️ 三层判据合成：额度事件（有种类和精确时刻）优先，回执 429 + 文案兜底。
    #    ⛔ 不许只信额度事件——它的 rejected 形态本项目没有实测样本。
    return DispatchResult(task.name, receipt, report, model_mismatch=mismatch,
                          rate_limit=quota.classify(raw_result, rl))
