# DevLoop — 交付物与接口规范

> 建档：2026-07-26 ｜ 定位：**PLAN.md 说「做什么、为什么」，本文件说「做出来长什么样、怎么用」**
> 关联：[PLAN.md](PLAN.md)（总规划） · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)（故障手册）
> 状态：**规范先行**。本文件在动工前定义契约，避免「先写代码再反推规范」。
> ⚠️ **契约 ≠ 现状**：本文件描述的是**目标契约**，其中相当一部分尚未实现。**已实现的部分见下方「实现状态」一节**，未标注为已实现的一律视为设计意图。缺口台账在 [BACKLOG.md](BACKLOG.md)。
> **v2（2026-07-26）**：核心语言由 shell 改为 **Python**（依据见 PLAN.md 的技术栈裁决表）。旧版的 shell 契约已全部作废。

---

## 1. 这做出来的是个什么东西

**一句话：一个 Python 工具包 + 两个 MCP 服务 + 一个目录约定。**

它**不是**：有界面的产品、需要常驻的后台服务（MCP 服务按需由客户端拉起）、云端平台。

规模尚无可靠估算（此前写的「1500–2500 行」无推算依据，已删——零代码阶段给出精确区间是虚假精度）。**价值本来也不在代码量**，而在于把一套工作方式固化成了可执行的东西：

| 固化的是什么 | 落在哪 |
|---|---|
| 谁干什么活（贵模型判断 / **隔离的**工人执行 / 脚本裁决 / 人放行）<br>⛔ 2026-08-01 订正：**「便宜模型当工人」在事实上没有发生**——台账 25 单里 20 单的工人是 Opus（`claude-opus-4-7` ×14 + `claude-opus` ×6），只有 5 单走 DeepSeek；G-38 实测算上拆单成本，便宜路线还**输 5%**（质量 27% vs 84%）。⭐ 这套东西固化的是**隔离/验收/刹车/台账**，与省钱无关 | `dispatch.py` 的模型选择 + 任务书模板 |
| 「做完了」怎么判定（不靠模型自述） | `gates.py` 的退出码 |
| 花了多少、值不值 | `telemetry.py` 的台账 |
| 什么必须停下来问人 | `constitution.md` 清单 |
| 出事了怎么全停 | `halt.py` |
| 通道坏没坏怎么知道 | `doctor.py` |

**跨项目的实现方式**：工具本体不含任何项目知识，项目知识全住在各项目自己的 `.devloop\` 目录里。新项目接入 = 写一份 `.devloop\`，工具本体一行不改。

### 为什么是 Python 而不是 shell

三条理由，缺一不可：

1. **生态在 Python 这边**：要接的东西（LangGraph、MCP SDK、评测框架）没有一个提供 shell 接口，用 shell 就得为每一个手写胶水
2. **解决已知隐患**：Windows 上 `.sh` 由谁执行始终没有定论；改由 Python **显式指定解释器**调用，不再依赖系统文件关联
3. **是后续一切的前提**：LangGraph、MCP SDK、Langfuse、评测框架全在 Python 生态

---

## 1.5 实现状态（2026-07-30 · Phase 0/1/2/4/5/6 完成，Phase 7 核心完成，Phase 3 暂缓，Phase 8 未开始）

⛔ **本节是防止「照 SPEC 动手然后撞墙」的唯一屏障**——第 9 轮审计在 PLAN 里抓到过同类问题（把设计写成既成事实），此处不再重犯。

⚠️ **本表的 ✅ 只保证「这条路真跑过」，不保证「每条调用路径上都活着」。**
2026-07-29 与 07-30 两轮独立审计各抓到一批「标着完成但其实是死的」：
宪法 T5 三道在**自动驾驶**路径上一道都没接（8d9f6f4）、`[stage.accept] require_pass`
**一行代码都没读过**（8d9f6f4）、活工作区检查在**手动派单**路径上恒不执行（1659c49）。
三条的单测当时**全绿**——它们验的是「函数对不对」，不是「有没有被调用」。
⭐ 因此现在这类防线一律另配**源码级/spy 守卫**（AST 比对两个调用点的关键字参数集合；
从真 CLI 进去 spy 住实际收到的值），判据见 §5.8 末尾与 §5.9。

| 能力 | 状态 | 归属阶段 |
|---|---|---|
| `devloop dispatch`（单任务 / `--task-dir` 批量、只读权限、按进程注入 env、缓存友好前缀、回执模型核对） | ✅ **已实现并实测** | Phase 1 |
| `devloop stats`（台账汇总） | ✅ 已实现 | Phase 1 |
| `.devloop/` 发现、缺失即报错 | ✅ 已实现 | Phase 1 |
| 规则摘要拼装、缺失即拒派 | ✅ 已实现 | Phase 1 |

| 写操作自动 worktree 隔离 | ✅ **已实现**（`--tools implement` 自动生效，无需手动开关） | Phase 2 |
| `devloop gates`（含指纹校验、仓库外副本执行、`--commit` 走一次性 worktree） | ✅ **已实现，红绿双测通过** | Phase 2 |
| 派单完成后自动跑闸 | ✅ **已实现**（写操作强制，不可绕过） | Phase 2 |
| `--model cheap\|premium` 切换 | ✅ **已实现为 `--backend`**（`cheap`/`premium` 现在是注册表里的别名） | Phase 6 |
| `--out` 自定义回执目录 | ❌ 未实现 | 择机 |
| `devloop doctor` | ✅ **已实现**。⛔ 但它**不是无条件免费的**：带 `--project` 或 `--probe` 时**真花订阅额度**，需模式令牌且会留台账（0e2518e）。契约见 §5.1 | Phase 0 |
| 防篡改指纹校验**接入自动流程** | ✅ **已实现并实测**（派单前记录、跑闸时校验，伪造指纹返回退出码 2） | Phase 2 |
| `devloop halt`（急停，默认只列不杀） | ✅ 已实现，⚠️ **杀进程那一步是手工实测的，没有自动化红测**（真跑 `taskkill /T /F` 要先起真进程，平台强绑定）——其余逻辑有测试 | Phase 6 补 |
| `dispatch --detach` + `devloop status`（非阻塞派单） | ✅ **已实现并实测**（7 单前台干等 18.1 分钟 → 派出去 1 秒返回，活在后台照跑 8 分 47 秒）<br>⛔ `--detach` **不缩短执行时间**，只把等待从前台挪走 | Phase 6 |
| `devloop eval`（评测集 20 题 + `--compare` 回归比对） | ✅ **已实现并实测**。干净基线：pro 20/20 $0.2959 · flash 19/20 $0.1020。⛔ 诚实口径是「**20 题上没测出质量差别**（差 1 题，Fisher p=1.0），flash 便宜 2.9 倍」——不是「flash 质量掉了」 | Phase 5 |
| `devloop prune`（列出可清理的隔离分支，只列不删） | ✅ 已实现 | Phase 4 补 |
| `devloop backends`（后端注册表，`--backend` 换执行者） | ✅ **已实现并实测**（换后端 = 改配置一行） | Phase 6 |
| **订阅后端**（`kind = "subscription"`，走你的 Claude 订阅起子进程） | ✅ **已实现并实测**（106a35d）。⭐ 注册表**默认后端已改成它**；2026-07-29 台账 10 行全部走订阅、合格 7、`cost_usd_real` **9 行为 $0**（⚠️ 首行 00:34 是 `1ff4dbb` 修好之前留下的 `None`，**保留未清洗**——它正是 §5.1.1 引用的那个 bug 的证据）。形态差异见 §5.1.1 | Phase 7 补 |
| **撞订阅额度当场停整批**（`quota.py`） | 🔶 **判据与停批已实现，但停批本身从未真触发过**（a8d6932）。⛔ 诚实口径：台账 21 行里 `failure_class = "ratelimit"` 出现 **0 次**；带额度事件的 6 行**全是 `status: "allowed"`**——真跑到的只有解析路径，`rejected` 那条分支没有实测样本。退出码 3，见 §5.1 / §5.5 | Phase 7 补 |
| **阶段内接力**（`[stage] chain`，让后一单看得见前一单的产出） | ✅ **已实现并真跑验证**（64042ed / ecc3325，stage nightly 2/2 绿）；接力点已落盘、`--resume` 读得回来（0e2518e）。⛔ 默认关，代价见 §5.8 | Phase 7 补 |
| **模式触发机制**（三层：`UserPromptSubmit` 识别 → 令牌文件 → `PreToolUse` 强制） | ✅ **已实现并实测**（2ff956d / 93d3bdb / 1659c49 / 0e2518e）。`tools/test_mode_gate.py` 实跑全绿。⛔ 它**不是密封舱**，残余漏洞见 §5.9 | 工具层，不属任何 Phase |
| **台账并发写加锁 + 坏行必须抛**（`telemetry._append` / `load`） | ✅ **已实现并实测**（0e2518e）。⛔ 这条**推翻了 BACKLOG G-41**，见 §5.2.2 | Phase 4 补 |
| `doctor --project` / `--probe` 的花费留台账（`_record_smoke`） | ✅ **已实现**（0e2518e）。⛔ 此前它真花订阅额度却**一行账都不留**，见 §5.1 与 §5.9 | Phase 0 补 |
| 建 worktree 前查磁盘余量（`worktree.check_disk`） | ✅ **已实现**（0e2518e），⚠️ 但「⛔ 不写死常数」这句话到 2026-08-02 之前**一直是假的**：`if s > 0` 把「空壳(0.0)」「超上限量不出来(-1.0)」「合法小值」压成一档，前两种都退回写死的 200MB，而它唯一一次真生效（eco-ob 首单）就是错的（编 200，真实 251→gate_sync 后 361）。现改为：同项目已有壳取 **max**（空壳/量不出的滤掉）→ 都不行则按 **checkout** 实测（`git ls-files` + `synced_paths`）→ 仍不行则**只守 500MB 余量、不编数字**。⛔ 常数已删除 | Phase 2 补 |
| `devloop collect`（subagent 交接收单） | ✅ 已实现并实测 | Phase 6 |
| `--parallel N` 并行派单 | 🔶 已实现。⚠️ 「3 单 32s → 16s」测于 `a8d6932` 把它改成**按波提交**之前——⛔ 按波那条路径只在测试里跑过，没真跑 | Phase 6 |
| 检索 MCP 服务 | ⏸ **暂缓**（立项理由被实测削弱：**11 个工作单元**只用 Read/Grep 就跑完了对账；⚠️ 那批共 13 份回执，含重派——作论据时用 11，用 13 会把论据说得比实际强） | Phase 3 |
| 调度 MCP 服务 | ⏸ **拆出单列**——非阻塞已由 `--detach` 解决；MCP 是交付方式不是痛点（G-49） | 原 Phase 6 |
| 宪法执行层（`constitution.md` 散文 + `constitution.toml` 判据 + 项目外的锚） | ✅ **已实现并实测**（改闸→点名文件与指纹→rc=2；还原→rc=0）。⚠️ 但 T5 三道曾在**自动驾驶**路径上全是死的（8d9f6f4），活工作区那道又曾在**手动派单**路径上被 `ws_before = None` 覆盖成恒不执行（1659c49）——两处现由源码级/spy 守卫钉住 | Phase 7 |
| 阶段计划（依赖 DAG + 预算 + 点名验收） | ✅ **已实现**，全部校验在花第一分钱之前。⚠️ `[stage.accept] require_pass` 曾是**一行代码都没读过**的死配置（8d9f6f4），现由 `plan.effective_require_pass` 接上，见 §5.8 | Phase 7 |
| 自动驾驶循环（四条失控防线 + 台账重算恢复） | ✅ **已实现**。⚠️ 诚实口径：真跑 **3 次**——**1 次当场崩**（023726e，宪法快照被循环里的 `before` 覆盖成 int）、2 次 2/2 全绿。⭐ 崩那次看门狗（K=2）正确停机，那是四条失控防线里**第一条真正触发过的** | Phase 7 |
| LangGraph 状态机 | ⏸ **未接**——实测 34 个传递依赖 / 6.6 MB，且 SqliteSaver 需另装包。触发条件见 BACKLOG G-55 | Phase 7 |
| 隔离分支产出固化（跑闸后提交，见 §5.2.1） | ✅ **已实现并实测** | Phase 2 补 |
| 失败原因归因（`subtype`/`why_failed`/`truncated`） | ✅ **已实现** | Phase 4 补 |
| 闸的协议外退出码归一化为 2 | ✅ **已实现，红绿测试** | Phase 4 补 |
| 真实价目表接入台账（`prices.json` → `cost_usd_real`） | ✅ **已实现** | Phase 4 补 |
| 隔离分支清理策略 | ✅ **已实现**（`devloop prune`，只列不删） | Phase 4 补 |
| `devloop` 注册到 PATH | ❌ 未实现（G-04，当前只能 `python -m devloop.cli`） | 择机 |

**已实测数据**（首单，2026-07-26）：29s · 17 轮 · 缓存命中 7424 tok · 报告与答案卷逐条一致。
⚠️ 此处原写「$0.1252」——那是回执里的**合成价**（Claude Code 套 Opus 价目表算的，见 G-28），已删。
真实成本按 `prices.json` × 回执原始 token 重算，台账字段见 §5.2 后的台账字段表。

**当前计量**（2026-07-30 实跑核对）：

```text
pytest                     377 用例 0 失败（两天前是 291）
tools/test_mode_gate.py    46 条断言全绿（⚠️ 必须写成文件跑，见 §5.9.6）
2026-07-29 派单            合计 11 次、美元成本 $0.0082
                           · 台账 10 行，全部走订阅后端，cost_usd_real 9 行为 $0
                             ⚠️ 首行 00:34 是 1ff4dbb 修好前留下的 None，保留未清洗
                             ⛔ 另有 1 次 doctor 体检误走 deepseek 花了 $0.0082，
                                **当时没留台账**——那正是 0e2518e 补 _record_smoke 的理由
                           · 第 11 次是 doctor 体检**误走 deepseek** 花掉的 $0.0082
                             ⛔ 它当时**一行台账都没留**——那正是 0e2518e 补上
                                `_record_smoke` 要修的东西（见 §5.1 的 doctor 契约）
