# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立
git worktree 里干活，改动完成后会有一组闸自动裁决。

# 任务

`devloop/credentials.py` 是新加的模块，它检查 Claude 订阅令牌过期没有。

**给它补一个小工具函数并写测试**：

在 `devloop/credentials.py` 里加一个函数 `describe(st: CredStatus) -> str`，
把状态转成一行**给日志用的**极简摘要，形如：

    订阅令牌 有效 · 剩余 7.9h
    订阅令牌 已过期 · 需重登

要求：
- 不许改 `check()` 与 `CredStatus` 的现有行为
- **新建** `tests/test_credentials_describe.py`，写 2 条测试覆盖它（有效 / 已过期）

# 自己验

跑这两条，把真实输出贴进报告：
- `python -m pytest -q tests/test_credentials_describe.py` → 预期全绿
- `python -m pytest -q` → 预期全绿

# 禁令

- ⛔ 不许改 `tests/` 下**已有的**任何文件——只许新建 `tests/test_credentials_describe.py`
- ⛔ 不许新建 `tests/conftest.py`
- ⛔ 不许改 `.devloop/` 下任何文件
- ⛔ 不许 git commit / push——产出由编排方在跑完闸之后代为固化
- ⛔ 判不了的事就说判不了，不许猜

# 报告格式

1. 改了什么（贴新增代码）
2. 实测记录（两条命令的真实输出）
3. 不确定的地方
