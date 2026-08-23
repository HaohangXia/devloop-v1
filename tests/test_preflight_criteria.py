"""⭐⭐ 开跑前先验判据本身（G-127）。

## 第一性：一个拓扑上的洞

工具的防线**全在花钱之前**拦 —— 宪法的锚、作业格式校验、额度闸。
⛔ **唯独「判据本身对不对」要花完钱才知道**：它写在闸里，
而闸只在工人干完之后才跑。

⚠️ 而判据恰恰是最容易写错的那一样。翻遍历史：**工人从来不是瓶颈**，
每一次失败都追到「判据或作业写错了」。

⭐ 2026-08-08 的账单：一道**数学上必然红**的闸（对照存档比派单基准旧 7 个提交，
中间还隔着一次故意改变世界的改动）——

| | |
|---|---|
| 烧掉 | **5,146,226 tokens** |
| 产出 | **0 个文件** |
| ⛔ 更贵的 | 一个 **42 秒**就改对了的工人，**把自己正确的代码删了**，追了 49 分钟不存在的 bug |

⭐ 而同一件事，体检模式 **0.55 秒**就点名了：
「存档录于 5e746f181a0e，本单基准 f7636381ab35，隔着 7 个提交」。

## ⛔ 第一版是错的，错法本身值得记

第一版直接跑闸脚本，靠 `timeout_s=120` 兜底。**当场撞上两个坑**：

1. 不认识开关的闸会**照常跑全套**（eco-ob 实测 26 分钟）；
2. ⛔ `subprocess` 的 timeout 杀得掉 bash，**杀不掉 bash 起的 godot** ——
   孙进程攥着管道不放，整个调用**挂死**，超时形同虚设。

⭐ 第一性修法：**体检必须构造上就便宜**，⛔ 不能靠一个超时把昂贵变便宜。
→ 判据落在**一行声明**上（`DEVLOOP-PREFLIGHT: 1`）：闸自己写明支持，才跑它。
"""

from __future__ import annotations

import ast
import inspect

from devloop import cli, gates


def _paths(tmp_path, gates_body: str):
    from devloop.config import ProjectPaths  # noqa: PLC0415
    d = tmp_path / ".devloop"
    d.mkdir(parents=True, exist_ok=True)
    (d / "gates.sh").write_text(gates_body, encoding="utf-8")
    return ProjectPaths(tmp_path)


def test_闸没声明支持体检时不许当成通过(tmp_path) -> None:
    """⛔ 缺省必须落在**保守**那边。

    ⚠️「没人验过」和「验过了没问题」是两件事 ——
    把前者读成后者，正是本项目七种假绿的第三种（判据的维度错了）。
    """
    p = _paths(tmp_path, "#!/usr/bin/env bash\necho hi\n")
    verdict, lines, _ = gates.preflight(p, base_commit="abc123")
    #  ⭐ 三态：这一档是 unknown（答不上来），⛔ **不是** ok，⚠️ 也不是 bad。
    #     ⛔ 判成 ok = 把「没验过」读成「没问题」（第三种假绿）；
    #     ⛔ 判成 bad = 把所有还没适配的项目一刀切死 —— ⚠️ 那会逼人去关掉守卫
    #        （2026-08-09 第一版就是这么写的，10 条回归当场变红）。
    assert verdict == "unknown", f"⛔ 应判「答不上来」，实际 {verdict}"
    assert any("没声明" in x for x in lines), f"⚠️ 理由要说人话，实际：{lines}"


def test_不声明就一行都不跑(tmp_path) -> None:
    """⛔ 这一条守的是**代价**，不是结果。

    ⚠️ 第一版直接跑，结果不认识开关的闸照跑全套 26 分钟，
    而超时杀不掉孙进程 → **整个调用挂死**。
    ⭐ 所以「不声明就不跑」必须是**读文件**判出来的，不能是「跑了再说」。
    """
    #  ⭐ 这个脚本一旦被执行就会睡很久；测试若超时 = 「先看声明」那道判据没生效
    p = _paths(tmp_path, "#!/usr/bin/env bash\nsleep 300\n")
    import time as _t
    t0 = _t.time()
    verdict, _, _ = gates.preflight(p, base_commit="abc123")
    assert _t.time() - t0 < 5.0, (
        "⛔ 它去跑那个脚本了——⚠️ 必须先读文件看有没有声明，"
        "**不声明就一行都不跑**（第一版就是这么挂死的）")
    assert verdict == "unknown"


