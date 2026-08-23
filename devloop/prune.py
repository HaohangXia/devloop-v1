"""列出可清理的隔离分支与 worktree。

**为什么只列不删**：每派一单留一个分支（G-26 之后产出固化在上面）。分支会堆积，
但**里面装着工人的产出**——那正是 L3 那次丢掉的东西。宪法把「合回主线」列为
必须人工放行的动作，删除同理：**这里只给清单和命令，删不删由人决定。**

⚠️ 判定「能不能删」的唯一依据是**产出有没有别处留存**，不是分支有多老。
一个三天前的分支若从未被合并、产出也没归档，删了就是第二次 G-26。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Branch:
    name: str
    sha: str
    merged: bool          # 是否已合进当前 HEAD
    empty: bool           # 分支上有没有工人的提交（没有 = 产出从未固化）
    worktree: str         # 关联的 worktree 路径，空串表示已不存在
    #  ⭐ 闸对这一单的判定，直接从尖端提交信息里读：`闸全绿` / `闸未过` / `未跑闸`。
    #  ⛔ 读不出来时是**空串**，不是「未跑闸」——「读不出来」与「跑了闸但没验到」
    #     是两件事，把前者当后者会让一条**无从判断**的分支看起来像有结论。
    #  ⚠️ 2026-08-02 之前 `scan()` 明明已经取到了整条提交信息，却只留了一个
    #     `empty` 布尔——于是「五道全绿的产出」与「闸没过的垃圾」印出来一模一样，
    #     人必须逐支 `git show` 才分得开。**那就是 30 秒读不完的原因。**
    gate: str = ""
    #  ⭐ worktree 里未提交（含已暂存）的条目数。⛔ 三种状态必须分得开：
    #       `worktree == ""`      没有工位 → 不可能有未提交的活，本字段无意义
    #       `dirty` 是整数         问出来了，0 就是真干净
    #       `dirty is None` 且有工位  **问不出来** → ⛔ 不许当成干净
    #  ⚠️ 加在**末尾**：G-95 的学费——中途插字段会让所有位置参数错位。
    dirty: int | None = None

    @property
    def uncommitted(self) -> str:
        """worktree 里未提交的活；没有就返回空串。⭐ 空串 = 这条不构成阻拦。"""
        if not self.worktree:
            return ""
        if self.dirty is None:
            return "⛔ 有 worktree 却问不出它的状态（目录没了？）——**不知道**不算干净"
        if self.dirty:
            return f"⛔ worktree 里有 **{self.dirty} 个未提交改动**，删了就没了"
        return ""

    @property
    def safe_to_delete(self) -> bool:
        """⛔ **只有一种情况可以安心删：已合并，且工位上没有未提交的活。**

        ## ⛔ 为什么 `merged` 一条不够（G-109，2026-08-04 真派单实证）

        工人跑了 50 分钟被自己的死线掐死，产出**留在 worktree 里没提交**。
        于是分支尖端 == 基准 ⇒ 从 HEAD 可达 ⇒ `git branch --merged` 收录它
        ⇒ 本命令印「可清理」，并给出 `git worktree remove --force`。

        > ⭐ **「产出没被提交」这件事本身，让工具认定「产出已在主线上」。**

        ⚠️ 这不是 `merged` 判错了——它判的是「这条分支的**提交**都在主线上」，
        而那是对的（一个提交都没有，空集当然被包含）。⛔ 错的是把它当成
        「这一单的**产出**都在主线上」的代用品。⭐ 能直接量的是 `git status`。

        ## ⛔ 为什么 `empty` 也不许进这个判定（2026-08-02 的旧账，仍然成立）

        ⚠️ 这里原来是 `self.merged or self.empty`。而模块头写得很死：
        「判定「能不能删」的唯一依据是**产出有没有别处留存**……删了就是第二次 G-26」
        ——⭐ 而 `empty` 是**按提交主题猜的**（`head_msg.startswith("work(")`），
        不是「别处留存」的证据。

        它对本判定的贡献只有两格：

        | merged | empty | |
        |---|---|---|
        | True | * | 已合并，`merged` 一条就够，⛔ `empty` 不贡献任何东西 |
        | False | True | ⛔ **唯一独占的格子，而它恰好是危险格** |

        危险格 = 「尖端从 HEAD 不可达」+「字符串启发式说没产出」
        → `report()` 印「可清理」并给出 `git branch -D`
        → **删掉唯一一份未合并的产出**。

        ⭐ 不是假想：`L4-20260726-172258` 尖端是 `feat(L4): ...`，改了
        `devloop/gates.py` 一个文件，**真产出**，却因为不叫 `work(` 被判 empty。
        ⚠️ 它没出事只因为**碰巧已合并**。

        ⚠️ 代价：确实没产出、又没合并的分支会落进「别删」（多一点噪音）。
        ⛔ 与「删掉唯一一份产出」不对称，所以往保守那边靠。
        """
        if self.uncommitted:
            return False
        return self.merged

    @property
    def reason(self) -> str:
        #  ⛔ **未提交的活压过一切其它原因**：别的原因说的是「提交在不在主线上」，
        #     而它说的是「有一份活根本还不是提交」。⚠️ 后者一旦被前者的措辞盖住
        #     （比如印成「已合并进主线，产出已在主线上」），人就会照着删。
        if self.uncommitted:
            return f"{self.uncommitted}　⭐ 先进那个目录看一眼：git -C … status"
        #  ⛔ **两者同时成立时不许只说 merged。**
        #     `scan()` 里那段注释早就写着这个隐患：「两种情况的处置恰好都是
        #     「可删」，所以不会丢东西，**但会把原因说反，而原因正是人决定
        #     删不删的依据**」。2026-08-02 实测：本仓 8 个「可清理」分支里
        #     **4 个**是 merged 且 empty，全被印成「产出已在主线上」——
        #     而它们的工人一个提交都没有。
        #
        #  ⚠️ **陷阱：这里不许断言「这一单没产出」。** `empty` 判的是
        #     「尖端是不是 `work(...)`」，而 `L4-20260726-172258` 的尖端是
        #     `feat(L4): ...`——**真产出，被误报成 empty**。
        #     ⭐ 工具分不出「没产出」与「产出走了别的路提交」，那就把两个
        #     事实摆出来让人看一眼，⛔ 别装作分得出。
        if self.merged and self.empty:
            return ("已合并进主线；⚠️ 但尖端不是 `work(...)` 工人提交"
                    "——要么这一单没产出，要么产出走了别的路。⛔ 看一眼再删")
        if self.merged:
            return "已合并进主线，产出已在主线上"
        #  ⛔ 未合并那一档必须按闸的判定分岔——三种的处置完全不同：
        #     全绿 = 可以合；没过 = 合之前必须看；没跑闸 = 没有机器判据。
        if not self.empty and self.gate == "闸未过":
            return ("⚠️ 有产出，但**闸没过**——⛔ 合之前必须看，"
                    "别把没通过验收的东西合进主线")
        if not self.empty and self.gate == "未跑闸":
            return ("有产出，但**没跑闸**（多半是只读单或零改动）"
                    "——⚠️ 没有机器判据，只能人看")
        if not self.empty and self.gate == "闸全绿":
            return "⚠️ 有未合并的产出，**闸全绿**——删了就没了"
        if self.empty:
            #  ⚠️ 这一档从 2026-08-02 起落进「别删」而不是「可清理」
            #     ——`empty` 是启发式，L4 证明它会误报（见 `safe_to_delete`）。
            return ("尖端不是 `work(...)` 工人提交——多半是那一单没产出。"
                    "⚠️ 但这只是按提交主题猜的，`L4` 就被猜错过；"
                    "⛔ 又没合并，所以不算安心可删。先 `git show` 看一眼")
        return "⚠️ 有未合并的产出——删了就没了"


def _git(project: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(project), *a], capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def scan(project: Path) -> list[Branch]:
    wt_of: dict[str, str] = {}
    cur_wt = ""
    for ln in _git(project, "worktree", "list", "--porcelain").splitlines():
        if ln.startswith("worktree "):
            cur_wt = ln[9:].strip()
        elif ln.startswith("branch "):
            wt_of[ln[7:].strip().replace("refs/heads/", "")] = cur_wt

    # ⚠️ `git branch` 的前缀有三种：`* ` 当前分支、`+ ` **被别的 worktree 检出**、`  ` 普通。
    #    第一版只剥了 `* `，于是每一个隔离分支（它们全都被 worktree 检出，带 `+`）
    #    都匹配不上，`merged` 恒为空 —— 已合并的分支会被判成「未合并，别删」。
    #    方向上是安全的（宁可不删），但它让这个命令基本没用。测试逮到的。
    merged = {l.strip().lstrip("*+ ").strip()
              for l in _git(project, "branch", "--merged").splitlines() if l.strip()}

    out: list[Branch] = []
    for ln in _git(project, "branch", "--list", "devloop/*").splitlines():
        name = ln.strip().lstrip("*+ ").strip()
        if not name:
            continue
        sha = _git(project, "rev-parse", "--short", name).strip()
        # ⚠️ 「空」的判据不能用「尖端 == 与 HEAD 的共同祖先」——分支一旦被合并
        #    那两者必然相等，于是**已合并的分支会被误判成「从来没产出」**。
        #    两种情况的处置恰好都是「可删」，所以不会丢东西，但会把原因说反，
        #    而原因正是人决定删不删的依据。
        #
        # ⛔ 也**不能按作者邮箱查**（本函数原来就是那么写的）。
        #    2026-07-29 审计抓到：worktree 与主仓库共用 `.git/config`，
        #    工人身份被写进了主仓库，此后维护者亲手写的 24 个提交都挂着
        #    `worker@devloop.local`——**master 的尖端就带这个邮箱**。
        #    于是今后从 master 开的**每一个**隔离分支，往回查都能查到一条
        #    「工人提交」，全部被判成「有未合并产出，别删」。
        #    ⚠️ 根源已修（worktree.py 改用 `-c` 每命令注入），但**历史改不回来**，
        #    所以判据本身必须换成不依赖身份的。
        #
        # ⛔ 也**不能查「分支上有没有主线没有的提交」**（我 2026-07-29 一度改成
        #    这样）——那正是上面那段注释警告的写法：合并之后分支的提交从主线
        #    可达，计数归零，已合并的分支又会被说成「从来没产出」。
        #    已有测试 test_已合并的分支原因要说对_不能说没产出 当场抓到了。
        #
        # 判据落在**分支尖端这一个提交**上：devloop 分支要么停在基准上（没产出），
        # 要么尖端就是 `commit_result` 写的那一条工人提交。合并不会移动分支，
        # 所以合并之后尖端仍是那条工人提交——两种情形都判得对。
        # ⚠️ 同时看主题前缀和正文里的分支名：只看前缀的话，主线尖端恰好是一条
        #    被快进合并的工人提交时，从它开的新分支会被误判成有产出。
        head_msg = _git(project, "log", "-1", "--format=%s%n%b", name)
        empty = not (head_msg.startswith("work(") and name in head_msg)
        #  ⭐ 同一个字符串里还写着闸的判定（`commit_result` 拼的），
        #     2026-08-02 之前被整个扔掉了。⛔ 读不出来留空串，不许猜。
        m = re.search(r"工人产出 · (闸全绿|闸未过|未跑闸)", head_msg)
        wt = wt_of.get(name, "")
        out.append(Branch(name, sha, name in merged, empty, wt,
                          gate=m.group(1) if m else "", dirty=_dirty(wt)))
    return out


def _dirty(worktree: str) -> int | None:
    """worktree 里未提交（含已暂存）的条目数。⛔ 问不出来返回 None，**不许返回 0**。

    ⭐ 用 `--porcelain`：它对已暂存与未暂存**一视同仁**都出一行。
    ⚠️ 只看未暂存的话，工人 `git add` 一下就能让这条判据失效。

    ⛔ `-uall` 是必须的：默认 `-unormal` 把一个全新目录**折叠成一行**，
       于是「工人新建了一个装着 30 个文件的目录」被数成 1。⚠️ 这里数的是
       「有没有」和「大概多少」，折叠不影响「有没有」，但会让人低估代价。
    """
    if not worktree:
        return None
    r = subprocess.run(["git", "-C", worktree, "status", "--porcelain", "-uall"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        return None                      # ⛔ 「不知道」就说不知道
    return len([l for l in r.stdout.splitlines() if l.strip()])


def _base_of(project: Path, name: str) -> str | None:
    """这条分支从哪儿分岔出来的。算不出来返回 None。

    ⛔ **不许用 `<分支>^`（尖端的父提交）。** 那假设「一条分支只有一个提交」，
    ⚠️ 而 `commit_result` 只在 `changed_files()` 非空时追加尖端——
    工人**在 worktree 里可以自己先提交若干版**，于是分支可以有两个以上提交。
    2026-08-03 实测：一条两提交的分支，按尖端算「改 1 个文件」，实际 2 个；
    ⛔ 更狠的是归档只装了尖端，**删完 `git gc` 一次就取不回来了**。

    ⛔ 也不许用 `master` / `main` 这种写死的名字：仓库叫什么不固定，
    而且合回主线之后共同祖先会漂移。⭐ `merge-base ... HEAD` 两个毛病都没有。
    """
    r = subprocess.run(["git", "-C", str(project), "merge-base", name, "HEAD"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    b = r.stdout.strip()
    return b if r.returncode == 0 and b else None


def _files_changed(project: Path, name: str) -> int | None:
    """这条分支相对**分岔点**改了几个文件；算不出来返回 None。

    ⚠️ 数的是整条分支，⛔ 不是尖端一个提交——理由见 `_base_of`。
    ⭐ 这一行的全部用途是让人**估复核成本**，少报等于让人低估。
    """
    base = _base_of(project, name)
    if base is None:
        return None
    out = _git(project, "diff", "--name-only", base, name)
    return sum(1 for l in out.splitlines() if l.strip())


def report(project: Path) -> tuple[str, int]:
    """返回 (给人看的报告, 可安心删的条数)。⛔ 不执行任何删除。"""
    bs = scan(project)
    if not bs:
        return "没有 devloop/* 分支——干净。", 0

    safe = [b for b in bs if b.safe_to_delete]
    keep = [b for b in bs if not b.safe_to_delete]
    L = [f"{len(bs)} 个隔离分支：可清理 {len(safe)} · 建议保留 {len(keep)}"]

    # ⚠️ 只统计「建议保留」的——「可清理」的要么已合并、要么没产出，算进去会
    #    虚报「还有多少产出待处理」，那正是无人值守跑一夜后最想问的一个数。
    if keep:
        counts = [_files_changed(project, b.name) for b in keep]
        counted = [c for c in counts if c is not None]
        skipped = len(counts) - len(counted)
        total = sum(counted)
        summary = f"待处理产出：{len(counted)} 个分支合计改动 {total} 个文件"
        if skipped:
            # ⛔ 数不出来的分支不许当 0 计——那会把「没数据」和「零改动」
            #    混成一件事，排查时找不到线索。
            summary += f"（跳过 {skipped} 个数不出来的分支）"
        #  ⛔ **未提交的活必须单列，不许漏也不许并进上面那个数。**
        #     本函数自己写着这一行的用途是「让人估复核成本，少报等于让人低估」，
        #     ⚠️ 而 `_files_changed` 只数**已提交**的差异——工人被掐死那种情形
        #     提交数为 0，于是 50 分钟的活在账上是「0 个文件」（G-109 实测原文：
        #     「待处理产出：1 个分支合计改动 0 个文件」）。
        #  ⭐ 单列而不是相加：它们是两种东西（进了提交的 vs 还摊在工位上的），
        #     处置方式也不同——前者 `git merge`，后者得先进去看。
        wip = [b for b in keep if b.dirty]
        if wip:
            summary += (f"；另有 **{sum(b.dirty or 0 for b in wip)} 个未提交改动**"
                        f"摊在 {len(wip)} 个工位上（⛔ 还没进任何提交）")
        L.append(summary)

    L.append("")

    if keep:
        #  ⚠️ 措辞不能再是「有未合并产出」——2026-08-02 起 `empty` 不再参与
        #     删除判定，于是「启发式说没产出、但没合并」的分支也落在这一堆里，
        #     ⛔ 对它们说「有未合并产出」是把一个猜测说成了事实。
        L.append("⚠️ **别删**（未合并，或工具无法确认产出已别处留存）：")
        for b in keep:
            L.append(f"  {b.name} @ {b.sha}")
            L.append(f"      {b.reason}")
            L.append(f"      看：git show {b.sha}   ｜ 合：git merge {b.name}")
        L.append("")

    if safe:
        L.append("可清理：")
        for b in safe:
            L.append(f"  {b.name} @ {b.sha}  —— {b.reason}")
        L.append("")
        L.append("⛔ 本命令不删任何东西。要删，自己跑：")
        for b in safe:
            if b.worktree:
                L.append(f"  git worktree remove --force {b.worktree} && "
                         f"git -C {project} branch -D {b.name}")
            else:
                L.append(f"  git -C {project} branch -D {b.name}")
    return "\n".join(L), len(safe)


# ══ 归档：删之前先打包并验证（2026-08-02）════════════════════════
#
# ⭐ 本模块头写着「判定「能不能删」的唯一依据是**产出有没有别处留存**……
#    或产出也没归档，删了就是第二次 G-26」。
#    ⛔ 而在这之前，**「归档」这条路根本不存在** —— 判定只能二选一：
#    要么已合并（安全），要么别删。
#
# ⚠️ 而判定终究是判断，判断会错：`empty` 那个启发式实测误报过（`L4` 尖端是
#    `feat(L4)` 而非 `work(`，是真产出却被判 empty）。⛔ 那条已不参与删除判定，
#    但这一层要保证的是另一件事：**判错了也拿得回来。**


@dataclass(frozen=True)
class Archived:
    name: str
    bundle: Path
    ok: bool
    verified: bool
    detail: str = ""
    files: int | None = None
    sha: str = ""


def _run(project: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project), *a], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def archive_branches(project: Path, dest: Path,
                     only: list[str] | None = None) -> list[Archived]:
    """把每个隔离分支打成 `git bundle` 并**真验一遍**。

    ⭐ 只装**分支尖端那一个提交**（`<分支>^..<分支>`）：实测 **2KB**，
    而全历史要 944KB。⚠️ 判据与 `scan()::empty`、`_files_changed` 一致——
    devloop 分支的产出就固化在尖端那一条 `commit_result` 写的提交上。

    ⛔ **`git bundle verify` 必须真跑。** 「打了包」不等于「包是好的」，
    那正是本项目一直在防的形态（宣称有的能力，没有实际证据）。
    """
    dest.mkdir(parents=True, exist_ok=True)
    names = only if only is not None else [b.name for b in scan(project)]
    out: list[Archived] = []
    for name in names:
        f = dest / (name.replace("/", "__") + ".bundle")
        sha = _run(project, "rev-parse", name).stdout.strip()
        #  ⛔ 边界取**分岔点**，不是尖端的父提交。
        #     ⚠️ 2026-08-03 实测：两提交的分支按 `<分支>^..<分支>` 打包 →
        #     `verify` 过、MANIFEST 打 ✅、删掉之后跑一次**日常的 `git gc`** →
        #     ⛔ `Repository lacks these prerequisite commits`——**产出真的丢了**。
        #     那句「父提交在主线上，所以只要主线还在就接得上」对多提交分支是假的：
        #     父提交是工人自己的中间提交，它不在主线上。
        base = _base_of(project, name)
        rng = f"{base}..{name}" if base else name
        r = _run(project, "bundle", "create", str(f), rng)
        if r.returncode != 0 and base:
            #  ⚠️ 区间为空（分支已完全合并）时 git 拒绝造包。
            #     ⭐ 退回打**全历史**——大一些，但永远取得回来。
            #     ⛔ 不许因此跳过归档：那会让「已合并」这一档没有后路。
            r = _run(project, "bundle", "create", str(f), name)
        if r.returncode != 0 or not f.exists() or f.stat().st_size == 0:
            #  ⛔ 失败必须报出来。⚠️ 静默跳过会让它落进「已归档，可删」。
            out.append(Archived(name, f, False, False,
                                r.stderr.strip()[:200] or "包是空的", sha=sha))
            continue
        v = _run(project, "bundle", "verify", str(f))
        out.append(Archived(name, f, True, v.returncode == 0,
                            "" if v.returncode == 0 else v.stderr.strip()[:200],
                            files=_files_changed(project, name), sha=sha))
    _write_manifest(project, dest, out)
    return out


def reverify(project: Path, items: list[Archived]) -> list[Archived]:
    """重新验一遍已有的包。⚠️ 归档与删除之间可能隔了很久（或换了个人）。"""
    return [replace(a, verified=(a.bundle.exists()
                                 and _run(project, "bundle", "verify",
                                          str(a.bundle)).returncode == 0))
            for a in items]


def _write_manifest(project: Path, dest: Path, items: list[Archived]) -> None:
    """⭐ 包是二进制的。⚠️ 人要能**不解包**就看出里面装的是什么、怎么取回来。"""
    L = [f"# 隔离分支归档 · {project.name}", "",
         "⛔ 这些 `.bundle` 是删分支之前留的后路。**判错了靠它拿回来。**", "",
         "| 分支 | 尖端 | 改了几个文件 | 包 | 验证 |", "|---|---|---|---|---|"]
    for a in items:
        L.append(f"| `{a.name}` | `{a.sha[:12]}` | "
                 f"{a.files if a.files is not None else '数不出'} | "
                 f"`{a.bundle.name}` | {'✅ 通过' if a.verified else '⛔ **没过**：' + a.detail} |")
    L += ["", "## 怎么把一个分支取回来", "",
          "```bash",
          "# 先看包里装的是什么（⚠️ 不用解包）",
          "git bundle list-heads <这个目录>/<包名>.bundle",
          "git bundle verify    <这个目录>/<包名>.bundle",
          "",
          "# 取回来（落到 refs/heads/restored/<原分支名>）",
          "git fetch <这个目录>/<包名>.bundle "
          "'refs/heads/*:refs/heads/restored/*'",
          "```", "",
          "⚠️ 包里只有**尖端那一个提交**，它的父提交要在仓库里才接得上"
          "——父提交在主线上，所以只要主线还在就接得上。",
          "⛔ 主线也没了的话，这些包救不回来——那种情况要靠远端仓库。"]
    (dest / "MANIFEST.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def delete_branches(project: Path, names: list[str],
                    archived: list[Archived] | None) -> int:
    """删分支。⛔ **只有归档过且验证通过的才准删。**

    ⚠️ 这一层是 2026-08-02 加的，此前本模块**只列不删**（「删不删由人决定」）。
    ⭐ 改成可以删的前提正是这一层：判错了拿得回来。⛔ 拿不回来就还是不许删。
    """
    ok = {a.name for a in (archived or []) if a.ok and a.verified}
    missing = [n for n in names if n not in ok]
    if missing:
        raise ValueError(
            f"⛔ 这些分支没有**验证通过的**归档，拒绝删除：{'、'.join(missing)}。\n"
            f"   先跑 `prune --archive <目录>`，⚠️ 并确认 MANIFEST.md 里它们是 ✅。")
    n = 0
    for name in names:
        wt = next((b.worktree for b in scan(project) if b.name == name), "")
        if wt:
            _run(project, "worktree", "remove", "--force", wt)
        if _run(project, "branch", "-D", name).returncode == 0:
            n += 1
    return n
