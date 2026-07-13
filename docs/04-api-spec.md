# 04 — API 規格

REST、JSON、`/api/v1` 前綴。認證用 `Authorization: Bearer <jwt>`。所有列表端點支援分頁與 `store_id` 範圍過濾（由 token 角色決定可見範圍）。金額以字串傳輸、後端轉 `Decimal`，**新台幣整數元（含稅定價）**。錯誤採一致格式 `{ "error": { "code", "message", "details" } }`。

> 以下為合約骨架；實作時補齊 Pydantic schema、驗證、權限裝飾。

## Auth
```
POST   /api/v1/auth/login            -> { access_token, refresh_token, user }
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
GET    /api/v1/auth/me
```

## Users (MANAGER)
```
GET    /api/v1/users
POST   /api/v1/users
PATCH  /api/v1/users/{id}            # 角色/停用
```

## Contacts
```
GET    /api/v1/contacts?role=&q=     # q 比對姓名/電話(national_id 不可明文搜尋)
POST   /api/v1/contacts/lookup   # body: { national_id }; 後端以 HMAC blind index 精確比對, 供收購去重(回既有 contact 或空)
       # 用 POST 放 body(不放 query): national_id 不可進入 URL/access log(CLAUDE.md §5 優先於本規格)
POST   /api/v1/contacts              # 建檔(含加密 national_id + blind index)
GET    /api/v1/contacts/{id}         # national_id 預設遮罩
GET    /api/v1/contacts/{id}/national-id   # MANAGER 解密查看 -> 寫稽核
PATCH  /api/v1/contacts/{id}
GET    /api/v1/contacts/{id}/purchases     # 會員消費紀錄
```

## Inventory
```
GET    /api/v1/catalog-products?q=&low_stock=
POST   /api/v1/catalog-products
PATCH  /api/v1/catalog-products/{id}        # 改價留痕

GET    /api/v1/serialized-items?status=&ownership=&q=
GET    /api/v1/serialized-items/{id}
GET    /api/v1/serialized-items/by-code/{item_code}   # POS 掃碼查件
PATCH  /api/v1/serialized-items/{id}        # 改價/下架(留痕)
POST   /api/v1/serialized-items/{id}/photos
POST   /api/v1/serialized-items/{id}/print-label   # 補印條碼標籤(經硬體代理, Code 128 編 item_code, 留稽核)

GET    /api/v1/bulk-lots?status=&q=          # E 級散裝批清單(供 POS 選堆)
GET    /api/v1/bulk-lots/{id}
GET    /api/v1/bulk-lots/by-code/{lot_code}  # 掃堆標籤
PATCH  /api/v1/bulk-lots/{id}                # 改均一價/調整件數(留痕)
POST   /api/v1/bulk-lots/{id}/print-label    # 補印整堆標籤(經硬體代理, Code 128 編 lot_code, 留稽核)
```

## Master Data（品牌/型號主檔 + 定價輔助）
```
GET    /api/v1/brands?q=                      # autocomplete 品牌
POST   /api/v1/brands                          # 當場新增品牌
GET    /api/v1/product-models?q=&brand_id=     # autocomplete 型號(可依品牌過濾)
POST   /api/v1/product-models                  # 新增型號(品牌+品名+分類)
GET    /api/v1/product-models/{id}/pricing?acquisition_cost=
       # 回該型號收購/售出價歷史 + 依 settings.default_margin_pct 算出的建議含稅售價
       # 建議售價 = round_ntd(acquisition_cost / (1 - margin_pct/100))；margin_pct 限 0-99
```

## Acquisition（收購/寄售入庫）
```
POST   /api/v1/acquisitions
       body: { type: BUYOUT|CONSIGNMENT|BULK_LOT, contact_id, total_cash_paid?,
               # BUYOUT/CONSIGNMENT (commission_pct 為整數百分數):
               items?:[{name, brand_id?, product_model_id?, category_id, grade(S~D),
                        acquisition_cost?|commission_pct?, listed_price, photos?}],
               # BULK_LOT (E 散裝):
               lot?:{ name, brand_id?, category_id, acquisition_cost, acquisition_basis(WEIGHT|BAG|UNSPECIFIED),
                      total_qty, unit_price, label? } }
       效果: BUYOUT/CONSIGNMENT 建 serialized_item(s); BULK_LOT 建 bulk_lot;
             stock_movement(IN); BUYOUT/BULK_LOT 建 cash_movement(BUYOUT_OUT);
             回傳待列印 item_code / lot_code
GET    /api/v1/acquisitions/{id}
POST   /api/v1/acquisitions/{id}/print-labels   # 入庫批次列印條碼/堆標籤(經硬體代理, Code 128)
       # 事後補印單件改用 /serialized-items/{id}/print-label 或 /bulk-lots/{id}/print-label
```

