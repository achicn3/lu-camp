# ADR-027：線上掃碼點餐放在 Cloudflare，POS 主動拉單

日期：2026-10-01。狀態：提議中（規格見 docs/44）。

## 背景

店主要做店內掃碼點餐：菜單要能設選項和照片，付款支援現金（櫃台）與 LINE Pay（客人手機線上付）。
部署要盡量使用 Cloudflare 免費方案。POS 留在店內 MacBook，不對外公開。

## 決定

- 新增 monorepo 最上層 `online-order/`：Cloudflare Worker（TypeScript）＋靜態資產＋D1＋R2。
- **雲端不連進店內**：店內 backend 主動推菜單、拉訂單、回報狀態（HMAC 簽章）。POS 長時間沒來拉單時
  雲端自動暫停接單。
- **POS 是唯一的帳**：銷售、發票、退款、日結只在 POS。雲端只存訂單與線上付款紀錄。
- LINE Pay 線上付款（Online API v4 request/confirm）由 Worker 執行。POS 匯入已付款的單時成立銷售，
  不再扣款；退款由 POS 呼叫 Online refund。
- 菜單改成分類＋可共用的選項群組（可加價）＋照片，POS 與線上共用。推翻 2026-06-22「扁平、不加價」的裁示；
  既有品項原樣保留。
- 同一線上單只會有一筆銷售：`online_orders.remote_order_id` 與 `sales.online_order_id` 都設唯一約束。

## 替代方案

- POS 整套搬上雲：要搬資料庫、印表機橋接，店內斷網就停擺。不採用。
- Cloudflare Tunnel 把店內 API 開出去：等於把 POS 暴露在公網上，而且還是要另做授權。不採用。
- Supabase：免費專案閒置 7 天會自動暫停，而且多一家供應商要管。不採用。

## 影響

POS 新增 `onlineorder` 模組、菜單 migration、`sales.online_order_id`、`linepay_transactions.channel`。
雲端新增一個要部署、維護的服務。需要申請 LINE Pay 線上收款資格（待確認）。
