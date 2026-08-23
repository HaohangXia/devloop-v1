"""点名的闸没过时，退出码要按**诊断方向**分岔（H-2）。

## ⛔ 缺陷的形状

`GateResult.code` 自己的注释写着 `0 全过 · 1 有未过 · 2 闸自身故障`，
`gate_broken` 的 docstring 更明说「**必须与「活没干好」区分，否则环境故障
会被误判成质量问题**」。而 `run_gates` 的 `not_pass` 分支一律返回 2。

实测对照——**同一个 FAIL，点名了判 2、不点名判 1**：

| 点名的闸判定 | 脚本 exit | 改前 code | 该是 |
|---|---|---|---|
| FAIL | 1 | **2** | **1** |
| SKIP | 0 | 2 | 2 |
| VOID | 0 | 2 | 2 |
| 不点名 + FAIL | 1 | 1 | 1 |

⚠️ 「被点名」不该改变「活没干好」这个事实的性质。

## ⭐ 为什么 SKIP/VOID 必须留在 2

| 档 | 事实 | 谁能修 | 重试有没有用 | 码 |
|---|---|---|---|---|
| FAIL | 闸验了，判否 | 工人 | **有**，那正是 retries 的用处 | 1 |
| SKIP | 闸能验，被开关跳过 | 人（关开关） | ⛔ 无。开关在环境里，工人重试 N 次撞 N 次同一堵墙、烧 N 份额度 | 2 |
| VOID | 这个项目里没有可验之物 | 人（改计划或改闸） | ⛔ 无。**守卫的目标不存在**，是计划↔闸的契约对不上 | 2 |
| FAIL + SKIP/VOID 混合 | 一部分验收压根没跑 | 人先，工人后 | ⛔ 不能只说「活没干好」 | 2（保守） |

混合取 2 的理由：判 1 的代价是自动驾驶去重试一个**结构上不可能变绿**的东西
（真花钱）；判 2 的代价是叫人多看一眼（不花钱）。⚠️ 不对称，所以往 2 靠。

⚠️ **这不是放宽**：`passed` 在 1 和 2 下同样为 False，`verified` 不变，
「能不能放行」的结论一个字没变。变的只有**诊断方向**。
"""

from __future__ import annotations

import textwrap

from devloop.config import ProjectPaths
from devloop.gates import GateLine, GateResult, run_gates


def _gate(tmp_path, body: str) -> ProjectPaths:
    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
    return ProjectPaths(proj)


# ── 判定表 ────────────────────────────────────────────────────────

def test_点名的闸FAIL是活没干好不是闸坏了(tmp_path) -> None:
    """⛔ 这是本条的核心：FAIL 意味着闸**成功地**完成了工作并给出否定结论。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed 128 passed\n'
        exit 1
    """)
    r = run_gates(p, target=p.project, require_pass=["语法", "pytest"])
    assert r.code == 1, f"⛔ 点名的闸 FAIL 判成了 {r.code}——把活没干好说成闸坏了"
    assert not r.passed
    assert not r.gate_broken
    assert "闸自身故障" not in r.summary(), \
        "⛔ code 1 的说辞里不许出现「闸自身故障」——归类器靠这四个字分岔"


def test_不点名的同一个FAIL判一样的码(tmp_path) -> None:
    """⚠️ 「被点名」不该改变事实的性质。这是判定表里的关键对照行。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed\n'
        exit 1
    """)
    named = run_gates(p, target=p.project, require_pass=["语法", "pytest"]).code
    plain = run_gates(p, target=p.project).code
    assert named == plain == 1, f"点名 {named} vs 不点名 {plain}——同一个 FAIL 判了两种码"


def test_点名的闸SKIP仍然是闸不可用(tmp_path) -> None:
    """⛔ 开关在环境里，工人重试 N 次撞 N 次同一堵墙、烧 N 份额度。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'SKIP\tpytest\t被 DEVLOOP_SKIP_TESTS 跳过\n'
        exit 0
    """)
    r = run_gates(p, target=p.project, require_pass=["语法", "pytest"])
    assert r.code == 2 and r.gate_broken, "SKIP 不是活没干好，重试它没有意义"


