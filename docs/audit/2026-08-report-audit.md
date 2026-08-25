# 報表金流正確性稽核（2026-08）

本文件保留初始唯讀稽核證據，並附上後續修正與驗證結果。

- 稽核日期：2026-08-24（Asia/Taipei）
- 初始稽核基準：`main` @ `fa005ed`
- 修正基準：`main` @ `4e12ca2` + `fix/report-audit-findings` 工作樹
- 範圍：所有報表 endpoint 與查詢邏輯、日結／期間彙總／稅額／寄售應付款。
- 範圍外：認證授權、前端樣式與 i18n、硬體 agent 印表機協定、作廢／更正流程設計。
- 初始判定數：P0 0 項、P1 10 項、P2 4 項、待確認 5 項。
- 初始稽核方法：靜態追蹤 router → report service → domain service／repository → ORM／migration；初始稽核未連線查詢正式資料庫，也未執行寫入或測試。後續修正另以隔離資料庫、真後端與真瀏覽器驗證，證據見下節。

## 修正後狀態

- 已確認營業日不跨午夜；P1-1／待確認-1 不再列為現行業務缺口。
- P1-7～P1-10 已修正：贈品退回／成本沖回、匯出欄位、帳本權威負債、退款渠道淨額均已落入報表。
- 401 報表暫不實作；P1-2／P1-3 與待確認-3 保留為未來稅務需求，不自行假設法定分類。
- P1-4～P1-6 與初始 P2-1～P2-4 仍是既有 baseline，本次未宣稱修正；分別涉及寄售跨期退貨抽成、`period_margin`／insights 口徑與既有查詢／索引效能債。
- 已移除每日摘要的 `estimated_net_income`、說明文字、前端卡片、匯出欄位與 API contract；保留銷售毛利報表的「淨毛利（扣支付手續費）」。目前沒有正式淨利報表。
- 活動結束後退貨不回算活動成效、測試交易不進正式店別，依店長確認接受現況。
- 最終無記憶 agent review 判定 implemented changes clean，沒有新增 P0／P1／P2 或分層違規；過程中 reviewer 找到的贈品查詢過寬與 smoke 假陽性均已修正並重新 review。

### 修正後驗證

- 完整後端測試：`1566 passed`（1 個既有 Pydantic serializer warning），另有贈品 501 筆批次邊界、跨店、JSON／CSV 實值與四種退款渠道的精準測試。
- 前端：55 個 test files、483 tests 通過；TypeScript、ESLint 通過；OpenAPI 與生成 TypeScript 型別重建後無漂移。
- 隔離實機：以 `lucamp_report_verify_20260825_2` 副本、獨立後端 `127.0.0.1:8002`、既有真前端 `localhost:3000` 驗證；未連正式資料庫，也未停止使用者既有 `:8000`／`:3000` 服務。
- 寬區間真 API（2025-01-01～2027-01-01）：贈品送出 201、退回 12、淨額 189；成本 54,954、退回成本 3,076、淨成本 51,878。銷售毛利回傳 CASH／STORE_CREDIT／LINE_PAY／TAIWAN_PAY 四種期間淨收款，並包含支付手續費、淨毛利與贈品退回欄位；`estimated_net_income` 不存在。
- 真 CSV：贈品送出／退回／淨額欄與銷售毛利的支付手續費、淨毛利、淨收款、贈品退回／淨額欄均存在。
- Playwright 真瀏覽器：今日營運、趨勢、現金對帳、銷售毛利、贈品、庫存價值、寄售應付七個分頁及季度趨勢 API／畫面／CSV 均通過；截圖位於 session 暫存目錄 `/tmp/lu-camp-shots/report-audit-20260825/`。
- 驗證完成後已正常停止獨立 `:8002` 後端並刪除拋棄式資料庫；`lucamp_manual`、`lucamp_e2e` 與使用者既有 `:8000`／`:3000` 均保留。

## 查核基線

以下未標「修正後判定」的程式碼片段與行號是初始稽核基準 `main @ fa005ed` 的原始證據；已修正項目前置的「修正後判定」則引用目前工作樹行號。這樣保留問題發生時的實際程式碼，不把修正後程式碼誤寫成原始缺陷。

### 報表 endpoint 盤點

下列 15 個 GET endpoint 是本輪找到的完整報表入口；均由 `ReportsService` 唯讀取數，本身不執行 DB 寫入，也沒有 DB 以外副作用。

| 模組 | Endpoint | 主要數字來源 | 路徑 |
|---|---|---|---|
| 現金日報 | `GET /reports/daily-cash` | cash session 與 cash movement | `backend/app/modules/reports/finance_router.py:41-49` |
| 每日摘要 | `GET /reports/daily-summary` | `daily_cash`、`margin_breakdown`、購物金 flow、目前設定 | `backend/app/modules/reports/finance_router.py:118-126` |
| 趨勢 | `GET /reports/trends` | 每桶 `margin_breakdown`、購物金 flow、cash movement | `backend/app/modules/reports/finance_router.py:174-175` |
| 經營洞察 | `GET /reports/insights` | 售出明細的 Python 彙總及 `margin_breakdown` | `backend/app/modules/reports/finance_router.py:244-245` |
| 庫存價值 | `GET /reports/inventory-value` | 三類在庫資料的 Python 彙總 | `backend/app/modules/reports/finance_router.py:304-313` |
| 寄售應付 | `GET /reports/consignment-payables` | 全部 consignment settlement | `backend/app/modules/reports/finance_router.py:377-390` |
| 銷售毛利 | `GET /reports/sales-margin` | `margin_breakdown` | `backend/app/modules/reports/finance_router.py:444-458` |
| 折扣 | `GET /reports/discounts` | sale adjustment／allocation | `backend/app/modules/reports/finance_router.py:498-506` |
| 內用／外帶 | `GET /reports/dine-in` | 餐飲銷售明細 | `backend/app/modules/reports/finance_router.py:557-568` |
| 贈品 | `GET /reports/gifts` | gift sale lines | `backend/app/modules/reports/finance_router.py:645-655` |
| 活動成效 | `GET /reports/campaign-performance` | 每活動 `margin_breakdown` 與活動折扣 | `backend/app/modules/reports/finance_router.py:704-715` |
| 購物金負債 | `GET /reports/store-credit/liability` | account balance cache 與 ledger lot | `backend/app/modules/reports/router.py:52-64` |
| 購物金流量 | `GET /reports/store-credit/flows` | store-credit ledger | `backend/app/modules/reports/router.py:82-98` |
| 購物金效益 | `GET /reports/store-credit/effectiveness` | 多個獨立指標查詢及 `period_margin` | `backend/app/modules/reports/router.py:149-166` |
| 購物金對帳 | `GET /reports/store-credit/reconciliation` | ledger、account cache、balance chain | `backend/app/modules/reports/router.py:200-218` |

報表 router 明文宣告為唯讀、UTC 瞬間與 Asia/Taipei 分桶：

`backend/app/modules/reports/finance_router.py:1-5`
```python
"""Phase 6 財務報表路由（MANAGER；docs/19）：每日現金對帳等。

所有報表唯讀、store 範圍（由 token 的 store_id 限定）；金額整數元字串。
?format=csv|xlsx 走 export_response，與 JSON 同源（同一 service 取數，匯出只做呈現轉換）。
時間瞬間維持 UTC；營業日與報表分桶固定以 Asia/Taipei 切界線。
"""
```

### 時間、邊界與狀態基線

- 共用 `created_at`／`updated_at` 為 `DateTime(timezone=True)`；錢櫃的 `opened_at`、`closed_at`、`CashMovement.created_at` 也明確使用 `timezone=True`。
- API 的期間參數拒絕 naive datetime 並轉為 UTC。
- 日／週／月／季分桶以 Asia/Taipei 計算；期間比較普遍採半開區間 `[from, to)`，即含頭、不含尾。
- 程式中的「營業日」目前就是 Asia/Taipei 自然日 00:00 至次日 00:00，沒有看到另外的營業日切點設定。
- 銷售報表普遍排除 `VOIDED`；`COMPLETED` 與 `RETURNED` 都會進入查詢，退貨由 return event 的時間另行扣減。測試交易沒有獨立狀態，列於「待確認」。

`backend/app/core/db.py:28-39`
```python
class TimestampMixin:
    """共用時戳欄位。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

`backend/app/core/time.py:12-22,40-44`
```python
STORE_TIME_ZONE_NAME = "Asia/Taipei"
STORE_TIME_ZONE = ZoneInfo(STORE_TIME_ZONE_NAME)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("日期時間必須包含時區")
    return value.astimezone(UTC)


