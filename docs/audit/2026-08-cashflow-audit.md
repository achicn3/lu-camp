# 金流程式碼稽核（2026-08）

本文件前半先列修正後結論，後半保留 2026-08-24 修正前的唯讀稽核快照，方便逐項追溯。
2026-08-24 經店長解除唯讀限制後，已在 `fix/cashflow-audit-findings` 分支完成程式、migration、
前端與測試修正。

- 稽核基準：`main` @ `fa005ed`（2026-08-24）
- 修正基準：工作樹 `fix/cashflow-audit-findings`（驗證至 2026-08-25）。
- 本輪狀態：階段 1、階段 2、修正與驗證完成；正式環境 migration 套用後仍需做一次部署查驗。
- 範圍：收購／寄售建單與定價、購物金帳本、寄售結算、錢櫃現金、稅額、收據／帳單／發票金額來源，以及其 transaction／併發／外部副作用邊界。
- 範圍外：前端樣式與 i18n、硬體代理協定細節、認證授權、作廢／更正流程設計。
- 舊基準 `93cd4b7` 的階段 2 結論未沿用；以下結論均依目前基準重新查證。

## 修正後結論（白話版）

目前沒有仍待施工的 P0／P1／P2。修正後又做了一輪規格、程式分層與實機流程交叉 review；
review 找到的 LINE Pay 解除配對後補單、寄售預設抽成送值、退款識別碼漂移、簽署逾期補帳、
Numeric 邊界與分層／smoke 缺口都已處理。另有 3 項依店長裁示接受現況：散裝成本可有整批 1 元尾差、稅率固定
5% 時不另改歷史報表算法，以及不特別限制 100% 抽成。還有 1 項不是程式問題，而是上線後
要確認 migration 真的已套到正式 DB。最後一輪 Standards 與 Spec 對抗 review 均為 clean。

| 分類 | 尚未處理 | 已處理／受控 | 店長接受現況 | 說明 |
|---|---:|---:|---:|---|
| P0 | 0 | 1 | 0 | LINE Pay 收款必須有客顯；不確定結果自動查原單，不會自動再扣款 |
| P1 | 0 | 6 | 3 | 接受散裝 1 元尾差、固定 5% 的歷史摘要算法，以及不限制 100% 抽成 |
| P2 | 0 | 11 | 0 | DB 守衛、migration、分層、測試隔離、契約與瀏覽器 smoke 均已補齊 |
| 待確認 | 1 | 2 | 1 | 只剩正式 DB 套版後查 trigger／constraint；Amego 與進項發票已定案 |

### 修正後 A–E 判定

| 類別 | 判定 | 白話結論 |
|---|---|---|
| A. 數值正確性 | **符合（含 2 項裁示口徑）** | 後端用 Decimal／整數元；前端費率改用十進位字串＋BigInt；稅與折讓尾差能對回原發票。散裝逐件成本的整批 1 元尾差依裁示允許。 |
| B. 帳本完整性 | **符合** | 購物金與錢櫃帳一般 DML 都是 insert-only；測試若要清資料，只能在每個 pytest process 專用、跑完即刪除的 DB。 |
| C. 原子性與併發 | **符合（外部金流採可復原流程）** | DB 內主檔、明細、庫存、購物金與錢櫃仍同一交易；LINE Pay 無法與本地 DB 共用 transaction，改以持久狀態、原單查詢及本地補完處理斷點。 |
| D. 冪等性 | **符合** | 銷售、收購、退貨、購物金、寄售付款與人工現金調整都有冪等鍵／唯一約束；LINE Pay 復原不會再送一次收款或退款。 |
| E. 邊界條件 | **符合（含 100% 抽成裁示口徑）** | 0 元贈品單、全額購物金、餘額不足、負數輸入與寄售拆帳均有 service 或 DB 守衛。系統仍允許輸入 100% 抽成；若真的發生，0 元付款會被 DB 擋下並整筆回滾，不會錯付，但該結算會維持待付。店長裁示實務上不會使用，不另限制。 |

## P0：修正後狀態

### P0-1：已處理 — LINE Pay 不再允許跳過客顯，失敗時可自動查原單

`backend/app/modules/sales/service.py:850-863`

```python
if line_pay_tenders:
    if cart is None:
        raise InvalidSaleTender("LINE Pay 收款必須使用已配對的客顯購物車")
    if reconciled_linepay_result is None:
        pairing = await self._display_repo.get_active_pairing_for_terminal(...)
        if pairing is None or pairing.kiosk_device_id != cart.kiosk_device_id:
            raise InvalidSaleTender("LINE Pay 收款前，客顯必須維持配對並連接原購物車")
```

`backend/app/modules/customerdisplay/background_service.py:62-80`

```python
for store_id, terminal_id in targets:
    async with factory() as session:
        outcome = await reconcile_uncertain_payment_target(
            session, store_id=store_id, terminal_id=terminal_id, linepay_client=linepay_client
        )
```

**白話：** 一般請款沒有客顯、或原客顯已解除配對，都不會先扣款。平台結果不確定時，背景
工作只查原本的 order，不呼叫 `pay`；即使這段時間故障客顯已被解除配對，查到平台成功仍會
依後端保存資料補完本地銷售。整合測試先真的呼叫解除配對，再驗證補單成功：
`backend/tests/integration/test_customer_display_cart_api.py:989-1016`。

## P1：修正後狀態

### P1-1：已處理 — 寄售強制抽成，預設讀系統設定，仍可逐件改

`backend/app/modules/acquisition/service.py:485-491`

```python
if data.type == AcquisitionType.CONSIGNMENT:
    await self._settings.lock_store_shared(store_id)
    default_commission_pct = int(
        (await self._settings.get_effective_settings(store_id)).default_commission_pct
    )
```

`backend/app/modules/acquisition/service.py:816-820`

```python
commission = (
    item.commission_pct if item.commission_pct is not None else default_commission_pct
)
```

`frontend/app/(authed)/acquisition/page.tsx:959-970`

```typescript
body.items = rows.map((r) => ({
  // ...
  ...(type === "BUYOUT"
    ? { acquisition_cost: ntd(r.acquisitionCost) }
    : r.commissionPct === ""
      ? {}
      : { commission_pct: parseNtd(r.commissionPct) }),
}));
```

**白話：** 欄位沒手改時，畫面仍會顯示當下查到的預設值，但送出時不把它冒充成逐件覆寫；
後端會在建單交易中鎖住設定並讀最新值。真的手改的品項才送 `commission_pct`。
`frontend/__tests__/acquisition-commission-page.test.tsx:77-154` 已驗證未手改的 request 沒有該欄位；
實機瀏覽器另驗證新列顯示系統設定 37，且第二件可獨立改為 42、第一件仍維持 37。

### P1-2：店長接受現況 — 散裝逐件成本允許整批 1 元尾差

`backend/tests/integration/test_reports_sales_margin.py:265-273`

```python
# 1000 ÷ 3 每件以 HALF_UP 記 333；售出一件 500，毛利 167。
assert body["bulk_cogs"] == "333"
assert body["gross_margin"] == "167"
```

**白話：** 系統以「成交當下每件整數元成本」記帳，所以 1,000 元三件會是每件 333 元，
整批可能差 1 元。店長已明確允許，不另做尾件分攤。

### P1-3：已處理 — 散裝毛利全部改讀成交成本快照

`backend/app/modules/inventory/service.py:403-405`

```python
sold_price = line.net_amount
sold_cost = line.cost_snapshot
```

**白話：** 商品頁、毛利報表與購物金效益分析都以成交時已落盤的 `cost_snapshot` 為準，
不再有一邊顯示 333、另一邊重算 333.333… 的情況。

### P1-4：已處理 — 多次部分折讓最後一定精確沖回原發票

`backend/app/modules/einvoice/service.py:601-609`

```python
if cumulative_total == invoice.total:
    target_cumulative_net = Decimal(invoice.net)
preferred_net = target_cumulative_net - prior_net
net = min(max(preferred_net, min_net), max_net)
tax = total - net
```

**白話：** 每次折讓看「累計已退多少」分配未稅與稅；最後全退時直接以原發票 net／tax
為終點，所以不會留下 1 元稅額沖不掉。

### P1-5：店長接受現況 — 固定 5% 期間保留目前歷史摘要算法

`backend/app/modules/reports/service.py:887`

```python
net_ex_tax, tax = split_tax_inclusive(margin.recognized_revenue, settings.tax_rate)
```

**白話：** 每日摘要仍以查詢時設定拆稅；店長裁示目前稅率固定 5%，因此不施工。在固定 5%
的前提下數字不會漂移；若未來真的允許改稅率，這一項必須重新開案。

### P1-6：已處理 — 前端費率不再用浮點數四捨五入

`frontend/lib/money.ts:28-35`

```typescript
const scale = BigInt(10_000);
const numerator = BigInt(amountNtd) * rateUnits;
const rounded = (BigInt(2) * magnitude + scale) / (BigInt(2) * scale);
```

**白話：** API 的 Decimal 費率保持字串，轉成整數比例後才算 HALF_UP；例如 5,000 ×
0.0003 不會因 JavaScript float 變成 1.499999… 而少顯示 1 元。

### P1-7：已處理 — 人工錢櫃調整可安全重試

`backend/app/modules/cashdrawer/router.py:92-95`

```python
idempotency_key: Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=80)
]
```

`backend/app/modules/cashdrawer/models.py:68`

```python
Index(
    "uq_cash_movements_store_idempotency_key",
    "store_id", "idempotency_key", unique=True,
    postgresql_where=text("idempotency_key IS NOT NULL"),
)
```

`frontend/scripts/cash-smoke.mjs:59-114`

```javascript
const committed = await route.fetch();
await route.fulfill({
  status: 503,
  body: JSON.stringify({ detail: "模擬後端已入帳、但回應在網路中遺失" }),
});
// 使用者直接再點一次；兩次 request 必須沿用同一 key，DB 只准有一筆。
```

**白話：** 最常見的真實情境不是使用者故意連點，而是後端已入帳、Wi-Fi 卻在回應途中斷掉。
畫面會保留原內容讓使用者直接重試；同一筆調整會拿回原紀錄。同一 key 若換金額或事由則回
409，不會把不同內容誤認成同一筆。實機測試真的先讓後端入帳，再把回應改成 503；重試後
確認兩次沿用同一 key，PostgreSQL 只有一筆 137 元異動。

### P1-8：已處理 — LINE Pay 已退款但本地失敗時會自動補完，絕不重退

`backend/app/modules/sales/inputs.py:73-80`

```python
class LinePayReturnRecovery:
    attempt_id: int
    refund_key: str
    store_id: int
    sale_id: int
    lines: tuple[LinePayReturnRecoveryLine, ...]
```

`backend/app/modules/returns/service.py:153-172`

```python
await self.create_return(
    ...,
    # SUCCEEDED 日誌已是平台真相；None 只允許套用日誌，不再送 refund。
    linepay_client=None,
    reconciled_linepay_refund_key=recovery.refund_key,
    allow_expired_provider_consent=True,
)
```

**白話：** 呼叫平台前先把本地退貨所需內容持久保存。若平台已成功但本地 transaction 掛掉，
背景排程直接沿用平台成功當時保存的 `refund_key`，不會因為期間又完成另一筆退貨、累計已退數量
改變而換成新 key。尚未補完時，一般人工退貨也會被
`backend/app/modules/returns/service.py:440-443` 阻擋，避免使用者重送第二次平台退款。

`backend/app/modules/signing/service.py:1084-1118`

```python
valid_status = task.status is SignatureTaskStatus.SIGNED or (
    allow_expired_provider_recovery and task.status is SignatureTaskStatus.EXPIRED
)
# 仍逐項核對退貨品項、發票、處置方式與退款金額後才 consume。
```