自动驾驶真跑               3 次：1 次当场崩、2 次 2/2 全绿
```

---

## 2. 交付物清单

### 工具本体（`C:\pg\_infra\devloop\`）—— 跨项目共用，写一次

| 路径 | 类型 | 产出阶段 | 状态 |
|---|---|---|---|
| `PLAN.md` · `SPEC.md` · `TROUBLESHOOTING.md` | 文档 | — | ✅ |
| `pyproject.toml` | 包定义（可 `pip install -e .`） | Phase 1 | ✅ |
| `devloop/cli.py` | 命令行入口 | Phase 1 | ✅ |
| `devloop/dispatch.py` | 派单：拼 prompt → 起工人 → 解析回执 | Phase 1 | ✅ |
| `devloop/telemetry.py` | 每单一行 JSONL 台账 | Phase 1 | ✅ |
| `devloop/doctor.py` | 通道自检 | Phase 0 | ✅ |
| `devloop/gates.py` | 闸执行器 | Phase 2 | ✅ |
| `devloop/halt.py` | 急停 | Phase 6 补 | ✅ |
| `devloop/jobs.py` | 后台作业调度 | Phase 6 | ✅ |
| `devloop/backends.py` | 工人后端注册表 | Phase 6 | ✅ |
| `devloop/handoff.py` | subagent 交接协议 | Phase 6 | ✅ |
| `devloop/credentials.py` | 订阅凭据自检：`check()` 便宜排除法 + `probe()` 真起子进程试（106a35d、6b321f9） | Phase 7 补 | ✅ |
| `devloop/quota.py` | 订阅额度：解析 `rate_limit_event` · 三层判据 · `HaltSignal` 停整批（a8d6932） | Phase 7 补 | ✅ |
| `devloop/nightly.py` | 昨夜简报：`branch_lines()` 列未合并的隔离分支 + `report()` 拼简报（ecc3325） | Phase 7 补 | ✅ |
| `devloop/blockfmt.py` | 报告格式解析（无转义分隔块） | Phase 4 补 | ✅ |
| `devloop/pricing.py` | 真实价目表换算 | Phase 4 补 | ✅ |
| `devloop/prune.py` | 隔离分支清理清单 | Phase 4 补 | ✅ |
| `devloop/naming.py` | 并发安全的时间戳 | Phase 4 补 | ✅ |
| `devloop/evals/` | 评测集（20 题）与跑分器 | Phase 5 | ✅ |
| `devloop/mcp_docs.py` | 检索 MCP 服务 | Phase 3 | 🔨 |
| `devloop/mcp_fleet.py` | 调度 MCP 服务 | Phase 6 | 🔨 |
| `devloop/constitution.py` | 宪法执行层（判据加载 · 锚 · 树内漂移 · T0 硬拒） | Phase 7 | ✅ |
| `devloop/plan.py` | 阶段计划（依赖 DAG · 预算 · 点名验收）。⭐ 2026-08-02 新增 `[stage] parallel`（默认 1）：⛔ 与 `chain` **互斥**（接力天然要求串行）；⚠️ `parallel > 1` 时**在 load 阶段**就用 `fanout.check` 查改动范围不重叠——那是唯一「还没花一分钱」的时刻 | Phase 7 | ✅ |
| `devloop/config.py` | 工人配置 + 项目 `.devloop/` 的发现。⛔ 找不到就报错退出，**绝不静默降级到默认值**；配置来自哪个文件必须能报出来 | Phase 0 | ✅ |
| `devloop/models.py` | 类型化契约：`TaskSpec`（三段整行校验 + 可选 `# 改动范围`）· `WorkerConfig` · `Receipt` | Phase 1 | ✅ |
| `devloop/worktree.py` | 隔离副本的建/查/清 + `check_disk`。⚠️ 基准用 `git stash create` 固化工作区（**不改工作区、不进 stash 栈、不进任何分支历史**） | Phase 2 | ✅ |
| `devloop/autopilot.py` | 自动驾驶循环（失控防线 · 台账重算恢复） | Phase 7 | ✅ |
| `devloop/records.py` | 不可恢复记录的指纹与归档（**T5 宪法第四条判据**）。⭐ 判据是**只追加**不是「哈希没变」——派单自己就会往台账追加一行，判成哈希会每一单都误报；⛔ 排除 `jobs`/`handoff`/`autopilot` 三个运行产物目录（2026-08-01 真跑抓到的误报） | 2026-08-01 | ✅ |
| `devloop/progress.py` | 实时心跳，走 **stderr**（不动 stdout 的成块输出）；闸那段用守护线程每 60 秒报数。⛔ 起因：eco-ob 首跑 **17 分钟控制台零输出**，而「挂一夜」的前提是看得见 | 2026-08-01 | ✅ |
| `devloop/audit.py` | 审计发现：解析 `<<<DEVLOOP-FINDINGS>>>` 块 / 按 id 去重落盘 / 转任务书。⭐ `verify_spec()` **要求推翻，不是要求核对**——「请核对这条对不对」拿到的永远是「对」 | 2026-08-01 | ✅ |
| `devloop/fanout.py` | 多派单的判据。只读默认并发 4、写默认串行——⚠️ 依据是成本差 **35 倍**（只读 7–30 秒不跑闸；写单 1043 秒、闸占 87%） | 2026-08-01 | ✅ |
| `templates/` | **七份** ✅：`gates.sh` · `task.md` · `audit-task.md` · `plan.toml` · `rules-digest.md` · `constitution.md` + `constitution.toml`。⚠️ 后五份都是**自动驾驶真跑的产出**（`task.md`/`rules-digest.md` 首次真跑；`audit-task.md`/`plan.toml` 2026-08-02 合入）。⛔ 此处曾两次与磁盘事实不符（写「四份」却列五项、另一处写「还差 gates.sh」而它有 197 行）——改这一格前先 `ls templates/` | 随各阶段 | ✅ |
| `tools/devloop-mode-hook.py` | 模式触发的**识别层**（`UserPromptSubmit` 钩子，纯正则）。契约见 §5.9 | 工具层 | ✅ |
| `tools/devloop-spend-gate.py` | 模式触发的**强制层**（`PreToolUse(Bash)` 钩子，`permissionDecision: "deny"`）。契约见 §5.9 | 工具层 | ✅ |
| `tools/test_mode_gate.py` | 上面两个钩子的自测。⚠️ **必须写成文件跑**——测试数据里字面包含要拦的命令串，在 Bash 里内联会被闸自己拦掉 | 工具层 | ✅ |
| `tools/probe_ledger_concurrency.py` | 台账并发写的复现脚本（推翻 G-41 的那份实测靠它） | 工具层 | ✅ |
| `_review/` | 各轮独立审查回执 | — | ✅ |

⚠️ **`tools/` 下这两个钩子不住在 devloop 包里，而是装进 `~/.claude/settings.json`**
（`PreToolUse(Bash)` 一条 + `UserPromptSubmit` 一条，指向本仓库的绝对路径）。
⛔ 连带后果：**换机器、或仓库挪了位置，整道闸会静默失效**——钩子是 fail-open 的（见 §5.9）。

### 机器级配置（`~\.claude\`）—— 不进 git

| 路径 | 说明 | 阶段 |
|---|---|---|
| `devloop-backends.json` | **工人后端注册表——决定默认后端是谁**。⚠️ 「默认后端是 subscription」是**本机这份文件**的属性，⛔ 不是工具的属性：`backends --migrate` 生成的模板默认仍是 `subagent-opus`，且**不含 subscription 后端**（`backends.py:215/237`）。换台机器要重新配 | Phase 6 |
| `worker-deepseek.json` | 工人端点/密钥/模型映射。**⛔ 含密钥，永不进任何仓库** | Phase 0 |
| `settings.json` | 现有文件，Phase 0 缩水其 env 块 | Phase 0 |

### 项目接入物（`<任意项目>\.devloop\`）—— 每项目一份

见 §4 目录规范。

---

## 3. 端到端：日常怎么用

### 场景 A — 派一单（最常用）

```bash
devloop dispatch --project C:/pg/eco-ob --task ./task.md
```

发生什么：读项目的 `.devloop\rules-digest.md` → 按**缓存友好的顺序**拼进任务书（稳定前缀在前，易变内容在后）→ 起一个 `claude -p` 工人 → 回执落到 `.devloop\reports\` 并追加一行台账。

⚠️ **「注入环境变量」和「加不加 `--bare`」按后端形态分叉**，不是所有单都一样——
注册表默认后端已改为 `subscription`（106a35d），它两样都不做。见 §5.1.1。

你看到：终端实时输出 + 结束时一行摘要（成本／耗时／模型／缓存命中／闸结果）。

### 场景 B — 并行派多单

```bash
devloop dispatch --project C:/pg/eco-ob --task-dir ./tasks/ --parallel 3

# 派出去就走，不等它跑完（7 单前台干等 18.1 分钟 → 派出去 1 秒返回）
# ⛔ 活还是要跑那么久（实测后台 8 分 47 秒，因为并发与后端也不同）——挪走的是「谁在等」
devloop dispatch --project C:/pg/eco-ob --task-dir ./tasks/ --parallel 3 --detach
devloop status --project C:/pg/eco-ob
```

### 场景 C — 验收一个阶段

工人跑完后，在 Desktop 会话里说「验收当前阶段」。贵模型会：跑 `devloop gates` 取客观事实 → 派独立工人做对抗审查 → 逐条对账验收线 → 给出**对账表 + 缺口清单 + 完成百分比** → 你决定放行。

### 场景 D — 出事了

```bash
devloop halt --project C:/pg/eco-ob            # 只列，不杀
devloop halt --project C:/pg/eco-ob --kill     # 确认后终止
```

⛔ **默认只列不杀**——跑着的作业里可能有已经花了钱、快要产出的单。
⚠️ 杀掉连同**子进程树**，只杀调度进程会留下还在烧钱的孤儿。
**已经花掉的钱不会退**，急停不撤销台账里那些跑完的单。

### 场景 E — 感觉哪里不对

```bash
devloop doctor
```

逐条报告各通道健康度。任何配置改动后都该跑一次。

### 场景 F — 看花了多少钱、质量有没有下滑

```bash
devloop stats --project C:/pg/eco-ob --since 7
```

---

## 4. `.devloop\` 目录规范（跨项目接入的核心）

> 📌 **`.devloop/` 应当被版本控制**（`reports/` · `telemetry.jsonl` · `jobs/` · `handoff/`
> 除外，那些是运行产物）。⚠️ 漏掉后两个的代价已经发生过：一次审查的探针在
> devloop 仓库自己的 `.devloop/` 下落了一个 handoff 目录，随后一次 `git add -A`
> 把它扫进了提交历史。
> `gates.sh` 与 `rules-digest.md` 定义了「这个项目怎么算通过」与「工人必须遵守什么」，与 CI 配置同级。
> **不跟踪的代价是实测过的**：eco-ob 未跟踪 `.devloop/`，导致禁改守卫在干净检出里恒为空、恒 PASS——守了个寂寞（BACKLOG G-17/G-23）。

> ⚠️ 此规范**必须先于首单存在**。审查曾指出：把规范推迟到最后会形成鸡生蛋——前期就要写 `.devloop\` 内容，没规范无从下笔。

```text
<项目根>\.devloop\
  gates.sh           【必选】验收闸。契约见 §5.2。保持 shell——它本来就是调项目自己的测试命令
  rules-digest.md    【必选】喂给工人的项目规则摘要。格式见 §5.4
  config.toml        【可选】三个真被读取的键：
                       · [gates] sync_ignored_paths ——
                     干净检出里需要从原项目补齐的、被 gitignore 的构建缓存。
                     （缺它会让工具链失败进而闸假失败，实测：eco-ob 缺 game/.godot
                     导致两次写任务被误判成工人失败，G-12。）
                       · [worker] backend —— 项目级默认执行者（后端注册表里的名字）。
                     ⚠️ 返回空表示「本项目没有意见」，⛔ 不在这里编默认值。
                       · [worker] env —— 传给工人进程的环境变量（2026-08-01 新增）。
                     ⛔ 起因：eco-ob 首跑时工人报 `godot: command not found`，
                        而闸跑得好好的——闸里写死了完整路径。
                        **工人和闸看到的不是同一个环境**，此前没人知道。
                     ⛔ 值必须是字符串（toml 写 `TIMEOUT = 30` 会在派单那一刻才炸，
                        那时钱已经花了）；⛔ `ANTHROPIC_*` / `CLAUDE_*` / `PATH` 一律拒绝
                        ——认证由后端注册表管，PATH 决定执行哪个二进制。
                     此处曾列的「默认模型/并发数/超时/文档索引目录」四项均未实现。
  constitution.md    【Phase 7 必选】给**人**读的红线表述。⛔ 任何脚本不得解析
  constitution.toml  【Phase 7 必选】给**脚本**读的判据表
                     ⛔ 判不了的条款必须在 [[unjudged]] 里逐条登记——
                        不登记的话，结论会印出一句没有尾巴的「无宪法命中」，
                        而那句话本身就是假绿
  plans/             【Phase 7】阶段计划（依赖 DAG + 预算 + 点名验收）。字段契约见 §5.8
  tasks/             【可选】任务书目录
  reports/           【自动生成】工人回执落盘处，建议 gitignore
  telemetry.jsonl    【自动生成】台账，建议 gitignore
  jobs/              【自动生成】--detach 的后台作业记录，建议 gitignore
                     ⚖️ `prune` 不管它，理由见下；确认作业已结束后手工删除
  handoff/           【自动生成】subagent 交接批次，建议 gitignore
