-- 店內作廢／退貨回報（docs/44 §4.5 C6；O5 收尾）：已成立的單 payment_status 可到
-- PARTIALLY_REFUNDED（部分退）→ REFUNDED（全退），refunded_amount 記店內累計退了多少（只增不減）。
-- 退款本身只在 POS 做；雲端只記結果給客人看。
ALTER TABLE orders ADD COLUMN refunded_amount INTEGER NOT NULL DEFAULT 0;
