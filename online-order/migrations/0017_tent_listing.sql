-- 寄售帳篷上官網（docs/97 §5）：每店一列，存 POS 最後推來的整份清單。版本只往前。
CREATE TABLE tent_listing (
  store_id INTEGER PRIMARY KEY,
  version INTEGER NOT NULL,
  json TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  synced_at TEXT NOT NULL
);
