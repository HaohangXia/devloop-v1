"""派单的 prompt 拼装 + 台账。

这两处的共同点：**错了不会崩，只会悄悄产生错误的数据或多花钱**。
- prompt 前缀顺序错 → 缓存永不命中 → 成本静默翻倍（实测未分层时命中为 0）
- 台账记账时机错 → 「工人跑完了」被记成「这单合格了」（已实际发生过）
"""

from __future__ import annotations

import json

from devloop.dispatch import TOOL_PRESETS, DispatchResult, build_prompt
from devloop.models import Receipt, TaskSpec
from devloop import telemetry


def _spec(tmp_path, body="# 角色\nA\n# 任务\nB\n# 禁令\nC"):
    f = tmp_path / "task.md"
    f.write_text(body, encoding="utf-8")
    return TaskSpec.load(f)


class TestBuildPrompt:
    def test_规则摘要必须排在任务之前(self, tmp_path):
        """Prompt 缓存只对**完全一致的前缀**生效。

        规则摘要每单相同、任务书每单不同，所以稳定的必须在前。
        顺序反了不会报错，只会让缓存永不命中——实测未分层时命中为 0，
        分层后同一任务命中 33920 token。
        """
        p = build_prompt("RULES_MARKER", _spec(tmp_path, "# 角色\nA\n# 任务\nTASK_MARKER\n# 禁令\nC"))
        assert p.index("RULES_MARKER") < p.index("TASK_MARKER")

    def test_规则摘要完整进入_prompt(self, tmp_path):
        """--bare 让工人读不到项目 CLAUDE.md，摘要是它唯一的规则来源。丢了等于没护栏。"""
        rules = "## 铁律\n- 不许用系统随机\n- 不许改基线"
        p = build_prompt(rules, _spec(tmp_path))
        assert "不许用系统随机" in p and "不许改基线" in p

    def test_前缀不含易变内容(self, tmp_path):
        """时间戳/随机 ID/会话号进了前缀，缓存就作废——这是最容易无意犯的错。"""
        a = build_prompt("RULES", _spec(tmp_path))
        b = build_prompt("RULES", _spec(tmp_path))
        head = "以下是本项目的规则摘要"
        assert a[:a.index(head) + 200] == b[:b.index(head) + 200]


class TestToolPresets:
    def test_只读预设不得含写工具(self):
        """越权靠工具层拦，不依赖工人自觉——这是「不给绕过路径」的一部分。"""
        ro = TOOL_PRESETS["readonly"]
        for w in ("Write", "Edit"):
            assert w not in ro


class TestTelemetry:
    def _res(self, *, err=None, cost=0.1):
        return DispatchResult("t", Receipt(total_cost_usd=cost, duration_ms=1000,
                                           num_turns=3), None, error=err)

    def test_工人成败与闸成败必须分开记(self, tmp_path):
        """已实际发生过的事故：闸拦下了，但台账记成成功，统计显示 100%（实际 50%）。

        「工人跑完了」≠「这单合格」，而「这单不合格」≠「模型不行」——
        三者混成一个 ok 字段，就永远回答不了「便宜模型够不够用」。
        """
        f = tmp_path / "t.jsonl"
        telemetry.record(f, self._res(), model="m", tools="implement",
                         gate_ok=False, gate_detail="回归未过")
        row = json.loads(f.read_text(encoding="utf-8").strip())
        assert row["worker_ok"] is True, "工人本身确实跑通了"
        assert row["gate_ok"] is False
        assert row["ok"] is False, "闸没过 → 整单不合格"

    def test_只读任务无闸时不应被判失败(self, tmp_path):
        f = tmp_path / "t.jsonl"
        telemetry.record(f, self._res(), model="m", tools="readonly", gate_ok=None)
        row = json.loads(f.read_text(encoding="utf-8").strip())
        assert row["gate_ok"] is None and row["ok"] is True

    def test_归因字段必须存在且默认待标注(self, tmp_path):
        """失败可能来自四个方向：模型能力/任务书判据/工具环境/闸误报。
        不留归因位，成功率这个数就没有解释力。"""
        f = tmp_path / "t.jsonl"
        telemetry.record(f, self._res(err="boom"), model="m", tools="readonly")
        assert "failure_class" in json.loads(f.read_text(encoding="utf-8").strip())

    def test_汇总区分工人与闸(self, tmp_path):
        f = tmp_path / "t.jsonl"
        telemetry.record(f, self._res(), model="m", tools="implement", gate_ok=True)
        telemetry.record(f, self._res(), model="m", tools="implement", gate_ok=False)
        out = telemetry.summarize(telemetry.load(f))
        assert "工人自身跑通 2/2" in out and "过闸 1/2" in out

    def test_空台账不崩(self, tmp_path):
        assert "还没派过单" in telemetry.summarize(telemetry.load(tmp_path / "nope.jsonl"))


