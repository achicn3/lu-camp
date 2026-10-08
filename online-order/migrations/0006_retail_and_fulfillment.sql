-- 帶回家零售商品（docs/63 §13、M1d）：訂單行可以是餐飲品項或一般商品（兩者擇一）；訂單記交貨狀態。
-- SQLite 不能把既有欄位改成可空，order_lines 照原資料重建。
CREATE TABLE order_lines_new (
  order_id TEXT NOT NULL,
  store_id INTEGER NOT NULL,
  line_no INTEGER NOT NULL,
  item_id INTEGER,
  catalog_product_id INTEGER,
  name TEXT NOT NULL,
  option_ids TEXT NOT NULL,                   -- JSON 陣列
  unit_price INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  line_total INTEGER NOT NULL,
  limited INTEGER NOT NULL,
  experience_id INTEGER,
  PRIMARY KEY (order_id, line_no),
  CHECK ((item_id IS NULL) <> (catalog_product_id IS NULL))
);
INSERT INTO order_lines_new
  (order_id, store_id, line_no, item_id, catalog_product_id, name, option_ids, unit_price, qty,
   line_total, limited, experience_id)
SELECT order_id, store_id, line_no, item_id, NULL, name, option_ids, unit_price, qty,
       line_total, limited, experience_id
FROM order_lines;
DROP TABLE order_lines;
ALTER TABLE order_lines_new RENAME TO order_lines;

-- 交貨：NONE（沒有帶回家商品）／AWAITING（付了錢、待交貨）／HANDED_OVER（店員交給客人了）。
ALTER TABLE orders ADD COLUMN fulfillment TEXT NOT NULL DEFAULT 'NONE'
  CHECK (fulfillment IN ('NONE', 'AWAITING', 'HANDED_OVER'));
