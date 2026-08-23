"""工人配置与项目 .devloop/ 目录的发现。

两条纪律：
1. 找不到就报错退出，绝不静默降级到默认值。
2. 配置来自哪个文件必须能报出来——诊断时这是第一个要问的问题。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import WorkerConfig

CLAUDE_HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / ".claude"
WORKER_FILE = CLAUDE_HOME / "worker-deepseek.json"
SETTINGS_FILE = CLAUDE_HOME / "settings.json"


class ConfigError(RuntimeError):
    """退出码 2 —— 工具自身错误，与「活没干好」区分开。"""


def load_worker_config() -> WorkerConfig:
    """按优先级找工人配置，并记录实际来源。

    优先 worker-deepseek.json（Phase 0 的产物）；它还不存在时退回读
    settings.json 的 env 块——但**这是显式的、会被报出来的退路**，
    不是悄悄发生的默认行为。
    """
    if WORKER_FILE.exists():
        raw = json.loads(WORKER_FILE.read_text(encoding="utf-8"))
        return WorkerConfig(**raw, source=str(WORKER_FILE))

    if SETTINGS_FILE.exists():
        env = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("env", {})
        token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
        base = env.get("ANTHROPIC_BASE_URL")
        model = env.get("ANTHROPIC_MODEL")
        if token and base and model:
            return WorkerConfig(
                base_url=base,
                model=model,
                auth_token=token,
                timeout_s=int(env.get("API_TIMEOUT_MS", 3_000_000)) // 1000,
                source=f"{SETTINGS_FILE}（回退路径：Phase 0 完成后应改为 {WORKER_FILE.name}）",
            )

    raise ConfigError(
        f"找不到工人配置。请建 {WORKER_FILE}，内容形如：\n"
        '  {"base_url": "...", "model": "...", "auth_token": "...", "timeout_s": 3000}'
    )


class ProjectPaths:
    """项目侧 .devloop/ 的布局。找不到即报错——见 SPEC.md §4 发现规则。"""

    def __init__(self, project: Path):
        self.project = project.resolve()
        self.root = self.project / ".devloop"
        if not self.root.is_dir():
            raise ConfigError(
                f"{self.project} 下没有 .devloop/ 目录。\n"
                f"接入三步见 SPEC.md §6：建目录 → 填 gates.sh 与 rules-digest.md → 派个只读任务验证。"
            )

    @property
    def rules_digest(self) -> Path:
        return self.root / "rules-digest.md"

    @property
    def gates(self) -> Path:
        return self.root / "gates.sh"

    @property
    def reports(self) -> Path:
        d = self.root / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def findings(self) -> Path:
        """审计发现的台账。⚠️ 与 telemetry 分开：台账记「跑了什么」，
        这里记「查出了什么」——两者的生命周期完全不同（一单跑完就定了，
        而一条发现要经历 报出→复核→修掉 三个阶段）。"""
        return self.root / "findings.jsonl"

    @property
    def telemetry(self) -> Path:
        return self.root / "telemetry.jsonl"

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    def synced_paths(self) -> list[str]:
        """干净检出里必须补齐的、被 gitignore 的构建缓存。

        为什么需要：worktree 是干净检出，被 gitignore 的目录（引擎导入缓存、
        依赖目录等）不会出现在里面，工具链会因缺它们而失败——闸于是产生**假失败**。
        2026-07-26 实测：eco-ob 的 `game/.godot/` 缺失导致 godot 无法加载资源，
        两次写任务被误判为工人失败。
        """
        if not self.config_file.exists():
            return []
        import tomllib
        data = tomllib.loads(self.config_file.read_text(encoding="utf-8"))
        return list(data.get("gates", {}).get("sync_ignored_paths", []))

    def default_backend(self) -> str | None:
        """项目级的默认执行者（`config.toml` 的 `[worker].backend`）。

        ⚠️ 返回 None 表示「本项目没有意见」，由注册表的 default 决定——
        **不在这里编一个默认值**。找不到配置就静默挑一个，是本项目最贵的
        失效模式（「你以为在用 A，实际在用 B」）的温床。
        """
        if not self.config_file.exists():
            return None
        import tomllib
        data = tomllib.loads(self.config_file.read_text(encoding="utf-8"))
        return data.get("worker", {}).get("backend") or None

    #  ⛔ 项目配置**不许**碰这几类变量。
    #  · `ANTHROPIC_*` / `CLAUDE_*`：改的是工人的认证与运行模式——
    #    那是后端注册表（`~/.claude/devloop-backends.json`）的职责，
    #    ⚠️ 密钥永不进任何仓库，项目配置更不该有能力覆盖它。
    #  · `PATH`：改的是命令解析。让项目配置决定工人执行哪个二进制，
    #    等于给了它一个提权面。
    #  ⚠️ `.devloop/config.toml` 确实在宪法的受保护清单里（改了会被发现），
    #    但「会被发现」不等于「该允许」——这几类从一开始就不该由项目定。
    _ENV_DENY_PREFIX = ("ANTHROPIC_", "CLAUDE_")
    _ENV_DENY_EXACT = ("PATH",)

    def worker_env(self) -> dict[str, str]:
        """项目声明的、要传给工人进程的环境变量（`[worker].env`）。

        ## ⛔ 为什么需要这个

        2026-08-01 eco-ob 首跑：任务书让工人跑 `godot --headless --check-only`，
        工人报 `godot: command not found, exit=127`。查下来**这台机器上根本
        没有叫 `godot` 的命令**——闸里写死的是完整路径。

        ⚠️ 也就是说 **工人和闸看到的不是同一个环境**，而此前没人知道。
        任务书要求工人跑一个它环境里不存在的命令，于是唯一的本地语法校验
        永远跑不成，问题要拖到 900 秒的全量回归才暴露。

        ⛔ 值必须是字符串：toml 里写 `TIMEOUT = 30` 很自然，但 subprocess 的
        env 只吃字符串，传 int 进去会在**派单那一刻**才炸——那时钱已经花了。
        所以在这里就拦下来。
        """
        if not self.config_file.exists():
            return {}
        import tomllib
        raw = tomllib.loads(self.config_file.read_text(encoding="utf-8"))
        env = raw.get("worker", {}).get("env", {}) or {}
        out: dict[str, str] = {}
        for k, v in env.items():
            if k in self._ENV_DENY_EXACT or k.startswith(self._ENV_DENY_PREFIX):
                raise ConfigError(
                    f"{self.config_file} 的 [worker].env 不许设 `{k}`。\n"
                    f"   ⛔ 认证类（ANTHROPIC_*/CLAUDE_*）由后端注册表管，"
                    f"PATH 决定工人执行哪个二进制——都不该由项目配置来定。")
            if not isinstance(v, str):
                raise ConfigError(
                    f"{self.config_file} 的 [worker].env 里 `{k}` 不是字符串"
                    f"（是 {type(v).__name__}）。\n"
                    f"   ⚠️ 环境变量只能是字符串。写成 \"{v}\" 即可。")
            out[k] = v
        return out

    def read_rules_digest(self) -> str:
        """工人读不到项目 CLAUDE.md（--bare 的代价），这是它获知规则的唯一渠道。"""
        if not self.rules_digest.exists():
            raise ConfigError(
                f"缺 {self.rules_digest}。\n"
                f"--bare 模式下工人读不到项目 CLAUDE.md，规则摘要是它唯一的规则来源；"
                f"没有它就派单等于让工人在没有护栏的路上开车。"
            )
        return self.rules_digest.read_text(encoding="utf-8")
