<p align="right">
  <strong>English</strong> ·
  <a href="./README.zh-CN.md"><img alt="阅读中文版" src="https://img.shields.io/badge/Language-中文-0F766E?style=flat-square"></a>
</p>

# DevLoop v1

**A historical AI coding-agent orchestration project built around separate worktrees,
script-based acceptance gates, explicit spend controls and run telemetry.**

> **Historical v1, not a maintained product.** This repository is published as an
> inspectable record of the engineering work. It has no support commitment or roadmap.
> The verification lessons continued in
> [nonconstant](https://github.com/HaohangXia/nonconstant); that project does not import
> DevLoop's code.

DevLoop started with a concrete failure: an agent reported 35 passing checks out of 37 on work that
satisfied only 1.5 of 9 acceptance criteria. Its central design decision was therefore to
make “done” a decision made by executable checks rather than by the model's own summary.

## System shape

```text
explicit user trigger
        |
        v
dispatcher -----> worker process in a separate Git worktree
        |                         |
        |                         v
        |                  implementation attempt
        |                         |
        v                         v
budget / quota brakes ----> script acceptance gates
                                  |
                                  v
                         ledger + reviewable branch
```

A Git worktree provides change isolation and a disposable branch. It is not a security
sandbox: workers still run on the same machine and require an explicitly configured tool
and credential boundary.

## What was implemented

- **Explicit modes, when host hooks are registered.** Direct work is unmarked; `%task`
  requests one dispatch, `%auto` arms an unattended stage and `%go` releases it. The
  repository includes a pre-tool enforcement hook that operates outside the model's prompt
  context.
- **Separate worktrees.** Write tasks run on dedicated branches instead of editing the main
  checkout directly.
- **Executable acceptance.** Project-provided scripts, not model prose, determine whether a
  unit passes its declared gate.
- **Runaway controls.** Dispatch count, wall time, quota, spend and disk headroom can stop a
  batch.
- **Operational records.** A ledger records backend, timing, token and outcome data; detached
  jobs can be inspected and halted later.
- **Human authority.** Constitution clauses identify decisions that must stop and return to
  a person rather than being guessed by the loop.

## Evidence, including a disproved hypothesis

The snapshot currently collects **847 tests**. The number describes the repository's test
collection, not production usage or independent assurance. Several tests deliberately prove
that a guard turns red; green-only tests would not establish that a gate can reject anything.

DevLoop was initially justified as a cost-tiered system: an expensive model would plan and a
cheaper model would execute. A controlled documentation-audit batch contradicted that claim.

| Measured scope | Lower-cost execution route | Premium route |
|---|---:|---:|
| Execution only | US$0.56 | US$26.82 |
| Including the decomposition work that produced the task specifications | US$28.23 | US$26.82 |

The result was scoped to 11 units in one documentation-audit task. It does not establish a
universal model-cost law, but it was enough to reject the project's own unqualified savings
claim. The value that remained was parallelism, change isolation, unattended execution and
inspectable evidence.

The mode and spend-control scripts are **not enabled by installing the Python package**.
They require separate machine-level registration in `~/.claude/settings.json`, which is not
included in this snapshot. That registration points to absolute paths and the hooks fail
open: moving the repository, changing machines or breaking a hook can silently remove spend
enforcement. Treat them as host-specific guardrails, not a security boundary; see
[`SPEC.md`, §§2 and 5.9](SPEC.md).

See [`SNAPSHOT.md`](SNAPSHOT.md) for provenance and authorship, [`SPEC.md`](SPEC.md) for the
contract, and [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for failure records.

## Inspect the snapshot

Requirements: Python 3.11 or later, Git, and Bash for shell-gate tests (Git Bash on
Windows). Clone this repository, then install the package and test dependency in an
isolated environment:

```bash
git clone https://github.com/HaohangXia/devloop-v1.git
cd devloop-v1
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
PYTHONUTF8=1 python -m pytest
python -m devloop.cli --help
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and set
`$env:PYTHONUTF8 = "1"` before running pytest.

The test suite does not require dispatching a paid model. Commands that actually start a
worker require a compatible local backend and user-owned credentials; no credentials belong
in this repository.

The snapshot CI runs this suite on Windows with Python 3.11 and Git Bash, without model
credentials or live worker dispatch. Tests requiring the unpublished historical report
corpus or the original local target project are explicitly skipped when those inputs are
absent. A successful CI run does not validate a real backend, unattended operation or host
hook installation.

## Command surface

This table is an index, not a promise of ongoing support. Use `--help` and [`SPEC.md`](SPEC.md)
for the frozen command contract.

| Command | Purpose |
|---|---|
| `devloop dispatch` | Run one or more task specifications through worker processes. |
| `devloop collect` | Collect a batch returned by an external subagent orchestrator. |
| `devloop halt` | List or stop detached work. |
| `devloop status` | Inspect detached-job progress from the ledger. |
| `devloop eval` | Run the frozen, hand-verified evaluation set. |
| `devloop autopilot` | Execute the units in one declared stage. |
| `devloop constitution` | Initialise or check human-decision boundaries. |
| `devloop nightly` | Summarise an unattended run for morning review. |
| `devloop prune` | Inspect, archive or remove isolated branches and worktrees. |
| `devloop audit` | Inspect, verify or convert recorded audit findings. |
| `devloop records` | Fingerprint or archive records that cannot be regenerated. |
| `devloop backends` | List configured worker backends without printing credentials. |
| `devloop doctor` | Run cheap elimination checks only. Adding `--project` or `--probe` starts a real, quota-consuming worker path and records a ledger row; when the host hooks are registered, it also requires a mode token. |
| `devloop gates` | Run the project's acceptance gates. |
| `devloop stats` | Summarise recorded ledger activity. |

## Boundaries

- This is a historical engineering snapshot, not a production service or supported library.
- Worktree isolation is not process, network or credential isolation.
- A scripted gate can only check what its author made observable; it cannot prove that a
  specification is complete or that reasoning is correct.
- Model availability, subscription behaviour and provider pricing change over time. Recorded
  measurements are historical and scoped to their documented runs.
- Unattended execution spends quota and can create many local branches and worktrees. The
  controls reduce that risk; they do not eliminate it.
- `.devloop/` runtime records are normally ignored by Git and require deliberate archival if
  they need to survive.

## Repository map

| Path | Purpose |
|---|---|
| [`devloop/`](devloop/) | Python package: dispatch, gates, worktrees, ledger and controls. |
| [`tools/`](tools/) | Mode hooks and supporting operational utilities. |
| [`templates/`](templates/) | Seven project-side templates: gates, task, audit task, plan, rules digest and two constitution files. The frozen inventory in `SPEC.md` prevents adding folder READMEs here. |
| [`tests/`](tests/) | Regression, red/green gate and integration-style tests. |
| [`.devloop/`](.devloop/) | Deliberately retained snapshot evidence: project-side gate configuration, historical plans and tasks, and one pre-G28 telemetry backup. These are records, not current runtime state. |
| [`SPEC.md`](SPEC.md) | Frozen command, exit-code and file-format contract. |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | Failure records, including diagnoses that were later disproved. |
| [`BACKLOG.md`](BACKLOG.md) | Open historical items at the snapshot point. |
| [`BACKLOG-ARCHIVE.md`](BACKLOG-ARCHIVE.md) | Closed findings and preserved issue identifiers. |
| [`SNAPSHOT.md`](SNAPSHOT.md) | Publication boundary, provenance and authorship disclosure. |

## Authorship

AI coding workers produced much of the implementation and prose in the working repository.
Andrew Xia set the design, acceptance criteria, rejection decisions and measurement requests,
then selected this squashed public snapshot. [`SNAPSHOT.md`](SNAPSHOT.md) documents the
limits of commit-author metadata and first-person language in the historical files.

## Licence

[MIT](LICENSE)
