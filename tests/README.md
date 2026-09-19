<p align="right"><strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a></p>

# Test suite

The suite covers command wiring, worktree lifecycle, gate behaviour, quota and wall-time
controls, ledger durability, detached jobs, templates, documentation drift and the frozen
evaluation harness.

Tests include both ordinary regression cases and explicit red/green demonstrations. A green
path shows that valid work can pass; a red path shows that the control can reject a known bad
condition. Neither proves that every real-world failure is covered.

The quota fixture is derived from a captured event stream. It preserves the event schema
and rate-limit values used by the contract tests; response text, identifiers, model names
and usage amounts are replaced with illustrative values. Machine paths, account connections,
installed tools and other unrelated environment metadata are removed before publication.

Backend-policy tests load `fixtures/backends.example.json`, not the reader's private
registry. Quota-control tests run the real credential check against temporary synthetic
metadata and replace the worker call; no login or token is required. A negative test also
checks that missing credentials stop dispatch before any worker is reached. These tests do
not audit the configuration of a real machine.

Run the current collection from the repository root:

```bash
python -m pip install -e ".[dev]"
PYTHONUTF8=1 python -m pytest -ra
```

On PowerShell, set `$env:PYTHONUTF8 = "1"` first, then run `python -m pytest -ra`.
Git and Bash are required for subprocess and shell-gate tests. Some historical tests require
the unpublished `_review/` corpus or the original `eco-ob` checkout and explicitly skip when
those inputs are absent. No model credentials or live worker dispatch are required.

See [`../README.md`](../README.md) for the measured count and its scope.
