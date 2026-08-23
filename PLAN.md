# PLAN.md — not in this snapshot

The planning document is not published here, and this file exists so the twenty
or so links pointing at it do not simply 404.

**Why it is held back.** `PLAN.md` is the working plan, not a design document.
Alongside the architecture it carries the author's own scheduling, priorities and
personal reasons for choosing one thing over another — including a section whose
stated criterion for adopting a technology is external demand rather than whether
the project needs it. That is a legitimate way to plan and a poor thing to
publish: a reader would be looking at motives, not at the system.

**What you lose.** Section numbers referenced elsewhere (`§0.1`, `§14`, and
others) resolve to nothing. Where a claim in `SPEC.md` or `README.md` cites
`PLAN.md` as its source, take it as unsourced until it is published.

**What you do not lose.** Every mechanism `PLAN.md` describes is either
implemented in `devloop/` or specified in `SPEC.md`, both of which are here.
`SPEC.md` is the contract and `tests/test_docs_in_sync.py` keeps it honest
against the code; `PLAN.md` never had that property. Where the two disagreed,
the code was always the answer.
