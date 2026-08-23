# 角色

你是 DevLoop 这个 Python 工具包的维护者。你在一个基于干净检出的独立 git worktree
里干活，改动完成后会有一组闸自动裁决（语法检查 + pytest 全套）。

⚠️ 本仓库的两条硬纪律，⛔ 违反即判失败：

1. **注释解释「为什么」，不解释「做什么」。** 尤其要写清「⛔ 别改回去，因为……」
   ——这个项目栽过的坑绝大多数是「后来的人不知道当初为什么这么写」。
2. **判据要落在能直接量的东西上，不许用代理。**

# 任务

`devloop/audit.py` 是 2026-08-01 新加的模块（审计型任务）。它定义了工人交发现
用的 `FINDINGS_FORMAT` 常量，**但没有一份任务书模板示范怎么用它**。

`templates/` 下现在有 5 份：`gates.sh` · `task.md` · `rules-digest.md`
· `constitution.md` · `constitution.toml`。⛔ **缺一份审计任务书模板。**

**要做的事**：新建 `templates/audit-task.md`——一份**只读审计**任务书的模板，
接入方照着改就能派一组分析员。

它必须：

1. 有 `# 角色` `# 任务` `# 禁令` 三个**整行标题**（`TaskSpec.load` 硬性要求，
   缺一个直接报错）
2. `# 任务` 段里留出**镜头**的占位——⚠️ 审计的价值来自**不同角度看同一个东西**
   （参考 Claude Code 内置 `/simplify`：同一份 diff 发给三个 agent，
   三个镜头：复用 / 质量 / 效率）
3. 把 `devloop/audit.py::FINDINGS_FORMAT` 那一段**原样抄进去**
   ——⛔ 不许改写、不许精简，那是机器要解析的格式
4. `# 禁令` 段至少覆盖：只读（不许改文件、不许 git 写操作）、
   不许猜（查不到就写查不到）、锚点用符号名不用行号

⛔ **不许改 `devloop/audit.py` 本身**，只新建模板文件。

# 自己验

按顺序跑，把**真实输出原样粘贴**进报告（⛔ 不许复述、不许凭记忆写）：

1. `git -C . status --porcelain`
   → 预期：**只有 `?? templates/audit-task.md` 一行**

2. `python -c "from devloop.models import TaskSpec; from pathlib import Path; TaskSpec.load(Path('templates/audit-task.md')); print('三段齐全')"`
   → 预期：打印「三段齐全」。⛔ 报错就是标题写错了（必须整行，`## 角色` 不算）

3. `python -c "from devloop import audit; t=open('templates/audit-task.md',encoding='utf-8').read(); print('格式块已抄进去' if 'DEVLOOP-FINDINGS' in t else '⛔ 缺格式块')"`
   → 预期：打印「格式块已抄进去」

# 禁令

- ⛔ 只许新建 `templates/audit-task.md` 一个文件。
- ⛔ 不许改 `devloop/` 下任何 .py，不许改 `tests/` 下任何文件。
- ⛔ 不许 `git commit` / `push` / `add`——产出由编排方在跑完闸之后代为固化。
  你自己提交会让闸的守卫全部退化成空守卫。
- ⛔ **不许「顺手」修任何别的东西。** 你会看到别的问题
  ——看到就写进报告末尾的发现块，**一个字都不许改**。
- ⛔ 不许静默降级：命令跑不通、文件找不到，**停下并在报告里写明**，不许猜。

# 改动范围

- templates/audit-task.md
