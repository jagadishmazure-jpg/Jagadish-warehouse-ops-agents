# Implementation Guide

How to adopt this repo's patterns in a real warehouse operation.

## 1. Swap the telemetry source
`warehouse/telemetry.py` seeds a synthetic SQLite database. In production,
replace `seed_demo_data` with a read replica of the WMS: keep the same
table shapes (`skus`, `zones`, `slots`, `inbound`, `waves`, `labor`) or
rewrite the four views in `views.py` to match your schema. The gateway,
intents, and optimizer only depend on the views.

## 2. Calibrate the slotting model
- Recompute `avg_daily_picks` from real pick history (trailing 30 days).
- Tune `LABOR_HOURS_PER_MOVE` from your time studies; the 0.5 h default is
  a placeholder.
- Set `GATE_LABOR_HOURS` to your shift supervisor's approval threshold.

## 3. Wire the carrier feed
Implement `LiveCarrierBackend` against your TMS tracking API. Keep the
gap contract: any failure returns `ok: False` with a `gap` string, and the
planner must surface the gap rather than invent an ETA.

## 4. Put the gates in the workflow
`ConfirmationGate` is in-memory. Persist pending tokens (e.g. a `gates`
table) and surface approve/deny in the WMS UI or the Streamlit demo before
any reslot move or wave release executes.

## 5. Close the feedback loop
`feedback.py` writes PII-redacted corrections into the audit chain. Mine
thumbs-down corrections weekly into golden eval cases (see the
`Jagadish-agent-feedback-loop` repo pattern) and gate prompt/config changes
on them.

## 6. Run the optimizer on a schedule
The slotting optimizer is cheapest as a weekly batch (Sunday night shift),
not per question. The planner's reslot path is for ad-hoc "what-if" runs.
