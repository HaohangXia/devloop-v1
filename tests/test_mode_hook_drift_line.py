"""⛔ 每轮注入的那块「回到工具」的牌子：**该响时必须响，不该响时一个字都不许多**。

## 这条是怎么来的（2026-08-11）

用户原话：「我们的主要目标是这个工具，eco-ob 是我们的手段。
**我感觉你每次都在忘这一条。**有什么方法可以让你做完一个阶段的、
比如用 eco-ob 测试完，能自己回到正轨上来吗？」

⭐ 第一性原则推下来只有一条路：主模型跨轮次没有持久注意力，
能穿过上下文重建的只有「自动加载的文件」和「钩子注入的文字」。
⛔ 而「忘了目的」的那一轮，恰恰是不会主动去读讲目的那份文档的那一轮。
⇒ 牌子只能立在**每轮必到的注入**上。

## ⛔ 为什么判据是「闸版号」而不是「有没有写反思」

要让这个数归零，**必须真的去编辑 `templates/gates.sh` 的内容**。
⚠️ 「写下来」这条路已经被实测证伪两次（`_trials/` 的 T-7、`BACKLOG.md` 的 G-124
都白纸黑字写过同一条教训，然后原样复发）。字是免费的，版号不是。

## ⭐ 三条测试里有两条是**绿检**

⛔ 一块不管欠不欠都印同一句的牌子，等于没牌子 —— 而且会先变成噪音、
再被整个摘掉。所以「不该响时一个字都不多」必须被断言，
⚠️ 而且要断言**长度相等**，⛔ 不能只断言「不含关键词」。

⚠️ 喂 stdin **必须**走 `json.dump` 或临时文件，⛔ 不许用 `echo '{...}'`
—— Git Bash 会吃掉反斜杠，造成假阴性（eco-ob 的 `gd_check.py` 文件头记过这笔学费）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "tools" / "devloop-mode-hook.py"
TEMPLATE = ROOT / "templates" / "gates.sh"


def _tpl_rev() -> int:
    import re
    m = re.search(r"DEVLOOP-GATES-REV:\s*(\d+)", TEMPLATE.read_text(encoding="utf-8"))
    assert m, "⛔ 模板自己没有版本代号，这套判据无从谈起"
    return int(m.group(1))


def _proj(tmp_path: Path, rev: int | None) -> Path:
    """造一个临时项目。rev=None 表示**压根没有** `.devloop/`。"""
    p = tmp_path / "proj"
    if rev is not None:
        (p / ".devloop").mkdir(parents=True)
        (p / ".devloop" / "gates.sh").write_text(
            f"#!/usr/bin/env bash\n# DEVLOOP-GATES-REV: {rev}\n", encoding="utf-8")
    else:
        p.mkdir(parents=True)
    return p


def _inject(cwd: Path) -> str:
    """跑一遍钩子，把它注入给模型的那段文字取回来。"""
    payload = json.dumps({"hook_event_name": "UserPromptSubmit",
                          "session_id": "drift-test",
                          "cwd": str(cwd),
                          "prompt": "随便问一句"}, ensure_ascii=False)
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload.encode("utf-8"),
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, f"⛔ 钩子自己挂了：{r.stderr.decode('utf-8', 'replace')[-800:]}"
    assert out.strip(), "⛔ 钩子一个字都没吐 —— 注入这条管子断了"
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _baseline() -> str:
    """不欠账时应有的注入长度（模式 A 那几行）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_mh", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.CTX["A"]


def test_靶子领先时注入里必须点名(tmp_path: Path) -> None:
    """⭐ 红检：闸比模板新 3 版 → 那块牌子必须出现，且说清领先几版。"""
    ctx = _inject(_proj(tmp_path, _tpl_rev() + 3))
    assert "比模板新" in ctx, f"⛔ 领先 3 版居然一声不吭：\n{ctx[:400]}"
    assert "3 版" in ctx, ctx[:400]
    #  ⭐ 光喊「有问题」不够，还得说清**这个仓是靶子** —— 那才是要治的那件事
    assert "靶子" in ctx and "DevLoop 才是目的" in ctx, ctx[:400]


def test_两边同版时一个字都不许印(tmp_path: Path) -> None:
    """⭐ 绿检①：搬完之后它必须**自己闭嘴**。

    ⛔ 断言的是**长度相等**，不是「不含关键词」——
    ⚠️ 后者挡不住「换个说法照样天天喊」。
    """
    ctx = _inject(_proj(tmp_path, _tpl_rev()))
    assert ctx == _baseline(), f"⛔ 不欠账却多印了字：\n{ctx[:400]}"


def test_不是devloop项目时一个字都不许印(tmp_path: Path) -> None:
    """⭐ 绿检②：这个钩子挂在全局 `UserPromptSubmit` 上、**没有 matcher**。

    ⚠️ `C:/pg` 下十几个仓都会跑到它。在一个跟 DevLoop 无关的目录里
    每轮被告知「eco-ob 欠账」是纯噪音，⛔ 而那正是会让人把整个钩子摘掉的那种。
    """
    ctx = _inject(_proj(tmp_path, None))
    assert ctx == _baseline(), f"⛔ 在非 DevLoop 项目里也在喊：\n{ctx[:400]}"


def test_真eco_ob上今天必须是响的() -> None:
    """⛔ **防空转**：上线第一天就必须是响的，否则这套东西是假绿。

    ⚠️ 已知事实：`grep -c PREFLIGHT` → 模板 **0** / eco-ob **7**，
    体检模式（G-127）做出来了但只装在靶子上。
    ⭐ 所以「装完就绿」= 判据坏了，当场作废。

    ⚠️ 这条会在 PREFLIGHT 搬回模板、两边同版之后**自然失效** ——
    ⭐ 那时它应当被改成绿检（断言不响），⛔ 而不是被删掉。
    """
    eco = Path("C:/pg/eco-ob")
    if not (eco / ".devloop" / "gates.sh").exists():
        import pytest
        pytest.skip("本机没有 eco-ob，跳过")
    from devloop.doctor import gates_rev_pair
    a, b = gates_rev_pair(eco)
    if a is None or b is None or a <= b:
        import pytest
        pytest.skip(f"eco-ob 已经不领先了（rev {a} vs 模板 {b}）—— 欠账已还")
    ctx = _inject(eco)
    assert "比模板新" in ctx, f"⛔ eco-ob 领先 {a - b} 版却不吭声：\n{ctx[:400]}"
