"""summarize 里的「闸判定」一行——只认 gate_code，不看文字。

⛔ 本文件是新加的，⛔ 不改任何已有测试。测试只覆盖新加那一行的语义，
并顺手守住「其它行没被动过」这条底线。
"""

from __future__ import annotations

from devloop.telemetry import summarize


def _row(**overrides) -> dict:
    """做一条最小合法的台账行——只填 summarize 会读的字段。

    ⚠️ 不填 gate_code 也是有意的：外面要覆盖「历史行没有这个键」的情况。
    """
    base = {
        "ok": True,
        "worker_ok": True,
        "gate_ok": True,
        "cost_usd_real": 0.01,
        "cost_usd_synthetic": 0.02,
        "duration_s": 10.0,
        "cache_read_tokens": 1,
        "failure_class": None,
    }
    base.update(overrides)
    return base


def _gate_line(text: str) -> str:
    for ln in text.split("\n"):
        if ln.startswith("闸判定："):
            return ln
    raise AssertionError(f"没找到「闸判定：」这一行；输出为：\n{text}")


# ── 1. 四种 gate_code 都要数对 ────────────────────────────────────────
def test_四种_gate_code_各计数正确() -> None:
    rows = (
        [_row(gate_code=0) for _ in range(3)]
        + [_row(gate_code=1, ok=False, gate_ok=False) for _ in range(2)]
        + [_row(gate_code=2, ok=False, gate_ok=False) for _ in range(1)]
        + [_row(gate_code=None, gate_ok=None) for _ in range(4)]
    )
    line = _gate_line(summarize(rows))
    assert "全过 3" in line
    assert "有未过 2" in line
    assert "⚠️ 闸自身故障 1" in line
    assert "没跑闸 4" in line
    # ⛔ 这批里没有老行，不该出现「无判定记录」
    assert "无判定记录" not in line


# ── 2. 缺键的老行要落进「无判定记录」，不许当成 None ────────────────────
def test_缺_gate_code_键的老行不许被当成没跑闸() -> None:
    """⚠️ **期望值在合并复核时改过，理由要留下。**

    ⭐ 这条测试的**意图仍然对**：老行不许被无差别当成「没跑闸」。
    ⛔ 但它原来的期望值（老行全落「无判定记录」）在真台账上会印出
    「没跑闸 0」——而台账里 `gate_ok is None`（只读单）有 10 条。

    现在：老行**退回读 `gate_ok`**。本夹具三条老行的基线是 `gate_ok=True`，
    所以它们落「全过」；⭐ 而「不许被当成没跑闸」这个意图**照样成立**
    ——下面仍然断言「没跑闸」只有那 2 条真·没跑闸的。
    """
    # 一半是老行（无 gate_code 键，gate_ok=True），一半是新的 None（真·没跑闸）
    rows = [_row() for _ in range(3)] + [_row(gate_code=None, gate_ok=None) for _ in range(2)]
    line = _gate_line(summarize(rows))
    assert "没跑闸 2" in line, f"⛔ 老行被混进「没跑闸」了：{line}"
    assert "全过 3" in line, line
    assert "分不出" in line, "⛔ 老行推出来的档必须标明它分不出细分"


# ── 3. gate_detail 里含「闸自身故障」但 gate_code == 1 ──────────────────
#     这是本单的重点：⛔ 只认结构化字段 gate_code，不许拿文字猜。
def test_文字里含闸自身故障但_gate_code_是_1_必须计进有未过() -> None:
    rows = [
        _row(
            gate_code=1,
            ok=False,
            gate_ok=False,
            #  ⚠️ 真实形态：gates.sh 把失败的 pytest 节点名原样放进 FAIL 说明，
            #     而节点名可能含「闸自身故障」四个字（2026-08-02 实测发生过）
            gate_detail="FAIL tests/test_x.py::test_闸自身故障要归到闸自身故障",
        )
    ]
    line = _gate_line(summarize(rows))
    assert "有未过 1" in line
    assert "⚠️ 闸自身故障 0" in line


# ── 4. 全是老行时那一行仍然照常打印 ─────────────────────────────────
def test_全是老行时闸判定行仍然打印() -> None:
    """⚠️ **本条的期望值在合并复核时改过一次，理由要留下。**

    ⛔ 原来钉的是「五条老行 → 无判定记录 5、其余全 0」。
    那在真台账上会印出 **`没跑闸 0`**——而台账里 `gate_ok is None`
    （只读单，真的没跑闸）有 **10 条**，⚠️ 且它与同屏上一行
    `过闸 19/21` **当面矛盾**。⭐ 那条断言钉的是一个**错误行为**。

    现在：老行退回读 `gate_ok`。本夹具的 `_row()` 基线是 `gate_ok=True`，
    所以五条落进「全过」，并在行尾标明它们分不出细分档。
    """
    rows = [_row() for _ in range(5)]        # 五条老行，均缺 gate_code 键
    line = _gate_line(summarize(rows))
    assert "全过 5" in line, line
    assert "无判定记录" not in line, "⛔ 有 gate_ok 就不该落进「无判定记录」"
    assert "分不出" in line, "⛔ 老行推出来的档必须标明它分不出细分"


