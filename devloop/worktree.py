"""为写操作任务提供隔离的 git worktree。

**为什么工人必须在 worktree 里干活**（两个理由，缺一不可）：

1. **隔离** —— 工人改错了不污染你的工作区，丢弃只需删一个分支。
2. **⭐ 让闸的守卫生效** —— 三道改动守卫靠 `git status` 判断「工人改了什么」。
   只有在某个提交的干净检出里，git 差异才等于工人的改动；在本来就有未提交
   改动的工作区里二者无法区分（Phase 2 绿测实测：会产生三条误伤）。
   worktree 天然是干净检出，**这是守卫能工作的前提，不是可选的便利**。

**提交时机：跑闸之后，且只提交到隔离分支。**

闸必须验工作区，不能验提交——三道守卫靠 `git status --porcelain` 判断「工人改了
什么」，先提交会让工作区变干净，守卫全部退化成空守卫（本项目已两次栽在空守卫上，
见 BACKLOG G-20）。所以顺序是硬的：**先跑闸，后提交**，由 `cli.py` 保证。

跑完闸就必须提交，则是 2026-07-26 的血教训：此前完全不提交，成果只活在 worktree
的未提交工作区里，而派单结束时却打印「分支 …（保留供你审查）」——那个分支上一个
提交都没有。L3 任务的产出（五闸全绿 + 答案卷全过）因此在删 worktree 时**真的丢了**。
记入 BACKLOG G-26。

这不违反「只在用户明确要求时提交」：那条纪律护的是**用户的主线**。这里提交的目标
是 DevLoop 自己造的一次性隔离分支，master 一个字节都不会动，合不合回主线仍然由用户
定夺。不提交的代价是静默丢失产出，那比多一个可随手删掉的分支严重得多。
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import naming

#  ⭐ 本进程里**没拆干净**的工位路径。⛔ 存在的理由不是「记录」，是**让失败可观测**。
#
#  ⚠️ latch T5（2026-08-22 从真实事故推出）：
#     「任何可能失败的操作，若其失败**不改变任何可观测输出**，
#       则该失败**必然**被累积到灾难规模。⛔ 与失败率无关，只与速率是否 > 0 有关。」
#
#  ⛔ 实证就是这一处：清理失败被 `capture_output=True` 吞掉 ⇒ 无人知晓 ⇒ 累积
#     ⇒ 2026-08-21 用户的 C 盘被撑爆（2,983 个工位 ≈26 GB ＋ 60 GB pytest 临时目录）。
#  ⭐ 实测速率 ≈63 个/小时。
LEAKS: list[str] = []
from .config import ConfigError


class BranchHijack(RuntimeError):
    """产出即将落到隔离分支之外（宪法 C-4）。

    ⚠️ 单独一个异常类，是为了让调用方能与「工人没产出」「闸没过」区分开——
    它们三个的正确处置完全不同，混成一个 RuntimeError 就分不开了。
    """


@dataclass
class Worktree:
    path: Path
    branch: str
    project: Path

    def changed_files(self) -> list[str]:
        p = subprocess.run(["git", "status", "--porcelain"], cwd=self.path,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        return [ln[3:].strip() for ln in p.stdout.splitlines() if ln.strip()]

    def changed_since(self, base: str) -> list[str]:
        """这条隔离分支相对基点改了哪些文件。

        ⭐ 2026-08-15 加。⛔ 为什么不能用 `changed_files()`：
        那一个问的是「**工作区里还没提交的改动**」（`git status --porcelain`），
        ⚠️ 而卷宗是在 `commit_result()` **之后**写的 —— 那时工作区已经干净，
        于是它对**每一单**都报「实际改了 0 个」。

        ⭐ 实测（2026-08-15，盘上现成的 f3 那条分支）：
        `git status --porcelain` → **0 行**；
        `git diff --name-only 基点..HEAD` → **`game/data/species.json`**。
        ⇒ 那一单工人真把 `graze_max` 0.18→0.72、`herbivore_efficiency` 0.35→0.0875
        改好并提交了（回执 `subtype=success · 26 轮 · 38 分钟`），
        ⛔ 而卷宗白纸黑字写着「实际改了 0 个：⚠️ 一个都没有」。

        ⚠️ 判据的维度错了：**提交前该问工作区，提交后必须问提交。**
        """
        if not base:
            return []
        #  ⛔ `-z` 不是可有可无：不加它，git 对**非 ASCII 文件名**会加引号并转成
        #     八进制转义（`"game/\344\270\255.gd"`）。那串东西照原样进卷宗是乱码，
        #     ⚠️ 而且拿去跟声明的路径比会判成**越界**——一个凭空造出来的假红。
        #  ⭐ `-z` 让 git 原样吐路径、用 NUL 分隔 ⇒ 引号和转义都不再存在。
        p = subprocess.run(["git", "diff", "--name-only", "-z", f"{base}..HEAD"],
                           cwd=self.path, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            #  ⛔ 拿不到就返回 None 的语义（空列表会被读成「一个都没改」），
            #     ⚠️ 由调用方决定怎么说 —— 这里用抛的，让上层的 try 接住。
            raise RuntimeError(f"git diff {base}..HEAD 失败：{p.stderr.strip()[:120]}")
        return [x for x in p.stdout.split("\0") if x.strip()]

    def commit_result(self, task_name: str, *, gate_ok: bool | None) -> str | None:
        """把工人的改动固化到本 worktree 的隔离分支上，返回提交 SHA。

        ⚠️ **必须在跑完闸之后调用**——闸靠工作区差异识别工人改动，先提交等于
        把三道守卫全部变成空守卫。调用顺序由 `cli.py::cmd_dispatch` 保证。

        闸没过也照样提交：失败的尝试同样是证据，重试时要拿它作对照；何况
        「没过」有时是闸自身故障（退出码 2），把产出扔掉等于把排查线索一起扔掉。
        提交信息里写明闸的结论，免得日后误把红的当绿的。

        工人没改任何东西时返回 None——不造空提交。
        """
        def git(*a: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *a], cwd=self.path, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

        # ⛔ **提交前必须确认 HEAD 就是本 worktree 的隔离分支**（宪法 C-4）。
        #
        #   `git commit` 落到哪个分支完全取决于此刻的 HEAD，而 `self.branch`
        #   只是建 worktree 时记下的一个字符串。下面那句提交信息宣称
        #   「仅落在隔离分支 …，未触碰主线」——**在加这道断言之前，那句话
        #   是工具在替自己作证，没有任何东西保证它为真。**
        #
        #   实测（临时仓库 + 真 commit_result，2026-07-28）：工人在 worktree 里跑
        #   `git symbolic-ref HEAD refs/heads/master`（⚠️ 比 `git checkout master`
        #   强得多——主 worktree 占着 master 时它照样成功，且一个文件都不动），
        #   随后本函数把产出提交到了 **master**：
        #       master 1b17820 → 8a56130 ，而隔离分支仍停在 1b17820（空）
        #   CLI 据此打印「分支 devloop/… @ 8a56130（产出已固化）」——一句假话，
        #   且 `prune` 会把那个空分支判成可删。见 BACKLOG G-54。
        #
        # ⛔ 断言放在**本函数里**，不放调用方：放 cli.py 只护得住一条调用路径，
        #    测试、`gates --commit`、将来的自动驾驶全都绕得过去。
        head = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if head != self.branch:
            # ⛔ 绝不 `return None`：那会让 CLI 打印「工人没有产出，无可固化」，
            #    而工人明明有产出——G-26（产出静默丢失）+ 静默降级，双重复发。
            raise BranchHijack(
                f"⛔ 拒绝提交：本 worktree 的 HEAD 是 `{head}`，"
                f"不是它自己的隔离分支 `{self.branch}`。\n"
                f"   提交下去就会落到 `{head}` 上——那是用户的分支，"
                f"宪法 C-4 规定任何触及主线的提交都要人批准。\n"
                f"   ⚠️ **工人的产出未丢失**，仍在 {self.path}。")

        if not self.changed_files():
            return None

        verdict = {True: "闸全绿", False: "闸未过", None: "未跑闸"}[gate_ok]
        msg = (f"work({task_name}): 工人产出 · {verdict}\n\n"
               f"由 DevLoop 编排方代为提交，仅落在隔离分支 {self.branch}，未触碰主线。\n"
               f"（提交前已断言 HEAD == 该分支——见本函数的 C-4 断言。）\n"
               f"提交发生在跑闸之后——闸验的是工作区，先提交会让守卫失效。")

        # 身份显式写死，不继承用户的 git 身份：日后 `git log` 一眼看得出
        # 这行代码是机器写的还是人写的，这在追责和复盘时是必须区分的。
        #
        # ⛔ **必须用 `-c` 每命令注入，绝不能 `git config` 写进去。**
        #    linked worktree 与主仓库**共用 `.git/config`**（除非开了
        #    `extensions.worktreeConfig`，而它默认没开）。所以在 worktree 里跑
        #    `git config user.name` 是**改主仓库的身份**，而且改完不会还原。
        #
        #    实测后果（2026-07-29 审计抓到）：主仓库 `.git/config` 的 user.name
        #    被写成 `devloop-worker[sub-smoke]`、user.email 写成
        #    `worker@devloop.local`，此后**维护者亲手写的 24 个提交**全部挂上了
        #    工人身份。于是：
        #      · 这个函数存在的**唯一理由**（一眼分清人和机器）反过来被它自己毁掉
        #      · `prune.py` 用 `--author=worker@devloop.local` 判分支空不空，
        #        而 master 尖端就带这个邮箱 → 今后从 master 开的**每个**隔离分支
        #        都会被判成「有未合并产出，别删」，永久失效
        #
        #    `-c` 只作用于这一条命令，不落盘、不污染、也不依赖 worktreeConfig。
        git("add", "-A")
        r = git("-c", "user.email=worker@devloop.local",
                "-c", f"user.name=devloop-worker[{task_name}]",
                "commit", "-q", "-m", msg)
        if r.returncode != 0:
            return None
        return git("rev-parse", "HEAD").stdout.strip()[:12] or None

    def remove(self) -> None:
        """拆掉这个工位。⛔ **拆不掉必须喊出来。**

        ## ⛔⛔ 2026-08-22：这里以前两句都是 `capture_output=True` 且不查返回码

        ⇒ Windows 上文件被占用时删除失败，⛔ **而没有任何输出**。
        ⚠️ 实测代价：`%TEMP%` 下堆了 **2,983 个**没删掉的工位（≈26 GB），
        ＋ pytest 临时目录 **60 GB / 一千万个以上文件**，⛔ 直到用户的 C 盘被撑爆。

        ⭐ 而这条属于一个**可推导**的物种（latch T5）：

        > **任何可能失败的操作，若其失败不改变任何可观测输出，
        > 则该失败必然被累积到灾难规模。**
        > ⛔ 这不取决于失败率高低，只取决于速率是否 > 0。

        ⚠️ **实测速率：≈63 个/小时。** 速率 > 0 ⇒ 必然到达灾难阈值。

        ⛔ **所以这里不许静默。** 但也**不许抛异常**——拆工位是收尾动作，
        ⭐ 它失败不该掩盖闸的结论（那是另一个方向的错）。
        ⇒ **喊出来 + 记数，然后继续。**
        """
        leaked = []
        for what, args in (("工位", ["worktree", "remove", "--force", str(self.path)]),
                           ("分支", ["branch", "-D", self.branch])):
            p = subprocess.run(["git", *args], cwd=self.project,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if p.returncode != 0:
                leaked.append(f"{what}（{p.stderr.strip()[:120] or '无错误输出'}）")
        if leaked:
            LEAKS.append(str(self.path))
            print(f"⚠️ 工位没拆干净：{'；'.join(leaked)}\n"
                  f"   路径 {self.path}\n"
                  f"   ⛔ 本进程累计漏了 {len(LEAKS)} 个 —— 漏够多了会撑爆磁盘"
                  f"（2026-08-21 实测：2,983 个 ≈ 26 GB）。\n"
                  f"   ⭐ 手工清：git -C {self.project} worktree prune",
                  file=sys.stderr)


def snapshot_base(project: Path) -> str:
    """把当前工作区（含未提交改动）固化成一个提交对象，返回其 SHA。

    **为什么需要**：worktree 只能从提交检出，于是闸默认验的是 HEAD。若开发者
    手头有未提交的在途改动——而那些改动恰好是让测试转绿的——闸就会永远红，
    且红的原因与工人无关。2026-07-26 实测：eco-ob 的 HEAD 是 23 通过 12 失败，
    而工作区是 38 通过 0 失败，差别正是 17 个文件的未提交改动。

    `git stash create` 只**造一个提交对象**，不修改工作区、不进 stash 栈、
    不进任何分支历史——因此不违反「只在用户明确要求时提交」这条纪律。
    工作区干净时它返回空，此时退回 HEAD。
    """
    r = subprocess.run(["git", "stash", "create"], cwd=project,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    sha = r.stdout.strip()
    return sha if sha else "HEAD"


def resolve_base(project: Path) -> str:
    """把「本批的基准」解析成一个**具体的 40 位 sha**。

    ⛔ 绝不返回字面量 `"HEAD"`。宪法的树内判据拒绝 HEAD——工人自提交正是
    它要抓的动作，用 HEAD 当锚等于攻击成功时锚自己也跟着移动。

    ⚠️ 也不能简单用 `rev-parse HEAD`：工作区可能有**在途改动**，那些不是工人
    干的，拿 HEAD 当锚会把它们算到工人头上（与 G-12 同源）。所以先取工作区快照。

    ⚠️ **这个函数存在的唯一理由是「只许有一处实现」。** 2026-07-28 的教训：
    同一段解析逻辑在 `cmd_autopilot` 和 `cmd_dispatch` 里各写了一遍，
    我只修了前一处——于是干净工作区 + 已启用宪法的项目上，
    **每一单写任务都被判成宪法故障**（干得好的活被判失败）。
    """
    base = snapshot_base(project)
    if base == "HEAD":              # 工作区干净时 snapshot_base 退回字面量
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout.strip()
    if not base:
        raise ConfigError(
            f"取不到 {project} 的基准提交——这是个 git 仓库吗？"
            f"（宪法的树内判据必须锚在一个具体提交上）")
    return base


#  留这么多余量。⚠️ 磁盘写满不是「少一个 worktree」——
#  `snapshot_base` 会造提交对象，git 写不下去时仓库可能进入奇怪状态。
#
#  ⭐ 改完之后它承担的责任更重了：**它是唯一一条不依赖任何估算的底线。**
#  「我不知道它多大，但少于 500 MB 空闲一律不开工」是站得住的判断；
#  ⛔ 而「它大约 200 MB」不是——那正是本次删掉的那个写死常数。
_HEADROOM_MB = 500

#  一次量多少条目就放弃。⚠️ 数的是 rglob 的条目（含目录），不只是文件。
#  实测：eco-ob 项目目录 5785 条 / 0.11s；一个真 worktree 约 3500 条。
#  ⛔ 而 同机另外三个项目目录都 >20000——
#     它们会撞这条上限，所以「撞上限」必须是一个**可区分**的结果，不是 0。
_CAP_ENTRIES = 20000


def _dir_size_mb(p: Path, cap_entries: int = _CAP_ENTRIES) -> float | None:
    """粗略量一个目录多大（MB）。⚠️ 扫太多条目就放弃，别让检查本身变成负担。

    ⛔ **量不出来返回 `None`，不是 `-1.0`。**
    哨兵值只有在调用方**显式区分它**时才是哨兵，否则它就是个负数——
    而调用方写的正是 `if s > 0`，于是 `-1.0`（量不出来）和 `0.0`（真的空）
    走了同一条路，一起掉进那个写死的常数。⭐ 换成 None 之后
    `0.0` 从此**只**表示「真的空」，两种事实再也压不到一起。
    """
    #  ⛔ `rglob` 对不存在的目录**不抛异常，只是不产出**——不先查一下的话
    #     它会返回 `0.0`（「真的空」），而事实是「量不出来」。
    #     ⚠️ 那正是本函数这次要消灭的那种混淆，别在入口处又造一个。
    if not p.is_dir():
        return None
    total = n = 0
    try:
        for f in p.rglob("*"):
            n += 1
            if n > cap_entries:
                return None
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        return None                          # 目录本身读不动
    return total / 1e6


def _checkout_size_mb(project: Path) -> float | None:
    """按 **checkout** 估一个新 worktree 要占多少盘，⛔ 不是量项目目录多大。

    ⚠️ 二者的差 = `.git` + 所有 gitignored 产物 − `gate_sync` 会拷回去的那部分。
    实测这个差在 eco-ob 上是 2 倍（610 MB 目录 vs 251 MB checkout），
    在一个依赖目录很大的项目上是 2400 倍。⛔ 拿项目目录估等于估错了对象。

    量两部分：
      · `git ls-files` 的字节数 —— worktree 真正会检出的东西
      · `ProjectPaths.synced_paths()` 里那几个 gitignored 目录 ——
        ⭐ 那正是 `gates.py::sync_caches` 待会儿要拷进去的

    实测：eco-ob `146.4 + 110.0 = 256.4 MB`（真实 251→361，误差 2%）；
    devloop `4.3 MB`（实测壳 3.2–5.9）。耗时 0.03–0.13s，比 rglob 还快。

    ⛔ tracked 部分为 0 时返回 `None` 而不是 0.0：那说明这不是个能量的 git 仓库，
    ⚠️ **不许把它当成「0 MB 的项目」**。一个 tracked 文件确实全空的合法仓库会被
    误归入「量不出来」——这是**有意**的保守选择，⛔ 别顺手「修好」它，
    那会造出一个 0.0 的假估算，正是本次要根除的那类东西。
    """
    r = subprocess.run(["git", "ls-files", "-z"], cwd=project,
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not r.stdout:
        return None
    total = 0
    for rel in r.stdout.split(chr(0)):
        if not rel:
            continue
        try:
            total += (project / rel).stat().st_size
        except OSError:
            pass
    if total == 0:
        return None
    #  ⚠️ `synced_paths()` 返回的是**相对项目根的字符串**（如 `'game/.godot'`），
    #     ⛔ 不是 Path——必须自己拼。第一版直接把 str 丢进 `_dir_size_mb`，
    #     `p.is_dir()` 抛 AttributeError，又被一个裸 `except Exception: pass`
    #     吞掉，于是 eco-ob 静默从 256 掉到 146（**低估 43%**）而无人知道。
    #  ⛔ 所以这里只 catch 「读不到项目配置」这一种（合法：非 devloop 项目），
    #     ⚠️ 其余异常一律往上抛——静默吞掉的估算错误正是本次要根除的那类东西。
    from .config import ConfigError, ProjectPaths
    try:
        synced = ProjectPaths(project).synced_paths()
    except (ConfigError, FileNotFoundError, OSError):
        synced = []                          # 不是 devloop 项目，合法
    for rel in synced:
        if (s := _dir_size_mb(project / rel)) is not None:
            total += s * 1e6
    return total / 1e6


def _free_mb(p: Path) -> float | None:
    """盘上还剩多少 MB。⚠️ 抽成函数是为了让测试能直接摆布它。"""
    import shutil as _sh
    try:
        return _sh.disk_usage(str(p)).free / 1e6
    except OSError:
        return None


def _estimate_mb(project: Path, root: Path) -> float | None:
    """一个新 worktree 大约要多少 MB。⛔ 量不出来就返回 `None`，**不编数字**。

    ## ⭐ 为什么取 max 而不是「挑最近那个」

    原来是 `same[-1]`，注释写着「最近那个，形态最接近」。⛔ 三个假设全不成立：

    1. `Path.iterdir` 文档明写 arbitrary order；本机 NTFS 实测是**按文件名排序**。
       在 devloop 自己的 root 上看着对，纯属任务 id `r1<r2<r3` 恰好与时间同序。
    2. 最近建的不等于最完整——`Worktree.remove()` 在 Windows 上文件被占用时
       会留下空壳或半删壳，2026-08-02 现场那个就是。
    3. 「形态最接近」对**容量检查**而言本来就该取 `max`，不是取 latest。

    ⭐ `max` 一次解决三件事：空壳（0 被滤掉）、半删壳（被完整的压住）、
    iterdir 无序（max 与顺序无关）——「该怎么排序」这个问题因此自然消失。

    ⚠️ 候选多时按 mtime 降序只量前几个：**mtime 是真实信号**，
    而且这里只用于限流——量错顶多少看一个候选，不改变 max 的正确性。
    """
    cands = ([d for d in root.iterdir()
              if d.is_dir() and d.name.startswith(project.name + "-")]
             if root.exists() else [])
    if len(cands) > _MAX_PROBES:
        cands.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        cands = cands[:_MAX_PROBES]
    sizes = [s for d in cands if (s := _dir_size_mb(d)) is not None and s > 0]
    if sizes:
        return max(sizes)
    #  这个项目的第一单（或壳全是空的 / 全量不出来）→ 按 checkout 实测。
    return _checkout_size_mb(project)


#  ⚠️ 每个候选约 0.05–0.13s；devloop 的 root 现有 16 个 ≈ 1s，
#  与「别让检查本身变成负担」直接冲突。
_MAX_PROBES = 5


def check_disk(project: Path, root: Path) -> None:
    """建 worktree 之前看一眼盘。⛔ 不够就**当场报错**，别等 git 写到一半炸。

    ⚠️ 这条是无人值守才要紧的：手动跑一单没人会写满盘，
    而跑一夜十几单、每单几百 MB，加起来就是几个 G。

    ⛔ 判不出来时**只守余量，不假装知道要多少**。
    ⚠️ 这句话原来就写在这儿，但一直是假的——`_FALLBACK_SIZE_MB = 200`
    总能编出一个数来挡。2026-08-02 实测它唯一一次在生产路径上真正生效
    （eco-ob 首单）时是错的：编了 200，真实 251→361。
    """
    free_mb = _free_mb(root)
    if free_mb is None:
        return                                # 盘都问不出来，⛔ 别挡着干活
    est = _estimate_mb(project, root)
    if free_mb < (est or 0.0) + _HEADROOM_MB:
        same = ([d for d in root.iterdir()
                 if d.is_dir() and d.name.startswith(project.name + "-")]
                if root.exists() else [])
        how = (f"一个 worktree 约 {est:.0f} MB" if est
               else f"一个 worktree 多大**量不出来**（只按 {_HEADROOM_MB} MB 余量判）")
        raise OSError(
            f"⛔ 磁盘空间不够，拒绝再建 worktree。\n"
            f"   剩余 {free_mb:.0f} MB · {how} · 要求留 {_HEADROOM_MB} MB 余量\n"
            f"   ⚠️ {root} 下本项目已有 {len(same)} 个 worktree 目录"
            f"（⚠️ 可能含已不在 `git worktree list` 里的残壳）\n"
            f"   看哪些能清：python -m devloop.cli prune --project {project}\n"
            f"   ⛔ 写满盘不是「少跑一单」——git 写提交对象写到一半，"
            f"仓库可能进入需要手工救的状态。")


def create(project: Path, task_name: str, base: str | None = None) -> Worktree:
    """在项目旁边开一个隔离 worktree。

    位置放在项目**外面**（同级的 `.devloop-worktrees/`），避免工人在自己的
    工作目录里看到别的 worktree，也避免 git 把它当成项目内容。
    """
    if base is None:
        base = snapshot_base(project)
    # ⚠️ 并发安全的时间戳：秒级粒度在同名任务并发时会撞分支名（G-41 实测）
    stamp = naming.stamp()
    branch = f"devloop/{task_name}-{stamp}"
    root = project.parent / ".devloop-worktrees"
    root.mkdir(exist_ok=True)
    check_disk(project, root)
    wt = root / f"{project.name}-{task_name}-{stamp}"

    r = subprocess.run(["git", "worktree", "add", "-b", branch, str(wt), base],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise ConfigError(f"建 worktree 失败：{r.stderr.strip()[:300]}")
    return Worktree(wt, branch, project)