# ── 回执必须说清「为什么失败」（G-32）────────────────────────
# 2026-07-26 实测：u11 撞满 30 轮上限被截断，台账正确记了 worker_ok=False，
# 但 error/failure_class 全是 None——说了失败，没说为什么。
# 「轮数不够」是拆单太大（编排方的错），「模型不行」是能力问题，
# 成本实验里混为一谈就会把自己的失误算到便宜模型头上。

def test_回执撞轮数上限时说得出原因():
    from devloop.models import Receipt
    r = Receipt(is_error=True, subtype="error_max_turns",
                terminal_reason="max_turns",
                errors=["Reached maximum number of turns (30)"])
    assert r.why_failed == "error_max_turns"


def test_回执没失败时不给原因():
    from devloop.models import Receipt
    assert Receipt(is_error=False, subtype="error_max_turns").why_failed == ""


def test_回执失败但上游没给字段时不装作知道():
    from devloop.models import Receipt
    assert Receipt(is_error=True).why_failed == "未知（上游没给原因）"


def test_台账把截断单独标出来(tmp_path):
    from devloop import telemetry
    from devloop.dispatch import DispatchResult
    from devloop.models import Receipt
    p = tmp_path / "t.jsonl"
    r = Receipt(is_error=True, subtype="error_max_turns", num_turns=31,
                usage={"input_tokens": 100, "output_tokens": 50})
    telemetry.record(p, DispatchResult("u11", r, None), model="m", tools="readonly")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["truncated"] is True, "撞轮数上限必须单独可筛——它是拆单错误不是模型错误"
    assert row["error"] == "error_max_turns", "台账必须说得出为什么失败"
    assert row["input_tokens"] == 100 and row["output_tokens"] == 50, \
        "算真实成本要原始 token（回执的 total_cost_usd 是 Opus 合成价，G-28）"


def test_台账不把正常单标成截断(tmp_path):
    from devloop import telemetry
    from devloop.dispatch import DispatchResult
    from devloop.models import Receipt
    p = tmp_path / "t.jsonl"
    telemetry.record(p, DispatchResult("u01", Receipt(is_error=False), None),
                     model="m", tools="readonly")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["truncated"] is False and row["error"] is None


# ── 真实成本换算接入台账（G-28 的「实现了≠接上了」那一半）──────
# prices.json 建好后有一段时间在 devloop 包里零引用，台账仍直写合成价。
# 这正是本项目反复栽的那个坑：代码存在、看起来完备，真实路径上没走。

def test_台账并存合成价与真实价(tmp_path):
    from devloop import telemetry
    from devloop.dispatch import DispatchResult
    from devloop.models import Receipt
    p = tmp_path / "t.jsonl"
    r = Receipt(total_cost_usd=1.0166,
                usage={"input_tokens": 46174, "output_tokens": 21152,
                       "cache_read_input_tokens": 1255168})
    telemetry.record(p, DispatchResult("u", r, None),
                     model="deepseek-v4-pro[1m]", tools="readonly")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["cost_usd_synthetic"] == 1.0166
    assert row["cost_usd_real"] is not None
    assert row["cost_usd_real"] < row["cost_usd_synthetic"] / 5, \
        "真实价应比 Opus 合成价低一个量级；差不多说明换算没生效"
    assert "deepseek" in row["price_source"].lower() or "http" in row["price_source"]


