# DevLoop

**An orchestration harness for AI coding agents.** An expensive model plans and reviews; workers execute in throwaway worktrees; deterministic scripts decide what counts as done.

> **v1, archived.** No issues, no roadmap, nothing promised. Published as a record
> of the work, not as something to depend on — a successor project carries the
> maintenance. See [SNAPSHOT.md](SNAPSHOT.md).
>
> Single-commit snapshot, not the history. **846 tests / 846 条测试** (`python -m pytest`) — several
> of them exist to prove a guard goes red, not just that it goes green. Most of the
> text you are about to read was written by the workers rather than by me;
> [SNAPSHOT.md](SNAPSHOT.md) says who wrote what, and what was left out.

> ### ⛔ What this is *not* (revised 2026-08-01)
>
> It was originally justified as a **cost play** — expensive model plans, *cheap* model executes.
> **That justification is dead**, killed by its own measurements:
> G-38 found the cheap route **loses by 5%** once decomposition cost is counted (quality 27% vs 84%),
> and the ledger shows **20 of 35 real units ran on Opus**, not on the cheap backend.
>
> ⭐ **What survives is a different value proposition — and it is the one that matters for
> unattended work:** isolation · a gate that scripts (not models) decide · brakes that can
> actually stop it · a ledger that says what it cost.
> ⚠️ Claude Code's built-in subagents give you **none of those four** — they share your
> working directory, nothing forces verification, there is no budget/quota/watchdog brake,
> and nothing is recorded.

> Built to solve a real problem: an agent once reported *"35/37 tests green"* on work that met only 1.5 of 9 acceptance criteria. DevLoop exists so that **"done" is decided by an exit code, never by a model's self-report.**

> ⭐ **Default backend since 2026-07-29 (`106a35d`): your own Claude subscription.** Workers are `claude -p` subprocesses authenticated by OAuth — **⛔ no `--bare`** (that flag exists precisely to skip OAuth and use an env-var key, which is the opposite of what a subscription needs) and **⛔ no injected `ANTHROPIC_*`** (injecting them points the worker at a third-party endpoint, at which point it is not your subscription any more). ⚠️ The paid-API backend needs `--bare` for the mirror-image reason: without it an expired OAuth token hijacks the key. Both directions are pinned by tests.
>
> **The dollar cost of the default route is `0` — that is a value, not an unknown.** Recording it as `None` would stop the autopilot on its very first unit, because one of its guards is "stop if the cost cannot be computed" (`1ff4dbb`). ⛔ But `0` is not "free": what it spends is subscription quota, a real and exhaustible resource — `devloop/quota.py` exists for exactly that reason.

---

## How to make it do work

⚠️ **Nothing happens unless you say so, and you say so with a literal — not with a sentence.** DevLoop does not infer what you want.

| What you type | What you get |
|---|---|
| *(nothing)* | **A — Claude edits directly.** ⛔ The cheapest action is the unmarked one on purpose: the costs are asymmetric. Landing in A by mistake costs you one extra sentence; landing in C by mistake costs forty minutes. Marking the cheap action taxes 99% of your messages, and a tax like that gets optimised away eventually. |
| `%task <what to do>` | **B — dispatch one unit** to an isolated worker in a throwaway worktree. |
| `%auto <project> <what to do>` | **C — arm the unattended loop.** ⛔ Arms it and nothing more: while armed, only `--dry-run` gets through. |
| `%go` | **C — release it.** Two keys, deliberately: C is an order of magnitude more expensive than B, so what separates them has to be a **mechanism break, not one character** — a slip of the finger slides toward the expensive side. |
| `%stop` | revoke every token (the brake) |
| `%mode` | show the current state, ⛔ change nothing |

`/devloop` is equivalent to `%auto`, and carries `disable-model-invocation` — ⛔ **Claude cannot trigger it itself**; only you can (`93d3bdb`). Both entry points are kept because they are good at different things: the slash command is *visible* in the menu, the literal is fewer keystrokes.

**⭐ This is not a convention — it is enforced by two hooks, and the judgement never touches the model** (`2ff956d`):

```text
the literal you typed ──regex──▶ token file ──PreToolUse──▶ command allowed / denied
```

- **Recognition** — a `UserPromptSubmit` hook (`tools/devloop-mode-hook.py`) runs **before the model ever sees your message**: pure regex, writes a token to disk, and echoes back what it decided via `systemMessage`. ⛔ The echo is printed by the script, never written by Claude — Claude gets the classification wrong, so Claude would get the echo wrong too.
- **Enforcement** — a `PreToolUse(Bash)` hook (`tools/devloop-spend-gate.py`) returns `permissionDecision: "deny"`. **With no token, the quota-spending commands physically cannot start.**
- **CLAUDE.md explains this and nothing more.** ⛔ It never carries the gate: it is a prompt, and prompts are diluted by attention decay and by context compaction.

The design was forced by the user, who rejected the version where the model classified intent in natural language — *"不行 我觉得这个判断方法也不好，我觉得需要一些特殊的字段来进行触发"*. ⚠️ He was right with evidence: on 2026-07-29 I misjudged which route to take **four times in one day**, and all four were only caught by a real run.

**Tokens do not stick.** B's token is revoked on the next message that carries no code, with a 10-minute hard cap; C's survives across turns but expires after 30 minutes. ⛔ All of it lives on disk, never in the session's memory — context compaction cannot reach it, and the moment anyone writes "the current mode" into CLAUDE.md or asks the model to "remember" it, the whole scheme degrades silently. Two leaks have already been closed and are worth knowing about: the gate used to glob the state directory and take the first unexpired token, so **a `%task` in one session released a dispatch in another** (`93d3bdb`, now keyed by `session_id`); and the revoke condition was written as "no code was typed at all", so a typo like `%tsak` — or `%go` with nothing armed — **left the previous round's token alive for its full 10 minutes** (`1659c49`). ⚠️ The nasty version of that second one was `%auto`: the context injected into the model said "dry runs only" while the physical gate was still open. When the two disagree, ⛔ it is the gate that must be the strict one.