# ── 5. 现有输出没被改动 ─────────────────────────────────────────────
#     取一组具体 rows，把「闸判定：」那一行剔掉，剩余各行必须与旧版逐字相同。
def test_除新增行外其它输出逐字未变() -> None:
    rows = [
        _row(gate_code=0, cost_usd_real=0.01, cost_usd_synthetic=0.02,
             duration_s=60.0, cache_read_tokens=100),
        _row(gate_code=1, ok=False, gate_ok=False, worker_ok=True,
             cost_usd_real=0.02, cost_usd_synthetic=0.03,
             duration_s=30.0, cache_read_tokens=0),
    ]
    got = summarize(rows).split("\n")
    remaining = [ln for ln in got if not ln.startswith("闸判定：")]
    #  ⚠️ 这是把改动前手工跑一遍拿到的输出，逐字钉在这里。
    #     任何一个字的漂移都会让这条测试挂——那正是它的用途。
    expected = [
        "派单 2 次，整体合格 1（50%）",
        "  其中 工人自身跑通 2/2，过闸 1/2",
        "累计真实成本 $0.0300，单均 $0.0150（按价目表重算，非账单）",
        "累计耗时 1.5 分钟，单均 45 秒",
        "缓存命中 1/2 单",
        "失败归因：未分类 1 单",
        "  ⚠️ 归因决定「模型够不够用」的答案——taskspec/tooling 类失败不算模型的锅",
    ]
    assert remaining == expected
    # ⛔ 顺手确认「闸判定」那一行确实是被**新增**进去的
    assert any(ln.startswith("闸判定：") for ln in got)


# ── 6. ⛔ 合并复核补的四条 ────────────────────────────────────────
#
# ⚠️ 上面 5 条的夹具 `_row()` 基线是 `gate_ok=True`，
# ⛔ **一条都没覆盖真台账里 10/31 行的形状**（`gate_ok is None`，只读单）。
# 于是「没跑闸 0」这个假话在 5 条全绿的情况下活了下来。

def test_老行里没跑闸的要落进没跑闸而不是无判定记录() -> None:
    """⛔ 这是那句假话的正面判据。

    真台账实测：31 行里 10 行 `gate_ok is None`（只读单本来就不跑闸），
    而它们全部缺 `gate_code`（该字段 2026-08-02 才加）。
    ⚠️ 一股脑归到「无判定记录」→ 印「没跑闸 0」→ 与同屏
    `过闸 19/21` 矛盾。
    """
    line = _gate_line(summarize([_row(gate_ok=None) for _ in range(3)]))
    assert "没跑闸 3" in line, line
    assert "无判定记录" not in line, line


def test_连gate_ok都没有才算无判定记录() -> None:
    """⚠️ 退路也有尽头：两个字段都没有时才是真的「判不了这一单」。"""
    line = _gate_line(summarize([_row_without("gate_ok")]))
    assert "无判定记录 1" in line, line


def test_协议外的gate_code不许静默消失() -> None:
    """⛔ 实测：`gate_code=7` → 各档全 0，而实际有 1 单，**且没有任何提示**。
    ⚠️ 这正是本项目最怕的形态：一个**看起来正常的数字**。"""
    line = _gate_line(summarize([_row(gate_code=7)]))
    assert "判不了 1" in line, line


def test_字符串形态的gate_code不许让报警档归零() -> None:
    """⛔ **这条最狠**：台账是 JSON 文件，序列化漂一次就会出现 `"2"`。

    实测（改前）：`gate_code=2`(int) → 报警档 1；`gate_code="2"`(str) → 报警档 **0**。
    ⚠️ 一个在它该报警的输入上静默归零的报警器，**比不装更坏**。
    """
    for v in (2, "2", 2.0):
        line = _gate_line(summarize([_row(gate_code=v)]))
        assert "⚠️ 闸自身故障 1" in line, f"gate_code={v!r} → {line}"
    #  ⚠️ 布尔要挡住：Python 里 `True == 1` 成立，不挡会被当成「有未过」
    line = _gate_line(summarize([_row(gate_code=True)]))
    assert "判不了 1" in line, line


def test_各档相加恒等于总单数() -> None:
    """⛔ **这是唯一能抓住「有单元掉进没人统计的缝里」的判据。**

    ⚠️ 逐档断言永远抓不到它——那正是 `gate_code=7` 那个洞在
    5 条测试全绿的情况下活下来的原因。
    """
    import re

    rows = [_row(gate_code=0), _row(gate_code=1), _row(gate_code=2),
            _row(gate_code=None), _row(gate_code=7), _row(gate_code="2"),
            _row(), _row(gate_ok=None), _row_without("gate_ok")]
    line = _gate_line(summarize(rows))
    #  ⚠️ 只数「档名 数字」这种形态，⛔ 别把行尾那句「其中 N 单来自老行」也数进来
    total = sum(int(m) for m in re.findall(
        r"(?:全过|有未过|闸自身故障|没跑闸|无判定记录|判不了) (\d+)", line))
    assert total == len(rows),         f"⛔ 各档相加 {total} ≠ 实际 {len(rows)} 单——有单元消失了：{line}"


def _row_without(key: str) -> dict:
    r = _row()
    r.pop(key, None)
    return r
