"""Escalation tiers (T1-T4) and human confirmation gates.

Tiers mirror docs/sops/sop-03-wave-picking.md. High-impact actions -- a reslot
plan whose labor exceeds RESLOT_GATE_HOURS and every wave release -- are
gated: the planner mints a gate token and a human must approve/deny before
anything executes.
"""

RESLOT_GATE_HOURS = 8.0


def assess_tier(wave_rows=(), inbound_rows=(), misplaced_rows=()):
    """Worst-case tier across wave, inbound, and slotting signals."""
    over = [r for r in wave_rows if r["units"] > r["capacity_units"]]
    if over and max(r["units"] / r["capacity_units"] for r in over) > 1.2:
        return "T4"
    if over:
        return "T3"
    late = [r.get("days_late", 0) or 0 for r in inbound_rows]
    if late and max(late) >= 5:
        return "T3"
    fast_misplaced = [r for r in misplaced_rows
                      if r.get("velocity_class") == "A"]
    if fast_misplaced:
        return "T2"
    if wave_rows or inbound_rows or misplaced_rows:
        return "T2"
    return "T1"


class ConfirmationGate:
    """In-memory approve/deny ledger for high-impact actions."""

    def __init__(self):
        self._pending = {}
        self._decided = {}
        self._seq = 0

    @staticmethod
    def requires_gate(action_type, value=0.0):
        if action_type == "reslot":
            return value > RESLOT_GATE_HOURS
        if action_type == "wave_release":
            return True
        return False

    def request(self, action_type, summary, payload=None):
        self._seq += 1
        token = f"GATE-{self._seq:04d}"
        self._pending[token] = {
            "token": token,
            "action": action_type,
            "summary": summary,
            "payload": payload or {},
            "status": "pending",
        }
        return token

    def approve(self, token):
        return self._decide(token, "approved")

    def deny(self, token, reason=""):
        return self._decide(token, "denied", reason)

    def _decide(self, token, status, reason=""):
        if token not in self._pending:
            raise KeyError(f"unknown or already-decided gate token: {token}")
        record = self._pending.pop(token)
        record["status"] = status
        record["reason"] = reason
        self._decided[token] = record
        return record

    def pending(self):
        return [dict(v) for v in self._pending.values()]

    def decided(self):
        return [dict(v) for v in self._decided.values()]

    def get(self, token):
        if token in self._pending:
            return dict(self._pending[token])
        return dict(self._decided.get(token, {})) or None