type AwareDateTime = Annotated[datetime, AfterValidator(_aware_utc)]

def store_day_bounds(value: date) -> tuple[datetime, datetime]:
    """回傳台灣營業日對應的 UTC 半開區間 ``[start, end)``。"""
    local_start = datetime(value.year, value.month, value.day, tzinfo=STORE_TIME_ZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)
```

`backend/app/shared/enums.py:155-168`
```python
class SaleStatus(StrEnum):
    """銷售單自身的生命週期（與發票生命週期分離）。

    COMPLETED：正常成立；RETURNED：全數退貨（退貨流程設定）；
    VOIDED：整筆作廢（打錯單，視同未發生）。
    """

    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"
    VOIDED = "VOIDED"
```

### 名詞與共用定義

| 名詞 | 主帳務報表現況 | 其他定義 |
|---|---|---|
| 營業額 `gross_turnover` | 自有、寄售、一般商品／餐飲等有效成交額，依退貨日扣減 | 洞察排行不扣退貨 |
| 認列營收 `recognized_revenue` | 自有全額＋寄售抽成，依退貨日扣減非寄售營收 | 日摘要以此數字重新拆稅；發票則以整張銷售總額拆稅 |
| 毛利 `gross_margin` | 自有已知成本毛利＋寄售抽成；`sales-margin`、`daily-summary`、`trends` 與活動成效共用 `margin_breakdown` | 購物金效益使用另一個不扣退貨、範圍較窄的 `period_margin`；洞察排行也自行計算 |
| 應付賣家 | 寄售成交時建立 `PENDING payout_amount` | 報表另列 `PAID`、`CANCELLED`、`reclaim_needed`，只有 `PENDING` 計入待付 |

### 寄售三態在報表中的位置

| 狀態 | 程式現況 | 出現的報表 |
|---|---|---|
| 未售出 | 序號品 `IN_STOCK` 且 ownership 為 consignment；尚無 settlement | 庫存價值的寄售在庫件數／牌價，不進寄售應付 |
| 已售未結算 | 成交建立 `PENDING` settlement | 寄售應付 `total_pending_payout`；主毛利認列有效抽成 |
| 已結算 | 付款後為 `PAID` | 寄售應付 `total_paid_payout`；付款當下進 cash movement |
| 退貨衍生態 | 未付退貨為 `CANCELLED`；已付退貨維持 `PAID` 並設 `reclaim_needed` | 寄售應付各自分欄，不以負數沖抵 pending |

`backend/app/modules/consignment/service.py:213-238`
```python
async def create_settlement(
    self,
    store_id: int,
    *,
    serialized_item_id: int,
    sale_id: int,
    gross: Decimal,
    commission_pct: int,
) -> ConsignmentSettlement:
    """賣出寄售品 → 建 PENDING 結算。

    commission_amount = round_ntd(gross × pct / 100)；payout = gross − commission。
    店家收入只認 commission_amount（§7.3）。
    """
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
    return await self._repo.add(settlement)
```

`backend/app/modules/consignment/service.py:166-177`
```python
"""反轉已鎖定的結算列（PENDING→CANCELLED；PAID→reclaim_needed；其餘 no-op）並留痕。"""
reversed_rows: list[ConsignmentSettlement] = []
for settlement in settlements:
    before_status = settlement.status
if before_status == ConsignmentSettlementStatus.PENDING:
    settlement.status = ConsignmentSettlementStatus.CANCELLED
    after: dict[str, object] = {"status": ConsignmentSettlementStatus.CANCELLED.value}
elif (
    before_status == ConsignmentSettlementStatus.PAID and not settlement.reclaim_needed
):
    settlement.reclaim_needed = True
    after = {"status": before_status.value, "reclaim_needed": True}
```

## P0：會產生錯帳或掉錢

本輪未找到足以在不假設業務規則的前提下判定為 P0 的證據。

## P1：特定條件下數字不一致

### P1-1　跨午夜 session 使同一日摘要混用兩種日期歸屬

**現況：** `daily-cash` 先以 `cash_session.opened_at` 落在自然日內選 session，再把入選 session 的全部 movement 納入；movement 本身沒有再套報表日期。`daily-summary` 同時把這份現金日報與依交易事件時間 `[start, end)` 查出的銷售、退貨與購物金合併。

**風險：** session 若在 23:xx 開啟並跨至次日，次日發生的收現／退款／支出會全數歸到開帳日；同一日摘要的營業額、購物金流量卻歸到事件發生日。日摘要內的現金、營業額與購物金因而無法按日勾稽，且與趨勢報表依 `CashMovement.created_at` 落桶的現金支出也不同。

**為什麼：** 選取範圍套在 session 的開啟時間，而分項合計套在 session 全生命週期。

`backend/app/modules/reports/service.py:576-584`
```python
async def daily_cash(self, store_id: int, report_date: date) -> DailyCashReport:
    """每日現金對帳（docs/19 §2.2）：依 opened_at 的台灣營業日取本店 session。

    每 session 的 expected 與關帳同源（cashdrawer `session_breakdown`）。購物金兌付總額另計、
    只展示不進現金 expected（CLAUDE.md §6）。無 session 日回空 sessions + 全 0 合計（非 500）。
    """
    now = _now()
    start, end = store_day_bounds(report_date)
    sessions = await self._cash.list_sessions_in_range(store_id, start, end)
```

`backend/app/modules/cashdrawer/repository.py:86-99`
```python
async def list_sessions_in_range(
    self, store_id: int, start: datetime, end: datetime
) -> list[CashSession]:
    """opened_at 落在 [start, end) 的本店 session（唯讀報表用，依 id 排序）。"""
    stmt = (
        select(CashSession)
        .where(
            CashSession.store_id == store_id,
            CashSession.opened_at >= start,
            CashSession.opened_at < end,
        )
        .order_by(CashSession.id)
    )
    result = await self._session.scalars(stmt)
    return list(result)
```

`backend/app/modules/cashdrawer/service.py:164-170`
```python
sums = {t: Decimal(0) for t in CashMovementType}
for movement in await self._repo.list_movements(session.id):
    sums[movement.type] += movement.amount
if session.status == CashSessionStatus.CLOSED and session.expected_amount is not None:
    expected = session.expected_amount
else:
    expected = await self.expected_amount(session)
```

`backend/app/modules/reports/service.py:864-876`
```python
async def daily_summary(self, store_id: int, report_date: date) -> DailySummaryReport:
    """每日營運儀表板（docs/19 R5）：組合 daily_cash（R1）+ margin_breakdown（R2）的同源數字。

    稅以認列營收在總額層級推一次（§6）。估算淨利＝毛利 − 當日攤提固定支出，明確標註為估計
    （固定營業費用系統未逐日記錄）；月固定支出未設 → null。
    """
    now = _now()
    start, end = store_day_bounds(report_date)
    cash = await self.daily_cash(store_id, report_date)
    margin = await self._sales.margin_breakdown(store_id, start, end)
    settings = await self._settings.get_effective_settings(store_id)

    flows = await self._sc.flows(store_id, date_from=start, date_to=end, granularity="day")
```

### P1-2　歷史日摘要稅額使用目前稅率，且課稅基礎與已開發票不同

**現況：** 日摘要在查詢當下取得有效設定，將「認列營收」重新拆成未稅／稅；沒有讀取銷售已落盤的 `sale.tax` 或發票的 `tax`／`tax_rate` 快照。結帳與發票建立則以整張 `sale.total` 及結帳當下稅率拆稅並保存。

**風險：** 稅率設定變更後重跑歷史日摘要，歷史稅額會跟著改變。含寄售成交時，日摘要以「自有全額＋寄售抽成」為基礎，發票以「整張銷售總額」為基礎，即使稅率相同也會產生不同稅額；報表稅額不能直接和已開發票勾稽。

**為什麼：** 報表重新運算管理口徑的認列營收，而交易與發票保留的是成交口徑快照。

`backend/app/modules/reports/service.py:870-876`
```python
now = _now()
start, end = store_day_bounds(report_date)
cash = await self.daily_cash(store_id, report_date)
margin = await self._sales.margin_breakdown(store_id, start, end)
settings = await self._settings.get_effective_settings(store_id)