def test_模型不在价目表时真实价为空而非猜一个(tmp_path):
    from devloop import telemetry
    from devloop.dispatch import DispatchResult
    from devloop.models import Receipt
    p = tmp_path / "t.jsonl"
    telemetry.record(p, DispatchResult("u", Receipt(total_cost_usd=9.9,
                     usage={"input_tokens": 100}), None),
                     model="某个没见过的模型", tools="readonly")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["cost_usd_real"] is None, "不知道就是不知道，静默回退到默认价是本项目的老毛病"
    assert "未知" in row["price_source"]


def test_带方括号后缀的模型名能查到价(tmp_path):
    from devloop.pricing import real_cost_usd
    a = real_cost_usd("deepseek-v4-pro[1m]", {"input_tokens": 1_000_000})
    b = real_cost_usd("deepseek-v4-pro", {"input_tokens": 1_000_000})
    assert a == b == 0.435, "配置用的是带 [1m] 后缀的名字，查不到价整条链就断了"


def test_摘要不把合成价与真实价相加(tmp_path):
    """混着算会得出一个既不是合成价也不是真实价的数——比报错更糟，它看起来正常。"""
    from devloop import telemetry
    p = tmp_path / "t.jsonl"
    rows = [
        {"ts": "2026-07-26T10:00:00", "ok": True, "worker_ok": True, "gate_ok": None,
         "cost_usd": 1.0, "duration_s": 1, "cache_read_tokens": 0},          # 旧格式：只有合成价
        {"ts": "2026-07-26T10:01:00", "ok": True, "worker_ok": True, "gate_ok": None,
         "cost_usd_synthetic": 1.0, "cost_usd_real": 0.04,
         "duration_s": 1, "cache_read_tokens": 0},                            # 新格式
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = telemetry.summarize(telemetry.load(p))
    assert "1/2" in out or "仅 1" in out, "必须说清有多少单算不出真实价"
    assert "不可相加" in out


def test_全是旧格式时明说给不出真实成本(tmp_path):
    from devloop import telemetry
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"ts": "2026-07-26T10:00:00", "ok": True, "worker_ok": True,
                             "gate_ok": None, "cost_usd": 2.5, "duration_s": 1,
                             "cache_read_tokens": 0}), encoding="utf-8")
    out = telemetry.summarize(telemetry.load(p))
    assert "无法给出真实成本" in out and "G-28" in out


# ── 成本口径：算不出就是 None，不是 0（G-43）──────────────────
# 「模型在表里但 token 全 0」乘出来是 0.0——看起来像正常数字，会被求和进总账、
# 被当成「这单免费」。而真相是上游没报 usage（子代理交接就常这样）。

def test_token全为0时成本是未知而不是零():
    from devloop.pricing import real_cost_usd
    assert real_cost_usd("deepseek-v4-pro", {"input_tokens": 0, "output_tokens": 0}) is None
    assert real_cost_usd("deepseek-v4-pro", {}) is None


def test_有token时正常算出成本():
    from devloop.pricing import real_cost_usd
    c = real_cost_usd("deepseek-v4-pro", {"input_tokens": 1_000_000})
    assert c == 0.435


def test_模型不在表里也是未知():
    from devloop.pricing import real_cost_usd
    assert real_cost_usd("没见过的模型", {"input_tokens": 999}) is None


def test_来源必须逐模型取_不能张冠李戴():
    """顶层 _source 是 DeepSeek 的定价页；Claude 后端不能盖这个戳。"""
    from devloop.pricing import price_source
    assert "deepseek" in price_source("deepseek-v4-pro").lower()
    assert "deepseek" not in price_source("claude-opus").lower(), \
        "Claude 系后端盖上 DeepSeek 定价页的来源戳 = 看起来有出处、实际张冠李戴"


def test_缓存写入必须计入成本():
    """C 臂实测 cache_write 159 万 token；漏掉它会系统性低估 Anthropic 系后端。"""
    from devloop.pricing import real_cost_usd
    assert real_cost_usd("claude-opus", {"cache_creation_input_tokens": 1_000_000}) == 6.25


# ── 轮数天花板（G-46）────────────────────────────────────────
# 第二批 7 单里 3 单撞 30 轮上限白跑。我先做了个「按文件大小估轮数」的预测器，
# 用 7 个真实数据点一标定就发现**根本不成立**：每轮读的行数从 3 到 45，差 15 倍，
# 因为真正的驱动是「有多少条断言要逐个核」，而那个数不读完就数不出来。
#
# ⭐ 正解简单得多：**没用掉的轮数不花钱**（按 token 计费）。天花板给高不给低。
#    给低了撞顶那单 report 长度为 0——**整单白跑，钱照花**。

