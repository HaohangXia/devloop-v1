<p align="right"><strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a></p>

# Operational tools

These scripts sit beside the Python package because they are host-side controls and probes,
not importable product modules.

| File | Purpose |
|---|---|
| [`devloop-mode-hook.py`](devloop-mode-hook.py) | Recognises explicit user mode triggers before the model handles the prompt. |
| [`devloop-spend-gate.py`](devloop-spend-gate.py) | Denies quota-spending commands when no valid mode token exists. |
| [`probe_ledger_concurrency.py`](probe_ledger_concurrency.py) | Reproduces concurrent ledger-write behaviour. |
| [`test_mode_gate.py`](test_mode_gate.py) | Separate harness for the mode gate's literal command cases. |

The Python package does not register these hooks. Machine-level registration, absolute-path
coupling and fail-open limitations are documented in the root [`README.md`](../README.md) and
[`SPEC.md`, §§2 and 5.9](../SPEC.md). Read those boundaries before installing a hook into a
live environment.