⚠️ **Why the prefix is `%` and not something you'd have guessed** — every candidate below was rejected against the CLI binary, not against intuition (`93d3bdb`, `1659c49`):

- ⛔ `!` — the **built-in shell-mode prefix**: `function vG(H){if(H.startsWith("!"))return"bash"}`, and the UI prints `! for shell mode`. `!task 干活` is executed as the shell command `task 干活` and **never reaches the model**, so no hook ever sees it. ⚠️ Full-width `！` *does* work, because the check is on the half-width character — the same glyph works or fails depending on your IME, and **you cannot see the difference**. This shipped as a regression and my own test stayed green, because it piped `{"prompt":"!task ..."}` straight into the hook's stdin and **bypassed the CLI's input classification** — the test exercised the wrong layer.
- ⛔ `/` is slash commands · `#` at line start is the built-in "write this into CLAUDE.md" shortcut · `@` pops file completion **anywhere** in the line.
- ⛔ `++` and `--` are hit by `C++` and `--dry-run` · `//` and `..` are hit by pasted code comments · `。。` is hard to separate from the Chinese ellipsis `。。。` · `跑::` cannot be typed at all under Chinese punctuation mode.
- ⭐ `%` has **zero `startsWith` hits** in the CLI, and Chinese IMEs do not rewrite it (they do rewrite comma and full stop).

⛔ **State the residual hole, because this gets over-sold otherwise:** this is a strong guardrail, **not a sealed box.** A phrasing that dodges the gate's string matching is imaginable; what it stops is *casually walking through the door*. Three known soft spots, each a deliberate trade-off:

- Both hooks **fail open** (`except: return 0`) — a broken hook must not block you from working. The price is that a typo inside the gate silently switches the whole gate off. ⚠️ That really happened: a reference to an undefined regex turned 15 assertions from DENY into allow, **with nothing printed on screen** (`1659c49`). The self-test now treats any traceback on stderr as a failure.
- Claude has Bash, so in principle it could write itself a token. The gate therefore denies any Bash command that touches the state directory. ⚠️ It caught me on the very first command after install — and the first version of that rule matched the word `devloop-mode` against the hook's **own filename** `devloop-mode-hook.py`, so I could not even run its self-test. ⛔ "Better to over-block than to let one through" is the wrong instinct here: lock the tool against its own maintainer and the maintainer just switches the whole thing off. The criterion is now anchored on the **path**.
- The gate judges **per command segment** (split on `&&` `||` `;` `|` and newlines, after stripping shell comments), because the first version searched the whole command line for `--dry-run` and let `autopilot --dry-run && autopilot` through entirely. ⚠️ Splitting is a heuristic — separators inside quotes get mis-split — but it errs **strict**: over-splitting only demands a token more often, ⛔ it never releases a real run. The fix was not cosmetic: of 206 recorded command lines mentioning `dispatch`/`autopilot`, **204 were compound** — compound is the normal case, so the first version had voided the gate's entire value proposition.

---

## Why this exists

Running AI coding agents at scale hits three walls:

| Wall | DevLoop's answer |
|---|---|
| **Cost** — every task on a frontier model burns budget fast | Tiered routing: the expensive model only sees condensed information (plans, reports, review). Cheap workers do the token-heavy execution. ⚠️ **Measured, and it did not pay off at n=11** — the decomposition tax exceeded the savings outright and quality was 3× worse. See the Status section. This row is the project's hypothesis, not a demonstrated result. ⭐ **2026-07-29 the question changed shape**: workers now run on the maintainer's Claude subscription (`kind = "subscription"`), so the execution leg costs **$0 in dollars** and spends quota instead. That does not *solve* the trade-off, it relocates it — quota runs out, and when it does the whole batch has to stop. |
| **Trust** — agents confidently report success on incorrect work | A deterministic gate runs after every task. Exit code, not prose. The gate executes from a read-only copy outside the worktree, and its fingerprint is recorded before dispatch and verified before it runs — a worker cannot rewrite its own examiner. Any exit code outside the 0/1/2 protocol is normalised to "the gate itself is broken", so a crashing gate can never be misreported as failing work. **Implemented and tested.** |
| **Repetition** — you re-type "now run the tests / now fix it / now do the next one" all day | Those instructions become code: dependency-ordered dispatch, gate-on-completion, failure retry with context, and an escalation path that only interrupts you when a rule in the constitution is hit. |

---

## Architecture

```text
┌─ Orchestrator (frontier model) ──────────────────────┐
│  plan · decompose · review reports · accept · merge  │  ← expensive, but only reads condensed data
└──────────────┬───────────────────────────────────────┘
               │  devloop dispatch  (env-scoped per process)
┌──────────────▼───────────────────────────────────────┐
│  Python orchestration package                        │
│  task queue · worktree isolation · telemetry         │
└──────────────┬───────────────────────────────────────┘
               │  spawns workers (--parallel N, or --detach to run in background)
┌──────────────▼───────────────────────────────────────┐
│  Workers · one throwaway worktree each · subprocess  │  ← `claude -p` on YOUR subscription
│  implement · test · fix · bulk mechanical edits      │  ← $0 in dollars, spends quota
└──────────────┬───────────────────────────────────────┘
               │  on completion
┌──────────────▼───────────────────────────────────────┐
│  Acceptance gate  —  pure script, zero model, zero $ │  ← cannot lie, cannot be bribed
│  test suite · determinism check · baseline guard     │
└──────────────────────────────────────────────────────┘
```

**Cross-project by construction.** The tool holds no project knowledge. Each repo supplies a `.devloop/` directory (a gate script and a rules digest). Onboarding a new project touches zero lines of the tool.