```

**只有 `gates.sh` 和 `rules-digest.md` 是接入的最小集。** 其余按需。

**⚠️ 工具还会在项目外面建东西**（接手的人需要知道该清理什么）：

```text
<项目同级>\.devloop-worktrees\<项目名>-<任务名>-<时间戳>\   隔离 worktree
分支 devloop/<任务名>-<时间戳>                                 产出固化在这里，见 §5.2.1
```

分支会随派单累积，当前无自动清理（G-27）。丢弃单个：`git worktree remove --force <路径>` + `git branch -D <分支>`。

**⛔ 建 worktree 前必须查磁盘余量**（`worktree.check_disk`，0e2518e）：

```text
估法    ⛔ 不写死常数 —— 拿 .devloop-worktrees/ 下**已有的同类 worktree 实际多大**去估
        实测差两个数量级：devloop 约 4 MB/个，而 eco-ob 约 475 MB/个
        （gates.py 还会把 game/.godot 的 107 MB 复制进去）
        → max_dispatches = 6 的单个阶段约 2.8 GB
不够时  当场抛错，并告诉人去跑 prune。⛔ 不是「少跑一单」的问题：
        git 写提交对象写到一半没空间，仓库可能进入需要手工救的状态
判不出  ⛔ 放行 —— 检查本身不该挡住干活
```

⚠️ 这条只在**无人值守**下才要紧：手动跑一单没人会写满盘，跑一夜十几单才会。

**⛔ worktree 与主仓库共用 `.git/config`**（2026-07-29 审计抓到）：
在 worktree 里跑 `git config user.name` 改的是**主仓库的身份**，而且改完不会还原。
实测后果是维护者亲手写的 **24 个提交**全挂上了工人身份。
⛔ 因此提交身份一律用 `git -c user.email=… -c user.name=…` **每命令注入**，
不许写进任何 config。
⚠️ 连带：`prune` 原本靠**作者邮箱**判分支空不空，身份改回来之后那条判据永久失效
——改判**分支尖端提交**。

**发现规则**：`--project <路径>` 到该路径下找 `.devloop\`；找不到则**报错退出，不静默降级**（沿用「忘传参数就悄悄退回旧行为」的教训）。

---

## 5. 接口契约

### 5.1 `devloop` 命令行

> ⚠️ **本节及 §3 的所有示例都写作 `devloop ...`，但那个可执行文件当前不在 PATH 上**
> （`pyproject.toml` 有 `[project.scripts]` 入口，egg-info 里也有 entry_points，但没生成可执行文件——BACKLOG G-04）。
> **实际能跑的写法是 `python -m devloop.cli ...`**，其余参数完全一致。
> 照 §3 抄命令之前先做这个替换，否则第一条就 command not found。

> ⛔ **另有一道装在机器上的闸**：`dispatch` / `autopilot` 的**真跑形态**、
> 以及 `doctor --project` / `doctor --probe`，都需要用户先打模式代号拿到令牌，
> 否则 `PreToolUse` 会 `deny`，命令**物理跑不起来**。契约见 §5.9。
> ⚠️ 这道闸**不在 devloop 包里**，它拦的是「谁有权让它开始花钱」，不是「它怎么干活」。

```text
devloop dispatch --project <路径> (--task <文件> | --task-dir <目录>) [选项]

必选
  --project <路径>       项目根目录，须含 .devloop\
  --task <文件>          任务书（Markdown，格式见 §5.3）
  --task-dir <目录>      批量：目录下每个 .md 派一单

可选
  --backend <名字>       谁来干这批活。名字见 `devloop backends`；
                         不给则用项目 config.toml 的 [worker].backend，再不给用注册表 default
  --model <名字>         ⚠️ 已弃用，等价于 --backend（cheap/premium 现在是注册表里的别名）
  --tools <预设>         readonly | implement | full，默认 readonly
  --max-turns <N>        默认 120（没用掉的轮数不花钱，所以给高不给低——见 BACKLOG G-46）
  --parallel <N>         同时跑几单。⛔ **不给时默认值按 --tools 分化**：
                           readonly = 4（不建 worktree、不跑闸，实测单单 7–30 秒，几乎免费）
                           写操作   = 1（串行）
                         ⚠️ 写操作要并行时，`fanout.check` 会在**花第一分钱之前**拒绝，
                            退出码 2，理由是任务书没声明 `# 改动范围` 或范围重叠。
  --why-parallel         纯打印「什么时候该多派单」的判据然后退出（不花钱）
                         ⛔ 必须在读任何文件、连任何后端之前返回，
                            否则会因为 --project/--task 不存在而炸
  --require-pass <闸名>   可重复。点名这几道闸必须 PASS
                         ⛔ SKIP 与 VOID 都不算过。⚠️ 但**不是一律判 2**（2026-08-02 分岔）：
                           · 点名的全是 FAIL      → **1**（闸验了、判否，活没干好，重试有意义）
                           · 含 SKIP / VOID       → **2**（没验到，重试撞同一堵墙、烧同样的额度）
                           · 混合                 → **2**（保守：代价不对称）
                           · 点名一道**不存在**的闸 → **2**（闸与验收契约对不上）
                         ⭐ 「被点名」不该改变「活没干好」这个事实的性质——
                            实测改前：同一个 FAIL，点名了判 2、不点名判 1
  --detach               派出去就走，不等它跑完；用 `devloop status` 看进度
                         ⚠️ 退出码 0 只表示「起成功了」，不表示活干完了
                         ⚠️ 对 subagent 后端无意义，会明说被忽略
                         ⛔ 它靠**重建一条子进程命令行**（`_rebuild_argv`）实现，
                            所以**每加一个新开关都必须同时加进那份重建列表**，
                            否则会被静默吞掉。`--wait-for-reset` 就漏过一次
                            ——用户以为设了，实际没传下去（见 §5.8 末尾的守卫盲区）
  --wait-for-reset       撞订阅额度之后自己睡到 API 给的恢复时刻，醒来接着派
                         ⛔ 默认**不睡**：只打印剩几单、该等到几点，然后退 3。
                            睡几个小时的进程占着终端，那该是操作者自己的决定
                         ⛔ 拿不到精确恢复时刻的周上限**一律不自动等**（见 §5.5 额度事件）
  --out <目录>           ❌ 未实现（回执固定落 <项目>\.devloop\reports\）

⛔ 参数**不接受前缀缩写**（`allow_abbrev=False`）。理由不是洁癖：
   argparse 默认会把 `--det` 当成 `--detach`，而任何「按字面量过滤参数」的
   代码都会漏掉缩写形式——实测 `--det` 让后台作业无限自我重生（约 5 个作业/秒，
   一单活都不干）。让解析器直接拒绝，比让下游去猜可靠。

⛔ 曾写在这里但从未存在的两个参数（实测均报 unrecognized arguments）：
     --worktree · --gates / --no-gates
   真实语义由 --tools 决定，没有独立开关：
     --tools implement|full  → 自动开隔离 worktree，且强制跑闸，不可关
     --tools readonly        → 不开 worktree、不跑闸（台账里 gate_ok=null）

退出码
  0  全部工人成功且闸通过
  1  至少一个工人失败或闸未过
  2  工具自身错误（缺 .devloop\、找不到密钥、参数非法、后端配置错）
  3  **「还没完」** —— subagent 批次就绪待编排方 · 后台作业仍在跑或已死 ·
     急停列出了活作业但没杀 · **撞订阅额度停了整批**（a8d6932）
     ⛔ 2026-08-01 订正：判据是 `halt.tripped`，**不是「还有单没派」**。
        额度在**最后一波**撞上时 todo 已空，按剩余单数判会把「撞了额度」报成成功——
        而上游会据此判定「这批活干完了」。
     ⚠️ 绝不能用 0 顶替：0 的意思是「全都成功了」，脚本看到 0 会往下走，
     而此时活可能一个字都没干。这是自动驾驶最危险的失效模式。
     ⛔ **撞额度是 3，不是 1。** 1 的语义是「工人失败或闸未过」＝活没干好；
        额度耗尽是**根本没让它干**。记成 1，上游会据此判定「这批活失败了」
        而不是「该重来」——而重来正是唯一正确的动作。
     ⚠️ **3 压过 1**：既有真失败又有没派完的单时，「还没跑完」是更要紧的事实。

