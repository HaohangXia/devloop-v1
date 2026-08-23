"""⛔ 装到项目上的 `gates.sh` 与模板脱节时，必须有人报出来（G-114）。

## 这条是怎么来的

2026-08-04 复核发现：eco-ob 的 `.devloop/gates.sh` 里

- ⛔ 连 `void()` 这个函数**都没有**——整个 VOID 判档从来没装进去过；
- ⛔ 「禁改清单」那一支报的是 **PASS**，而它冠名要验的东西
  （`.devloop/`）在该项目里**根本没被 git 跟踪**，它验了 **0 条**；
- ⚠️ 而 `f-feeding.toml` 的 `require_pass` **点名了这道闸**。

⭐ 这个洞 2026-08-01 就修好了（G-88）——**修在模板里**。
⛔ 装到项目上的那一份**从来没跟着更新**，同一个洞多活了三天，
而且**没有任何东西会告诉你**。

## ⛔ 判据不能是「必须和模板一模一样」

⚠️ 项目本来就该改 gates.sh（那是它自己的验收命令）。
⭐ 判据落在**版本代号**上：模板带一个 `DEVLOOP-GATES-REV`，
装出去的那份记下它是从哪一版生成的，`doctor` 比对两者。

## ⭐ 「不知道」不许当成「没问题」

装出去的老副本没有这个代号——⛔ 那时正确的回答是
「**不知道它基于哪一版**」，而不是沉默。⚠️ eco-ob 那一份正是这种情况，
而沉默让它多活了三天。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devloop import doctor

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "gates.sh"
REV_RE = re.compile(r"DEVLOOP-GATES-REV:\s*(\d+)")


def test_模板自己带着版本代号() -> None:
    """⛔ 模板没有代号，比对就无从谈起。"""
    m = REV_RE.search(TEMPLATE.read_text(encoding="utf-8"))
    assert m, ("⛔ `templates/gates.sh` 里没有 `DEVLOOP-GATES-REV: <数字>`"
               "——⚠️ 改模板时必须把它加一")


def _proj(tmp_path, body: str) -> Path:
    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text(body, encoding="utf-8")
    return p


def test_代号对得上就不吭声(tmp_path) -> None:
    """⛔ 每次都报一句 = 那句话没人看。"""
    rev = REV_RE.search(TEMPLATE.read_text(encoding="utf-8")).group(1)
    c = doctor.gates_template_drift(_proj(tmp_path, f"# DEVLOOP-GATES-REV: {rev}\n"))
    assert c.ok is True, c.detail


def test_代号落后要报出来并说清差了几版(tmp_path) -> None:
    c = doctor.gates_template_drift(_proj(tmp_path, "# DEVLOOP-GATES-REV: 1\n"))
    assert c.ok is False
    assert "1" in c.detail, c.detail
    assert "模板" in c.detail


def test_项目比模板新时必须报出来并说清领先几版(tmp_path) -> None:
    """⛔⛔ 另一半输入 —— **以前一次都没喂过**。2026-08-11 补。

    ⚠️ 旧代码是 `if a >= b: return Check(True, ..., "与模板一致")`，
    实测对 eco-ob（rev 7 vs 模板 rev 4）打的就是 `ok=True`、
    还印了一句「**rev 7，与模板一致**」—— ⛔ **那句话是假的**。

    ⭐ 而它假的那个方向恰好是这个工具最要命的失效：
    **在靶子项目上改进了闸，却没搬回模板**。
    实测旁证：`grep -c PREFLIGHT` 模板 **0** / eco-ob **7**。

    ⛔ 这是本项目反复出现的那个形状的第 12 次：
    **同一件事有两条路。修好的永远是「有人盯着」的那条。**
    「靶子落后于模板」有人盯（G-114），「靶子领先于模板」没人盯。
    """
    rev = int(REV_RE.search(TEMPLATE.read_text(encoding="utf-8")).group(1))
    c = doctor.gates_template_drift(_proj(tmp_path, f"# DEVLOOP-GATES-REV: {rev + 3}\n"))
    assert c.ok is False, f"⛔ 领先 3 版居然判绿：{c.detail}"
    assert "领先 3 版" in c.detail, c.detail
    #  ⭐ 还要说清楚“该往哪儿搬”，⛔ 否则看到红也不知道干啥
    assert "templates/gates.sh" in c.detail, c.detail


def test_真devloop仓上这条必须是绿的() -> None:
    """⭐ 绿检（⛔ 缺了就是只做过红检的判据）。

    工具仓自己的 `.devloop/gates.sh` 与模板同版 → 必须仍然是绿的。
    ⛔ 防的是「把它改成永远报警」—— 恒红的闸两周内必被关掉。
    """
    root = Path(__file__).resolve().parents[1]
    a, b = doctor.gates_rev_pair(root)
    assert a is not None and b is not None, f"⛔ 读不出版号：{a} / {b}"
    assert a == b, f"⚠️ 工具仓自己就脱节了：自己 rev {a}、模板 rev {b}"
    assert doctor.gates_template_drift(root).ok is True


def test_压根没有代号时说不知道而不是说没问题(tmp_path) -> None:
    """⭐ eco-ob 那一份就是这种。⛔ 「不知道」不许当成「没问题」。

    ⚠️ 判据是 `ok is not True`——⭐ `None`（判不了）和 `False`（确实落后）
    都可以，⛔ 但绝不许是 `True`。
    """
    c = doctor.gates_template_drift(_proj(tmp_path, "#!/usr/bin/env bash\necho hi\n"))
    assert c.ok is not True, f"⛔ 没有代号却说没问题：{c.detail}"
    assert "不知道" in c.detail or "判不了" in c.detail or "没有" in c.detail


def test_项目没有gates时不报警(tmp_path) -> None:
    """⚠️ 还没接闸的项目不该被这条骚扰。"""
    p = tmp_path / "empty"
    (p / ".devloop").mkdir(parents=True)
    c = doctor.gates_template_drift(p)
    assert c.ok is None


# ══════════════════════════════════════════════════════════════════════
#  ⭐ 后果判据：doctor 真的会跑它
# ══════════════════════════════════════════════════════════════════════

def test_doctor真的把这条算进去了(tmp_path) -> None:
    """⛔ 判据写好了没接上 = 等于没修——今天已经踩过两次。"""
    p = _proj(tmp_path, "# DEVLOOP-GATES-REV: 1\n")
    names = [c.name for c in doctor.run(p)]
    assert any("闸模板" in n for n in names), \
        f"⛔ `doctor` 没跑这条检查，装出去的副本照样悄悄过期。实得：{names}"


# ══════════════════════════════════════════════════════════════════════
#  ⭐ 真现场：eco-ob 那一份现在是什么状态
# ══════════════════════════════════════════════════════════════════════

def test_真项目上这条检查跑得起来() -> None:
    """⚠️ 只断言「跑得起来且给了结论」，⛔ 不断言结论是什么

    ——那取决于 eco-ob 当下有没有跟上，而那是**用户的**决定，不该由测试逼着。
    """
    eco = Path("C:/pg/eco-ob")
    if not (eco / ".devloop" / "gates.sh").exists():
        pytest.skip("本机没有 eco-ob")
    c = doctor.gates_template_drift(eco)
    assert c.name and c.detail, "检查跑了但什么都没说"