---

## Quick start

Two paths, both exercised for real on 2026-07-29 on the subscription backend. (⚠️ `devloop` is **not** on PATH — the console script in `pyproject.toml` was never installed (G-04). Every command below uses `python -m devloop.cli`, which is what actually runs.)

**Dispatch one unit and watch it:**

```bash
python -m devloop.cli dispatch --project . --task .devloop/tasks/<spec>.md --tools implement
```

`--tools` defaults to `readonly`; a write task needs `implement`. The worker gets a throwaway worktree, the gate runs from a read-only copy outside it, and the output is committed to an isolation branch **only after** the gate is green — never before, or three of the guards go vacuous.

**Run a whole stage unattended:**

```bash
python -m devloop.cli autopilot --project . --stage <name>            # .devloop/plans/<name>.toml
python -m devloop.cli autopilot --project . --stage <name> --dry-run  # ⛔ spends nothing
```

`--dry-run` prints what the next round would dispatch and whether a limit would stop it immediately. ⚠️ It reads the **effective** gate list (stage-level ∪ task-level) — announcing the task-level list while running the effective one would be confirming a guard that differs from the one that runs.

⚠️ Both commands above are exactly what the spend gate guards. Typed by you in a shell they run normally; asked of Claude without the matching mode token, the `PreToolUse` hook denies them before the process starts.

⛔ Exit code `3` from `autopilot` means **"it stopped, come look"** (budget · dispatch count · wall clock · watchdog · constitution · quota · nothing left to dispatch), *not* success.

**When you hit the subscription cap:**

```bash
python -m devloop.cli dispatch --project . --task-dir .devloop/tasks/ --wait-for-reset
```

Without the flag the batch stops and prints how many units are left plus the exact time to come back at; with it, the process sleeps until then and resumes. ⚠️ `--wait-for-reset` is a **`dispatch`** flag — `autopilot` stops the stage instead (exit 3) and you re-run the stage to pick up the rest. ⛔ A weekly cap with no precise reset time never auto-waits: guessing five hours there means re-hitting the wall on every wake-up, and every hit spends real quota.

**Next morning, find out what happened overnight:**

```bash
python -c "from pathlib import Path; from devloop import nightly; print(nightly.report(Path('.')))"
```

Two blocks: the isolation branches that still carry unmerged output (one `devloop/<task>-<ts> @ <sha>` per line, so the list can be piped into the next unit), and the last 24 hours of the ledger (`派单 N 次，合格 M`). ⚠️ "No records" is printed as a sentence, never as an empty string — a blank screen at 8am reads as "the reporting tool is broken", not as "nothing ran last night", and those two must stay distinguishable.

---

## What it can and cannot do (as of 2026-07-30)

⚠️ Everything under **Can** has been run for real at least once, unless the bullet says otherwise in its own words. Everything under **Not shown** has not — and it is listed rather than omitted, because a README that only lists wins is the exact failure mode this project exists to catch.

**Can:**