其他子命令
  devloop doctor [--project <路径>] [--probe]        通道自检
     ⛔ **带 `--project` 或 `--probe` 时它是花钱命令**，且花的是**订阅额度**：
        · `--project` → `_dispatch_smoke` 真派一单最小任务，走注册表默认后端
        · `--probe`   → `credentials.probe()` 真起一次子进程（实测一次最小调用
                        产生**七万级 cache_creation token**）
     ⛔ 两种形态都**必须留台账**（`doctor._record_smoke`，任务名 `doctor-smoke`，
        `tools = "readonly"`、`gate_ok = null`、`gate_detail` 里写明「真花额度，不是干跑」）。
        理由：三条失控防线（已花多少 / 派了几次 / 有没有算不出成本的）**全都只从台账读**，
        不记账等于对它们完全隐形——而 doctor 是「夜跑标准流程第 1 步」，
        于是夜跑会在一个**已经被吃掉的 5 小时窗口**上起跑。2026-07-30 之前它一行账都不留。
     ⚠️ 记账整段包在 try 里：**记账失败绝不能让体检本身报错**。
        体检的用途是告诉你哪里坏了，它自己因为记账失败而红是帮倒忙。
     ⛔ 因此这两种形态**需要模式令牌**（§5.9），裸 `doctor` 仍随时可跑。
     ⚠️ 判据别只看子命令名：`doctor` 不带这两个参数是**真不花钱**，
        一刀切拦下只会让人想办法关掉整套。
  devloop nightly --project <路径>                  ⭐ 早上接管的第一条命令
     一屏读完「昨夜发生了什么、今天要动什么」。⛔ 本项目**没有整体回档**这一步：
     主线永远不会被自动改动（`checkout`/`switch`/`push`/`reset` 全仓零次出现，
     由 `tests/test_no_git_write_verbs.py` 钉住），⭐ **不合并本身就是回档**。
     输出三块：
       · **整批一票否决位**（放最上面，它决定要不要逐支看）：
         闸自身故障 N 单 · 宪法命中 N 单 · 算不出成本 N 单 · 上一轮没走到收尾
       · 每支待处理分支一行：`分支 @ sha · 闸全绿|闸未过|未跑闸 · 改 N 个文件`
         ⚠️ 判定读不出来时印「判定读不出」，⛔ 不许猜成「未跑闸」
       · 最近 24 小时的派单数与合格数
     退出码：0 = 干净 · 3 = 要人看 · 2 = 输入坏了
     ⭐ 2026-08-02 之前每行只有名字和 sha——「五道闸全绿的产出」与
        「闸没过的垃圾」印出来一模一样，人必须逐支 `git show` 才分得开。
        而闸的判定**早就写在分支尖端提交信息里**（`commit_result` 拼的），
        `prune.scan()` 取到了却整个扔掉了。那就是 30 秒读不完的原因。

  devloop prune --project <路径>                    列出隔离分支
                [--archive <目录>]                  打包并**真验一遍**
                [--delete]                          删掉「已合并」的
                [--discard <分支>]…                 **点名弃掉**（含未合并的）
     ⛔ `--delete` / `--discard` **必须**同时给 `--archive`：删之前先
        `git bundle` 打包（只装分支尖端那一个提交，实测 **2KB**，全历史要 944KB）
        并跑 `git bundle verify` **真验一遍**，验过才准删；删之前还会**重验**。
        ⚠️ 「打了包」不等于「包是好的」——判据落在 `verify` 的退出码上。
     ⭐ 两个开关分开不是洁癖：`--delete` 作用于**机器判定**为已合并的集合，
        `--discard` 作用于**你点名**的那几支。⛔ 混成一个，等于让机器的判定
        去删人没看过的东西。
     ⚠️ 归档目录里有 `MANIFEST.md`：逐支写明尖端 sha、改了几个文件、验证结果，
        以及**取回命令**——包是二进制的，人要能不解包就看出里面装的是什么。

  devloop gates --project <路径> [--commit <sha>]   单独跑闸
  devloop halt --project <路径> [--kill]           急停（默认只列不杀）
  devloop status --project <路径> [--job ID] [--all] 看后台作业
  devloop eval [--backend N] [--compare]           跑评测集
  devloop constitution init|anchor|check --project <路径>
                                                   宪法：生成 / 登记基准 / 对账
                                                   ⚠️ anchor 只该由**人**执行——
                                                   能自愈的锚不是锚
  devloop autopilot --project <路径> --stage <名> [--dry-run] [--resume]
                                                   无人值守跑完一个阶段
                                                   ⛔ --dry-run 不花一分钱
                                                   ⚠️ --resume 且阶段开了 chain 时，
                                                      接力点从状态文件读回（§5.8）；
                                                      ⛔ 不读的话会静默丢回项目 HEAD
  devloop prune --project <路径>                    列出可清理的隔离分支
  devloop audit --project <路径>                    看审计发现（严重度排序 + 未复核标记）
       [--spec 发现ID] [--kind verify|fix] [--out 目录]
                         给一条发现生成任务书。verify = **要求推翻它**（默认）· fix = 去修它
                         ⛔ 扇出仍走 `dispatch --tools readonly --task-dir ... --parallel N`，
                            本命令只补「它们回来之后」的三件事：看得见 / 能被推翻 / 能变任务书
  devloop records --project <路径> [--archive 目录]  归档/核对不可恢复的记录
                         ⚠️ `.devloop/` 被 gitignore 整体忽略（`git ls-files` → 0），
                            里面的台账/回执/任务书**删了不可再生**
  devloop backends [--migrate]                      列出可用后端
  devloop collect --project <路径> [--status]       收 subagent 批次
  devloop stats --project <路径> [--since 7]       台账汇总
```

**行为约定**

- `--tools readonly` 时**不得**传 `Write/Edit`；工具层拦截，不依赖工人自觉
- 回执必须核对 `modelUsage` 字段确认实际模型，不符即判失败（**别假定**）
- **缓存友好的 prompt 组装顺序**：`系统前缀 → rules-digest → 任务书模板骨架 → 具体任务`。⛔ 前缀内不得含时间戳、随机 ID、会话号——会打碎缓存

### 5.1.1 后端形态（`--backend` 选中的是一整套，不只是模型名）

`kind` 有四个合法取值（`backends.py::KINDS`）：`api` · `anthropic` · `subagent` · `subscription`。
⭐ `subscription` 是 2026-07-29 加的（106a35d），**注册表默认后端已改成它**。

| | `api` / `anthropic` | `subagent` | `subscription` |
|---|---|---|---|
| 怎么跑 | 子进程 `claude -p` | ⛔ **不能是子进程**，走交接协议（§5.7） | 子进程 `claude -p` |
| `--bare` | ⛔ **必须加** | — | ⛔ **绝不加** |
| 注入 `ANTHROPIC_*` | 注入 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` | — | ⛔ **一个都不注入**，连 `ANTHROPIC_MODEL` 都不给（订阅走账号自己的默认模型，硬指定反而可能撞「模型不存在」） |
| 认证走哪条 | 环境变量里的 key | 编排方自己 | OAuth（`~/.claude/.credentials.json`） |
| 成本口径 | `prices.json` × 回执 token | 按注册表 `price_key` 查 | **恒为 `0.0`，不是「未知」**，`price_key = __subscription__` |

⛔ **`--bare` 对两条路线的作用正好相反，这是全表最容易搞反的一格**：

- `--bare` 的用途是**跳过 OAuth 与 keychain**，好让环境变量里的 key 生效。
  付费端点**必须**带它——不加的话，磁盘上那份 OAuth 凭据会**劫持**掉环境变量里的 key，
  报 401（TROUBLESHOOTING 故障 4）。
- 而订阅形态要的**恰恰是 OAuth**。加了 `--bare` 就等于把订阅关掉，
  再掉回去读环境变量里那个（订阅形态压根没注入的）key。

两条都有测试钉住（`tests/test_subscription_backend.py`）。

⛔ **订阅的美元成本是 `0`，不是「未知」。** 记 `None` 会让自动驾驶**第一单就停机**
——它有一条「算不出成本就停」的防线（「算不出」被当成「花了 0 元」会让预算永不耗尽）。
⚠️ 但 `0 ≠ 免费`：额度是真实的稀缺资源，`price_source` 里写明了。
⚠️ 连带后果：订阅后端上，`check_limits` 里两条按预算判的防线**结构性恒假**，
`total_usd` 那个数字完全不起作用。
⭐ 剩下**还活着**的是三条：`max_dispatches`（管「烧了多少」）· `max_wall_min`（管时间）
· `watchdog_k`（管停滞），加上撞额度即停。
⛔ 此处原来只写「真正的防线是 max_dispatches 与撞额度即停」——**漏了后两条**。
⚠️ 把三条说成一条，会让人以为改坏了 `max_dispatches` 就全没防线了。
准确说法是：**`max_dispatches` 是「烧了多少」这个维度上唯一还活着的那条。**

⚠️ **价目 key 与模型名是两个字段，不许合并**（1ff4dbb，端到端真跑抓到、单测抓不到）：
`telemetry.record(..., model=cfg.model)` 曾把模型名 `claude-opus-4-7` 当价目 key 传，
而订阅的 key 是 `__subscription__` → `cost_usd_real` 记成 `None`。
函数是对的，**路径是断的**——六种假绿里的第二种（实现了但没接线）。
现在 `model` 记「谁干的」，`price_key` 记「按什么价算」，由 `WorkerConfig.pricing_key()` 给出。

#### ⛔ 订阅凭据：能不能跑，**只有真试一次才知道**

同一个判据被推翻了三次（106a35d → 6b321f9），三次都是同一个病——**拿间接读数替代真实行为**：

| 版本 | 判据 | 结果 |
|---|---|---|
| ① | `.credentials.json` 的 **mtime** | ❌ 令牌可能在内存里刷新 |
| ② | 问 `claude auth status` | ❌ 它只看凭据文件在不在，**不看过没过期**。实测同一时刻：`auth status` 报 `loggedIn: true, subscriptionType: max`，而 `claude -p` 报 `401 OAuth access token has expired` |
| ③ | 读文件里的 `expiresAt` | ❌ **还是错**（见下） |

2026-07-29 18:46 实测第三次翻车：

```text
测试前 expiresAt   08:22（10 小时前就过期了）
claude -p "回答一个字：好"   →  is_error false，正常返回
测试后 expiresAt   次日 02:46
```

⭐ **CLI 用 `refreshToken` 自动续了期，全程不需要人。** 据此拒派会拦下**本来能跑通**的活
——**比不检查更坏**，因为它以「凭据过期」的名义停机，而真实原因是判据错了。
⚠️ 换字段解决不了：凭据文件里**根本没有 refreshToken 的过期时间**
（字段只有 `accessToken` / `refreshToken` / `expiresAt` / `scopes` / `subscriptionType` / `rateLimitTier`）。

因此 `credentials.py` 的定位是：

- `check()` = **便宜的排除法**，只回答「**肯定**跑不了」（没文件 / 文件坏 / 过期**且连 refreshToken 都没有**）。
  ⛔ 它**不**回答「一定能跑」。⚠️「访问令牌过期」不再是拒派理由；`expiring_soon` 保留但降级为提示。
- `probe()` = **真起一次子进程试**。唯一可靠，但花额度（实测一次最小调用产生七万级 cache_creation token），
  所以只给 `doctor --probe` 用。
- ⭐ 两者**不一致时必须单列报出来**——分歧被压下去，下次就得靠一整批 401 才能发现。

⭐ **对无人值守的意义**：一次登录只管 8 小时，但每次派单都是新子进程、都会按需自动续期，
所以 **8 小时这条线不再是跑一夜的障碍**。⚠️ 前提是 refreshToken 本身没失效——两个月不用是会失效的。

### 5.2 `gates.sh`（项目提供，`gates.py` 调用）

```text
调用：  bash <项目>/.devloop/gates.sh <被检查的工作目录>
注入的环境变量（**属于协议的一部分，写 gates.sh 时必须知道**）：
  DEVLOOP_CLEAN_CHECKOUT=1|0   1=被检查目录是干净检出，改动守卫此时才有意义
                               0=工作区本来就有未提交改动，守卫无法区分「工人改的」
                                 与「在途改动」，此时守卫必须 SKIP 而不是硬判
  ⛔ 不读这个变量的守卫，会在脏工作区里产生误伤（实测三条），
     或在干净检出里恒 PASS（空守卫，G-17）——两种都等于没守。

解释器  由 Python 显式指定（Git Bash，显式排除 System32 下的 WSL 入口），
        不依赖系统文件关联
工作目录 ⚠️ 不作任何保证 —— run_gates 调 subprocess.run 时不传 cwd，
        闸继承的是调用者当时的目录。闸脚本不得依赖 cwd，
        一切路径只能从 $1 推。
执行位置 gates.sh 被 shutil.copy2 拷到系统临时目录后执行，且只拷这一个文件。
        两条推论：$0 / BASH_SOURCE 不指向 .devloop/，不能靠它定位项目；
                 不能 source 相邻的 .devloop/lib.sh 之类。

退出码
  0  全闸通过
  1  有闸未通过（正常的"不合格"）
  2  闸自身故障（环境缺失、命令找不到、脚本崩溃）
     ⚠️ 1 与 2 必须区分，否则「闸坏了」会被当成「活没干好」
  ⛔ **两种自相矛盾一律归 2**（2026-08-02 补，两半对称）：
        · `exit 0` 却印了 FAIL —— 契约写着「0 = 全都验过且都过了」。
          ⚠️ 改前**不点名时会放行**（passed=True 而摘要写着「1 未过」），
          坏产出直接流下去。病因几乎总是 `bad()` 在子 shell 或管道里
          `fail=1` 没传回主 shell。
        · `exit 1` 却一行 FAIL 都没印 —— 说有闸没过却没说是哪道。
          ⚠️ 照 1 处理会让 `retries` 拿一个没有内容的失败反复烧额度。
  ⚠️ **`SKIP`/`VOID` + `exit 0` 不在此列，那是设计如此**：`skip()`/`void()`
        故意不置 `fail`，SKIP/VOID 的含义是「**没验**」而不是「验了没过」，
        与「没有东西失败」自洽。⭐ 放不放行由 `require_pass` 决定。

  其他  非 0/1/2 的退出码（脚本语法错、set -u 撞未赋值变量的 127、
        被信号杀死的 128+N）**一律由 run_gates 归一化为 2**，
        原始退出码写进 stderr。⚠️ 这条不是可选的容错：
        此前直接透传，127 会落进「质量不合格但零道闸失败」，
        正是上面那句明令必须区分的两件事被违反（G-40）。

stdout  逐条结果，每行：<PASS|FAIL|SKIP|VOID>\t<闸名>\t<一句话说明>

        四档的分工：
          PASS = 验过且通过        FAIL = 验过且不通过
          SKIP = **本来能验，这次被开关跳过**（`DEVLOOP_SKIP_TESTS=1` 那种）
          VOID = ⛔ **我这一道本来就没有可验的东西**
        ⚠️ SKIP 与 VOID 对「能不能放行」含义相同（都不算通过），
           但对**排查**含义完全不同：SKIP 要查谁开了开关，VOID 要查为什么这个项目里它是空的。
        ⛔ VOID 的判据只能在闸自己身上——**只有它知道有没有东西可验**。
           外面用启发式（「detail 里没数字就算空过」）会误伤
           `基线守卫: ref_*.json 未被改动` 那种真验过的。
        ⚠️ 起因（2026-08-01）：eco-ob 的 `禁改清单` 在 worktree 内无 `.devloop/` 时
           报 **PASS 而验了 0 条**，而它被写进了 `--require-pass`
           ——**点名一道恒过的闸等于没点名**。
        这是给贵模型读的结构化输入，格式必须稳定
stderr  诊断细节，人读
```

