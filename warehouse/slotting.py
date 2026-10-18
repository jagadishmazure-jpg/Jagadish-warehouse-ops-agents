"""ABC-velocity slotting optimizer (domain differentiator).

In ambient warehousing, travel is the hidden cost driver: every pick is a
walk. ABC analysis says fast movers (A) belong in the golden zone near the
dock and slow movers (C) belong in the back. This module scores the current
slotting against that rule and recommends moves.

For each slotted SKU it computes the daily travel saving of moving it to a
zone whose rank matches its velocity class::

    saving_m_per_day = picks_per_day * (current_distance - ideal_distance) * 2

(round trip, in meters). Moves are ranked by saving; each move costs
``LABOR_HOURS_PER_MOVE`` of labor, and a reslot plan whose labor exceeds
``GATE_LABOR_HOURS`` needs a human confirmation gate (see replenishment).
"""

LABOR_HOURS_PER_MOVE = 0.5
GATE_LABOR_HOURS = 8.0

_IDEAL_RANK = {"A": 1, "B": 2, "C": 3}


def _zones_by_rank(conn):
    zones = {}
    for row in conn.execute(
        "SELECT zone_id, distance_to_dock, zone_rank FROM zones"
    ):
        zones.setdefault(row["zone_rank"], []).append(
            {"zone_id": row["zone_id"], "distance_to_dock": row["distance_to_dock"]}
        )
    return zones


def recommend_moves(conn):
    """Return ranked reslot recommendations, highest travel saving first.

    Each recommendation: sku, sku_name, velocity_class, avg_daily_picks,
    from_zone, to_zone, saving_m_per_day, labor_hours.
    """
    zones = _zones_by_rank(conn)
    ideal_zone = {
        rank: min(members, key=lambda z: z["distance_to_dock"])
        for rank, members in zones.items()
    }
    rows = conn.execute(
        """SELECT k.sku, k.name, k.velocity_class, k.avg_daily_picks,
                  s.slot_id, z.zone_id, z.distance_to_dock, z.zone_rank
           FROM slots s
           JOIN skus k ON k.sku = s.sku
           JOIN zones z ON z.zone_id = s.zone_id"""
    )
    recs = []
    for row in rows:
        want = _IDEAL_RANK[row["velocity_class"]]
        if row["zone_rank"] == want:
            continue  # already correctly slotted
        target = ideal_zone[want]
        saving = row["avg_daily_picks"] * (
            row["distance_to_dock"] - target["distance_to_dock"]
        ) * 2.0
        if saving <= 0:
            continue
        recs.append({
            "sku": row["sku"],
            "sku_name": row["name"],
            "velocity_class": row["velocity_class"],
            "avg_daily_picks": row["avg_daily_picks"],
            "slot_id": row["slot_id"],
            "from_zone": row["zone_id"],
            "to_zone": target["zone_id"],
            "saving_m_per_day": round(saving, 1),
            "labor_hours": LABOR_HOURS_PER_MOVE,
        })
    recs.sort(key=lambda r: r["saving_m_per_day"], reverse=True)
    return recs


def plan_summary(recommendations):
    """Aggregate a move list into totals for gating and reporting."""
    total_saving = round(sum(r["saving_m_per_day"] for r in recommendations), 1)
    total_labor = round(
        sum(r["labor_hours"] for r in recommendations), 2
    )
    return {
        "moves": len(recommendations),
        "total_saving_m_per_day": total_saving,
        "total_labor_hours": total_labor,
        "requires_gate": total_labor > GATE_LABOR_HOURS,
    }
