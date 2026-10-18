# Threat Model

Lightweight STRIDE-style review for the warehouse operations agent.

## Assets
- Telemetry database (operational picture of the warehouse).
- Audit chain (tamper-evident record of planner decisions).
- Confirmation gates (the human-approval boundary for reslots and releases).

## Threats and mitigations

| # | Threat | Mitigation |
|---|--------|------------|
| 1 | Prompt injection smuggles SQL writes (spoofing the planner's intent) | Gateway enforces SELECT-only with cited rule IDs; literals stripped before scanning; identifier allowlist limited to 4 views |
| 2 | Tampering with audit history to hide a bad release | SHA-256 hash chain; `verify_chain()` fails on any alteration (tested) |
| 3 | Hallucinated carrier ETA drives a bad labor plan | Live backend degrades to an explicit gap; planner reports the gap |
| 4 | PII in feedback corrections persists in the append-only log | `redact_correction()` replaces emails and 10+ digit numbers before write; over-length corrections rejected |
| 5 | Over-capacity wave released without oversight | Every wave release mints a confirmation gate; over-capacity waves escalate to T3/T4 |
| 6 | Runaway reslot plan churns the night shift | Plans above 8 labor-hours need a confirmation gate; optimizer output is advisory until approved |
| 7 | Stale velocity classes mis-slot the building | Documented limitation: classes are static in the synthetic data; production must re-derive from pick history (see implementation guide) |

## Out of scope
- Network security of the deployment, WMS access controls, and secrets
  management are deployment concerns, not modeled here.
- The Streamlit demo is explicitly a demo: no auth, in-memory gates.
