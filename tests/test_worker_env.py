"""项目可以声明要传给工人的环境变量。

## ⛔ 为什么需要

2026-08-01 eco-ob 首跑：任务书让工人跑 `godot --headless --check-only ...`，
工人报 `godot: command not found, exit=127`。

查下来**这台机器上根本没有叫 `godot` 的命令**——闸里写死的是完整路径
（`.devloop/gates.sh`: `GODOT="${GODOT_BIN:-C:/.../Godot_v4.7-stable_win64_console.exe}"`）。

⚠️ 也就是说：**工人和闸看到的不是同一个环境**，而此前没人知道。
任务书要求工人跑一个它环境里不存在的命令，于是唯一的本地语法校验永远跑不成。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from devloop.config import ProjectPaths


def _proj(tmp_path: Path, toml: str) -> ProjectPaths:
    (tmp_path / ".devloop").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".devloop" / "config.toml").write_text(toml, encoding="utf-8")
    return ProjectPaths(tmp_path)


def test_声明的环境变量能读出来(tmp_path: Path) -> None:
    p = _proj(tmp_path, '[worker]\nenv = { GODOT_BIN = "C:/godot.exe" }\n')
    assert p.worker_env() == {"GODOT_BIN": "C:/godot.exe"}


def test_没声明就是空(tmp_path: Path) -> None:
    assert _proj(tmp_path, "[gates]\n").worker_env() == {}


def test_没有配置文件也不炸(tmp_path: Path) -> None:
    (tmp_path / ".devloop").mkdir(parents=True)
    assert ProjectPaths(tmp_path).worker_env() == {}


@pytest.mark.parametrize("name", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_SIMPLE", "PATH",
])
def test_认证与PATH类变量一律拒绝(tmp_path: Path, name: str) -> None:
    """⛔ 项目配置能改工人的认证或命令解析 = 一个提权面。

    ⚠️ `.devloop/config.toml` 在宪法的受保护清单里（改了会被发现），
    但「会被发现」不等于「不该允许」——这类变量**从一开始就不该由项目配置来定**。
    """
    p = _proj(tmp_path, f'[worker]\nenv = {{ {name} = "x" }}\n')
    with pytest.raises(Exception) as e:
        p.worker_env()
    assert name in str(e.value)


def test_值必须是字符串(tmp_path: Path) -> None:
    """⚠️ toml 里写 `TIMEOUT = 30` 很自然，但 subprocess 的 env 只吃字符串，
    ⛔ 传个 int 进去会在**派单那一刻**才炸——那时钱已经花了。"""
    p = _proj(tmp_path, "[worker]\nenv = { TIMEOUT = 30 }\n")
    with pytest.raises(Exception) as e:
        p.worker_env()
    assert "TIMEOUT" in str(e.value)


def test_eco_ob的配置真的声明了GODOT_BIN() -> None:
    """⛔ 光有机制没用——要证明真实项目用上了，且路径真的存在。"""
    f = Path("C:/pg/eco-ob/.devloop/config.toml")
    if not f.exists():
        pytest.skip("eco-ob 不在这台机器上")
    env = tomllib.loads(f.read_text(encoding="utf-8")).get("worker", {}).get("env", {})
    assert "GODOT_BIN" in env, "⛔ eco-ob 没声明 GODOT_BIN——工人还是跑不了 godot"
    assert Path(env["GODOT_BIN"]).exists(), \
        f"⛔ 声明的路径不存在：{env['GODOT_BIN']}——写错的路径会静默失效"
