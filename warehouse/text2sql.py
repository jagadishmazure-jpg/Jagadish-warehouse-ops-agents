"""Rule-based NL -> SQL over the read-only warehouse views.

Five planner intents, each mapped to a fixed parameterized SELECT template over
the view allowlist. The gateway in views.py enforces SELECT-only, identifier
allowlist, and the LIMIT cap, so generated SQL can never escape the sandbox.

On a database error the planner retries once with a simplified query
(``SELECT * FROM <view> LIMIT 20``) before raising EscalationNeeded.
"""

import re
import sqlite3

from . import views

SLOTTING_SQL = (
    "SELECT sku, sku_name, velocity_class, avg_daily_picks, slot_id, zone_id, "
    "zone_name, distance_to_dock, zone_rank, ideal_rank, mis_slotted "
    "FROM v_slotting_adherence ORDER BY avg_daily_picks DESC LIMIT 50"
)
WAVE_SQL = (
    "SELECT wave_id, zone_id, zone_name, shift_id, orders, units, "
    "capacity_units, utilization, released "
    "FROM v_wave_status ORDER BY utilization DESC LIMIT 50"
)
INBOUND_SQL = (
    "SELECT shipment_id, carrier, eta_day, arrived_day, status, lines, days_late "
    "FROM v_inbound_backlog ORDER BY days_late DESC LIMIT 50"
)
LABOR_SQL = (
    "SELECT shift_id, zone_id, zone_name, workers, picks_done, picks_target, "
    "utilization FROM v_labor_util ORDER BY utilization DESC LIMIT 50"
)
MISPLACED_SQL = (
    "SELECT sku, sku_name, velocity_class, avg_daily_picks, slot_id, zone_id, "
    "zone_name, distance_to_dock, zone_rank, ideal_rank "
    "FROM v_slotting_adherence WHERE mis_slotted = 1 "
    "ORDER BY avg_daily_picks DESC LIMIT 50"
)

_TEMPLATES = {
    "slotting_adherence": ("v_slotting_adherence", SLOTTING_SQL),
    "wave_progress": ("v_wave_status", WAVE_SQL),
    "inbound_backlog": ("v_inbound_backlog", INBOUND_SQL),
    "labor_utilization": ("v_labor_util", LABOR_SQL),
    "misplaced_skus": ("v_slotting_adherence", MISPLACED_SQL),
}

_INTENT_PATTERNS = [
    ("slotting_adherence", re.compile(r"slot|slotting|putaway|put\s*-?\s*away|adherence", re.I)),
    ("wave_progress", re.compile(r"\bwave\b|waves\b|picking wave|batch", re.I)),
    ("inbound_backlog", re.compile(r"inbound|receiving|shipment|backlog|dock|carrier", re.I)),
    ("labor_utilization", re.compile(r"labor|labour|staff|shift|utili[sz]ation|workers", re.I)),
    ("misplaced_skus", re.compile(r"mis[-\s]?slot|misplaced|wrong (zone|slot)|travel", re.I)),
]


class UnknownIntent(Exception):
    """The question matched no governed intent."""


class EscalationNeeded(Exception):
    """Even the simplified retry failed; a human must take over."""


def detect_intents(question):
    """Return every matching intent name, in priority order (fan-out)."""
    return [name for name, pattern in _INTENT_PATTERNS if pattern.search(question)]


def build_sql(intent):
    """Return (sql, params) for an intent. Raises UnknownIntent for bad names."""
    if intent not in _TEMPLATES:
        raise UnknownIntent(intent)
    _view, sql = _TEMPLATES[intent]
    return sql, ()


def run(conn, intent):
    """Execute one intent with a single simplified-query retry on DB errors."""
    view, sql = _TEMPLATES[intent]
    try:
        rows = views.run_query(conn, sql)
    except sqlite3.Error:
        try:
            rows = views.run_query(conn, f"SELECT * FROM {view} LIMIT 20")
            sql = f"SELECT * FROM {view} LIMIT 20  -- simplified retry"
        except sqlite3.Error as exc:
            raise EscalationNeeded(intent) from exc
    return {"intent": intent, "sql": sql, "rows": rows}


def run_question(conn, question):
    """Detect the first intent and run it. Raises UnknownIntent when nothing matches."""
    intents = detect_intents(question)
    if not intents:
        raise UnknownIntent(question)
    return run(conn, intents[0])
