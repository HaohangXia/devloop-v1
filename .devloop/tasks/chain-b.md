# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立
git worktree 里干活，改动完成后会有一组闸自动裁决。

# 任务

⭐ **上一单已经建好了 `devloop/nightly.py`，里面有 `branch_lines(project)`。**
你的工作副本里**应该能看见它**。

⛔ **如果看不见它，立刻停下并在报告里写明「看不见上一单的产出」**——
那说明接力机制没生效，是本次要验的关键点，⛔ 不许自己重写一个顶上。

在 `devloop/nightly.py` 里再加一个函数：

```python
def report(project: Path) -> str:
    """一份「昨夜发生了什么」的简报，给第二天早上看。"""
```

内容包含：
- 待处理的隔离分支（调用**已有的** `branch_lines`，⛔ 不要重写）
- 台账里最近 24 小时的单数、其中几单合格
  （台账是 `.devloop/telemetry.jsonl`，每行一个 JSON；
   可用 `devloop/telemetry.py` 里已有的 `load(path, since_days=1)`）
- 没有任何记录时说清「没有记录」，⛔ 不要返回空串

**新建** `tests/test_nightly_report.py`，至少 2 条测试。

# 自己验

跑这两条，把**真实输出**贴进报告：
- `python -m pytest -q tests/test_nightly_report.py`
- `python -m pytest -q`

# 禁令

- ⛔ 不许改 `tests/` 下**已有的**任何文件——只许新建 `tests/test_nightly_report.py`
- ⛔ 不许新建 `tests/conftest.py`
- ⛔ 不许改 `.devloop/` 下任何文件
- ⛔ 不许 git commit / push
- ⛔ 判不了的事就说判不了，不许猜

# 报告格式

1. **`devloop/nightly.py` 里原本有没有 `branch_lines`？**（这一条最重要，必答）
2. 改了什么（贴新增代码）
3. 实测记录（两条命令的真实输出）
4. 不确定的地方