**⛔ 三道防篡改**（详见 PLAN.md）：从仓库外只读副本执行 · 执行前校验哈希 · 工人改动触及 `.devloop\` 即判死。

**验证对象：两条路径不同，必须分清**

| 路径 | 验什么 | 为什么 |
|---|---|---|
| **派单路径**（`dispatch --tools implement`） | worktree 的**工作区** | 三道改动守卫靠 `git status --porcelain` 识别工人改了什么。**先提交会让工作区变干净，三道守卫全部退化成空守卫**（G-20 同型）。所以这条路径必须验工作区，不是取舍，是硬约束 |
| **`gates --commit <sha>`** | 那个**提交** | 工人可能留下未暂存文件，提交能过所有本地检查却引用了没进提交的文件。**工作目录会撒谎，提交不会。** |

⚠️ **本节此前写的是一句全称句「`gates.py` 把待验提交检出到一次性 worktree 再跑闸」——那与派单主路径的实现相反。**
两条路径各有一个缺陷面，当前接受派单路径这一侧的代价，记在 BACKLOG G-10，未关闭。

### 5.2.1 产出固化（跑闸**之后**，顺序不可交换）

派单流程在跑完闸后，把工人产出提交到该单自己的隔离分支：

```text
时序（硬约束）  建 worktree → 派工人 → 跑闸 → 提交到隔离分支 → 记台账
分支名          devloop/<任务名>-<YYYYmmdd-HHMMSS>
worktree 位置   <项目同级>/.devloop-worktrees/<项目名>-<任务名>-<时间戳>
提交身份        devloop-worker[<任务名>] <worker@devloop.local>   ⛔ 不继承用户 git 身份
闸没过          照样提交，提交信息里写明「闸未过」
工人没改东西    不提交，不造空提交
主线            一个字节不动；合回主线仍须用户逐单批准
```

**⚠️ 为什么必须提交**：此前完全不提交，产出只活在 worktree 的未提交工作区，而派单收尾却打印
「分支 … 保留供你审查」——那个分支上一个提交都没有。照着同一行给出的删除命令一执行，产出永久消失。
一次五闸全绿、答案卷全过的产出就是这么丢的（G-26）。

**⚠️ 为什么必须在跑闸之后**：见上表——先提交，守卫全空。

#### 台账字段（`.devloop/telemetry.jsonl`，每单一行）

| 字段 | 含义 |
|---|---|
| `ts` · `task` · `model` · `tools` | 何时、哪单、哪个模型、什么权限 |
| **`worker_ok`** | 工人自身跑完没报错 |
| **`gate_ok`** | 闸判定；`null` = 没跑闸（只读任务） |
| **`ok`** | `worker_ok and gate_ok is not False` |
| **`gate_code`** | 闸的**结构化**判定：`0` 全过 · `1` 有未过（活没干好）· `2` 闸自身故障 · `null` 没跑闸。⭐ 2026-08-02 新增：`autopilot.escalation` 的失败归类原来靠对 `error` 做**子串匹配**，实测会被**测试名劫持**——本仓 `gates.sh` 把 pytest 的失败节点名原样放进 FAIL 的 detail，于是一个叫 `test_闸自身故障要归到闸自身故障` 的测试挂掉，就会让「活没干好」被归成「闸自身故障 → 先修环境」。⚠️ 而五份计划全都点名 pytest，那是自动驾驶的主路径 |
| `failure_class` | 事后**人工**标注：`model` / `taskspec` / `tooling` / `gate`。当前无自动写入口。⭐ 例外：`ratelimit` 由 `quota.py` **自动写**（a8d6932）——原有四类里没有对的那个 |
| `error` | 为什么失败（编排侧问题优先，其次工人自报的 `why_failed`） |
| `truncated` | 是否撞轮数上限 —— **拆单太大，不是模型不行**，成本实验里必须分开 |
| `cost_usd_synthetic` | 回执原样带来的合成价（Opus 价目表套第三方 token，高 19.6–27 倍）**⛔ 不可对外引用** |
| `cost_usd_real` | 按 `prices.json` 重算的估值；价目表里没有该模型时为 `null`，**此时呈现「未知」，不许拿合成价顶替** |
| `price_source` | 这一单按哪张表算的，换表时能分辨新旧数据 |
| `gate_detail` | 闸的一句话说明。⚠️ `doctor` 的探针单在这里写明「真花额度，不是干跑」——事后翻账时要能一眼分出「体检花的」和「干活花的」 |
| `quota_status` · `quota_kind` · `quota_resets_at` · `quota_utilization` | 离开时的订阅额度状态（来自 `rate_limit_event`）。**每单都记，不只是撞墙那次**。⚠️ 没拿到额度事件时四者都是 `null`，**不造默认值**——「不知道额度」与「额度充足」不是一回事（付费端点根本拿不到这些字段）|
| `input_tokens` · `output_tokens` · `cache_read_tokens` | 原始 token —— 唯一可信的计量 |
| `duration_s` · `turns` · `models_used` · `report` | ⚠️ `duration_s` **只是工人那一段**，⛔ 别拿它估一单要多久（实测工人 93s 而整单 1043s） |
| `setup_s` · `worker_s` · `gate_s` | **分段墙钟**：建 worktree+同步缓存 / 工人 / 闸。⚠️ 缺项记 `null`，与「量到 0 秒」区分（只读单压根不跑闸） |

⭐ **`worker_ok` / `gate_ok` / `ok` 三分是本项目的核心口径**：「工人跑完了」≠「这单合格」，
而「这单不合格」≠「模型不行」。混在一个字段里，就永远回答不了「便宜模型够不够用」。

### 5.2.2 台账的读写契约（⛔ 不是「日志」，是失控防线唯一的数据源）

⛔ 先把定位说死：自动驾驶的每一条防线——**花了多少 / 派了几次 / 哪些绿了**——
**全都只从台账读**。所以台账的完整性不是「日志质量问题」，
丢一行 = 防线读到偏小的数，坏一行 = 防线全部归零。

```text
写侧 telemetry._append(path, line)
  ⛔ 进程内 threading.Lock  —— 盖 --parallel（同进程多线程）
  ⛔ 跨进程文件锁 <台账>.lock（O_CREAT|O_EXCL，最多等约 1 秒）
                          —— 盖 --detach 的后台作业与前台命令并存
  ⛔ 写完 flush + os.fsync
  ⚠️ 拿不到文件锁**也照写**：丢一行日志远好过丢一单活

读侧 telemetry.load(path, since_days=None, strict=True)
  ⛔ 逐行 try；**有坏行必须抛 LedgerCorrupted**，⛔ 不许静默跳过
     ——静默跳过等于让「已花多少」「派了几次」偷偷变小，而那正是防线读的数
  ⛔ 报错必须点名文件、说清是台账、给出坏行行号
     （原实现是一句裸列表推导，一行坏行直接抛 JSONDecodeError，
      上层 except ValueError 把它显示成「输入错误：Unterminated string」
      ——不提文件名、不说是台账，人根本不知道该去修什么）
  ⚠️ 用 errors="replace" 读：并发写坏时会出现非法 UTF-8，不能让读操作直接炸
  strict=False 只给排查用：跳过坏行，但**仍然把同一段话打到 stderr**
```

⛔ **这一节推翻了 BACKLOG G-41 的结论。** G-41 记的是「16 线程并发写 →
16 行齐全、0 行无法解析 ✅ 未损坏」。2026-07-30 用 `tools/probe_ledger_concurrency.py` 复跑：
那是**一次**试验，而且用的是**等长短行**。换成真实形态（8 线程 × 80 行、**不等长**——
带 `error` 文本的行比没有的长好几倍）：

```text
40 次试验  →  40 次都丢行且出坏行
其中一次写出非法 UTF-8 字节，连 read_text 都抛 UnicodeDecodeError
加锁后复跑：三组配置各 40 次，零丢行零坏行
```

**⛔ 花钱之后那段不许裸奔**（`dispatch_one` → `_finish()`，0e2518e）：

```text
硬约束   `subprocess.run` **返回之后**的一切（mkdir / 解析 / 落盘）都要包在 try 里，
         出错也**必须返回**一个带「⚠️ 钱已花掉但回执处理失败」的结果，
         ⛔ 不许把异常抛给上层
理由     上层判「钱花没花」的依据是 `if res is not None`。异常一抛，
         这个判断永远读不到 → 钱花了，台账零行
实测复现 把 `.devloop/reports` 造成一个文件（mkdir 必炸）：
         输出只有一行 `✗ t: FileExistsError`，**台账 0 行、一个字都没提钱花过**
⛔ 连带  例外路径补记台账时**不许新造 `DispatchResult`**（会丢掉 `rate_limit`），
         用 `dataclasses.replace`。⚠️ 同一个坑主路径上已经踩过一次
```

⚠️ **别把这条和 §5.5 的「坏行一律跳过」搞混——两者方向相反，而且都是对的**：

| | 事件流（`quota.parse_stream`） | 台账（`telemetry.load`） |
|---|---|---|
| 坏行怎么办 | ⛔ **跳过** | ⛔ **抛 `LedgerCorrupted`** |
| 为什么 | 坏行是**别人**（CLI）写的，是常态（实测见过 `Warning: no stdin data received in 3s`）。一行坏行让整单失败 = 把**已经花掉的额度**扔了 | 坏行是**我们自己**写的，说明写侧出了问题。跳过它 = 让失控防线读到一个偏小、且看起来正常的数 |

---

### 5.3 任务书格式（Markdown）

⚠️ `models.py::TaskSpec.load` **只校验三段**：`# 角色` · `# 任务` · `# 禁令`。

⭐ **可选第四段 `# 改动范围`**（2026-08-01 新增）：一行一条路径或 glob。

```markdown
# 改动范围

- game/src/sim/sim_world.gd
- templates/*.md
```

⛔ **它是可选段**——28 份既有任务书都没有它，加了这个字段**不许把它们判红**。
⚠️ **但写操作要并行时它是必需的**：`fanout.check` 会因为缺它而**拒绝派单**（退出码 2）。
理由：并行的两单若改到同一处就会合并冲突，而**冲突要人来解，省下的墙钟连本带利还回去**。
⚠️ **串行时不查**——两单改同一个文件是合法的（后一单基于前一单的产出），那正是 `chain` 的用法。
`# 报告格式` 与 `# 自己验` 缺失**不报错**——所以硬约束要写进被校验的 `# 禁令`，
写在 `# 报告格式` 里的要求没有任何机制兜底。

> ⛔ **派单前置：任务书必须经独立审查实地核对。**
> 2026-07-26 实测：4 份由模型设计的任务书，**全部**被独立审查判为不可用——把脏工作区数字当 HEAD、把未跟踪文件当仓库文件、引用错误的提交号、注释位置描述与事实相反。
> **凭记忆或推断写任务书的失败率接近 100%。** 审查必须**实地 grep/read 核对**任务书里的每一个文件名、符号名、行号、数字，而不是通读一遍觉得合理。
> 附带收益：该轮审查还在派单前查出了两个**工具本身**的缺陷（空守卫、非确定性判据）。

> **数据模型用 Pydantic**（Phase 1 采用，依据见 [PLAN.md 技术栈裁决表](PLAN.md)）：任务书解析结果、工人回执、闸结果三者均定义为 Pydantic 模型，而非裸 dict。理由是类型化契约在跨进程边界上最有价值——回执来自外部进程，字段缺失或类型漂移必须在入口处被挡住。

```markdown
# 角色
（一句话：你是什么身份）

# 任务
（要干什么。可机器验证的验收标准写在这里）

# 禁令
（明确不许做什么。只读任务必须含「不许创建/修改/删除任何文件」）

# 报告格式
（要求工人如何组织输出）
```

