"""SC-4 購物金報表的回應 schema（docs/16 §4/§5；金額字串整數元 §11）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from app.shared.enums import CampaignStatus


def format_ntd(value: Decimal) -> str:
    """輸出一般十進位金額，避免 Decimal 的科學記號流入畫面或匯出檔。"""
    return format(value, "f")


NTDAmount = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]
NTDAmountOpt = Annotated[
    Decimal | None,
    PlainSerializer(lambda d: None if d is None else format_ntd(d), return_type=str | None),
]


class AgingBuckets(BaseModel):
    """未兌付負債帳齡分桶（依發出時間）。"""

    lt_30d: NTDAmount
    d30_90: NTDAmount
    d90_180: NTDAmount
    d180_365: NTDAmount
    gt_365d: NTDAmount


class MemberBalanceRow(BaseModel):
    contact_id: int
    name: str
    balance: NTDAmount


class LiabilityReport(BaseModel):
    """§5A 購物金負債報表。"""

    generated_at: datetime
    store_id: int
    total_outstanding: NTDAmount
    aging_buckets: AgingBuckets
    per_member: list[MemberBalanceRow]
    # 負債健康比 = total_outstanding ÷ monthly_fixed_cash_outflow；分母為 SC-5 設定，
    # 尚未上線 → 回 null（N/A）。SC-5 合併後接上。
    liability_health_ratio: str | None


class FlowRow(BaseModel):
    period: date
    # issued/redeemed 為 net 欄（毛額 − 沖正）；net_change = issued − redeemed + adjustment_net,
    # 恰等於該期帳本 signed 淨變化、可與 liability 差額對上（docs/19 §3.1）。
    issued: NTDAmount
    redeemed: NTDAmount
    net_change: NTDAmount
    # R0 稽核分欄（docs/19 §3.2）：毛額落發出/兌付當期、沖正落沖正當期；adjustment_net 為人工校正。
    issued_gross: NTDAmount
    issued_reversed: NTDAmount
    redeemed_gross: NTDAmount
    redeemed_reversed: NTDAmount
    adjustment_net: NTDAmount


class FlowsReport(BaseModel):
    """§5A 發出 vs 兌付 vs 淨變化（granularity=day/week/month）。"""

    generated_at: datetime
    store_id: int
    granularity: str
    date_from: datetime
    date_to: datetime
    rows: list[FlowRow]


class DailyCashSessionRow(BaseModel):
    """每日現金對帳——單一 session 列（docs/19 §2.2）。"""

    session_id: int
    status: str
    opened_at: datetime
    closed_at: datetime | None
    opened_by: int
    closed_by: int | None
    opening_float: NTDAmount
    cash_sales: NTDAmount  # SALE_IN（僅現金 leg）
    acquisition_void_in: NTDAmount  # 作廢收購退現（F6.5，進帳）
    buyout_out: NTDAmount
    consignment_payout_out: NTDAmount
    sale_refund_out: NTDAmount  # 退貨退現（出帳）
    manual_adjust_total: NTDAmount  # 可正可負
    expected_amount: NTDAmount  # 與關帳同公式
    counted_amount: NTDAmountOpt  # 未關帳 → null
    variance: NTDAmountOpt  # 未關帳 → null


class DailyCashReport(BaseModel):
    """每日現金對帳報表（docs/19 §2.2）。expected 與關帳 expected_amount 同源。"""

    generated_at: datetime
    store_id: int
    date: date
    sessions: list[DailyCashSessionRow]
    # 當日合計（counted/variance 僅含已關帳 session）。
    total_opening_float: NTDAmount
    total_cash_sales: NTDAmount
    total_acquisition_void_in: NTDAmount
    total_buyout_out: NTDAmount
    total_consignment_payout_out: NTDAmount
    total_sale_refund_out: NTDAmount
    total_manual_adjust: NTDAmount
    total_expected: NTDAmount
    total_counted: NTDAmount
    total_variance: NTDAmount
    total_store_credit_redeemed_display_only: NTDAmount


class PaymentMethodTotal(BaseModel):
    """單一收款方式的期間彙總（docs/30 §7 決策 1）：收款額＋手續費（店家成本）。"""

    method: str  # TenderType 值（CASH/STORE_CREDIT/LINE_PAY/TAIWAN_PAY）
    received: NTDAmount  # 該方式收款額合計
    fee: NTDAmount  # 該方式手續費合計（現金/購物金為 0）


class SalesMarginReport(BaseModel):
    """銷售 / 毛利報表（docs/19 §2.3）。未作廢銷售；買斷認成本、寄售只認抽成、catalog 成本 N/A。"""

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    gross_turnover: NTDAmount  # 營業額（所有成交全額，含寄售全額）
    recognized_revenue: NTDAmount  # 認列營收（自有全額 + 寄售抽成）
    owned_cogs: NTDAmount  # 自有序號成本
    bulk_cogs: NTDAmount  # 自有散裝成本
    catalog_cogs: NTDAmount = Decimal(0)  # 一般商品成本（有成交成本快照者）
    consignment_commission_income: NTDAmount
    gross_margin: NTDAmount
    gross_margin_rate: NTDAmountOpt  # 毛利 ÷ 已知成本營收；分母 0 → null
    unknown_cost_sales: NTDAmount  # 成本未知營收（catalog + 餐飲 + 缺成本自有），不假造毛利
    # 餐飲/二手分列（裁示）：food=餐飲認列營收；secondhand=非餐飲認列營收（=recognized−food）
    food_revenue: NTDAmount
    secondhand_revenue: NTDAmount
    cash_received: NTDAmount
    store_credit_redeemed: NTDAmount
    transaction_count: int
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


class DailySummaryReport(BaseModel):
    """每日營運儀表板（docs/19 R5）：組合 R1 現金 + R2 毛利的同源數字，店長一眼看「今天賺多少」。

    營業額 vs 認列營收明確區分（寄售全額 ≠ 店家營收）；成本未知不假造毛利；估算淨利明確標註。
    """

    generated_at: datetime
    store_id: int
    date: date
    # 營收面
    gross_turnover: NTDAmount  # 營業額（含寄售全額）
    recognized_revenue: NTDAmount  # 認列營收（自有全額 + 寄售抽成）
    net_sales_ex_tax: NTDAmount  # 認列營收除稅（總額層級推稅一次）
    tax: NTDAmount
    consignment_commission_income: NTDAmount
    # 成本/毛利面
    cogs: NTDAmount  # 自有序號 + 散裝成本
    gross_margin: NTDAmount
    gross_margin_rate: NTDAmountOpt  # 分母 0 → null
    unknown_cost_sales: NTDAmount
    # 餐飲/二手分列（裁示）：food=餐飲認列營收；secondhand=非餐飲認列營收（=recognized−food）
    food_revenue: NTDAmount
    secondhand_revenue: NTDAmount
    # 現金/支出面（與 R1 同源）
    cash_sales_in: NTDAmount
    acquisition_void_in: NTDAmount
    buyout_out: NTDAmount
    consignment_payout_out: NTDAmount
    manual_adjust: NTDAmount
    total_cash_out: NTDAmount  # buyout + consignment payout（店家真實掏現）
    expected_cash: NTDAmount
    counted_cash: NTDAmount
    cash_variance: NTDAmount
    store_credit_issued: NTDAmount  # 購物金發出（非現金）
    store_credit_redeemed: NTDAmount  # 購物金兌付（非現金）
    # 概覽
    transaction_count: int
    avg_ticket: NTDAmountOpt  # 客單價＝營業額 ÷ 筆數；0 筆 → null
    estimated_net_income: NTDAmountOpt  # 估算淨利＝毛利 − 當日攤提固定支出；未設 → null
    estimated_net_income_note: str  # 明確標註為估計（固定營業費用未逐日記錄）


class TrendRow(BaseModel):
    """財務趨勢單一期間（docs/19 R6）。period 為桶起始日。"""

    period: date
    gross_turnover: NTDAmount
    recognized_revenue: NTDAmount
    food_revenue: NTDAmount
    secondhand_revenue: NTDAmount
    gross_margin: NTDAmount
    gross_margin_rate: NTDAmountOpt
    cogs: NTDAmount
    total_cash_out: NTDAmount
    store_credit_issued: NTDAmount
    store_credit_redeemed: NTDAmount
    transaction_count: int


class TrendsReport(BaseModel):
    """財務趨勢時間序列（docs/19 R6）：依 granularity 分桶的 R5 同義 KPI；餵趨勢圖。

    桶與 [from, to) 取交集（首/末桶可為部分期間），故各桶 KPI 加總 = 全期 margin_breakdown，
    可交叉驗證（同源）。空桶補 0 列，圖表連續。日界固定為 Asia/Taipei。
    """

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    granularity: str
    rows: list[TrendRow]


class InventoryValueReport(BaseModel):
    """庫存價值與庫齡（docs/19 §2.4）。

    自有（owned）才計成本價值；寄售在庫另列售價總額、不當自有資產；catalog 成本未建模 → cost=null。
    aging 為「自有在庫成本價值」按入庫時間分桶（catalog 無入庫時間、寄售非自有，皆不入 aging）。
    已售/退場（SOLD/SOLD_OUT/RETURNED/WRITTEN_OFF、remaining=0）不入在庫。
    """

    generated_at: datetime
    store_id: int
    # 自有序號
    owned_serialized_count: int
    owned_serialized_cost: NTDAmount
    owned_serialized_retail: NTDAmount
    # 自有散裝（count = 剩餘件數）
    owned_bulk_remaining_qty: int
    owned_bulk_cost: NTDAmount
    owned_bulk_retail: NTDAmount
    # 自有在庫成本/售價總計
    total_owned_cost_value: NTDAmount
    total_owned_retail_value: NTDAmount
    # 寄售在庫（另列，非自有資產；售價總額）
    consignment_serialized_count: int
    consignment_bulk_remaining_qty: int
    consignment_inventory_gross: NTDAmount
    # 一般商品（成本未建模 → cost N/A）
    catalog_total_qty: int
    catalog_retail_value: NTDAmount
    catalog_cost_value: NTDAmountOpt
    # 成本未知（從未收過貨）的一般商品件數：有幾件未知要說出來，不是整個類別回 null。
    catalog_unknown_cost_qty: int = 0  # 恆 null（成本未建模）
    # 庫齡：自有在庫成本價值按入庫時間分桶（Σ = total_owned_cost_value）
    owned_cost_aging: AgingBuckets


class ConsignmentPayableRow(BaseModel):
    """寄售應付單列（docs/19 §2.5）。輸出寄售人姓名/電話，禁 national_id。"""

    settlement_id: int
    consignor_id: int | None
    consignor_name: str | None
    consignor_phone: str | None
    sale_id: int
    item_code: str
    item_name: str
    gross: NTDAmount
    commission_amount: NTDAmount
    payout_amount: NTDAmount
    status: str
    reclaim_needed: bool
    sale_created_at: datetime


class ConsignmentPayablesReport(BaseModel):
    """寄售應付報表（docs/19 §2.5）。

    只計 PENDING 入待付合計；PAID/CANCELLED 分欄；reclaim_needed（已付後退貨需追回）獨立分欄，
    不以負數沖抵 pending。status_filter 只影響明細列，合計恆涵蓋全部狀態。
    """

    generated_at: datetime
    store_id: int
    status_filter: str
    rows: list[ConsignmentPayableRow]
    total_pending_payout: NTDAmount
    total_paid_payout: NTDAmount
    total_cancelled_payout: NTDAmount
    total_reclaim_needed_payout: NTDAmount


class ReconciliationReport(BaseModel):
    """§4 對帳：全帳戶 I-3（SUM==快取==最新 balance_after）+ 全域總負債。"""

    generated_at: datetime
    store_id: int
    mismatches: list[dict[str, object]]
    ledger_total_outstanding: NTDAmount
    cached_total_outstanding: NTDAmount
    cached_total_trustworthy: bool


# 比率欄（0–1 等小數）以字串序列化、缺樣本回 null（沿 NTDAmountOpt 風格）。
Ratio = NTDAmountOpt

# 估計值欄位（UI/報表須標示「估計值」；α 另標「代理法」）。
ESTIMATE_FIELDS = ["beta_retention", "alpha_incremental", "delta_per_1000"]
ALPHA_METHOD_NOTE = (
    "α 為代理法估計值（docs/16 §5B-α）：以「兌付對應 CREDIT 入帳前的低頻/新會員消費」"
    "近似新增傾向，無法驗證個體反事實；樣本不足時更不穩定，不得作為精確損益依據。"
)


class EffectivenessReport(BaseModel):
    """§5B 效益指標報表（單期間）。estimate_fields 所列為估計值，須於 UI 標示。"""

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    take_rate: Ratio
    avg_premium_rate: Ratio
    beta_retention: Ratio
    excess_spend_rate: Ratio
    alpha_incremental: Ratio
    gross_margin_m: Ratio
    delta_per_1000: Ratio
    redemption_count: int
    alpha_sample_insufficient: bool
    estimate_fields: list[str]
    alpha_method_note: str


class CampaignPerformanceRow(BaseModel):
    """單檔活動成效（docs/21 C4）。

    營運指標取活動排定區間 [starts_at, ends_at) 的銷售（與 R2 sales-margin 同源、半開區間）；
    campaign_discount_total 為此活動實際發出的折讓（依 sale_line.campaign_id 歸屬，非區間概算）。
    gross_margin_rate 分母為已知成本營收，0/未知 → null（不假造）。
    """

    campaign_id: int
    name: str
    status: CampaignStatus
    discount_pct: int
    starts_at: datetime
    ends_at: datetime
    campaign_discount_total: NTDAmount
    gross_turnover: NTDAmount
    recognized_revenue: NTDAmount
    gross_margin: NTDAmount
    gross_margin_rate: NTDAmountOpt
    transaction_count: int


class DiscountReasonRow(BaseModel):
    """一個折扣原因在期間內的使用情形。"""

    reason_id: int | None  # 未指定原因的折扣歸為 null 一列
    reason_name: str
    adjustment_count: int
    item_discount_total: NTDAmount
    order_discount_total: NTDAmount
    discount_total: NTDAmount


class DiscountReport(BaseModel):
    """臨時折扣報表：誰在什麼原因下折了多少。

    金額一律取落盤的 `applied_amount`，**不事後重算**——商品價格與活動日後都會變，
    重算會讓歷史折扣跟著漂。無主管核准機制，這份報表是事後稽核的主要依據。
    """

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    discount_total: NTDAmount
    item_discount_total: NTDAmount
    order_discount_total: NTDAmount
    adjustment_count: int
    by_reason: list[DiscountReasonRow]
    by_clerk: list["DiscountClerkRow"]


class DiscountClerkRow(BaseModel):
    """一位店員在期間內打出的折扣（無核准機制 → 事後稽核靠這個看得出異常）。"""

    clerk_user_id: int
    clerk_username: str
    adjustment_count: int
    discount_total: NTDAmount


class GiftReasonRow(BaseModel):
    """一個贈送原因在期間內送出的數量、原價價值與成本。"""

    reason_id: int | None
    reason_name: str
    gift_count: int  # 贈品明細筆數
    gift_qty: int  # 件數
    retail_value: NTDAmount
    cost: NTDAmount


class GiftReport(BaseModel):
    """贈品報表：送了什麼、送了多少、成本多少。

    贈品原價**不計入營業額**、**不計入折扣總額**；成本獨立呈現，不混入商品毛利
    （營收 0 加全額成本會讓毛利率失真）。退回的贈品不在此扣除——退回另有庫存異動
    `GIFT_RETURN` 可查，混在一起會讓「送出去多少」這個問題再也答不出來。
    """

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    gift_qty: int
    retail_value: NTDAmount
    cost: NTDAmount
    by_reason: list[GiftReasonRow]
    by_product: list["GiftProductRow"]


class GiftProductRow(BaseModel):
    """被當成贈品送出去最多的品項。"""

    description: str
    gift_qty: int
    retail_value: NTDAmount
    cost: NTDAmount


class CampaignPerformanceReport(BaseModel):
    """活動成效報表（docs/21 C4）：每檔生效中/已結束活動期間的營運成效 + 該活動發出的折讓。唯讀。"""

    generated_at: datetime
    store_id: int
    rows: list[CampaignPerformanceRow]


class InsightsBreakdownRow(BaseModel):
    """經營洞察：單一品牌或類型的售出彙整列。"""

    key: int | None
    label: str
    units_sold: int
    revenue: NTDAmount
    margin: NTDAmount
    avg_unit_price: NTDAmount
    avg_days_in_stock: float | None


class InsightsTurnover(BaseModel):
    """經營洞察：周轉 / 滯銷摘要。"""

    in_stock_over_90d: int
    avg_turnover_days: float | None
    owned_serialized: int
    consignment_serialized: int
    bulk_on_sale: int
    catalog_in_stock: int


class InsightsRevenueMix(BaseModel):
    """經營洞察：業態營收結構（認列口徑：寄售只認抽成）。"""

    secondhand: NTDAmount
    consignment_commission: NTDAmount
    food: NTDAmount


class InsightsReport(BaseModel):
    """經營洞察報表（#8）：品牌/類型暢銷、周轉/滯銷、業態結構。趨勢另走 /reports/trends。"""

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    brand_breakdown: list[InsightsBreakdownRow]
    category_breakdown: list[InsightsBreakdownRow]
    turnover: InsightsTurnover
    revenue_mix: InsightsRevenueMix


