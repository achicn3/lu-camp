"""storecredit 查詢/校正 schema（金額字串整數元，§6/§11）。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.shared.enums import StoreCreditEntryType, StoreCreditSourceType

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]
PremiumRate = Annotated[
    Decimal | None, PlainSerializer(lambda d: None if d is None else str(d), return_type=str | None)
]


class StoreCreditEntryRead(BaseModel):
    """帳本分錄輸出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_type: StoreCreditEntryType
    signed_amount: NTDAmount
    balance_after: NTDAmount
    cash_equivalent: NTDAmount | None
    premium_rate_applied: PremiumRate
    source_type: StoreCreditSourceType
    source_id: int | None
    reversal_of_id: int | None
    reason: str | None
    created_by: int
    created_at: datetime


class StoreCreditBalanceRead(BaseModel):
    """餘額＋異動歷史（GET /contacts/{id}/store-credit）。"""

    contact_id: int
    balance: NTDAmount
    entries: list[StoreCreditEntryRead]


class StoreCreditAdjustRequest(BaseModel):
    """人工校正輸入（限 MANAGER；可正可負、非零；事由必填留痕）。"""

    amount: Decimal
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("amount")
    @classmethod
    def _whole_nonzero(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("金額必須為整數元")
        if value == 0:
            raise ValueError("金額不可為零")
        return value

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("事由不可為空白")
        return stripped