- **Do one job in several dependent steps.** `[stage] chain = true` (⛔ off by default) moves the next unit's starting point onto the previous unit's *accepted* commit. Without it every worktree is cut from the same base, so `needs` only orders the units — it does not let the second one see the first one's output, and a plan like "write the module → write its tests → fix what they find" simply cannot run. ⚠️ The chain point is **not** the worktree's HEAD (that anchor moves when an attack succeeds); it is the commit the orchestrator writes after every gate is green. ⛔ The cost: chained units are tied together — you cannot accept the later one and reject the earlier one — which is why a human has to switch it on. Real-run evidence (`64042ed`, stage `nightly`, 2/2 green): `chain-b`'s parent commit **is** `chain-a`, its diff is +30 lines (appended, not a rewrite), and its `report()` really calls the previous unit's `branch_lines()`.
- **Read a quota verdict off every single receipt, as a fact rather than an estimate.** ⭐ That is why the dispatch output format was changed from `json` to `stream-json --verbose`: `rate_limit_event` rides along on every run, and its `resetsAt` is an exact **unix second** (⚠️ not milliseconds — the credential file's `expiresAt` is, and mixing them up is a 1000× error). ⛔ Nothing is estimated, because the estimate is useless here: "wait about five hours" has an error bound of one whole window, and waiting 4h50m for nothing was measured. The machinery built on top: a `HaltSignal` stops the whole batch (⛔ not a raised exception — an `except Exception` somewhere upstream would swallow it), `--parallel` was rewritten to submit **in waves** so queued units can still be cancelled, the run exits `3`, and the ledger records `failure_class = "ratelimit"`. ⛔ Three independent criteria, because one is not enough: the rate-limit event, then `api_error_status == 429` on the receipt, then a whitelist of message prefixes — and never `subtype`, which is still `"success"` on a throttled receipt. ⛔ The counter-criterion matters more than the criteria: `Server is temporarily limiting requests (not your usage limit)` is **server-side capacity throttling**, unrelated to your plan; treating it as quota exhaustion parks the batch for hours over something that clears in minutes. ⛔ Reproducing this needs `--verbose` (without it the CLI exits 1 with no output) and `stdin=DEVNULL` (without it every unit waits 3s for stdin, and a run with no terminal hangs outright). ⛔ The worker environment also clears `CLAUDE_CODE_RETRY_WATCHDOG` explicitly: dispatch inherits the operator's entire environment, and with that variable set a 429 goes down the retry path instead of returning at once — the subprocess then dies at the 50-minute timeout and the failure is filed as "worker timed out", which destroys the premise the whole halt design rests on. ⚠️ **Honest scope: the halt itself has never fired.** `failure_class = "ratelimit"` appears **0 times** in the ledger; what has actually executed on real runs is the parsing path on `status: "allowed"` receipts. Everything above the parse is covered by tests only.
- ⚠️ **Backed by a red-then-green test only — no live kill-and-resume yet.** By this project's own rule ("pytest green is not evidence that a thing works"), that is *not* the same as "can". Listed here so it is visible, not to claim it.
- **Pick the chain back up after a crash.** The chain point used to live only in memory: `--resume` silently fell back to the project's HEAD, so downstream units were cut from a tree that did not contain the earlier units' output — which either fails outright or, worse, makes the worker rewrite its own version of it and **fork the output silently**. `Run.chain_head` is now persisted and read back by `resume_chain_head()`, saved the instant it advances because the process can be killed at any moment (`0e2518e`). ⚠️ Verified by a test that was red before the fix — **not** by an actual kill-and-resume of a live stage.
- **Notice a worker stepping outside its box, and keep that output out of the trunk.** After every unit: protected-file fingerprints re-checked, existing refs re-checked, and the project's live workspace compared against its pre-dispatch snapshot. ⚠️ That last one **detects**, it does not prevent. ⚠️ And until `8d9f6f4` all three ran on the manual-dispatch path only — on the autopilot path they were dead code (phase 7 row below). ⛔ Then the mirror image showed up the next day: a stray `ws_before = None`, added while fixing an unrelated `NameError`, overwrote the snapshot that had been taken earlier in the same function — so the live-workspace check became a permanent no-op on the **manual** path (`1659c49`). ⚠️ Both tests guarding it stayed green, because one passed `ws_before=` itself and the other only AST-checked that the keyword *appeared*: they verified that something was passed, never that what was passed was a real snapshot. The replacement test drives the real CLI and spies on the value `_run_unit` actually receives. Separately the gate lets a worker add `test_*.py` but fails on any edit, delete or rename of an existing test — ⛔ and on any *other* new file under `tests/`, because one autouse fixture in a new `conftest.py` can hollow out the entire suite while looking identical to a legitimate new test in `git status` (both are `??`).
- **Stop itself when it stops making progress.** The watchdog fired for real on 2026-07-29 (`023726e`, K=2, exit code 3) — ⭐ the first of the four runaway defences to have ever actually triggered.

**Not shown, or not built:**

- **Running a whole night.** ⚠️ Never attempted. The 8-hour token lifetime is no longer the blocker — measured on 2026-07-29, a token 10 hours past `expiresAt` ran fine because the CLI refreshes itself (`6b321f9`) — but "no known blocker" is not "it worked".
- **Stopping a batch on a quota wall.** The mechanism is built and tested, but ⛔ **it has never once triggered on a real run** — see the caveat on the quota bullet above. Until it does, treat "it stops itself when quota runs out" as a claim about code, not about behaviour.
- **Parallel dispatch.** Exercised in tests only. ⚠️ The `32s → 16s` figure further down was measured **before** `--parallel` was rewritten to submit in waves (`a8d6932`, so that queued units can actually be stopped when quota runs out); the wave path has not been run for real.
- **Container isolation.** Not built (Phase 8). Workers are isolated by git worktree and by tool allowlist, nothing stronger.

---

## Status

🚧 **Phases 0, 1, 2 and 5 complete; 4, 6 and 7 have their main body done but carry unverified sub-items; Phase 3 deferred.** ⚠️ "Complete" here means the critical path was exercised by a **real run**, not that the tests are green — see the legend in PLAN.md §2. Dispatch, telemetry, self-check and the acceptance gate all run end-to-end against a production codebase. A graded capability probe (L1/L3/L4), an 11-unit documentation audit, and a premium-model control arm have all been run against real repositories — and the control arm is the reason the headline cost claim below now carries a warning instead of a number.

⭐ **2026-07-29 unblocked the standing dilemma** — "unattended" and "quality" used to be an either/or: the API backend could run as a subprocess but measured worse and cost dollars, while the subagent backend needed a human to launch it. The whole basis for "a subagent cannot be a subprocess backend" turned out to be **a single OAuth token that had been expired for two months**, not a technical limit (`106a35d`). Re-logging in, the subprocess ran. That day: **11 real dispatches for $0.0082 total** — and the $0.0082 is the interesting part, because it was a single `doctor` run that silently took the old DeepSeek config instead of the registry's default backend. Everything else ran on subscription quota at **$0**. 3 real autopilot runs — 1 crash (see phase 7) and 2 that finished 2/2 green.

⚠️ **2026-07-30 was a second independent review, and it found six more blockers** (`1659c49`, `0e2518e`) — the ledger losing and corrupting lines under concurrent writes, `doctor` spending quota while both the gate and the docs called it free, the chain point vanishing on restart, and the mode gate being released by a `--dry-run` anywhere in a compound command. Each is described where it belongs below. Test suite **302 → 377** across the two days.

| Phase | What | Status |
|---|---|---|
| 0 | Channel repair + `devloop doctor` self-check | ✅ Done |
| 1 | Python core: dispatch, telemetry, prompt-cache-aware prompt assembly | ✅ Working — first real task dispatched |
| 2 | Acceptance gate with tamper protection | ✅ **Complete** — red/green double test, first write task green, all three tamper defences wired |
| 4 | First real workload on a production codebase | ✅ **Shipped, including the control arm** — 14 cheap dispatches + 11 premium subagents on byte-identical task specs. 117 pooled contradictions. **The cost comparison came out negative for the cheap route** (see below) |
| 3 | Retrieval MCP server (hybrid search, citation-enforced) | ⏸ Deferred — its stated justification was that workers would need retrieval to do real work. 11 work units then completed a real documentation audit (13 receipts including redispatches — the argument uses the unit count, not the receipt count) with nothing but `Read`/`Grep`. The premise is weakened; re-argue before building |
| 5 | Eval harness: 20 hand-verified cases pinned to a frozen baseline commit | ✅ **Done** — refuses to run if the baseline drifts. Clean baseline: premium 20/20 at $0.30, cheap 19/20 at $0.10. **The honest reading is "no measurable quality difference at n=20" (one case apart, Fisher p=1.0), not "the cheap one is worse."** The first three runs were invalidated — twice because receipts were landing *inside the graded repo*, once because a turn cap truncated three cases and the old code scored them as wrong answers |
| 6 | Non-blocking dispatch (`--detach` + `status` + `halt`) | ✅ **Done** — 7 units: 18.1 min of waiting → returns in 1s. ⛔ It does not make the work faster; it moves who waits |
| 6b | Dispatch MCP server | ⏸ **Split out and deferred.** Its stated justification was non-blocking dispatch — which `--detach` now provides. What's left (permission narrowing, cross-client access) is real but not urgent |
| 7 | Autonomous loop: constitution, stage plan, runaway defences | ✅ **Core done.** The constitution is split three ways — prose for humans (never parsed), a criteria table for scripts, and fingerprints anchored *outside* the project. ⛔ Clauses that **cannot** be machine-judged are registered explicitly, and every verdict prints "N clauses not covered" — because "no violations" without that tail is itself a false green. LangGraph deferred: 34 transitive deps against this package's one. ⚠️ **2026-07-29 — an independent audit found defences here that were marked done and had never taken effect.** `cmd_autopilot` never passed `before` / `ws_before` / `halt` into `_run_unit` (`8d9f6f4`): on the autopilot path the three T5 checks and the stop-on-quota signal were **dead code**, so the unattended mode was the only mode with no defences — manual dispatch had all of them. In the same pass, `[stage.accept] require_pass` turned out never to have been read by a single line of code — a textbook replay of the G-53 empty guard, in the file that cites G-53, with `--dry-run` reading the criteria aloud to a human while nothing enforced them. Then the **first real run crashed on the freshly-wired code** (`023726e`): the constitution snapshot was named `before`, the loop body already had `before = len(prog.done_ok)`, and round one overwrote it with an int → `'int' object has no attribute 'refs'`. ⚠️ `--dry-run` never reaches T5 and the unit tests call `_run_unit` directly, bypassing that scope — only a real run could have caught it |
| 8 | Containerised workers | Planned |

> ⚠️ **The core premise was tested and it did not hold — at this scale, on this task.**
>
> The control arm has now been run: the same 11 task specs, byte-identical, executed by Claude subagents instead of the cheap workers.
>
> | Accounting | Cheap route | Premium route | Verdict |
> |---|---|---|---|
> | Execution leg only | **$0.56** | $26.82 | cheap wins 48× |
> | Plus the decomposition work that produced the task specs ($27.67) | **$28.23** | $26.82 | **cheap loses by 5%** |
> | Plus orchestrator main-loop, review, spot-checking | not tallied, only goes up | $26.82 | **cheap loses by more** |
>
> **Break-even was unit 12 — this run was 11 units. It did not even break even.**
>
> ⚠️ These numbers were **corrected on 2026-07-27**. The first version understated every cost, because the token de-duplication kept the *first* streaming frame of each message instead of the last, zeroing out most output tokens (BACKLOG G-42). The self-check I had written for that code only ever ran against worker receipts, where first frame equals last — so it passed while covering only half the data sources. **The correction made the result worse, not better**: the first version reported "cheap wins by 4%".
>
> Quality went the other way too. The premium arm reported **98 contradictions to the cheap arm's 32**, covering 84% of the pooled set against 27%. I hand-verified 7 mechanically checkable findings that only the premium arm found: **7/7 were real.** So the extra findings are not hallucination — the cheap route simply missed most of the problems.
>
> Two things cut against this result and are stated because they are load-bearing: the decomposition cost is **one-off** and would amortise over a larger batch (how much of it is reusable infrastructure vs. per-batch work is not yet separated — that is the single biggest unknown here), and the premium arm's cost is **inflated** by subagent context overhead that the `--bare` workers don't carry, which means the real gap is *worse* for the cheap route, not better.
>
> Scope: eco-ob · documentation-vs-code audit · read-only · n=11 · first-draft task specs. **The scope is asymmetric**: doc auditing is the friendliest possible task for a decomposition strategy — cleanly separable, mechanically reviewable, orchestration tax near its theoretical floor. Failing to save money *here* generalises much further than succeeding here would have.
>
> Full write-up, including everything I can think of that attacks this conclusion: [_review/p4/RESULT.md](_review/p4/RESULT.md).

**Measured so far** (dispatch mechanism validated end-to-end):

> ⚠️ The batch table immediately below was measured on the **paid-API (DeepSeek) route, which stopped being the default on 2026-07-29**. It is kept because the accounting lesson in it is the point, not the vendor.

> ⚠️ **Receipt dollar figures are synthetic.** Every receipt's `total_cost_usd` matches Opus's price table applied to DeepSeek's token counts, exactly — Claude Code doesn't know third-party endpoint pricing and substitutes its own. Recomputed against DeepSeek's published rates (`prices.json`, fetched 2026-07-26):
>
> | Batch | Receipts | Synthetic | **Real** | Overstated by |
> |---|---|---|---|---|
> | Capability probe (Phases 1–3) | 13 | $2.38 | **$0.12** | 19.6× |
> | Documentation audit (Phase 4) | 13 | $15.22 | **$0.56** | 27.0× |
> | Adversarial design review | 15 | $5.68 | **$0.29** | 19.5× |
> | **Total** | 41 | $23.27 | **$0.98** | 23.9× |
>
> The multiplier varies with cache-hit ratio — cache reads are priced 138× too high, and they dominate. Token counts were always real; the dollars were not. **These recomputed figures are estimates from a published rate card, not a bill**: the configured model is `deepseek-v4-pro[1m]` and the rate card lists `deepseek-v4-pro`, so a long-context surcharge cannot be ruled out. See BACKLOG G-28.

> Every `$` below is the **recomputed real** figure for the paid-API route. The synthetic receipt values are kept only in the batch table above, for traceability. ⚠️ Rows dated 2026-07-29 onwards ran on the subscription backend, where the dollar figure is `0` by construction — quota is what was spent there, and the ledger says so in `price_source`. ⛔ With one exception, and it is kept visible rather than tidied away: the `doctor` run that fell back to the old DeepSeek config, `$0.0082`.

| Metric | Value |
|---|---|
| Minimum dispatch overhead | 1.2k input tokens / ~1s |
| Adversarial design review | 15 dispatches, **$0.29 real** ($0.019 each) / ~2 min each |
| **First dispatched task** (code inventory, read-only) | **$0.0073 real** / 29s / 17 turns / 7424 cached tokens |
| Gate red/green test | Green: 3 guards pass on clean checkout. Red: baseline tamper caught, exit 1 (not 2) |
| Concurrency | `--parallel N` measured at 3 units: 32s serial → 16s. ⚠️ Measured **before** `a8d6932` changed `--parallel` to submit in waves. ⭐ The wave path is now covered by an end-to-end harness that stubs `_run_unit` and counts actual dispatches across 9 budget/parallelism combinations (`tests/test_autopilot_dispatch_count_e2e.py`) — zero quota. ⛔ It is still **not** a measurement of real wall-clock speedup at scale. `--detach` returns in ~0.3s and the job survives the parent exiting (verified: a 7-unit job ran 8m47s after its launcher was gone) |
| **First write task** (gitignore edit, isolated worktree) | **$0.0070 real** / 63s / 18 turns — all 4 gates green |
| Worker error rate, first 9 dispatches | **0** — every failure traced to tooling or repo state, none to the model. That result is scoped to scripted tasks with full disclosure |
| Self-test suite | **377 tests, 0 failures** (2026-07-30; 291 two days earlier, 370 → 377 in `0e2518e` alone) plus **46/46** in `tools/test_mode_gate.py`, which is a separate runner because its test data contains the literal command strings the spend gate blocks — ⛔ inlined into Bash, the gate denies its own test. ⚠️ The gate's pytest timeout was raised 120s → 600s the same day: at 120s the margin over the real suite had shrunk from ~400× (38 tests / 0.3s, the original baseline) to ~4× (348 tests / 30s, measured that day), and a timeout is reported as "something hung" — wrong attribution, which `retries` would then burn a whole stage on. ⛔ The gate text no longer quotes any baseline number; a hard-coded baseline goes stale silently |
| **Subscription backend, first real day** (2026-07-29) | **11 dispatches · $0.0082 total.** Not `$0` and not an unknown: one `doctor` run took the stale DeepSeek worker config instead of the registry default and really spent dollars — booked as spent rather than rounded away. Every other unit ran on subscription quota at **$0** — a value, not an unknown. ⚠️ Recording it as unknown was not hypothetical: `1ff4dbb` — the ledger wrote `cost_usd_real = None` because the dispatch path passed the **model name** where the price key belonged (`__subscription__`), while the unit test that "covered" it called the pricing function directly and stayed green. Second of the six kinds of false green: implemented but never wired |
| Autopilot, first real runs (2026-07-29) | 3 runs: 1 crashed on its own freshly-wired code (`023726e`), 2 finished 2/2 green. ⭐ The crashed run then stopped correctly on the watchdog (K=2) — the first of the four runaway defences to fire for real |
| **A published measurement overturned by re-running it** | BACKLOG G-41 recorded "16 concurrent writers → 16 lines, 0 unparseable ✅ not corrupted". Re-run on 2026-07-30 (`tools/probe_ledger_concurrency.py`): that was **one** trial, with **equal-length short lines**. With realistic shapes — 8 threads × 80 lines, **unequal lengths**, since a row carrying error text is several times longer — **40 out of 40 trials lost lines and produced corrupt ones**, one of them writing bytes that are not valid UTF-8 (`read_text` itself raised). ⛔ The damage is not "some log lines missing": every runaway defence (how much spent, how many dispatched, which passed) reads **only** from the ledger, so lost lines mean the budget never tops out and accepted units get re-dispatched at real cost, while one corrupt line takes every reader down. Fixed with a thread lock + file lock + `fsync` on write, and per-line `try` on read where ⛔ a bad line **must** raise (`LedgerCorrupted`) — silently skipping it is exactly how "how much have I spent" quietly shrinks. Re-run after the fix: 3 configurations × 40 trials, zero lost, zero corrupt (`0e2518e`) |
| **A self-check that spent the thing it was checking** | `doctor --project` / `--probe` really dispatches a unit on the default backend — and `credentials.probe()` had itself recorded that one minimal call produces ~70k cache-creation tokens. Both the spend gate and the command docs listed it under "costs nothing", **and it wrote no ledger row at all**. ⚠️ It is step 1 of the overnight procedure, so every health check was quietly eating the current 5-hour window while all three runaway defences were blind to it — the night run would then start inside a window that had already been consumed. Now it needs a token like anything else, and `_record_smoke()` books it. ⚠️ The booking is wrapped in `try`: ⛔ a health check that goes red because *its own accounting* failed is worse than useless (`0e2518e`) |
| **Disk was never checked, and the number is project-dependent** | Worktrees only ever accumulated (`prune` lists, never deletes — that is deliberate), and nothing anywhere called `disk_usage`. Measured: this repo ~**4 MB** per worktree (7 of them, 28 MB over two days) but **eco-ob ~475 MB** per worktree, because the gate script also copies 107 MB of `game/.godot` in — at `max_dispatches=6` that is ~**2.8 GB for one stage**. ⛔ `check_disk()` therefore estimates from the *existing* worktrees' real size rather than a hard-coded constant — ⚠️ **except that sentence was false until 2026-08-02**: a single `if s > 0` collapsed "empty shell (0.0)", "too many entries to measure (-1.0)" and "legitimately small" into one branch, and the first two both fell back to a hard-coded 200 MB. The one time it actually fired in production (eco-ob's first unit) it was wrong: it invented 200 MB where the real figure is 251 MB, or ~361 MB after `gate_sync` copies the caches in. Now: **max** over this project's existing shells (empties and unmeasurables filtered out) → else measure the **checkout** (`git ls-files` + `synced_paths`) → else guard only the 500 MB headroom and **invent no number**. The constant is gone. ⛔ When it cannot tell, it allows the run — a capacity check must not become the thing that blocks work (`0e2518e`) |
| **Worker identity leaked into the main repo** | `worktree.py` set `git config user.name/email` inside the worktree — and a linked worktree **shares `.git/config` with the main repo**, so it rewrote the maintainer's identity and never restored it. **24 commits hand-written by the maintainer carry a worker identity** as a result. Fixed by injecting `git -c user.…` per command (`8d9f6f4`). ⚠️ Collateral: `prune` decided whether a branch was empty by author e-mail, so it broke permanently once trunk carried the worker address; the criterion is now the branch tip's commit subject plus the branch name. ⛔ History is not being rewritten — the cost of that exceeds the benefit |
| **Cross-file write task (L3)** | **$0.0163 real** / 119s / 26 turns — 5 gates green, answer-key PASS on every criterion |
| **Trap-tier task (L4)** | **$0.0143 real** / 124s / 15 turns — 5 gates green, answer-key PASS on every criterion, all 3 traps avoided |
| Capability probe | Read-only 6/6 · Write 3/3 (L1, L3, L4; **L2 was never dispatched**). No model error at any graded tier — but every graded task handed the worker a reproduction command, a hint, and a self-check recipe |
| **Defect found by this project's own discipline** | **G-26: gates green, answer key green, telemetry complete — and the output was then silently deleted.** The isolation branch never held a commit; the "branch preserved for your review" message was false. One task's output ($0.41, all criteria passed) was genuinely lost before the defect was caught. Fixed: output is committed to the branch after the gate runs — never before, or the guards go vacuous |
| **A criterion overturned three times** | "Are the subscription credentials usable?" — (1) guess from the credential file's mtime → wrong; (2) ask `claude auth status` → wrong (same machine, same second: it says `loggedIn: true` while `claude -p` returns 401 — it checks that the file exists, not that it is valid); (3) read `expiresAt` out of the file → **still wrong**. Measured 2026-07-29: a token 10 hours past `expiresAt` ran fine, and afterwards `expiresAt` had moved 8 hours forward — the CLI refreshes itself with `refreshToken`, no human in the loop, so refusing to dispatch on that basis blocks work that would have succeeded. All three were the same disease: **an indirect reading substituted for the real behaviour**. `check()` is now only a cheap elimination test that answers "definitely cannot run"; `probe()` actually starts a subprocess; and `doctor --probe` must report the two **separately whenever they disagree** (`6b321f9`) |
| **First real model error** | Found in the unscripted work package, not in the graded probe: given a claim with an eight-part scope, a worker checked two of the eight (searching for architecture class names rather than behaviours) and generalised to "consistent". It had listed 14 naming variants and written 26 cross-checks — every surface signal was green. Only an answer key caught it |
| **First self-report / artefact mismatch** | A worker's `self_check.no_line_anchors` reported `true` while its report body carried 19 forbidden line anchors. Scoring now regexes the artefact and ignores the self-report |
| Design defects found by adversarial workers | 2 critical + **7 (round 4, wording included) + 10 (round 6 onward, logic only)** — ⛔ AUDIT-LOG explicitly forbids merging those two into one number; see [AUDIT-LOG](_review/AUDIT-LOG.md) for the authoritative tally |

