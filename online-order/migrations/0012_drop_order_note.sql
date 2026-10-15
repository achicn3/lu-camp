-- 備註從線上點餐整個移除（2026-10-15 店主）：客人頁不送、雲端不存、不帶給 POS。
-- 2026-10-13 起雲端就已拒收有內容的備註，這欄只剩 NULL。
ALTER TABLE orders DROP COLUMN note;
