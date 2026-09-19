<p align="right">
  <a href="./README.md"><img alt="Read in English" src="https://img.shields.io/badge/Language-English-0F766E?style=flat-square"></a>
  · <strong>中文</strong>
</p>

# DevLoop v1

**一个历史版本的 AI 编码 Agent 编排项目，核心机制包括独立 Git worktree、脚本验收闸、
明确的额度控制与运行遥测。**

> **这是历史 v1，不是持续维护的产品。**仓库作为可检查的工程记录公开，不承诺支持或路线图。
> 其中的验证经验延续到了 [nonconstant](https://github.com/HaohangXia/nonconstant)，
> 但 nonconstant 不导入 DevLoop 的代码。

DevLoop 来自一次具体失败：一个 Agent 报告 37 项检查中有 35 项为绿色，实际却只满足 9 条验收
标准中的 1.5 条。因此，项目最核心的设计决定是：是否完成应由可执行检查判断，而不是由
模型自己的总结判断。

## 系统结构

```text
用户显式触发
     |
     v
调度器 ------> 独立 Git worktree 中的工作进程
  |                         |
  |                         v
  |                       实现尝试
  |                         |
  v                         v
预算 / 额度刹车 ------> 脚本验收闸
                           |
                           v
                    台账 + 可复核分支
```

Git worktree 提供的是变更隔离和可丢弃分支，不是安全沙箱。工作进程仍运行在同一台机器上，
工具与凭据边界需要另外显式配置。

## 已实现的机制

- **注册宿主 hook 后的显式模式。**直接工作不带代号；`%task` 请求一次派发，`%auto` 为
  无人值守阶段上锁待命，`%go` 再将其放行。仓库提供的 Pre-tool hook 可在模型 prompt 之外
  执行模式限制。
- **独立 worktree。**写入任务在专用分支工作，不直接修改主 checkout。
- **可执行验收。**由项目提供的脚本判断一个工作单元是否通过，而不是相信模型叙述。
- **失控保护。**派发数量、墙钟时间、额度、费用和磁盘余量都可以停止批次。
- **运行记录。**台账记录后端、时间、token 与结果；后台任务可以在之后检查或停止。
- **人的最终权限。**constitution 条款标出必须停下并交还给人的决定，避免编排器擅自猜测。

## 证据，以及一个被实测推翻的假设

当前快照可收集到 **847 项测试**。这个数字只描述仓库中的测试集合，不代表生产使用或第三方
认证。其中一部分测试专门证明保护机制会转红，因为只证明“能通过”的测试无法说明闸真的
具备拒绝能力。

项目最初被论证为分级成本系统：较贵模型负责规划，较便宜模型负责执行。一次受控的文档审计
批次反驳了这个无条件的节省成本主张。

| 测量范围 | 低成本执行路线 | 高级模型路线 |
|---|---:|---:|
| 仅执行 | US$0.56 | US$26.82 |
| 加上生成任务规格的拆解成本 | US$28.23 | US$26.82 |

该结果只覆盖一个文档审计任务中的 11 个工作单元，不能推广成普遍的模型成本定律；但它足以
让项目撤回原来的无条件节省成本主张。最终留下的价值是并行、变更隔离、无人值守执行和可检查证据。

模式与费用控制脚本**不会因为安装 Python 包而自动启用**。它们需要另行注册到机器级
`~/.claude/settings.json`，而该配置不在此快照中。注册项使用绝对路径，hook 又是 fail-open；
移动仓库、更换机器或 hook 自身损坏，都可能静默取消费用限制。应把它们视为宿主专用护栏，
而不是安全边界；详见 [`SPEC.md` 第 2 节与第 5.9 节](SPEC.md)。

出处与作者边界见 [`SNAPSHOT.md`](SNAPSHOT.md)，冻结契约见 [`SPEC.md`](SPEC.md)，失败记录见
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

## 检查这个快照

要求 Python 3.11 或更高版本、Git，以及用于 shell 闸测试的 Bash（Windows 使用 Git Bash）。
先克隆仓库，再在隔离环境中安装包与测试依赖：

```bash
git clone https://github.com/HaohangXia/devloop-v1.git
cd devloop-v1
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
PYTHONUTF8=1 python -m pytest
python -m devloop.cli --help
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活环境，并在运行 pytest 前设置
`$env:PYTHONUTF8 = "1"`。

运行测试不需要派发付费模型。真正启动工作进程的命令需要兼容的本地后端和用户自己的凭据；
任何凭据都不应进入本仓库。

快照 CI 在 Windows、Python 3.11 和 Git Bash 环境中运行测试，不配置模型凭据，也不派发真实
工作进程。依赖未公开历史报告语料或原本地靶子项目的测试，会在缺少相应输入时明确跳过。
CI 通过不代表真实后端、无人值守运行或宿主 hook 安装已经验证。

## 命令入口

下表只是索引，不代表持续支持承诺。冻结的命令契约以 `--help` 与 [`SPEC.md`](SPEC.md) 为准。

| 命令 | 用途 |
|---|---|
| `devloop dispatch` | 让工作进程执行一个或多个任务规格。 |
| `devloop collect` | 收集外部 Subagent 编排器返回的一个批次。 |
| `devloop halt` | 列出或停止后台任务。 |
| `devloop status` | 从台账检查后台任务进度。 |
| `devloop eval` | 运行冻结且经人工核对的评测集。 |
| `devloop autopilot` | 执行一个已声明阶段中的工作单元。 |
| `devloop constitution` | 初始化或检查必须由人决定的边界。 |
| `devloop nightly` | 为早晨复核汇总无人值守运行。 |
| `devloop prune` | 检查、归档或删除隔离分支与 worktree。 |
| `devloop audit` | 检查、复核或转换已记录的审计发现。 |
| `devloop records` | 指纹化或归档无法重新生成的记录。 |
| `devloop backends` | 列出已配置的工作后端，但不打印凭据。 |
| `devloop doctor` | 裸命令只做低成本排除检查；加 `--project` 或 `--probe` 会真正启动、消耗额度并写入台账；宿主 hook 已注册时还需模式 token。 |
| `devloop gates` | 运行项目的验收闸。 |
| `devloop stats` | 汇总台账活动。 |

## 能力边界

- 这是历史工程快照，不是生产服务或受支持的库。
- Worktree 隔离不是进程、网络或凭据隔离。
- 脚本闸只能检查作者已经做成可观察信号的内容，无法证明规格完整或推理正确。
- 模型可用性、订阅行为和供应商价格会变化；所有测量都是有时间和范围限定的历史记录。
- 无人值守执行会消耗额度并创建大量本地分支和 worktree；保护机制只能降低风险，不能消除风险。
- `.devloop/` 运行记录通常被 Git 忽略，需要保留时必须主动归档。

## 仓库地图

| 路径 | 用途 |
|---|---|
| [`devloop/`](devloop/) | Python 包：派发、验证闸、worktree、台账与保护机制。 |
| [`tools/`](tools/) | 模式 hook 与配套运维工具。 |
| [`templates/`](templates/) | 7 份项目侧模板：闸、任务、审计任务、计划、规则摘要和两份 constitution 文件。`SPEC.md` 的冻结清单不允许在此目录增加 README。 |
| [`tests/`](tests/) | 回归测试、红绿验证和集成式测试。 |
| [`.devloop/`](.devloop/) | 刻意保留的快照证据：项目侧闸配置、历史计划与任务，以及一份 G28 前遥测备份；这些是记录，不代表当前运行状态。 |
| [`SPEC.md`](SPEC.md) | 冻结的命令、退出码与文件格式契约。 |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | 失败记录，包括后来被证伪的诊断。 |
| [`BACKLOG.md`](BACKLOG.md) | 快照时仍开放的历史事项。 |
| [`BACKLOG-ARCHIVE.md`](BACKLOG-ARCHIVE.md) | 已关闭发现及保留的编号。 |
| [`SNAPSHOT.md`](SNAPSHOT.md) | 公开边界、出处与作者说明。 |

## 作者边界

AI 编码工作进程生成了工作仓库中相当一部分实现与文字。Andrew Xia 负责设计、验收标准、
拒绝决定和测量要求，并选择了这个压缩后的公开快照。[`SNAPSHOT.md`](SNAPSHOT.md) 记录了历史
文件中 commit 作者字段与第一人称叙述的限制。

## 许可

[MIT](LICENSE)
