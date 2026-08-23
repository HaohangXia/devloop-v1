"""跨进程边界的数据契约。

工人回执来自外部进程，字段缺失或类型漂移必须在入口处被挡住——
这是 Pydantic 在本项目里唯一但充分的用途。
"""

from __future__ import annotations

import re

from pathlib import Path

from pydantic import BaseModel, Field


class WorkerConfig(BaseModel):
    """工人的端点与模型。密钥单独持有，不进任何序列化输出。"""

    base_url: str
    model: str
    auth_token: str = Field(repr=False)
    timeout_s: int = 3000
    source: str = "unknown"  # 配置从哪来的，doctor 和日志要报它
    #  ⭐ 走你的 Claude 订阅（OAuth），而不是第三方付费端点。
    #     两者的执行形态**完全相反**，见 env() 与 dispatch.build_cmd。
    subscription: bool = False
    #  ⛔ **查价目表用的 key，和跑的模型不是一回事。** 缺省空 = 用模型名。
    #     订阅形态的 key 是 `__subscription__`（美元成本为 0），而模型名是
    #     `claude-opus-4-7`——拿模型名去查价目表查不到，记成「未知」。
    price_key: str = ""

    def pricing_key(self) -> str:
        """查价目表用哪个 key。

        ⛔ 别用 `self.model` 顶替：2026-07-29 端到端实跑抓到，派单路径一直
        把模型名当价目 key 传，于是订阅单的 `cost_usd_real` 记成 None。
        ⚠️ 而自动驾驶有一条防线是「算不出成本就停」——记 None 会让订阅
        无人值守**第一单就停机**，而无人值守正是这条路线存在的理由。
        """
        return self.price_key or self.model

    def env(self) -> dict[str, str]:
        """注入给工人进程的环境变量。

        ⛔ **订阅形态什么端点/密钥都不注入。** 注入 `ANTHROPIC_BASE_URL` 或
        `ANTHROPIC_AUTH_TOKEN` 会把请求打到第三方端点——那就不是「用订阅」了。
        """
        # ⛔ **必须显式置空 `CLAUDE_CODE_RETRY_WATCHDOG`，不能只是「不设置」。**
        #    dispatch 是 `{**os.environ, **cfg.env()}`——**全量继承**操作者环境。
        #    CLI 二进制：`goH(){return xH(process.env.CLAUDE_CODE_RETRY_WATCHDOG)}`，
        #    置位后 429 走**重试**路径而不是立刻返回。哪天有人为了 CI 设了它，
        #    撞额度的子进程就不再秒退，而是自己重试到 timeout_s（默认 50 分钟）
        #    才被超时打死。那时「秒退 → 当场停批」的前提没了，
        #    而且失败会被记成「工人超时」——⛔ 归因直接错。
        base = {"API_TIMEOUT_MS": str(self.timeout_s * 1000),
                "CLAUDE_CODE_RETRY_WATCHDOG": ""}
        if self.subscription:
            # ⚠️ 只给超时。⛔ 连 ANTHROPIC_MODEL 都不给：
            #    订阅走的是账号自己的默认模型，硬指定反而可能撞上「模型不存在」。
            return base
        return {
            **base,
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": self.auth_token,
            "ANTHROPIC_MODEL": self.model,
        }


class TaskSpec(BaseModel):
    """一份任务书。四段固定标题，见 SPEC.md §5.3。"""

    path: Path
    body: str

    @property
    def name(self) -> str:
        return self.path.stem

    @classmethod
    def load(cls, path: Path) -> TaskSpec:
        body = path.read_text(encoding="utf-8")
        # ⛔ **整行匹配，不是子串。** `"# 角色" in body` 是子串检查，
        #    实测四种写法里三种静默通过：`## 角色`（二级标题）、
        #    `# 角色与身份`（加后缀）、以及**正文里只是提到这五个字**。
        #    最后一种最坏：一份根本没有角色段的任务书照样放行，
        #    工人拿到的是一份缺段落的任务书——判据的维度错了（第三种假绿）。
        #    ⚠️ 实测 28 份真实任务书在严格判据下零份被判红，收紧是安全的。
        missing = [h for h in ("# 角色", "# 任务", "# 禁令")
                   if not re.search(rf"^{re.escape(h)}\s*$", body, re.M)]
        if missing:
            raise ValueError(f"{path.name} 缺少必需段落: {'、'.join(missing)}")
        return cls(path=path, body=body)

    @property
    def scope(self) -> list[str]:
        """`# 改动范围` 段里声明的路径/通配符。没写就返回空表。

        ⛔ **可选段**：28 份既有任务书都没有它，加了这个字段不许把它们判红。
        ⚠️ 但**写任务要并行时它是必需的** —— 判据与理由见
        `devloop/fanout.py` 模块 docstring：并行的两单若改到同一处就会
        合并冲突，而冲突要人来解，省下的墙钟连本带利还回去。
        """
        m = re.search(r"^# 改动范围\s*$(.*?)(?=^# |\Z)", self.body, re.M | re.S)
        if not m:
            return []
        return [ln.strip().lstrip("-").strip()
                for ln in m.group(1).splitlines()
                if ln.strip().startswith("-") and ln.strip().lstrip("-").strip()]


class Receipt(BaseModel):
    """`claude -p --output-format json` 的回执。只声明我们真正会读的字段。"""

    is_error: bool = False
    result: str = ""
    session_id: str = ""
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    usage: dict = Field(default_factory=dict)
    modelUsage: dict = Field(default_factory=dict)  # noqa: N815 — 上游字段名

    # ⚠️ 以下三个字段回答的是「**为什么**失败」，不是「有没有失败」。
    #    2026-07-26 实测：u11 单撞满 30 轮上限被截断，台账正确记了 worker_ok=False，
    #    但 error 与 failure_class 都是 None——**说了失败，没说为什么**，
    #    只能靠人去开原始回执 JSON 才知道是轮数不够而不是模型不行。
    #    这两件事在成本实验里必须区分：轮数不够是我的拆单错误，不是模型能力问题。
    # ⛔ **限流时 `subtype` 仍然是 "success"**（CLI 二进制原文核对过），
    #    带信号的是 `is_error` + 这个字段。没有它，撞额度就只能靠
    #    stream-json 的 rate_limit_event 一条路——而那条路的 rejected 形态
    #    本项目没有实测样本。留着它当第二层判据。
    api_error_status: int | None = None
    subtype: str = ""            # 例：error_max_turns / error_during_execution
    terminal_reason: str = ""    # 例：max_turns
    errors: list = Field(default_factory=list)

    @property
    def models_used(self) -> list[str]:
        return list(self.modelUsage)

    @property
    def why_failed(self) -> str:
        """一句人话的失败原因；没失败时返回空串。

        优先用上游给的结构化字段，实在没有才退回自由文本——
        `subtype` 是可枚举的，比 `errors` 里的英文句子更适合做统计分类。
        """
        if not self.is_error:
            return ""
        if self.subtype:
            return self.subtype
        if self.terminal_reason:
            return f"terminal:{self.terminal_reason}"
        if self.errors:
            return str(self.errors[0])[:120]
        return "未知（上游没给原因）"

    @property
    def cache_read_tokens(self) -> int:
        return int(self.usage.get("cache_read_input_tokens", 0))

    def ran_on(self, expected_model: str) -> bool:
        """回执必须核对实际模型——别假定（SPEC.md §5.1 行为约定）。"""
        return any(expected_model in m for m in self.models_used)
