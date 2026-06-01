# ADR-009：品牌/型號主檔與定價輔助（定價計算機）

- **Status**: Accepted
- **Context**: 收購建檔需要一致的品牌/品名以利查詢、報表與定價參考；店員定價需要快速、可重現的建議售價，並參考同型號歷史行情。
- **Decision**:
  - `brand` 為輕主檔（店員可當場新增）；`product_model` 為型號主檔（品牌 + 品名/型號 + 分類）。收購品名「自由輸入 + 優先 autocomplete 既有 `product_model`」；選既有帶入品牌/分類與價格歷史，輸入全新則順手建一筆。
  - `serialized_item`/`bulk_lot`/`catalog_product` 帶 `brand_id`；`serialized_item` 可選 `product_model_id`。
  - **定價輔助**：主算法用目標毛利率 `建議售價 = round_ntd(收購價 ÷ (1 − margin_pct/100))`，為含稅整數元（沿用 `core/money`，見 ADR-007）；`default_margin_pct` 放 `settings`（整數百分數，預設 45），**`margin_pct` 限 0–99**，超出須擋下回錯。同時顯示該型號歷史售價；店員可手動覆蓋任一數字。
  - **價格歷史不另建表**：依 `product_model_id` 聚合既有 `acquisition`（收購價）與 `sale_line`（售出價）取得。
- **Alternatives**: 另建價格歷史表（冗餘、需同步維護）；品名純自由輸入無主檔（資料髒、難聚合）；固定加價金額而非毛利率（不隨成本縮放）。
- **Consequences**: ＋資料乾淨可聚合、定價快速可重現、無冗餘歷史表；－型號未填時無法提供型號層級歷史（`product_model_id` 為選填，需引導店員建檔）。
- **Trade-off**: 以「輕主檔 + 交易聚合」換取「資料品質 vs 維護成本」的平衡。
