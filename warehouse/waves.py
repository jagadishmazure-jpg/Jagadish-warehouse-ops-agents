"""Wave-planning engine (domain differentiator).

Orders are grouped into pick waves by zone and priority, then packed against
per-wave capacity. A wave that exceeds ``WAVE_CAPACITY_UNITS`` is flagged
``over_capacity`` -- releasing an over-capacity wave needs a human
confirmation gate, because it means the shift plan no longer fits the labor
assigned to it.
"""

WAVE_CAPACITY_UNITS = 250

_PRIORITIES = ("P1", "P2", "P3")


def _validate_order(order):
    for key in ("order_id", "zone_id", "priority", "units"):
        if key not in order:
            raise ValueError(f"order is missing {key!r}: {order!r}")
    if order["priority"] not in _PRIORITIES:
        raise ValueError(f"unknown priority {order['priority']!r}")
    if order["units"] <= 0:
        raise ValueError(f"units must be positive: {order!r}")
    return order


def plan_waves(orders, wave_capacity=WAVE_CAPACITY_UNITS):
    """Group orders into waves by (zone, priority); pack against capacity.

    Returns a list of wave dicts: wave_id, zone_id, priority, order_ids,
    orders, units, capacity_units, over_capacity.
    """
    groups = {}
    for order in orders:
        o = _validate_order(order)
        key = (o["zone_id"], o["priority"])
        groups.setdefault(key, []).append(o)

    waves = []
    seq = 0
    for (zone_id, priority), members in sorted(groups.items()):
        # Pack greedily in priority order; a single order bigger than the
        # wave is its own wave (flagged over capacity).
        members.sort(key=lambda o: o["units"], reverse=True)
        buckets, current = [], {"order_ids": [], "units": 0}
        for o in members:
            if current["order_ids"] and (
                current["units"] + o["units"] > wave_capacity
            ):
                buckets.append(current)
                current = {"order_ids": [], "units": 0}
            current["order_ids"].append(o["order_id"])
            current["units"] += o["units"]
        if current["order_ids"]:
            buckets.append(current)
        for bucket in buckets:
            seq += 1
            waves.append({
                "wave_id": f"PLAN-W{seq:03d}",
                "zone_id": zone_id,
                "priority": priority,
                "order_ids": bucket["order_ids"],
                "orders": len(bucket["order_ids"]),
                "units": bucket["units"],
                "capacity_units": wave_capacity,
                "over_capacity": bucket["units"] > wave_capacity,
            })
    return waves


def release_requires_gate(wave):
    """Wave releases always need human confirmation (per SOP-03)."""
    return True
