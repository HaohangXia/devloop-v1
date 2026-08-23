"""⛔ 卷宗不许对人说「没点名的闸红了不影响结论」——**那是假的**。

## 这条是怎么来的（2026-08-15）

`dossier.py` 里原话：

```
- 闸报了但没被点名：X（⚠️ 它们红了不影响验收结论）
```

⭐ 从 `gates.py::run_gates` 从头推一遍，这句站不住：

1. `require_pass` 那一段满足之后，最后一行是 `return GateResult(code, ...)`
   —— **原样透传脚本的退出码**；
2. 模板的 `bad()` 置 `fail=1`，脚本 `exit $fail` ⇒ 任何一道 FAIL 都让 `code=1`；
3. 就算脚本印了 FAIL 却 `exit 0`，上面那段「自相矛盾」判 **2**。

⇒ 两条路都不放行。**`require_pass` 的语义是「至少这几道要过」，
⛔ 不是「只看这几道」。** 只有 `skip()` / `void()` 不置 `fail=1`，
⇒ 没点名的 **SKIP/VOID** 才是真的不影响。

## ⛔ 为什么这条比它看起来重

卷宗的全部意义是**让人放行前不必重读 172KB 事件流**。
一句「这几道你不用看」正好把人从**唯一一道真的拦下了这一单**的闸前面支开——
⚠️ **一份写得漂亮的卷宗让人跳过复核，比没有卷宗更坏**（这是卷宗自己的开篇纪律）。

## ⭐ 反复出现的那个形状

> **同一件事有两条路。修好的永远是「有人盯着」的那条。**

这里两条路是：**闸怎么判**（`gates.py`，有测试盯着）与
**卷宗怎么对人说**（`dossier.py`，没人盯）。⛔ 所以下面第四条测试
**把两条路绑在同一个断言里**：哪天 `run_gates` 真改成「只看点名的」，
那一条会红，逼着同一次改动把卷宗的说法一起改掉。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from devloop import dossier
from devloop.config import ProjectPaths
from devloop.gates import run_gates
from devloop.models import TaskSpec

_ROW = {
    "ts": "2026-08-03T01:00:00", "task": "u1", "model": "claude-opus-4-7",
    "tools": "implement", "worker_ok": True, "gate_ok": False, "ok": False,
    "gate_code": 1, "gate_detail": "1 过 / 1 未过", "error": None,
}

_SPEC = "# 角色\n\nx\n\n# 任务\n\ny\n\n# 改动范围\n\n- devloop/a.py\n\n# 禁令\n\n- z\n"


def _proj(tmp_path: Path) -> ProjectPaths:
    p = tmp_path / "p"
    (p / ".devloop" / "tasks").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (p / ".devloop" / "tasks" / "u1.md").write_text(_SPEC, encoding="utf-8")
    return ProjectPaths(p)


def _spec(paths: ProjectPaths):
    return TaskSpec.load(paths.project / ".devloop" / "tasks" / "u1.md")


def _md(tmp_path: Path, lines: list[tuple[str, str]] | None) -> str:
    paths = _proj(tmp_path)
    return dossier.build(
        paths, _spec(paths), _ROW,
        gate_names=["点名的闸", "没点名的闸"],
        required=["点名的闸"],
        gate_lines=lines,
        changed=["devloop/a.py"], base="abc1234", sha="def5678")


def _about(md: str, name: str) -> str:
    """只取**说到这道闸**的那几行。⛔ 全文匹配会把说明文字算进来，那是假判据。"""
    return "\n".join(ln for ln in md.splitlines()
                     if name in ln and ln.startswith("-"))


def test_没点名的闸红了必须说照样不通过(tmp_path: Path) -> None:
    """⛔ 这是那句假话的最小复现。"""
    md = _md(tmp_path, [("点名的闸", "PASS"), ("没点名的闸", "FAIL")])
    assert "不影响验收结论" not in md, (
        "⛔ 卷宗又在叫人别看一道真的会拦下这一单的闸")
    assert "照样判这一单不通过" in _about(md, "没点名的闸")


def test_没点名但验过通过的闸不许被说成没验(tmp_path: Path) -> None:
    """⛔⛔ 2026-08-16 加。第一版只分两拨（红 / SKIP·VOID），
    于是**验过而且通过**的落进后一拨，被印成「都是 SKIP/VOID——没验」。

    ⚠️ 那是把上一句假话换个方向再说一遍：
    原来「没验的说成没事」，⛔ 改完变成「**验过没事的说成没验**」。
    ⭐ 而 `require_pass` 缺省是空的 ⇒ **每一道闸**都落进这一拨 ⇒ 几乎每一单都印。
    """
    md = _md(tmp_path, [("点名的闸", "PASS"), ("没点名的闸", "PASS")])
    said = _about(md, "没点名的闸")
    assert "验过而且通过" in said, f"⛔ 一道验过并通过的闸被说成别的：{said}"
    #  ⚠️ 认**那句具体的话**，⛔ 不认「没验」两个字——
    #     正确的措辞里就有「没点名不等于没验」，全字匹配是个假红判据。
    assert "SKIP/VOID＝**没验**" not in said
    assert "照样判这一单不通过" not in said


def test_没点名的SKIP才是真的没验(tmp_path: Path) -> None:
    """⭐ 绿检。⛔ 缺了它，上面两条可以靠「一律喊红」蒙混过关——
    那就成了一根坏掉的火警（只做过红检的守卫）。"""
    md = _md(tmp_path, [("点名的闸", "PASS"), ("没点名的闸", "SKIP")])
    said = _about(md, "没点名的闸")
    assert "照样判这一单不通过" not in said
    assert "SKIP/VOID＝**没验**" in said, (
        "⚠️ SKIP 的含义是「没验」，⛔ 不是「验过没事」")


def test_同一道闸打两行时前面那个红不许被吞掉(tmp_path: Path) -> None:
    """⛔⛔ 用字典存判定时，后一行会盖掉前一行。

    ⚠️ 「第一次没过 → 重跑一次 → 过了」是很自然的闸写法，`SPEC.md` 没有一条禁止。
    ⭐ 那时闸自己会 `exit 1`（`bad()` 置了 `fail=1`）把整单拦下，
    ⛔ 而卷宗若只认后一行，就会把它归进「你不用看」那一堆
    ——刚堵上的洞从侧门原样回来。
    """
    md = _md(tmp_path, [("点名的闸", "PASS"),
                        ("没点名的闸", "FAIL"), ("没点名的闸", "PASS")])
    assert "照样判这一单不通过" in _about(md, "没点名的闸")


def test_编排方没传判定时按最坏情况当红算并且说清是猜的(tmp_path: Path) -> None:
    """⛔ 「不知道」不许印成「没事」，⭐ 也不许印成「确定坏了」。"""
    md = _md(tmp_path, None)
    said = _about(md, "没点名的闸")
    assert "按最坏情况" in said
    assert "猜的" in said, (
        "⛔ 把「不知道」印成一句斩钉截铁的结论——本项目为这个区分栽过多次")


def test_闸的实际行为必须和卷宗的说法对得上(tmp_path: Path) -> None:
    """⭐⭐ 把两条路绑在一起的那一条。

    ⚠️ 上面三条只验「卷宗怎么说」。⛔ 说得再好，只要 `run_gates` 哪天
    真的改成「只看点名的」，那三条会**继续绿**，而卷宗从那一刻起开始撒反向的谎。
    ⇒ 这一条**真跑一次闸**，把事实钉进来。
    """
    d = Path(tempfile.mkdtemp())
    (d / ".devloop").mkdir()

    def _run(verdict_of_unnamed: str, code: str) -> int:
        (d / ".devloop" / "gates.sh").write_text(
            "printf 'PASS\\t点名的闸\\t好着呢\\n'\n"
            f"printf '{verdict_of_unnamed}\\t没点名的闸\\t随便\\n'\n"
            f"exit {code}\n", encoding="utf-8", newline="\n")
        return run_gates(ProjectPaths(d), require_pass=["点名的闸"]).code

    #  红检：唯一的变量就是那道**没点名**的闸
    red = _run("FAIL", "1")
    #  绿检：同一份闸，把它改成 PASS
    green = _run("PASS", "0")

    assert green == 0, f"⛔ 绿检没绿（code={green}）——下面的红检结论作废"
    assert red != 0, (
        "⛔ 没点名的闸红了，run_gates 却放行了。"
        "⇒ `require_pass` 的语义变了，"
        "而 `dossier.py` 里那句「照样判这一单不通过」现在是**反向的谎**——"
        "⭐ 请在同一次改动里把卷宗的措辞一起改掉。")
