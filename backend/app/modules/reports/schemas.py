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
    issued: NTDAmount
    redeemed: NTDAmount
    net_change: NTDAmount


class FlowsReport(BaseModel):
    """§5A 發出 vs 兌付 vs 淨變化（granularity=day/week/month）。"""

    generated_at: datetime
    store_id: int
    granularity: str
    date_from: datetime
    date_to: datetime
    rows: list[FlowRow]


class ReconciliationReport(BaseModel):
    """§4 對帳：全帳戶 I-3（SUM==快取==最新 balance_after）+ 全域總負債。"""

    generated_at: datetime
    store_id: int
    mismatches: list[dict[str, object]]
    ledger_total_outstanding: NTDAmount
    cached_total_outstanding: NTDAmount
    cached_total_trustworthy: bool
