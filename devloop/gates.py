"""验收闸执行器。

职责分工：`gates.sh`（项目侧）决定**验什么**，本模块决定**怎么验**。

三道防篡改（PLAN.md「闸的自我保护」）——红绿双测只证明闸工作正常，
不证明闸没被改过。工人对 worktree 有写权限，若它改了 gates.sh 使检查跳过，
下一单就执行被改过的闸，闸自己放自己过，无人察觉。
  1. 从仓库外的只读副本执行，worktree 里那份即使被改也不生效
  2. 执行前校验指纹，与登记值不符即拒绝
  3. 改动触及 .devloop/ 即判死（这条在 gates.sh 里）
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import worktree as wt_mod
from .config import ConfigError, ProjectPaths


#  ⭐ 判定的**唯一来源**。⛔ 加第五档时只改这一处。
#
#  ⚠️ 2026-08-02 加 VOID 时它不存在：判定集合散在 `_parse` 的一个元组里，
#  而 `cli.py` 另有一张打印用的符号表 `_GATE_MARK`。两处各写一份 →
#  VOID 加进 `_parse` 却没进符号表 → `_GATE_MARK[l.verdict]` **KeyError**，
#  ⛔ 崩在 `wt.commit_result()` **之前**，工人干完的活直接丢掉。
#  ⚠️ 后来改成 `.get(..., "?")` 只是不崩了——它会静默印 `?`，
#  那是把「表没更新」呈现成「判定不认识」，⭐ 所以还要一条测试钉住两处同步。
VERDICTS = ("PASS", "FAIL", "SKIP", "VOID")


@dataclass
class GateLine:
    #  PASS = 验过且通过 · FAIL = 验过且不通过
    #  SKIP = **本来能验，这次被开关跳过了**（`DEVLOOP_SKIP_TESTS=1` 那种）
    #  VOID = ⛔ **我这一道本来就没有可验的东西**
    #
    #  ⚠️ SKIP 与 VOID 对「能不能放行」含义相同（都不算通过），
    #     但对**排查**含义完全不同：SKIP 要去查谁把开关打开了，
    #     VOID 要去查为什么这个项目里它是空的。所以必须分成两档。
    #
    #  ⛔ VOID 的判据只能在闸自己身上——**只有它知道有没有东西可验**。
    #     外面用启发式（「detail 里没数字就算空过」）会误伤
    #     `基线守卫: ref_*.json 未被改动` 那种真验过的。
    verdict: str  # PASS / FAIL / SKIP / VOID
    name: str
    detail: str


@dataclass
class GateResult:
    code: int  # 0 全过 · 1 有未过 · 2 闸自身故障
    lines: list[GateLine] = field(default_factory=list)
    stderr: str = ""
    #  生效中的跳过开关（操作者环境里的 DEVLOOP_SKIP_*）。⛔ 必须报出来。
    skip_switches: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.code == 0

    @property
    def gate_broken(self) -> bool:
        """闸自己坏了 —— 必须与「活没干好」区分，否则环境故障会被误判成质量问题。"""
        return self.code == 2

    @property
    def verified(self) -> int:
        """真正**验过并通过**的道数。

        ⚠️ 这个数才是「验收有没有发生」的度量，退出码不是。
        `code == 0` 只说明脚本自认为没问题——它可能一条都没验（G-53）。
        """
        return sum(1 for l in self.lines if l.verdict == "PASS")

    def named(self, name: str) -> GateLine | None:
        for l in self.lines:
            if l.name == name:
                return l
        return None

    def summary(self) -> str:
        note = (f"　⚠️ 生效中的跳过开关：{'、'.join(self.skip_switches)}"
                if self.skip_switches else "")
        #  ⛔ **诊断信息不许在故障路径上蒸发。**
        #     此前这里是 `if gate_broken: return ...` 一句短路，于是下面统计
        #     FAIL 明细与 SKIP/VOID 分档的代码在 code==2 时一行都走不到
        #     ——而 `constitution.py::ConstitutionResult.summary` 在 broken 分支
        #     **照样**把已查出的 hits 列出来，注释就写着这句话。
        #  ⚠️ 顺序有讲究：统计必须接在被 `[:300]` 截断的 stderr **之后**，
        #     否则长 stderr 会把统计一起截掉（实测 306 字的场景）。
        head = f"闸自身故障：{self.stderr.strip()[:300]}　" if self.gate_broken else ""
        bad = [l for l in self.lines if l.verdict == "FAIL"]
        s = head + f"{self.verified} 过 / {len(bad)} 未过"
        if bad:
            s += "：" + "；".join(f"{l.name}（{l.detail}）" for l in bad)
        #  ⛔ 空转的两档必须报出来。不报就等于把「没验」呈现成「验过了没事」。
        for tag, why in (("SKIP", "被开关跳过"), ("VOID", "本项目无可验之物")):
            got = [l.name for l in self.lines if l.verdict == tag]
            if got:
                s += f"　⚠️ {tag}（{why}）：{'、'.join(got)}"
        return s + note


def find_bash() -> str:
    """定位 Git Bash —— 不能直接用 "bash"。

    Windows 上裸 `bash` 会被解析到 WSL 的 bash，而 WSL 里没有 Git Bash 的
    /bin/bash，报 `execvpe(/bin/bash) failed`。2026-07-26 实测撞到。
    这就是审计一直标记为「Windows 上 .sh 由谁执行」的那个未决问题的具体形态。
    """
    import os

    if (env := os.environ.get("DEVLOOP_BASH")):
        return env
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    found = shutil.which("bash")
    if found and "System32" not in found:  # System32\bash.exe 是 WSL 的入口
        return found
    raise ConfigError(
        "找不到 Git Bash。裸 `bash` 在 Windows 上会解析到 WSL，无法执行闸脚本。\n"
        "请安装 Git for Windows，或用 DEVLOOP_BASH 指定 bash.exe 路径。"
    )


def fingerprint(path: Path) -> str:
    """计算闸文件 SHA-256 前 16 位。

    为什么存在性检查放在这里而不是调用方：
    dispatch 与 gates 两个子命令都调用本函数，一处修比两处修更不容易漏。
    项目吃过「实现了 ≠ 接上了」的亏——此前 run_gates 自己处理了缺文件，
    但调用方先崩了，那段代码根本走不到。
    """
    if not path.is_file():
        raise ConfigError(
            f"缺 {path}。\n"
            f"写操作需要 gates.sh 做验收闸，但该文件不存在。\n"
            f"接入三步见 SPEC.md §6：建 .devloop/ → 填 gates.sh → 派个只读任务验证。"
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _parse(stdout: str) -> list[GateLine]:
    out = []
    for ln in stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0] in VERDICTS:
            out.append(GateLine(parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return out


def active_skip_switches(env: dict | None = None) -> list[str]:
    """操作者环境里生效中的跳过开关。

    ⛔ `run_gates` 把 `os.environ` 原样透传给闸——这是必要的（闸要用 PATH、
    要用 git 配置），但代价是：**操作者 shell 里一个 `DEVLOOP_SKIP_TESTS=1`
    就能把真实验收变成空转**，而 eco-ob 的闸里真有这个分支
    （`gates.sh:81-82`，注释写着「不得用于真实验收」——但注释对机器没有约束力）。

    ⚠️ 不粗暴剥离：闸自身的快速自测要用它。**但必须报出来**——
    「有跳过开关生效」这件事，人有权在看结果之前知道。
    """
    import os
    e = os.environ if env is None else env
    return sorted(k for k, v in e.items()
                  if k.startswith("DEVLOOP_SKIP") and str(v).strip() not in ("", "0"))


def sync_caches(paths: ProjectPaths, work: Path, *, overwrite: bool) -> list[str]:
    """把被 gitignore 的构建缓存补进干净检出。返回同步了哪几个路径。

    ## ⛔ 为什么要在**工人跑之前**也调一次

    这段原本只藏在 `run_gates` 里，也就是**工人干完活之后**才执行。于是：

        建 worktree（干净检出，没有 game/.godot）
          → 派工人进去干活   ← ⛔ 它手里没有引擎缓存
            → 跑闸（这时才补缓存）

    ⚠️ eco-ob 的 `game/.godot` 有 **110 MB / 901 个条目**。工人在没有它的
    worktree 里跑任何 Godot 命令，都会崩或者极慢——而它多半会把这个当成
    「代码有问题」，然后要么报一个**假失败**，要么去「修」一个不存在的问题。
    ⛔ 两种都比不跑更坏。

    ## ⚠️ `overwrite` 两种用法不能混

    · 工人跑之前 `overwrite=False`：补齐缺的就行，别白白多拷 110 MB
    · 闸跑之前 `overwrite=True`：**无条件重来**。工人只要跑过一次引擎就会留下
      半个缓存，那时「缺了才补」的条件不成立，闸就会拿**工人留下的残缺缓存**
      去做验收——⛔ 验收用的东西不该由被验收方决定。
    """
    if work == paths.project:
        return []
    done = []
    for rel in paths.synced_paths():
        src, dst = paths.project / rel, work / rel
        if not src.exists():
            continue
        if dst.exists():
            if not overwrite:
                continue
            shutil.rmtree(dst, ignore_errors=True) if dst.is_dir() else dst.unlink(True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
        done.append(rel)
    return done


#  ⭐ 闸的硬死线。⛔ **必须是模块常量，不许再当内联默认值。**
#     2026-08-04（G-107）：它当时只以 `timeout_s: int = 1800` 的形式存在，
#     于是没有任何地方能引用它——
#       · 心跳只印了 `expect_s=900`，人看不到真正会杀进程的是 1800；
#       · 计划里的 `max_wall_min = 60` 与「工人 3000 + 闸 1800 = 80 分钟」
#         从来没有被对过账，那个 60 从写下去那天起就兑现不了。
#     ⚠️ 判据要落在**同一个数**上：印出来的死线和真正生效的死线必须同源。
GATE_TIMEOUT_S = 1800


def parse_preflight_states(out: str) -> dict:
    """把体检的原始输出解析成 `{闸名: (OK|BAD|VOID, 说明)}`。

    ⭐ 2026-08-12 加。⛔ 为什么需要它：`preflight()` 只返回**格式化好的字符串**，
    调用方就算想核对「计划点名的那几道闸有没有在体检里露面」也拿不到数据。

    ⚠️ 实测：eco-ob 的体检只打 3 行、真跑打 5 行 ——
    而**闭嘴的那两道，正是零改动时非绿的那两道**（禁改清单 VOID、M1 回归 FAIL）。
    ⛔ 「没打」和「没问题」在 `preflight()` 的判定里长得一模一样：
    `verdict` 只在**有行说 BAD** 时才变 bad，没打出来的闸不产生任何行。
    """
    states: dict[str, tuple[str, str]] = {}
    for raw in (out or "").splitlines():
        raw = raw.strip()
        if not raw.startswith("PREFLIGHT"):
            continue
        parts = raw.split("	")
        if len(parts) > 2:
            states[parts[1]] = (parts[2], parts[3] if len(parts) > 3 else "")
    return states


def preflight(
    paths: "ProjectPaths",
    *,
    base_commit: str = "",
    timeout_s: int = 120,
) -> tuple[bool, list[str], str]:
    """⭐ **开跑前先验判据本身**（G-127）。

    返回 `(判定, 每道闸的一行说明, 原始输出)`，判定 ∈ `"ok"` / `"bad"` / `"unknown"`。

    ⛔ **三态，不是两态** —— 这是 2026-08-09 当场交的学费：
    第一版把「闸还没声明支持体检」也当成拒绝理由，⚠️ 结果**一单都派不出去**
    （10 条回归当场变红）。⭐ 而那会逼所有现有项目去**关掉守卫** ——
    正是本项目最不想要的那种结局。

    | 判定 | 什么意思 | 调用方该怎么办 |
    |---|---|---|
    | `ok` | ⭐ 每道闸都说自己的前提成立 | 放行 |
    | `bad` | ⛔ **有闸明说自己的前提不成立**（例如存档配不上这份代码） | **拒绝派单** |
    | `unknown` | ⚠️ 闸没声明支持体检 / 声明了却一行没打 | ⭐ **大声喊出来，但放行** |

    ⚠️ `unknown` 借的是本项目既有的 **VOID（无可验之物）** 语义：
    ⛔ 「没人验过」不许读成「验过了没问题」，⭐ 但它也不等于「有问题」。

    ## ⛔ 这个函数存在的理由（拓扑上的一个洞）

    工具的防线全在**花钱之前**拦：宪法的锚、作业格式校验、额度闸……
    ⛔ **唯独「判据本身对不对」要花完钱才知道** —— 因为它写在闸里，
    而闸只在工人干完之后才跑。

    ⚠️ 而判据恰恰是最容易写错的那一样：翻遍历史，
    **工人从来不是瓶颈**，每一次失败都追到「判据或作业写错了」。

    ⭐ 2026-08-08 实测代价：一道**数学上必然红**的闸（对照存档比派单基准
    旧 7 个提交，中间还隔着一次故意改变世界的改动）烧掉 **5,146,226 tokens**、
    产出 0 个文件，⛔ 而且把一个 42 秒就改对了的工人逼得**删掉自己正确的代码**。

    ## 契约

    闸脚本收到 `DEVLOOP_PREFLIGHT=1` 时：
    ⛔ **不许跑任何要几十分钟的大考**，只检查自己的前提
    （对照存档在不在、配不配得上这份代码、该有的输入齐不齐），
    每道闸打一行 `PREFLIGHT<TAB>闸名<TAB>OK|BAD|VOID<TAB>说明`，然后**立即退出**。

    ⚠️ 闸脚本**不认识**这个开关时会照常跑全套 —— 那正是为什么这里给了
    `timeout_s=120` 的小死线：⛔ 超时 = 它没实现体检模式，按「答不上来」处理，
    **不许当成通过**。
    """
    import os
    import shutil
    import subprocess
    import tempfile

    if not paths.gates.exists():
        return "bad", [f"⛔ 缺 {paths.gates}"], ""

    #  ⛔⛔ **先看它声不声明支持体检，不声明就一行都不跑。**
    #
    #  ⚠️ 第一版直接跑，靠 `timeout_s=120` 兜底 —— **当场撞上两个坑**：
    #     ① 不认识开关的闸会照常跑全套（eco-ob 实测 26 分钟）；
    #     ② `subprocess` 的 timeout 杀得掉 bash，⛔ **杀不掉 bash 起的 godot**
    #        —— 孙进程攥着管道不放，于是整个调用**挂死**，超时形同虚设。
    #
    #  ⭐ 第一性：体检必须**构造上就便宜**，⛔ 不能靠一个超时把昂贵变便宜。
    #     所以判据落在**一行声明**上：闸脚本自己写明它支持体检，才跑它。
    #  ⚠️ 声明不算保证（它可能声明了却没实现），⛔ 所以超时那道兜底仍然留着。
    marker = "DEVLOOP-PREFLIGHT: 1"
    try:
        if marker not in paths.gates.read_text(encoding="utf-8", errors="replace"):
            return "unknown", [f"⚠️ 这个项目的闸没声明支持体检（脚本里找不到 `{marker}`）"
                               "——⛔ 本单的判据**没有被验过**，别读成通过"], ""
    except OSError as exc:
        return "bad", [f"⛔ 读不了闸脚本：{exc}"], ""

    with tempfile.TemporaryDirectory(prefix="devloop-preflight-") as tmp:
        safe = Path(tmp) / "gates.sh"
        shutil.copy2(paths.gates, safe)
        env = {**os.environ,
               "DEVLOOP_PREFLIGHT": "1",
               "DEVLOOP_BASE_COMMIT": base_commit or ""}
        try:
            proc = subprocess.run(
                [find_bash(), str(safe), str(paths.project)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return "unknown", ["⚠️ 闸不认识体检模式（跑超时了）——⛔ 判据没被验过，别读成通过"], ""
        except (FileNotFoundError, ConfigError) as exc:
            return "bad", [f"⛔ {exc}"], ""

    out = proc.stdout or ""
    lines, verdict = [], "ok"
    for raw in out.splitlines():
        if not raw.startswith("PREFLIGHT"):
            continue
        parts = raw.split("	")
        name = parts[1] if len(parts) > 1 else "?"
        state = parts[2] if len(parts) > 2 else "?"
        why = parts[3] if len(parts) > 3 else ""
        mark = {"OK": "⭐", "BAD": "⛔", "VOID": "⚠️"}.get(state, "⚠️")
        lines.append(f"{mark} {name}：{why}")
        if state == "BAD":
            verdict = "bad"
        elif state != "OK" and verdict == "ok":
            #  ⚠️ 闸自己报 VOID = 它验不了（不是「验了没过」）——按「答不上来」处理
            verdict = "unknown"
    if not lines:
        #  ⛔ 一行都没打 = 这个项目的闸没实现体检模式。
        #  ⚠️ 这**不是**「没问题」——是「没人验过」。判它不通过，由调用方决定要不要放行。
        return "unknown", ["⚠️ 声明了支持体检、却一行都没打——⛔ 本单的判据"
                           "**没有被验过**，别读成通过"], out
    return verdict, lines, out


def run_gates(
    paths: ProjectPaths,
    *,
    target: Path | None = None,
    expect_fingerprint: str | None = None,
    timeout_s: int = GATE_TIMEOUT_S,
    clean_checkout: bool = False,
    require_pass: list[str] | None = None,
    base_commit: str = "",
) -> GateResult:
    """执行闸。

    target 默认为项目自身；传入 worktree 路径则检查该 worktree。
    闸脚本**总是从仓库外的只读副本执行**——这是防篡改第 1 道。

    `require_pass` 点名哪几道闸**必须是 PASS**。不给则只按退出码与「验过几道」判。
    ⚠️ 点名是必要的：即便修掉「空转即绿」，一个 5 道闸里 4 道 SKIP、1 道 PASS
    的结果仍然是「绿」——那正是 G-42 那种假绿（自检存在、判据正确，
    **但只覆盖了一部分输入**）。自动驾驶的验收必须点名。
    """
    if not paths.gates.exists():
        return GateResult(2, stderr=f"缺 {paths.gates}")

    actual = fingerprint(paths.gates)
    if expect_fingerprint and actual != expect_fingerprint:
        return GateResult(
            2,
            stderr=f"闸指纹不符：登记 {expect_fingerprint}，实际 {actual}。"
                   f"闸文件被改动过——在查明原因之前拒绝执行。",
        )

    work = (target or paths.project).resolve()

    # ⛔ 闸跑之前**无条件**重新同步一次，不管 worktree 里已经有什么。
    #    ⚠️ 原来的条件是 `not dst.exists()`——只在缺失时补。可是工人只要跑过
    #    一次引擎（哪怕被 max_turns 或超时打断、只留下半个缓存），条件就不成立，
    #    于是**闸拿工人那个残缺缓存去做验收**。
    #    ⛔ 验收用的缓存不该由被验收方决定。这是「测了错的东西」的一种形态。
    sync_caches(paths, work, overwrite=True)

    # 防篡改第 1 道：拷到仓库外的临时目录执行
    with tempfile.TemporaryDirectory(prefix="devloop-gate-") as tmp:
        safe = Path(tmp) / "gates.sh"
        shutil.copy2(paths.gates, safe)
        try:
            import os
            #  ⭐ `DEVLOOP_BASE_COMMIT` = 这一单的**基准提交**（2026-08-09 加）。
            #
            #  ⛔ 它存在的唯一理由是一类**必然假红**：
            #     闸拿「上一版跑出来的存档」当对照（eco-ob 的「逐比特中性」就是这种），
            #     ⚠️ 而那份存档是在**某个提交**上录的。基准一旦往前走过、
            #     中间又动过被测代码，那道闸**数学上必然红**——工人做什么都过不了。
            #
            #  ⭐ 2026-08-08 实测撞到：存档录在 5e746f1，派单基准是 7 个提交之后的
            #     f7636381ab35，中间隔着一次**故意改变世界**的改动。
            #     工人 42 秒就改对了，看见红以为是自己错，**把对的代码删了**，
            #     然后花 49 分钟追一个不存在的 bug，超时交白卷（烧掉 514 万 tokens）。
            #
            #  ⛔ 闸自己**算不出**这个数：它只拿得到工作目录，而工作目录的 HEAD
            #     已经带上了工人的提交。⭐ **知道基准的是编排方，所以由编排方传。**
            #  ⚠️ 空字符串 = 编排方没给（手工跑闸时就是这样）——
            #     ⛔ 项目侧的闸遇到空值必须报 VOID，**不许当 PASS**。
            env = {**os.environ,
                   "DEVLOOP_CLEAN_CHECKOUT": "1" if clean_checkout else "0",
                   "DEVLOOP_BASE_COMMIT": base_commit or ""}
            proc = subprocess.run(
                [find_bash(), str(safe), str(work)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_s, env=env,
            )
        except subprocess.TimeoutExpired:
            return GateResult(2, stderr=f"闸执行超时（>{timeout_s}s）")
        except (FileNotFoundError, ConfigError) as exc:
            return GateResult(2, stderr=str(exc))

    # ⚠️ 退出码必须归一化到协议里的 0/1/2。
    #    此前直接透传 returncode，于是任何**协议外**的退出码（脚本语法错、
    #    `set -u` 撞未赋值变量的 127、被信号杀死的 128+N）都会落进
    #    `passed=False` 且 `gate_broken=False` —— 报出来是「质量不合格，
    #    但一道失败的闸都没有」，把**闸坏了**说成**活没干好**。
    #    而 SPEC §5.2 写得明明白白：1 与 2 必须区分，否则环境故障会被当成质量问题。
    #    2026-07-26 实证：eco-ob 的闸引用未赋值变量，测试一失败就 127（BACKLOG G-36）。
    #    协议外的码一律按 2 处理——「我不认识这个结果」本身就是闸故障。
    code = proc.returncode
    lines = _parse(proc.stdout)
    switches = active_skip_switches()
    if code not in (0, 1, 2):
        return GateResult(
            2, lines,
            f"闸以协议外的退出码 {code} 结束（协议只认 0/1/2）。"
            f"常见来源：脚本语法错、set -u 撞未赋值变量、被信号杀死。"
            f"原始 stderr：{proc.stderr.strip()[:400]}", switches)

    passed_names = {l.name for l in lines if l.verdict == "PASS"}

    # ⛔ G-53：**退出 0 但一道都没通过 = 什么都没验，不是「全过」。**
    #    实测两种形态都曾报 passed=True：全 SKIP、以及脚本什么都不打印就 exit 0。
    #    这是六种假绿里的第一种（守卫的目标不存在），只是更彻底——
    #    **一个目标都没有，还报全过**。归为 2：闸不可用于验收，不是活没干好。
    if code == 0 and not passed_names:
        # ⚠️ 说辞要诚实。早先这里印「一道都没验」并附三条**猜**的原因——
        #    而 eco-ob 有一条合法分支会长成这样：测试真跑了 486 秒、38 条断言
        #    全过，只有墙钟性能断言没达标，闸于是 `skip "M1 回归"`。
        #    那时「一道都没验」是假话，三条猜测也全不成立。
        # ⛔ **判定不放宽**：闸自己那行 SKIP 的原话就是「非工人问题，
        #    **但也未证明代码正确**」——没证明就不能放行，这个结论是对的。
        #    要改的是解释：**把闸自己说的话摆出来**，不要猜。
        said = "；".join(f"{l.verdict} {l.name}（{l.detail}）" for l in lines) or "一行输出都没有"
        return GateResult(
            2, lines,
            f"闸以 0 退出，但**没有任何一道闸通过**（解析到 {len(lines)} 行，"
            f"其中 PASS 0 行）。退出 0 的含义是「全都验过且都过了」，"
            f"而这里没有任何一项被证明——拿它当验收就是假绿。\n"
            f"   闸自己说的是：{said}\n"
            f"   ⚠️ 这不一定是「什么都没跑」——也可能跑了但结论是 SKIP"
            f"（未证明 ≠ 已证明）。无论哪种，都不足以放行。"
            + (f"\n   原始 stderr：{proc.stderr.strip()[:200]}" if proc.stderr.strip() else ""),
            switches)

    # ⛔ **闸自相矛盾：印了 FAIL 却 exit 0 —— 不许放行。**
    #
    #    2026-08-02 实测（改前，且**不点名时最危险**）：
    #      `PASS 语法` + `FAIL pytest` + `exit 0`
    #      → passed=True · gate_broken=False · summary「1 过 / **1 未过**」
    #    ⛔ 判定与它自己的摘要自相矛盾，而结论是**放行**，坏产出直接流下去。
    #    ⚠️ 上面那条 G-53（`code==0 且零 PASS`）只挡住「一道都没过」，
    #    ⭐ 挡不住「有过的也有没过的，却整体 exit 0」——它更隐蔽，因为
    #    `verified` 是正数，看起来「验收确实发生了」。
    #
    #    归 2 而不是 1，与本文件 `code not in (0,1,2)` 那段同源：契约写着
    #    `0 = 全过`，印了 FAIL 还 exit 0 **就是协议违规**，「我不认识这个结果」
    #    本身就是闸故障。⚠️ 诊断方向也对——这类脚本的病因几乎总是 `bad()`
    #    在子 shell 或管道里 `fail=1` 没传出来，要修的确实是闸不是活。
    #
    # ⛔ 必须排在 `require_pass` **之前**：「这个闸的结论能不能用」比
    #    「点名的那几道过没过」更靠前，点名与否不该改变它。
    # ⛔ **镜像的另一半：`exit 1` 却一行 FAIL 都没印。**
    #
    #    实测 `PASS 语法` + `PASS 别的` + `exit 1` → code 1 · summary「2 过 / 0 未过」，
    #    而归类给出的建议是「读上面闸的结论，看是哪一道没过」——⛔ 一道都没有。
    #    ⚠️ 后果比看起来重：`retries` 会拿这个没有内容的失败反复重试，
    #    每次烧一份额度去修一个闸没说哪里错的东西。
    #
    #    与下面那条（`exit 0` 却印了 FAIL）同源：契约写着 `1 = 有闸未过`，
    #    没有任何一行说自己没过，这个退出码就不可信。
    #
    # ⚠️ **`SKIP`/`VOID` + `exit 0` 不在此列，那是设计如此。**
    #    `skip()` 故意不置 `fail=1`：SKIP 的含义是「**没验**」，不是「验了没过」，
    #    与 `exit 0`（没有东西失败）自洽。⭐ 放不放行是 `require_pass` 的政策问题
    #    ——点名了就落 code 2 交人。⛔ 别顺手把它也改成矛盾，那会把
    #    「本项目这道闸不适用」变成「闸坏了」。
    if code == 1 and not any(l.verdict == "FAIL" for l in lines):
        said = "；".join(f"{l.verdict} {l.name}" for l in lines) or "一行输出都没有"
        return GateResult(
            2, lines,
            f"闸**自相矛盾**：以 1（有闸未过）退出，却**一行 FAIL 都没印**。"
            f"闸自己说的是：{said}。"
            f"⛔ 没有任何一道声明自己没过，这个退出码就无从解释；"
            f"⚠️ 照 1 处理会让重试拿一个没有内容的失败反复烧额度。"
            f"常见病因：`set -e` 下某条命令非零退出把整脚本带走，"
            f"或 `exit $fail` 前有别的命令改了 `$?`。先修闸。", switches)

    if code == 0 and (bad0 := [l for l in lines if l.verdict == "FAIL"]):
        return GateResult(
            2, lines,
            f"闸**自相矛盾**：以 0（全过）退出，却自己印了 "
            f"{len(bad0)} 道 FAIL——{'；'.join(f'{l.name}（{l.detail}）' for l in bad0)}。"
            f"⛔ 契约写的是「0 = 全都验过且都过了」，所以这个退出码不可信；"
            f"而 FAIL 那几行又明说有东西没过。两个信号打架时**不放行**。"
            f"⚠️ 常见病因：`bad()` 在子 shell 或管道里执行，`fail=1` 没传回主 shell"
            f"（`printf ... | while read` 这种写法）。先修闸，再谈验收。", switches)

    if require_pass:
        seen = {l.name for l in lines}
        missing = [n for n in require_pass if n not in seen]
        if missing:
            # ⛔ 点名了一道**根本不存在**的闸 —— 守卫的目标不存在（第一种假绿）。
            #    若只检查「没有 FAIL」，这里会绿，而实际上那道闸压根没跑过。
            return GateResult(
                2, lines,
                f"验收点名了这几道闸，但闸的输出里**根本没有它们**："
                f"{'、'.join(missing)}。闸与验收契约对不上——在查明之前拒绝放行。"
                f"（闸实际报出的：{'、'.join(sorted(seen)) or '一行都没有'}）", switches)
        not_pass = [n for n in require_pass if n not in passed_names]
        if not_pass:
            by = {n: next(x for x in lines if x.name == n) for n in not_pass}
            detail = "；".join(
                f"{n}（{by[n].verdict}{'：' + by[n].detail if by[n].detail else ''}）"
                for n in not_pass)
            # ⛔ **按诊断方向分岔，不是一律判 2**（2026-08-02 审计 H-2）。
            #
            #    此前这里一律 2。而 `GateResult.code` 自己的注释写着
            #    `0 全过 · 1 有未过 · 2 闸自身故障`，`gate_broken` 的 docstring
            #    更明说「必须与「活没干好」区分」。实测对照：**同一个 FAIL，
            #    点名了判 2、不点名判 1**——⚠️「被点名」不该改变事实的性质。
            #
            #    为什么当初会混：写这段时的关注点是「点名一道恒过/空转的闸等于
            #    没点名」（eco-ob 首跑那条 VOID）。⭐ 那个场景里 not_pass 全是
            #    SKIP/VOID，判 2 完全正确；FAIL 是顺手被卷进去的，当时没有反例。
            #    下面那句「SKIP 与 VOID 都不是通过」自己就承认了这一点。
            #
            # | 档 | 事实 | 谁能修 | 重试有没有用 | 码 |
            # |---|---|---|---|---|
            # | FAIL | 闸验了，判否 | 工人 | **有**，那正是 retries 的用处 | 1 |
            # | SKIP | 被开关跳过 | 人 | ⛔ 无：开关在环境里，重试 N 次撞 N 次同一堵墙 | 2 |
            # | VOID | 无可验之物 | 人 | ⛔ 无：守卫的目标不存在，契约对不上 | 2 |
            # | 混合 | 一部分验收压根没跑 | 人先工人后 | ⛔ 不能只说活没干好 | 2 |
            #
            # ⚠️ 混合取 2 是因为**代价不对称**：判 1 的代价是自动驾驶去重试一个
            #    结构上不可能变绿的东西（真花钱），判 2 的代价是叫人多看一眼。
            # ⚠️ 这不是放宽——`passed` 两档都是 False，`verified` 不变，
            #    「能不能放行」一个字没变。变的只有诊断方向。
            vacuous = [n for n in not_pass if by[n].verdict in ("SKIP", "VOID")]
            if not vacuous:
                #  ⛔ 码硬写 1，不许透传 `proc.returncode`：实测存在「打印 FAIL
                #     却 exit 0」的自相矛盾脚本，透传会把它变成 0。
                #  ⚠️ 说辞里最好别出现「闸自身故障」四个字。
                #     ⛔ 但**别把这条当保证**：`escalation` 的归类曾经只靠这四个字
                #     分岔，而 2026-08-02 实测它会被**测试名劫持**——本仓 gates.sh
                #     把 pytest 的失败节点名原样放进 FAIL 的 detail，
                #     一个叫 `test_闸自身故障要归到闸自身故障` 的测试挂掉就够了。
                #     ⭐ 真正的收口是台账里的结构化 `gate_code`（见 telemetry.record），
                #     这条文本纪律现在只是历史行的退路。
                return GateResult(
                    1, lines,
                    f"验收点名的闸未通过：{detail}。"
                    f"⚠️ 这几道**真验过**并判定不通过——是活没达标，闸本身是好的。",
                    switches)
            n_fail = len(not_pass) - len(vacuous)
            return GateResult(
                2, lines,
                f"验收点名的闸没有全部通过：{detail}。"
                + (f"⚠️ 其中 {n_fail} 道是 FAIL（活确实没达标），"
                   f"但另有 {len(vacuous)} 道**根本没验到**，"
                   f"先修后者——重试改不动它。" if n_fail else "")
                + f"⚠️ SKIP（被开关跳过）与 VOID（本项目无可验之物）都不是通过"
                f"——被点名的检查没有真正验到东西，就无从放行。"
                f" ⛔ 点名一道恒过的闸等于没点名"
                f"（2026-08-01 eco-ob 首跑踩到：`禁改清单` 在 worktree 内"
                f"无 .devloop/ 时报 PASS，实际验了 0 条）。", switches)

    return GateResult(code, lines, proc.stderr, switches)


def run_on_commit(paths: ProjectPaths, commit: str = "HEAD", **kw) -> GateResult:
    """把指定提交检出到一次性 worktree 再验。

    为什么不直接验工作区：工人可能留下未暂存的文件，提交能通过所有本地检查，
    却引用了一个从未进入提交的文件——本地看得见，CI 看不见。
    **工作目录会撒谎，提交不会。**
    """
    with tempfile.TemporaryDirectory(prefix="devloop-wt-") as tmp:
        wt = Path(tmp) / "tree"
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), commit],
            cwd=paths.project, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if add.returncode != 0:
            return GateResult(2, stderr=f"建 worktree 失败：{add.stderr.strip()[:300]}")
        try:
            return run_gates(paths, target=wt, clean_checkout=True, **kw)
        finally:
            #  ⛔⛔ 2026-08-22：这里以前是 `capture_output=True` 且不查返回码。
            #     原注释写着「Windows 上文件可能被占用，清理失败不应掩盖闸的结论」——
            #     ⭐ 前半句对（不该抛异常），⛔ **后半句被执行成了「一个字都不说」**。
            #
            #  ⚠️ latch T5：失败若不改变任何可观测输出 ⇒ 必然累积到灾难规模。
            #     实证：2026-08-21 用户 C 盘被撑爆，≈63 个/小时。
            #  ⇒ ⭐ **不掩盖结论 ≠ 不出声。** 喊出来 + 记数，然后照常返回闸的结论。
            p = subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                               cwd=paths.project, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if p.returncode != 0:
                wt_mod.LEAKS.append(str(wt))
                print(f"⚠️ 闸的一次性工位没拆干净：{p.stderr.strip()[:120] or '无错误输出'}\n"
                      f"   路径 {wt}\n"
                      f"   ⛔ 本进程累计漏了 {len(wt_mod.LEAKS)} 个"
                      f"（⚠️ 2026-08-21 实测：漏到 2,983 个时撑爆了 C 盘）。\n"
                      f"   ⭐ 手工清：git -C {paths.project} worktree prune",
                      file=sys.stderr)