`dispatch.py` 会自动在最前面插入 `rules-digest.md`——**因为 `--bare` 让工人读不到项目 `CLAUDE.md`，这是它获知项目规则的唯一渠道。**
⚠️ **`--bare` 只加在 `api` / `anthropic` 后端上**（见 §5.1.1）；订阅后端不加它，所以上面那条理由在订阅路径上不成立。
但插入行为**不分后端，一律执行**——`rules-digest.md` 是契约的一部分，不是对 `--bare` 的补救。

### 5.4 `rules-digest.md` 格式

```markdown
# <项目名> · 工人须知

## 铁律（违反即判失败）
- 一条一行，每条须**可判定**，不要写"注意代码质量"这类没法验的话

## 禁改清单
- 路径或 glob，一行一条

## 常用命令
- 跑测试 / 构建 / 检查 的确切命令行
```

维护者是人。判定标准：**只写工人干活时真会用到、且违反会造成实际损害的**。它越短工人越可能真的遵守。
**⛔ 保护级别与 `gates.sh` 同级**：工人不得修改（宪法 A 类）。

### 5.5 工人回执与台账

派单固定使用 `claude -p --output-format stream-json --verbose`（a8d6932）。
⚠️ **回执因此不是「一个 JSON 文档」，而是「多行事件流」**——一行一个事件，
落盘的也是**完整事件流**（不只是 `result`）。

#### 为什么从 `json` 换成 `stream-json`

⭐ 为的是那条 `rate_limit_event`。实测（2026-07-29）**每一跑**都会吐一条，
不只是撞墙那次——于是能在撞墙**之前**就看见自己离墙多近：

```json
{"type":"rate_limit_event","rate_limit_info":{
  "status":"allowed","resetsAt":1785332400,"rateLimitType":"five_hour",
  "overageStatus":"rejected","isUsingOverage":false}}
```

`resetsAt = 1785332400` → 2026-07-29 23:40:00。`--output-format json`（单结果）拿不到这些。
⚠️ `result` 事件本身的字段与 json 版一致，已实测核对——所以下面那张字段表照旧成立。
⚠️ 回执文件名仍是 `<时间戳>_<任务名>.json`，但**里面装的是多行事件流，不是单个 JSON 文档**——
拿 `json.load` 去读它会当场炸。事后排查「当时额度是什么状态」只能靠这份完整流。

#### ⛔ 三条硬约束（少一条整套就不成立）

| 约束 | 不遵守会怎样（实测） |
|---|---|
| ⛔ `--verbose` 是**必需项**，不是调试开关 | 不加直接报「When using --print, --output-format=stream-json requires --verbose」，**退出码 1、零输出** |
| ⛔ `stdin` 必须给 `DEVNULL` | 不给则继承编排方的 stdin：打印 `Warning: no stdin data received in 3s`，**每单白等 3 秒**；而在没有终端的场合（计划任务、后台派单）继承一个永不结束的 stdin 会**直接把整批挂死** |
| ⛔ `CLAUDE_CODE_RETRY_WATCHDOG` 必须**显式置空**（不能只是「不设置」） | 派单是 `{**os.environ, **cfg.env()}` ——**全量继承**操作者环境。CLI 二进制 `goH(){return xH(process.env.CLAUDE_CODE_RETRY_WATCHDOG)}`：置位后 429 走重试路径而不是立刻返回，撞额度的子进程会重试到 50 分钟超时才被打死。于是「秒退→停批」的前提没了，失败还会被记成「工人超时」 |

#### ⚠️ 解析约定（`quota.parse_stream`）

- ⚠️ **坏行不许打死整个解析。** 事件流里混进非 JSON 行是常态（实测见过
  `Warning: no stdin data received in 3s`）。**一行坏行让整单失败，等于把已经花掉的额度扔了。**
  判据：非 `{` 开头的行、`json.loads` 失败的行、解析出来不是 dict 的，一律**跳过**。
  ⛔ **这条只对事件流成立，对台账正好相反**（台账坏行必须抛 `LedgerCorrupted`）。
  两者的判据不许统一，理由见 §5.2.2 末尾那张对照表。
- `result` 事件取**最后一条**（后来的覆盖先前的）。
- `rate_limit_event` 取**最后一条**：一次派单可能吐多条（额度在跑的过程中变化），
  最后一条才是离开时的真实状态，⚠️ 用第一条会**低估**用量。
- ⛔ 没有额度事件时返回 `None`，**不许造默认值**——那会让「不知道额度」和「额度充足」变成同一件事。
- 整个流里**没有 `result` 事件**时判失败，原始输出连同 stderr 落到 `<回执>.raw.txt`。

#### 额度事件（`rate_limit_event.rate_limit_info`）

CLI 二进制里的 zod schema（权威取值集合，2026-07-29 从 `claude.exe` 抠出）：

```text
status:        "allowed" | "allowed_warning" | "rejected"
rateLimitType: "five_hour" | "seven_day" | "seven_day_opus"
               | "seven_day_sonnet" | "overage"
resetsAt:      number  ← HTTP 头 anthropic-ratelimit-unified-reset
```

⚠️ `resetsAt` 是精确的 **unix 秒**；**凭据那边的 `expiresAt` 是毫秒**，⛔ 别混。

⚠️ **两种上限性质完全不同，不说清人会白等一周**（官方口径）：
`five_hour` 是滚动 5 小时会话窗口、`seven_day` 是**固定时间**每周重置，两者**跨模型共享**——换模型没用；
而 `seven_day_opus` / `seven_day_sonnet` 是**模型专属**的——**换个模型就能接着干**。

⛔ **不用「等 5 小时」这种估算。** 「撞的那一刻 + 5 小时」会系统性多等，
多等的量 = 窗口已流逝的部分，**误差上界是一整个窗口**。用实测那个窗口（18:40 起、23:40 止）：
若 23:30 撞墙，按猜法睡到 04:30，而实际 23:40 就恢复——**白等 4 小时 50 分**。
官方从没定义过窗口起算点（查了 6 个官方页面），所以这个公式也修不了。但不需要修：`resetsAt` 直接给。

**三层判据**（⛔ 一层不够）：

| 层 | 判据 | 给出什么 |
|---|---|---|
| A | `rate_limit_event.status == "rejected"` | 唯一带**种类和精确恢复时刻**的 |
| B | 回执 `is_error` + `api_error_status == 429` | 兜底 |
| C | 文案前缀白名单 | 只定性，不定时 |

⚠️ A 层的 `rejected` 形态**本项目没有实测样本**（不许为取样去真撞一次），所以 B/C 不是冗余。
⛔ **不许拿 `subtype` 当判据**：CLI 二进制原文显示，限流回执的 `subtype` **仍然是 `"success"`**。
为此给 `Receipt` 补了 `api_error_status`。

⛔ **反判据比正判据更容易漏**：
「Server is temporarily limiting requests (not your usage limit)」是**服务端容量限流**，
与套餐额度无关。把它当额度用尽会让整批停机、还睡几个小时，而它几分钟就过去了。
（文案表与反判据都是从 `claude.exe` 里抠出来的原串，常量表 `Gs9`。）

**撞了额度之后**：

```text
停批机制    quota.HaltSignal ——⛔ 不抛异常：_run_unit 里那个 except Exception
            会就地吞掉它（那个兜底本身是对的，一单失败不该连坐）
并行        改成**按波提交**，一波跑完查一次信号
            ⛔ 原来一次性 submit 进池，排队的单停不掉
退出码      3（还没跑完），见 §5.1
台账        failure_class = "ratelimit"，⛔ 直接定，不等人工标——
            原有四类里没有对的那个，而 model 是最顺手也最错的选择
            （那会把额度问题算进「工人这一档够不够用」的账上）
自动续跑    只有 --wait-for-reset 才自己睡
            ⛔ 周上限拿不到精确时刻时**一律不自动等**：官方明写周上限是固定
               时间重置，拿 5 小时去估会一路撞墙，每次醒来再撞一次，
               而每次都真花额度。判不出种类时同样不自动等——猜错的代价不对称
```

**必读字段**（`result` 事件）：

| 字段 | 用途 |
|---|---|
| `is_error` | 成败 |
| `result` | 工人正文输出 |
| `total_cost_usd` · `duration_ms` | 记账 |
| `modelUsage` | **必须核对**——确认真跑在预期模型上 |
| `usage.cache_read_input_tokens` | **缓存命中监控**——为 0 说明前缀分层失效 |
| — | *（Phase 5 起经 LiteLLM 抽象层派单，以支持多提供商切换与模型横向对比；见 [PLAN.md 技术栈裁决表](PLAN.md)）* |
| `subtype` | 失败的**类别**（可枚举，适合统计）。`error_max_turns` = 撞轮数上限 = **拆单太大，不是模型不行**。⛔ **不可用于判额度**——限流回执的 `subtype` 仍是 `"success"` |
| `api_error_status` | 上游 HTTP 状态。`429` 是额度判据的 B 层（a8d6932 新增） |
| `terminal_reason` | 终止原因，如 `max_turns` |
| `errors` | 上游给的自由文本原因数组 |
| `why_failed`（派生） | 一句人话：优先 `subtype` → `terminal:<reason>` → `errors[0]` → 「未知（上游没给原因）」。没失败时为空串 |
| `num_turns` | ⚠️ **它不是 `--max-turns` 限的那个量**。`num_turns` 数的是工具结果回传条数（user 角色消息），`--max-turns` 限的是**去重后的 assistant 轮数**，实测比值 1.0–2.8 倍且不固定（同一批单里 `num_turns=71` 的成功、`num_turns=31` 的撞了上限）。判断轮数余量要去数会话记录里的 assistant 消息，见 BACKLOG G-35 |

`telemetry.py` 每单追加一行 JSONL：任务 ID、项目、模型、成本、耗时、轮数、缓存命中、闸结果、重试次数。**从第一单就开始记**——趋势要靠攒。
⛔ 追加**必须走 `telemetry._append` 的双层锁 + fsync**，读**必须走 `telemetry.load`**（坏行抛）。契约见 §5.2.2。

### 5.6 检索 MCP 服务（`mcp_docs.py`，Phase 3）

暴露给工人的工具：

| 工具 | 职责 |
|---|---|
| `search_docs(query, top_k)` | 混合检索（关键词 + 向量），**每条结果强制带文件路径 + 行号** |
| `read_doc_section(path, anchor)` | 读取指定文档段落 |

**⛔ 红线**：它是**查询工具**，不改变项目章程的权威分层规则（「是什么」看代码链、「为什么」看设计文档链）。工人的结论仍须附出处，检索结果不等于结论。

### 5.7 调度 MCP 服务（`mcp_fleet.py`，Phase 6）

| 工具 | 职责 |
|---|---|
| `dispatch_task` | 派单，**立即返回 task_id，非阻塞** |
| `get_task_status` · `get_task_report` · `list_tasks` | 查询 |
| `run_gates` · `fleet_health` · `halt_fleet` | 操作 |

价值：~~非阻塞派单~~（⚠️ **这条已被 `--detach` 解决**，2026-07-27 落地——它曾是本项做立项的主要理由，现在不是了）· 权限收窄（桌面端不再需要无限 Bash 权限）· 跨客户端可用。

⚖️ 因此触发条件改为「**需要一个可对外展示的自写 MCP Server 时**」，而不是「被卡住时」——G-49 的结论是
MCP 是**交付方式**，不是痛点。痛点已经没了，剩下的两条价值都不紧急。

### 5.8 阶段计划（`.devloop\plans\<阶段>.toml`，`plan.py` 解析）

`devloop autopilot --stage <名>` 读的就是它。⛔ **全部校验在花第一分钱之前完成。**

```toml
[plan]
version = 1              # ⛔ 只认 1。版本不同意味着语义可能变了，人确认之前拒绝执行

[stage]
id       = 'nightly'
goal     = '一句话说清这个阶段要拿到什么'
task_dir = '.devloop/tasks'   # ⚠️ 相对**项目根**，不是相对计划文件
base     = 'HEAD'             # 每单的工作副本从哪起
chain    = true               # ⭐ 阶段内接力，⛔ 默认 false，语义见下

[stage.budget]                # ⛔ 前四项一个都不能省，且不许是非正数
total_usd         = 0.50
reserve_usd       = 0.10      # 派下一单前必须还剩这么多
max_dispatches    = 6         # ⭐ 绝对上限：成本算不出来时它仍然能停住
max_wall_min      = 40
default_max_turns = 120       # 可选
watchdog_k        = 2         # 可选，默认 3：连续几轮没进展就停

[stage.accept]
require_pass = ['pytest']     # 阶段级点名：这几道闸必须 PASS

[[task]]
id      = 'chain-a'
tools   = 'implement'
retries = 2
  [task.accept]
  require_pass = ['测试守卫', 'pytest']   # 任务级点名
  # why = '...'               # 没有机器判据时**必须**写明理由

[[task]]
id      = 'chain-b'
needs   = ['chain-a']         # ⚠️ 只保证先后顺序；要「看得见产出」得开 chain
tools   = 'implement'
retries = 2
  [task.accept]
  require_pass = ['测试守卫', 'pytest']
```

