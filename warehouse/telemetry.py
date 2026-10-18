"""Synthetic ambient warehouse telemetry store (SQLite).

All data is fictional and seeded for deterministic tests. Day ``0..29`` is the
30-day history window; day ``30`` is "today".

Tables: skus, zones, slots, inbound, waves, labor.

Velocity classes (ABC analysis): A = fast (>20 picks/day), B = mid (5-20),
C = slow (<5). Ideal slot zone rank: A -> 1 (golden, near dock), B -> 2,
C -> 3 (back of the building). Travel is the hidden cost driver in ambient
warehousing -- every pick is a walk.

Deterministic edge-case anchors (inserted explicitly after random generation):
- SKU-FAST-01: velocity class A, 45 picks/day, slotted in Z-BACK (rank 3,
  180 m from dock) -> guaranteed mis-slotted anchor.
- SKU-DEAD-07: velocity class C, 0.2 picks/day, slotted in Z-GOLD (rank 1,
  12 m from dock) -> guaranteed mis-slotted anchor (dead stock in the
  golden zone).
- WAVE-OVR-01: 320 units against 250 capacity, unreleased -> guaranteed
  over-capacity wave anchor.
- SHIP-LATE-01: carrier "GreatLakes Freight", ETA day 24, still in transit
  on day 30 -> 6 days late anchor.
- SHIFT-UNDER-02: 0.58 labor utilization anchor; SHIFT-OVER-01: 1.12
  utilization anchor.
"""

import random
import sqlite3

CURRENT_DAY = 30

SCHEMA = """
CREATE TABLE skus(sku TEXT PRIMARY KEY, name TEXT, category TEXT,
    unit_cube REAL, velocity_class TEXT, avg_daily_picks REAL);
CREATE TABLE zones(zone_id TEXT PRIMARY KEY, name TEXT,
    distance_to_dock REAL, zone_rank INTEGER);
CREATE TABLE slots(slot_id TEXT PRIMARY KEY, zone_id TEXT, sku TEXT,
    capacity_units INTEGER);
CREATE TABLE inbound(shipment_id TEXT PRIMARY KEY, carrier TEXT,
    eta_day INTEGER, arrived_day INTEGER, status TEXT, lines INTEGER);
CREATE TABLE waves(wave_id TEXT PRIMARY KEY, zone_id TEXT, shift_id TEXT,
    orders INTEGER, units INTEGER, capacity_units INTEGER, released INTEGER);
CREATE TABLE labor(shift_id TEXT PRIMARY KEY, zone_id TEXT, workers INTEGER,
    picks_done INTEGER, picks_target INTEGER, shift_day INTEGER);
"""

_ZONES = [
    ("Z-GOLD", "Golden zone (dock-adjacent)", 12.0, 1),
    ("Z-FWD", "Forward pick", 45.0, 1),
    ("Z-MID", "Mid building", 95.0, 2),
    ("Z-BACK", "Back of building", 180.0, 3),
]

# (sku, name, category, unit_cube, velocity_class, avg_daily_picks)
_SKUS = [
    ("SKU-FAST-01", "Widget Pro A", "Hardware", 0.4, "A", 45.0),
    ("SKU-FAST-02", "Widget Pro B", "Hardware", 0.4, "A", 38.0),
    ("SKU-FAST-03", "Gadget X1", "Electronics", 0.6, "A", 29.0),
    ("SKU-MID-04", "Bracket Set", "Hardware", 0.3, "B", 12.0),
    ("SKU-MID-05", "Cable 2m", "Electronics", 0.1, "B", 9.5),
    ("SKU-MID-06", "Filter Cartridge", "Consumables", 0.8, "B", 6.0),
    ("SKU-DEAD-07", "Legacy Adapter Z", "Electronics", 0.5, "C", 0.2),
    ("SKU-SLOW-08", "Gasket Kit", "Hardware", 0.2, "C", 1.8),
    ("SKU-SLOW-09", "Manual Rev 4", "Consumables", 0.1, "C", 0.9),
    ("SKU-SLOW-10", "Spare Fuse 10A", "Hardware", 0.05, "C", 2.4),
]

