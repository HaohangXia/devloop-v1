"""审计型任务：**发现**要能落盘、能核、能变成任务书。

## ⛔ 这个模块补的是哪一块

2026-08-01 eco-ob 首跑的最大结论（`_trials/eco-ob-01/50_关于工具的结论.md` T-7）：
**这一天最值钱的产出不是那次派单，是围绕它跑的几轮审计**——
土蚁被冻结 8 天、三物种灭绝、性能超标 21 倍、标准答案自我参照……
**一条都不来自派单**。

⚠️ 而扇出机制**本来就有**（`--tools readonly` + `--task-dir` + `--parallel`），
缺的只有三样：

1. 工人的发现是**自由文本**，没法聚合、没法比对、没法追踪
2. 没有**独立复核**那一层（首跑当天它抓出了我 5 条判断里的 4 条错）
3. 发现**变不成任务书**——查出来的问题要人手抄一遍才能派出去修

⛔ 所以本模块**只做这三样**，不重造扇出。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devloop import audit

REPORT = """\
读了一圈，结论如下。

<<<DEVLOOP-FINDINGS
- claim: 土蚁的密度从头到尾没变过
  evidence: ref_m1.json 六个检查点恒为 194.299999999993
  confidence: 已核实
  severity: 关键
- claim: 性能断言的及格线被抬过
  evidence: test_m1.gd::_test_tick_budget 的 sustained <= 7.5，git 历史里原为 0.25
  confidence: 已核实
  severity: 重要
DEVLOOP-FINDINGS>>>