---

## Commands

```bash
python -m devloop.cli dispatch     --project <repo> (--task SPEC.md | --task-dir ./tasks/)
                     [--tools readonly|implement|full] [--parallel N] [--detach] [--wait-for-reset]
                     [--require-pass <gate>]... [--why-parallel]
                     ⛔ `--parallel` defaults **by `--tools`**: readonly = 4 (no worktree,
                        no gate — 7–30s per unit), writes = 1 (serial).
                        Writes going parallel must declare `# 改动范围` in each task spec,
                        non-overlapping, or dispatch refuses **before spending anything**.
                        `--why-parallel` prints the criteria and exits.
python -m devloop.cli autopilot    --project <repo> --stage <name>   # run a whole stage; [--resume] [--dry-run]
python -m devloop.cli audit        --project <repo> [--spec ID] [--kind verify|fix]
                     # see / verify / convert audit findings.
                     # ⛔ Fan-out itself still goes through `dispatch --tools readonly`;
                     #    this only covers what happens *after* they come back.
                     # ⭐ `--kind verify` generates a spec that demands the finding be
                     #    **refuted**, not confirmed — "please confirm this" always returns "yes".
python -m devloop.cli records      --project <repo> [--archive DIR]
                     # fingerprint / archive the records that cannot be regenerated.
                     # ⚠️ `.devloop/` is gitignored wholesale (`git ls-files` → 0):
                     #    the ledger, receipts and task specs in it are **gone if deleted**.