_CARRIERS = ["GreatLakes Freight", "BlueRidge Carriers", "PrairieLine Logistics"]


def connect():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def seed_demo_data(conn, seed=42):
    rng = random.Random(seed)
    conn.executescript(SCHEMA)

    for zone_id, name, dist, rank in _ZONES:
        conn.execute(
            "INSERT INTO zones VALUES (?, ?, ?, ?)", (zone_id, name, dist, rank)
        )
    for sku, name, cat, cube, vclass, picks in _SKUS:
        conn.execute(
            "INSERT INTO skus VALUES (?, ?, ?, ?, ?, ?)",
            (sku, name, cat, cube, vclass, picks),
        )

    # Slots: default slotting is mostly sane; anchors override below.
    slot_seq = 0
    default_zone = {"A": "Z-GOLD", "B": "Z-MID", "C": "Z-BACK"}
    for sku, _name, _cat, _cube, vclass, _picks in _SKUS:
        slot_seq += 1
        conn.execute(
            "INSERT INTO slots VALUES (?, ?, ?, ?)",
            (f"SLOT-{slot_seq:03d}", default_zone[vclass], sku, 500),
        )

    # Anchor 1: fast mover stranded in the back of the building.
    conn.execute("UPDATE slots SET zone_id = 'Z-BACK' WHERE sku = 'SKU-FAST-01'")
    # Anchor 2: dead stock occupying the golden zone.
    conn.execute("UPDATE slots SET zone_id = 'Z-GOLD' WHERE sku = 'SKU-DEAD-07'")

    # Inbound shipments: 8 on-time, 2 late anchors.
    for i in range(8):
        eta = rng.randint(24, 29)
        arrived = eta if rng.random() < 0.8 else None
        status = "received" if arrived is not None else "in_transit"
        conn.execute(
            "INSERT INTO inbound VALUES (?, ?, ?, ?, ?, ?)",
            (f"SHIP-{i + 1:04d}", rng.choice(_CARRIERS), eta, arrived,
             status, rng.randint(4, 20)),
        )
    conn.execute(
        "INSERT INTO inbound VALUES ('SHIP-LATE-01', 'GreatLakes Freight', "
        "24, NULL, 'in_transit', 12)"
    )
    conn.execute(
        "INSERT INTO inbound VALUES ('SHIP-LATE-02', 'BlueRidge Carriers', "
        "26, NULL, 'in_transit', 7)"
    )

    # Waves: 5 normal + the over-capacity anchor.
    wave_defs = [
        ("WAVE-1001", "Z-GOLD", "SHIFT-01", 42, 180, 250, 1),
        ("WAVE-1002", "Z-MID", "SHIFT-01", 35, 210, 250, 1),
        ("WAVE-1003", "Z-FWD", "SHIFT-02", 28, 150, 250, 0),
        ("WAVE-1004", "Z-BACK", "SHIFT-02", 20, 120, 250, 0),
        ("WAVE-1005", "Z-GOLD", "SHIFT-03", 50, 245, 250, 1),
        ("WAVE-OVR-01", "Z-MID", "SHIFT-OVER-01", 64, 320, 250, 0),
    ]
    for wave in wave_defs:
        conn.execute("INSERT INTO waves VALUES (?, ?, ?, ?, ?, ?, ?)", wave)

    # Labor shifts: 3 normal + utilization anchors.
    labor_defs = [
        ("SHIFT-01", "Z-GOLD", 12, 1050, 1200, 30),
        ("SHIFT-02", "Z-MID", 10, 880, 1000, 30),
        ("SHIFT-03", "Z-FWD", 8, 640, 800, 30),
        ("SHIFT-UNDER-02", "Z-BACK", 10, 580, 1000, 30),
        ("SHIFT-OVER-01", "Z-MID", 9, 1120, 1000, 30),
    ]
    for shift in labor_defs:
        conn.execute("INSERT INTO labor VALUES (?, ?, ?, ?, ?, ?)", shift)

    conn.commit()
    return conn