flows = await self._sc.flows(store_id, date_from=start, date_to=end, granularity="day")
```

`backend/app/modules/reports/service.py:887-889`
```python
net_ex_tax, tax = split_tax_inclusive(margin.recognized_revenue, settings.tax_rate)
cogs = margin.owned_cogs + margin.bulk_cogs + margin.catalog_cogs
total_cash_out = cash.total_buyout_out + cash.total_consignment_payout_out
```

`backend/app/modules/sales/service.py:1053-1057`
```python
# 稅於發票總額層級推算一次（§6）；不逐項算稅。
net, tax = split_tax_inclusive(total, settings.tax_rate)
sale.subtotal = Decimal(net)
sale.tax = Decimal(tax)
sale.total = total
```

`backend/app/modules/sales/service.py:1138-1147`
```python
if settings.einvoice_enabled and total > 0:
    info = invoice_info if invoice_info is not None else InvoiceInfoInput()
    is_b2b = info.buyer_tax_id is not None
    donate = info.npoban is not None
    has_carrier = info.carrier_type is not None and info.carrier_id is not None
    await self._einvoice.create_pending_invoice(
        store_id,
        sale_id=sale.id,
        total=total,
        tax_rate=settings.tax_rate,
```

`backend/app/modules/einvoice/models.py:129-134`
```python
net: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 未稅
tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 稅額
total: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 含稅總額
# 結帳當下稅率快照（Codex 第九輪）：F0401 金額/TaxRate 以此計——結帳後改 settings
# 稅率不得改變已落地發票的申報內容。
tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0.05"))
```

### P1-3　現有報表集合無法直接對應 401 所需的發票、折讓與進項稅資料

**現況：** 15 個報表 endpoint 中，稅額只有日摘要的重新計算值；`ReportsService` 沒有讀取 invoice、invoice allowance 或 purchasing receipt。另一方面，發票／折讓與進項發票的淨額、稅額都已分別落在其他模組。

**風險：** 報表輸出不能直接得出「有效銷項發票減有效折讓」或「進項發票稅額」的期間合計，也沒有發票狀態／日期口徑；因此不能僅靠現有報表欄位對應 401，也不能由報表交叉驗證銷項與進項稅。

**為什麼：** 報表服務的資料來源停在 sales/settings/store-credit 等管理彙總，沒有納入已落盤的稅務憑證事實。

`backend/app/modules/reports/service.py:23-29`
```python
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.service import ContactService
from app.modules.inventory.service import InventoryService
from app.modules.reports.aging import BUCKET_KEYS as INVENTORY_BUCKET_KEYS
from app.modules.reports.aging import _bucket_for_age
```

`backend/app/modules/reports/service.py:68-72`
```python
from app.modules.sales.service import SalesService
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.modules.storecredit.suggestion_service import PremiumSuggestionService
from app.modules.user.service import UserService
```

`backend/app/modules/einvoice/models.py:192-200`
```python
id: Mapped[int] = mapped_column(primary_key=True)
store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
invoice_id: Mapped[int] = mapped_column(index=True)
return_id: Mapped[int | None] = mapped_column()
allowance_no: Mapped[str | None] = mapped_column(String(16))
net: Mapped[Decimal] = mapped_column(Numeric(12, 0))
tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
voided: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
```

`backend/app/modules/purchasing/models.py:163-170`
```python
# 進項發票（裁示 2026-07-11）：供應商開立的發票於**收貨時**選填登錄（漏登可事後補登一次）。
# 號碼＝2 英文＋8 數字；金額整數元；net＋tax 由 total 以 split_tax_inclusive 拆分（§6），
# DB CHECK 守恆與一致性（要嘛全空、要嘛號碼/日期/三金額齊備且 net+tax=total）。
invoice_number: Mapped[str | None] = mapped_column(String(10))
invoice_date: Mapped[date | None] = mapped_column(Date)
invoice_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
invoice_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
invoice_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
```

### P1-4　寄售退貨的抽成會回寫原銷售期，而非歸到退貨發生日

**現況：** 主毛利說明退貨應歸屬退貨發生日，非寄售營收／成本也確實依 return event 扣減；但寄售抽成是先找「銷售建立於查詢期間內」的 sale id，再依 settlement 的目前狀態加總。退貨會把 settlement 改成 `CANCELLED` 或 `reclaim_needed`，這兩者又會被抽成查詢排除。

**風險：** 寄售品跨期間退貨後，重跑原銷售期會直接少掉原本的抽成；重跑退貨期則因原 sale id 不在該期，不會出現負抽成。歷史期間會隨後續退貨改變，且寄售抽成與同份毛利的其他退貨項目使用不同歸屬日。

**為什麼：** 寄售抽成採 settlement 現況，而不是期間內的抽成／反轉事件。

`backend/app/modules/sales/service.py:1839-1852`
```python
async def margin_breakdown(
    self, store_id: int, date_from: datetime, date_to: datetime
) -> MarginBreakdown:
    """期間銷售/毛利彙總（單一口徑，R2/R5/R6 共用）。寄售抽成經 consignment service 取。

    退貨扣減（D-8(1)，裁示 2026-07-16）：依退貨行按比例自各營收/成本桶扣除，
    歸屬**退貨發生日**（與退現出帳同日）。
    """
    from app.modules.returns.service import ReturnsService

    comp = await self._repo.margin_components(store_id, date_from, date_to)
    adj = await ReturnsService(self._session).margin_adjustments(store_id, date_from, date_to)
    comp = replace(
```

`backend/app/modules/sales/service.py:1868-1869`
```python
    sale_ids = await self._repo.nonvoid_sale_ids(store_id, date_from, date_to)
    commission = await self._consignment.commission_total_for_sales(store_id, sale_ids)
```

`backend/app/modules/consignment/repository.py:210-223`
```python
async def commission_total_for_sales(self, store_id: int, sale_ids: list[int]) -> Decimal:
    """指定銷售集合的「有效」寄售抽成合計（SC-5b/毛利推導；唯讀，店家收入只認抽成）。

    退貨反轉後抽成經濟上不成立（客人已退款，不變量 #7）：未付結算 CANCELLED、
    已付結算 reclaim_needed（款待向寄售人追回）皆排除，否則報表高估店家收入。
    """
    if not sale_ids:
        return Decimal(0)
    stmt = select(func.coalesce(func.sum(ConsignmentSettlement.commission_amount), 0)).where(
        ConsignmentSettlement.store_id == store_id,
        ConsignmentSettlement.sale_id.in_(sale_ids),
        ConsignmentSettlement.status != ConsignmentSettlementStatus.CANCELLED,
        ConsignmentSettlement.reclaim_needed.is_(False),
    )
```

`backend/app/modules/consignment/service.py:166-177`
```python
"""反轉已鎖定的結算列（PENDING→CANCELLED；PAID→reclaim_needed；其餘 no-op）並留痕。"""
reversed_rows: list[ConsignmentSettlement] = []
for settlement in settlements:
    before_status = settlement.status
if before_status == ConsignmentSettlementStatus.PENDING:
    settlement.status = ConsignmentSettlementStatus.CANCELLED
    after: dict[str, object] = {"status": ConsignmentSettlementStatus.CANCELLED.value}
elif (
    before_status == ConsignmentSettlementStatus.PAID and not settlement.reclaim_needed
):
    settlement.reclaim_needed = True
    after = {"status": before_status.value, "reclaim_needed": True}
```

### P1-5　「實際毛利率」有另一套不扣退貨、品項範圍較窄的定義

**現況：** 購物金效益匯出把 `gross_margin_m` 標成「直接量測」，但數字來自 `period_margin`。該函式明文不套退貨，且底層 `goods_margin_and_revenue` 排除一般商品、餐飲與寄售散裝；主報表的 `margin_breakdown` 則扣退貨並納入一般商品已知成本等桶。

**風險：** 有退貨、一般商品／餐飲或寄售散裝時，購物金效益的「實際毛利率」不會等於同期間銷售毛利報表的毛利率；依它計算的 `delta_per_1000` 也沿用不同口徑。

**為什麼：** 同一毛利名詞不是共用主帳務 service，而是另有一份獨立 SQL／Python 定義。

`backend/app/modules/reports/router.py:173-181`
```python
rows = [
    ["選用率 take_rate", _ratio_cell(report.take_rate), "直接量測"],
    ["平均溢價率 avg_premium_rate", _ratio_cell(report.avg_premium_rate), "直接量測"],
    ["額外消費率 excess_spend_rate", _ratio_cell(report.excess_spend_rate), "直接量測"],
    ["實際毛利率 gross_margin_m", _ratio_cell(report.gross_margin_m), "直接量測"],
    ["沉澱率 beta_retention", _ratio_cell(report.beta_retention), estimate],
    ["新增比例 alpha_incremental", _ratio_cell(report.alpha_incremental), alpha_label],
    ["損益敏感度 delta_per_1000", _ratio_cell(report.delta_per_1000), estimate],
    ["兌付筆數 redemption_count", str(report.redemption_count), "直接量測"],
]
```

`backend/app/modules/sales/service.py:1812-1821`
```python
async def period_margin(
    self, store_id: int, date_from: datetime, date_to: datetime
) -> dict[str, Decimal]:
    """期間毛利拆解：revenue（商品收入）、buyout_margin（買斷毛利）、寄售抽成。

    買斷毛利＋商品收入由自有/寄售商品行推導（sales 經 inventory 成本，repo 唯讀 join）；
    寄售抽成經 consignment service 依未作廢 sale_id 取（§2）。m = (買斷毛利＋抽成) ÷ 收入。
    """
    # 已知限制（裁示 2026-07-16「其餘文件化」）：此處**不套**退貨扣減。period_margin
    # 僅供 SC-5b 溢價建議引擎（分析用、非帳務），D-8 退貨扣減已在 margin_breakdown
```

`backend/app/modules/sales/service.py:1824-1828`
```python
    buyout_margin, revenue = await self._repo.goods_margin_and_revenue(
        store_id, date_from, date_to
    )
    sale_ids = await self._repo.nonvoid_sale_ids(store_id, date_from, date_to)
    commission = await self._consignment.commission_total_for_sales(store_id, sale_ids)
```

`backend/app/modules/sales/repository.py:481-485`
```python
"""二手商品的（買斷毛利, 商品收入）；未作廢、期間內。

買斷毛利只認自有品（序號 OWNED：售價−取得成本；散裝自有：售價−每件成本×數量）。
商品收入＝自有序號＋寄售序號＋自有散裝的售價（寄售收入計入分母，店家收入認抽成另計）。
排除：一般商品（catalog 無成本基礎）、寄售散裝（無抽成基礎）——皆於 docs/16 §5B 註明。
"""
```

### P1-6　經營洞察的排行與同份報表營收結構使用不同退貨口徑

**現況：** 品牌／類型排行直接彙總 sale line，查詢沒有讀取 return line／return event；同一份洞察報表的 `revenue_mix` 卻取自主帳務 `margin_breakdown`，會依退貨日扣減。

**風險：** 期間有退貨時，品牌／類型列的營收與毛利加總不會等於同份報表的營收結構；同期退貨再售時，排行會重複計入件數與營收。

**為什麼：** breakdown 與 headline 分別採售出明細及主毛利兩個資料源。

`backend/app/modules/sales/repository.py:382-394`
```python
.join(Sale, SaleLine.sale_id == Sale.id)
.join(SerializedItem, SaleLine.serialized_item_id == SerializedItem.id)
.where(
    Sale.store_id == store_id,
    Sale.status != SaleStatus.VOIDED,
    Sale.created_at >= date_from,
    Sale.created_at < date_to,
    SaleLine.line_type == SaleLineType.SERIALIZED,
    # 贈品成交 0 元：算進「售出」會虛增銷量、又以零營收減成本拉低毛利。
    SaleLine.line_kind != SaleLineKind.GIFT,
)
)
return [tuple(r) for r in rows]
```

`backend/app/modules/reports/service.py:500-502`
```python
ser = await self._sales.serialized_sold_rows(store_id, date_from, date_to)
bulk = await self._sales.bulk_sold_rows(store_id, date_from, date_to)
norm = self._normalize_serialized(ser) + self._normalize_bulk(bulk)
```

`backend/app/modules/reports/service.py:535-539`
```python
revenue_mix=InsightsRevenueMix(
    secondhand=bd.secondhand_revenue - bd.consignment_commission_income,
    consignment_commission=bd.consignment_commission_income,
    food=bd.food_revenue,
),
```

### P1-7　已退回的贈品仍留在贈品報表與貢獻毛利成本中（已修正）

**修正後判定：** 贈品報表保留送出總額，另列退回與淨額；退回事件歸屬實際退貨日，貢獻毛利在同日沖回贈品成本。退貨 service 先經 sales service 篩出贈品行，再以 500 筆為上限查期間前／期間內累積量，避免一般商品退貨參與歷史贈品聚合。證據：`backend/app/modules/reports/service.py:368-451`、`backend/app/modules/returns/service.py:386-461`、`backend/app/modules/returns/repository.py:75-134`、`backend/app/modules/sales/service.py:2063-2072,2144-2156`。以下保留初始稽核證據。

**現況：** 退貨流程已把贈品退回記成獨立的 `GIFT_RETURN` stock reason，註解也說這是為了讓贈品報表算出退回件數；但贈品報表只查原 sale line，沒有讀取 return line 或 stock movement。主毛利的退貨調整又明確排除 gift line，正向 `gift_cost` 因而不會反轉。

**風險：** 贈品實際退回後，贈品報表仍顯示已送出數量／原價／成本，`contribution_margin` 也持續扣除該贈品成本；庫存事實與報表不一致。

**為什麼：** 已存在的 `GIFT_RETURN` 事件沒有進入任何贈品報表彙總。

`backend/app/modules/returns/service.py:876-884`
```python
async def _return_inventory_line(
    self, store_id: int, return_id: int, line: SaleLine, qty: int
) -> None:
    # 贈品退回要能與一般退貨分辨，否則贈品報表算不出「送出去又退回來」幾件。
    reason = (
        StockReason.GIFT_RETURN
        if line.line_kind is SaleLineKind.GIFT
        else StockReason.RETURN
    )
```

`backend/app/modules/sales/repository.py:663-675`
```python
base = (
    select(SaleLine)
    .join(Sale, SaleLine.sale_id == Sale.id)
    .where(
        Sale.store_id == store_id,
        Sale.status != SaleStatus.VOIDED,
        Sale.created_at >= date_from,
        Sale.created_at < date_to,
        SaleLine.line_kind == SaleLineKind.GIFT,
    )
)
retail = func.coalesce(func.sum(SaleLine.original_unit_price * SaleLine.qty), 0)
cost = func.coalesce(func.sum(SaleLine.cost_snapshot), 0)
```

`backend/app/modules/returns/repository.py:124-133`
```python
.where(
    CustomerReturn.store_id == store_id,
    CustomerReturn.created_at >= date_from,
    CustomerReturn.created_at < date_to,
    Sale.status != SaleStatus.VOIDED,
    # 贈品的成本在正向報表就獨立於毛利之外（gift_cost 自成一桶）；
    # 反轉時若把它算進 catalog_cogs，退回一件成本 100 的贈品會讓當期
    # 一般商品 COGS 變成 −100，憑空生出 100 元毛利。
    SaleLine.line_kind != SaleLineKind.GIFT,
)
```

### P1-8　銷售毛利 CSV／XLSX 少了 JSON 已提供的付款費用、淨毛利與付款方式（已修正）

**修正後判定：** CSV／XLSX 已補支付手續費合計、淨毛利、各付款方式淨收款／手續費，以及贈品送出／退回／淨額；貢獻毛利標題亦與公式一致。證據：`backend/app/modules/reports/finance_router.py:464-504`。以下保留初始稽核證據。

**現況：** `SalesMarginReport` 明確有 `payment_fee_total`、`net_margin`、`payment_methods`；前端也顯示這些值。CSV／XLSX 的 rows 沒有輸出三者，且把 `contribution_margin` 標成只扣贈品成本，實際公式還扣了付款手續費。

**風險：** 同一次報表查詢，JSON／畫面與匯出檔不是等價帳務證據；只保存匯出檔時會看不到已知付款成本與各渠道收款，也會誤讀貢獻毛利的扣除項目。

**為什麼：** 匯出欄位清單沒有跟 schema／service 的新增金額同步。

`backend/app/modules/reports/schemas.py:151-161`
```python
# 支付手續費（docs/30 §7 決策 1）：手續費為店家成本、獨立支出行；gross_margin 不含（認列營收
# 不變），另提供 net_margin = gross_margin − payment_fee_total。payment_methods 依 tender 分列。
payment_fee_total: NTDAmount
net_margin: NTDAmount
payment_methods: list[PaymentMethodTotal]
# 臨時折扣（少收的錢）與贈品（送出去的成本）各自分列，不混進毛利也不互相混：
# 贈品成本若計入 gross_margin，營收 0 加全額成本會讓毛利率失真。
manual_discount_total: NTDAmount = Decimal(0)
gift_retail_value: NTDAmount = Decimal(0)
gift_cost: NTDAmount = Decimal(0)
contribution_margin: NTDAmount = Decimal(0)  # 淨毛利 − 贈品成本
```

`backend/app/modules/sales/service.py:1917-1925`
```python
payment_fee_total=comp.payment_fee_total,
net_margin=gross_margin - comp.payment_fee_total,
payment_methods=comp.payment_methods,
manual_discount_total=comp.manual_discount_total,
gift_retail_value=comp.gift_retail_value,
gift_cost=comp.gift_cost,
# 扣掉贈品成本後的實際貢獻。贈品成本**不進 gross_margin**——營收 0 加全額成本
# 會讓毛利率失真；它的代價要單獨看見。
contribution_margin=gross_margin - comp.payment_fee_total - comp.gift_cost,
```

`backend/app/modules/reports/finance_router.py:469-493`
```python
rows=[
    ["營業額", str(report.gross_turnover)],
    ["認列營收", str(report.recognized_revenue)],
    ["自有序號成本", str(report.owned_cogs)],
    ["自有散裝成本", str(report.bulk_cogs)],
    ["一般商品成本", str(report.catalog_cogs)],
    ["寄售抽成收入", str(report.consignment_commission_income)],
    ["毛利", str(report.gross_margin)],
    ["毛利率", rate],
    ["成本未知營收", str(report.unknown_cost_sales)],
    ["餐飲營收", str(report.food_revenue)],
    ["二手營收", str(report.secondhand_revenue)],
    ["現金收款", str(report.cash_received)],
    ["購物金收款", str(report.store_credit_redeemed)],
    ["交易筆數", str(report.transaction_count)],
    ["臨時折扣", str(report.manual_discount_total)],
    ["贈品原價價值", str(report.gift_retail_value)],
    ["贈品成本", str(report.gift_cost)],
    ["貢獻毛利（扣贈品成本）", str(report.contribution_margin)],
],
```

### P1-9　購物金負債報表宣稱帳本推導，實際總額與會員列使用 balance cache（已修正）

**修正後判定：** liability 的會員餘額、帳齡限制值與總額均改由 ledger 分組加總推導；account cache 只留在 reconciliation 的差異偵測用途。證據：`backend/app/modules/storecredit/repository.py:175-198`、`backend/app/modules/storecredit/service.py:588-610`。以下保留初始稽核證據。

**現況：** router 文件宣稱所有數值從帳本推導；但負債總額、會員餘額與帳齡最後限制值都來自 `store_credit_accounts.balance`。另一個 reconciliation endpoint 才同時回 ledger total 與 cache total，且明文在 mismatch 時以 ledger 值為準。

**風險：** reconciliation 已偵測到 mismatch 時，liability 仍會回傳不可信的 cache 總負債及會員／帳齡分布，且報表本身沒有 `cached_total_trustworthy` 標記；同一時間兩張購物金報表可顯示不同總負債。

**為什麼：** liability 沒有使用已存在的 `ledger_total_outstanding` 權威查詢。

`backend/app/modules/reports/router.py:1-4`
```python
"""SC-4 購物金報表路由（MANAGER；docs/16 §4）：負債/帳齡、流量、對帳；?format=csv|xlsx 匯出。

所有報表唯讀、store 範圍；數值從帳本推導。匯出檔含產生時間/區間/店別。
"""
```

`backend/app/modules/storecredit/repository.py:175-194`
```python
async def ledger_total_outstanding(self, store_id: int) -> Decimal:
    """帳本推導總負債 = Σ 各 contact 的正向帳本餘額（含孤兒帳本）。"""
    per_contact = (
        select(func.sum(StoreCreditLedger.signed_amount).label("bal"))
        .where(StoreCreditLedger.store_id == store_id)
        .group_by(StoreCreditLedger.contact_id)
        .subquery()
    )
    stmt = select(func.coalesce(func.sum(per_contact.c.bal), 0)).where(per_contact.c.bal > 0)
    value = await self._session.scalar(stmt)
    return Decimal(value if value is not None else 0)

async def total_outstanding(self, store_id: int) -> Decimal:
    """全域總負債 = Σ 正餘額（docs/16 §4 對帳）。"""
    stmt = select(func.coalesce(func.sum(StoreCreditAccount.balance), 0)).where(
        StoreCreditAccount.store_id == store_id,
        StoreCreditAccount.balance > 0,
    )
    value = await self._session.scalar(stmt)
    return Decimal(value if value is not None else 0)
```

`backend/app/modules/storecredit/repository.py:242-248`
```python
async def balances_by_contact(self, store_id: int) -> dict[int, Decimal]:
    """各會員快取餘額（含 0／負，呼叫端自行過濾）。"""
    stmt = select(StoreCreditAccount.contact_id, StoreCreditAccount.balance).where(
        StoreCreditAccount.store_id == store_id
    )
    rows = await self._session.execute(stmt)
    return {int(c): Decimal(b) for c, b in rows}
```

`backend/app/modules/storecredit/service.py:594-615`
```python
async def aging_report(self, store_id: int, *, now: datetime) -> dict[str, object]:
    """未兌付負債帳齡分桶（FIFO 沖銷發出列；docs/16 §5A）。"""
    lots_rows = await self._repo.positive_lots(store_id)
    positive_sum = await self._repo.positive_sum_by_contact(store_id)
    balances = await self._repo.balances_by_contact(store_id)
    per_contact: dict[int, list[IssuedLot]] = {}
    for contact_id, amount, issued_at in lots_rows:
        per_contact.setdefault(contact_id, []).append(
            IssuedLot(amount=amount, issued_at=issued_at)
        )
    buckets: OrderedDict[str, Decimal] = OrderedDict((k, Decimal(0)) for k in BUCKET_KEYS)
    for contact_id, lots in per_contact.items():
        balance = balances.get(contact_id, Decimal(0))
        if balance <= 0:
            continue  # 無未兌付餘額者不入帳齡
        consumed = positive_sum.get(contact_id, Decimal(0)) - balance
        contact_buckets = age_outstanding(lots, consumed, now)
        for key, value in contact_buckets.items():
            buckets[key] += value
    return {
        "total_outstanding": await self._repo.total_outstanding(store_id),
        "buckets": buckets,
    }
```

`backend/app/modules/storecredit/service.py:569-577`
```python
# 總負債雙值（adversarial 第八輪 high）：快取值在有不符時不可信，
# 一律同時回帳本推導值（含孤兒帳本）；呈報以 ledger 值為準。
return {
    "store_id": store_id,
    "accounts_checked": len(await self._repo.list_accounts(store_id)),
    "mismatches": mismatches,
    "ledger_total_outstanding": str(await self._repo.ledger_total_outstanding(store_id)),
    "cached_total_outstanding": str(await self._repo.total_outstanding(store_id)),
    "cached_total_trustworthy": not mismatches,
}
```

### P1-10　銷售毛利的營收會按退貨日扣減，收款方式仍只加總原銷售 tender（已修正）

**修正後判定：** 銷售毛利依退貨發生日聚合 `ReturnTender`，從同期間各付款方式收款扣除；CASH、STORE_CREDIT、LINE_PAY、TAIWAN_PAY 均可呈現負的期間淨收款。退款渠道查詢與贈品數量查詢分離，只有需要付款／贈品明細的銷售毛利入口才執行，趨勢、每日摘要、活動與 insights 不增加該查詢。證據：`backend/app/modules/returns/repository.py:56-73`、`backend/app/modules/returns/service.py:407-413`、`backend/app/modules/sales/service.py:2044-2067,2123-2142`。以下保留初始稽核證據。

**現況：** `margin_breakdown` 以 return event 的期間扣除營收／成本；同一報表的 `cash_received`、`store_credit_redeemed`、付款方式與手續費，只查 `SaleTender` 且以原 `Sale.created_at` 落桶。實際退款去向另存在 `ReturnTender`，沒有進入報表。

**風險：** 退貨發生期間可以出現負營業額／負認列營收，但各付款方式收款為 0；原銷售期間仍保留全額收款。使用同一份 sales-margin 對現金、購物金或行動支付淨收款時會差退款額，無法直接和錢櫃及購物金流量按期勾稽。

**為什麼：** 營收採淨退貨事件口徑，tender 欄位採原始收款口徑，schema 沒有同期間退款 tender 欄位。

`backend/app/modules/sales/service.py:1850-1867`
```python
comp = await self._repo.margin_components(store_id, date_from, date_to)
adj = await ReturnsService(self._session).margin_adjustments(store_id, date_from, date_to)
comp = replace(
    comp,
    owned_serialized_revenue=comp.owned_serialized_revenue - adj.owned_serialized_revenue,
    owned_serialized_cogs=comp.owned_serialized_cogs - adj.owned_serialized_cogs,
    owned_bulk_revenue=comp.owned_bulk_revenue - adj.owned_bulk_revenue,
    owned_bulk_cogs=comp.owned_bulk_cogs - adj.owned_bulk_cogs,
    consignment_serialized_revenue=comp.consignment_serialized_revenue
    - adj.consignment_serialized_revenue,
    consignment_bulk_revenue=comp.consignment_bulk_revenue - adj.consignment_bulk_revenue,
    catalog_revenue=comp.catalog_revenue - adj.catalog_revenue,
    catalog_known_revenue=comp.catalog_known_revenue - adj.catalog_known_revenue,
    catalog_cogs=comp.catalog_cogs - adj.catalog_cogs,
    unknown_cost_revenue=comp.unknown_cost_revenue
    - adj.catalog_revenue
    - adj.no_cost_serialized_revenue,
)
```

`backend/app/modules/sales/repository.py:839-869`
```python
tender_rows = list(
    await self._session.execute(
        select(
            SaleTender.tender_type,
            func.coalesce(func.sum(SaleTender.amount), 0),
            func.coalesce(func.sum(SaleTender.fee_amount), 0),
        )
        .join(Sale, SaleTender.sale_id == Sale.id)
        .where(
            Sale.store_id == store_id,
            Sale.status != SaleStatus.VOIDED,
            Sale.created_at >= date_from,
            Sale.created_at < date_to,
        )
        .group_by(SaleTender.tender_type)
    )
)
cash_received = Decimal(0)
store_credit_redeemed = Decimal(0)
payment_fee_total = Decimal(0)
# 依 tender 型別列舉順序穩定輸出各方式（收款額, 手續費），供報表分列。
by_type: dict[TenderType, tuple[Decimal, Decimal]] = {}
for tender_type, amount, fee in tender_rows:
    by_type[tender_type] = (Decimal(amount), Decimal(fee))
    payment_fee_total += Decimal(fee)
    if tender_type == TenderType.CASH:
        cash_received = Decimal(amount)
    elif tender_type == TenderType.STORE_CREDIT:
        store_credit_redeemed = Decimal(amount)
payment_methods = tuple(
    (t.value, by_type[t][0], by_type[t][1]) for t in TenderType if t in by_type
)
```

`backend/app/modules/returns/models.py:108-127`
```python
class ReturnTender(Base, TimestampMixin):
    """本次退貨的實際退款去向；各渠道金額加總應等於退貨退款總額。"""

    __tablename__ = "return_tenders"
    __table_args__ = (
        UniqueConstraint("return_id", "tender_type", name="uq_return_tenders_return_type"),
        CheckConstraint("amount > 0", name="ck_return_tenders_amount_positive"),
        ForeignKeyConstraint(
            ["return_id", "store_id"],
            ["returns.id", "returns.store_id"],
            ondelete="CASCADE",
            name="fk_return_tenders_return_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    return_id: Mapped[int] = mapped_column(index=True)
    tender_type: Mapped[TenderType] = mapped_column(_enum_col(TenderType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
```

## P2：可維護性風險

### P2-1　趨勢與活動成效按桶／按活動重複執行整套毛利查詢

**現況：** 趨勢最多建立 400 個 bucket，每個 bucket 依序呼叫 `margin_breakdown`、購物金 flow 與 cash out；活動成效也對每個活動呼叫一次 `margin_breakdown`。`margin_breakdown` 本身會再執行多個 sales／returns／consignment 查詢。

**風險：** 查詢次數隨 bucket 或活動數線性增加；大期間報表會造成大量 DB round-trip，並拉長同一份報表各桶取數的時間跨度。

**為什麼：** 每個 bucket／活動都由 Python 迴圈各自觸發一輪完整查詢。

`backend/app/modules/reports/service.py:821-836`
```python
now = _now()
rows: list[TrendRow] = []
cursor, _ = store_bucket_bounds(granularity, date_from)
count = 0
while cursor < date_to:
    _, nxt = store_bucket_bounds(granularity, cursor)
    count += 1
    if count > MAX_TREND_BUCKETS:
        raise DomainError(
            f"期間/粒度產生過多分桶（>{MAX_TREND_BUCKETS}）；請縮小區間或放大粒度"
        )
    bstart = max(cursor, date_from)
    bend = min(nxt, date_to)
    margin = await self._sales.margin_breakdown(store_id, bstart, bend)
    issued, redeemed = await self._sum_flows(store_id, bstart, bend)
    cash_out = await self._cash.cash_out_in_range(store_id, bstart, bend)
```

`backend/app/modules/reports/service.py:542-556`
```python
campaigns = await self._campaigns.list_campaigns(store_id)
discount_totals = await self._sales.discount_totals_by_campaign(store_id)
rows: list[CampaignPerformanceRow] = []
for c in campaigns:
    if c.status not in (CampaignStatus.ACTIVE, CampaignStatus.ENDED):
        continue
    bd = await self._sales.margin_breakdown(store_id, c.starts_at, c.ends_at)
```

### P2-2　多個報表存在逐列查詢，錢櫃開帳 session 還會重讀 movement

**現況：** 日現金逐 session 查 breakdown；開帳 session 的 breakdown 先讀一次 movement，再由 `expected_amount` 讀第二次。購物金負債逐會員查 contact，折扣報表逐店員查 user，購物金 reconcile 也逐帳戶查 ledger sum 與 latest balance。

**風險：** session、會員、店員或帳戶數增加時產生 N+1；開帳日現金每個 session 至少多一次相同 movement 讀取。

**為什麼：** 已取得的集合沒有批次補齊關聯資料，且同一個 session breakdown 內重複讀取相同明細。

`backend/app/modules/cashdrawer/service.py:158-170`
```python
sums = {t: Decimal(0) for t in CashMovementType}
for movement in await self._repo.list_movements(session.id):
    sums[movement.type] += movement.amount
if session.status == CashSessionStatus.CLOSED and session.expected_amount is not None:
    expected = session.expected_amount
else:
    expected = await self.expected_amount(session)
```

`backend/app/modules/cashdrawer/service.py:126-130`
```python
async def expected_amount(self, session: CashSession) -> Decimal:
    """結帳應有現金 = 開帳零用金 + Σ(SALE_IN, ACQUISITION_VOID_IN)
    − Σ(BUYOUT_OUT, PAYOUT_OUT, SALE_REFUND_OUT) ± ΣMANUAL_ADJUST。"""
    total = session.opening_float
    for movement in await self._repo.list_movements(session.id):
```

`backend/app/modules/reports/service.py:945-948`
```python
balances = await self._sc.per_member_balances(store_id)
per_member: list[MemberBalanceRow] = []
for contact_id, balance in balances:
    contact = await self._contacts.get_contact(store_id, contact_id)
```

`backend/app/modules/reports/service.py:319-321`
```python
clerk_rows = []
for clerk_id, count, total in by_clerk:
    user = await self._users.get_user_in_store(store_id, clerk_id)
```

`backend/app/modules/storecredit/service.py:556-558`
```python
for account in await self._repo.list_accounts(store_id):
    ledger_sum = await self._repo.sum_signed(store_id, account.contact_id)
    latest = await self._repo.latest_balance_after(store_id, account.contact_id)
```

### P2-3　庫存價值與寄售應付把全店明細拉回 Python 聚合

**現況：** 三種庫存估值查詢與寄售應付查詢都明文「全部、不分頁」，service 再逐列加總與分桶。

**風險：** 資料量與應用記憶體、傳輸量、Python 運算時間線性成長；報表只需要的合計仍承擔完整 ORM row 載入成本。

**為什麼：** SUM／COUNT／CASE 等彙總在應用層完成。

`backend/app/modules/inventory/repository.py:304-314`
```python
async def serialized_for_valuation(self, store_id: int) -> list[SerializedItem]:
    """在庫序號品（IN_STOCK，全部、不分頁；庫存價值/庫齡報表用）。"""
    stmt = (
        select(SerializedItem)
        .where(
            SerializedItem.store_id == store_id,
            SerializedItem.status == SerializedItemStatus.IN_STOCK,
        )
        .order_by(SerializedItem.id)
    )
    return list((await self._session.scalars(stmt)).all())
```

`backend/app/modules/inventory/repository.py:316-339`
```python
async def bulk_for_valuation(self, store_id: int) -> list[BulkLot]:
    """在售且有餘量的散裝堆（ON_SALE 且 remaining_qty>0，全部；庫存價值/庫齡報表用）。"""
    stmt = (
        select(BulkLot)
        .where(
            BulkLot.store_id == store_id,
            BulkLot.status == BulkLotStatus.ON_SALE,
            BulkLot.remaining_qty > 0,
        )
        .order_by(BulkLot.id)
    )
    return list((await self._session.scalars(stmt)).all())

async def catalog_for_valuation(self, store_id: int) -> list[CatalogProduct]:
    """有庫存的一般商品（quantity_on_hand>0，全部；庫存價值報表用，成本未建模）。"""
    stmt = (
        select(CatalogProduct)
        .where(
            CatalogProduct.store_id == store_id,
            CatalogProduct.quantity_on_hand > 0,
        )
        .order_by(CatalogProduct.id)
    )
    return list((await self._session.scalars(stmt)).all())
```

`backend/app/modules/consignment/repository.py:148-151`
```python
async def all_settlements_for_report(self, store_id: int) -> list[dict[str, Any]]:
    """店內所有寄售結算（不分頁、不篩狀態；應付報表用，呈現/合計由 service 決定）。"""
    stmt = self._settlements_select(store_id, None).order_by(ConsignmentSettlement.id.desc())
    return [dict(row) for row in (await self._session.execute(stmt)).mappings().all()]
```

`backend/app/modules/reports/service.py:153-169`
```python
all_rows = await self._consignment.all_settlements_for_report(store_id)
total_pending = Decimal(0)
total_paid = Decimal(0)
total_cancelled = Decimal(0)
total_reclaim = Decimal(0)
rows: list[ConsignmentPayableRow] = []
for r in all_rows:
    status = r["status"].value if hasattr(r["status"], "value") else str(r["status"])
    payout = Decimal(r["payout_amount"])
    if status == "PENDING":
        total_pending += payout
    elif status == "PAID":
        total_paid += payout
    elif status == "CANCELLED":
        total_cancelled += payout
    if r["reclaim_needed"]:
        total_reclaim += payout
```

### P2-4　主要期間報表欄位沒有宣告 store＋時間的複合索引

**現況：** 銷售、退貨、cash movement 與 store-credit ledger 的報表查詢重複使用 `store_id`＋時間範圍；ORM／Alembic 只看到各欄單獨索引或其他唯一索引，沒有這些表的 `(store_id, created_at)`／`(store_id, opened_at)` 複合索引。`TimestampMixin.created_at` 本身也未設 `index=True`。

**風險：** 單店資料量增加時，期間查詢可能掃描該店大量歷史列，並放大 P2-1 的逐桶查詢成本。此判定只涵蓋 repo 宣告的 schema；正式 DB 是否另有人工建立索引不在本次靜態稽核可見範圍。

**為什麼：** 目前索引設計主要支援 store 或關聯 id，而報表的主 access pattern 是 store 加時間。

`backend/app/modules/sales/repository.py:729-733`
```python
.where(
    Sale.store_id == store_id,
    Sale.status != SaleStatus.VOIDED,
    Sale.created_at >= date_from,
    Sale.created_at < date_to,
```

`backend/app/modules/sales/models.py:58-81`
```python
__tablename__ = "sales"
__table_args__ = (
    UniqueConstraint("store_id", "idempotency_key", name="uq_sales_store_idempotency_key"),
    # 複合租戶鍵：供 sale_tenders 的 (sale_id, store_id) 複合 FK 綁定（SC-3 P2）。
    UniqueConstraint("id", "store_id", name="uq_sales_id_store"),
```

`backend/app/modules/sales/models.py:78-81`
```python
)

id: Mapped[int] = mapped_column(primary_key=True)
store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
```

`backend/alembic/versions/c3d8f1a4b6e2_add_cashdrawer.py:113-118`
```python
op.create_index(
    op.f("ix_cash_movements_session_id"), "cash_movements", ["session_id"], unique=False
)
op.create_index(
    op.f("ix_cash_movements_store_id"), "cash_movements", ["store_id"], unique=False
)
```

`backend/alembic/versions/b8e4f9a2c6d1_add_returns.py:70-71`
```python
op.create_index(op.f("ix_returns_store_id"), "returns", ["store_id"], unique=False)
op.create_index(op.f("ix_returns_sale_id"), "returns", ["sale_id"], unique=False)
```

`backend/alembic/versions/c5d1e8a2b7f4_add_store_credit_ledger.py:135-150`
```python
op.create_index("ix_store_credit_ledger_store_id", "store_credit_ledger", ["store_id"])
op.create_index(
    "uq_store_credit_ledger_idem_key",
    "store_credit_ledger",
    ["store_id", "idempotency_key"],
    unique=True,
    postgresql_where=sa.text("idempotency_key IS NOT NULL"),
)
op.create_index(
    "uq_store_credit_ledger_reversal_of",
    "store_credit_ledger",
    ["reversal_of_id"],
    unique=True,
    postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
)
op.create_index("ix_store_credit_ledger_contact_id", "store_credit_ledger", ["contact_id"])
```

## 待確認

### 待確認-1　營業日是否應為自然日 00:00，或另有門市切點（已確認）

**修正後判定：** 店長確認不會跨午夜；本項不再列為現行缺口。以下保留初始稽核證據。

**現況：** 共用 `store_day_bounds` 把「營業日」固定為 Asia/Taipei 00:00 至次日 00:00；日現金另以 session 開啟日歸屬，形成 P1-1 的可證明不一致。

**待確認資訊：** 門市正式營業日定義、跨午夜班別政策、日結是否以關帳時間／開帳時間／固定時刻切分。沒有這些資訊，無法判定自然日或 session 日哪個才是業務正確口徑。

`backend/app/core/time.py:40-44`
```python
def store_day_bounds(value: date) -> tuple[datetime, datetime]:
    """回傳台灣營業日對應的 UTC 半開區間 ``[start, end)``。"""
    local_start = datetime(value.year, value.month, value.day, tzinfo=STORE_TIME_ZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)
```

### 待確認-2　測試交易是否可能進入正式店別資料（已確認）

**修正後判定：** 店長確認測試交易不會進入正式店別；本項關閉。以下保留初始稽核證據。

**現況：** `SaleStatus` 只有 `COMPLETED`、`RETURNED`、`VOIDED`；期間報表只排除 `VOIDED`，Sale model 與報表條件未見 `is_test`／sandbox 類欄位。

**待確認資訊：** 正式環境是否允許測試結帳、是否有專用測試 store、測試資料是否以正式作廢流程排除，以及營運上如何識別測試單。若測試單可能以 `COMPLETED` 留在正式 store，現有報表會納入；若環境／店別完全隔離，則不是缺口。

`backend/app/shared/enums.py:155-168`
```python
class SaleStatus(StrEnum):
    """銷售單自身的生命週期（與發票生命週期分離）。

    COMPLETED：正常成立；RETURNED：全數退貨（退貨流程設定）；
    VOIDED：整筆作廢（打錯單，視同未發生）。

    **VOIDED 是「這筆銷售是否有效」的唯一事實來源**：報表、清單與後續操作一律以
    `sale.status != VOIDED` 判斷，不得再用 `invoice_status == VOID` 代替——後者是
    **發票**的狀態，電子發票關閉時根本沒有發票，兩者語意不同（見 ADR）。
    """

    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"
    VOIDED = "VOIDED"
```

`backend/app/modules/sales/repository.py:729-735`
```python
.where(
    Sale.store_id == store_id,
    Sale.status != SaleStatus.VOIDED,
    Sale.created_at >= date_from,
    Sale.created_at < date_to,
    SaleLine.line_type == SaleLineType.SERIALIZED,
    SaleLine.line_kind != SaleLineKind.GIFT,
)
```

### 待確認-3　401 的法定分類、寄售稅務身分與進項憑證輸入規則（延期，不實作）

**修正後判定：** 目前不做 401 報表；本項保留為未來需求，不將現有資料冒充法定申報數字。以下保留初始稽核證據。

**現況：** P1-3 已確認沒有 401 專用報表。另進項發票只輸入含稅總額，系統以「登錄當下目前設定稅率」反推淨額與稅額；schema 沒有讓使用者登錄憑證實際稅額或不同稅別。寄售銷售的日摘要稅與開票基礎又不同（P1-2）。

**待確認資訊：** 各商品的應稅／免稅／零稅率分類、寄售交易是以店家為賣方或代理人、401 各欄應採發票開立日或平台核可日、作廢／折讓認列規則、紙本憑證來源，以及供應商發票是否可能混合稅別或非目前設定稅率。缺少這些資料時，不能判定哪個稅額才符合法定申報口徑。

`backend/app/modules/purchasing/service.py:299-310`
```python
@staticmethod
def _invoice_fields(invoice: "InputInvoiceIn", tax_rate: Decimal) -> dict[str, object]:
    """進項發票欄位＋稅額拆分（§6：net = round_ntd(total/(1+rate))、tax = total − net）。"""
    net, tax = split_tax_inclusive(Decimal(invoice.invoice_total), tax_rate)
    return {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "invoice_total": Decimal(invoice.invoice_total),
        "invoice_net": Decimal(net),
        "invoice_tax": Decimal(tax),
    }
```

`backend/app/modules/purchasing/service.py:330-337`
```python
if receipt.invoice_number is not None:
    raise InputInvoiceAlreadySet(
        f"收貨批次 {receipt_id} 已登錄發票 {receipt.invoice_number}，不可覆寫"
    )
settings = await self._settings.get_effective_settings(store_id)
for key, value in self._invoice_fields(invoice, Decimal(settings.tax_rate)).items():
    setattr(receipt, key, value)
await self._session.flush()
```

### 待確認-4　「已移除稅後淨利」是否也包含目前仍存在的「估算淨利」（已處理）

**修正後判定：** 店長確認移除；`estimated_net_income` 已從後端 schema/service/router、前端、匯出與 API contract 移除。以下保留初始稽核證據。

**現況：** repo 搜尋未找到 `稅後淨利`、`after_tax` 或 `post_tax` 欄位／引用；但後端 schema、service、CSV／XLSX 與前端仍完整保留 `estimated_net_income`。它的公式是「毛利－當日攤提固定支出」，沒有稅後計算，也沒有扣已知的付款手續費與贈品成本。

**待確認資訊：** 「已移除範圍」只禁止法定／會計語意的稅後淨利，還是所有淨利估算都應消失；以及 `estimated_net_income` 預期的正式定義。未取得原始需求或 ADR，不能把名稱不同的估算淨利直接判定為殘留。

`backend/app/modules/reports/schemas.py:200-203`
```python
transaction_count: int
avg_ticket: NTDAmountOpt
estimated_net_income: NTDAmountOpt  # 估算淨利＝毛利 − 當日攤提固定支出；未設 → null
estimated_net_income_note: str  # 明確標註為估計（固定營業費用未逐日記錄）
```

`backend/app/modules/reports/service.py:896-905`
```python
days_in_month = calendar.monthrange(report_date.year, report_date.month)[1]
monthly = settings.monthly_fixed_cash_outflow
estimated_net_income: Decimal | None = (
    margin.gross_margin - Decimal(round_ntd(monthly / Decimal(days_in_month)))
    if monthly > 0
    else None
)
note = (
    "估算淨利＝毛利 − 當日攤提固定支出（月固定現金支出 ÷ 當月天數）；固定營業費用"
    "（租金/薪資）未逐日記錄，僅供概估、非精確損益。未設定月固定支出 → N/A。"
)
```

`frontend/app/(authed)/reports/page.tsx:370-375`
```tsx
<div className="rpt-stat rpt-stat-estimate">
  <dt>
    估算淨利
    <span className="rpt-badge-estimate">估計值</span>
  </dt>
  <dd><MoneyText value={report.estimated_net_income} /></dd>
</div>
```

`frontend/app/(authed)/reports/page.tsx:399-403`
```tsx
{report.estimated_net_income_note && (
  <p className="rpt-dashboard-footnote">
    <span className="rpt-badge-estimate">估計值</span>
    估算淨利說明：{report.estimated_net_income_note}
  </p>
)}
```

### 待確認-5　活動結束後才發生的退貨是否應回算活動成效（接受現況）

**修正後判定：** 店長接受活動成效不回算活動結束後退貨。以下保留初始稽核證據。

**現況：** 活動成效永遠以活動排定的 `[starts_at, ends_at)` 呼叫 `margin_breakdown`；主毛利的退貨則歸屬退貨發生日。活動結束後才退貨時，該 return event 不在活動區間內，因此不會減少該活動列的營業額／毛利。

**待確認資訊：** 活動成效是要呈現活動期間的當時成交（gross performance），還是活動所帶來交易的最終淨結果（含活動結束後退貨）。兩種定義都可能合理，現有需求註解不足以判定。

`backend/app/modules/reports/service.py:542-556`
```python
async def campaign_performance(self, store_id: int) -> CampaignPerformanceReport:
    """活動成效報表（docs/21 C4）：每檔生效中/已結束活動的營運成效 + 其發出的折讓。唯讀。

    營運指標以活動排定區間 [starts_at, ends_at) 取 margin_breakdown（與 R2 同源、半開區間）；
    折讓總額依 sale_line.campaign_id 精確歸屬（非區間概算）。DRAFT/CANCELLED 無成交、不列。
    依 starts_at 新到舊排序。
    """
    campaigns = await self._campaigns.list_campaigns(store_id)
    discount_totals = await self._sales.discount_totals_by_campaign(store_id)
    rows: list[CampaignPerformanceRow] = []
    for c in campaigns:
        if c.status not in (CampaignStatus.ACTIVE, CampaignStatus.ENDED):
            continue
        # 區間 [starts_at, ends_at)；模型 CHECK 保證 ends_at > starts_at（滿足 from<to）。
        bd = await self._sales.margin_breakdown(store_id, c.starts_at, c.ends_at)
```

`backend/app/modules/sales/service.py:1842-1846`
```python
"""期間銷售/毛利彙總（單一口徑，R2/R5/R6 共用）。寄售抽成經 consignment service 取。

退貨扣減（D-8(1)，裁示 2026-07-16）：依退貨行按比例自各營收/成本桶扣除，
歸屬**退貨發生日**（與退現出帳同日）。
"""
```

## 可對帳性結論

| 勾稽 | 現況 | 結論 |
|---|---|---|
| `sales.total` ↔ `sale_tenders` | deferred DB guard 驗證每張銷售 tender 合計等於 total | 交易層可勾稽 |
| Store-credit sale tender ↔ ledger debit | deferred DB guard 驗證同店、同買方、等額雙向對應 | 交易層可勾稽 |
| Return total ↔ return tenders ↔ store-credit refund | return DB guard 驗證退款渠道與帳本 | 交易層可勾稽 |
| 銷售報表 ↔ 錢櫃 | `sales-margin.cash_received` 已依退貨日扣現金退款；錢櫃另含開帳、收購、寄售付款與人工調整 | 銷售／退款事件段可按期勾稽；整個錢櫃餘額仍須把其他 movement 類型分列 |
| 銷售報表 ↔ 購物金 flow | `sales-margin.store_credit_redeemed` 已依退貨日扣購物金退款；flow 另含發出、人工調整與沖正 | 兌付／退款事件段可按期勾稽；負債總額仍以 ledger liability／reconciliation 為準 |
| 銷售報表 ↔ 發票／折讓 | 發票有交易稅額快照、折讓有獨立金額，但報表不用；P1-2 的日摘要另以認列營收重算 | 不能由現有報表直接勾稽 |
| 稅額 ↔ 401 | 無發票／折讓／進項稅期間報表，且法定分類待確認 | 不能直接對應 |

交易層既有守衛證據：

`backend/app/modules/sales/models.py:406-410`
```python
# 收款守衛：
#  (A) 對平：Σ sale_tenders.amount 必須等於 sales.total（現金＋購物金須與總額對平）。
#  (B) 購物金 ↔ 帳本雙向綁定（負債級）：STORE_CREDIT 收款金額必須對應一筆等額、同店、
#      同買方的 store_credit_ledger DEBIT/SALE 分錄；反之 DEBIT/SALE 分錄也必須對應一筆
#      等額的 STORE_CREDIT 收款。
```

`backend/app/modules/returns/models.py:132-145`
```python
# 退貨金額與退款渠道、購物金帳本的 deferred 雙向守衛。service 可在同一交易內依序建立
# return、明細、退款渠道與帳本；到 COMMIT 才要求完整對平，兼顧原子寫入與 DB 不變量。
RETURN_TENDER_GUARD_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION returns_verify_refund_consistency(p_return_id BIGINT)
RETURNS void AS $$
DECLARE
  return_store INT;
  return_sale BIGINT;
  return_total NUMERIC;
  line_sum NUMERIC;
  tender_sum NUMERIC;
  sc_tender NUMERIC;
  sc_ledger NUMERIC;
```

## 範圍外缺口

- 作廢／更正流程依任務要求不展開；本輪僅確認報表普遍以 `Sale.status != VOIDED` 排除作廢銷售，未提出流程設計。
