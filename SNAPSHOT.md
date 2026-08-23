# About this snapshot

**This is a work archive, not a product.** DevLoop is v1 and it is archived: no
issues, no roadmap, no undertaking to fix anything. A successor exists and is
where the maintenance goes.

The distinction is deliberate and it was the whole reason this was not published
earlier — releasing something *as a product* carries a maintenance obligation,
and publishing it *as a record of work* carries roughly none. This is the second
thing. Read it, take what is useful, expect nothing.

It is a single-commit snapshot of a working repository, not its history.

## Who wrote what

The working repository has 190 commits. **Not one of them carries my name** —
every author field reads `devloop-worker`, because that is the identity the
workers commit under, and the workers wrote most of the text you are reading,
including large parts of `README.md`.

What is mine is the part that does not show up in an author field: the design,
the criteria, the rejections, and the number-checking. Where a document says a
measurement was re-run and a previous conclusion was overturned, I am the one
who asked for the re-run and read the result.

Two consequences worth stating plainly:

- **First person in these documents is usually not me.** A line like "I
  misjudged which route to take four times in one day" was written by an agent
  about itself. Read `I` as the worker unless the sentence is quoting me, in
  which case it is marked.
- **A worktree leak makes this unfixable in the history anyway.** Worktrees
  shared `.git/config` for a period, so around two dozen commits I typed by hand
  also carry a worker identity. I did not rewrite history to correct it: the
  rewrite costs more than the confusion it removes, and this note is cheaper
  than either.

## Why a snapshot and not the repository

The working repository carries planning notes and internal review material that
are about me rather than about the tool. Rather than publish those or spend a
week redacting them, this is the tool and its documentation, taken at a point in
time. `PLAN.md` is not included — the file in its place explains what is missing
and what that costs you.

## What is here, and what it is worth

- `devloop/` — the package. This is the answer to any question the docs get wrong.
- `SPEC.md` — the contract: subcommands, exit codes, the gate protocol, file
  formats. `tests/test_docs_in_sync.py` fails the build when it drifts from the
  code, which is a property none of the other documents have.
- `README.md` — how to make it do work, and what it will not do.
- `TROUBLESHOOTING.md` — failures with their symptoms, and a column for the
  diagnoses that turned out to be wrong. That column is the useful part.
- `CLAUDE.md` — the hard constraints, including the ones added after a rule
  written in prose failed to hold.
- `tests/` — the suite. Several tests exist to prove a guard goes red, not just
  that it goes green.

## What it does not do

Read the `⛔ What this is not` block at the top of `README.md` before anything
else. It kills the reason this project was originally started, using the
project's own measurements. That block is not modesty; it is the current state.