**逾期同意的口徑：** 正常新退貨仍只能用 `SIGNED` 同意；唯一例外是 LINE Pay 已經退款成功、
現在只補本地帳，而且必須是同一張銷售、同一批品項、同一張發票、同一處置與同一退款金額。
背景工作也改成先補這類不可逆的外部退款，再做 TTL 清理：
`backend/app/modules/customerdisplay/background_service.py:119-140`。

`backend/app/modules/sales/service.py:1676-1688`

```python
candidate = await self._repo.get_refund_attempt(store_id, attempt_id)
txn = await self._repo.get_linepay_by_order_id(store_id, candidate.order_id)
if txn is not None:
    sale = await self._repo.lock_sale(store_id, txn.sale_id)
attempt = await self._repo.get_refund_attempt(store_id, attempt_id, for_update=True)
```

**併發口徑：** 正常退貨、背景補帳與店長人工裁定現在都固定先鎖銷售單、再鎖退款紀錄。
因此店長不能在另一個退貨剛通過檢查後插隊把 `PENDING` 改成 `SUCCEEDED`，背景排程也不會以
相反鎖序和正常退貨互卡。鎖序契約測試在
`backend/tests/test_linepay_refund_lock_order.py:11-107`。

整合測試
`backend/tests/integration/test_linepay_refund_recovery.py:187-260,269-341` 已驗證「另一筆退貨先完成」
仍只補原退款、人工重送會被擋，以及完全相符的逾期同意可補帳；實機瀏覽器另由店長按
「確認已退款」，再等待真實背景排程自動收斂成 `RETURNED|REFUNDED|137|IN_STOCK|1`。

### P1-9：店長接受現況 — 不特別限制 100% 抽成

`backend/app/modules/acquisition/schemas.py:27-28,52`

```python
COMMISSION_PCT_MIN = 0
COMMISSION_PCT_MAX = 100
commission_pct: int | None = Field(
    default=None, ge=COMMISSION_PCT_MIN, le=COMMISSION_PCT_MAX
)
```

`backend/app/modules/consignment/service.py:224-236`

```python
commission_amount = commission(gross, commission_pct)
payout = gross - Decimal(commission_amount)
# ...
payout_amount=payout,
```

`backend/app/modules/cashdrawer/models.py:75`

```python
CheckConstraint("amount <> 0", name="ck_cash_movement_amount_nonzero")
```

**白話：** 系統仍接受 0–100。若有人真的把單品設成 100%，賣家實拿會算成 0；付款時因
0 元現金異動不合法，整筆 transaction 會回滾，結算維持 `PENDING`，所以不會錯付或少一筆
現金帳，但使用者重試同一內容仍會失敗。店長已明確裁示實務上不會發生，不加限制，也不做
0 元付款特例。

## P2：修正後狀態

### P2-1：已處理 — 稅率與費率最多四位小數

`backend/app/modules/settings/schemas.py:133-139`

```python
@field_validator("tax_rate", "premium_rate", "premium_rate_min", "premium_rate_max")
def _rate_scale(cls, value: Decimal | None) -> Decimal | None:
    if value is not None and value != value.quantize(Decimal("0.0001")):
        raise ValueError("費率最多四位小數")
```

### P2-2：已處理 — 寫入 Numeric(12,0) 前擋住輸入值與衍生值溢位

`backend/app/core/money.py:22-36`

```python
MAX_NTD = Decimal("999999999999")

def ensure_ntd_fits_numeric_12(value: Decimal, *, field: str = "金額", absolute: bool = False) -> Decimal:
    if abs(value) > MAX_NTD:
        raise ValueError(...)
    return value
```

除了單一輸入欄位，購物金新餘額、錢櫃關帳應有金額與差額等「真的會寫進
`Numeric(12,0)`」的衍生值，也在寫 DB 前擋下。採購單只持久化數量與單價，沒有明細小計或
整單總額欄位，因此只限制單價本身，不把 `數量 × 單價` 當成 DB 欄位上限：

`backend/app/modules/purchasing/schemas.py:48-66`

```python
class PurchaseOrderLineCreate(BaseModel):
    qty: int = Field(gt=0)
    unit_cost: NTDAmount

    @field_validator("unit_cost")
    def _positive_whole(cls, value: Decimal) -> Decimal:
        ensure_ntd_fits_numeric_12(value, field="unit_cost ")
        return value
```

`backend/tests/test_money_schema_limits.py:80-87` 與
`backend/tests/integration/test_purchasing_api.py:539-560` 證明數量與單價各自可寫 DB 時，即使兩者
乘積超過 `MAX_NTD` 仍可建立，不會用不存在的 DB 欄位限制合法採購單。

`backend/app/modules/storecredit/service.py:104-115,158-164`

```python
if abs(signed_amount) > MAX_NTD:
    raise StoreCreditConflict(...)
if cash_equivalent is not None and abs(cash_equivalent) > MAX_NTD:
    raise StoreCreditConflict(...)
# ...
if new_balance > MAX_NTD:
    raise StoreCreditConflict(...)
```

`backend/app/modules/cashdrawer/service.py:258-263`

```python
if abs(expected) > MAX_NTD:
    raise CashAmountOutOfRange(...)
variance = counted_amount - expected
if abs(variance) > MAX_NTD:
    raise CashAmountOutOfRange(...)
```

**白話：** 這兩種不是一般單筆交易會碰到，而是帳戶／錢櫃長期累加、資料匯入或異常大量
交易時才可能發生。原本會到 PostgreSQL 寫入才爆成 500；現在會在 service 明確拒絕，整筆
transaction 回滾，不留下半套帳。購物金案例由
`backend/tests/integration/test_store_credit.py:153-196` 覆蓋；錢櫃案例由
`backend/tests/integration/test_cashdrawer_api.py:391-454` 覆蓋。

### P2-3：已處理 — 錢櫃帳由 DB 背書 insert-only 與金額形狀

`backend/app/modules/cashdrawer/models.py:75`

```python
CheckConstraint("amount <> 0", name="ck_cash_movement_amount_nonzero")
CheckConstraint("type = 'MANUAL_ADJUST' OR amount > 0",
                name="ck_cash_movement_system_amount_positive")
```

`backend/app/modules/cashdrawer/schemas.py:75-82`

```python
value = _whole(v, allow_negative=True)
if value == 0:
    raise ValueError("人工現金調整金額不可為零")
```

**白話：** API 現在會把 0 元人工調整當成可理解的 422 輸入錯誤，不再一路送到 DB 才變成
500；真正繞過 API 的寫入仍由 DB CHECK 擋住。

`backend/app/modules/cashdrawer/models.py:125`

```sql
CREATE TRIGGER trg_cash_movement_immutable
BEFORE UPDATE OR DELETE ON cash_movements
FOR EACH ROW EXECUTE FUNCTION cash_movement_immutable()
```

### P2-4：已處理 — migration 不再引用會漂移的 live model 常數

`backend/alembic/versions/a8c1f4e7b2d5_cashflow_audit_guards.py:7`

```python
All trigger SQL is frozen in this revision. Do not import model constants here.
```

此 migration 會收斂完整購物金 trigger 集合，並加入錢櫃、寄售與 LINE Pay 退款復原欄位；
`backend/tests/test_cashflow_audit_migration.py:14-26` 檢查 migration 無 live import 且名稱齊全。

### P2-5：已處理 — 測試 trigger bypass 被隔離在一次性測試 DB

`backend/tests/db_safety.py:13`

```python
TEST_DATABASE_NAME = f"{_base_name}_test_{os.getpid()}"
os.environ["DATABASE_URL"] = _base_url.set(database=TEST_DATABASE_NAME).render_as_string(...)
```

`backend/tests/conftest.py:37-77` 在 suite 開始建立該 DB，結束精確刪除此 DB。現金帳測試清理 helper
另會先檢查 DB 名稱含 `_test_`，不符合就拒絕停用 trigger。正式／開發 DB 不在清理範圍。

### P2-6：已處理 — 一般 fixture 明確觸發 deferred 金流守衛

`backend/tests/integration/test_sales_tenders.py:167-172`

```python
with pytest.raises(DBAPIError, match="收款明細加總"):
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
```

### P2-7：已受控 — 報價與成交以可執行契約鎖住同一金額結果

`backend/tests/integration/test_sales_campaign_discount.py:258`

```python
assert sale.total == quote.total
for quoted, actual in zip(quote.lines, persisted, strict=True):
    assert (actual.unit_price, actual.line_total, actual.discount_amount, actual.net_amount) == (
        quoted.unit_price, quoted.line_total, quoted.discount_amount, quoted.net_amount
    )
```

**白話：** 依本輪「不做重構」原則，沒有搬動深層定價架構；改以序號品、一般品、散裝品的
逐欄契約測試防止日後報價與成交悄悄漂移。

### P2-8：已處理 — 寄售結算恆等式由 DB 強制

`backend/app/modules/consignment/models.py:26-37`

```python
CheckConstraint("commission_pct BETWEEN 0 AND 100", ...)
CheckConstraint("commission_amount + payout_amount = gross", ...)
```

直接改壞 `payout_amount` 或把抽成改成 101 都會被 DB 拒絕：
`backend/tests/test_sales.py:219-234`。

### P2-9：已處理 — LINE Pay 背景排程只負責定時喚醒 service

`backend/app/modules/customerdisplay/scheduler.py:14-30`

```python
async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        carts, tasks, retention_due = await CustomerDisplayBackgroundService.sweep_once()
```

`backend/app/modules/customerdisplay/background_service.py:83-117`

```python
async def _recover_refund_attempt(session, attempt_id):
    if not await ReturnsService(session).recover_succeeded_linepay_refund(attempt_id):
        return False
    await session.commit()
    return True
```

**白話：** scheduler 現在只管「每分鐘叫醒一次」；先補外部已成功退款、再清理簽署 TTL、
每筆各自 commit／rollback 的業務順序與 transaction 邊界都在 background service。它只跨模組
呼叫 service；退款復原協調集中在 returns service，sales 讀寫仍走自己的 service／repository，
沒有另一條背景路徑繞過正常金流規則。靜態分層契約由
`backend/tests/test_customerdisplay_scheduler_boundary.py:4-16` 鎖住。

### P2-10：已處理 — 人工現金調整的冪等內容由 service 定義

`backend/app/modules/cashdrawer/service.py:156-187`

```python
async def record_manual_adjustment(...):
    fingerprint = hashlib.sha256(
        canonical_json_bytes({
            "session_id": session_id,
            "type": CashMovementType.MANUAL_ADJUST.value,
            "amount": amount,
            "note": note,
        })
    ).hexdigest()
    return await self.record_movement(...)
```

`backend/app/modules/cashdrawer/router.py:103-116` 只呼叫此 service 並 commit／處理 409。這讓
HTTP router 不必自己決定「同一冪等鍵是否為同一筆錢」的業務規則。

### P2-11：已處理 — 五條受影響金流 UI 都有真後端／PostgreSQL smoke

`frontend/scripts/purchasing-smoke.mjs:99-110`

```javascript
await page.fill('input[aria-label="發票未稅金額"]', "1000");
await page.fill('input[aria-label="發票稅額"]', "50");
await page.fill('input[aria-label="發票含稅金額"]', "1050");
await page.click('[role="dialog"] button:has-text("確認收貨")');
```

**白話：** smoke 現在不是只填總額；它會像現場人員一樣輸入原始發票未稅、稅額、含稅
總額，再真的送到後端與 PostgreSQL。此次隔離環境實跑 19/19 通過並保存操作截圖。

