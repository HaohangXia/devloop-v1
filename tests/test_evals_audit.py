"""评测集自身的红测（Phase 5 审查补，2026-07-27）。

⚠️ **评测集出错比模型出错更危险**：模型答错会被记下来，评测集错了会把
自己的错记在模型头上，而分数看起来仍然完全正常。

审查在这一路上报了 13 条，其中 4 条被证伪那轮推翻（凑数或举证错误）。
以下每条都是我自己复现过的。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devloop.evals import KINDS, Case, load
from devloop.evals import runner as R


# ══ _extract：格式没对上 ≠ 判断错了 ═════════════════════════════
# 实测 20 种真实写法，旧正则漏掉 10 种。**Markdown 加粗是模型最常见的行为**，
# 恰好是失败的那一种——漏掉就记 ok=False，把格式问题算进模型准确率。

@pytest.mark.parametrize("text", [
    "VERDICT: broken",                      # 裸行（旧的也过）
    "VERDICT:broken",
    "verdict: BROKEN",
    "VERDICT: **broken**",                  # 值加粗 ← 旧的漏
    "**VERDICT: broken**",                  # 整行加粗 ← 旧的漏
    "VERDICT: broken.",                     # 英文句号 ← 旧的漏
    "VERDICT: broken。",                     # 中文句号 ← 旧的漏
    "VERDICT: `broken`",                    # 反引号 ← 旧的漏
    "VERDICT：broken",                       # 全角冒号 ← 旧的漏
    "- VERDICT: broken",                    # 列表项 ← 旧的漏
    "## VERDICT: broken",                   # 标题 ← 旧的漏
    "> VERDICT: broken",                    # 引用 ← 旧的漏
    "VERDICT : broken",
    "结论如下\n\nVERDICT: broken\n",
])
def test_常见的判定写法都要认(text):
    assert R._extract(text) == "broken", f"没认出来：{text!r}"


@pytest.mark.parametrize("text", [
    "我一开始以为 broken，后来发现 holds。",
    "这句话是 broken 的吗？不好说。",
    "The claim is broken in spirit but VERDICT is not given here yet",
])
def test_不许做模糊匹配(text):
    """⛔ 正文里出现 broken 就算，会把「我一开始以为 broken，后来发现 holds」
    判成 broken。放宽的只能是**装饰**，不能是词的位置。"""
    assert R._extract(text) == ""


def test_取最后一个判定():
    assert R._extract("VERDICT: holds\n改主意了\nVERDICT: **broken**") == "broken"


# ══ severity：三分里有一格落在分类外 ════════════════════════════

def _ans(want, got, **kw):
    c = Case(id="x", kind=want, doc="d", anchor="a", claim="c", why="w",
             evidence="e", verified_by="v", traps=[])
    return R.Answer(case=c, got=got, ok=(got == want), cost=None, secs=0, turns=0, **kw)


@pytest.mark.parametrize("want,got,expect", [
    ("broken", "undecidable", "保守"),
    ("holds", "undecidable", "保守"),          # ← 旧实现打成「说反了」
    ("undecidable", "holds", "硬判"),
    ("undecidable", "broken", "硬判"),
    ("holds", "broken", "乱报"),               # ← holds 类存在的唯一理由，此前没有标签
    ("broken", "holds", "说反了"),
])
def test_答错的性质要分得开(want, got, expect):
    """`holds→broken`（乱报）与 `broken→holds`（说反了）方向相反，
    此前共用一个标签，打印出来分不开——而 holds 类测的就是「会不会乱报」。"""
    assert expect in _ans(want, got).severity


def test_答对了没有性质():
    assert _ans("broken", "broken").severity == ""


# ══ 派单失败 ≠ 答错 ════════════════════════════════════════════

def test_派单失败的题不计入准确率分母():
    """⛔ 超时、找不到 claude、撞满轮数被截断，一律被降级成「这题答错了」
    记在被测后端头上。`models.py` 自己写过这条教训：
    「轮数不够是我的拆单错误，不是模型能力问题」。"""
    r = R.Run(backend="b", model="m", started="t")
    r.answers = [_ans("broken", "broken"),
                 _ans("broken", "", failure="error_max_turns")]
    assert r.by_kind()["broken"] == (1, 1), "失败那题不该进分母"
    assert len(r.failures) == 1
    assert "没取到读数" in r.answers[1].severity


def test_没取到读数的题要在报告里单开一段():
    r = R.Run(backend="b", model="m", started="t")
    r.answers = [_ans("broken", "", failure="工人超时（3000s）")]
    out = R.format_run(r)
    assert "没取到读数" in out and "工人超时" in out


def test_小样本必须当场说破():
    """曾拿 undecidable 上的 2/3 → 1/3 说「质量掉了」，而 Fisher 精确检验
    p=1.0、Clopper-Pearson 区间几乎完全重叠——那句话根本不成立。
    免责声明此前只针对总分，**偏偏漏了最小的那一类**。"""
    r = R.Run(backend="b", model="m", started="t")
    r.answers = [_ans("undecidable", "undecidable") for _ in range(3)]
    assert "不产生可比结论" in R.format_run(r)


def test_混淆矩阵要印得出来():
    """判分口径第 2 条承诺「混淆矩阵全出，不合并成一个数」，
    而此前只印得出 by_kind 与 severity 两个投影。"""
    r = R.Run(backend="b", model="m", started="t")
    r.answers = [_ans("broken", "holds"), _ans("holds", "holds")]
    assert "混淆矩阵" in R.format_run(r)


# ══ compare：会在真有回归时印「没有回归」════════════════════════

def _row(ts, ids_ok, only=""):
    return {"ts": ts, "backend": "b", "model": "m", "only": only,
            "answers": [{"id": i, "want": "broken", "got": "broken" if ok else "holds",
                         "ok": ok, "severity": "", "turns": 1, "cost": None}
                        for i, ok in ids_ok]}


def test_上次是部分跑分时不得报没有回归(tmp_path):
    """⛔ 实测复现：历史里夹一次 `--only c01`，随后的全量跑分只跟那一题比，
    **19 题真回归被原样印成「逐题结果完全一致——没有回归」**。
    这套东西存在的唯一理由就是那句话，而它当时是错的。"""
    p = tmp_path / "runs.jsonl"
    rows = [_row("T1", [(f"c{i:02d}", True) for i in range(1, 21)]),
            _row("T2", [("c01", True)], only="c01"),
            _row("T3", [("c01", True)] + [(f"c{i:02d}", False) for i in range(2, 21)])]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")
    out = R.compare(p)
    assert "无从比对" in out, "两次题目集合不同，必须说出来"
    assert "完全一致" not in out or "重合" in out


def test_题目集合相同时照常报逐题变化(tmp_path):
    p = tmp_path / "runs.jsonl"
    rows = [_row("T1", [("c01", True), ("c02", True)]),
            _row("T2", [("c01", True), ("c02", False)])]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")
    out = R.compare(p)
    assert "回归" in out and "c02" in out
    assert "无从比对" not in out


def test_零题跑分不得写入历史(tmp_path):
    p = tmp_path / "runs.jsonl"
    R.save(R.Run(backend="b", model="m", started="t"), p)
    assert not p.exists(), "0 题的跑分只会成为污染源"


# ══ 题面泄露 ═══════════════════════════════════════════════════

def test_固定提示块不得含任何题目的标识符():
    """`Case.prompt` 的文档写着「不给 traps（那会把难点直接标出来）」，
    而固定提示块里曾把 c04 的 trap 原样写出——连标识符都一模一样
    （`maxHp` ↔ `max_hp`），等于把解法递到手上。

    ⚠️ 既有的 `test_题面不许泄露答案` 照过不误：它只查 why/traps 的字面串，
    抓不到「提示块与某道题的标识符撞车」这个洞。
    """
    import re
    # ⚠️ 只看**代码符号**（含下划线的 snake_case、或有大小写转折的 camelCase）。
    #    `holds`/`undecidable`/`godot` 这类普通词题面本来就该有，不算泄露；
    #    真正的洞是 `maxHp` ↔ `max_hp` 这种「一眼能对上某道题」的标识符。
    def symbols(txt):
        out = set()
        for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", txt):
            if "_" in w or re.search(r"[a-z][A-Z]", w):
                out.add(w)
        return out

    cases = load()
    fixed = cases[0].prompt().replace(cases[0].claim, "").replace(cases[0].anchor, "")
    idents = set()
    for c in cases:
        for txt in [c.claim, c.why, *c.traps]:
            idents |= symbols(txt)
    hit = sorted(w for w in idents if w in fixed)
    assert not hit, f"固定提示块里出现了题目里的代码符号：{hit}"


def test_anchor不得把文档自己的状态说出来():
    """`anchor` 是给工人的定位信息。写成「§五 · **已闭合**表 D1 行」
    等于顺带告诉他答案方向——两次跑分在那道题上都答了 anchor 暗示的方向。"""
    bad = [c.id for c in load() if "已闭合" in c.anchor or "未闭合" in c.anchor]
    assert not bad, f"这些题的 anchor 泄露了文档状态：{bad}"


# ══ --only 的入参校验 ═══════════════════════════════════════════

def test_only传未知题号必须拒跑():
    """静默降级会印「总计 0/0」、写进历史、返回 0；再叠加 compare 只比交集，
    就成了污染源。照 `check_baseline` 拒跑的写法。"""
    with pytest.raises(RuntimeError) as e:
        R.run(None, only="c99")
    assert "c99" in str(e.value)


# ══ 评测集数据本身 ═════════════════════════════════════════════

def test_每条题目的判据要能自洽():
    """⚠️ c18 的 why 曾写「读测试源码数不出通过数」——**这句是错的**。
    实地 `grep -rn EXPECTED_ASSERTIONS game/tests/` 一把拉出
    m0=31 / wyrm=45 / m1=38 / m2=23(=22+1)，四个数字与 claim 逐个对上。
    标签靠「失败数=0 需运行」还站得住，但判据站不住，直接导致工人的
    有据取证被打成最重的「⚠️ 硬判」。
    """
    for c in load():
        assert c.kind in KINDS
        assert c.why.strip(), f"{c.id} 没有判据"
        assert "数不出" not in c.why or "失败数" in c.why, (
            f"{c.id} 的判据说「数不出」，但若源码里有对应常量，这句就是错的——"
            f"要么改成诚实版本，要么把 claim 收窄")


def test_only的部分跑分不进主历史(tmp_path):
    """⛔ `--only` 是调试用的部分跑分。进了主历史就成污染源：
    下一次全量跑分与它比对时题目集合不同，实测过的后果是
    「19 题回归被印成『没有回归』」。compare() 现在会把差集说出来，
    但最稳的还是根本不让它进来。"""
    from devloop.evals import runner as RR

    hist = tmp_path / "runs.jsonl"
    r = RR.Run(backend="b", model="m", started="t")
    r.answers = [_ans("broken", "broken")]
    RR.save(r, tmp_path / "runs-debug.jsonl", only="c01")
    assert (tmp_path / "runs-debug.jsonl").exists()
    assert not hist.exists(), "部分跑分不该出现在主历史里"


def test_部分跑分的记录要留下only字段(tmp_path):
    """留着它，是为了以后能一眼看出「这一行是部分跑分」。"""
    import json
    from devloop.evals import runner as RR
    p = tmp_path / "runs.jsonl"
    r = RR.Run(backend="b", model="m", started="t")
    r.answers = [_ans("broken", "broken")]
    RR.save(r, p, only="c01")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["only"] == "c01" and row["n"] == 1


def test_作废的跑分不参与趋势比对(tmp_path):
    """⛔ 在已知配置错误或脏环境下跑出来的读数，不能当基准。
    实例：2026-07-28 的 flash 那次，三题因 `--max-turns 30` 撞顶被截断——
    那是配置错误，不是模型表现，拿它当基准会把后续的改善读成回归。"""
    p = tmp_path / "runs.jsonl"
    rows = [_row("T1", [("c01", True), ("c02", True)]),
            {**_row("T2", [("c01", False), ("c02", False)]), "invalidated": "配置错误"},
            _row("T3", [("c01", True), ("c02", False)])]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")
    out = R.compare(p)
    assert "跳过 1 次已作废" in out, "⛔ 静默跳过 = 读者以为历史就这么长"
    assert "T1" in out and "T3" in out and "T2" not in out


def test_可用跑分不足两次时也要说清跳过了几次(tmp_path):
    p = tmp_path / "runs.jsonl"
    rows = [{**_row("T1", [("c01", True)]), "invalidated": "脏环境"},
            _row("T2", [("c01", True)])]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")
    out = R.compare(p)
    assert "跳过 1 次" in out and "无从比对" in out
