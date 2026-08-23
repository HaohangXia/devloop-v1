# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立
git worktree 里干活，改动完成后会有一组闸自动裁决。

# 任务

**BACKLOG G-91**：台账（`.devloop/telemetry.jsonl`）读的时候用的是
`path.read_text(encoding="utf-8", errors="replace")`（`devloop/telemetry.py::load`）。

⚠️ `errors="replace"` 会把非法字节换成 `�`。⛔ 而 `�` 是**合法字符**：
坏掉的那一行仍然是合法 JSON，`json.loads` 照样成功 ——
⇒ **字段已经损坏了，而 `load` 一个字都不会说。**

⭐ 这与同一个函数的开篇纪律直接打架，那里写着：
> ⛔ **坏行必须报出来，不许静默 skip。** 静默跳过等于让「已花多少」「派了几次」
> 偷偷变小，而那正是失控防线读的数。

⇒ **`errors="replace"` 是同一件事的另一种形态：不是跳过，是「悄悄改内容」。**

**做法**：
1. 新建 `tests/test_ledger_utf8.py`，至少 3 条：
   - **正面**：写一行含非法字节的台账 → `load(strict=True)` 必须**喊出来**
     （抛错或返回带告警），⛔ 不许静默返回一个被 `�` 污染的字典。
   - **绿检**：正常的中文台账（`语法`、`禁改清单` 这类）必须照读不误 ——
     ⛔ 缺了它，上一条可以靠「一律报错」蒙混过关。
   - **保守方向**：真出坏行时，宁可**报错**也不许让「已花多少 / 派了几次」变小。
2. 按测试改 `devloop/telemetry.py::load`。

⚠️ `errors="replace"` 当初是有理由的（注释写着「并发写坏时不能让读操作直接炸」）。
⭐ **别简单粗暴地删掉它** —— 要的是「**炸之前先说清哪一行坏了**」，
⛔ 不是「一坏就整个读不了」。两者的差别在报告里写清楚。

# 改动范围

- `tests/test_ledger_utf8.py`（新建）
- `devloop/telemetry.py`

# 自己验

- `python -m pytest -q tests/test_ledger_utf8.py`
- `python -m pytest -q`
- ⭐ **红检**：把 `load` 改回原样，确认新测试变红；还原，确认回绿。两次输出都贴。

# 禁令

- ⛔ 不许改 `tests/` 下**已有的**任何文件——只许新建 `tests/test_ledger_utf8.py`
- ⛔ 不许新建 `tests/conftest.py`
- ⛔ 不许改 `.devloop/` 下任何文件（含真实台账）
- ⛔ 不许 git commit / push
- ⛔ 判不了的事就说判不了，不许猜

# 报告格式

1. 改了什么（贴改动前后）
2. 实测记录（每条命令的真实输出，含红检）
3. 「报错」与「读不了」的差别，你是怎么处理的
4. 不确定的地方
