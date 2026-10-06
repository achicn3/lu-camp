-- 即時可售狀態按菜單版本保存；更新只能在目前發佈版本上遞增 revision。
CREATE TABLE menu_availability (
  store_id INTEGER NOT NULL,
  menu_version INTEGER NOT NULL CHECK (menu_version > 0),
  revision INTEGER NOT NULL CHECK (revision > 0),
  json TEXT NOT NULL,
  PRIMARY KEY (store_id, menu_version)
);
