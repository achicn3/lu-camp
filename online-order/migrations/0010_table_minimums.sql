-- 桌號低消（2026-10-14 店主）：每桌一條規則（JSON；NULL＝不限制），送單時由雲端檢查。
ALTER TABLE tables ADD COLUMN minimum TEXT;
-- 「每人至少一份」的桌號要客人填用餐人數，一併帶給 POS。
ALTER TABLE orders ADD COLUMN party_size INTEGER;
