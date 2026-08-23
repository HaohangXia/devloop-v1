# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立
git worktree 里干活，改动完成后会有一组闸自动裁决。

# 任务

**BACKLOG G-65**：`autopilot` 有一条防线叫「撞额度就停批」，
⛔ 而台账里 `failure_class == "ratelimit"` 出现过 **0 次** —— **这条防线从来没有真触发过**。
真跑到的只是「额度充足」那条解析路径。

⚠️ 而今晚这一整夜的无人值守，正好要靠它兜底。

**做一条能在「不真撞额度」的前提下验它的判据。**

具体：新建 `tests/test_quota_stop_fires.py`，至少 3 条测试：

1. **正面**：造一条 `rate_limit.blocked == True` 的派单结果，喂给
   `autopilot.check_limits`（或它真正读那个字段的那个函数），
   断言它**真的返回停批**，且停批理由里点明是额度、⛔ 不是「模型不行」。
2. **绿检**：同样的路径，`blocked == False` 时**必须不停**。
   ⛔ 缺了这条，上一条可以靠「一律停批」蒙混过关。
3. **归因**：`telemetry.record` 对 `blocked == True` 的那一单，
   `failure_class` 必须是额度那一类，⛔ 不许落进「model」。

⭐ 先去读 `devloop/quota.py` 与 `devloop/autopilot.py`，
**判据落在真调用上**，⛔ 不许只 grep 源码文本说「有这段代码所以没问题」。

⚠️ 如果你发现这条防线**实现上根本走不到**（比如订阅制下永远拿不到那个字段），
⭐ **那就是最有价值的发现** —— 在报告里写清楚「它为什么走不到」，
并把测试写成**会红的形态**（xfail 不算），⛔ 不许为了让它绿而放宽判据。

# 改动范围

- `tests/test_quota_stop_fires.py`（新建）
- 只有在确认防线真的接错了线时，才动 `devloop/autopilot.py` 或 `devloop/quota.py`

# 自己验

跑这两条，把**真实输出**贴进报告：
- `python -m pytest -q tests/test_quota_stop_fires.py`
- `python -m pytest -q`

⭐ 还要做一次**红检**：把你验的那条防线故意弄坏（比如让它无视 `blocked`），
确认你的测试**真的会红**；然后还原，确认回绿。两次输出都贴出来。

# 禁令

- ⛔ 不许改 `tests/` 下**已有的**任何文件——只许新建 `tests/test_quota_stop_fires.py`
- ⛔ 不许新建 `tests/conftest.py`
- ⛔ 不许改 `.devloop/` 下任何文件
- ⛔ 不许 git commit / push
- ⛔ 不许真去撞额度（不许发任何真实 API 请求）
- ⛔ 判不了的事就说判不了，不许猜

# 报告格式

1. 改了什么（贴新增代码）
2. 实测记录（上面每条命令的真实输出，含红检的红与还原后的绿）
3. 不确定的地方
