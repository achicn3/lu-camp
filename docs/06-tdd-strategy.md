# 06 — TDD 測試策略

## 原則

- **Test-first，紅→綠→重構**：先寫失敗測試 → 最小實作通過 → 重構。禁止先實作後補測試。
- 測試對應需求：每個 `01-requirements.md` 的功能點與 `CLAUDE.md §7` 的不變量都要有對應測試。
- 測試金字塔：大量單元、適量整合、少量端對端。

## 工具

**後端**
- `pytest`、`pytest-asyncio`、`httpx`（呼叫 FastAPI）、`pytest-cov`。
- 測試 DB：用真實 PostgreSQL，每測以交易回滾隔離。**不用 SQLite 取代**（行為需與正式一致）。
  - 現況（Phase 1 起）：改用 `docker compose` 起的本機開發 DB + 交易回滾隔離（conftest 以外層交易包覆、session 走 savepoint），**暫不使用 testcontainers**——本機環境曾出現 Node/子行程不穩，先採最省、可立即跑綠的方案。待環境穩定（或上 CI service container）即可回 `testcontainers` 取得每次乾淨實例。
- 測資工廠：`factory_boy` 或自製 fixtures（建立 store/user/contact/item...）。
- 時間/隨機性可注入（避免 flaky）。

**前端**
- `vitest`（或 jest）+ React Testing Library：元件與邏輯。
- `Playwright`：關鍵流程 e2e。

**硬體代理**
- 以介面/抽象包裝印表機；測試用 fake printer 驗證送出的 ESC/POS 指令序列。

## 各層測試重點

- **單元（service/domain）**：金額、抽成、稅、狀態轉移、不變量。最重的測試在這層。
- **整合（API + DB）**：端點正確性、權限、交易、錯誤格式、`store_id` 範圍過濾。
- **端對端**：收購→入庫→上架→銷售→（寄售結算/開票/退貨）→現金對帳的完整路徑。

## 覆蓋率門檻（本機關卡強制）

- `services/`、領域邏輯：**≥ 90%**。
- 整體：**≥ 80%**。未達標本機關卡失敗。

## 必測的關鍵不變量（對應 CLAUDE.md §7）

1. **序號品唯一售出**：已 `SOLD` 的 `serialized_item` 不可再加入銷售或重複入庫；並發兩筆銷售同一件只能成功一筆。
2. **寄售拆帳**（`commission_pct` 為整數百分數）：`commission_amount = round_ntd(gross × commission_pct / 100)`、`payout_amount = gross − commission_amount`（含邊界與四捨五入；預設 pct=50 時對拆）。賣出寄售品必產生 `consignment_settlement(PENDING)`。
3. **毛利認列**：買斷品毛利 = 售價 − 收購成本；寄售品店家收入只認 `commission_amount`，不得計入全額售價。
4. **現金對帳**：`expected = opening_float + ΣSALE_IN − ΣBUYOUT_OUT − ΣCONSIGNMENT_PAYOUT_OUT ± MANUAL_ADJUST`；差異正確計算與記錄。
5. **發票開關解耦**：`einvoice_enabled` 為 true/false 兩種情況下，銷售都完整寫入；false 時 `invoice_status=NOT_ISSUED` 且不產生 XML/不配號。
6. **退貨折讓**：已開票的退貨產生 `invoice_allowance` 並排上傳，原發票不得被刪除。
7. **PII**：`national_id` 寫入後在 DB 為密文；一般 API 回應遮罩；log 不含明文；`MANAGER` 解密查看會寫 `audit_log`；以 `national_id_blind_index`(HMAC) 做精確去重比對，且**不可明文/部分搜尋**（測試：相同號碼命中既有 contact、明文 q 查不到）。
8. **金額型別**：所有金額計算用 `Decimal`、**新台幣整數元**（ROUND_HALF_UP、含稅定價、稅於發票總額層級推算 `net+tax=total`），無 float 誤差（以含小數的測資驗證）。
9. **離線發票佇列**：模擬 Turnkey 目錄不可寫/上傳失敗時，項目進入 `FAILED/PENDING` 可重送，銷售不受影響。
10. **稽核完整**：作廢、改價、現金調整、權限/設定變更、PII 查看都產生稽核紀錄。
11. **定價輔助**：`建議售價 = round_ntd(收購價 ÷ (1 − margin_pct/100))` 為含稅整數元；`margin_pct` 邊界 **0–99**（=100/>100 須被擋下回錯）；店員可手動覆蓋。
12. **設定型別**：`settings` 單列具型別、Pydantic 驗證（測試非法型別/範圍被擋）。

## 範例（紅燈先行示意）

```python
def test_consignment_split_default_50(consignment_item_factory, sales_service):
    item = consignment_item_factory(listed_price="1000", commission_pct="50")
    sale = sales_service.sell(item_code=item.item_code)
    s = sale.consignment_settlement
    assert s.commission_amount == Decimal("500")
    assert s.payout_amount == Decimal("500")
    assert s.status == "PENDING"

def test_serialized_item_cannot_be_sold_twice(sold_item, sales_service):
    with pytest.raises(ItemNotAvailable):
        sales_service.sell(item_code=sold_item.item_code)
```

## 本機檢查流程

1. lint + format check（ruff / eslint / prettier）
2. type check（mypy strict / tsc）
3. backend pytest + coverage gate
4. frontend test + coverage
5. e2e（Playwright）於合併前或 nightly
6. 任一失敗即阻擋合併
