"""ReAct-style planner orchestrating warehouse tools.

Loop: reason (detect intents, possibly several -> fan-out) -> act (run each
tool, audited) -> synthesize (cited answer + escalation tier + optional
confirmation-gate token for high-impact actions). Every step is audit-logged.

High-impact actions: reslot plans above the labor gate and every wave release
are gated behind human confirmation.
"""

import re

from . import audit as audit_mod
from . import carrier as carrier_mod
from . import replenishment as repl
from . import slotting, sop_rag, text2sql

_RESLOT_RE = re.compile(r"\bre[-\s]?slot\b|reassign (slot|zone)|move sku", re.I)
_RELEASE_WAVE_RE = re.compile(r"release (the )?wave|release wave", re.I)


class PlannerAgent:
    def __init__(self, conn, sops_dir, carrier_backend=None):
        self.conn = conn
        self.audit = audit_mod.AuditLog()
        self.gate = repl.ConfirmationGate()
        self.sops = sop_rag.SopIndex(sop_rag.load_chunks(sops_dir))
        self.carrier = carrier_backend or carrier_mod.FakeCarrier()

    # -- tool-facing helpers -------------------------------------------------
    def _tool_telemetry(self, intent):
        result = text2sql.run(self.conn, intent)
        self.audit.record("planner", "tool.telemetry", {
            "intent": intent, "sql": result["sql"], "rows": len(result["rows"]),
        })
        return result

    def _tool_sop(self, question):
        answer = self.sops.answer(question)
        self.audit.record("planner", "tool.sop", {
            "found": answer["found"], "citations": answer["citations"],
        })
        return answer

    def _tool_carrier(self, carrier, shipment_id):
        signal = self.carrier.get_signal(carrier, shipment_id)
        self.audit.record("planner", "tool.carrier", {
            "carrier": carrier, "shipment_id": shipment_id,
            "ok": signal.get("ok"), "delay_days": signal.get("delay_days"),
        })
        return signal

    def _tool_slotting(self):
        recs = slotting.recommend_moves(self.conn)
        summary = slotting.plan_summary(recs)
        self.audit.record("planner", "tool.slotting", summary)
        return recs, summary

    # -- main entry point ----------------------------------------------------
    def ask(self, question):
        self.audit.record("planner", "question_received", {"question": question})
        intents = text2sql.detect_intents(question)

        results, wave_rows, inbound_rows, misplaced_rows = {}, [], [], []
        for intent in intents:  # multi-intent fan-out
            try:
                result = self._tool_telemetry(intent)
            except text2sql.EscalationNeeded:
                self.audit.record("planner", "telemetry.escalated", {"intent": intent})
                continue
            results[intent] = result
            if intent == "wave_progress":
                wave_rows = result["rows"]
            if intent == "inbound_backlog":
                inbound_rows = result["rows"]
            if intent == "misplaced_skus":
                misplaced_rows = result["rows"]

        sop_answer = self._tool_sop(question)

        carrier_signal = None
        if inbound_rows:
            top = inbound_rows[0]
            carrier_signal = self._tool_carrier(top["carrier"], top["shipment_id"])

        reslot_plan = None
        if _RESLOT_RE.search(question):
            reslot_plan = self._tool_slotting()

        tier = repl.assess_tier(wave_rows, inbound_rows, misplaced_rows)
        gate_token = self._maybe_gate(question, reslot_plan)

        answer, citations = self._synthesize(
            question, intents, results, sop_answer, carrier_signal,
            reslot_plan, tier, gate_token,
        )
        self.audit.record("planner", "answer_synthesized", {
            "tier": tier, "gate_token": gate_token, "citations": citations,
        })
        return {
            "question": question,
            "intents": intents,
            "row_counts": {k: len(v["rows"]) for k, v in results.items()},
            "answer": answer,
            "citations": citations,
            "tier": tier,
            "gate_token": gate_token,
            "carrier": carrier_signal,
            "reslot_plan": reslot_plan,
            "audit_events": len(self.audit.events()),
        }

    def _maybe_gate(self, question, reslot_plan):
        if reslot_plan is not None:
            _recs, summary = reslot_plan
            if repl.ConfirmationGate.requires_gate(
                    "reslot", summary["total_labor_hours"]):
                token = self.gate.request(
                    "reslot",
                    f"Reslot plan: {summary['moves']} moves, "
                    f"{summary['total_labor_hours']:.1f} labor-hours",
                    summary,
                )
                self.audit.record("planner", "gate.requested",
                                  {"token": token, **summary})
                return token
        if _RELEASE_WAVE_RE.search(question):
            token = self.gate.request(
                "wave_release", "Wave release requested by planner",
                {"question": question},
            )
            self.audit.record("planner", "gate.requested", {"token": token})
            return token
        return None

    def _synthesize(self, question, intents, results, sop_answer,
                    carrier_signal, reslot_plan, tier, gate_token):
        lines, citations = [], []
        if not intents and not sop_answer["found"] and reslot_plan is None:
            return ("I couldn't map that to a warehouse question or a warehouse "
                    "SOP. Try asking about slotting, waves, inbound shipments, "
                    "labor, or reslotting."), []
        for intent, result in results.items():
            lines.append(f"[{intent}] {len(result['rows'])} row(s) from governed views.")
            if intent == "misplaced_skus" and result["rows"]:
                worst = result["rows"][0]
                lines.append(
                    f"Worst mis-slot: {worst['sku_name']} ({worst['sku']}), "
                    f"velocity {worst['velocity_class']} at "
                    f"{worst['avg_daily_picks']} picks/day, sitting in "
                    f"{worst['zone_name']} ({worst['distance_to_dock']} m from dock)."
                )
            if intent == "wave_progress" and result["rows"]:
                over = [r for r in result["rows"]
                        if r["units"] > r["capacity_units"]]
                if over:
                    lines.append(
                        f"Over-capacity waves: {len(over)} -- worst is "
                        f"{over[0]['wave_id']} at "
                        f"{over[0]['utilization']:.0%} utilization."
                    )
        if reslot_plan is not None:
            recs, summary = reslot_plan
            lines.append(
                f"Slotting optimizer: {summary['moves']} recommended move(s), "
                f"{summary['total_saving_m_per_day']:,.0f} m/day travel saved, "
                f"{summary['total_labor_hours']:.1f} labor-hours."
            )
            if recs:
                top = recs[0]
                lines.append(
                    f"Top move: {top['sku_name']} {top['from_zone']} -> "
                    f"{top['to_zone']}"
                    + (f" (swap with {top['swap_with']})" if top.get("swap_with") else "")
                    + f" saves {top['saving_m_per_day']:,.0f} m/day net."
                )
        if sop_answer["found"]:
            lines.append(sop_answer["message"])
            for excerpt in sop_answer["excerpts"]:
                lines.append(f"- {excerpt['citation']}: {excerpt['text'][:200]}")
            citations.extend(sop_answer["citations"])
        if carrier_signal:
            if carrier_signal.get("ok"):
                lines.append(
                    f"Carrier signal for {carrier_signal['shipment_id']} "
                    f"({carrier_signal['carrier']}): "
                    f"{carrier_signal['status']} "
                    f"({carrier_signal['delay_days']} day(s) delay)."
                )
            else:
                lines.append(
                    f"Carrier signal gap: {carrier_signal.get('gap')} -- "
                    "answered without external carrier data."
                )
        lines.append(f"Escalation tier: {tier}.")
        if gate_token:
            lines.append(
                f"High-impact action gated behind human confirmation: {gate_token}. "
                "Approve or deny it in the demo before anything executes."
            )
        return "\n".join(lines), citations
