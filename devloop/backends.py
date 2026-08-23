"""工人后端注册表 —— 「换一个 api 就换个模型」。

**为什么是「后端」而不是「模型」**：用户的心智单位是一整套东西——端点 + 模型名 +
密钥 + 价目表键。一个名字选中一整套，才叫「换一个 api 的事情」。
`--model` 只能选中模型名，端点和钥匙还得另配，那不是同一件事。

三类后端，只在**执行腿**分叉，准备阶段完全对称：

| kind | 谁来干 | 怎么跑 |
|---|---|---|
| `api` | 任何 Anthropic 兼容端点（DeepSeek / Kimi / GLM / …） | 子进程 `claude --bare -p` |
| `subagent` | Claude 子代理（就是编排方自己，只是并行开几个） | ⛔ **不能是子进程**，走交接协议 |
| `anthropic` | Anthropic 官方 API | 同 api，但密钥注入 `ANTHROPIC_API_KEY` |

⛔ **`subagent` 不能做成子进程**（2026-07-27 实测）：`claude auth status` 说已登录，
但那是桌面 App 在**内存**里刷新的；派生的子进程读**磁盘上**那份过期凭据 →
`401 OAuth access token has expired`。用户没有 Anthropic key 也不打算配。
所以它只能是「Python 出工单 → 编排方起子代理 → 结果写回」的交接。

⛔ **密钥永不进仓库**：注册表住在 `~/.claude/`，且支持 `auth_token_env` 从环境变量取。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .models import WorkerConfig

CLAUDE_HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / ".claude"
REGISTRY_FILE = CLAUDE_HOME / "devloop-backends.json"
LEGACY_WORKER_FILE = CLAUDE_HOME / "worker-deepseek.json"

KINDS = ("api", "anthropic", "subagent", "subscription")
#  ⭐ subscription（2026-07-29 加）：起子进程但**走你的 Claude 订阅**（OAuth），
#     不注入端点/密钥、不加 --bare。它是唯一同时满足「无人值守」与「贵模型质量」的路。
#     此前判定不可行，根子是一个过期两个月的令牌，不是技术限制。


class BackendError(RuntimeError):
    """退出码 2 —— 配置问题，与「活没干好」区分开。"""


@dataclass(frozen=True)
class Backend:
    """一个可派单的执行者。不可变——配置在加载后不该再被改。"""

    name: str
    kind: str
    model: str
    source: str                       # 这份配置从哪来的，诊断时第一个要问的
    base_url: str = ""
    auth_token: str = ""              # ⚠️ 不进任何 __repr__ / 序列化，见 redacted()
    price_key: str = ""               # 缺省 = model，见 pricing.py
    timeout_s: int = 3000
    agent_type: str = "general-purpose"   # 仅 subagent
    max_parallel: int = 4                 # 仅 subagent，写进工单给编排方看
    enabled: bool = True
    note: str = ""

    @property
    def is_subprocess(self) -> bool:
        """能不能用子进程跑。⛔ subagent 不能——见模块 docstring。

        ⭐ `subscription` 能：2026-07-29 实测，重新登录之后
        `claude -p` 起的子进程跑通了（模型 claude-opus-4-7，工具白名单与
        工作目录都生效）。此前判定它不行，根子是一个**过期两个月的令牌**。
        """
        return self.kind in ("api", "anthropic", "subscription")

    #  ⚠️ 旧名保留：`can_subprocess` 是同一件事的另一个叫法
    can_subprocess = is_subprocess

    @property
    def is_subscription(self) -> bool:
        """走你的 Claude 订阅（OAuth），不花美元、花额度。"""
        return self.kind == "subscription"

    @property
    def price_lookup(self) -> str:
        return self.price_key or self.model

    def worker_config(self) -> WorkerConfig:
        """转成 dispatch 需要的形态。仅子进程类后端可用。"""
        if not self.is_subprocess:
            raise BackendError(
                f"后端 {self.name} 的 kind={self.kind} 不能用子进程派单。\n"
                f"⛔ 子代理无法以子进程运行：派生进程读磁盘上的 OAuth 凭据，而那份是过期的\n"
                f"   （桌面 App 只在内存里刷新）。走交接协议，见 SPEC.md §5.7。")
        return WorkerConfig(base_url=self.base_url, model=self.model,
                            auth_token=self.auth_token, timeout_s=self.timeout_s,
                            source=f"{self.source} · 后端 {self.name}",
                            subscription=self.is_subscription,
                            price_key=self.price_lookup)

    def redacted(self) -> dict:
        """给人看、给日志用的形态。**密钥只报长度与来源，绝不报明文。**"""
        if self.kind == "subagent":
            tok = "—（子代理不需要密钥）"
        elif self.auth_token:
            tok = f"<已配置 len={len(self.auth_token)}>"
        else:
            tok = "<无>"
        return {"name": self.name, "kind": self.kind, "model": self.model,
                "base_url": self.base_url or "—", "auth_token": tok,
                "price_key": self.price_lookup, "enabled": self.enabled,
                "source": self.source, "note": self.note}


@dataclass(frozen=True)
class Registry:
    backends: dict[str, Backend]
    aliases: dict[str, str]
    default: str
    source: str

    def resolve(self, name: str | None) -> Backend:
        """按名字或别名取后端。

        ⛔ **找不到就报错，绝不静默挑一个。** 派单最贵的失效模式是
        「你以为在用 A，实际在用 B」——硬编码默认值正是它的温床。
        """
        if not name:
            if not self.default:
                raise BackendError(
                    f"没指定 --backend，注册表（{self.source}）也没有 default 字段。\n"
                    f"可用后端：{', '.join(sorted(self.backends)) or '（空）'}")
            name = self.default
        real = self.aliases.get(name, name)
        b = self.backends.get(real)
        if b is None:
            extra = f"（别名 {name} → {real}）" if real != name else ""
            raise BackendError(
                f"没有名为 {name} 的后端{extra}。\n"
                f"可用：{', '.join(sorted(self.backends)) or '（空）'}\n"
                f"别名：{', '.join(f'{k}→{v}' for k, v in sorted(self.aliases.items())) or '（无）'}\n"
                f"注册表：{self.source}")
        if not b.enabled:
            raise BackendError(
                f"后端 {b.name} 被标记为 enabled=false，不可使用。"
                + (f"\n原因：{b.note}" if b.note else ""))
        return b


def _one(name: str, raw: dict, source: str) -> Backend:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise BackendError(f"后端 {name} 的 kind={kind!r} 无效，只能是 {'/'.join(KINDS)}")

    tok = raw.get("auth_token")
    tok_env = raw.get("auth_token_env")
    if kind == "subagent":
        if tok or tok_env:
            raise BackendError(f"后端 {name} 是 subagent，不该配密钥——它不走网络端点")
    else:
        if tok and tok_env:
            # 歧义即错误：两个都给，就没人知道实际用的是哪个
            raise BackendError(f"后端 {name} 同时配了 auth_token 与 auth_token_env，二选一")
        if tok_env:
            tok = os.environ.get(tok_env)
            if not tok:
                # 加载时就炸，不拖到派单时——那时已经建了 worktree、花了准备时间
                raise BackendError(
                    f"后端 {name} 要求环境变量 {tok_env}，但它没有设置。")
        if not tok:
            # ⭐ 订阅后端**不需要密钥**——它走 OAuth（~/.claude/.credentials.json）。
            #    ⚠️ 但那份凭据会过期（实测一次只管 8 小时），
            #    所以它的「有没有配好」不在这里判，在 credentials.check() 里判。
            if kind != "subscription":
                raise BackendError(f"后端 {name} 缺 auth_token / auth_token_env")
        if not raw.get("base_url") and kind == "api":
            raise BackendError(f"后端 {name} 是 api，必须给 base_url")

    return Backend(
        name=name, kind=kind, model=raw.get("model", ""), source=source,
        base_url=raw.get("base_url", ""), auth_token=tok or "",
        price_key=raw.get("price_key", ""), timeout_s=int(raw.get("timeout_s", 3000)),
        agent_type=raw.get("agent_type", "general-purpose"),
        max_parallel=int(raw.get("max_parallel", 4)),
        enabled=bool(raw.get("enabled", True)), note=raw.get("note", ""),
    )


def load() -> Registry:
    """加载注册表。回退路径必须被报出来，不是悄悄发生的。"""
    if REGISTRY_FILE.exists():
        raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        src = str(REGISTRY_FILE)
        backs = {n: _one(n, b, src) for n, b in (raw.get("backends") or {}).items()}
        if not backs:
            raise BackendError(f"{REGISTRY_FILE} 里没有任何后端")
        return Registry(backs, dict(raw.get("aliases") or {}),
                        raw.get("default", ""), src)

    # 回退：老的单后端配置。合成一份只有一个后端的注册表，并把回退这件事写进 source。
    if LEGACY_WORKER_FILE.exists():
        raw = json.loads(LEGACY_WORKER_FILE.read_text(encoding="utf-8"))
        src = (f"{LEGACY_WORKER_FILE}（回退：跑 `devloop backends --migrate` "
               f"升级为多后端注册表）")
        b = Backend(name="deepseek", kind="api", model=raw.get("model", ""),
                    source=src, base_url=raw.get("base_url", ""),
                    auth_token=raw.get("auth_token", ""),
                    price_key=raw.get("model", "").split("[", 1)[0],
                    timeout_s=int(raw.get("timeout_s", 3000)))
        return Registry({"deepseek": b}, {"cheap": "deepseek"}, "deepseek", src)
        # ⚠️ 老配置只有这一个后端，所以它只能当默认——但那是**迁移路径**，
        #    不是推荐配置。跑 `devloop backends --migrate` 之后请把 default
        #    改成不直接花钱的那条（2026-07-28 裁决，见 BACKLOG G-57）。

    raise BackendError(
        f"找不到后端注册表。建 {REGISTRY_FILE}，内容形如：\n"
        + json.dumps({
            "default": "subagent-opus",
            "aliases": {"cheap": "deepseek", "premium": "subagent-opus"},
            "backends": {
                "deepseek": {"kind": "api", "base_url": "https://api.deepseek.com/anthropic",
                             "model": "deepseek-v4-pro[1m]", "auth_token": "sk-...",
                             "price_key": "deepseek-v4-pro"},
                "subagent-opus": {"kind": "subagent", "model": "claude-opus-5",
                                  "price_key": "claude-opus"},
            }}, ensure_ascii=False, indent=2))


def migrate() -> Path:
    """把老的单后端配置升级成注册表。⛔ 显式动作，不在 load() 里偷偷做。"""
    if REGISTRY_FILE.exists():
        raise BackendError(f"{REGISTRY_FILE} 已存在，不覆盖。要重来请先手工改名备份。")
    if not LEGACY_WORKER_FILE.exists():
        raise BackendError(f"没有 {LEGACY_WORKER_FILE} 可迁移。")
    raw = json.loads(LEGACY_WORKER_FILE.read_text(encoding="utf-8"))
    model = raw.get("model", "")
    doc = {
        "_comment": "DevLoop 工人后端注册表。⛔ 含密钥，永不进任何仓库。",
        "_schema": 1,
        "default": "subagent-opus",
        "aliases": {"cheap": "deepseek", "premium": "subagent-opus"},
        "backends": {
            "deepseek": {
                "kind": "api", "base_url": raw.get("base_url", ""), "model": model,
                "auth_token": raw.get("auth_token", ""),
                "price_key": model.split("[", 1)[0],
                "timeout_s": int(raw.get("timeout_s", 3000)),
                "note": f"由 {LEGACY_WORKER_FILE.name} 迁移而来",
            },
            "subagent-opus": {
                "kind": "subagent", "model": "claude-opus-5", "price_key": "claude-opus",
                "note": "⛔ 不是子进程。走交接协议：dispatch 出工单退出码 3 → 编排方起子代理 → devloop collect 收单",
            },
        },
    }
    REGISTRY_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return REGISTRY_FILE
