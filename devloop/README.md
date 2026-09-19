<p align="right"><strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a></p>

# Python package

This directory contains DevLoop v1's orchestration implementation. Start at
[`cli.py`](cli.py) for the command surface, then follow the capability boundaries below.

| Area | Main modules |
|---|---|
| Dispatch and backends | `dispatch.py`, `backends.py`, `credentials.py`, `models.py` |
| Separate worktrees | `worktree.py`, `prune.py`, `jobs.py` |
| Acceptance and review | `gates.py`, `audit.py`, `dossier.py`, `constitution.py` |
| Unattended stages | `autopilot.py`, `plan.py`, `progress.py`, `nightly.py` |
| Limits and evidence | `quota.py`, `pricing.py`, `telemetry.py`, `records.py` |
| Diagnostics | `doctor.py`, `halt.py`, `handoff.py`, `fanout.py` |

This is historical code. Public behaviour is described by [`../SPEC.md`](../SPEC.md), while
[`../README.md`](../README.md) explains status and limitations.
