"""Regression checks for the warehouse operations agent (synthetic data)."""

import os
import sqlite3

import pytest

from warehouse import (
    audit,
    carrier,
    demo_helpers,
    feedback,
    orchestrator,
    replenishment,
    slotting,
    sop_rag,
    telemetry,
    text2sql,
    views,
    waves,
)

SOPS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "docs", "sops")


@pytest.fixture()
def conn():
    c = telemetry.connect()
    views.bootstrap(c, seed=42)
    yield c
    c.close()


@pytest.fixture()
def planner(conn):
    return orchestrator.PlannerAgent(conn, SOPS_DIR)


# ---------------------------------------------------------------- telemetry

def test_seed_is_deterministic(conn):
    rows = conn.execute("SELECT COUNT(*) n FROM skus").fetchone()["n"]
    assert rows == 10
    zones = conn.execute("SELECT COUNT(*) n FROM zones").fetchone()["n"]
    assert zones == 4


def test_fast_mover_mis_slotted_anchor(conn):
    row = conn.execute(
        "SELECT zone_id FROM slots WHERE sku = 'SKU-FAST-01'").fetchone()
    assert row["zone_id"] == "Z-BACK"


def test_dead_stock_in_golden_zone_anchor(conn):
    row = conn.execute(
        "SELECT zone_id FROM slots WHERE sku = 'SKU-DEAD-07'").fetchone()
    assert row["zone_id"] == "Z-GOLD"


def test_over_capacity_wave_anchor(conn):
    row = conn.execute(
        "SELECT units, capacity_units, released FROM waves "
        "WHERE wave_id = 'WAVE-OVR-01'").fetchone()
    assert row["units"] == 320
    assert row["capacity_units"] == 250
    assert row["released"] == 0


def test_late_shipment_anchor(conn):
    row = conn.execute(
        "SELECT * FROM v_inbound_backlog WHERE shipment_id = 'SHIP-LATE-01'"
    ).fetchone()
    assert row["carrier"] == "GreatLakes Freight"
    assert row["days_late"] == 6


def test_labor_utilization_anchors(conn):
    under = conn.execute(
        "SELECT utilization FROM v_labor_util WHERE shift_id = 'SHIFT-UNDER-02'"
    ).fetchone()
    over = conn.execute(
        "SELECT utilization FROM v_labor_util WHERE shift_id = 'SHIFT-OVER-01'"
    ).fetchone()
    assert under["utilization"] == 0.58
    assert over["utilization"] == 1.12


def test_slotting_adherence_view_flags_both_anchors(conn):
    rows = {r["sku"]: r["mis_slotted"]
            for r in views.run_query(conn, "SELECT * FROM v_slotting_adherence")}
    assert rows["SKU-FAST-01"] == 1
    assert rows["SKU-DEAD-07"] == 1
    assert rows["SKU-FAST-02"] == 0  # sane default slotting


# ------------------------------------------------------------------- gateway

def test_gateway_rejects_delete():
    with pytest.raises(views.SecurityError) as exc:
        views.check("DELETE FROM waves")
    assert exc.value.rule_id == "SEC-SELECT-01"


def test_gateway_rejects_stacked_statements():
    with pytest.raises(views.SecurityError) as exc:
        views.check("SELECT * FROM v_wave_status; DROP TABLE waves")
    assert exc.value.rule_id == "SEC-SELECT-01"


def test_gateway_rejects_write_keyword():
    with pytest.raises(views.SecurityError) as exc:
        views.check("SELECT * FROM v_wave_status WHERE units > 0 UPDATE waves")
    assert exc.value.rule_id == "SEC-WRITE-02"


def test_gateway_rejects_unknown_identifier():
    with pytest.raises(views.SecurityError) as exc:
        views.check("SELECT * FROM waves")
    assert exc.value.rule_id == "SEC-VIEW-03"


def test_gateway_ignores_write_word_inside_string_literal():
    # 'update' inside a quoted value must not false-positive.
    assert views.check(
        "SELECT * FROM v_wave_status WHERE wave_id = 'update'") is True


def test_gateway_caps_limit(conn):
    rows = views.run_query(conn, "SELECT * FROM v_wave_status LIMIT 500")
    assert len(rows) <= views.MAX_LIMIT


def test_gateway_allows_governed_select(conn):
    rows = views.run_query(conn, "SELECT wave_id FROM v_wave_status")
    assert len(rows) == 6


# ------------------------------------------------------------------ text2sql

