# SOP-04: Cycle Counts

Applies to inventory accuracy checks outside full physical counts.

## Count cadence
1. A-class SKUs are counted weekly, B-class monthly, C-class quarterly.
2. Any SKU with a mis-slot flag gets counted at the next cycle, because
   mis-slotted SKUs are the top source of pick errors.

## Count procedure
1. The counter scans the slot and enters the on-hand quantity blind (without
   seeing the system quantity first).
2. A variance above 2% of slot capacity triggers a recount by a second
   counter before any adjustment is posted.
3. Adjustments post to the audit log with the counter ID and the reason.

## Zero-count slots
1. A slot showing zero on-hand for a live SKU is investigated the same day;
   it usually means a mis-pick, not a stockout.
