-- 寄售帳篷清單同店一次只處理一份（docs/97 §5，Codex 對抗審查第三輪）：驗照片、寫清單、清照片整段持鎖，
-- 避免較舊的請求清照片時刪到較新清單的照片。期限到了（請求卡死）別人可以接手。
CREATE TABLE tent_lock (
  store_id INTEGER PRIMARY KEY,
  token TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