⛔ **加载期就会拒绝的几种写法**（全在花第一分钱之前）：`plan.version != 1` ·
`[stage]` 缺 `id`/`goal`/`task_dir`/`base` · 预算缺项或非正数 ·
任务**既没有** `accept.require_pass` **也没有** `accept.why`（「忘了写验收标准」
和「这单确实没法机检」在文件里长得一模一样，前者是漏洞后者是已知代价，必须分开）·
重复 id · 悬空 `needs` · 依赖成环 · `task_dir` 下缺任务书 ·
**只读单点名闸**（见下）。

#### `[stage] chain` —— 阶段内接力（G-60，64042ed）

**它解决的问题**：每单的 worktree 原本都从**同一个 base** 建（base 在循环外算一次）。
于是 `needs` 只保证**先后顺序**，后一单**看不见**前一单的产出。这样一份自然的计划跑不了：

```text
1. 写模块  →  2. 给模块写测试（needs 1）  →  3. 修问题（needs 2）
```

第 2 单打开工作区会发现模块不存在——三单各自从同一张白纸开始。
⚠️ 于是能跑的只剩「一批互不相干的小改动」——而那恰恰是**最不需要**自动驾驶的活，
它根本没有需要编排的依赖。

**语义**：某单**整体合格且产出已固化**之后，把下一单的起点推到那条提交上。

```text
推进条件   ⛔ 两个都不能少：ok（闸、宪法都过了）+ sha（产出真固化了）
           没过闸就推进接力点 = 让后面所有单建立在没验过的产出之上，
           而闸的全部意义就是别让没验过的东西往下传
锚是什么   ⚠️ **不是**「拿 worktree 的 HEAD 当锚」——那个是要防的，
           攻击成功时锚会跟着动。推到的是**编排方**在闸全绿之后写的那条提交
           （commit_result，提交前还断言过 HEAD == 该分支）。
           ⛔ 工人自己的提交永远成不了接力点
接力点存哪 ⛔ **必须落盘**：`Run.chain_head` 写进阶段状态文件，
           且**推进的同一时刻立刻 save**（进程随时可能被杀）
`--resume` ⛔ `--resume` + `chain = true` 时必须调 `resume_chain_head()` 把它读回来，
           读到才覆盖 base（读不到就照常算 base，不静默假装接上了）
```

⛔ **接力点只活在内存里 = 等于没有**（0e2518e 修）：`base` 每次进程启动都重算，
而接力只在循环体内推进。于是 `--resume` 之后下游单会**静默地**从项目 HEAD 建工作副本，
而前几单的产出只在**未合并的隔离分支**上、树里根本没有——下游单要么必然失败，
要么工人「自己重写一个顶上」，**产出静默分叉**。
⚠️ 而崩溃恢复正是无人值守的立项理由，这条漏洞恰好打在它的立项理由上。

⛔ **代价，也是它必须默认关的理由**：接力把几单**绑在一起**——后一单的分支含着前一单的提交。
合并时合最后一个就全拿到，**但不能只否掉前一单**。
而「合回主线要人逐单批」是宪法条款，**这个代价必须由人明确接受，不能由默认值替他接受**。

**真跑验证**（stage `nightly`，2/2 绿，ecc3325）：

- chain-b 的提交**父提交就是 chain-a** —— 接力点真的推进了
- chain-b 的 diff 只有 **+30 行**到 `nightly.py`（**追加**，不是重写）
- `report()` 里 `pending = branch_lines(project)` —— 真的调用了上一单的函数
- ⚠️ chain-b 的任务书里明写「看不见上一单的产出就停下报告，⛔ 不许自己重写一个顶上」，
  它**没有**触发那条——说明它确实看见了。不接力的话这个计划必然失败，这就是判据。

#### `[stage.accept] require_pass` 与任务级取**并集**

```text
生效值 = plan.effective_require_pass(stage, task) = 阶段级 ∪ 任务级
⚠️ 只读单（tools = readonly）**不并**，直接返回空
--dry-run 展示的也是生效值（否则演练确认的和真跑的不是一回事）
```

⚠️ **只读单为什么不并**：只读单不建 worktree、**不跑闸**，闸结果恒为空。
把阶段级点名并进去，就又造出一个空守卫——正是这个函数要消灭的那种东西。
（同一条不变量在加载期也有：只读单**显式**点名闸直接判配置错误，两条出路是改 `tools`
或改用 `accept.why`。）

⛔ **它曾经是一行代码都没读过的死配置**（8d9f6f4 独立审计抓到）：
`plan.py` 定义并解析了 `[stage.accept] require_pass`，`demo.toml` 里配着它、
旁边还写着「⛔ SKIP 不算过（G-53）」，然后**全仓没有任何一处读 `sp.require_pass`**——
只有任务级的被传下去。
⚠️ 这是 G-53 那种**空守卫**的教科书复刻，而且**就写在引用 G-53 的文件里**：
配置摆在那儿、`--dry-run` 还会把闸名念给人听，**主动确认一个永不生效的守卫**。
`templates.toml` 侥幸没出事，只因为它在任务级又抄了一遍同样四道闸。

#### ⛔ 自动驾驶路径必须与手动派单路径接同一套防线

`autopilot` 撞订阅额度时**当场停整个阶段**，`Stop.why = "订阅额度耗尽"`、
`failure_class = "ratelimit"`，退出码 **3**。
⚠️ 这条在订阅后端上格外要紧：订阅的美元成本恒为 `0.0`，于是按预算判的两条防线
（「花超了」「算不出成本」）**结构性恒假**——`total_usd` 那个数字完全不起作用。
剩下真正能停住它的只有 `max_dispatches` 与撞额度即停。

⛔ **这两条曾经在自动驾驶路径上全是死的**（8d9f6f4 独立审计抓到）：
`cmd_dispatch` 调 `_run_unit` 传了 `before` / `ws_before` / `halt`，
`cmd_autopilot` 只传 `con` / `base`。于是自动驾驶模式下，
**宪法 T5 三道（既有引用 / 受保护文件 / 活工作区）恒不执行**，
**撞额度恒不停批**，会继续一单一单撞同一堵墙。
⚠️ 而自动驾驶正是**无人值守**那条路——人不在旁边看着的那个模式，
恰恰是唯一没有这些防线的模式；手动派单反而全都有。
守卫落在**源码上**：AST 解析比对两个 `_run_unit` 调用点的关键字参数集合，不许分叉
（要靠行为判据，就得撞真额度或真违宪）。

#### ⚠️ 守卫本身的盲区：AST 只验「传没传」，不验「传的是不是真东西」

⛔ 上面那条 AST 守卫**挡不住第二种失效**——参数名在、值是假的。2026-07-30 真发生过：

```text
cmd_dispatch 在宪法前置里采了  ws_before = constitution.workspace_state(...)
后来为修一个 NameError，又在**后面**补了一句  ws_before = None
→ 快照被覆盖，「活工作区」这道宪法检查在**手动派单**路径上恒不执行
```

⚠️ 两条守着它的测试**当时全绿**：一条**自己**传 `ws_before=`（验的是 `_run_unit` 内部），
另一条只用 AST 检查**关键字名在不在**。⛔ 两条都是判据维度错了。
补法：`test_派单路径传下去的必须是真快照而不是None` —— 从**真 CLI** 进去、
spy 住 `_run_unit` 看它**实际收到的值**，并做过红检（放回 bug → 断言炸）。

⛔ **同型盲区还有一处**：`--detach` 曾静默吞掉 `--wait-for-reset`
（`_rebuild_argv` 重建子进程命令行时漏了这个开关），
⚠️ 而那条不变量测试的**参数化列表里没有这个新参数**，于是它对这个开关**恒绿**。
⭐ 结论写进契约：**参数化的不变量测试，漏项时是恒绿的——守卫有盲区比没守卫更坏**，
因为它会让人停止怀疑。加新参数时必须同时加进那份列表。

---

### 5.9 模式触发机制（`tools/devloop-mode-hook.py` + `tools/devloop-spend-gate.py`）

> ⚠️ 这一节写的是**工具层**的契约，不属于任何 Phase：它管的不是 devloop 怎么干活，
> 而是**谁有权让它开始花钱**。

**它替换掉的方案**，以及为什么：原方案是「你用自然语言说，我来判断走哪条」。
用户否掉了它，原话：「不行 我觉得这个判断方法也不好，我觉得需要一些特殊的字段来进行触发」。
⚠️ 实据在他那边：2026-07-29 当天模型在这个项目上**判断错了四次**，四次都是真跑才抓到的。
⭐ **靠理解意图这一环本身就是不可靠的那一环。**

⛔ 而且光挑个符号没用：**如果最后还是模型读那个符号来决定，那只是把「靠 Claude 判断」
换了层皮，而且更坏**——用户以为自己按了开关，就不再复核了。

#### 5.9.1 三层，以及判断落在谁手里

```text
你打的字面量 ──正则──▶ 令牌文件 ──PreToolUse──▶ 命令放行 / deny
```

| 层 | 载体 | 判断落在谁手里 | 职责 |
|---|---|---|---|
| **识别层** | `UserPromptSubmit` 钩子（`tools/devloop-mode-hook.py`） | ⭐ **纯正则，模型完全不参与** | 在**模型看到这条消息之前**跑；认代号 → 落盘令牌 → 用 `systemMessage` 回显 → 用 `additionalContext` 往模型上下文注入一条明确指令 |
| **强制层** | `PreToolUse(Bash)` 钩子（`tools/devloop-spend-gate.py`） | ⭐ **字符串匹配 + 读一个文件，零语义判断** | `permissionDecision: "deny"`。没令牌，花额度的命令**物理跑不起来** |
| **解释层** | `CLAUDE.md` | 模型 | ⛔ **只解释，绝不承担闸**——它是提示，会被注意力衰减和上下文压缩稀释 |

⛔ **回显由脚本打，不由 Claude 写。** Claude 会判错，也就会写错。
⛔ **没有回显的开关等于没有开关**——用户必须看得见自己按下去了。

#### 5.9.2 三档字面量与缺省

| 你打的（**第一行行首**） | 模式 | 后面要不要跟内容 |
|---|---|---|
| （什么都不打） | **A 直接干**（缺省） | — |
| `%task <要干的事>` | **B 派一单** | ⛔ 要，光一个代号不触发 |
| `%auto <项目> <要干的事>` | **C 唤起**（⛔ **不放行**） | ⛔ 要 |
| `%go` | **C 放行** | 不要 |
| `%stop` | 撤销全部令牌（刹车） | 不要 |
| `%mode` | 只看当前状态，⛔ **不改状态** | 不要 |

⭐ **A 是缺省，且缺省必须是最便宜的那档**：代价不对称——误留在 A 只要多说一句话，
误入 C 是四十分钟。给最便宜的动作加标记 = 向 99% 的消息收税，那税迟早被省掉。

⛔ **C 要两把钥匙，且两者之间必须是机制断层**：C 比 B 贵一个数量级，
⛔ 不能只差一个字符——手抖的滑动方向正好朝贵的那边。
`%auto` 只**唤起**（此时只许 `--dry-run`），必须**用户单独再发一条 `%go`** 才放行。
⛔ 模型不能替他打 `%go`：钩子只在**真实用户消息**上触发。

`/devloop` 与 `%auto` 等价（钩子拿到的是**展开前的原文**，看得见斜杠命令）。
⭐ 两个入口都留着：斜杠命令在菜单里**看得见**（可发现性），`%auto` 打字更少。
⛔ 斜杠命令定义必须带 `disable-model-invocation`——**Claude 自己叫不动，只有用户能起**。

判定规则（每条都对应一种具体的误触发）：

```text
⛔ 只认整条消息的**第一行行首**   —— 粘贴日志、句中提到代号，都不触发
⛔ 一次性，不继承                 —— 下一条不带代号就回到 A
   ⚠️「模式粘着」很危险：你说「继续」时，粘着的模式会替你做决定
⛔ %task / %auto 后面必须有内容    —— 光一个代号多半是手滑
⛔ 认不出的代号一律不触发，只回一行提示 —— **别猜他想干什么**
```

#### 5.9.3 ⛔ 前缀选型：每一条都从 CLI 二进制核实过

⛔ **`!` 根本用不了**，而这是上一版的回归——CLI 里就一行：

```text
function vG(H){if(H.startsWith("!"))return"bash";return"prompt"}
```

