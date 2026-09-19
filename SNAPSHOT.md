# About this snapshot

**This is a historical work snapshot, not a product.** DevLoop is v1 and is no
longer maintained here: no issues, no roadmap, no undertaking to fix anything.
A separate project, [nonconstant](https://github.com/HaohangXia/nonconstant), continues
the independent-verification direction without importing DevLoop code. The GitHub
repository itself is not marked with GitHub's archived-state control. Publication
maintenance (documentation, fixture minimisation and snapshot tests) does not imply
that DevLoop v1 has resumed product development.

The purpose is to make the design decisions, implementation and limitations inspectable,
not to offer a hosted service or promise ongoing compatibility with model providers.

It began as a squashed snapshot of a working repository, not a publication of
that repository's full development history.

## Who wrote what

At the original snapshot point, the working repository had 190 commits. **Not one of
those commits carried my name** —
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
- `README.md` — what was built, how to inspect the snapshot, its command surface
  and its boundaries.
- `TROUBLESHOOTING.md` — failures with their symptoms, and a column for the
  diagnoses that turned out to be wrong. That column is the useful part.
- `CLAUDE.md` — the hard constraints, including the ones added after a rule
  written in prose failed to hold.
- `tests/` — the suite. Several tests exist to prove a guard goes red, not just
  that it goes green.

## What it does not do

Read the measured cost comparison and `Boundaries` section in `README.md` before
treating this snapshot as a reusable tool. The measurement rejects the project's
original unqualified savings claim; the boundary section records what remains
host-specific, historical or unproven.

The September 2026 publication refresh adds bilingual navigation and snapshot CI, and
minimises a captured quota-event fixture. The fixture preserves contract shape and
rate-limit values; response text, identifiers, model names and usage amounts are
illustrative substitutions. This is not a new live-model measurement. CI excludes no
tests by name, but existing tests explicitly skip when their unpublished report corpus
or original local target project is absent. No private development history, provider
credentials or live host configuration is added by this refresh.