python -m devloop.cli status       --project <repo> [--job ID] [--all]   # progress, read from the ledger
python -m devloop.cli halt         --project <repo> [--kill]             # emergency stop (lists only by default)
python -m devloop.cli collect      --project <repo>                      # close out a subagent handoff batch
python -m devloop.cli eval         [--backend NAME] [--compare]          # 20 graded cases on a frozen baseline
python -m devloop.cli backends                                           # who can do the work; swap = config change
python -m devloop.cli constitution init|anchor|check --project <repo>    # ⚠️ `anchor` is a human-only command
python -m devloop.cli nightly      --project <repo>   # ⭐ the first command in the morning
                     # One screen: a **batch-level veto block** on top (gate self-failures,
                     # constitution hits, un-costable units, a stage that never reached its
                     # ending), then one line per pending branch carrying the gate verdict
                     # (`闸全绿` / `闸未过` / `未跑闸`) and the file count.
                     # ⛔ There is **no rollback step** in this project: mainline is never
                     #    touched automatically (`checkout`/`switch`/`push`/`reset` appear
                     #    zero times repo-wide, pinned by `tests/test_no_git_write_verbs.py`).
                     #    ⭐ Not merging *is* the rollback. Exit 0 = clean, 3 = needs a human.
python -m devloop.cli prune        --project <repo> [--archive DIR] [--delete] [--discard BRANCH]...
                     # list isolation branches; archive / delete / discard them.
                     # ⛔ `--delete` and `--discard` **require** `--archive`: each branch is
                     #    packed with `git bundle` (tip commit only — 2 KB measured, versus
                     #    944 KB for full history) and then **actually verified** with
                     #    `git bundle verify`. Re-verified again right before deletion.
                     #    ⚠️ "It produced a bundle" is not "the bundle is good" — the
                     #    criterion is the verify exit code, and a restore was proven in a
                     #    clean clone, not assumed.
                     # ⭐ `--delete` acts on what the **tool judges** merged; `--discard`
                     #    acts on what **you name** (the only path that removes an unmerged
                     #    branch). Merging the two switches would let a machine verdict
                     #    delete something no human looked at.
