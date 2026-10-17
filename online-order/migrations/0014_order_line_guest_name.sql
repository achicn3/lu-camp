-- 客人頁顯示的品名（2026-10-17 店主）：體驗卡的行只顯示卡片名稱（＋客人另外選的選項）。
-- POS 拉單照舊用 name（卡片名・品項（選項））。舊單與一般點為 NULL，客人頁照 name 顯示。
ALTER TABLE order_lines ADD COLUMN guest_name TEXT;
