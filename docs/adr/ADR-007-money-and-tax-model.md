# ADR-007：金額與稅模型（新台幣整數元、含稅定價）

- **Status**: Accepted
- **Context**: 純現金交易、台灣 NT$ 最小單位為 1 元；零售慣例為含稅標價。需明確且一致的金額/稅規則以守護帳務不變量（拆帳、對帳、報表、定價輔助）。二手收購/寄售拆帳的稅務處理待會計師確認。
- **Decision**:
  - 幣別新台幣、**金額一律整數元（無角分）**；內部用 `Decimal` 計算，邊界以 **ROUND_HALF_UP quantize 到整數元**（`core/money.py` 的 `round_ntd()`）。DB 金額欄位 `NUMERIC` scale 0。
  - **標價含稅**（`unit_price`/`listed_price` 為含稅價）。稅於**發票總額層級**推算一次（不逐項算稅）：`net = round_ntd(total / (1 + tax_rate))`、`tax = total − net`，保證 `net + tax = total`。
  - `tax_rate` 放 `settings`、預設 5%（應稅）；二手/寄售之免稅或特殊情形做成可設定，不寫死。
  - `core/money.py` 提供並測試：`round_ntd()`、`split_tax_inclusive(total, rate) -> (net, tax)`、`commission(gross, pct) -> amount`、定價 `round_ntd(cost / (1 − margin_pct/100))`。**禁用 float。**
- **Alternatives**: 兩位小數金額（與現金找零不符）、外加稅（與台灣含稅標價慣例不符）、逐項算稅（易產生加總不一致）。
- **Consequences**: ＋帳務一致、無 float 誤差、與現金操作相符；－稅務最終仍須會計師確認（已設定化以便調整）。
- **Trade-off**: 以「整數元 + 總額層級含稅推算」換取簡單一致；稅率/免稅情形保留設定彈性。
