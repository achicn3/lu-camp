-- 線上 LINE Pay 開關（2026-10-20 店主）：POS 設定打開才開放客人用 LINE Pay；預設關。
-- 關掉只擋新的付款，已經在付的（授權後請款、補查）照常完成。
ALTER TABLE stores_meta ADD COLUMN linepay_enabled INTEGER NOT NULL DEFAULT 0;