## Sales / POS
```
POST   /api/v1/sales
       body: { lines:[{type(SERIALIZED|CATALOG|BULK_LOT), item_code?|catalog_product_id?|bulk_lot_id?, qty}],
               buyer_contact_id?, invoice:{ type, buyer_tax_id?, carrier_type?, carrier_id?,
               donation_code?, print_mark? }? }
       效果: 建 sale + sale_line、序號品 -> SOLD、散裝 -> 扣 bulk_lot.remaining_qty(以該堆 unit_price)、
             stock_movement(OUT)、cash_movement(SALE_IN)、寄售品 -> 建 consignment_settlement(PENDING)、
             若 einvoice_enabled -> 產 invoice + 排 upload queue、列印收據/證明聯
GET    /api/v1/sales?from=&to=
GET    /api/v1/sales/{id}
POST   /api/v1/sales/{id}/print-detail   # 補印商品明細聯(經硬體代理; 留稽核)
POST   /api/v1/sales/{id}/void        # 權限+稽核; 已開票 -> 作廢發票流程
```

## Returns
```
POST   /api/v1/returns
       body: { sale_id, lines:[...], reason }
       效果: 退現金(cash_movement OUT, 需開帳中 session)、序號品回 IN_STOCK / 數量回補 / 散裝回補 remaining_qty、
             已售寄售品 -> 反轉 consignment_settlement(未付 CANCELLED / 已付 reclaim_needed)、
             已開票 -> 建 invoice_allowance + 排 upload queue
GET    /api/v1/returns/{id}
```

## Consignment
```
GET    /api/v1/consignment/settlements?status=PENDING
POST   /api/v1/consignment/settlements/{id}/pay    # 付款 -> PAID + cash_movement(CONSIGNMENT_PAYOUT_OUT)
POST   /api/v1/serialized-items/{id}/return-to-consignor   # -> RETURNED_TO_CONSIGNOR
GET    /api/v1/consignment/payables                # 應付未付彙總
```

## Purchasing
```
GET/POST   /api/v1/suppliers
GET/POST   /api/v1/purchase-orders
POST       /api/v1/purchase-orders/{id}/submit     # 草稿送出 -> ORDERED
POST       /api/v1/purchase-orders/{id}/cancel     # 未收貨前取消 -> CANCELLED
POST       /api/v1/purchase-orders/{id}/receive    # 分批收貨 -> 累加 received_qty + stock_movement(IN)
POST       /api/v1/purchase-orders/{id}/receipts/{receipt_id}/invoice  # 補登該批進項發票
```

## E-Invoice
```
GET    /api/v1/invoices/{id}
GET    /api/v1/einvoice/queue?status=             # 上傳佇列狀態
POST   /api/v1/einvoice/queue/{id}/retry          # 重送
GET    /api/v1/einvoice/process-results           # 讀 ProcessResult/SummaryResult 對帳
```

## Cash Drawer
```
POST   /api/v1/cash-sessions/open      { opening_float }
GET    /api/v1/cash-sessions/current
POST   /api/v1/cash-sessions/{id}/movements   { type, amount, note }   # 多為系統自動產生
POST   /api/v1/cash-sessions/{id}/close       { counted_amount } -> 回傳 expected & variance
```

## Stocktake
```
POST   /api/v1/stocktakes
POST   /api/v1/stocktakes/{id}/lines    { item_ref, counted_qty }
POST   /api/v1/stocktakes/{id}/close    # 產生 ADJUST 異動 + 稽核
```

## Reporting
```
GET    /api/v1/reports/daily-cash?date=
GET    /api/v1/reports/sales-margin?from=&to=&group_by=category|item
GET    /api/v1/reports/inventory-value?aging=true
GET    /api/v1/reports/consignment?status=
GET    /api/v1/reports/export?type=&format=csv|xlsx
```

## Settings (MANAGER)
```
GET    /api/v1/settings
PATCH  /api/v1/settings        # einvoice_enabled, default_commission_pct, default_margin_pct, tax_rate, grade_enum...
```

## Hardware Agent（localhost，獨立服務，非主後端）
```
POST   http://localhost:<port>/print/receipt    { sale }
POST   http://localhost:<port>/print/detail      { sale }   # 商品明細聯(逐項品名/數量/單價/小計/總計)
POST   http://localhost:<port>/print/einvoice    { invoice }
POST   http://localhost:<port>/print/label       { code(item_code 或 lot_code), name, price }
       # 以 1D Code 128 編碼 code(識別碼); 標籤含品名/價格等可讀文字
POST   http://localhost:<port>/drawer/open
GET    http://localhost:<port>/health
GET    http://localhost:<port>/devices/status
       # 裝置狀態面板資料來源；前端定時輪詢（前端不直接碰硬體）。回傳各機器：
       # { devices: [ { id, kind(LABEL_PRINTER|RECEIPT_PRINTER|SCANNER|CASH_DRAWER),
       #                model, online(bool), last_seen(ISO8601 UTC),         # A 級：保證
       #                details: { <能報的細項: paper_out|cover_open|error|drawer_open...> },
       #                unsupported: [ <該機型 SDK 查不到的細項鍵> ] } ] }    # B 級：優雅降級
       # 細項欄位依各機型官方 Python SDK 實際支援度填寫；查不到者列入 unsupported，不得臆造。
```

## Notification（預留接口，本期 no-op）
```
# 內部 service 介面，非對外 API:
NotificationService.notify(event, contact, payload)  # 先 log，未來接 LINE/簡訊
```
