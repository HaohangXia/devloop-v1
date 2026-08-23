"""闸的「空转即绿」缺陷（G-53）。

⛔ **这不是 Phase 7 的将来时，是今天就活着的假绿。**

实测复现（两种形态，都返回 `passed=True`）：

    A 全 SKIP  → code 0 · passed True · summary「0 过 / 0 未过」
    B 零输出   → code 0 · passed True · lines 0

`gates.py::GateResult.passed` 只看 `code == 0`；`_parse` 不要求任何行存在。
于是 **`gate_ok == True` 不代表任何东西被验过**——这是六种假绿里的第一种
（守卫的目标不存在），只是更彻底：**一个目标都没有，还报全过**。

现实触发路径不是假想：`C:\\pg\\eco-ob\\.devloop\\gates.sh:81-82` 有
`DEVLOOP_SKIP_TESTS=1` 分支，注释写着「不得用于真实验收」——**但注释对机器
没有约束力**，而 `gates.py` 把操作者 shell 的环境变量原样透传给闸。
也就是说：**一个环境变量能把验收闸变成空转，而工具报「全过」。**
"""

from __future__ import annotations

import textwrap

from devloop.config import ProjectPaths


def _gate(tmp_path, body: str):
    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    (proj / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
    (proj / "src").mkdir(exist_ok=True)
    return ProjectPaths(proj)


def test_一条都没验的闸不算绿_全跳过(tmp_path):
    """⛔ 全 SKIP 意味着**什么都没验**。它不是「活干得好」，是闸没法用于验收。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'SKIP\\t闸甲\\t跳过\\n'
        printf 'SKIP\\t闸乙\\t跳过\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project)
    assert not r.passed, "一条都没验，绝不能报『全过』"
    assert r.gate_broken, "这是闸自身不可用，不是『活没干好』——两者必须分开"
    assert "没有任何一道闸通过" in r.summary() or "验了 0 道" in r.summary()


def test_一条都没验的闸不算绿_零输出(tmp_path):
    """闸脚本什么都不打印就 exit 0——同上，且更隐蔽（连 SKIP 都看不见）。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, "exit 0\n")
    r = run_gates(paths, target=paths.project)
    assert not r.passed and r.gate_broken


def test_有真绿的闸照常算绿(tmp_path):
    """⚠️ 防回归：别把「更严」修成「一律判红」——那会让所有正常验收失效。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        printf 'SKIP\\t慢测\\t本次跳过\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project)
    assert r.passed and not r.gate_broken


def test_有失败的闸仍然报失败而不是闸坏了(tmp_path):
    """⚠️ 防回归：FAIL 是『活没干好』（1），不能被新逻辑误升成『闸坏了』（2）。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'FAIL\\t基线守卫\\t基线被改了\\n'
        exit 1
    """)
    r = run_gates(paths, target=paths.project)
    assert not r.passed and not r.gate_broken


def test_全失败没有一条PASS时仍归为活没干好(tmp_path):
    """⚠️ 边界：0 条 PASS 但有 FAIL —— 闸**工作正常**，只是活没过。
    这条把「空转」与「全红」区分开，前者是闸坏了，后者是活坏了。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'FAIL\\t甲\\t不行\\n'
        printf 'FAIL\\t乙\\t也不行\\n'
        exit 1
    """)
    r = run_gates(paths, target=paths.project)
    assert not r.passed
    assert not r.gate_broken, "有 FAIL 说明闸真的在验，只是活没过"


# ── 点名验收：光看「全过」不够 ──────────────────────────────

def test_点名的闸没跑到必须判红(tmp_path):
    """⛔ 即便修掉「空转即绿」，一个 5 道闸里 4 道 SKIP、1 道 PASS 的结果
    仍然会是「绿」。**验收必须能点名**：这几道闸必须是 PASS。

    这是 G-42 那种假绿的形态——自检存在、判据正确，**但只覆盖了一部分输入**。
    """
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        printf 'SKIP\\tM1 回归\\tDEVLOOP_SKIP_TESTS=1\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project, require_pass=["语法", "M1 回归"])
    assert not r.passed, "点名要求的闸只是 SKIP，不能算过"
    assert "M1 回归" in r.summary(), "必须点名是哪一道没到"


def test_点名的闸不存在必须判为闸坏了(tmp_path):
    """⛔ **第一种假绿的正面反测**：点名了一道**根本不存在**的闸。
    如果实现只检查「没有 FAIL」，这里会绿——而实际上守卫的目标不存在。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project, require_pass=["语法", "根本没有这道闸"])
    assert not r.passed
    assert r.gate_broken, "点名的闸不存在 = 闸与验收契约对不上，属闸自身故障"
    assert "根本没有这道闸" in r.summary()


def test_点名全部命中才算过(tmp_path):
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        printf 'PASS\\t基线守卫\\tok\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project, require_pass=["语法", "基线守卫"])
    assert r.passed and not r.gate_broken


def test_不点名时保持原有行为(tmp_path):
    """⚠️ 防回归：`require_pass` 是可选的，不传就是现在的语义。"""
    from devloop.gates import run_gates
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        exit 0
    """)
    assert run_gates(paths, target=paths.project).passed


# ── 环境变量卫生 ────────────────────────────────────────────

def test_操作者环境里的跳过开关必须被报出来(tmp_path, monkeypatch):
    """⛔ `gates.py` 把 `os.environ` 原样透传给闸。操作者 shell 里一个
    `DEVLOOP_SKIP_TESTS=1` 就能把真实验收变成空转，而工具一个字都不说。

    ⚠️ 不粗暴剥离——那会砸掉闸自身的快速自测。**但必须说出来**：
    「有跳过开关生效」这件事，人有权在看结果之前知道。
    """
    from devloop.gates import run_gates
    monkeypatch.setenv("DEVLOOP_SKIP_TESTS", "1")
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project)
    assert r.skip_switches, "生效中的跳过开关必须被记录下来"
    assert "DEVLOOP_SKIP_TESTS" in r.summary(), "必须在给人看的结论里说出来"


def test_没有跳过开关时不加噪音(tmp_path, monkeypatch):
    from devloop.gates import run_gates
    monkeypatch.delenv("DEVLOOP_SKIP_TESTS", raising=False)
    paths = _gate(tmp_path, """
        printf 'PASS\\t语法\\tok\\n'
        exit 0
    """)
    r = run_gates(paths, target=paths.project)
    assert not r.skip_switches
    assert "DEVLOOP_SKIP" not in r.summary()
