-- Keep the current attempt's start time across retries/confirm/reconciliation updates.
ALTER TABLE orders ADD COLUMN linepay_started_at INTEGER;
-- For pre-upgrade pending attempts, prefer the latest actual payment initiation event.
UPDATE orders SET linepay_started_at = COALESCE(
  (SELECT MAX(at) FROM order_events e WHERE e.store_id = orders.store_id
   AND e.order_id = orders.id AND e.source = 'linepay'
   AND e.from_state = 'UNPAID' AND e.to_state = 'PENDING'),
  updated_at
) WHERE payment_method = 'LINE_PAY' AND payment_status IN ('PENDING', 'CONFIRMING');
