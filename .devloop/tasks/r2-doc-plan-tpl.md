# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立 git worktree
里干活，改动完成后会有一组闸自动裁决（语法检查 + pytest 全套）。

⚠️ 本仓库的硬纪律：**注释解释「为什么」，不解释「做什么」**。
⛔ 每个数字后面都要跟「这个数依据什么」——⚠️ 拍脑袋的数要**明说是拍脑袋的**。

# 任务

`.devloop/plans/` 下有 4 份阶段计划（`demo` / `nightly` / `templates` / `wiring`），
**全是为具体场景手写的**，⛔ 没有一份可以拿来当模板。

**要做的事**：新建 `templates/plan.toml`——一份带完整注释的阶段计划模板。

字段以 `devloop/plan.py` 的解析代码为准（⛔ **以代码为准，不以现有 4 份为准**
——它们可能有过期字段）。至少覆盖：

- `[plan] version`
- `[stage]` 的 `id` / `goal` / `task_dir` / `base` / `chain`
- `[stage.budget]` 的 `total_usd` / `reserve_usd` / `max_dispatches` /
  `max_wall_min` / `watchdog_k` / `default_max_turns`
- `[stage.accept]` 的 `require_pass`
- 任务条目（`[[stage.task]]` 或代码里实际用的名字）

⭐ **每个预算数字旁边必须注明它的依据**，并写清「算不出成本时哪一条仍然能停住」
——⚠️ 那是兜底防线，参考 `.devloop/plans/demo.toml` 里 `max_dispatches` 的注释。

⭐ `require_pass` 旁边要写明：**SKIP 与 VOID 都不算通过**
（`devloop/gates.py::GateLine` 的注释解释了两者的区别）。

⛔ **不许改 `devloop/plan.py` 或现有的 4 份计划**，只新建模板。

# 自己验

按顺序跑，把**真实输出原样粘贴**进报告：

1. `git -C . status --porcelain`
   → 预期：**只有 `?? templates/plan.toml` 一行**

2. `python -c "import tomllib; d=tomllib.load(open('templates/plan.toml','rb')); print(sorted(d.keys()))"`
   → 预期：能解析，打印顶层键

3. `python -c "import tomllib; d=tomllib.load(open('templates/plan.toml','rb')); b=d['stage']['budget']; print('预算字段:', sorted(b))"`
   → 预期：六个预算字段都在

# 禁令

- ⛔ 只许新建 `templates/plan.toml` 一个文件。
- ⛔ 不许改 `devloop/` 下任何 .py，不许改 `.devloop/plans/` 下任何现有计划。
- ⛔ 不许 `git commit` / `push` / `add`。
- ⛔ **不许「顺手」修任何别的东西**——看到问题写进报告末尾的发现块，一个字都不许改。
- ⛔ 不许照抄现有计划里的数字**而不写依据**。⚠️ 没有依据的数字要标「拍脑袋的初值」。

# 改动范围

- templates/plan.toml