def test_点名的闸VOID仍然是闸不可用(tmp_path) -> None:
    """⛔ 守卫的目标不存在——是计划↔闸的契约对不上，与 `missing` 同类。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'VOID\t基线守卫\t本项目没有基线文件\n'
        exit 0
    """)
    r = run_gates(p, target=p.project, require_pass=["语法", "基线守卫"])
    assert r.code == 2 and r.gate_broken


def test_FAIL和SKIP混在一起取保守的那一档(tmp_path) -> None:
    """⚠️ 判 1 的代价是去重试一个结构上不可能变绿的东西（花钱）；
    判 2 的代价是叫人多看一眼（不花钱）。⛔ 不对称，往 2 靠。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed\n'
        printf 'SKIP\t改动守卫\t工作区脏\n'
        exit 1
    """)
    r = run_gates(p, target=p.project,
                  require_pass=["语法", "pytest", "改动守卫"])
    assert r.code == 2, "混合里有没验到的东西，不能只说「活没干好」"
    s = r.summary()
    assert "pytest" in s, "⛔ 混合场景里 FAIL 被那句 SKIP 的说辞掩盖了"


def test_点名了闸根本没报的那道仍然判二(tmp_path) -> None:
    """⚠️ 这一档改动前后都是 2，钉住它别被顺手改掉。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        exit 0
    """)
    r = run_gates(p, target=p.project, require_pass=["语法", "根本没有这道"])
    assert r.code == 2 and r.gate_broken


# ── ⛔ 闸自相矛盾：打印 FAIL 却 exit 0 ────────────────────────────
#
# ⚠️ **这一条比上面整组都严重**，而且是不点名时才最危险的那一档。
# 实测（改前）：`PASS 语法` + `FAIL pytest` + `exit 0`、**不点名** →
#     passed = True · gate_broken = False · summary = 「1 过 / **1 未过**」
# ⛔ 判定与它自己的摘要**自相矛盾**，而结论是**放行**——坏产出直接流下去。
#
# ⭐ 归 2（闸自身故障）而不是 1，理由与同文件 `code not in (0,1,2)` 那段同源：
#    契约写着 `0 = 全过`，打印了 FAIL 还 exit 0 **就是协议违规**，
#    「我不认识这个结果」本身就是闸故障。而且诊断方向也对——
#    这类脚本的病因几乎总是 `bad()` 在子 shell 里 `fail=1` 没传出来，
#    ⚠️ 要修的确实是闸，不是活。

def test_闸打印FAIL却exit0不许放行_不点名(tmp_path) -> None:
    """⛔ 最危险的一档：没有 require_pass 兜着。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed 128 passed\n'
        exit 0
    """)
    r = run_gates(p, target=p.project)
    assert not r.passed, "⛔ 闸自己印了 FAIL，工具却放行了"
    assert r.code == 2 and r.gate_broken, \
        "⛔ 打印 FAIL 却 exit 0 是协议违规——契约写着 0 = 全过"
    assert "pytest" in r.summary()


