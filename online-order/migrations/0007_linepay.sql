-- 線上 LINE Pay（docs/44 §4.4.2；O5a）：付款狀態直接記在訂單上（一張單同時只有一筆進行中的付款）。
-- payment_status：UNPAID → PENDING（已向 LINE Pay 要付款連結）→ CONFIRMING（正在請款）→ PAID；
-- 取消或失敗回到 UNPAID，linepay_result 記原因，客人可以重付或改到櫃檯付現。
ALTER TABLE orders ADD COLUMN invoice_carrier TEXT;          -- 手機條碼載具（/ 開頭 8 碼）
ALTER TABLE orders ADD COLUMN invoice_tax_id TEXT;           -- 統一編號（8 碼）
ALTER TABLE orders ADD COLUMN linepay_attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN linepay_transaction_id TEXT;   -- 19 位數字，一律存字串
ALTER TABLE orders ADD COLUMN linepay_payment_url TEXT;
ALTER TABLE orders ADD COLUMN linepay_result TEXT;           -- CANCELLED／FAILED／EXPIRED
ALTER TABLE orders ADD COLUMN linepay_checked_at INTEGER;    -- 最後一次補查（epoch ms）
-- POS 保留限量份數的到期時間（epoch ms）：雲端在到期前 1 分鐘就不再請款，不靠 POS 的到期回報準時送達。
ALTER TABLE orders ADD COLUMN hold_expires_at INTEGER;
CREATE INDEX ix_orders_linepay_open ON orders (store_id, payment_status) WHERE payment_method = 'LINE_PAY';
