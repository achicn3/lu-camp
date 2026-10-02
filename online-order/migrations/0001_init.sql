-- 線上點餐 D1 初始資料表（docs/44 §6）。每張表都帶 store_id（CLAUDE.md §4）。
-- O3 只用到菜單快照、桌位、整合 nonce；訂單相關表在 O4 加。

CREATE TABLE stores_meta (
  store_id INTEGER PRIMARY KEY,
  menu_version INTEGER NOT NULL DEFAULT 0,
  accepting_orders INTEGER NOT NULL DEFAULT 0,
  last_pos_seen_at TEXT
);

-- 已發佈的菜單快照；只留最近幾版（舊單要能對到下單當時的版本）。
CREATE TABLE menu_snapshots (
  store_id INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  json TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (store_id, version)
);

-- 桌位碼：不可猜的隨機碼 → 桌名；重發＝整份取代，舊碼即失效。
CREATE TABLE tables (
  store_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  label TEXT NOT NULL,
  service_mode TEXT NOT NULL CHECK (service_mode IN ('DINE_IN', 'TAKEOUT')),
  PRIMARY KEY (store_id, code)
);

-- 整合請求的 nonce（防重放），保留 10 分鐘。
CREATE TABLE integration_nonces (
  store_id INTEGER NOT NULL,
  nonce TEXT NOT NULL,
  seen_at INTEGER NOT NULL,
  PRIMARY KEY (store_id, nonce)
);
CREATE INDEX ix_integration_nonces_seen ON integration_nonces (seen_at);