python -m devloop.cli gates        --project <repo> [--commit SHA]       # run the acceptance gate
python -m devloop.cli doctor       [--project <repo>] [--probe]          # ⛔ with either flag it really dispatches
python -m devloop.cli stats        --project <repo>                      # ledger summary
```

⛔ **Three of these spend quota**: `dispatch`, `autopilot`, and `doctor` **with `--project` or `--probe`**. All three sit behind the mode token, and all three write a ledger row. Bare `doctor`, and `stats` / `prune` / `backends` / `constitution` / `status` / `halt`, do not spend and are never gated. ⚠️ `doctor` was on the wrong side of that line until `0e2518e` — see the metrics table.

**Exit codes are part of the contract**: `0` everything succeeded · `1` at least one unit
failed · `2` the tool or its input is broken · `3` **not finished yet** (batch awaiting the
orchestrator, background job still running or dead, halt listed live jobs without killing them,
autopilot stopped on a limit, **quota exhausted with units left unsent**).

> ⛔ Quota exhaustion is `3`, never `1`. `1` means "the work was done badly"; recording
> "could not run" as "done badly" makes the caller conclude the batch failed instead of
> concluding it should be retried. ⚠️ When both are true, `3` wins — "not finished" is the
> more urgent fact.

> ⛔ `3` must never be collapsed into `0`. `0` means *all of it worked*; a script that sees `0`
> moves on — and at that moment the work may not have started. That is the most dangerous
> failure mode for the autonomous loop this is being built toward.

Flags are **not** abbreviated (`allow_abbrev=False`). Not pedantry: argparse would otherwise
accept `--det` for `--detach`, and any code filtering argv by literal string misses it —
measured, that spawned self-replicating background jobs at ~5/second doing zero work.

⚠️ `--detach` rebuilds its own argv for the background process, so **every new dispatch flag has
to be added there too**. `--wait-for-reset` was added to the parser and not to that list, so
`--detach --wait-for-reset` **silently swallowed the switch** (`8d9f6f4`). ⛔ Worse than the bug:
the invariant test that was supposed to catch exactly this had a parametrised list of flags that
did not include the new one — so it was **permanently green on it**. A guard with a blind spot is
worse than no guard, because it is trusted.

---

## Documentation

| Document | Read it when |
|---|---|
| [PLAN.md](PLAN.md) | You want the reasoning: why each technology was chosen, what was rejected and why, and the full decision record |
| [SPEC.md](SPEC.md) | You want the contracts: CLI surface, gate protocol, file formats, `.devloop/` directory spec |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Something broke. Indexed by literal error message, and it records the wrong diagnoses too |
| [BACKLOG.md](BACKLOG.md) | Known gaps, each with a stated trigger condition for when it must be fixed |
| [_review/AUDIT-LOG.md](_review/AUDIT-LOG.md) | Eight rounds with written chapters in AUDIT-LOG (⚠️ round 9's receipts exist as `_review/r_*9.json`; the chapter text is what's missing). Four more rounds — 07-27, 07-28, 07-29, 07-30 — are not folded in yet — findings, corrections, and the authoritative measurement table |

> Documents are written in Chinese, except this README. `PLAN.md` is not part of this snapshot — see the note in its place.

---

## Design principles

1. **"Done" is decided by a script, never by a model's self-report.** The gate returns 0, 1, or 2 — pass, fail, or *the gate itself is broken*. That third state matters: an environment failure must never be mistaken for bad work.
2. **The reviewer is never the implementer.** Workers implement; independent processes with clean context review. This has repeatedly caught defects that self-review missed — including in this project's own design documents.
3. **Automate the process, not the authority.** A constitution enumerates what must stop and ask a human: changing acceptance criteria, touching baselines, any commit or push, any irreversible operation. Autonomy unlocks one rung at a time, and each rung must be earned by a clean record on the previous one.
4. **State the trade-off you actually made, not the one you wish you'd made.** The dispatch path runs gates against the *working tree* of a throwaway worktree, not against a commit — because three of the guards identify the worker's changes via `git status --porcelain`, and committing first would silently turn all three into no-ops. Verifying the commit instead is strictly better in one respect (a working directory can pass every local check while the commit imports a file that was never staged) and strictly worse in another (the guards stop guarding). The commit-based path exists behind `python -m devloop.cli gates --commit`; the dispatch path deliberately does not use it. This asymmetry is tracked as an open gap, not papered over.

---

## License

MIT
