# Interview Points

Talking points for discussing this repo in an interview. Every claim below
is backed by a test in `tests/test_warehouse.py`.

## The problem (30 seconds)
"Ambient warehouses lose money to walking. Fast-moving SKUs drift into the
back of the building, waves get released over capacity, and late inbound
starves the pick plan. This repo is an agent that watches warehouse
telemetry, proposes slotting fixes with computed travel savings, and gates
the risky actions behind human confirmation."

## Architecture decisions
- **Rule-based planner, not an LLM call**: five governed intents map to
  fixed SELECT templates. Deterministic, testable, and the SQL can never
  escape the sandbox (gateway rule IDs SEC-SELECT-01/02/03).
- **Read-only views as the trust boundary**: the planner never sees base
  tables. String literals are stripped before the write-keyword scan so
  quoted values can't false-positive.
- **Optimizers as tools, not chat**: the ABC slotting optimizer and the
  wave planner are pure functions with computed numbers (meters/day saved,
  over-capacity flags), invoked by the planner and audited like any tool.
- **Explicit gaps**: the carrier feed degrades to a stated gap, never a
  hallucinated ETA. The SOP retriever says "I could not find this" below
  threshold instead of improvising policy.

## The differentiator
"Most warehouse demos stop at dashboards. This one computes reslot moves
from ABC velocity classes with a per-move travel-saving estimate, and packs
waves against capacity with an over-capacity flag that blocks release."

## Numbers to quote
- Test suite: run `python -m pytest tests/ -q` (count is in the README).
- Anchor example: SKU-FAST-01 does 45 picks/day from 180 m out; moving it
  to the 12 m golden zone saves a computed number of meters per day
  (see the optimizer output in the README).

## Honest limits
- Telemetry is synthetic; velocity classes are static, not re-derived from
  history. Labor cost is a flat per-move constant.
- The carrier live backend is a stubbed interface; the fake backend is
  what tests exercise.
