-- 線上訂單（docs/44 §6；O4a 現金單）。每張表帶 store_id（CLAUDE.md §4）。
ALTER TABLE stores_meta ADD COLUMN paused_reason TEXT;
ALTER TABLE stores_meta ADD COLUMN last_pos_seen_ms INTEGER;

CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  store_id INTEGER NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,            -- 訂單權杖只存雜湊（§8.1 T6）
  idem_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,                  -- 同一冪等鍵換內容 → 409
  device_id TEXT NOT NULL,
  ip_hash TEXT NOT NULL,                      -- 不存原始 IP
  table_code TEXT,
  table_label TEXT,
  service_mode TEXT NOT NULL CHECK (service_mode IN ('DINE_IN', 'TAKEOUT')),
  menu_version INTEGER NOT NULL,
  total INTEGER NOT NULL CHECK (total >= 0),
  payment_method TEXT NOT NULL CHECK (payment_method IN ('CASH', 'LINE_PAY')),
  payment_status TEXT NOT NULL,               -- UNPAID／PAID／CANCELLED…（§4.6）
  sync_status TEXT NOT NULL,                  -- NEW／IMPORTED／SETTLED／VOIDED
  hold_status TEXT NOT NULL,                  -- NONE／HOLD_REQUESTED／HELD／REJECTED
  note TEXT,
  created_at INTEGER NOT NULL,                -- epoch ms（UTC）
  updated_at INTEGER NOT NULL,
  row_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (store_id, idem_key)
);
CREATE INDEX ix_orders_open ON orders (store_id, payment_status, sync_status, created_at);
CREATE INDEX ix_orders_device ON orders (store_id, device_id);
CREATE INDEX ix_orders_ip ON orders (store_id, ip_hash);
CREATE INDEX ix_orders_table ON orders (store_id, table_code);

CREATE TABLE order_lines (
  order_id TEXT NOT NULL,
  store_id INTEGER NOT NULL,
  line_no INTEGER NOT NULL,
  item_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  option_ids TEXT NOT NULL,                   -- JSON 陣列
  unit_price INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  line_total INTEGER NOT NULL,
  limited INTEGER NOT NULL,
  PRIMARY KEY (order_id, line_no)
);

CREATE TABLE order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL,
  order_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT,
  source TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE INDEX ix_order_events_order ON order_events (order_id);

-- 速率計數（§8.2）：每個 key 每分鐘一列。
CREATE TABLE rate_counters (
  store_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (store_id, key, window_start)
);
