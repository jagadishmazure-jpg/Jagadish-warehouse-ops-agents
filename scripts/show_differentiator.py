"""Print the slotting optimizer and wave planner on seeded data (rendered into README, checked in CI)."""
from warehouse import slotting, telemetry, views, waves

conn = telemetry.connect()
views.bootstrap(conn)
recs = slotting.recommend_moves(conn)
for r in recs:
    print(f"{r['move_type']}: {r['sku']} {r['from_zone']} -> {r['to_zone']} swap_with={r['swap_with']} "
          f"net {r['saving_m_per_day']} m/day, {r['labor_hours']} labor-h")
print(slotting.plan_summary(recs))
orders = [{"order_id": "O-1", "zone_id": "Z-MID", "priority": "P3", "units": 120},
          {"order_id": "O-2", "zone_id": "Z-GOLD", "priority": "P1", "units": 200},
          {"order_id": "O-3", "zone_id": "Z-GOLD", "priority": "P1", "units": 90},
          {"order_id": "O-4", "zone_id": "Z-BACK", "priority": "P2", "units": 300}]
for w in waves.plan_waves(orders):
    print(f"{w['wave_id']} {w['priority']} {w['zone_id']} orders={w['order_ids']} units={w['units']} "
          f"over_capacity={w['over_capacity']}")