def test_声明了就按它打的行判(tmp_path) -> None:
    """⭐ 契约：每道闸一行 `PREFLIGHT<TAB>名<TAB>OK|BAD|VOID<TAB>说明`。"""
    body = ('#!/usr/bin/env bash\n# DEVLOOP-PREFLIGHT: 1\n'
            'printf "PREFLIGHT\\t甲闸\\tOK\\t前提在\\n"\n'
            'printf "PREFLIGHT\\t乙闸\\tBAD\\t存档比基准旧 7 个提交\\n"\n')
    verdict, lines, _ = gates.preflight(_paths(tmp_path, body), base_commit="abc123")
    assert verdict == "bad", f"⛔ 有一道明说前提不成立，必须判 bad（拒绝派单），实际 {verdict}"
    assert any("乙闸" in x and "旧" in x for x in lines), f"⚠️ 原因要透出来：{lines}"

    body_ok = ('#!/usr/bin/env bash\n# DEVLOOP-PREFLIGHT: 1\n'
               'printf "PREFLIGHT\\t甲闸\\tOK\\t前提在\\n"\n')
    v2, _, _ = gates.preflight(_paths(tmp_path / "b", body_ok), base_commit="abc123")
    assert v2 == "ok", "⭐ 全 OK 就该放行——⛔ 否则这道体检自己就成了恒假的闸"


def test_声明了却一行不打也不许当通过(tmp_path) -> None:
    """⚠️ 「声明支持」不等于「真的实现了」。⛔ 一行都没打 = 没验过。"""
    body = '#!/usr/bin/env bash\n# DEVLOOP-PREFLIGHT: 1\nexit 0\n'
    verdict, lines, _ = gates.preflight(_paths(tmp_path, body), base_commit="abc123")
    assert verdict == "unknown", (
        f"⛔ 退出码 0 但一行没打，判成了 {verdict}——⚠️ 「空转即绿」的近亲")
    assert any("一行都没打" in x for x in lines), f"⚠️ 理由要说人话：{lines}"


def test_基准提交真的传给了体检(tmp_path) -> None:
    """⛔ 体检要判「存档配不配得上这份代码」，不给基准它什么都判不了。"""
    body = ('#!/usr/bin/env bash\n# DEVLOOP-PREFLIGHT: 1\n'
            'printf "PREFLIGHT\\t看基准\\tOK\\t收到=%s\\n" "${DEVLOOP_BASE_COMMIT:-空}"\n')
    _, lines, _ = gates.preflight(_paths(tmp_path, body), base_commit="deadbeef1234")
    assert any("deadbeef1234" in x for x in lines), (
        f"⛔ `DEVLOOP_BASE_COMMIT` 没传到闸里——体检判不了存档新旧。实际：{lines}")


def test_体检必须在演练里就跑() -> None:
    """⭐⭐ 这一条是整条修法的**要害**。

    ⛔ 第一版把体检插在了 `--dry-run` 的返回**之后** ——
    ⚠️ 那等于「判据坏了依然要花完钱才知道」，**原样复现了要修的那个洞**。
    """
    src = inspect.getsource(cli.cmd_autopilot)
    i_pre = src.find("_preflight_criteria")
    i_dry = src.find("args.dry_run")
    assert i_pre >= 0, "⛔ `cmd_autopilot` 根本没调体检"
    assert i_dry >= 0
    assert i_pre < i_dry, (
        "⛔ 体检排在了演练之后——⚠️ 那样「判据坏了」依然要花完钱才知道，"
        "**等于什么都没修**")


def test_答不上来时要放行但必须喊出来() -> None:
    """⭐ 比例原则：「没人验过」不等于「有问题」。

    ⛔ 2026-08-09 第一版把这一档也拒了 —— **一单都派不出去**（10 条回归当场变红）。
    ⚠️ 而更坏的后果是：那会逼人去**关掉守卫**，
    ⭐ 这个项目最不想要的结局就是「守卫太吵，于是被摘掉」。
    """
    src = inspect.getsource(cli._preflight_criteria)
    assert '"unknown"' in src, "⛔ 没区分「答不上来」这一档——要么全拒要么全放"
    i_unknown = src.index('"unknown"')
    i_return_false = src.rindex("return False")
    assert i_unknown < i_return_false, (
        "⛔ 「答不上来」那一档没在拒绝之前处理 —— 它会掉进拒绝分支")
    assert "没有被验过" in src, (
        "⚠️ 放行可以，⛔ 但必须**喊出来**：静默放行 = 又一次静默降级")


def test_只对被点名的闸卡死() -> None:
    """⛔ 比例原则：没点名任何闸的阶段照旧放行。

    ⚠️ 为了严谨把所有老用法都打死，是另一种坏 —— 那会逼人去关掉守卫。
    """
    tree = ast.parse(inspect.getsource(cli._preflight_criteria).lstrip())
    src = inspect.getsource(cli._preflight_criteria)
    assert "effective_require_pass" in src, (
        "⛔ 没看 `require_pass`——⚠️ 要么一刀切卡死所有阶段，要么一个都不卡")
    #  ⭐ 必须有「没点名 → 直接放行」那条早退
    has_early_true = any(
        isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
        and n.value.value is True
        for n in ast.walk(tree))
    assert has_early_true, "⛔ 没有「没点名闸就放行」的早退——比例原则没落地"
