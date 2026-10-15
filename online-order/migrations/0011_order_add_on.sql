-- 加點（2026-10-15 店主）：客人自己勾「我是加點」就不檢查本桌低消，真假由店員現場判斷；標記帶給 POS。
ALTER TABLE orders ADD COLUMN add_on INTEGER NOT NULL DEFAULT 0;
