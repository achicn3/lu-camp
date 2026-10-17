-- 客人 LINE Pay 綁定的手機條碼載具（2026-10-17 店主）：請款回應 merchantReference 帶回的，另存一欄、不蓋掉客人輸入的。
-- 開發票時客人輸入的（invoice_carrier／invoice_tax_id）優先，都沒填才用這個；都沒有就印紙本。
ALTER TABLE orders ADD COLUMN linepay_carrier TEXT;
