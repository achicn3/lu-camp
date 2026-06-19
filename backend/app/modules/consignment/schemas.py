"""consignment 讀寫 schema（Phase 4 / 4A）：結算查詢與付款結果。

金額以字串整數元傳輸（§6/§11）；不含寄售人 PII（姓名/電話/national_id 由 facade/會員中心提供，
本 API 只回結算本身的數字與狀態）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.shared.enums import ConsignmentSettlementStatus

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class ConsignmentSettlementRead(BaseModel):
    """寄售結算查詢輸出（付款工作清單／應付查詢／付款結果）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    serialized_item_id: int
    sale_id: int
    gross: NTDAmount
    commission_pct: int
    commission_amount: NTDAmount
    payout_amount: NTDAmount
    status: ConsignmentSettlementStatus
    paid_at: datetime | None
    paid_by: int | None
    reclaim_needed: bool
    created_at: datetime
    item_code: str | None = None
    item_name: str | None = None
    consignor_id: int | None = None
    consignor_name: str | None = None
    consignor_phone: str | None = None
    sale_created_at: datetime | None = None
