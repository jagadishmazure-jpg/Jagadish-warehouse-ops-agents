"""ABC-velocity slotting optimizer (domain differentiator).

In ambient warehousing, travel is the hidden cost driver: every pick is a
walk. ABC analysis says fast movers (A) belong in the golden zone near the
dock and slow movers (C) belong in the back. This module scores the current
slotting against that rule and recommends moves.

For each slotted SKU it computes the daily travel saving of moving it to a
zone whose rank matches its velocity class::

    saving_m_per_day = picks_per_day * (current_distance - target_distance) * 2

(round trip, in meters). The target must have room: a free slot, or a slow
mover in that zone to swap out, in which case the slow mover's added travel
is subtracted. Moves are ranked by saving; each move costs
``LABOR_HOURS_PER_MOVE`` of labor, and a reslot plan whose labor exceeds
``GATE_LABOR_HOURS`` needs a human confirmation gate (see replenishment).
"""

LABOR_HOURS_PER_MOVE = 0.5
GATE_LABOR_HOURS = 8.0

_IDEAL_RANK = {"A": 1, "B": 2, "C": 3}


def _zones(conn):
    """zone_id -> {rank, distance, free}; free = empty slots (sku IS NULL)."""
    zones = {}
    for row in conn.execute(
        "SELECT z.zone_id, z.distance_to_dock, z.zone_rank, "
        "SUM(CASE WHEN s.slot_id IS NOT NULL AND s.sku IS NULL THEN 1 ELSE 0 END) AS free "
        "FROM zones z LEFT JOIN slots s ON s.zone_id = z.zone_id GROUP BY z.zone_id"
    ):
        zones[row["zone_id"]] = {"rank": row["zone_rank"],
                                 "distance": row["distance_to_dock"],
                                 "free": row["free"] or 0}
    return zones


def recommend_moves(conn):
    """Return feasible reslot recommendations, highest net travel saving first.

    A fast mover can only go where there is room. For each under-slotted SKU
    the nearest zone of the right rank with a free slot is used; if every
    such zone is full, the SKU is *swapped* with the slowest over-slotted SKU
    occupying that rank (for example dead stock in the golden zone), and the
    saving is netted against the extra travel the slow SKU now costs. A move
    with no free slot and no swap partner is not recommended.

    Each recommendation: sku, sku_name, velocity_class, avg_daily_picks,
    slot_id, from_zone, to_zone, move_type ("move" or "swap"), swap_with,
    saving_m_per_day (net), labor_hours.
    """
    zones = _zones(conn)
    free = {z: info["free"] for z, info in zones.items()}
    rows = [dict(r) for r in conn.execute(
        """SELECT k.sku, k.name, k.velocity_class, k.avg_daily_picks,
                  s.slot_id, z.zone_id, z.distance_to_dock, z.zone_rank
           FROM slots s
           JOIN skus k ON k.sku = s.sku
           JOIN zones z ON z.zone_id = s.zone_id
           ORDER BY k.sku"""
    )]
    under = [r for r in rows if r["zone_rank"] > _IDEAL_RANK[r["velocity_class"]]]
    over = sorted((r for r in rows if r["zone_rank"] < _IDEAL_RANK[r["velocity_class"]]),
                  key=lambda r: (r["avg_daily_picks"], r["sku"]))
    under.sort(key=lambda r: (-r["avg_daily_picks"] * r["distance_to_dock"], r["sku"]))
    used_partners = set()
    recs = []
    for row in under:
        want = _IDEAL_RANK[row["velocity_class"]]
        candidates = sorted((z for z, info in zones.items() if info["rank"] == want),
                            key=lambda z: zones[z]["distance"])
        target = next((z for z in candidates if free[z] > 0), None)
        partner = None
        if target is None:
            partner = next((o for o in over if o["sku"] not in used_partners
                            and zones[o["zone_id"]]["rank"] == want), None)
            if partner is None:
                continue  # no room and nothing to displace: not feasible
            target = partner["zone_id"]
        gain = row["avg_daily_picks"] * (row["distance_to_dock"] - zones[target]["distance"]) * 2.0
        cost = 0.0
        if partner is not None:
            cost = partner["avg_daily_picks"] * (row["distance_to_dock"] - partner["distance_to_dock"]) * 2.0
        net = gain - cost
        if net <= 0:
            continue
        if partner is None:
            free[target] -= 1
        else:
            used_partners.add(partner["sku"])
        recs.append({
            "sku": row["sku"],
            "sku_name": row["name"],
            "velocity_class": row["velocity_class"],
            "avg_daily_picks": row["avg_daily_picks"],
            "slot_id": row["slot_id"],
            "from_zone": row["zone_id"],
            "to_zone": target,
            "move_type": "swap" if partner else "move",
            "swap_with": partner["sku"] if partner else None,
            "saving_m_per_day": round(net, 1),
            "labor_hours": LABOR_HOURS_PER_MOVE * (2 if partner else 1),
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
