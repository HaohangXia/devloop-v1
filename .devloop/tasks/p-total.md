# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立
git worktree 里干活，改动完成后会有一组闸自动裁决。

# 任务

`devloop/prune.py` 的 `report()` 会列出所有 `devloop/*` 隔离分支，
但**不告诉人这些分支一共改了多少东西**。无人值守跑一夜会攒出几十个分支，
那时最先想知道的就是「总共有多少产出待处理」。

在报告的第一行后面补一行合计，形如：

    3 个隔离分支：可清理 1 · 建议保留 2
    待处理产出：2 个分支合计改动 7 个文件

要求：
- 只统计 `safe_to_delete == False`（也就是「建议保留」）的那些分支——
  ⚠️ 可清理的那些要么已合并、要么没产出，算进去会虚报
- 每个分支改了几个文件，用 `git show --stat --format=""` 或
  `git diff --name-only <分支>^ <分支>` 之类的方式数
  ⛔ 数不出来的分支（比如没有父提交）**跳过并在那一行注明跳过了几个**，
  不许当成 0
- 没有待处理分支时不打这一行（别刷噪音）
- ⛔ 不许改 `Branch` 数据类的现有字段，也不许改 `scan()` 的返回类型
- **新建** `tests/test_prune_summary.py`，至少 2 条测试
  （有待处理分支 / 没有待处理分支）

# 自己验

跑这三条，把**真实输出**贴进报告：
- `python -m pytest -q tests/test_prune_summary.py`
- `python -m pytest -q`（预期全绿）
- `python -m devloop.cli prune --project .`（看真实报告长什么样）

# 禁令

- ⛔ 不许改 `tests/` 下**已有的**任何文件——只许新建 `tests/test_prune_summary.py`
- ⛔ 不许新建 `tests/conftest.py`
- ⛔ 不许改 `.devloop/` 下任何文件
- ⛔ 不许 git commit / push——产出由编排方跑完闸之后代为固化
- ⛔ 判不了的事就说判不了，不许猜

# 报告格式

1. 改了什么（贴新增代码）
2. 实测记录（三条命令的真实输出）
3. 不确定的地方