`frontend/scripts/acquisition-smoke.mjs:67-80`

```javascript
ok("寄售新列顯示設定的 37% 抽成", (await commissionInputs.first().inputValue()) === "37");
await commissionInputs.nth(1).fill("42");
ok("逐件修改不影響其他品項", ...);
```

`frontend/scripts/linepay-refund-recovery-smoke.mjs:174-200`

```javascript
await row.getByRole("button", { name: "確認已退款" }).click();
await row.getByText("不需再次退款", { exact: true }).waitFor();
// 等真實背景排程後查 DB 最終狀態
finalState === "RETURNED|REFUNDED|137|IN_STOCK|1";
```

`frontend/scripts/taiwanpay-smoke.mjs:181-213`

```javascript
await page.locator(".pos-tender-mode", { hasText: "LINE Pay" }).click();
ok("LINE Pay HALF_UP 手續費顯示", linepayFeeHint?.includes(money(fee)) ?? false);
await page.locator(".pos-tender-mode", { hasText: "台灣Pay" }).click();
ok("台灣Pay HALF_UP 手續費顯示", feeHint?.includes(money(fee)) ?? false);
await checkoutBtn.click();
```

另有 `frontend/scripts/cash-smoke.mjs:59-114` 的「已入帳但回應遺失後重試」。五條隔離實機
結果分別為：採購 19/19、寄售抽成 17/17、錢櫃重試 4/4、LINE Pay 退款復原 5/5、POS
行動支付費率 14/14。最後一條真的以 5,000 元、0.03% 費率切換 LINE Pay／台灣 Pay，兩邊
畫面都顯示 HALF_UP 後的 2 元，並以台灣 Pay 完成結帳；API 與 DB tender 也都是金額 5,000、
手續費 2。

## 待確認：修正後狀態

### 待確認-1：仍需部署後查驗 — 正式 DB 是否已套用新 migration

`backend/alembic/versions/a8c1f4e7b2d5_cashflow_audit_guards.py:175`

```python
def upgrade() -> None:
    ...
    op.execute(_CASH_IMMUTABLE_FUNCTION)
    op.execute(_CASH_IMMUTABLE_TRIGGER)
```

**需要的資訊：** 正式環境執行 `alembic upgrade head` 後的 revision，以及 `pg_trigger`／
`pg_constraint` 查詢結果。repo 內 migration 已備妥，但本 session 沒有正式 DB 部署權限，不能把
「程式碼已存在」寫成「正式 DB 已生效」。

### 待確認-2：已定案並處理 — 進項發票照原始發票登錄

`backend/app/modules/purchasing/schemas.py:160-186`

```python
invoice_net: NTDAmount
invoice_tax: NTDAmount
invoice_total: NTDAmount
```

```python
if self.invoice_net + self.invoice_tax != self.invoice_total:
    raise ValueError("原始發票未稅額＋稅額必須等於總額")
```

不再用補登當天的系統稅率反推歷史憑證。

### 待確認-3：已查官方規格並處理 — Amego 單價最多七位小數

