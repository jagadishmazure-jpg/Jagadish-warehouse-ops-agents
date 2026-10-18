# Ambient Warehouse Operations — Domain Notes

Distilled notes on the domain this repo models. Written in my own words;
no source material is quoted or copied.

## What an ambient warehouse optimizes

Unlike cold-chain (where temperature compliance dominates) or retail stores
(where shelf availability dominates), an ambient distribution warehouse
optimizes **travel and labor**. The building is a box; every pick is a walk
from a slot to the dock. The two levers that move cost are:

1. **Slotting** — which SKU lives in which zone. Put the fast movers near
   the dock and travel collapses.
2. **Wave planning** — how orders are grouped into pick waves. Good waves
   keep pickers dense in one zone; bad waves send them crisscrossing the
   building or overload a shift.

## ABC velocity analysis

Rank SKUs by average daily picks and split them into three classes:

- **A (fast):** the top ~20% of picks, typically a small slice of SKUs.
  These earn the golden zone (dock-adjacent).
- **B (mid):** the middle band, mid-building zones.
- **C (slow):** the long tail, back of the building.

The rule is a heuristic, not physics: seasonality and promotions move SKUs
between classes, which is why the optimizer re-runs weekly rather than once.

## Wave planning

A wave is a batch of orders released to the floor together. Planning groups
by zone first (keep the picker in one area) and priority second (P1 ships
first), then packs against a capacity ceiling. An over-capacity wave is a
labor plan that no longer fits the shift — that is why release always needs
a human confirmation.

## Inbound receiving

The dock is the front door. Late inbound starves waves; the escalation path
runs from carrier call (2 days late) to supervisor re-plan (5 days late).
Damage is segregated immediately and never put away into pick slots.

## Cycle counts

Full physical counts shut the building down, so accuracy is maintained with
cycle counts: A SKUs weekly, B monthly, C quarterly. Mis-slotted SKUs are
counted on sight because a SKU in the wrong zone is the top source of pick
errors.

## Where agents fit

The failure this repo targets is **stale slotting and brittle wave plans**:
velocity drifts, nobody re-runs the optimizer, waves get released over
capacity because the plan looked fine on paper. An agent that watches the
telemetry, proposes reslot moves with computed savings, and gates the risky
actions turns a weekly spreadsheet ritual into a continuous loop.
