"""Least-privilege gateway over the warehouse telemetry database.

The planner may only issue SELECT statements against four read-only views.
Every denial cites a stable rule ID:

- SEC-SELECT-01: statement is not a SELECT (or smuggles extra statements).
- SEC-WRITE-02:  SELECT text contains a write keyword.
- SEC-VIEW-03:   query references an identifier outside the view allowlist.

String literals are stripped before scanning so a word like 'update' inside a
quoted value can never false-positive. Result sets are auto-capped at
MAX_LIMIT rows.
"""

import re
import sqlite3

from . import authz
from . import telemetry as tel

MAX_LIMIT = 100

IDEAL_RANK = {"A": 1, "B": 2, "C": 3}

VIEW_SQL = {
    "v_slotting_adherence": """
        SELECT k.sku, k.name AS sku_name, k.velocity_class, k.avg_daily_picks,
               s.slot_id, z.zone_id, z.name AS zone_name, z.distance_to_dock,
               z.zone_rank,
               CASE k.velocity_class WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END
                   AS ideal_rank,
               CASE WHEN z.zone_rank =
                         (CASE k.velocity_class WHEN 'A' THEN 1
                                               WHEN 'B' THEN 2 ELSE 3 END)
                    THEN 0 ELSE 1 END AS mis_slotted
        FROM slots s
        JOIN skus k ON k.sku = s.sku
        JOIN zones z ON z.zone_id = s.zone_id
    """,
    "v_wave_status": """
        SELECT w.wave_id, w.zone_id, z.name AS zone_name, w.shift_id,
               w.orders, w.units, w.capacity_units,
               ROUND(CAST(w.units AS REAL) / w.capacity_units, 2) AS utilization,
               w.released
        FROM waves w
        JOIN zones z ON z.zone_id = w.zone_id
    """,
    "v_inbound_backlog": """
        SELECT shipment_id, carrier, eta_day, arrived_day, status, lines,
               CASE WHEN arrived_day IS NULL THEN 30 - eta_day ELSE 0 END
                   AS days_late
        FROM inbound
        WHERE status != 'received'
    """,
    "v_labor_util": """
        SELECT l.shift_id, l.zone_id, z.name AS zone_name, l.workers,
               l.picks_done, l.picks_target,
               ROUND(CAST(l.picks_done AS REAL) / l.picks_target, 2)
                   AS utilization
        FROM labor l
        JOIN zones z ON z.zone_id = l.zone_id
    """,
}

_WRITE_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create",
    "truncate", "attach", "detach", "vacuum", "pragma", "grant", "revoke",
}
_WRITE_PHRASES = ("replace into",)

_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?(?:\*/|$)", re.S)
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_FROM_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\s*;?\s*$", re.IGNORECASE)


class SecurityError(Exception):
    def __init__(self, rule_id, reason):
        super().__init__(f"[{rule_id}] {reason}")
        self.rule_id = rule_id
        self.reason = reason


def install_views(conn):
    for name, sql in VIEW_SQL.items():
        conn.execute(f"DROP VIEW IF EXISTS {name}")
        conn.execute(f"CREATE VIEW {name} AS {sql}")
    conn.commit()


def _strip_noise(sql):
    """Remove string literals and -- or /* */ comments before security scanning."""
    return _COMMENT_RE.sub(" ", _LITERAL_RE.sub(" ", sql))


def check(sql):
    """Raise SecurityError (with cited rule ID) if the SQL is not allowed."""
    cleaned = _strip_noise(sql)
    if ";" in cleaned:
        raise SecurityError("SEC-SELECT-01", "multiple statements are not allowed")
    head = cleaned.lstrip(" \t\n\r(").lower()
    if not head.startswith("select"):
        raise SecurityError("SEC-SELECT-01", "only SELECT statements are permitted")
    tokens = {t.lower() for t in _TOKEN_RE.findall(cleaned)}
    lowered = cleaned.lower()
    if tokens & _WRITE_KEYWORDS or any(p in lowered for p in _WRITE_PHRASES):
        raise SecurityError("SEC-WRITE-02", "write operations are denied on telemetry views")
    for ident in _FROM_RE.findall(cleaned):
        if ident.lower() not in VIEW_SQL:
            raise SecurityError(
                "SEC-VIEW-03",
                f"identifier '{ident}' is not an exposed read-only view",
            )
    return True


def _cap_limit(sql):
    sql = sql.strip().rstrip(";")
    match = _LIMIT_RE.search(sql)
    if match:
        if int(match.group(1)) > MAX_LIMIT:
            sql = _LIMIT_RE.sub(f"LIMIT {MAX_LIMIT}", sql)
        return sql
    return f"{sql} LIMIT {MAX_LIMIT}"


def run_query(conn, sql, params=()):
    """Security-checked SELECT against the allowlisted views. Returns row dicts."""
    check(sql)
    sql = _cap_limit(sql)
    # Engine-level backstop: text scanning misses comma joins, /**/ and
    # double-quoted names, so SQLite itself refuses any non-view read.
    with authz.views_only(conn, VIEW_SQL) as denied:
        try:
            cursor = conn.execute(sql, params)
        except sqlite3.DatabaseError:
            if denied:
                raise SecurityError(
                    "SEC-VIEW-03",
                    f"identifier '{denied[0]}' is not an exposed read-only view",
                ) from None
            raise
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(MAX_LIMIT)
    return [dict(zip(columns, row)) for row in rows]


def bootstrap(conn, seed=42):
    """Convenience: seed data + install views. Used by the demo and tests."""
    tel.seed_demo_data(conn, seed=seed)
    install_views(conn)
    return conn
