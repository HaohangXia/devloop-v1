"""⛔ 体检报出的闸名，必须与**真跑**报出的**逐字相等**。

## 这条是怎么来的（2026-08-16）

2026-08-12 加了「开跑前验判据」（G-127）：计划 `require_pass` 点名的每道闸，
开跑前那次体检也必须报一行；报不出就**拒绝派单**。

⛔ 而没有任何东西检查「闸自己报得全不全」。实测后果：

```
本仓 7 份计划 → 拒 6 份，只有一道都没点名的 demo 放行
```

被拒的理由全是「语法 / 测试守卫 / 禁改清单 没在体检里露面」——
⚠️ **而那几道本身好好的**，只是体检没报它们的名字。
⇒ 「开跑前先验判据」这道保险，把自己变成了**唯一还在拒单的东西**。

## ⭐ 判据是双向的（⛔ 单向会漏掉更贵的那一半）

| 方向 | 差集不空的后果 |
|---|---|
| **真跑有、体检没有** | ⛔ 点名它 → **还没花一分钱就被拒**（今天 6/7 就是这样） |
| **体检有、真跑没有** | ⛔⛔ 点名它 → 体检**放行** → **花完钱之后**才收「闸与验收契约对不上」 |

⚠️ 第二个方向今天有两个活例，⛔ 而且都是只读脚本读不出来的：
- `环境自检`：体检报它，真跑那段**只 `exit 2`，一行判定都不打**
- `改动守卫`：只在**脏工作区**那条路上以 SKIP 出现，
  而真跑**一律**在干净检出里（`cli.py::_run_unit` 与 `gates.py::run_on_commit`
  都硬写 `clean_checkout=True`）⇒ 它是个**谁也没法点名的名字**

## ⛔ 为什么这条测试必须真跑闸，不许只读脚本

⭐ 上面两个活例，是我先按「读脚本」的办法把体检补齐、判据显示两边归零之后，
**真跑一次闸才暴露出来的**。
⚠️ 读脚本得到的是「哪些名字**写**在文件里」，⛔ 而要问的是
「**真跑一次，哪些名字会被打出来**」——那是两件事。

**判据要落在能直接量的东西上，⛔ 不许用代用品。**
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from devloop import gates as gates_mod
from devloop.config import ProjectPaths

REPO = Path(__file__).resolve().parents[1]


def _preflight_names(paths: ProjectPaths) -> set[str]:
    """体检真跑一次，收它报出的闸名。"""
    _verdict, _lines, raw = gates_mod.preflight(paths)
    out = set()
    for ln in raw.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0].strip() == "PREFLIGHT":
            out.add(parts[1])
    return out


def _real_names(paths: ProjectPaths) -> set[str]:
    """⭐ **真跑一次闸**（干净检出，= 真派单的唯一形态），收它打出的闸名。

    ⛔ 不读脚本文本 —— 见模块 docstring 的最后一节。
    """
    res = gates_mod.run_on_commit(paths, "HEAD")
    return {l.name for l in res.lines}


def _fixture_project(tmp: Path) -> ProjectPaths:
    """⭐⭐ 一个**用真 `gates.sh`、假内容**的样板仓库。

    ## ⛔⛔ 2026-08-22：为什么不再对本仓自己跑（latch 09/11 号裁定）

    原来是 `run_on_commit(ProjectPaths(REPO), "HEAD")` —— **对自己跑**。
    ⇒ 闸会跑全量 pytest ⇒ 里面又有这条测试 ⇒ ⛔ **又起一个闸 ⇒ 递归。**

    **实测代价**（2026-08-22）：

    | | 秒 |
    |---|---|
    | 全量 pytest | **769** |
    | 排除本文件 | ⭐ **117** |
    | ⇒ 本文件独占 | ⛔ **652（85%）** |

    ⚠️ 而 652 ≈ 内层闸自己的 600 秒超时 ——
    ⭐ **递归的界不是设计出来的，是被另一个超时兜住的。**
    ⇒ 后果：闸对任何输入都超时报红（08-17 起，08-20 才被发现，2/2 命中）。

    ## ⛔ 为什么 fixture 必须用**真的** `gates.sh`

    latch §2.1 抓到的：若 fixture 配一份**假的**闸脚本，
    这条测试验的就是「假闸的名册 = 假闸的实跑」——
    ⇒ ⛔ **对真 `gates.sh` 零信息量，B 会静默退化成「干脆不验」。**

    ⭐ 所以这里**原样复制真 `.devloop/gates.sh`**，只把**被测内容**换成最小的：
    一个 `devloop/` 占位包 + 一个只有 1 条测试的 `tests/`。
    ⇒ 闸内那次 pytest 跑的是**这一条**，⛔ 不是本仓的 845 条 ⇒ **递归从结构上断掉**。

    ⚠️ 验收判据见 `test_真改坏闸文件时必须判红` —— ⛔ 那条不红就说明已退化。
    """
    p = tmp / "fx"
    (p / ".devloop").mkdir(parents=True)
    (p / "devloop").mkdir()
    (p / "tests").mkdir()
    #  ⭐ 真闸，原样复制。⛔ 一个字都不许改。
    shutil.copy2(REPO / ".devloop" / "gates.sh", p / ".devloop" / "gates.sh")
    (p / "devloop" / "__init__.py").write_text("", encoding="utf-8")
    (p / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n",
                                           encoding="utf-8")
    for a in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "fx"]):
        r = subprocess.run(["git", *a], cwd=p, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"⛔ 造 fixture 失败：git {a} → {r.stderr}"
    return ProjectPaths(p)


@pytest.fixture(scope="module")
def rosters(tmp_path_factory) -> tuple[set[str], set[str]]:
    #  ⭐ 对**样板仓库**跑，⛔ 不对自己跑（见 `_fixture_project` 的说明）。
    paths = _fixture_project(tmp_path_factory.mktemp("roster"))
    if not (REPO / ".devloop" / "gates.sh").exists():
        pytest.skip("本仓没有 .devloop/gates.sh")
    return _preflight_names(paths), _real_names(paths)


def test_真跑报出的闸名体检必须一个不少(rosters) -> None:
    """⛔ 差集不空 ⇒ 点名它的计划**还没花一分钱就被拒**。"""
    pre, real = rosters
    missing = sorted(real - pre)
    assert not missing, (
        f"⛔ 这几道真跑会报、体检却不报：{missing}\n"
        f"   ⇒ 任何 require_pass 点名它们的计划都会被 `_preflight_criteria` 当场拒掉，"
        f"而它们本身好好的。\n"
        f"   ⭐ 改法：在 `.devloop/gates.sh` 的体检段（`DEVLOOP_PREFLIGHT=1` 那一块）"
        f"给每道补一行 `pf`。\n"
        f"   体检报出：{sorted(pre)}\n   真跑报出：{sorted(real)}")


def test_体检报出的闸名真跑必须也报得出(rosters) -> None:
    """⛔⛔ 这一半比上一半贵：它让人**花完钱之后**才发现契约对不上。

    ⚠️ 2026-08-16 实测抓到两个：`环境自检`（真跑只 exit、不打判定行）、
    `改动守卫`（只在脏工作区那条路上出现）。
    """
    pre, real = rosters
    ghost = sorted(pre - real)
    assert not ghost, (
        f"⛔ 这几道体检报了、真跑却不报：{ghost}\n"
        f"   ⇒ 点名它们的计划会**体检放行**，然后在花完额度之后收"
        f"「闸与验收契约对不上」（退出码 2）。\n"
        f"   ⭐ 两种改法：让真跑那段**也打一行判定**（推荐），"
        f"或把体检里那一行删掉。\n"
        f"   ⛔ 别把体检那行改个名字了事 —— 那只是把谎换个说法。\n"
        f"   体检报出：{sorted(pre)}\n   真跑报出：{sorted(real)}")


def test_每份计划点名的闸体检都报得出(rosters) -> None:
    """⭐ 这一条是上面两条的**用途**：让计划真的派得出去。

    ⛔ `verify-0802.toml` 是**例外，且必须保持例外**：它点名 `基线守卫`，
    而本仓根本没有这道闸 —— 那是一份**故意点空**的计划，
    用来演练「闸与验收契约对不上」这条路。⚠️ 别为了凑绿去改它或补一道假闸。
    """
    from devloop import plan as plan_mod

    pre, _ = rosters
    known_empty = {"verify-0802"}
    rejected = {}
    for f in sorted((REPO / ".devloop" / "plans").glob("*.toml")):
        sp = plan_mod.load(f)
        named: set[str] = set()
        for t in sp.tasks:
            named |= set(plan_mod.effective_require_pass(sp, t))
        miss = sorted(n for n in named if n not in pre)
        if miss:
            rejected[f.stem] = miss

    assert set(rejected) <= known_empty, (
        f"⛔ 这几份计划会在开跑前被拒：{rejected}\n"
        f"   ⚠️ 允许被拒的只有 {sorted(known_empty)}（故意点空，用来演练契约对不上）。")
    assert "verify-0802" in rejected, (
        "⛔ `verify-0802` 不再被拒了 —— 要么有人给本仓补了一道假的 `基线守卫`，"
        "要么把这份计划改了。⚠️ 那会删掉唯一一次「闸与验收契约对不上」的演习。")


def test_闸脚本声明了自己支持体检() -> None:
    """⛔ 少了这行声明，`preflight` 返回 `unknown` —— 「没人验过」被读成「没问题」。"""
    src = (REPO / ".devloop" / "gates.sh").read_text(encoding="utf-8")
    assert "DEVLOOP-PREFLIGHT: 1" in src


def test_体检自己必须是绿的() -> None:
    """⭐ 绿检：什么都不改，体检判定必须是 `ok`。

    ⛔ 缺了它，上面几条可以靠「让体检对什么都报 VOID」蒙混过关 ——
    差集会归零，而整份体检退化成 `unknown`（= 谁也没验过）。
    ⚠️ 2026-08-16 真踩到：我给三道闸按 `DEVLOOP_CLEAN_CHECKOUT` 分支报 VOID，
    而体检自己天然拿不到那个标记 ⇒ 永远走 VOID ⇒ 判定从 ok 掉成 unknown。
    """
    verdict, lines, _ = gates_mod.preflight(ProjectPaths(REPO))
    assert verdict == "ok", f"⛔ 体检判定是 {verdict}，不是 ok。闸自己说的：{lines}"


def test_git不认识这个文件时要说清楚() -> None:
    """⚠️ 本仓的 `.devloop/gates.sh` **是被跟踪的**（受宪法 A-1 保护）。

    ⛔ 它一旦掉出版本管理，「禁改清单」那道闸就无从对照，
    而这条测试上面几条仍会全绿 —— 那是第①种假绿（守卫的目标不存在）。
    """
    p = subprocess.run(["git", "ls-files", "--error-unmatch", ".devloop/gates.sh"],
                       cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0, (
        "⛔ `.devloop/gates.sh` 不在版本管理里了 —— 「禁改清单」那道闸失去对照物，"
        "改坏了也没有上一版可退（宪法 A-1 保护的正是这个文件）。")


def test_真改坏闸文件时必须判红(tmp_path: Path) -> None:
    """⭐⭐ **B 的验收判据**（latch 11 号 §2.1 绑定）。

    ## ⛔ 它防的是什么

    上面那些测试改成「对样板仓库跑」之后，有一个静默退化的风险：
    ⚠️ **若样板仓库配的是一份假的 `gates.sh`，那这条测试验的就是
    「假闸的名册 = 假闸的实跑」—— 对真 `gates.sh` 零信息量。**

    ⇒ ⛔ 测试跑了、绿了、**测的是错的对象**。那正是假绿。

    ## ⭐ 所以判据是：故意改坏**真**闸文件，这套对账必须判红

    ⛔ 不红 ⇒ 说明样板仓库没在用真闸 ⇒ 「换个被测对象」已经退化成「干脆不验」。
    """
    #  ⭐ 造一份「真闸 + 一处名册漂移」：给体检段加一道真跑里不存在的闸名
    fx = _fixture_project(tmp_path)
    g = fx.project / ".devloop" / "gates.sh"
    src = g.read_text(encoding="utf-8")
    marker = 'pf "语法"'
    assert marker in src, "⛔ 真闸里找不到锚点 —— 本次红检作废，先修测试"
    g.write_text(
        src.replace(marker, 'pf "凭空多出来的一道闸" "OK" "x"\n    ' + marker, 1),
        encoding="utf-8")

    pre = _preflight_names(fx)
    real = _real_names(fx)

    assert "凭空多出来的一道闸" in pre, (
        "⛔ 改了真闸文件，体检却没报出来 —— 说明样板仓库用的不是这份闸。"
        "⚠️ 那意味着上面几条测试测的是错的对象（B 已退化成 A）。")
    assert "凭空多出来的一道闸" not in real, "⛔ 真跑里不该有这道"
    assert sorted(pre - real) == ["凭空多出来的一道闸"], (
        f"⛔ 名册漂移没被这套对账抓到。体检 {sorted(pre)} · 真跑 {sorted(real)}")
