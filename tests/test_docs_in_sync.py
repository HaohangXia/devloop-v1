"""⛔ 文档与代码对不上时，**测试当场变红**。

## 为什么这道检查必须存在

2026-08-02 用户问了一句「这些改动都写进文档了吗」，实查结果是**没有**：
`devloop nightly` 四份文档零提及、`prune` 的三个新开关基本没有、
刚合进来的两份模板零提及、测试条数写着 589 而实际 612。

⚠️ 而在那之前的**同一天**，我刚写完一份「动手前照着过一遍」的防坑清单，
里面第一条就是「实现了但生产路径够不着」。⛔ 然后我自己把文档同步这件事
留在了脑子里——**一条只活在脑子里（或只写在文档里）的规矩必然腐烂**，
这正是本项目反复栽的那个形状。

⭐ 所以答案不是「我记住」，是**让机器逼着做**。

## ⚠️ 这道检查的边界

它只管**能机械对账**的四件事，⛔ 管不了「写得对不对、讲没讲清楚」：

| 管 | 不管 |
|---|---|
| 子命令有没有被文档提到 | 提到的那段话准不准 |
| 开关有没有被提到 | 开关的语义讲没讲清 |
| `templates/` 每份文件有没有被点名 | 模板本身好不好用 |
| 文档里的测试条数是不是真的 | 那些测试有没有意义 |

⛔ **别把它当成「文档已经好了」的证据。** 它只挡住「压根没写」。

## ⚠️ 关于噪音

误报会训练人忽略检查——这是本项目写过的话。所以：
· 判据落在 **AST** 上（不是子串），不会被注释里的示例误伤
· 有白名单，但每一条都要写清**为什么它不必进文档**
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SPEC = _ROOT / "SPEC.md"
_READMES = (_ROOT / "README.md", _ROOT / "README.zh-CN.md")
# the internal planning doc is not part of the public snapshot; README carries the counts.

#  ⛔ 白名单：每一条都要写清**为什么它不必进面向使用者的文档**。
#  ⚠️ 往这里加一行之前先问：是它真的不该进文档，还是我不想写？
_FLAG_EXEMPT = {
    #  内部批处理开关，只被 `--detach` 起的后台作业自己用，人不直接打
    "--batch",
    #  由编排方在派单时自动传入（当场算指纹当场传），⛔ 人手打反而危险
    "--expect-fingerprint",
    #  `eval` 的调试用筛子（只跑某几道题），⚠️ 不是正式用法
    "--only",
}


def _cli_source() -> str:
    from devloop import cli
    return inspect.getsource(cli)


def _subcommands() -> list[str]:
    """从 AST 里取子命令名。⛔ 不判子串——注释里的示例会误伤。"""
    return sorted({
        n.args[0].value
        for n in ast.walk(ast.parse(_cli_source()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_parser" and n.args
        and isinstance(n.args[0], ast.Constant)
    })


def _long_flags() -> set[str]:
    return {
        a.value
        for n in ast.walk(ast.parse(_cli_source()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_argument"
        for a in n.args
        if isinstance(a, ast.Constant) and str(a.value).startswith("--")
    }


# ── 子命令 ────────────────────────────────────────────────────────

def test_每个子命令都要在SPEC里有说明() -> None:
    """⛔ `devloop nightly` 加进 CLI 的当天，四份文档零提及。"""
    spec = _SPEC.read_text(encoding="utf-8")
    missing = [c for c in _subcommands() if f"devloop {c}" not in spec]
    assert not missing, (
        f"⛔ 这些子命令在 SPEC.md 里没有说明：{missing}\n"
        f"   ⭐ 加进「其他子命令」那一节，写清它干什么、退出码是什么。")


def test_每个子命令都要在README里出现() -> None:
    """⚠️ README 是给外人看的第一眼——命令表漏一条，那条就等于不存在。"""
    missing = {}
    for readme in _READMES:
        rd = readme.read_text(encoding="utf-8")
        absent = [c for c in _subcommands()
                  if f"cli {c}" not in rd and f"devloop {c}" not in rd]
        if absent:
            missing[readme.name] = absent
    assert not missing, f"⛔ 这些 README 的命令表不完整：{missing}"


# ── 开关 ──────────────────────────────────────────────────────────

def test_每个长开关都要在SPEC里出现() -> None:
    """⛔ `prune --archive/--delete/--discard` 加完之后一个都没进文档。

    ⚠️ 免检的必须进白名单，且**写清为什么**——
    「不想写」不是理由，⭐ 那正是这道检查要挡的东西。
    """
    spec = _SPEC.read_text(encoding="utf-8")
    missing = sorted(f for f in _long_flags()
                     if f not in _FLAG_EXEMPT and f not in spec)
    assert not missing, (
        f"⛔ 这些开关在 SPEC.md 里没有说明：{missing}\n"
        f"   ⚠️ 若某个确实不该进文档，加进本文件的 `_FLAG_EXEMPT` "
        f"并写清理由。")


# ── templates ─────────────────────────────────────────────────────

def test_templates里每份文件都要被SPEC点名() -> None:
    """⛔ 这一格已经与磁盘事实不符过**两次**：一处写「四份齐了 ✅」却列了五项，
    另一处写「还差 gates.sh」而那份有 197 行早就在。

    ⭐ 判据落在「每份文件有没有被点名」上，⛔ **不判那个份数数字**——
    数字是会腐烂的，而「每份都被提到」不会。
    """
    spec = _SPEC.read_text(encoding="utf-8")
    tpl = sorted(p.name for p in (_ROOT / "templates").iterdir() if p.is_file())
    missing = [f for f in tpl if f not in spec]
    assert not missing, (
        f"⛔ `templates/` 下这几份没被 SPEC.md 点名：{missing}\n"
        f"   ⚠️ 新项目接入照着 templates/ 抄，没写进文档等于没有。")


# ── 测试条数 ──────────────────────────────────────────────────────

def _claimed_counts() -> list[tuple[str, int]]:
    """把文档里「N 条测试」这种断言全找出来。"""
    out = []
    for f in _READMES:
        for m in re.finditer(r"(\d+)\s*(?:tests|[条项]测试)", f.read_text(encoding="utf-8")):
            out.append((f.name, int(m.group(1))))
    return out


def test_文档里写的测试条数必须是真的() -> None:
    """⛔ 实测写着 589 而真实是 612。

    ⚠️ 那句话是拿来当**证据**用的（「有真数据背书，不是设计稿」）——
    ⭐ 一个当证据用的数字如果是旧的，它就不是证据，是装饰。

    ⚠️ 代价：每次加测试都要顺手改一下这个数。⛔ 那是**有意**的摩擦，
    这道检查存在的全部理由就是不让它靠人记。
    """
    claimed = _claimed_counts()
    #  ⛔ **不许 skip。** 2026-08-03 实测：我用一个空变量跑 sed，把那份文档
    #     里的数字抹成了空（「35 单真跑、 条测试」）——⭐ 这条检查当场**转绿**。
    #     ⚠️ 那正是本仓第一种假绿：**守卫的目标不存在**，于是守卫报「没事」。
    #  ⭐ 判据改成：那句话必须在、且必须带一个数。数字消失本身就是缺陷，
    #     因为「35 单真跑、686 条测试」是拿来当**证据**用的一句话。
    missing_claims = sorted(
        f.name for f in _READMES if not any(name == f.name for name, _ in claimed)
    )
    assert not missing_claims, (
        f"⛔ 这些 README 里一处「N 条测试」都找不到：{missing_claims}\n"
        "   ⚠️ 那句话是当证据用的；数字被删掉 = 证据没了，\n"
        "   ⛔ 而这条检查一旦对此 skip，它就成了自己要防的那种假绿。")
    #  ⛔ 真去数一遍。⚠️ 用 --collect-only 而不是跑全量：这条测试自己也在里面，
    #     跑全量会递归。
    #  ⛔ **不许猜输出格式**：本仓 `addopts = "-q"`，`--collect-only -q` 的输出是
    #     逐文件的 `tests/xxx.py: N`，⚠️ **没有**「N tests collected」那一行
    #     （第一版判据就是照想象写的，当场判错）。⭐ 逐行加起来才是能直接量的。
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    assert r.returncode == 0, (
        f"⛔ 测试收集失败，不能把部分收集结果当成完整计数：\n"
        f"{r.stdout[-1500:]}\n{r.stderr[-500:]}")
    per_file = re.findall(r"^\S+\.py:\s*(\d+)\s*$", r.stdout, re.M)
    assert per_file, f"⛔ 数不出实际条数：{r.stdout[-300:]}"
    real = sum(int(x) for x in per_file)
    wrong = [(f, n) for f, n in claimed if n != real]
    assert not wrong, (
        f"⛔ 文档里的测试条数与实际（{real}）对不上：{wrong}\n"
        f"   ⭐ 改法：把那几处的数字换成 {real}。")


# ── ⛔ 红检：一道恒过的检查与没有检查没区别 ───────────────────────

def test_这几道检查真的抓得到() -> None:
    """⚠️ 本项目栽过「为一个从未存在过的字符串写的守卫」——
    ⛔ 它通过了红检，因为红检用的是脑子里想象的错误写法。
    ⭐ 这里用**真实的**判据函数，喂给它一份缺东西的假文档。
    """
    subs = _subcommands()
    assert subs, "⛔ 一个子命令都没解析出来——判据是空的"
    assert "nightly" in subs and "prune" in subs, subs

    flags = _long_flags()
    assert "--discard" in flags and "--archive" in flags, sorted(flags)

    #  假文档：什么都没写 → 每一道都该报缺
    fake = "这份文档什么都没说。"
    assert [c for c in subs if f"devloop {c}" not in fake] == subs
    assert sorted(f for f in flags if f not in _FLAG_EXEMPT and f not in fake)


def test_白名单里的每一条都要有理由() -> None:
    """⛔ 白名单是这道检查唯一的后门。⚠️ 没有理由的豁免 = 悄悄把检查掏空。"""
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    block = src.split("_FLAG_EXEMPT = {")[1].split("}")[0]
    for f in _FLAG_EXEMPT:
        i = block.index(f'"{f}"')
        before = block[:i]
        #  ⚠️ 判据：这一行**上面**必须紧挨着注释
        assert before.rstrip().rstrip('"').rstrip().endswith(
            tuple("。）」)")) or "#" in before.rsplit("\n", 2)[-2], \
            f"⛔ 白名单里的 {f} 没写为什么免检"