def test_detect_all_five_intents():
    assert text2sql.detect_intents("check slotting adherence") == ["slotting_adherence"]
    assert text2sql.detect_intents("wave progress today") == ["wave_progress"]
    assert text2sql.detect_intents("inbound backlog at the dock") == ["inbound_backlog"]
    assert text2sql.detect_intents("labor utilization by shift") == ["labor_utilization"]
    assert text2sql.detect_intents("which SKUs are mis-slotted") == [
        "slotting_adherence", "misplaced_skus"]


def test_run_misplaced_returns_anchors(conn):
    result = text2sql.run(conn, "misplaced_skus")
    skus = {r["sku"] for r in result["rows"]}
    assert {"SKU-FAST-01", "SKU-DEAD-07"} <= skus


def test_run_question_first_intent_wins(conn):
    result = text2sql.run_question(conn, "show wave progress")
    assert result["intent"] == "wave_progress"
    assert len(result["rows"]) == 6


def test_unknown_intent_raises(conn):
    with pytest.raises(text2sql.UnknownIntent):
        text2sql.run_question(conn, "quantum flux capacitor policy")


def test_simplified_retry_on_db_error(conn, monkeypatch):
    # First query attempt fails; the simplified retry must still succeed.
    calls = {"n": 0}
    real_run_query = views.run_query

    def flaky(c, sql, params=()):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.Error("boom")
        return real_run_query(c, sql, params)

    monkeypatch.setattr(views, "run_query", flaky)
    result = text2sql.run(conn, "wave_progress")
    assert "simplified retry" in result["sql"]
    assert len(result["rows"]) == 6


def test_escalation_when_retry_fails(conn):
    conn.execute("DROP VIEW v_wave_status")
    conn.execute("DROP TABLE waves")
    with pytest.raises(text2sql.EscalationNeeded):
        text2sql.run(conn, "wave_progress")


# ------------------------------------------------------------------ slotting

def test_optimizer_recommends_fast_mover_forward(conn):
    recs = slotting.recommend_moves(conn)
    assert len(recs) == 1
    top = recs[0]
    assert top["sku"] == "SKU-FAST-01"
    assert top["from_zone"] == "Z-BACK"
    assert top["to_zone"] == "Z-GOLD"
    # 45 picks/day * (180 - 12) m * 2 (round trip)
    assert top["saving_m_per_day"] == 45.0 * (180.0 - 12.0) * 2.0


def test_optimizer_skips_move_that_increases_travel(conn):
    # SKU-DEAD-07 is mis-slotted but moving it back would add travel;
    # the optimizer must not recommend it.
    recs = slotting.recommend_moves(conn)
    assert all(r["sku"] != "SKU-DEAD-07" for r in recs)


def test_plan_summary_totals(conn):
    recs = slotting.recommend_moves(conn)
    summary = slotting.plan_summary(recs)
    assert summary["moves"] == 1
    assert summary["total_saving_m_per_day"] == 15120.0
    assert summary["total_labor_hours"] == 0.5
    assert summary["requires_gate"] is False


def test_plan_summary_empty():
    summary = slotting.plan_summary([])
    assert summary == {"moves": 0, "total_saving_m_per_day": 0,
                      "total_labor_hours": 0, "requires_gate": False}


# --------------------------------------------------------------------- waves

def _order(oid, zone, prio, units):
    return {"order_id": oid, "zone_id": zone, "priority": prio, "units": units}


def test_plan_waves_groups_by_zone_and_priority():
    orders = [_order("O1", "Z-GOLD", "P1", 100), _order("O2", "Z-GOLD", "P1", 100),
              _order("O3", "Z-MID", "P1", 100), _order("O4", "Z-GOLD", "P2", 50)]
    result = waves.plan_waves(orders)
    keys = {(w["zone_id"], w["priority"]) for w in result}
    assert keys == {("Z-GOLD", "P1"), ("Z-MID", "P1"), ("Z-GOLD", "P2")}
    assert sum(w["orders"] for w in result) == 4


def test_plan_waves_splits_over_capacity():
    orders = [_order("O1", "Z-GOLD", "P1", 200), _order("O2", "Z-GOLD", "P1", 200)]
    result = waves.plan_waves(orders, wave_capacity=250)
    assert len(result) == 2
    assert all(w["units"] == 200 for w in result)
    assert not any(w["over_capacity"] for w in result)


def test_plan_waves_flags_single_oversize_order():
    orders = [_order("O1", "Z-GOLD", "P1", 400)]
    result = waves.plan_waves(orders, wave_capacity=250)
    assert len(result) == 1
    assert result[0]["over_capacity"] is True


