-- 體驗卡來的行（M1c）：POS 拉單時靠它把「體驗卡」和同品項同選項的一般點分開、冠上卡片標題。
-- 舊單與一般點為 NULL。
ALTER TABLE order_lines ADD COLUMN experience_id INTEGER;
