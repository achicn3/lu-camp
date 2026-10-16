-- 續杯（2026-10-16 店主）：客人勾了續杯、照菜單折數算價的行；POS 帶入結帳時照續杯價收。
-- 舊單與一般點為 0。
ALTER TABLE order_lines ADD COLUMN refill INTEGER NOT NULL DEFAULT 0;