def test_默认轮数足够跑完实测过的最大单():
    """第二批 u12（BUILD_LOG 1891 行）实测用到 95 个 num_turns，默认值必须高于它。

    直接查源码里的常量，不走 argparse —— 这个默认值是会被人无意改小的那类东西，
    测试要盯的就是它本身。
    """
    import re
    from pathlib import Path as _P

    import devloop.cli as c
    src = _P(c.__file__).read_text(encoding="utf-8")
    m = re.search(r'"--max-turns",\s*type=int,\s*default=(\d+)', src)
    assert m, "找不到 --max-turns 的默认值"
    assert int(m.group(1)) >= 100, (
        f"默认轮数 {m.group(1)} 太低。实测最大单用到 95 轮；给低了撞顶的单 "
        f"report 长度为 0、整单白跑，而没用掉的轮数本来不花钱")


# ── 评测集（Phase 5）─────────────────────────────────────────
# 它要回答的是「换后端 / 改模板之后，判断力掉没掉」。
# ⚠️ 这套东西自身的正确性比它的分数更要紧——分数错了会误导所有后续决策。

class TestEvalHarness:
    def test_样本分布必须三类齐全(self):
        """只测 broken 的话，一个「一律答 broken」的模型能拿满分。"""
        from collections import Counter
        from devloop.evals import KINDS, load
        c = Counter(x.kind for x in load())
        assert set(c) == set(KINDS), f"缺了类别：{set(KINDS) - set(c)}"
        assert all(v >= 3 for v in c.values()), f"某类样本太少，抽不出信号：{dict(c)}"

    def test_题面不许泄露答案(self):
        """`why` 是判据、`evidence` 是答案位置、`traps` 是难点标注——都不能进题面。"""
        for c in __import__("devloop.evals", fromlist=["load"]).load():
            p = c.prompt()
            assert c.why not in p, f"{c.id}: 判据泄露进题面了"
            assert c.kind not in ("broken",) or "broken" not in c.claim.lower(), \
                f"{c.id}: 题干里出现了答案词"
            for t in c.traps:
                assert t not in p, f"{c.id}: 坑点标注泄露进题面了"

    def test_每条样本都要说清谁怎么核的(self):
        """答案的可信度全靠这一栏。没有出处的「已知答案」等于没有。"""
        for c in __import__("devloop.evals", fromlist=["load"]).load():
            assert c.verified_by and c.evidence, f"{c.id} 缺 verified_by / evidence"
            assert c.why, f"{c.id} 缺判据"

    def test_判定只认末尾的VERDICT行(self):
        """⛔ 不做模糊匹配——「我一开始以为 broken，后来发现 holds」不能判成 broken。"""
        from devloop.evals.runner import _extract
        assert _extract("我一开始以为 broken，后来发现\nVERDICT: holds") == "holds"
        assert _extract("VERDICT: broken\n中间改了主意\nVERDICT: undecidable") == "undecidable"
        assert _extract("正文里提到 broken 这个词但没给判定") == ""

    def test_答错的性质要分开_保守错和说反了不是一回事(self):
        from devloop.evals import Case
        from devloop.evals.runner import Answer
        mk = lambda kind: Case(id="x", kind=kind, doc="d", anchor="a", claim="c",
                               why="w", evidence="e", verified_by="v", traps=[])
        assert "保守" in Answer(mk("broken"), "undecidable", False, 0, 0, 0).severity
        assert "硬判" in Answer(mk("undecidable"), "holds", False, 0, 0, 0).severity
        assert "说反" in Answer(mk("broken"), "holds", False, 0, 0, 0).severity
        assert Answer(mk("holds"), "holds", True, 0, 0, 0).severity == ""

    def test_基准漂了必须拒跑(self, monkeypatch, tmp_path):
        """⛔ 基准一漂，分数就不再是「判断力」的度量——**而它看起来仍像个正常分数**。"""
        from devloop import evals
        monkeypatch.setattr(evals, "BASE_REPO", tmp_path / "不存在")
        assert evals.check_baseline(), "基准仓库不存在时必须报出来"
