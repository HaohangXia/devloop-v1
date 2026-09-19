<p align="right"><a href="./README.md">English</a> · <strong>中文</strong></p>

# Python 包

本目录保存 DevLoop v1 的编排实现。命令入口从 [`cli.py`](cli.py) 开始，主要能力边界如下。

| 领域 | 主要模块 |
|---|---|
| 派发与后端 | `dispatch.py`、`backends.py`、`credentials.py`、`models.py` |
| 独立 worktree | `worktree.py`、`prune.py`、`jobs.py` |
| 验收与复核 | `gates.py`、`audit.py`、`dossier.py`、`constitution.py` |
| 无人值守阶段 | `autopilot.py`、`plan.py`、`progress.py`、`nightly.py` |
| 限额与证据 | `quota.py`、`pricing.py`、`telemetry.py`、`records.py` |
| 诊断 | `doctor.py`、`halt.py`、`handoff.py`、`fanout.py` |

这是历史代码。公开行为以 [`../SPEC.md`](../SPEC.md) 为准，状态与限制见
[`../README.zh-CN.md`](../README.zh-CN.md)。