界面上还印着 `! for shell mode`。所以在输入框打 `!task 干活`，Claude Code 会把它当
**shell 命令**去执行 `task 干活`——**根本到不了模型，钩子也看不见**。
⚠️ 而全角 `！` **反而能用**（`startsWith("!")` 只匹配半角）——
**同一个字符半角失效、全角生效，这个区别肉眼看不出来**，⛔ 不能拿它当主字面量。

⛔ **当时自测为什么全绿**：它把 `{"prompt":"!task ..."}` 直接灌进钩子 stdin，
**绕过了 CLI 的输入分类**——测了错的那一层（六种假绿里的第四种）。
⚠️ 而上一版的提交正文列了 `@` `#` `++` `--` `跑::` 五种冲突，**唯独没查 `!` 自己**。

| 被否掉的前缀 | 实据 |
|---|---|
| `!` | **内置 shell 模式前缀**（上面那行 `vG`） |
| `/` | 斜杠命令，且会弹命令菜单 |
| `#` | 行首 `#` 是内置的「写进 CLAUDE.md」快捷键 |
| `@` | 在 Claude Code 里**任意位置**都弹文件补全 |
| `++` / `--` | 被 `C++` 和 `--dry-run` 打中 |
| `//` / `..` | 粘贴 JS/C 代码时片段常以 `// 注释` 开头，误触发 |
| `。。` | 中文口语的 `。。。` 是省略号，要靠「不许多一个」的规则去区分，脆 |
| `跑::` | 中文标点模式下 `:` 被改写成全角，**用户根本打不出来** |

⭐ **选 `%` 的三条实据**：CLI 里 `startsWith("%")` **零命中**；
中文输入法**不改写**百分号（逗号句号会被改成全角）；行首出现在自然语句里几乎为零。
⭐ 而且 `%` + **单词**比一串符号好：代号自解释、可扩展，读日志时一眼看出是命令不是标点，
⛔ 而且不再需要那些绕来绕去的排除规则。
⚠️ 仍然认全角 `％` 与 `！`（后者是中文标点模式下打 `!` 的实际产物，它能用，不认才是坑）。

#### 5.9.4 令牌的生命周期

```text
存放      ⛔ **全部落盘**，一个会话一份：<状态目录>/<session_id>.json
          C 的待批准状态另存 <session_id>.pending.json

B 令牌    一轮一撤（下一条不带代号就删）＋ **10 分钟**硬上限兜底
          （进程异常退出时不至于长期 armed）
C 令牌    跨轮（你得先跟模型谈计划、看完演练，才批），**30 分钟**到期
          %auto 的待批准窗口同样 30 分钟：过了就得重新 %auto

撤销条件  ⛔ 判据是「**落回模式 A**」，⛔ 不是「压根没打代号」
          ⚠️ %mode 排除在外——查状态不该有副作用
```

⛔ **撤销条件写错过一次，代价是分叉时闸在松的那一边**（1659c49 修）：
第一版写的是「压根没打代号才撤」，于是 `%tsak`（打错）、`%task`（空内容）、
`%go`（没 arm 过）这些情况代号都非空，**上一轮的 B 令牌继续活 10 分钟**，闸照样放行真跑。
⚠️ 最难受的是 `%auto`：注入给模型的上下文说「现在只许演练」，而物理闸在放行真跑。
⛔ 当时自测全绿，是因为它在验「认不出的代号」之前先 `rmtree` 了状态目录，
**正好跳过了泄漏路径**。

⛔ **令牌必须按 `session_id` 取，不许 glob 整个状态目录**（93d3bdb 修）：
第一版是「取第一个没过期的令牌」，于是 **A 会话打了 `%task`，B 会话的 dispatch 也被放行**。
⚠️ 两个会话同时开着在本机是常态，这不是理论问题。
（已核实 `PreToolUse` 的 stdin 带 `session_id`——CLI 里所有钩子的公共字段都来自同一个 `D1()`，第一项就是它。）

⛔ **状态全部落盘，绝不当会话记忆。** 上下文压缩碰不到磁盘上的文件。
⚠️ **一旦有人把「当前模式」写进 `CLAUDE.md`、或让模型「记住」当前是哪档，整套无声退化**
——退化成「靠 Claude 判断」，而那正是这套东西存在的唯一理由被推翻的那一版。

#### 5.9.5 强制层的放行规则

| 命令形态 | 要什么 |
|---|---|
| 带 `--dry-run` 的那一段 | 永远放行（⭐ 鼓励先演练） |
| `stats` / `prune` / `backends` / `constitution` / `status` / `halt` / 裸 `doctor` | 永远放行（真不花钱） |
| `dispatch` 真跑 | 令牌 `mode ∈ {B, C}` 且没过期 |
| `doctor --project` / `doctor --probe` | 同上——⛔ 它真花订阅额度，见 §5.1 |
| `autopilot` 真跑 | 令牌 `mode == C` **且 `approved`**，且没过期 |

⛔ **`--dry-run` 必须按段判，不能拿整条 Bash 命令去搜。**
第一版是 `if _DRYRUN.search(cmd): return 0`，判据落在整条命令上，于是这些全部被放行：

```text
autopilot --dry-run && autopilot          ← 后半截是真跑
autopilot   # 先 --dry-run 看看            ← 注释里的字也算
echo --dry-run; dispatch ...
```

⚠️ **实测记录里 206 条提到这两个子命令的行，有 204 条是复合命令**——
**复合是常态，不是边角情况**。这一条曾直接废掉整道闸的全部卖点。
改法：**先剥 shell 注释，再按 `&&` `||` `;` `|` 换行切段**，
只有命中 `dispatch`/`autopilot`/花钱形态 `doctor` 的**那一段自己**带 `--dry-run` 才放行。
⚠️ 分段是启发式（引号里的分隔符会被误切），但方向**偏严**——
多切几段只会让更多段需要令牌，⛔ 不会放过真跑。

⛔ **防自我授权**：模型有 Bash，理论上能自己写令牌文件把门打开。
所以**任何碰状态目录的 Bash 命令一律 deny**。
⚠️ 判据必须锚在**路径**上：第一版只匹配 `devloop-mode` 这个词，
结果把脚本自己的文件名 `devloop-mode-hook.py` 也匹配了——**连读一下、跑一下自检都被 deny**，
装上去第一条命令就绊住了作者。
⛔ **「宁可拦错也不放过」在这里是错的**：把工具本身锁死，人只会想办法关掉整套。

#### 5.9.6 ⛔ 残余漏洞（这是很强的护栏，**不是密封舱**）

```text
① 钩子 fail-open  两个钩子都是故意 `except: return 0`——自己坏了不该挡住用户干活。
                  ⛔ 代价：闸里一个笔误会把整道闸**静默关掉**。
                  2026-07-30 真发生过：引用了一个还没定义的正则，
                  **15 条断言从 DENY 变成放行，屏幕上什么都不报**。
                  对策：自测把 stderr 一并看住——有 traceback 就当失败。
② 不是密封舱      换个写法绕开字符串匹配是**可想象的**。它挡的是
                  「顺手就把门打开了」，⛔ 不是蓄意规避。⛔ 别当密封舱宣传。
③ 装在机器上      钩子注册在 ~/.claude/settings.json 里、指向本仓库绝对路径。
                  ⛔ 换机器或仓库挪位置 → 整道闸静默失效（见 §2）。
```

⚠️ **自测必须写成文件跑**（`python tools/test_mode_gate.py`）：
测试数据里字面包含要拦的命令串，在 Bash 里内联会被闸自己拦掉。
2026-07-30 实跑：46 条断言全绿（覆盖缺省两个都拦 / `%task` 只放行 dispatch /
令牌一轮一撤 / 全角也认 / 认不出的代号不触发 / `--dry-run` 串联 / 打错代号必须撤令牌 /
自我授权被拦但工具自己的脚本能跑 / 不花钱的子命令不许拦 / C 要两把钥匙 /
⛔ 跨会话不许串号 / 闸崩溃检测）。

⚠️ 顺带一条平台坑：Windows 下 Python 默认用 **GBK** 写 stdout，而 CLI 按 **UTF-8** 读。
不重设编码的话注入的中文会变乱码，而钩子「跑了」——**失败是静默的**。
（`devloop/cli.py` 在同一个坑上栽过。）

---

## 6. 新项目接入清单（四步）

1. **建目录**：项目根下建 `.devloop\`，写 `gates.sh` 与 `rules-digest.md`
   - ⚠️ **`templates\` 已有七份**（`gates.sh` · `constitution.md` · `constitution.toml` · `task.md` · `audit-task.md` · `plan.toml` · `rules-digest.md`）。⭐ `audit-task.md`（只读审计任务书）与 `plan.toml`（阶段计划）是 2026-08-02 从隔离分支合入的。⛔ 此处原写「四份，还差 `gates.sh`」，而 `templates/gates.sh` 有 197 行、早就存在——同一份文档的 §「交付物」里写的是「四份齐了 ✅」却列了五项，**两处互相矛盾且都与磁盘事实不符**（2026-08-02 独立复核抓到）。⭐ 新项目接入照 `templates/gates.sh` 改，⚠️ 那份才是抄写源；DevLoop 自己的 `.devloop/gates.sh` 里有大量只对本仓成立的判据
2. **填两份文件**：
   - `gates.sh`：把该项目「怎么算通过」写成命令，遵守 §5.2 的退出码与 stdout 约定
   - `rules-digest.md`：按 §5.4 写铁律、禁改清单、常用命令
3. ⭐⭐ **绿检闸**（**不花一分额度**，几秒出结果）：

   ```
   devloop gates --project <新项目> --commit HEAD
   ```

   **退出码必须是 0，FAIL 必须是 0 行。**
   ⛔ 不是 0 ⇒ **这道闸测的不是工人，是「这个仓库里本来有没有红」** —— 先修闸，别派单。

   ⚠️ **这一步 2026-08-16 才补进来。补之前，整份清单里唯一的验证动作是第 4 步的只读单，
   ⛔ 而只读单恰恰是唯一不建工位、不跑闸的那种**（`cli.py`：`writes = args.tools != "readonly"`，
   闸只在 `if wt:` 里跑）。⇒ **接入验收把唯一杀过人的东西整个绕开了，还要花订阅额度。**
   ⭐ 实测账单：靶子项目连败 4 单，其中 **3 单**死在闸上、不是死在作业上
   （铁证：2026-08-05 那一单工人**一个文件都没动**，闸照样红）。

   ⭐ 工具已经拦得住「空转即绿」（2026-08-16 实测：全 SKIP / 全 VOID / 一行不打
   三种都判退出码 2「闸自身故障」），⛔ 所以这一步不需要另配「至少一道 PASS」的判据。

   ⭐ 顺带跑一次开跑前体检的名册对账（⛔ 只对自动驾驶那条路生效，见 `PLAN.md` 的 D-PREFLIGHT-01）：
   照 `tests/test_preflight_roster.py` 的两条判据自查 ——
   **体检报出的闸名 ≡ 真跑报出的**，两个方向的差集都必须是空集。

4. **验证**：派一个只读任务（如「列出本项目的测试文件并报告各自用途」），确认能跑通、回执模型正确、禁令被遵守
   - ⛔ **别把这一步删掉换成第 3 步**：它同时核对**三样**——能跑通、**回执模型正确**、禁令被遵守，
     ⭐ 而闸只答得了第一样。「回执模型核对」在整份清单里**只有这一次**。

**工具本体零改动。** 若接入过程中发现必须改 `_infra\devloop\` 下的东西，那就是通用性纪律被破坏的信号——该把那部分外置到 `.devloop\`，而不是往工具里加特例。

---

## 7. 尚未定义（动工前须补齐）

| 缺口 | 阻塞谁 | 何时补 |
|---|---|---|
| `halt.py` 如何区分「舰队工人」与「用户自己的 claude 会话」 | Phase 7 | Phase 7 前置。方向：派单时打进程环境标记，按标记筛 |
| ~~「阶段计划」的任务依赖图用什么格式表达~~ | — | ✅ **已定并实现**：`[[task]] needs` + 加载期查悬空/成环，契约见 §5.8。⚠️ 与 LangGraph 无关——那条仍未接（BACKLOG G-55）|
| 嵌入模型与向量存储的具体选型 | Phase 3 | Phase 3 开工前。约束：嵌入式、不引入独立数据库服务 |
| ~~进度看门狗的实现载体~~ | — | ✅ **已定并实现**：`[stage.budget] watchdog_k`（默认 3），判据是「这一轮之后**绿的单有没有变多**」，⛔ 不看「有没有跑」。⭐ 2026-07-29 首次自动驾驶真跑崩掉时它（K=2）正确停机——**四条失控防线里第一条真正触发过的** |
