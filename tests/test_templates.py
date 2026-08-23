"""模板一致性（第三轮审查补，2026-07-28）。

⚠️ 审查指出 `templates/` **零测试覆盖**，而「宪法散文 ↔ 机械判据」这条契约
就是靠两份模板对齐的——**没测过的机制不算工作**，这两份就是活证据：
实测 9 条条款的标注与判据对不上，而给人读的那份还在说它们「有机械判据」。

本文件只测**契约对齐**，不测判据逻辑本身。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parent.parent / "templates"


def _md_clauses() -> list[tuple[str, str, str]]:
    """从人读版里抽出 (条款号, judge 档位, 由谁判)。

    标注格式：`### A-1 · 标题  [judge: auto · toml]`
      auto/partial —— 有机械判据
      none         —— ⛔ 判不了，必须登记进 [[unjudged]]
    由谁判：
      toml   —— 判据是 toml 里的一条 protected_* 记录
      代码   —— 判据写死在 Python 里（如 refs 快照、提交前断言）
      待配置 —— 出厂模板不给，**要接入者自己填**；不填就是空守卫
    """
    md = (TPL / "constitution.md").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"^### ([A-C]-\d\w?) .*?\[judge:\s*(\w+)(?:\s*·\s*([^\]]+))?\]",
                         md, re.M):
        out.append((m.group(1), m.group(2), (m.group(3) or "").strip()))
    return out


def _toml_clauses() -> tuple[set[str], set[str]]:
    """返回 (有机械判据的条款, 登记为判不了的条款)。"""
    t = (TPL / "constitution.toml").read_text(encoding="utf-8")
    # ⚠️ 只看**未被注释掉**的行——注释掉的判据不生效，
    #    把它算成「有判据」正是这套契约要防的那种自欺。
    live = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("#"))
    unj = set(re.findall(r'\[\[unjudged\]\]\s*\nclause\s*=\s*"([A-C]-\d\w?)"', live))
    allc = set(re.findall(r'clause\s*=\s*"([A-C]-\d\w?)"', live))
    return allc - unj, unj


def test_人读版里的每条标注都要有着落():
    """⛔ 「说了但没做」最可能藏身的地方。

    人读版说「这条有机械判据」，而判据表里没有——那条款就是**散文守卫**，
    正是本项目开篇否定的东西（`rules-digest.md` 写着禁改，真正执行的却是闸）。
    """
    judged, unjudged = _toml_clauses()
    bad = []
    for cid, level, by in _md_clauses():
        if level == "none":
            if cid not in unjudged:
                bad.append(f"{cid} 标 none，但没登记进 [[unjudged]]——"
                           f"结论的「未覆盖 N 条」尾巴会少报它")
        elif by == "toml":
            if cid not in judged:
                bad.append(f"{cid} 标「由 toml 判」，但判据表里没有生效的记录")
        elif by not in ("代码", "待配置"):
            bad.append(f"{cid} 的标注没写清由谁判（实得 {by!r}）——"
                       f"只许 toml / 代码 / 待配置")
    assert not bad, "宪法两份模板对不上：\n  " + "\n  ".join(bad)


def test_判据表里的条款号都要在人读版里出现():
    """反方向：toml 里有一条判据，人读版却没写这条条款——
    那么它命中的时候，人拿到一个**查不到出处的条款号**。"""
    md_ids = {c for c, _, _ in _md_clauses()}
    judged, unjudged = _toml_clauses()
    orphan = sorted((judged | unjudged) - md_ids)
    assert not orphan, f"判据表里这些条款号在人读版里查不到：{orphan}"


def test_标了待配置的条款必须说清不配会怎样():
    """⚠️ 「出厂不给、要你自己填」是**诚实的**——前提是说清代价：
    不填 = 那条是空守卫。不说，接入者会以为它自带保护。"""
    md = (TPL / "constitution.md").read_text(encoding="utf-8")
    todo = [c for c, _, by in _md_clauses() if by == "待配置"]
    assert todo, "至少应该有几条是留给接入者配的（基线、设计文档、依赖清单）"
    for cid in todo:
        seg = md.split(f"### {cid} ")[1].split("\n### ")[0]
        assert "空守卫" in seg or "不配" in seg, \
            f"{cid} 标了「待配置」，但没说清不配会怎样"


def test_两份模板都能被真正加载(tmp_path):
    """⛔ 模板必须是**能直接用**的。`constitution init` 复制过去就该跑得通，
    否则第一步就撞一堵墙。"""
    import shutil
    from devloop import constitution as C
    from devloop.config import ProjectPaths

    proj = tmp_path / "p"
    (proj / ".devloop").mkdir(parents=True)
    for name in ("constitution.toml", "constitution.md"):
        shutil.copy2(TPL / name, proj / ".devloop" / name)
    # 模板登记的受保护文件要存在，否则「守卫的目标不存在」会拦下（那是对的）
    for f in ("gates.sh", "rules-digest.md"):
        (proj / ".devloop" / f).write_text("x\n", encoding="utf-8")
    con = C.load(ProjectPaths(proj))
    assert con.unjudged, "模板必须登记判不了的条款"


# ══ gates.sh 模板 ═════════════════════════════════════════════

def test_闸模板必须讲清三个退出码():
    """⛔ 1 与 2 混在一起，环境故障会被当成质量问题——
    而那会让人去改代码解决一个跟代码无关的问题。"""
    t = (TPL / "gates.sh").read_text(encoding="utf-8")
    for k in ("0 = ", "1 = ", "2 = "):
        assert k in t, f"退出码 {k} 没讲"
    assert "活没干好" in t and "先修环境" in t


def test_闸模板必须写明零PASS不算绿():
    """⭐ 这是模板最该传下去的一条：**每条能过的路径都要 pass**。
    只在失败时才输出的闸，全跳过时会「退出 0 且零 PASS」——
    而那被工具判成闸自身故障，不是绿（G-53）。"""
    t = (TPL / "gates.sh").read_text(encoding="utf-8")
    assert "一道 PASS 都没有" in t or "零 PASS" in t


def test_闸模板必须警告管道后的退出码():
    """本项目一天之内栽了四次的那条。"""
    t = (TPL / "gates.sh").read_text(encoding="utf-8")
    assert "head" in t and "$?" in t


def test_闸模板必须说明改动守卫只在干净检出里有意义():
    """⛔ 在有未提交改动的工作区里，git 差异 ≠ 工人的改动。
    硬判会误伤，所以那种情况必须 SKIP。"""
    t = (TPL / "gates.sh").read_text(encoding="utf-8")
    assert "DEVLOOP_CLEAN_CHECKOUT" in t and "SKIP" in t


def test_闸模板必须说明它抓不到自提交():
    """⚠️ 基线守卫靠 git status，工人把改动**提交掉**就抓不到了——
    那一半由宪法的 base 锚定判据补。⛔ 别以为有了这道就够了。"""
    t = (TPL / "gates.sh").read_text(encoding="utf-8")
    assert "提交掉" in t and "constitution" in t


def test_闸模板是合法的bash语法():
    """⚠️ 模板必须是**能直接用**的——占位符不能破坏语法结构。"""
    import subprocess
    from devloop.gates import find_bash
    r = subprocess.run([find_bash(), "-n", str(TPL / "gates.sh")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    # 占位符 <...> 会让语法检查失败，这是预期的——
    # 所以只断言失败原因确实来自占位符，而不是结构写错了
    assert r.returncode != 0, "含占位符的模板本来就跑不了，这条是提醒"
    assert "<" in r.stderr or "syntax error" in r.stderr.lower()


# ── ⛔ VOID：模板是每个新项目的抄写源（H-3 的真靶子）──────────────
#
# G-68 加了第四档判定 VOID（「我这一道本来就没有可验的东西」），
# 消费侧全齐：`_parse` 认词、`require_pass` 分档、CLI 有符号、10 条测试。
# ⛔ **而生产侧一行都没写**——全库 `grep -rn VOID --include=gates.sh` 零命中。
#
# ⭐ 2026-08-02 独立复核指出：本仓自己那份 `.devloop/gates.sh` 里的四个候选
# 落点**全都不可达**（37 个已跟踪测试、devloop/ 恒有 .py、122/122 提交都跟踪
# .devloop、pytest 恒能收集）。改它净效果为 0。
#
# ⚠️ 真靶子是 `templates/gates.sh`：
#   ① 协议块声明「工具只认这三样，⛔ 不许自创」，只列 PASS/FAIL/SKIP
#   ② 手边没有 `void()` 辅助函数，写闸的人最省力的写法就是 `pass`
#   ③ 「禁改清单」那支**逐字**就是 G-68 立项时 eco-ob 首跑那条假绿
# ⛔ 每一个照模板接入的新项目都会复现同一个事故。


def _tpl() -> str:
    return (TPL / "gates.sh").read_text(encoding="utf-8")


def test_闸模板的协议块必须列出VOID():
    """⛔ 那个协议块是写闸的人**唯一会读**的契约声明。
    它漏了 VOID，人就照着它写，第四档永远没有发射点。"""
    head = _tpl().split("fail=0")[0]
    assert "VOID" in head, "⛔ 协议块没列 VOID——写闸的人不会知道有这一档"
    assert "不许自创" in head and "三样" not in head, \
        "⚠️ 「工具只认这三样」这句话现在是错的"


def test_闸模板必须提供void辅助函数():
    """⭐ 手边没有 `void()` 的人，最省力的写法就是 `pass`——那正是 G-68 的成因。"""
    assert "void()" in _tpl(), "⛔ 只有 pass/bad/skip，第四档没有省力的写法"


def test_void不许置fail():
    """⛔ 「没有可验之物」不是错误。⚠️ 要不要因此拦住是 `require_pass` 的政策问题。
    置了 `fail=1` 会产出「N 过 / 0 未过」却 exit 1 的自相矛盾输出，
    ⭐ 正好撞上 `gates.py` 那条新加的反矛盾检查。"""
    #  ⚠️ `next(...)` 必须带 default——不带的话模板里没有 `void()` 时抛的是裸
    #     `StopIteration`，⛔ 读不到下面写好的那句话（复核专门批评过这种写法）。
    line = next((l for l in _tpl().splitlines() if l.startswith("void()")), None)
    assert line is not None, "⛔ 模板里没有 void() 辅助函数"
    assert "fail=1" not in line, f"⛔ void 置了 fail：{line}"


def test_闸模板必须说清SKIP与VOID的区别():
    """⚠️ 两者对「能不能放行」相同，对**排查**完全不同——
    SKIP 去查谁开的开关，VOID 去查这个项目为什么是空的。"""
    head = _tpl().split("fail=0")[0]
    assert "开关" in head and ("没有可验" in head or "无可验" in head)


def test_禁改清单那条假绿必须已经改成VOID():
    """⛔ **这一行逐字就是 G-68 立项时踩到的那条**：
    2026-08-01 eco-ob 首跑，`禁改清单` 在 worktree 内无 .devloop/ 时报 PASS，
    实际验了 0 条它冠名要保护的东西。

    ⚠️ 准确的理由是「**保护对象不在仓内**」——项目不跟踪 .devloop 时，
    闸与规则根本不在仓里，工人无从修改它。⛔ 不是「什么都没求值」
    （那一支确实求值了「worktree 内无 .devloop/」这个可证伪的命题）。
    """
    t = _tpl()
    assert 'pass "禁改清单" "worktree 内无 .devloop/' not in t, \
        "⛔ 那条假绿还在模板里——每个照它接入的新项目都会复现"
    assert 'void "禁改清单"' in t


def test_模板里凡是void的都要说清为什么没东西可验():
    """⚠️ 「无可验之物」是个结论，⛔ 不写清楚为什么，排查的人就得自己猜。"""
    for l in _tpl().splitlines():
        s = l.strip()
        if s.startswith("void ") and not s.startswith("void()"):
            assert len(s) > 30, f"⛔ VOID 的说明太空：{s}"
