"""报告格式解析器（G-33）。

**为什么这个文件很薄**：真正的证据在 `_review/verify_blockfmt.py`——它拿 23 份
**真实历史报告**做 JSON→块→JSON 往返，21 项检查。那种测试依赖真实语料，
放进 tests/ 会把仓库和测试耦死。这里做两件事：

1. 把那个校验脚本当子进程跑一遍，退出码非 0 即失败（**证据不许孤悬在主套件之外**）
2. 锁住几条**最容易在重构中被弄丢的语义**——尤其「失败时 obj 必须是 None」
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from devloop.blockfmt import BEGIN, load_report, parse_block, to_block

ROOT = Path(__file__).resolve().parent.parent

# finding 的 7 个必需字段。缺一半以上会被判死（见 TestG33不许静默降级），
# 所以每个用例都得给全——否则测的是「缺字段」而不是它本来要测的东西。
_F = {"doc_anchor": "a", "doc_quote": "q", "verify": "v",
      "expect": "e", "evidence": "ev", "why": "w", "confidence": "high"}


def block(**override: str) -> str:
    """造一个字段齐全的单条 finding 块，只覆盖要测的那个字段。"""
    f = {**_F, **override}
    body = "".join(f"{k}: {v}\n" for k, v in f.items())
    return f"{BEGIN}\n@@ITEM meta\nunit: u\n@@ITEM finding\n{body}@@END"


def test_历史报告往返无损_跑真实语料():
    """23 份真实报告的往返校验。它是这次格式变更唯一的硬证据。"""
    script = ROOT / "_review" / "verify_blockfmt.py"
    if not script.exists():
        pytest.skip("校验脚本不在（历史语料未随仓库分发时会这样）")
    p = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    assert p.returncode == 0, f"往返校验失败：\n{p.stdout[-2000:]}"


class TestG33两颗历史雷:
    """这两个输入正是把旧 JSON 打死过的。它们必须永远能过。"""

    def test_grep交替语法原样保留(self):
        v = "grep '双层\\|double.layer' game/src/"
        j = parse_block(block(verify=v)).obj
        assert j is not None and j["findings"][0]["verify"] == v

    def test_中文里夹ASCII双引号原样保留(self):
        a = 'CLAUDE.md §3 · 未接入段与"卡在哪（现行）"段'
        j = parse_block(block(doc_anchor=a)).obj
        assert j is not None and j["findings"][0]["doc_anchor"] == a

    def test_值里的冒号不劈开值(self):
        q = "sim_world.gd:227-232（A4 corpse recycling）"
        j = parse_block(block(doc_quote=q)).obj
        assert j is not None and j["findings"][0]["doc_quote"] == q


class TestG33结构保证:
    """核心保证：值与分隔符处在互斥的列位置——值生不出结构行。

    这不是「分隔符够罕见所以撞不上」，是「值永远出现在行首之后」。
    """

    def test_值里出现分隔线本身也不会劈开块(self):
        blk = (f"{BEGIN}\n@@ITEM meta\nunit: u\n@@ITEM finding\n"
               "doc_anchor: a\n"
               "doc_quote: 格式定义如下\n+ @@END\n+ @@ITEM finding\n"
               "+ doc_anchor: 这行长得像字段\n"
               "verify: v\nexpect: e\nevidence: ev\nwhy: w\nconfidence: high\n@@END")
        j = parse_block(blk).obj
        assert j is not None
        assert len(j["findings"]) == 1, "值里的 @@END 伪造出了块边界"
        assert "@@END" in j["findings"][0]["doc_quote"]
        assert j["findings"][0]["verify"] == "v", "续行吞掉了后面的真字段"


class TestG33不许静默降级:
    """⛔ 解析失败就是失败。「尽力而为地猜出一部分」比报错危险得多。"""

    @pytest.mark.parametrize("bad,why", [
        (f"{BEGIN}\n@@ITEM meta\nunit: u\n", "块不完整（撞轮数上限的形态）"),
        ("正文里什么块都没有", "根本没有块"),
        (f"{BEGIN}\n@@ITEM meta\nunit: u\n忘了加号前缀的续行\n@@END", "孤儿行"),
        (f"{BEGIN}\n@@SECTION x\n@@END", "不认识的标记"),
        (f"{BEGIN}\n@@ITEM 矛盾\ndoc_anchor: x\n@@END", "不认识的条目类型"),
    ])
    def test_畸形输入必须报错且obj为None(self, bad, why):
        r = parse_block(bad)
        assert not r.ok, f"{why} 应判失败"
        assert r.obj is None, f"{why} 判失败了却还给出 obj —— 那正是「尽力而为」"

    def test_缺少数几个字段只告警不判死(self):
        """今天的 JSON 路径对缺字段就是宽容的（u08 有条 not_finding 缺 doc_quote
        仍进统计）。改成硬失败等于把本来能用的报告判死。"""
        f = {k: v for k, v in _F.items() if k != "confidence"}
        body = "".join(f"{k}: {v}\n" for k, v in f.items())
        r = parse_block(f"{BEGIN}\n@@ITEM meta\nunit: u\n@@ITEM finding\n{body}@@END")
        assert r.ok and r.obj is not None
        assert r.warnings, "缺字段应该出告警"

    def test_缺一半以上字段判死(self):
        """⚠️ 那不是笔误，是结构塌了——最常见成因是多行值漏写 `+ ` 前缀，
        后续行于是被当成新条目。**「解析出来了但内容是碎的」比解析失败更危险。**"""
        r = parse_block(f"{BEGIN}\n@@ITEM meta\nunit: u\n@@ITEM finding\n"
                        f"doc_anchor: x\ndoc_quote: y\n@@END")
        assert not r.ok and r.obj is None


def test_旧JSON格式仍能读():
    """⚠️ 换格式不许作废历史数据。23 份历史报告全是旧格式。"""
    r = load_report({"result": '```json\n{"unit": "u", "findings": []}\n```'})
    assert r.ok and r.obj["unit"] == "u"


def test_块格式与旧JSON往返等价():
    src = {"unit": "u", "base": "abc123",
           "findings": [dict(_F)],
           "undecidable": [], "not_findings": [],
           "naming_variants_tried": ["maxHp/max_hp"],
           "self_check": {"json_valid": True}}
    back = parse_block(to_block(src)).obj
    assert back is not None
    assert back["findings"] == src["findings"]
    assert back["naming_variants_tried"] == src["naming_variants_tried"]
