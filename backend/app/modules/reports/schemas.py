"""SC-4 購物金報表的回應 schema（docs/16 §4/§5；金額字串整數元 §11）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]
NTDAmountOpt = Annotated[
    Decimal | None,
    PlainSerializer(lambda d: None if d is None else str(d), return_type=str | None),
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
    consignment_commission_income: NTDAmount
    gross_margin: NTDAmount
    gross_margin_rate: NTDAmountOpt  # 毛利 ÷ 已知成本營收；分母 0 → null
    unknown_cost_sales: NTDAmount  # 成本未知營收（catalog + 缺成本自有），不假造毛利
    cash_received: NTDAmount
    store_credit_redeemed: NTDAmount
    transaction_count: int


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