以上。
"""


def test_从回执里解析出发现() -> None:
    fs = audit.parse(REPORT)
    assert len(fs) == 2
    assert fs[0].claim.startswith("土蚁")
    assert "194.299999999993" in fs[0].evidence
    assert fs[0].severity == "关键"


def test_没有发现块时返回空而不是崩() -> None:
    """⚠️ 工人可能什么都没找到——那是合法结果，不是错误。"""
    assert audit.parse("我看了一圈，没发现问题。") == []


def test_发现块为空也不崩() -> None:
    assert audit.parse("<<<DEVLOOP-FINDINGS\nDEVLOOP-FINDINGS>>>") == []


def test_缺字段的条目要被拒绝而不是静默补默认值() -> None:
    """⛔ 静默补默认值 = 一条没有证据的发现混进台账，看起来和真的一样。"""
    bad = "<<<DEVLOOP-FINDINGS\n- claim: 只有断言没有证据\n  severity: 关键\nDEVLOOP-FINDINGS>>>"
    with pytest.raises(audit.MalformedFinding) as e:
        audit.parse(bad)
    assert "evidence" in str(e.value)


def test_把关键度写错的要被拒绝() -> None:
    bad = ("<<<DEVLOOP-FINDINGS\n- claim: x\n  evidence: y\n"
           "  confidence: 已核实\n  severity: 天塌了\nDEVLOOP-FINDINGS>>>")
    with pytest.raises(audit.MalformedFinding):
        audit.parse(bad)


# ── 落盘 ──────────────────────────────────────────────────────────

def test_写台账并能读回(tmp_path: Path) -> None:
    fs = audit.parse(REPORT)
    p = tmp_path / "findings.jsonl"
    audit.append(p, fs, source="p1-lens-a")
    back = audit.load(p)
    assert len(back) == 2
    assert back[0].source == "p1-lens-a"
    assert back[0].id, "每条要有稳定 id，⛔ 否则没法引用、没法去重"


def test_同一条发现重复提交不会翻倍(tmp_path: Path) -> None:
    """⚠️ 多个分析员从不同角度看，撞上同一条是**常态**。
    ⛔ 台账里存两份的话，「找到 N 条问题」这个数就假了。"""
    fs = audit.parse(REPORT)
    p = tmp_path / "f.jsonl"
    audit.append(p, fs, source="a")
    audit.append(p, fs, source="b")
    assert len(audit.load(p)) == 2, "重复条目没被去掉"


def test_不同角度撞上同一条要记下都有谁报过(tmp_path: Path) -> None:
    """⭐ 两个独立视角报同一条，本身就是证据强度——⛔ 别把它丢了。"""
    fs = audit.parse(REPORT)
    p = tmp_path / "f.jsonl"
    audit.append(p, fs, source="a")
    audit.append(p, fs, source="b")
    assert set(audit.load(p)[0].sources) == {"a", "b"}


# ── 复核 ──────────────────────────────────────────────────────────

def test_生成的复核任务书要求推翻而不是确认(tmp_path: Path) -> None:
    """⛔ 「请确认这条对不对」拿到的永远是「对」。
    首跑当天 5 条判断错了 4 条，全是被**要求推翻**的复核抓出来的。"""
    spec = audit.verify_spec(audit.parse(REPORT)[0])
    assert "推翻" in spec
    assert "确认" not in spec.split("# 任务")[1][:200], "任务段不许出现「确认」的措辞"
    for seg in ("# 角色", "# 任务", "# 禁令"):
        assert seg in spec, f"缺 {seg} —— TaskSpec.load 会拒绝"


def test_复核任务书是只读的(tmp_path: Path) -> None:
    spec = audit.verify_spec(audit.parse(REPORT)[0])
    assert "不许改" in spec or "只读" in spec


# ── 变成修复任务书 ────────────────────────────────────────────────

def test_能把发现变成修复任务书() -> None:
    spec = audit.fix_spec(audit.parse(REPORT)[0])
    for seg in ("# 角色", "# 任务", "# 禁令"):
        assert seg in spec
    assert "194.299999999993" in spec, "⛔ 证据必须原样带进任务书，不然工人得重查一遍"


def test_生成的任务书真能被TaskSpec加载(tmp_path: Path) -> None:
    """⛔ 生成一份 TaskSpec 拒收的任务书 = 到派单那一刻才炸。"""
    from devloop.models import TaskSpec
    for maker in (audit.verify_spec, audit.fix_spec):
        f = tmp_path / f"{maker.__name__}.md"
        f.write_text(maker(audit.parse(REPORT)[0]), encoding="utf-8")
        TaskSpec.load(f)


def test_未复核的发现不许被当成结论() -> None:
    """⚠️ 「找到 12 条问题」和「12 条经独立复核成立」是两回事。"""
    fs = audit.parse(REPORT)
    assert all(f.verified is None for f in fs), "刚解析出来就带着复核结论？"
    s = audit.summary(fs)
    assert "未复核" in s, s


# ── 接线：⛔ 光有模块没用，要证明派单真的在收 ──────────────────────

def test_派单跑完真的会收发现() -> None:
    """⛔ 这个项目栽过三次「实现了但生产路径没调」。判据落在源码上。

    ⚠️ 而且**不能只在 readonly 时收**——写任务的工人同样会看到别的问题，
    任务书还明令它「看到就写进报告、一个字都不许改」。
    首跑那一单（写操作）就报了 4 条顺手发现，其中一条是真 bug。
    """
    import ast
    import inspect

    from devloop import cli

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    src = ast.dump(fn)
    assert "audit_mod" in src or "'parse'" in src, \
        "⛔ `_run_unit` 里没有收发现——审计的产出永远进不了台账"
    #  ⛔ 不许被 `if tools == "readonly"` 之类的条件圈起来
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "parse"):
            continue
        seg = ast.dump(fn)
        assert '"readonly"' not in seg.split("audit_mod")[0][-400:], \
            "⛔ 收发现被 readonly 条件圈住了——写任务的顺手发现会丢"
        return
    raise AssertionError("找不到 audit 的 parse 调用")


def test_格式坏掉不许打死这一单() -> None:
    """⚠️ 钱在派单那一刻就花掉了，回执还在盘上，人还能手工看。
    ⛔ 但必须**报出来**，不许静默吞。"""
    import ast
    import inspect

    from devloop import cli

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers]
    caught = [h for h in handlers
              if "MalformedFinding" in ast.dump(h.type) if h.type is not None]
    assert caught, "⛔ 发现块格式坏了会打死整单——那时钱已经花了"
    body = ast.dump(caught[0])
    assert "out" in body and ("append" in body or "Call" in body), \
        "⛔ 捕获了却没报出来 = 静默吞"


# ── ⛔ 台账必须真·只追加（2026-08-02 实测撞到的阻断） ──────────────

def test_台账是真追加不是整文件重写(tmp_path: Path) -> None:
    """⛔ **两个模块的设计互相打架，实测复现过。**

    `records.py` 的 T5 判据是「**只追加**」——前 N 字节的指纹必须一模一样。
    而 `append()` 原来是「读全文 → 内存合并 → 整个覆盖」：
    第二个角度报出**同一条 claim** 时，那行的 `sources` 变长 → 后面全部移位
    → `records.verify` 判「前缀被改写（抹掉历史再补）」。

    ⚠️ 而本模块的 docstring 自己写着「多个分析员撞上同一条是**常态**」，
    `records.py` 的 docstring 写着「天天喊狼来了的守卫会被人关掉」。

    ⛔ 后果：一批里第 1 单收了条重复发现，**第 2 单就被宪法判失败**；
    自动驾驶下 `snap_before` 整个阶段只采一次，**剩下的单全部连坐**。
    与刚修掉的 G-71 是同一个形状，只是肇事者换成了 findings。

    ⭐ 判据落在**字节**上：写第二次之后，文件的前 N 字节必须原样不动。
    """
    p = tmp_path / "f.jsonl"
    fs = audit.parse(REPORT)
    audit.append(p, fs, source="角度A")
    head = p.read_bytes()
    audit.append(p, fs, source="角度B")          # 同一条，第二个来源
    after = p.read_bytes()
    assert after.startswith(head), (
        "⛔ 前缀被改写了——records.verify 会把这判成「抹掉历史再补」。\n"
        f"   之前 {len(head)} 字节，之后 {len(after)} 字节")


def test_重复提交后读出来仍然只有一条(tmp_path: Path) -> None:
    """⚠️ 改成只追加之后最容易出的新毛病：去重丢了，「找到 N 条」这个数变假。"""
    p = tmp_path / "f.jsonl"
    fs = audit.parse(REPORT)
    audit.append(p, fs, source="a")
    audit.append(p, fs, source="b")
    audit.append(p, fs, source="c")
    assert len(audit.load(p)) == 2, "去重丢了"
    assert set(audit.load(p)[0].sources) == {"a", "b", "c"}, "撞上的来源没合全"


def test_并发写不丢发现(tmp_path: Path) -> None:
    """⛔ 并行审计是**默认形态**（只读默认并发 4）——多个工人同时往这里写。
    ⚠️ 旁边的 telemetry 早就为同样的问题加过锁（线程锁 + 文件锁 + fsync），
    这边一条都没抄过来。"""
    import concurrent.futures as cf

    p = tmp_path / "f.jsonl"
    def w(i):
        blk = (f"<<<DEVLOOP-FINDINGS\n- claim: 第 {i} 条发现\n"
               f"  evidence: probe-{i}\n  confidence: 已核实\n"
               f"  severity: 一般\nDEVLOOP-FINDINGS>>>")
        audit.append(p, audit.parse(blk), source=f"w{i}")
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(w, range(8)))
    assert len(audit.load(p)) == 8, f"⛔ 并发写丢了发现，只剩 {len(audit.load(p))} 条"


def test_records的守卫不再误报findings(tmp_path: Path) -> None:
    """⭐ 端到端：正是真跑里会发生的那一幕。"""
    import subprocess

    from devloop import records

    d = tmp_path / "proj"
    (d / ".devloop").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], capture_output=True)
    f = d / ".devloop" / "findings.jsonl"
    fs = audit.parse(REPORT)
    audit.append(f, fs, source="角度A")
    before = records.fingerprint(d)
    audit.append(f, fs, source="角度B")
    assert records.verify(d, before) == [], \
        "⛔ 第二个角度报同一条发现，记录守卫就误判——一批里后续的单会全部连坐"
