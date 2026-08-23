"""测试守卫的判据：**改**已有测试算作弊，**新建**测试不算。

## ⚠️ 2026-07-29 修正的判据错误

原判据是「`tests/` 一个字都不许动」。它确实挡住了作弊，但**把合法写测试
也一起挡了**——而 TDD 是本项目的硬要求。

实测（订阅后端第一单真跑）：一单「加个函数并补两条测试」的正常活，
工人干得完全正确，闸却判失败，理由是 `M tests/test_subscription_backend.py`。

⛔ 那是判据维度错了：要防的是「**改**已有断言」，不是「碰过 tests/」。
按原判据，工人**永远无法**为自己写的功能补测试——于是要么没有测试，
要么每一单都红。两条都不能接受。

## 新判据只有两句话

  · 改 / 删 / 改名**已有**测试文件  → 失败（削弱断言的唯一途径）
  · 新建 `test_*.py`               → 放行（新增断言只会更严）
  · 新建其它文件（尤其 conftest.py）→ 失败

最后一条不是洁癖：`tests/conftest.py` 里一个 `autouse` fixture 就能把整包
已有测试架空——那是「新建文件」却能**削弱已有断言**的唯一后门。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from devloop.gates import find_bash

GATES = Path(__file__).resolve().parent.parent / ".devloop" / "gates.sh"


def _run_guard(work: Path) -> tuple[str, str]:
    """跑真闸，只把「测试守卫」那一行摘出来。返回 (判定, 说明)。

    ⚠️ 跑的是仓库里那份真 `gates.sh`，不是复刻品——复刻品会和真闸各自漂移，
    那时测试绿而闸坏，正是本项目一直在防的「测了错的东西」。
    """
    out = subprocess.run(
        [find_bash(), str(GATES), str(work)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "DEVLOOP_CLEAN_CHECKOUT": "1"},
    ).stdout
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] == "测试守卫":
            return parts[0], parts[2]
    raise AssertionError(f"闸没有输出「测试守卫」这一行。全部输出：\n{out}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个最小仓库：有 devloop/ 包和一份已提交的测试。"""
    if shutil.which("git") is None:                      # pragma: no cover
        pytest.skip("没有 git")
    (tmp_path / "devloop").mkdir()
    (tmp_path / "devloop" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_已有.py").write_text(
        "def test_x():\n    assert 1 == 1\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True,
                       capture_output=True)
    return tmp_path


def test_没碰测试就放行(repo: Path):
    verdict, detail = _run_guard(repo)
    assert verdict == "PASS", detail


def test_新建测试文件必须放行(repo: Path):
    """⭐ 这条是本次修正的**目的**：不放行它，工人就永远写不了测试。"""
    (repo / "tests" / "test_工人新加的.py").write_text(
        "def test_y():\n    assert 2 == 2\n", encoding="utf-8")
    verdict, detail = _run_guard(repo)
    assert verdict == "PASS", f"新建测试文件被误判为作弊：{detail}"
    assert "新增 1" in detail, f"说明里要写清放行了几个：{detail}"


def test_改动已有测试必须判失败(repo: Path):
    """⛔ 削弱断言的唯一途径。这条是原判据的**正确内核**，不能连同修正一起丢掉。"""
    (repo / "tests" / "test_已有.py").write_text(
        "def test_x():\n    assert True   # 断言被改松了\n", encoding="utf-8")
    verdict, detail = _run_guard(repo)
    assert verdict == "FAIL", f"改动已有测试竟然放行了：{detail}"
    assert "改动了已有测试" in detail


def test_删除已有测试必须判失败(repo: Path):
    """⚠️ 删掉碍事的测试与改松它等价，判据必须同样覆盖——
    只写 M 不写 D 是很容易漏的半个守卫。"""
    (repo / "tests" / "test_已有.py").unlink()
    verdict, detail = _run_guard(repo)
    assert verdict == "FAIL", f"删除已有测试竟然放行了：{detail}"


def test_新建conftest必须判失败(repo: Path):
    """⛔ **「新建文件」里唯一能削弱已有断言的后门。**

    `tests/conftest.py` 里一个 autouse fixture 就能把整包已有测试架空，
    而它在 `git status` 里和一份正常的新测试长得一模一样（都是 `??`）。
    所以判据不能只看「新增还是改动」，还要看**新增的是什么**。
    """
    (repo / "tests" / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def 架空一切(monkeypatch):\n    pass\n", encoding="utf-8")
    verdict, detail = _run_guard(repo)
    assert verdict == "FAIL", f"新建 conftest.py 竟然放行了：{detail}"
    assert "conftest" in detail