光貿[官方 API 文件](https://invoice.amego.tw/api_doc/)載明 `UnitPrice` 最多 7 位小數，
`DetailAmountRound=1` 可令明細小計四捨五入為整數。現況：

`backend/app/modules/einvoice/amego.py:62-64`

```python
return _decimal_str(value.quantize(Decimal("0.0000001"), rounding=ROUND_HALF_UP))
```

`backend/app/modules/einvoice/amego.py:126`

```python
"DetailAmountRound": 1,
```

測試 `backend/tests/test_einvoice_amego.py:184-195` 驗證 100 ÷ 3 送 `33.3333333`，而權威
`Amount` 仍是 `100`。

### 待確認-4：店長已裁示 — 現階段固定 5%

沒有修改稅額公式或把非 5% 行為另做一套。此裁示同時是 P1-5 接受現況的前提；未來若開放
店別改稅率，需重新稽核歷史報表與發票稅率快照的一致性。

## 修正後驗證

- 前端：TypeScript、ESLint、Vitest 全數通過；55 個測試檔、482 筆測試。
- 後端：Ruff 與 mypy 全數通過（mypy 檢查 184 個 app 來源檔）；完整 pytest 共 1,556 筆全數通過，
  總覆蓋率 88.36%（門檻 80%）。完整測試從空白的一次性 DB 跑 `alembic upgrade head`，
  因此也實際驗證了整條 migration 可建庫。
- DB 重點測試：LINE Pay 收款對帳／退款復原、錢櫃 DB trigger、寄售結算 CHECK、折讓稅尾差、
  migration 凍結、deferred tender guard、報價／成交一致、Numeric 上界均已納入自動測試。
- 實機流程：以隔離的真 PostgreSQL、真 backend 與 Playwright 操作頁面。採購原始發票 19/19；
  寄售抽成畫面預設 37、逐件可改 42（17/17）；錢櫃模擬「後端已入帳、回應遺失」後由使用者
  重試，同一 key 且 DB 只有一筆 +137（4/4）；店長確認 LINE Pay 已退款後，真實背景排程自動
  補完本地退貨，DB 最終為 `RETURNED|REFUNDED|137|IN_STOCK|1`（最新 service／鎖序版重跑 5/5）；POS 同時驗證 LINE Pay／
  台灣 Pay 的 5,000 × 0.03% 均顯示 2 元，並完成台灣 Pay 結帳與 DB tender 核對（14/14）。

---

以下為修正前唯讀稽核快照；其中的「不符合／待確認」是當時狀態，現況以本文件上方
「修正後結論」為準。

## 階段 1：金額相關 model／service／endpoint 盤點

欄位口徑：

- 「DB 寫入」是指該列所列檔案在一般執行路徑會執行 DML、`flush`、`commit`，或間接呼叫這類路徑；單純 ORM/schema 定義另行標示。
- 「外部副作用」是 DB 以外的作用，例如 LINE Pay／Amego HTTP、SSE、檔案／物件儲存、實體列印或開錢櫃。
- 同一 router 同時有讀寫端點時，以「寫端點」標示；職責欄列出本次金流稽核相關端點。

| 模組 | 檔案 | 職責 | 是否觸及 DB 寫入 | 是否有外部副作用 |
|---|---|---|---|---|
| 共用金額核心 | `backend/app/core/money.py` | 新台幣整數元 `round_ntd`、折扣、含稅定價、含稅拆稅、寄售抽成 | 否 | 否 |
| 金額 canonical／冪等指紋 | `backend/app/core/canonical.py` | Decimal canonical JSON；拒絕 float，供銷售等流程建立穩定指紋 | 否 | 否 |
| DB session／transaction 基礎 | `backend/app/core/db.py` | async engine、session factory、每 request session 生命週期；各 router 的 commit／rollback 基礎 | 否（僅提供 session） | 否 |
| 金額異動稽核 | `backend/app/core/audit.py` | `audit_log` model 與 insert-only 寫入；保存改價、付款、現金調整等 before／after | 是（INSERT／flush） | 否 |
| 系統金流設定－model／邊界 | `backend/app/modules/settings/models.py`<br>`backend/app/modules/settings/defaults.py`<br>`backend/app/modules/settings/schemas.py` | `tax_rate`、寄售預設抽成、購物金溢價率與最低消費、行動支付費率、發票開關的型別、預設值與輸入輸出 | 否（schema／ORM 定義） | 否 |
| 系統金流設定－service／endpoint | `backend/app/modules/settings/repository.py`<br>`backend/app/modules/settings/service.py`<br>`backend/app/modules/settings/router.py` | 讀寫設定、溢價率歷史；`GET/PATCH /settings`、`GET /settings/premium-rate/history` | 是（UPDATE／INSERT／commit） | 否 |
| 收購－model | `backend/app/modules/acquisition/models.py` | 收購／寄售單頭、現金腿與購物金現金等值；含收購－庫存－購物金 DB 背書 guard | 否（ORM／constraint DDL 定義） | 否 |
| 收購－repository／service | `backend/app/modules/acquisition/repository.py`<br>`backend/app/modules/acquisition/service.py` | 買斷、寄售、散裝建單；加總收購成本、拆現金／購物金撥款、建庫存、寫錢櫃與購物金、產生補印憑證資料；作廢入口僅列入盤點、不在本輪展開 | 是（跨模組 INSERT／UPDATE／flush） | 否 |
| 收購－schema／endpoint | `backend/app/modules/acquisition/schemas.py`<br>`backend/app/modules/acquisition/router.py` | 金額字串邊界；`POST /acquisitions`、`GET /acquisitions/{id}`、`GET /acquisitions/{id}/receipt`、作廢端點（僅盤點） | 是（寫端點 commit） | 否；列印由前端／硬體代理另觸發 |
| 庫存與上架定價－model | `backend/app/modules/inventory/models.py` | 序號品收購成本／上架價、散裝批總成本／單價／數量、一般商品售價／成本、分類最低倍數 | 否（ORM 定義） | 否 |
| 庫存與上架定價－repository／service | `backend/app/modules/inventory/repository.py`<br>`backend/app/modules/inventory/service.py`<br>`backend/app/modules/inventory/pricing_defaults.py` | 改價、含稅建議售價、散裝每件成本、庫存狀態轉移與原子扣量、估值資料來源 | 是（INSERT／條件 UPDATE／flush） | 否 |
| 庫存與上架定價－endpoint | `backend/app/modules/inventory/schemas.py`<br>`backend/app/modules/inventory/router.py` | 序號品／散裝／一般商品讀取與改價、商品建立、分類定價規則讀寫 | 是（寫端點 commit） | 否 |
| 餐飲品項價格 | `backend/app/modules/menu/models.py`<br>`backend/app/modules/menu/repository.py`<br>`backend/app/modules/menu/service.py`<br>`backend/app/modules/menu/schemas.py`<br>`backend/app/modules/menu/router.py` | `menu_items.unit_price` 建立、修改與 POS 取價；`/menu-items` 讀寫端點 | 是（寫端點 INSERT／UPDATE／commit） | 否 |
| 活動折扣／折扣原因 | `backend/app/modules/campaigns/models.py`<br>`backend/app/modules/campaigns/repository.py`<br>`backend/app/modules/campaigns/service.py`<br>`backend/app/modules/campaigns/schemas.py`<br>`backend/app/modules/campaigns/router.py`<br>`backend/app/modules/sales/reasons.py`<br>`backend/app/modules/sales/reasons_router.py` | 活動折扣率、生效狀態、適用品類；贈品／臨時折扣原因主檔；`/campaigns`、`/gift-reasons`、`/discount-reasons` | 是（主檔 INSERT／UPDATE／commit） | 否 |
| 銷售／POS－model | `backend/app/modules/sales/models.py` | 銷售總額與稅額、明細原價／成交價／折扣／淨額／成本快照、tender 與手續費、購物金套用、LINE Pay 交易／退款嘗試；含跨表 DB guard | 否（ORM／constraint DDL 定義） | 否 |
| 銷售／POS－定價純邏輯 | `backend/app/modules/sales/pricing.py` | 訂單／品項折扣與最大餘額法分攤 | 否 | 否 |
| 銷售／POS－repository／service | `backend/app/modules/sales/repository.py`<br>`backend/app/modules/sales/service.py` | 報價、結帳、折扣、贈品、總額拆稅、tender 對平、購物金核銷、錢櫃收現、庫存扣減、寄售結算、毛利／手續費彙總、冪等重放；作廢邏輯僅列入盤點 | 是（跨模組 INSERT／UPDATE／flush；部分 durable commit） | 是（LINE Pay 收款／退款 HTTP） |
| 銷售／POS－輸入輸出／endpoint | `backend/app/modules/sales/inputs.py`<br>`backend/app/modules/sales/schemas.py`<br>`backend/app/modules/sales/router.py` | 金額字串 schema；`POST /sales`、`POST /sales/quote`、銷售查詢、`POST /sales/{id}/print-detail`、LINE Pay 退款對帳；作廢端點僅盤點 | 是（寫端點 commit／rollback） | 是（建立 LINE Pay client 並由 service 呼叫平台）；此端點不直接列印 |
| LINE Pay client | `backend/app/modules/sales/linepay.py` | LINE Pay Offline 收款、查詢、退款的簽章、order id 與 HTTP transport | 否 | 是（LINE Pay HTTP） |
| 購物金－model／DB guard | `backend/app/modules/storecredit/models.py` | append-only ledger、`signed_amount`、`balance_after`、現金等值／溢價率、帳戶快取餘額、建議率紀錄與 DB triggers／constraints | 否（ORM／constraint DDL 定義） | 否 |
| 購物金－repository／service | `backend/app/modules/storecredit/repository.py`<br>`backend/app/modules/storecredit/service.py` | 帳戶列鎖、唯一分錄插入、入帳／扣抵／退款回補／沖正／人工校正、快取餘額、SUM 重算與鏈結對帳；購物金唯一一般寫入介面 | 是（INSERT ledger、UPDATE account、flush） | 否 |
| 購物金－schema／endpoint | `backend/app/modules/storecredit/schemas.py`<br>`backend/app/modules/storecredit/router.py` | `GET /contacts/{id}/store-credit`、`POST .../adjustments`、當日溢價建議 | 是（校正與建議紀錄 commit） | 否 |
| 購物金－建議／效益指標 | `backend/app/modules/storecredit/engine.py`<br>`backend/app/modules/storecredit/metrics.py`<br>`backend/app/modules/storecredit/suggestion_service.py` | 溢價建議純計算、效益指標、每日建議快照 | 是（suggestion service 寫建議紀錄；engine／metrics 純讀算） | 否 |
| 寄售結算－model | `backend/app/modules/consignment/models.py` | `gross`、抽成率、`commission_amount`、`payout_amount`、付款／追回狀態 | 否（ORM 定義） | 否 |
| 寄售結算－repository／service | `backend/app/modules/consignment/repository.py`<br>`backend/app/modules/consignment/service.py` | 售出建立結算、抽成與應付計算、付款列鎖／冪等、錢櫃現金流出、報表彙總；退貨／作廢反轉僅盤點相關寫入 | 是（INSERT／UPDATE／flush） | 否 |
| 寄售結算－schema／endpoint | `backend/app/modules/consignment/schemas.py`<br>`backend/app/modules/consignment/router.py` | `GET /consignment/settlements`、`POST /consignment/settlements/{id}/pay` | 是（付款端點 commit） | 否 |
| 錢櫃－model | `backend/app/modules/cashdrawer/models.py` | 開帳金、實點、應有、差異與每筆 cash movement 金額 | 否（ORM 定義） | 否 |
| 錢櫃－repository／service | `backend/app/modules/cashdrawer/repository.py`<br>`backend/app/modules/cashdrawer/service.py` | 開帳、班別／movement 列鎖、銷售收現／收購付現／寄售付款／退貨退現／人工調整、應有現金與結帳差異 | 是（INSERT／UPDATE／flush） | 否；不直接驅動實體錢櫃 |
| 錢櫃－schema／endpoint | `backend/app/modules/cashdrawer/schemas.py`<br>`backend/app/modules/cashdrawer/router.py` | `/cash-sessions` 開帳、current、movements、close | 是（寫端點 commit） | 否 |
| 退貨／退款－model | `backend/app/modules/returns/models.py` | 退貨單／明細退款額、退款 tender 金額 | 否（ORM 定義） | 否 |
| 退貨／退款－純計算 | `backend/app/modules/returns/refund.py`<br>`backend/app/modules/returns/invoice_policy.py` | 累計差額退款額與退貨時發票處置決策 | 否 | 否 |
| 退貨／退款－repository／service／endpoint | `backend/app/modules/returns/repository.py`<br>`backend/app/modules/returns/service.py`<br>`backend/app/modules/returns/schemas.py`<br>`backend/app/modules/returns/router.py` | `POST /returns/preview`、`POST /returns`、退貨查詢；退款分配、庫存回復、購物金回補、錢櫃退現、寄售結算反轉、發票折讓佇列 | 是（跨模組 INSERT／UPDATE／commit） | 是（LINE Pay 退款 HTTP）；發票平台交付由 e-invoice 路徑處理 |
| 電子發票／折讓－model | `backend/app/modules/einvoice/models.py` | 發票／折讓的未稅、稅、含稅總額與稅率快照；上傳 queue／結果事件 | 否（ORM 定義） | 否 |
| 電子發票／折讓－repository／service／序列化 | `backend/app/modules/einvoice/repository.py`<br>`backend/app/modules/einvoice/service.py`<br>`backend/app/modules/einvoice/serializer.py`<br>`backend/app/modules/einvoice/dropper.py` | 依銷售總額建發票、總額層拆稅、登記紙本、折讓、queue 認領／送出／結果回寫、補印 payload 與證明聯列印標記；作廢路徑僅盤點 | 是（INSERT／UPDATE／多階段 commit） | 是（Amego HTTP；舊 Turnkey outbox 檔案曝光） |
| Amego client | `backend/app/modules/einvoice/amego.py` | 開立／查詢／作廢／折讓／補印 API payload、解析與 HTTP transport | 否 | 是（Amego HTTP） |
| 電子發票／折讓－schema／endpoint | `backend/app/modules/einvoice/schemas.py`<br>`backend/app/modules/einvoice/router.py` | `/invoices/{id}`、`/einvoice/queue`、銷售開票、補印資料、證明聯列印標記、紙本登記、queue 送出／重試／結果回報；作廢路徑僅盤點 | 是（寫端點 commit；送出端點有多階段落庫） | 是（Amego HTTP／補印資料取得） |
| 採購／進項－model | `backend/app/modules/purchasing/models.py` | 採購明細單位成本、收貨與進項發票含稅／未稅／稅額 | 否（ORM 定義） | 否 |
| 採購／進項－service／endpoint | `backend/app/modules/purchasing/repository.py`<br>`backend/app/modules/purchasing/service.py`<br>`backend/app/modules/purchasing/schemas.py`<br>`backend/app/modules/purchasing/router.py` | 採購單建立／收貨入庫成本、進項發票登記與拆稅；`/purchase-orders`、`/purchase-orders/{id}/receipts/{receipt_id}/invoice` 等端點 | 是（INSERT／UPDATE／commit） | 否 |
| 會員金額彙總 facade | `backend/app/modules/contacts/member_service.py`<br>`backend/app/modules/contacts/member_schemas.py`<br>`backend/app/modules/contacts/service.py`<br>`backend/app/modules/contacts/router.py` | 會員購買金額／tender、購物金餘額、寄售待付與抽成、來源商品價格；`/contacts/{id}/overview`、purchases、consignments、sourced-items | 混合：金額彙總端點唯讀；同 router 的聯絡人寫端點會 commit | 否 |
| 財務／購物金報表 | `backend/app/modules/reports/service.py`<br>`backend/app/modules/reports/aging.py`<br>`backend/app/modules/reports/export.py`<br>`backend/app/modules/reports/router.py`<br>`backend/app/modules/reports/finance_router.py`<br>`backend/app/modules/reports/schemas.py` | 現金日報、摘要、趨勢、庫存價值、寄售應付、銷售毛利／折扣／活動、購物金負債／流量／效益／對帳與匯出 | 否（讀取／彙總） | 否（僅回傳 JSON／CSV／Excel response） |
| 客顯購物車／付款協調－model | `backend/app/modules/customerdisplay/models.py` | server-authoritative cart JSONB 金額快照、tender、revision、付款狀態與 append-only event | 否（ORM／constraint DDL 定義） | 否 |
| 客顯購物車／付款協調－service／endpoint | `backend/app/modules/customerdisplay/repository.py`<br>`backend/app/modules/customerdisplay/service.py`<br>`backend/app/modules/customerdisplay/schemas.py`<br>`backend/app/modules/customerdisplay/router.py` | 客顯購物車報價快照、購物金簽署凍結、checkout、付款不確定狀態與對帳；`/customer-display/*`、`/kiosk/*`、SSE | 是（cart／event／付款狀態 INSERT／UPDATE／commit） | 是（SSE；LINE Pay 查詢／收款；背景 sweep） |
| 簽署金額快照 | `backend/app/modules/signing/models.py`<br>`backend/app/modules/signing/repository.py`<br>`backend/app/modules/signing/service.py`<br>`backend/app/modules/signing/schemas.py`<br>`backend/app/modules/signing/router.py` | 收購切結與購物金核銷的 content JSONB 金額快照、內容指紋、簽署／消耗狀態；`/signing/*`、`/kiosk/tasks/*` | 是（task／event INSERT／UPDATE／commit） | 否 |
| 前端共用金額／付款摘要 | `frontend/lib/money.ts`<br>`frontend/lib/payment.ts`<br>`frontend/features/settings/helpers.ts`<br>`frontend/features/reports/reports.ts` | NTD 解析／顯示、tender 付款摘要、費率／百分比與報表數值顯示 | 否 | 否 |
| 前端收購定價 | `frontend/features/acquisition/pricing.ts`<br>`frontend/features/acquisition/validation.ts` | 含稅建議售價、未稅反推、毛利率、應付總額、現金／購物金拆分與溢價預覽 | 否 | 否 |
| 前端 POS／退款／採購／現金計算 | `frontend/features/pos/cart.ts`<br>`frontend/features/pos/discounts.ts`<br>`frontend/features/pos/tender.ts`<br>`frontend/features/returns/refund.ts`<br>`frontend/features/returns/plan.ts`<br>`frontend/features/purchasing/purchasing.ts`<br>`frontend/features/cash/money-input.ts`<br>`frontend/features/inventory/inventory.ts` | 購物車加總、折扣 payload、tender 計畫與找零、退貨預覽、採購草稿總額、現金輸入與庫存價格解析 | 否 | 否 |
| 前端 API／冪等邊界 | `frontend/lib/api.ts`<br>`frontend/lib/api-types.ts`<br>`frontend/lib/idempotency.ts` | 型別化 API、金額字串合約、收購／銷售／收貨等重試用 idempotency key 保存與清除 | 間接（呼叫後端寫入端點） | 是（後端 HTTP） |
| 前端金流操作畫面 | `frontend/app/(authed)/pos/page.tsx`<br>`frontend/app/(authed)/sales/page.tsx`<br>`frontend/app/(authed)/acquisition/page.tsx`<br>`frontend/app/(authed)/cash/page.tsx`<br>`frontend/app/(authed)/consignment/page.tsx`<br>`frontend/app/(authed)/inventory/page.tsx`<br>`frontend/app/(authed)/menu/page.tsx`<br>`frontend/app/(authed)/campaigns/page.tsx`<br>`frontend/app/(authed)/purchasing/page.tsx`<br>`frontend/app/(authed)/reports/page.tsx`<br>`frontend/app/(authed)/settings/page.tsx`<br>`frontend/app/(authed)/contacts/page.tsx`<br>`frontend/app/(authed)/contacts/[id]/page.tsx` | 送出建單／結帳／付款／改價／折扣／現金／設定請求，顯示後端金額，並協調列印／開錢櫃 | 間接（呼叫後端 API） | 是（後端與 hardware-agent HTTP、實體列印／開錢櫃） |
| 前端客顯／SSE | `frontend/features/customer-display/PosCustomerDisplay.tsx`<br>`frontend/app/kiosk/page.tsx` | 顯示後端 cart snapshot、送簽署／付款狀態、SSE 重連 | 間接（呼叫後端 API） | 是（HTTP／SSE） |
| 前端列印金額轉送 | `frontend/lib/agent.ts` | 將後端 `SaleRead`、`InvoiceRead`、`AcquisitionReceiptRead` 金額原樣組成 hardware-agent payload；發票用 `invoice.net/tax/total` | 否 | 是（hardware-agent HTTP、列印／開錢櫃） |
| 硬體代理列印資料 model | `hardware-agent/agent/interfaces.py` | 收據明細、tender、收購憑證、發票未稅／稅／含稅金額的字串 payload；代理宣告只排版不計價 | 否 | 否 |
| 硬體代理列印／錢櫃 endpoint | `hardware-agent/agent/main.py`<br>`hardware-agent/agent/routers/print.py`<br>`hardware-agent/agent/devices.py`<br>`hardware-agent/agent/drivers/escpos_receipt.py`<br>`hardware-agent/agent/drivers/einvoice_format.py`<br>`hardware-agent/agent/drivers/brother_label.py` | `/print/detail`、`/print/acquisition`、`/print/einvoice`、`/print/raw`、`/print/label`、`/drawer/open` 等入口與金額／價格排版；協定細節不在本稽核展開 | 否 | 是（實體列印／開錢櫃） |
| 備份／還原跨切面 | `backend/app/modules/backup/service.py`<br>`backend/app/modules/backup/restore.py`<br>`backend/app/modules/backup/restore_service.py`<br>`backend/app/modules/backup/backend.py`<br>`backend/app/modules/backup/router.py` | 整庫備份、R2／本機保存、還原到拋棄式 DB 並驗證 `sales`、`return_tenders`、`cash_sessions`、`store_credit_ledger`、`invoices` 等金流表；`/backup/*` | 是（備份／還原 metadata；拋棄式 DB restore） | 是（pg_dump／pg_restore、檔案、R2、背景工作） |

## 階段 2：逐條查證

本階段僅作靜態原始碼、migration、fixture 與 git 歷史的唯讀查證；未執行測試、未連線或查詢任何部署中資料庫，也未修改實作或測試。

### 結論摘要

| 嚴重度 | 數量 | 摘要 |
|---|---:|---|
| P0 | 1 | LINE Pay 可在沒有客顯購物車時先完成平台扣款；若其後本機交易失敗，後端沒有可持久化的付款待對帳載體 |
| P1 | 8 | 寄售預設抽成、散裝成本、折讓稅尾差、歷史報表稅率、前端 float、人工現金調整冪等、LINE Pay 退款外部邊界 |
| P2 | 8 | 稅率 scale、Numeric 上界、現金帳 DB 守衛、購物金 migration／fixture／deferred-trigger 測試、報價與成交雙實作、寄售結算 DB 不變量 |
| 待確認 | 4 | 部署 DB trigger 實況、進項發票稅率政策、Amego 單價 scale、營業稅率是否允許店別調整 |

### A–E 查核矩陣

| 編號 | 查核項目 | 判定 | 證據／結論 |
|---|---|---|---|
| A-1 | 金額全程 Decimal／整數；float 與 JSON | **不符合** | 後端核心與 API 輸出為 `Decimal`／字串，canonical JSON 拒絕 float；但前端費用與購物金溢價預覽以 `number × number`、`Math.round` 計算，存在 1 元顯示差，見 P1-6。 |
| A-2 | DB Numeric precision／scale 與程式端一致 | **不符合** | 多數金額 DB 為 `Numeric(12,0)`，輸入守整數但未普遍守 12 位上界；`tax_rate` DB 為 `Numeric(5,4)`，PATCH 未守四位 scale，見 P2-1、P2-2。 |
| A-3 | 四捨五入時機／方向在後端、前端、發票一致 | **不符合** | 核心後端統一 `ROUND_HALF_UP`，售價與稅額只在最後／總額層 round；發票與列印沿用落盤值。前端 `Math.round` 路徑不完全等價，散裝成本另在 DB scale coercion 才隱式取整，見 P1-2、P1-6。 |
| A-4 | 5% 含稅反推、逐項與總額課稅的 1 元差 | **不符合** | 銷售與原發票使用 `net=round(total/(1+rate))`、`tax=total-net`，符合總額層口徑；但多筆折讓各自拆稅會使全退累計的 net/tax 與原發票相差 1 元，見 P1-4。營業稅是否必須固定 5% 見待確認-4。 |
| B-1 | 購物金帳本 append-only，含 migration／fixture | **不符合** | production repository 只 INSERT 且 model 定義 UPDATE/DELETE trigger；但 migration 引用可變的 live constant，測試／模擬亦有停用 trigger 後 DELETE/TRUNCATE 的路徑，見 P2-4、P2-5 與待確認-1。 |
| B-2 | 餘額即時 SUM 或快取；重算／對帳 | **符合** | 對外讀 `store_credit_accounts.balance` 快取；每次寫入鎖帳戶、先比對 `SUM`／最新 `balance_after`／快取，DB trigger 原子同步；`reconcile()` 可全鏈重算並只回報不符。 |
| B-3 | 餘額小於 0 的可達路徑 | **符合** | 一般 service 寫入在 `new_balance < 0` 時拒絕；ledger 與 account 另有 `balance_after >= 0`、`balance >= 0` DB CHECK。未發現 production 寫入可達負餘額。 |
| C-1 | 主檔、明細、庫存、購物金、錢櫃、寄售結算同一 DB transaction | **符合** | 銷售、收購、退貨、寄售付款的 service 只 flush，由同一 request session 在 router 尾端一次 commit；收購另以 savepoint 保證 service 例外不留半套。外部 LINE Pay 不屬同一 DB transaction，另見 C-4。 |
| C-2 | async session 跨 task 共用／commit 時序 | **符合** | `get_session()` 每 request 建立一個 session；背景 sweeper 自行向 session factory 取新 session。SSE 長連線只在同一 generator task 唯讀，且每輪 rollback 釋放 transaction。未發現金流 request session 被交給 `create_task()`。 |
| C-3 | 同會員／同商品併發鎖策略 | **符合** | 購物金以 account `FOR UPDATE`；現金 movement／關帳以 cash session `FOR UPDATE`；序號品先按 id 上鎖，散裝與一般商品以條件 UPDATE 扣量；寄售付款鎖 settlement 並以 transaction advisory lock 序列化冪等鍵。 |
| C-4 | 外部副作用在 commit 後才觸發 | **不符合** | 實體列印／開櫃在前端收到成功回應後觸發；Amego 先 commit queue claim 再曝光。LINE Pay 收款與退款則在主交易 commit 前呼叫平台，見 P0-1、P1-8。 |
| D-1 | 重複結帳、前端重試、SSE 重連不重複帳／扣款 | **不符合** | 銷售／收購／退貨／購物金／寄售付款均有回放、唯一鍵或列鎖；SSE 重連只 invalidate 後 GET 全量狀態。但人工現金調整沒有冪等鍵，重送會再 INSERT，見 P1-7；無客顯 LINE Pay 的失敗後對帳見 P0-1。 |
| D-2 | idempotency key 或唯一約束 | **不符合** | `sales` 有 `(store_id,idempotency_key)` unique，購物金有來源 unique／manual key unique，收購與退貨亦有 unique；人工現金調整 request/model 無 key 或唯一約束，見 P1-7。 |
| E-1 | 0 元交易 | **符合** | 銷售只允許全單贈品為 0 元且不產生 tender／發票；BUYOUT／BULK_LOT 應付總額必須大於 0。 |
| E-2 | 全額購物金支付 | **符合** | 沒有 CASH tender 時不要求開帳；購物金仍要求會員、已凍結客顯購物車、簽署與帳戶鎖定餘額相符。 |
| E-3 | 購物金不足 | **符合** | 扣抵走同帳戶列鎖，權威 `SUM + signed_amount` 小於 0 即拒絕，整筆銷售由 router rollback。 |
| E-4 | 負數金額 | **符合** | 對外金額 schema 與 service 普遍拒絕負數；`MANUAL_ADJUST` 明確允許正負以表達現金校正，且需 manager／事由／audit。 |
| E-5 | 寄售抽成後賣家實拿＋店家抽成恆等售價 | **符合（production service）** | `commission_amount=round_ntd(gross*pct/100)`，`payout=gross-commission_amount`，所以 service 建立的列恆等；DB 本身未背書此等式，見 P2-8。 |

### 符合項目的主要實碼證據

後端金額核心使用 Decimal 與 `ROUND_HALF_UP`，含稅拆分在總額層只做一次，抽成以差額導出應付額：

`backend/app/core/money.py:24`

```python
def round_ntd(value: Decimal) -> int:
    """四捨五入（ROUND_HALF_UP）到整數元。"""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

`backend/app/core/money.py:64`

```python
def split_tax_inclusive(total: Decimal, rate: Decimal) -> tuple[int, int]:
    """將含稅總額拆為（未稅 net, 稅額 tax），保證 net + tax = total（整數元、不差一元）。

    稅於發票總額層級推算一次（不逐項算稅，見 CLAUDE.md §6）：
    `net = round_ntd(total / (1 + rate))`、`tax = total − net`。
    rate 為小數稅率（如 0.05），限 0 ≤ rate < 1。
    """
```

`backend/app/core/money.py:73`

```python
total_ntd = round_ntd(total)
net = round_ntd(total / (Decimal(1) + rate))
tax = total_ntd - net
```

金額證據用的 canonical JSON 明確拒絕 float：

`backend/app/core/canonical.py:19`

```python
if isinstance(value, Decimal):
    return format(value, "f")
if isinstance(value, float):
    raise ValueError("canonical JSON 不接受浮點數；金額必須用十進位字串")
```

購物金寫入先鎖帳戶並重算帳本，負餘額在寫入前拒絕：

`backend/app/modules/storecredit/service.py:131`

```python
account = await self._repo.lock_account(store_id, contact_id)
replay = await self._find_replay_locked(
    store_id,
    entry_type=entry_type,
    source_type=source_type,
    source_id=source_id,
    reversal_of_id=reversal_of_id,
    fingerprint=fingerprint,
    idempotency_key=idempotency_key,
)
```

`backend/app/modules/storecredit/service.py:146`

```python
ledger_balance = await self._repo.sum_signed(store_id, contact_id)
latest_after = await self._repo.latest_balance_after(store_id, contact_id)
cached = Decimal(account.balance)
if cached != ledger_balance or (latest_after is not None and latest_after != cached):
    raise StoreCreditConflict(
        f"contact {contact_id} 帳本/快取不一致（帳本 {ledger_balance}、"
        f"快取 {cached}），寫入中止——請先執行對帳處理"
    )
new_balance = ledger_balance + signed_amount
if new_balance < 0:
    raise InsufficientStoreCredit(
        f"contact {contact_id} 購物金餘額不足（{account.balance} {signed_amount:+}）"
    )
```

銷售的本機金流副作用同在 service 內，路由最後才 commit：

`backend/app/modules/sales/service.py:1081`

```python
await self._apply_tenders(
    store_id,
    sale,
    plan,
    clerk_user_id,
    buyer_contact_id,
    settings,
    idempotency_key=idempotency_key,
    linepay_client=linepay_client,
    reconciled_linepay_order_id=reconciled_order_id if line_pay_tenders else None,
    reconciled_linepay_result=reconciled_linepay_result,
    linepay_attempt=linepay_attempt,
)
```

`backend/app/modules/sales/router.py:389`

```python
try:
    lines = await svc.get_lines(sale.id)
    tenders = await svc.get_tenders(sale.id)
    await session.commit()
```

收購將建單、入庫、現金與購物金放在 savepoint／外層同一 transaction：

`backend/app/modules/acquisition/service.py:431`

```python
try:
    async with self._session.begin_nested():
        return await self._create_acquisition_impl(
            store_id, clerk_user_id, data, idempotency_key
        )
```

`backend/app/modules/acquisition/service.py:611`

```python
if cash_part > 0:
    await self._cash.record_movement(
        store_id,
        CashMovementType.BUYOUT_OUT,
        cash_part,
        actor_user_id=clerk_user_id,
        ref_type="acquisition",
        ref_id=acquisition.id,
    )
if credit_part > 0:
```

`backend/app/modules/acquisition/router.py:151`

```python
await session.commit()
return result
```

同商品／同會員／同錢櫃的主要鎖定證據：

`backend/app/modules/storecredit/repository.py:29`

```python
stmt = (
    select(StoreCreditAccount)
    .where(
        StoreCreditAccount.store_id == store_id,
        StoreCreditAccount.contact_id == contact_id,
    )
    .with_for_update()
    .execution_options(populate_existing=True)
)
```

`backend/app/modules/cashdrawer/service.py:92`

```python
併發保證：以 FOR UPDATE 鎖開帳中的 session 列，與 close_session 互斥（DB 層原子，
非先查狀態再插入）。若關帳已先一步轉 CLOSED，這裡的條件式查詢即查不到 OPEN → 拒絕，
避免現金異動落進已關閉的 session 而被對帳漏算。T6/T7/T11 的現金寫入都經此一處。
```

`backend/app/modules/inventory/repository.py:667`

```python
new_remaining = BulkLot.remaining_qty - qty
stmt = (
    update(BulkLot)
    .where(BulkLot.id == lot_id, BulkLot.remaining_qty >= qty)
```

銷售冪等由 request key、內容 fingerprint 與 DB unique 一起背書：

`backend/app/modules/sales/models.py:58`

```python
__tablename__ = "sales"
__table_args__ = (
    UniqueConstraint("store_id", "idempotency_key", name="uq_sales_store_idempotency_key"),
```

`backend/app/modules/sales/service.py:775`

```python
if idempotency_key is not None:
    replay = await self.find_idempotent_replay(
        store_id,
        idempotency_key,
        lines=lines,
        buyer_contact_id=buyer_contact_id,
        tenders=normalized_tenders,
        invoice_info=invoice_info,
        adjustments=adjustments,
        service_mode=service_mode,
        table_no=normalized_table_no,
    )
    if replay is not None:
        return replay
```

SSE 只通知重讀、沒有重放金流寫入：

`frontend/app/kiosk/page.tsx:329`

```tsx
const reload = () => {
  setStreamConnected(true);
  void queryClient.invalidateQueries({ queryKey: ["kiosk", "cart"] });
  void queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
  void queryClient.invalidateQueries({ queryKey: ["kiosk", "device"] });
};
source.addEventListener("open", reload);
source.addEventListener("state", reload);
```

0 元與全額購物金的邊界由成交端明確分支：

`backend/app/modules/sales/service.py:1002`

```python
gift_only = bool(lines) and all(
    line.line_kind is SaleLineKind.GIFT for line in lines
)
if total < 0 or (total == 0 and not gift_only):
    raise InvalidSaleTender("銷售總額必須大於 0（整單免費請全部以贈品開立）")
```

`backend/app/modules/sales/service.py:883`

```python
if has_cash and await self._cash.get_current_session(store_id) is None:
    raise NoOpenCashSession("結帳收現必須在開帳中的 cash_session 下進行，請先開帳")
```

寄售抽成以相減建立應付，production service 路徑恆等：

`backend/app/modules/consignment/service.py:227`

```python
commission_amount = commission(gross, commission_pct)
payout = gross - Decimal(commission_amount)
```

收據與發票證明聯的金額不在硬體代理重算，而是沿用後端落盤輸出：

`frontend/lib/agent.ts:76`

```ts
await postAgent("/print/einvoice", {
  sale_id: invoice.sale_id,
  invoice_number: invoice.invoice_no,
  invoice_date: invoice.invoice_date,
  invoice_time: invoice.invoice_time,
  random_code: invoice.random_number ?? "    ",
  sales_amount: invoice.net,
  tax_amount: invoice.tax,
  total_amount: invoice.total,
```

`hardware-agent/agent/drivers/escpos_receipt.py:166`

```python
out += _line(f"未稅　 {sale.subtotal}")
out += _line(f"營業稅 {sale.tax}")
out += _line(f"總計　 {sale.total}")
```

## P0：會產生錯帳或掉錢

### P0-1：無客顯購物車的 LINE Pay 可能已扣款，但後端沒有可持久化的待對帳紀錄

**現況：** POS 一般付款允許沒有配對 kiosk；此時 `cart_session_id` 送 `null`。LINE Pay `pay()` 在銷售 transaction commit 前執行。若平台已成功但後續 flush／commit 失敗，router 先 rollback，再嘗試記錄 `PAYMENT_UNCERTAIN`；但沒有 `cart_session_id` 時該函式在任何落庫前直接丟 409。

`frontend/app/(authed)/pos/page.tsx:1899`

```tsx
let checkoutCart: DisplayCart | null = null;
```

`frontend/app/(authed)/pos/page.tsx:1945`

```tsx
} else if (plan.storeCredit > 0) {
  throw new Error("購物金付款必須先配對並連線顧客螢幕");
}
```

`frontend/app/(authed)/pos/page.tsx:1970`

```tsx
tenders: toTenders(plan, { linePayKey }) ?? null,
// 已簽且折抵額相符才綁定（後端亦精確比對＋單次使用守護）。
signature_task_id: signed && !signMismatch ? signTaskId : null,
cart_session_id: checkoutCart?.id ?? null,
cart_revision: checkoutCart?.revision ?? null,
```

`backend/app/modules/sales/service.py:1337`

```python
result = await client.pay(
    order_id=order_id,
    amount=tender.amount,
    one_time_key=tender.line_pay_one_time_key,
    product_name="門市消費",
)
```

`backend/app/modules/sales/router.py:389`

```python
try:
    lines = await svc.get_lines(sale.id)
    tenders = await svc.get_tenders(sale.id)
    await session.commit()
except Exception as exc:
    await session.rollback()
```

`backend/app/modules/sales/router.py:163`

```python
if payload.cart_session_id is None or payload.tenders is None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="LINE Pay 結果不明，且缺少 POS 購物車可供對帳；禁止直接重試",
    )
```

**風險／為什麼：** 這條可達路徑會形成「平台已有扣款、sales／tender／LINE Pay transaction 均未 commit、後端也沒有 PAYMENT_UNCERTAIN 載體」的錯帳。前端雖保存冪等鍵、同瀏覽器重試可用相同 `order_id` check-first 復原，但該鍵不是伺服器端持久對帳事實；瀏覽器狀態遺失或改由其他終端處理時，系統內無紀錄可定位這筆已扣款。

## P1：特定條件下數字不一致

### P1-1：寄售建單的預設抽成固定為 50%，沒有使用系統 `default_commission_pct`

**現況：** 設定 API 回傳可調整的 `default_commission_pct`，收購頁也已載入整份 settings；但新明細固定初始化為字串 `"50"`，提交時直接送該列值。後端將 request 值原樣快照到寄售品，售出後又以此快照建立結算。

`backend/app/modules/settings/schemas.py:30`

```python
store_id: int
einvoice_enabled: bool
tax_rate: RateOut
default_commission_pct: int
default_margin_pct: int
```

`frontend/app/(authed)/acquisition/page.tsx:72`

```tsx
function emptyItem(): ItemDraft & { estimatedResale: string; rowKey: string } {
  return {
    // 穩定的列識別：用 index 當 React key 時，刪除中間列會讓後面的列沿用同一個元件實例，
    // 連帶把前一列的內部狀態（如已選標籤）帶過去。不進 API payload（逐欄挑選）。
    rowKey: newIdempotencyKey(),
    name: "",
    grade: "",
    categoryId: null,
    brandId: null,
    productModelId: null,
    listedPrice: "",
    acquisitionCost: "",
    commissionPct: "50",
```

`frontend/app/(authed)/acquisition/page.tsx:814`

```tsx
const settings = useQuery({
  queryKey: ["settings"],
  queryFn: async () => (await api.GET("/api/v1/settings")).data ?? null,
});
```

`frontend/app/(authed)/acquisition/page.tsx:941`

```tsx
...(type === "BUYOUT"
  ? { acquisition_cost: ntd(r.acquisitionCost) }
  : { commission_pct: parseNtd(r.commissionPct) }),
```

`backend/app/modules/acquisition/service.py:789`

```python
else:  # CONSIGNMENT
    ownership = OwnershipType.CONSIGNMENT
    consignor_id = contact_id
    commission = item.commission_pct
```

**風險／為什麼：** 當店家設定非 50% 的預設抽成時，新寄售列仍顯示並送出 50%。後續結算依法採用商品快照，因此店家抽成與賣家實拿會依錯誤的建單比例計算，而不是系統設定的預設比例。

### P1-2：散裝每件成本不先分配尾差，寫入 `Numeric(12,0)` 時會丟失整批成本

**現況：** 每件成本使用精確除法，成交行把 `per_piece_cost × qty` 直接指定給 `cost_snapshot`；欄位卻是整數 scale 0。

`backend/app/modules/inventory/service.py:1127`

```python
@staticmethod
def per_piece_cost(lot: BulkLot) -> Decimal:
    """每件成本 = acquisition_cost / total_qty。"""
    return lot.acquisition_cost / Decimal(lot.total_qty)
```

`backend/app/modules/sales/service.py:3026`

```python
**self._line_amounts(
    disc,
    qty=line.qty,
    cost=InventoryService.per_piece_cost(lot) * line.qty,
    gift=gift,
),
```

`backend/app/modules/sales/models.py:184`

```python
# 成交當下的成本（本行合計）。凍結於此，日後調整商品成本不會回頭改寫歷史毛利。
# NULL＝無成本可知（餐飲、或未填成本的商品），報表沿用既有「成本未知」口徑。
cost_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
```

**風險／為什麼：** 整批成本 1,000 元、3 件分三次各售 1 件時，每行送入 `333.333…`，DB 各自轉成 333，三行成本合計 999，不再等於原整批 1,000。這不是顯示問題；`cost_snapshot` 是毛利報表採用的持久成本事實。

### P1-3：兩支散裝毛利報表對同一成交採用不同成本基準

**現況：** `goods_margin_and_revenue()` 仍以批次總成本做未取整精確除法；`margin_components()` 優先讀已落盤的整數 `cost_snapshot`，只有舊資料沒有快照才另行 round。

`backend/app/modules/sales/repository.py:531`

```python
for acquisition_cost, total_qty, qty, net_amount in bulk:
    goods_revenue += net_amount
    if total_qty and total_qty > 0:
        cost = acquisition_cost * Decimal(qty) / Decimal(total_qty)
        buyout_margin += net_amount - cost
```

`backend/app/modules/sales/repository.py:770`

```python
for consignor_id, acquisition_cost, total_qty, qty, net_amount, cost_snapshot in bulk:
    if consignor_id is not None:  # 寄售散裝：全額計流水，無抽成基礎、不認自有成本
        consignment_bulk_revenue += net_amount
        continue
    owned_bulk_revenue += net_amount
    if cost_snapshot is not None:
        owned_bulk_cogs += cost_snapshot
    elif total_qty and total_qty > 0:
        owned_bulk_cogs += round_ntd(acquisition_cost * Decimal(qty) / Decimal(total_qty))
```

**風險／為什麼：** 同一行在購物金效益用的 `period_margin()` 與主要 R2／R5／R6 毛利報表可能得到小數成本與整數成本兩個答案；P1-2 的尾差會因此直接表現在跨報表數字不一致。

### P1-4：部分折讓逐筆拆稅，累計全退時可能無法沖回原發票的 net／tax

**現況：** 折讓只檢查累計 `total` 不超過原發票；每張折讓再各自呼叫 `split_tax_inclusive(total, invoice.tax_rate)`。

`backend/app/modules/einvoice/service.py:594`

```python
prior = await self._repo.sum_allowances_total(store_id, invoice_id)
if prior + total > invoice.total:
    raise AllowanceExceedsInvoice(
        f"折讓累計 {prior + total} 超過原發票總額 {invoice.total}"
    )

net, tax = split_tax_inclusive(total, Decimal(invoice.tax_rate))
```

`backend/app/modules/einvoice/service.py:601`

```python
allowance = InvoiceAllowance(
    store_id=store_id,
    invoice_id=invoice_id,
    return_id=return_id,
    net=Decimal(net),
    tax=Decimal(tax),
    total=Decimal(net + tax),
)
```

**風險／為什麼：** 5% 下原發票總額 100 會拆為 net 95／tax 5；兩張各 50 的折讓會各拆為 net 48／tax 2，累計成 net 96／tax 4。含稅總額仍對平 100，但全退後稅額少沖 1、未稅多沖 1。

### P1-5：每日摘要用查詢當下的稅率重算歷史認列營收

**現況：** 銷售與發票落盤時保存當時的 net／tax，發票也保存 `tax_rate`；每日摘要卻讀目前 settings，再對歷史期間的 `recognized_revenue` 重拆一次。

`backend/app/modules/einvoice/models.py:129`

```python
net: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 未稅
tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 稅額
total: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 含稅總額
# 結帳當下稅率快照（Codex 第九輪）：F0401 金額/TaxRate 以此計——結帳後改 settings
# 稅率不得改變已落地發票的申報內容。
tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0.05"))
```

`backend/app/modules/reports/service.py:871`

```python
start, end = store_day_bounds(report_date)
cash = await self.daily_cash(store_id, report_date)
margin = await self._sales.margin_breakdown(store_id, start, end)
settings = await self._settings.get_effective_settings(store_id)
```

`backend/app/modules/reports/service.py:887`

```python
net_ex_tax, tax = split_tax_inclusive(margin.recognized_revenue, settings.tax_rate)
```

**風險／為什麼：** 查詢歷史日期前若店別稅率設定已變動，該日摘要的「除稅淨額／稅額」會隨查詢時點改變，且不再是成交或發票當下的稅率快照口徑。

### P1-6：前端 `Math.round` 與後端 Decimal HALF_UP 在合法四位費率下可差 1 元

**現況：** POS 手續費提示與收購購物金溢價預覽以 JS `number` 相乘後 `Math.round`；後端以 Decimal 乘法與 `round_ntd` 落盤。

`frontend/app/(authed)/pos/page.tsx:569`

```tsx
{plan.taiwanPay > 0 && (
  <>
    <p className="hint">
      台灣Pay 收款 <Money value={plan.taiwanPay} />（請於台灣Pay App 完成收款）
      {taiwanpayFeePct > 0 && (
        <>
          {" "}
          · 本筆手續費{" "}
          <Money value={Math.round(plan.taiwanPay * taiwanpayFeePct)} />
```

`frontend/features/acquisition/pricing.ts:13`

```ts
export function roundNtd(value: number): number {
  return Math.round(value);
}
```

`frontend/features/acquisition/pricing.ts:136`

```ts
export function creditPremiumPreview(creditEquivNtd: number, premiumRate: number): number {
  return roundNtd(creditEquivNtd * premiumRate);
}
```

`backend/app/modules/sales/service.py:1250`

```python
elif tender.tender_type == TenderType.TAIWAN_PAY:
    # 非現金、不進抽屜、無外部 API（店員於台灣Pay App 收款）；僅記手續費快照。
    fee = Decimal(round_ntd(tender.amount * settings.taiwanpay_fee_pct))
```

`backend/app/modules/storecredit/service.py:268`

```python
amount = Decimal(round_ntd(cash_equivalent * (Decimal(1) + premium_rate)))
```

**風險／為什麼：** 合法費率 `0.0003`、金額 5,000 時，精確乘積是 1.5，後端 HALF_UP 得 2；JS 的實際乘積是 `1.4999999999999998`，`Math.round` 得 1。POS 費用提示與購物金溢價預覽可比後端落盤少 1 元。

### P1-7：人工錢櫃調整沒有 idempotency key 或唯一約束

**現況：** `POST /cash-sessions/{id}/movements` 的輸入只有類型、金額、事由；router 每次都呼叫 `record_movement()`，repository 每次都新增一列。

`backend/app/modules/cashdrawer/schemas.py:57`

```python
class CashMovementCreateRequest(BaseModel):
    """記一筆現金異動（MANUAL_ADJUST 可正可負；其餘類型非負）。"""

    type: CashMovementType
    amount: NTDAmount
    note: str = Field(min_length=1, max_length=200)  # 事由必填（留痕，§5）
```

`backend/app/modules/cashdrawer/router.py:110`

```python
movement = await svc.record_movement(
    user.store_id,
    payload.type,
    payload.amount,
    actor_user_id=user.id,
    ref_type="manual",
    note=payload.note,
)
await session.commit()
```

`backend/app/modules/cashdrawer/repository.py:57`

```python
async def add_movement(self, movement: CashMovement) -> CashMovement:
    self._session.add(movement)
    await self._session.flush()
    return movement
```

**風險／為什麼：** 雙擊、逾時後重試或代理重送同一人工調整時，兩次都會成功 INSERT；`expected_amount()` 又會把兩列都加總，造成應有現金與關帳差異重複增減。

### P1-8：LINE Pay 退款成功發生在退貨主交易 commit 前

**現況：** 退貨主交易先呼叫 `refund_line_pay_amount()`；退款實作以獨立 transaction 提交 PENDING、呼叫平台、再獨立提交 SUCCEEDED，退貨 router 最後才 commit 主交易。

`backend/app/modules/returns/service.py:603`

```python
refund_identity = _refund_identity(sale.id, requested, clean_reason, previous)
await SalesService(self._session).refund_line_pay_amount(
    store_id,
    sale.id,
    linepay_refund,
    linepay_client,
    refund_key=f"s{store_id}:return:{refund_identity}",
)
```

`backend/app/modules/sales/service.py:1623`

```python
async with sm() as ledger:
    row = await ledger.scalar(
        select(LinePayRefundAttempt)
        .where(LinePayRefundAttempt.refund_key == refund_key)
        .with_for_update()
    )
```

`backend/app/modules/sales/service.py:1640`

```python
    row.status = LinePayRefundStatus.PENDING
    row.return_code = None
await ledger.commit()
```

`backend/app/modules/sales/service.py:1644`

```python
# Phase 2：呼叫平台（傳輸錯誤 → 保留 PENDING 並上拋，下次重試即 ambiguous）
result = await client.refund(order_id=order_id, refund_amount=amount)
```

`backend/app/modules/returns/router.py:121`

```python
except DomainError as exc:
    await session.rollback()
    raise _map_domain_error(exc) from exc
await session.commit()
return ReturnRead.from_model(customer_return)
```

**風險／為什麼：** 平台退款成功後若退貨主交易 commit 失敗，外部已退款，但退貨單、庫存、錢櫃／購物金回補與折讓可能仍未成立。獨立退款帳可防止再次退款，下一次重試會依 SUCCEEDED 累計補回本地 `refunded_amount`；在重試完成前，平台與本地帳務仍不一致，若無後續處理則持續不一致。

## P2：可維護性風險

### P2-1：`tax_rate` 的 API scale 驗證少於 DB 的 `Numeric(5,4)`

**現況：** DB 稅率是四位小數；PATCH 只限制數值範圍。相同 DB scale 的溢價率與支付費率都有明確四位 validator，唯獨 `tax_rate` 未列入。

`backend/app/modules/settings/models.py:52`

```python
tax_rate: Mapped[Decimal] = mapped_column(
    Numeric(5, 4), server_default=text("0.05"), nullable=False
)
```

`backend/app/modules/settings/schemas.py:69`

```python
einvoice_enabled: bool | None = None
tax_rate: Annotated[Decimal, Field(ge=0, lt=1)] | None = None
default_commission_pct: Annotated[int, Field(ge=0, le=100)] | None = None
```

`backend/app/modules/settings/schemas.py:133`

```python
@field_validator("premium_rate", "premium_rate_min", "premium_rate_max")
@classmethod
def _rate_scale(cls, value: Decimal | None) -> Decimal | None:
    # DB 為 Numeric(5,4)：限四位小數，避免 API/留痕記 5dp 而 DB 落 4dp 不一致（Codex P2）。
    if value is not None and value != value.quantize(Decimal("0.0001")):
        raise ValueError("溢價率最多四位小數")
    return value
```

**風險／為什麼：** 五位以上稅率可通過 API，後續在 DB coercion 才改成四位；request／audit／即時運算與重新讀取的設定可能不是同一精度。

### P2-2：多個 `Numeric(12,0)` 金額入口沒有一致的 12 位上界

**現況：** 代表性的收購與現金 schema 只守整數／非負，沒有 `<= 999999999999`；對應 DB 欄位為 `Numeric(12,0)`。同一專案的 settings 金額則已有 12 位上界。

`backend/app/modules/acquisition/schemas.py:30`

```python
def _ensure_whole_nonneg(value: Decimal, field: str) -> Decimal:
    if value < 0:
        raise ValueError(f"{field} 不可為負")
    if value != value.to_integral_value():
        raise ValueError(f"{field} 必須為整數元（無角分）")
    # 正規化（Codex：冪等指紋不可受 "1000.0"/"1000"/1000 形式差異影響）：
    # 一律回整數形 Decimal，下游序列化/指紋/持久化全 canonical。
    return Decimal(value.to_integral_value())
```

`backend/app/modules/inventory/models.py:142`

```python
acquisition_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
consignor_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
commission_pct: Mapped[int | None] = mapped_column()
listed_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
```

`backend/app/modules/settings/schemas.py:82`

```python
monthly_fixed_cash_outflow: (
    Annotated[Decimal, Field(ge=0, le=Decimal("999999999999"))] | None
) = None
```

**風險／為什麼：** 13 位以上金額可通過部分 API／service 驗證，直到 flush／commit 才由 DB 拒絕；相同 Numeric 規格在不同入口呈現 422 或資料庫錯誤兩種行為。

### P2-3：`cash_movements` 宣告 append-only，但 DB 沒有不可變與金額形狀守衛

**現況：** model docstring 稱 append-only，repository 也只有一般新增路徑；但 model 沒有 UPDATE／DELETE trigger、金額非零／類型方向 CHECK，且 `record_movement()` 本身直接接受傳入 amount。

`backend/app/modules/cashdrawer/models.py:60`

```python
class CashMovement(Base):
    """現金異動（append-only 帳；無 updated_at）。"""

    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cash_sessions.id"), index=True)
    type: Mapped[CashMovementType] = mapped_column(_enum_col(CashMovementType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
```

`backend/app/modules/cashdrawer/service.py:99`

```python
movement = CashMovement(
    store_id=store_id,
    session_id=session.id,
    type=movement_type,
    amount=amount,
    ref_type=ref_type,
    ref_id=ref_id,
    note=note,
)
```

**風險／為什麼：** production HTTP 入口限制為 MANUAL_ADJUST，正常跨模組呼叫也傳正值；但 raw DML、migration、fixture 或未來直接 service 呼叫能更新／刪除現金歷史，或以負值寫入 SALE_IN 等系統類型，使 `expected_amount()` 依類型再加減後反轉經濟方向。

### P2-4：購物金初始 migration 從 live model import trigger DDL

**現況：** migration 沒有凍結自身的 trigger SQL，而是 import 執行當下的 `LEDGER_IMMUTABLE_DDL`；目前多個鏈結／快取 trigger 也只由這個既有 revision 迴圈安裝。

`backend/alembic/versions/c5d1e8a2b7f4_add_store_credit_ledger.py:13`

```python
import sqlalchemy as sa
from alembic import op

from app.modules.storecredit.models import LEDGER_IMMUTABLE_DDL, LEDGER_IMMUTABLE_DROP_DDL
```

`backend/alembic/versions/c5d1e8a2b7f4_add_store_credit_ledger.py:183`

```python
for ddl in LEDGER_IMMUTABLE_DDL:
    op.execute(ddl)
```

`backend/app/modules/storecredit/models.py:366`

```python
    """
CREATE TRIGGER trg_store_credit_cache_sync
AFTER INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_cache_sync()
""",
)
```

**風險／為什麼：** 新建資料庫執行同一 revision 時會套用「目前」tuple；早已跑過該 revision 的資料庫不會因 model tuple 後來增加 trigger 而自動重跑，導致相同 Alembic revision 可能有不同 DB 守衛集合。

### P2-5：測試／模擬 fixture 會停用或繞過購物金帳本不可變保護

**現況：** 部分 integration cleanup 將 replication role 設為 replica 後直接 DELETE；另有 cleanup 以 TRUNCATE 清空帳本與帳戶。

`backend/tests/integration/test_sales_signing_concurrency.py:337`

```python
async with sm() as s:
    await delete_customer_display_rows(s, store_id=store_id)
    await s.execute(text("SET session_replication_role = replica"))
    for model in (SaleTender, SaleLine, StockMovement, CashMovement, CashSession, AuditLog):
        await s.execute(delete(model).where(model.store_id == store_id))
    await s.execute(delete(Sale).where(Sale.store_id == store_id))
    await s.execute(
        text("DELETE FROM store_credit_ledger WHERE store_id = :sid"), {"sid": store_id}
    )
```

`backend/tests/integration/test_acquisition_payout.py:635`

```python
finally:
    async with sm() as s:
        await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
        await s.execute(text("DELETE FROM acquisitions"))
```

**風險／為什麼：** production 路徑仍由 trigger 保護，但「全 repo 完全 append-only」不成立；fixture 若非完全隔離到測試資料庫，會具備刪除不可變財務歷史的能力，且 TRUNCATE 不走 row-level UPDATE/DELETE trigger。

### P2-6：一般測試 transaction 可能不觸發 `DEFERRABLE INITIALLY DEFERRED` 金流守衛

**現況：** tender 對平／購物金雙向綁定的 constraint trigger 明確延遲到 transaction commit；共用 fixture 把測試 session 放在外層 transaction 的 savepoint，測試結束一律 rollback 外層 transaction。

`backend/app/modules/sales/models.py:406`

```python
# 收款守衛（Codex SC-3 P3＋第二輪 P1）。DEFERRABLE INITIALLY DEFERRED，於 COMMIT 時驗：
#  (A) 對平：Σ sale_tenders.amount 必須等於 sales.total（現金＋購物金須與總額對平）。
#  (B) 購物金 ↔ 帳本雙向綁定（負債級）：STORE_CREDIT 收款金額必須對應一筆等額、同店、
```

`backend/tests/conftest.py:68`

```python
async def db_session() -> AsyncGenerator[AsyncSession]:
    """產出一個與 DB 隔離的 session：測試結束自動 rollback。"""
    connection = await test_engine.connect()
    trans = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
```

`backend/tests/conftest.py:77`

```python
try:
    yield session
finally:
    await session.close()
    await trans.rollback()
```

**風險／為什麼：** router 的 `session.commit()` 在此 fixture 下通常只完成 savepoint；真正 transaction-level deferred trigger 到外層 rollback 前未必執行。測試即使走完 201，也不等於 production commit-time 金流守衛已被覆蓋。

### P2-7：報價與成交仍各自解析品項與計算活動折後價

**現況：** 兩條路徑共用 `_compute_discount()` 與 `apply_discounts()`，但 `_quote_line()` 與 `_process_*()` 仍各自取品項、判斷寄售／贈品／活動適用性並組金額。

`backend/app/modules/sales/service.py:2491`

```python
async def _quote_line(
    self,
    store_id: int,
    line: SaleLineInput,
    campaign: Campaign | None,
    discountable_out: list[bool] | None = None,
) -> QuoteLine:
    """單行試算（唯讀）：解析品項、算折後價；不動任何狀態。

    必須與 `_process_line` 得出**完全相同**的金額——客顯購物車快照由此建立，
    結帳時會與實際成交明細逐欄位比對，兩邊不一致就會整筆結帳失敗。
    """
```

`backend/app/modules/sales/service.py:2945`

```python
disc = (
    self._gift_discount(product.unit_price)
    if gift is not None
    else _compute_discount(campaign, product.unit_price, applies=applies)
)
```

**風險／為什麼：** 任何只改其中一路的定價規則會讓 POS 顯示／簽署快照與實際成交不同；有客顯簽署時會在逐欄比較失敗並阻斷結帳，沒有該比較的普通結帳則可能只在提交時得到與先前 quote 不同的金額。

### P2-8：寄售結算的金額恆等式只由 service 保證，DB 沒有背書

**現況：** service 會以 `payout=gross-commission` 建列；model 的三個金額只是獨立 Numeric 欄位，未宣告 `commission_amount + payout_amount = gross`、抽成範圍或非負 CHECK。

`backend/app/modules/consignment/service.py:227`

```python
commission_amount = commission(gross, commission_pct)
payout = gross - Decimal(commission_amount)
settlement = ConsignmentSettlement(
    store_id=store_id,
    serialized_item_id=serialized_item_id,
    sale_id=sale_id,
    gross=gross,
    commission_pct=commission_pct,
    commission_amount=Decimal(commission_amount),
    payout_amount=payout,
)
```

`backend/app/modules/consignment/models.py:24`

```python
__tablename__ = "consignment_settlements"

id: Mapped[int] = mapped_column(primary_key=True)
store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
serialized_item_id: Mapped[int] = mapped_column(ForeignKey("serialized_items.id"), index=True)
sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
gross: Mapped[Decimal] = mapped_column(Numeric(12, 0))
commission_pct: Mapped[int] = mapped_column()
commission_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
payout_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
```

**風險／為什麼：** production service 產生的列對平；raw DML、migration、fixture 或未來新增直寫路徑可建立不對平結算，付款流程會直接以 `payout_amount` 出現金，報表則另讀 `commission_amount`，兩者可彼此矛盾。

## 待確認

### 待確認-1：部署中資料庫是否實際具備目前 model 列出的全部購物金 triggers

`backend/app/modules/storecredit/models.py:229`

```python
LEDGER_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION store_credit_ledger_immutable() RETURNS trigger AS $$
```

`backend/alembic/versions/c5d1e8a2b7f4_add_store_credit_ledger.py:183`

```python
for ddl in LEDGER_IMMUTABLE_DDL:
    op.execute(ddl)
```

**待確認原因：** P2-4 只能從 repo 證明 migration 定義可漂移，無法從原始碼判定既有 production DB 在何時執行過哪一版 tuple。

**判定所需資訊：** 各部署 DB 的 Alembic revision、`pg_trigger`／`pg_proc` 實際清單與函式定義 checksum，至少包含 immutable、reversal guard、credit guard、balance chain guard、cache sync。

### 待確認-2：進項發票是否一律可用「登錄當下店別稅率」反推 net／tax

`backend/app/modules/purchasing/schemas.py:145`

```python
class InputInvoiceIn(BaseModel):
    """進項發票登錄輸入（裁示 2026-07-11：收貨時選填、漏登可補登一次）。

    號碼＝2 英文大寫＋8 數字；金額為含稅整數元字串（>0）。未稅/稅額由後端以
    settings.tax_rate 用 split_tax_inclusive 拆分（§6），不收前端算的值。
    """
```

`backend/app/modules/purchasing/service.py:334`

```python
settings = await self._settings.get_effective_settings(store_id)
for key, value in self._invoice_fields(invoice, Decimal(settings.tax_rate)).items():
    setattr(receipt, key, value)
```

**待確認原因：** 程式不收供應商發票上已載明的未稅額／稅額／稅別，而是以補登當下的店別設定重算。repo 內沒有足夠業務證據可判定供應商是否可能為免稅／零稅率，或發票日期與補登日期間是否可能跨稅率。

**判定所需資訊：** 進項發票允許的稅別、供應商發票實際欄位、稅率變更時的歷史政策，以及帳務是否要求逐字採用原憑證 net／tax。

### 待確認-3：Amego `UnitPrice` 是否接受任意長的小數字串

`backend/app/modules/einvoice/amego.py:88`

```python
# Amount（實收小計）為權威；折扣行的 UnitPrice 以小計÷數量表示（兩者一致，
# 避免平台以 Quantity×UnitPrice 驗算時對不上）。
effective_unit = Decimal(line.net_amount) / Decimal(line.qty)
```

`backend/app/modules/einvoice/amego.py:93`

```python
"Description": line.description[:_DESCRIPTION_MAX],
"Quantity": line.qty,
"UnitPrice": _decimal_str(effective_unit),
"Amount": _decimal_str(Decimal(line.net_amount)),
"TaxType": _TAX_TYPE_TAXABLE,
```

`backend/app/modules/einvoice/amego.py:55`

```python
def _decimal_str(value: Decimal) -> str:
    """Decimal → 無指數、無尾零字串（"450"、"52.5"）；金額欄位以字串傳輸。"""
    text = format(value.normalize(), "f")
    return text
```

**待確認原因：** 例如實付 100、數量 3 會產生長循環小數的 Decimal 字串；本 repo 的 docs／schema 未提供 Amego `UnitPrice` 最大 scale 或平台驗算容差，無法判定是否會拒單。

**判定所需資訊：** 目前串接之 Amego API 版本的 `ProductItem.UnitPrice` precision／scale、`Quantity × UnitPrice` 與 `Amount` 的驗算規則，以及真平台對循環小數案例的回應。

### 待確認-4：營業稅率是固定 5%，或允許每店由設定調整

`backend/app/modules/settings/models.py:52`

```python
tax_rate: Mapped[Decimal] = mapped_column(
    Numeric(5, 4), server_default=text("0.05"), nullable=False
)
```

`backend/app/modules/settings/schemas.py:69`

```python
einvoice_enabled: bool | None = None
tax_rate: Annotated[Decimal, Field(ge=0, lt=1)] | None = None
default_commission_pct: Annotated[int, Field(ge=0, le=100)] | None = None
```

**待確認原因：** 程式以 5% 為預設但允許 PATCH 為 0 到 1 之間任意稅率。本任務描述指定「5% 營業稅」，但 repo 同時把稅率視為店別設定；無法僅憑程式判定哪一項才是業務政策。

**判定所需資訊：** 店別是否可能為免稅／零稅率／不同稅制，以及 production 是否允許一般管理者調整 `tax_rate`。

## 範圍外缺口（一行）

作廢／更正流程依任務規則不展開；本輪只在交易與帳本整合證據中讀到既有入口，未對 F6.5 的完整性作判定。