def test_plan_waves_rejects_bad_orders():
    with pytest.raises(ValueError):
        waves.plan_waves([{"order_id": "O1"}])
    with pytest.raises(ValueError):
        waves.plan_waves([_order("O1", "Z-GOLD", "P9", 10)])
    with pytest.raises(ValueError):
        waves.plan_waves([_order("O1", "Z-GOLD", "P1", 0)])


def test_wave_release_always_gated():
    assert waves.release_requires_gate({"wave_id": "W1"}) is True


# ------------------------------------------------------------------- carrier

def test_fake_carrier_deterministic():
    a = carrier.FakeCarrier().get_signal("GreatLakes Freight", "SHIP-LATE-01")
    b = carrier.FakeCarrier().get_signal("GreatLakes Freight", "SHIP-LATE-01")
    assert a == b
    assert a["ok"] is True
    assert 0 <= a["delay_days"] <= 3


def test_live_carrier_gap_on_unreachable():
    backend = carrier.LiveCarrierBackend("http://127.0.0.1:9/", timeout=1)
    signal = backend.get_signal("GreatLakes Freight", "SHIP-LATE-01")
    assert signal["ok"] is False
    assert "gap" in signal
    assert signal["delay_days"] is None


# ------------------------------------------------------------------- sop rag

def test_sop_retrieval_cites_wave_release():
    index = sop_rag.SopIndex(sop_rag.load_chunks(SOPS_DIR))
    answer = index.answer("how do I release a pick wave")
    assert answer["found"] is True
    assert any("sop-03-wave-picking.md" in c for c in answer["citations"])


def test_sop_explicit_not_found():
    index = sop_rag.SopIndex(sop_rag.load_chunks(SOPS_DIR))
    answer = index.answer("quantum flux capacitor maintenance policy")
    assert answer["found"] is False
    assert answer["citations"] == []
    assert "could not find" in answer["message"]


# -------------------------------------------------------------- orchestrator

def test_ask_misplaced_fans_out_and_tiers(planner):
    result = planner.ask("Which SKUs are mis-slotted?")
    assert "misplaced_skus" in result["intents"]
    assert "SKU-FAST-01" in result["answer"]
    assert result["tier"] == "T2"  # fast mover mis-slotted
    assert result["audit_events"] > 0


def test_ask_wave_over_capacity_tier(planner):
    result = planner.ask("Show me wave progress")
    assert result["tier"] == "T4"  # WAVE-OVR-01 at 128% of capacity


def test_ask_inbound_late_tier(planner):
    result = planner.ask("What is the inbound backlog?")
    assert result["tier"] == "T3"  # SHIP-LATE-01 is 6 days late
    assert result["carrier"] is not None
    assert result["carrier"]["ok"] is True


def test_ask_multi_intent_fan_out(planner):
    result = planner.ask("Show wave progress and the inbound backlog")
    assert set(result["intents"]) == {"wave_progress", "inbound_backlog"}
    assert set(result["row_counts"]) == {"wave_progress", "inbound_backlog"}


def test_ask_reslot_plan_no_gate_for_small_plan(planner):
    result = planner.ask("Recommend reslot moves for the building")
    recs, summary = result["reslot_plan"]
    assert summary["moves"] == 1
    assert result["gate_token"] is None  # 0.5 labor-hours < 8h gate


def test_ask_wave_release_gated(planner):
    result = planner.ask("Please release wave WAVE-OVR-01")
    assert result["gate_token"] is not None
    gate = planner.gate.get(result["gate_token"])
    assert gate["action"] == "wave_release"
    assert gate["status"] == "pending"


def test_ask_unknown_question_falls_back(planner):
    result = planner.ask("quantum flux capacitor policy")
    assert result["intents"] == []
    assert "couldn't map" in result["answer"]
    assert result["tier"] == "T1"


def test_gate_approve_deny_flow(planner):
    token = planner.gate.request("wave_release", "release it", {})
    planner.gate.approve(token)
    assert planner.gate.get(token)["status"] == "approved"
    token2 = planner.gate.request("wave_release", "release it", {})
    planner.gate.deny(token2, reason="no labor")
    assert planner.gate.get(token2)["reason"] == "no labor"
    with pytest.raises(KeyError):
        planner.gate.approve("GATE-9999")


# ------------------------------------------------------------- replenishment