# ── 餐飲內用／外帶（docs/39）──────────────────────────────


class ServiceModeStats(BaseModel):
    """單一服務型態（內用或外帶）在期間內的統計。"""

    groups: int
    """組數：**一筆含餐飲品項的結帳算一組**（不論點了幾杯）。"""
    share: Decimal
    """佔比。**分母是「有餐飲的單」**，不是全店訂單——用全店當分母，
    多賣幾台二手裝備就會把內用佔比稀釋掉，看起來像內用生意變差。"""
    fnb_revenue: NTDAmount
    """餐飲營收：只計 `MENU` 行的實付（net_amount）。"""
    avg_ticket: NTDAmount
    """餐飲客單價 ＝ 餐飲營收 ÷ 組數。**只算 MENU 行**，
    否則同一張單的二手成交會把它灌水。"""
    gross_total: NTDAmount
    """整單合計（含非餐飲品項），供對照用；**不要拿它算客單價**。"""


class DineInSummary(BaseModel):
    dine_in: ServiceModeStats
    takeout: ServiceModeStats


class DineInTrendBucket(BaseModel):
    """一個時間桶（依 granularity）的內用/外帶組數與餐飲營收。空桶補 0。"""

    bucket_start: datetime
    dine_in_groups: int
    takeout_groups: int
    dine_in_revenue: NTDAmount
    takeout_revenue: NTDAmount


class DineInHourBucket(BaseModel):
    """**台北時間**的一個小時（0–23）。用 UTC 會讓尖峰整體位移 8 小時。"""

    hour: int
    dine_in_groups: int
    takeout_groups: int


class DineInReport(BaseModel):
    """餐飲內用／外帶報表（docs/39）。

    **口徑三條**（畫面必須顯示，不能只留在文件裡）：
    1. 佔比的分母是「有餐飲的單」，不是全店訂單
    2. 客單價只算 MENU 行
    3. 內用與外帶的客單價**不可直接比較**——外帶不累點、不折扣、
       不可用購物金（docs/35），計價條件本就不同
    """

    generated_at: datetime
    store_id: int
    date_from: datetime
    date_to: datetime
    granularity: str
    summary: DineInSummary
    trend: list[DineInTrendBucket]
    hourly: list[DineInHourBucket]
