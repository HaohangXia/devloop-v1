# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立
git worktree 里干活，改动完成后会有一组闸自动裁决。

# 任务

`devloop/quota.py` 里的 `RateLimit` 记录订阅额度状态，`resets_at` 是
**unix 秒**的恢复时刻。

给它加一个方法：

```python
def remaining_hours(self) -> float | None:
    """距额度恢复还有几小时。⚠️ 拿不到恢复时刻时返回 None，不是 0。"""
```

要求：
- `resets_at` 为 0（拿不到恢复时刻）→ 返回 `None`
  ⛔ 不许返回 0——「不知道」和「马上就恢复」是两回事，混在一起会让
  调用方以为可以立刻重试
- 恢复时刻已经过去 → 返回 `0.0`（不是负数）
- 其余情况返回剩余小时数（float）
- ⛔ 不许改 `RateLimit` 的现有字段与现有方法的行为
- **新建** `tests/test_quota_remaining.py`，至少 3 条测试覆盖上面三种情况

# 自己验

跑这两条，把**真实输出**贴进报告：
- `python -m pytest -q tests/test_quota_remaining.py`
- `python -m pytest -q`（预期全绿，且总数比之前多）

# 禁令

- ⛔ 不许改 `tests/` 下**已有的**任何文件——只许新建 `tests/test_quota_remaining.py`
- ⛔ 不许新建 `tests/conftest.py`
- ⛔ 不许改 `.devloop/` 下任何文件
- ⛔ 不许 git commit / push——产出由编排方跑完闸之后代为固化
- ⛔ 判不了的事就说判不了，不许猜

# 报告格式

1. 改了什么（贴新增代码）
2. 实测记录（两条命令的真实输出）
3. 不确定的地方