def test_tier_assessment_matrix():
    over = [{"units": 320, "capacity_units": 250, "velocity_class": "B"}]
    assert replenishment.assess_tier(over, [], []) == "T4"
    mild = [{"units": 260, "capacity_units": 250, "velocity_class": "B"}]
    assert replenishment.assess_tier(mild, [], []) == "T3"
    late = [{"days_late": 6}]
    assert replenishment.assess_tier([], late, []) == "T3"
    mis = [{"velocity_class": "A"}]
    assert replenishment.assess_tier([], [], mis) == "T2"
    assert replenishment.assess_tier([], [], []) == "T1"


def test_requires_gate_thresholds():
    assert replenishment.ConfirmationGate.requires_gate("reslot", 9.0) is True
    assert replenishment.ConfirmationGate.requires_gate("reslot", 1.0) is False
    assert replenishment.ConfirmationGate.requires_gate("wave_release") is True
    assert replenishment.ConfirmationGate.requires_gate("nope", 99) is False


# --------------------------------------------------------------------- audit

def test_audit_chain_verifies():
    log = audit.AuditLog()
    log.record("planner", "question_received", {"q": "hi"})
    log.record("planner", "answer_synthesized", {"tier": "T1"})
    assert log.verify_chain() is True
    assert log.events()[1]["prev_hash"] == log.events()[0]["hash"]


def test_audit_tamper_detected():
    log = audit.AuditLog()
    log.record("planner", "question_received", {"q": "hi"})
    log.events()[0]["details"]["q"] = "tampered"
    assert log.verify_chain() is False


# ------------------------------------------------------------------ feedback

def test_feedback_captured_with_pii_redacted():
    log = audit.AuditLog()
    event = feedback.capture_feedback(
        log, session_id="S1", turn_id="T1", rating="down",
        correction="wrong, email me at jane@example.com or 555-123-4567")
    details = event["details"]
    assert details["rating"] == "down"
    assert "[EMAIL]" in details["correction"]
    assert "[NUMBER]" in details["correction"]
    assert details["pii_redactions"] == 2
    assert "jane@example.com" not in details["correction"]
    assert log.verify_chain() is True


def test_feedback_validation():
    log = audit.AuditLog()
    with pytest.raises(feedback.FeedbackError):
        feedback.capture_feedback(log, session_id="S1", turn_id="T1",
                                  rating="maybe")
    with pytest.raises(feedback.FeedbackError):
        feedback.capture_feedback(log, session_id="S1", turn_id="T1",
                                  rating="down", correction="   ")
    with pytest.raises(feedback.FeedbackError):
        feedback.capture_feedback(log, session_id="  ", turn_id="T1",
                                  rating="up")
    with pytest.raises(feedback.FeedbackError):
        feedback.capture_feedback(log, session_id="S1", turn_id="T1",
                                  rating="down", correction="x" * 2001)
    # thumbs-up without correction is fine
    event = feedback.capture_feedback(log, session_id="S1", turn_id="T1",
                                      rating="up")
    assert event["details"]["correction"] == ""


def test_feedback_tamper_breaks_chain():
    log = audit.AuditLog()
    feedback.capture_feedback(log, session_id="S1", turn_id="T1", rating="up")
    log.events()[0]["details"]["rating"] = "down"
    assert log.verify_chain() is False


def test_list_feedback_filters_chain():
    log = audit.AuditLog()
    log.record("planner", "question_received", {})
    feedback.capture_feedback(log, session_id="S1", turn_id="T1", rating="up")
    assert len(feedback.list_feedback(log)) == 1


# -------------------------------------------------------------- demo helpers

def test_format_answer_and_gates(planner):
    result = planner.ask("Please release wave WAVE-OVR-01")
    text = demo_helpers.format_answer(result)
    assert result["gate_token"] in text
    assert "Sources:" in text or "Escalation tier" in result["answer"]
    pending = demo_helpers.pending_gates(planner.gate)
    assert len(pending) == 1
    payload = demo_helpers.gate_action_payload(planner.gate, result["gate_token"])
    assert payload["action"] == "wave_release"
    assert demo_helpers.gate_action_payload(planner.gate, "GATE-9999") is None


def test_recent_audit_rows(planner):
    planner.ask("Show me wave progress")
    rows = demo_helpers.recent_audit_rows(planner.audit, n=5)
    assert 0 < len(rows) <= 5
    assert all({"seq", "actor", "action", "hash"} <= set(r) for r in rows)


def test_format_reslot_plan(conn):
    recs = slotting.recommend_moves(conn)
    summary = slotting.plan_summary(recs)
    text = demo_helpers.format_reslot_plan(recs, summary)
    assert "SKU-FAST-01" in text
    assert "15,120" in text
