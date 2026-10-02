-- 桌位碼版本號（Codex 對抗審查 O3）：晚到的舊推送不能蓋掉新的桌位碼（否則停用的 QR 又能用）。
ALTER TABLE stores_meta ADD COLUMN tables_revision INTEGER NOT NULL DEFAULT 0;