def test_闸打印FAIL却exit0也不许因为点名而变一(tmp_path) -> None:
    """⚠️ 我在 30 分钟前把这一档写成了 `code == 1`——**当时的推理是错的**。

    ⛔ 判据不该是「点名的那道是 FAIL 所以算活没干好」，而该是
    「这个闸的退出码与它自己的输出对不上，它的任何结论都不能用于验收」。
    ⭐ 后者优先级更高，所以要在 require_pass 之前就拦掉。
    """
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t自相矛盾：打印 FAIL 却 exit 0\n'
        exit 0
    """)
    r = run_gates(p, target=p.project, require_pass=["pytest"])
    assert r.code == 2 and not r.passed


def test_正常的FAIL加exit1不受影响(tmp_path) -> None:
    """⛔ 反向钉住：别把「闸工作正常、活没干好」也一起判成闸坏了。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed\n'
        exit 1
    """)
    r = run_gates(p, target=p.project)
    assert r.code == 1 and not r.gate_broken


# ── summary 不许在故障路径上吞掉行级明细 ──────────────────────────

def test_闸自身故障时行级明细不许蒸发() -> None:
    """⛔ `constitution.py::ConstitutionResult.summary` 在 broken 分支**照样**
    把已查出的 hits 列出来，注释写着「诊断信息不许在故障路径上蒸发」。
    ⚠️ 闸这边此前正是宪法那边明令禁止的形态：`if gate_broken: return` 一句短路，
    后面统计 FAIL / SKIP / VOID 的代码一行都走不到。"""
    r = GateResult(2, [GateLine("FAIL", "pytest", "3 failed"),
                       GateLine("SKIP", "改动守卫", "工作区脏"),
                       GateLine("PASS", "语法", "好")],
                   stderr="验收点名的闸没有全部通过：改动守卫（SKIP）")
    s = r.summary()
    assert "闸自身故障" in s, "开头仍要说清这是闸不可用"
    assert "pytest" in s, "⛔ FAIL 明细在故障路径上蒸发了"
    assert "改动守卫" in s, "⛔ SKIP 分档在故障路径上蒸发了"


def test_stderr很长也不许把统计一起截掉() -> None:
    """⚠️ 顺序有讲究：统计必须放在被 `[:300]` 截断的 stderr **之后**。"""
    r = GateResult(2, [GateLine("FAIL", "某道闸", "x")], stderr="很长的原因。" * 80)
    assert "某道闸" in r.summary(), "⛔ 统计被 stderr 的截断连坐了"


def test_code1的summary本来就不丢明细不许顺手加东西() -> None:
    """⚠️ 点名的 FAIL 本来就在 `bad` 里。⛔ 别给 code 1 那条路顺手加料。"""
    s = GateResult(1, [GateLine("PASS", "语法", ""),
                       GateLine("FAIL", "pytest", "3 failed")]).summary()
    assert s.startswith("1 过 / 1 未过"), s
    assert "闸自身故障" not in s


# ── ⛔ 镜像的另一半：exit 1 却零 FAIL 行 ─────────────────────────

def test_exit1却零FAIL行也是自相矛盾(tmp_path) -> None:
    """⛔ 对抗复核指出：`code==0 ∧ 有 FAIL` 与 `code==1 ∧ 零 FAIL` 是对称的
    两半，上面只补了一半。

    ⚠️ 后果比看起来重：归类给的建议是「读上面闸的结论，看是哪一道没过」
    ——一道都没有；而 `retries` 会拿这个**没有内容的失败**反复重试，
    每次烧一份额度去修一个闸没说哪里错的东西。
    """
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'PASS\t别的\t好\n'
        exit 1
    """)
    r = run_gates(p, target=p.project)
    assert r.code == 2 and r.gate_broken, \
        "⛔ 说有闸没过却一行没说是哪道——这个退出码无从解释"
    assert "一行 FAIL 都没印" in r.summary()


def test_有FAIL行的exit1照旧是活没干好(tmp_path) -> None:
    """⛔ 反向钉住：别把正常的「活没干好」也判成闸坏了。"""
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'FAIL\tpytest\t3 failed\n'
        exit 1
    """)
    assert run_gates(p, target=p.project).code == 1


def test_SKIP加exit0不许被顺手改成矛盾(tmp_path) -> None:
    """⛔ **这条钉的是一个刻意不改的决定。**

    `skip()` 故意不置 `fail=1`：SKIP 的含义是「**没验**」，不是「验了没过」，
    与 `exit 0`（没有东西失败）自洽。⭐ 放不放行是 `require_pass` 的政策问题
    ——点名了就落 code 2 交人（上面已有测试）。

    ⚠️ 把它也判成矛盾，会把「本项目这道闸不适用」变成「闸坏了」，
    ⛔ 那是把绿说成坏，比现在更糟。
    """
    p = _gate(tmp_path, r"""
        printf 'PASS\t语法\t好\n'
        printf 'SKIP\tpytest\t被开关跳过\n'
        exit 0
    """)
    r = run_gates(p, target=p.project)
    assert r.code == 0 and r.passed, "⛔ 有人把 SKIP+exit0 也判成矛盾了"
    assert "SKIP" in r.summary(), "⚠️ 但空转必须报出来"
