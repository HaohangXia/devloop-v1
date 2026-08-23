"""评测集：一组**已知答案**的判断题。

**它要回答的问题**（这是它存在的唯一理由，别做成一个好看的仪表盘）：

1. **换后端会不会变差？** 用户要能「换个 api 就换个模型」——那就必须能回答
   「换了之后质量掉没掉」。手工重跑整批要几十分钟、几毛钱、且结论带噪声。
2. **改任务书模板会不会砸掉判断力？** 本项目已经改过两次模板（G-30 情态动词、
   G-33 报告格式）。改完只跑了「格式还能不能解析」，**没验过「判断还准不准」**。
3. **趋势**：这套东西在变好还是变坏。

## 答案从哪来（这是全部价值所在）

20 个样本**全部由编排方在 2026-07-26/27 亲手核实过**，每条都附：
判据（`why`）· 证据位置（`evidence`）· 谁怎么核的（`verified_by`）。
不是模型自称的，不是从工人报告里抄的。

⛔ **基准必须冻结**：全部样本锚在 `C:\\pg\\eco-ob-p4` @ `6f4b849`（tag `devloop-p4-base`）。
那个 worktree 一旦被改，这些答案就同时失效——所以跑分前先核对基准，对不上就拒跑。

## 三类判定

| kind | 含义 | 为什么要有这一类 |
|---|---|---|
| `broken` | 文档这句话已经不成立 | 主要目标：能不能找出腐烂 |
| `holds` | 仍然成立 | **假阳性的对照**。只测 broken 的话，一个「全部报矛盾」的模型能拿满分 |
| `undecidable` | 工具权限内判不了 | 测的是**知道自己不知道**。硬判一条判不了的，比漏报严重得多 |

⚠️ `traps` 字段记着每条撞过的坑（命名蛇形化、行号漂移、设计要求的双实现……），
**不进提示词**——它是给读结果的人看的，用来解释「为什么这条难」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CASES = Path(__file__).resolve().parent / "cases.jsonl"

# ⛔ 全部样本锚在这个基准上。它一变，答案就失效。
BASE_REPO = Path(r"C:\pg\eco-ob-p4")
BASE_SHA = "6f4b849"

KINDS = ("broken", "holds", "undecidable")


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    doc: str
    anchor: str
    claim: str
    why: str
    evidence: str
    verified_by: str
    traps: list

    def prompt(self) -> str:
        """喂给工人的题面。

        ⚠️ **只给文档原话与出处，不给任何暗示**：
        - 不给 `why`（那是答案）
        - 不给 `evidence`（那是答案的位置）
        - 不给 `traps`（那会把难点直接标出来）
        - **不告诉它三类判定的先验分布**，否则它会去凑比例
        """
        return f"""你在一个**冻结的只读代码库**里做事实核查。仓库根 = `{BASE_REPO}`（基准提交 {BASE_SHA}）。

你有 `Read` / `Grep` / `Glob`。**没有 Bash** —— 不能跑测试、不能跑 godot、不能跑 git。

# 要核实的一句话

出处：`{self.doc}` 的「{self.anchor}」

> {self.claim}

# 问题

**这句话在当前代码里还成立吗？**

三选一，不许选别的：

- **holds** —— 仍然成立
- **broken** —— 已经不成立（代码里有反证）
- **undecidable** —— 用你手上的工具**判不了**（比如需要把程序跑起来才知道）

⚠️ 三个答案地位平等。**说「判不了」是正确行为，不扣分；硬判一条你判不了的，扣分很重。**

⚠️ 查不到就换个写法再查：驼峰↔蛇形（`fooBar` ↔ `foo_bar`）、单复数、同义词。
照字面查一次就下结论，是这个项目栽过最多次的坑。

# 回答格式

正文随便写，但**最后一行必须单独是**下面三者之一，前后不要有别的字：

VERDICT: holds
VERDICT: broken
VERDICT: undecidable
"""


def load() -> list[Case]:
    out = []
    for ln in CASES.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        if d["kind"] not in KINDS:
            raise ValueError(f"{d['id']} 的 kind={d['kind']!r} 不在 {KINDS}")
        out.append(Case(**d))
    return out


def check_baseline() -> str:
    """跑分前核对基准。对不上就返回原因，调用方必须拒跑。

    ⚠️ 这不是形式主义：样本的答案全部锚在那个提交上。基准漂了，
    分数就不再是「模型判断力」的度量，而是「文档变了多少」的度量——
    而且它**看起来仍然像个正常分数**。
    """
    import subprocess
    if not BASE_REPO.exists():
        return f"基准仓库不存在：{BASE_REPO}"
    r = subprocess.run(["git", "-C", str(BASE_REPO), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8")
    # ⚠️ 先看 returncode 再看 stdout：git 不可用或那不是个仓库时 stdout 是空串，
    #    空串 startswith(sha) 为假 → 会报成「基准漂了」，**把人指去查提交，
    #    而真正的问题在 git**。诊断信息误导比没有诊断更费时间。
    if r.returncode != 0:
        return (f"读不到基准仓库的 HEAD（git 退出码 {r.returncode}）："
                f"{(r.stderr or '').strip()[:200] or '无输出'}")
    sha = r.stdout.strip()
    if not sha.startswith(BASE_SHA[:7]):
        return f"基准漂了：期望 {BASE_SHA}，实际 {sha}"
    dirty = subprocess.run(["git", "-C", str(BASE_REPO), "status", "--porcelain"],
                           capture_output=True, text=True, encoding="utf-8").stdout.strip()
    if dirty:
        n = len(dirty.splitlines())
        return f"基准工作区有 {n} 项未提交改动——答案可能已失效"
    return ""
