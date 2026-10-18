# SOP-02: Putaway and Slotting

Applies to putaway of received goods and to slotting (where each SKU lives).

## ABC velocity slotting
1. Classify every SKU by average daily picks: A (fast, over 20/day),
   B (mid, 5-20/day), C (slow, under 5/day).
2. A SKUs live in the golden zone near the dock; B SKUs in mid building;
   C SKUs in the back of the building. Travel is the cost driver -- every
   pick is a walk.
3. Re-run the slotting optimizer weekly. A SKU is mis-slotted when its
   zone rank does not match its velocity class.

## Reslot moves
1. The optimizer ranks moves by travel saved per day (meters, round trip).
2. A reslot plan above 8 labor-hours needs a supervisor's confirmation gate
   before any move is executed. Moves are done on the night shift so pick
   faces stay available during the day.
3. Never move a SKU while its wave is released for picking.

## Putaway rules
1. Put away to the SKU's assigned slot, not the nearest empty slot.
2. Overstock beyond the pick-face capacity goes to bulk reserve, recorded
   against the same SKU.
