"""cashdrawer 的 Pydantic schema：開帳/結帳/異動 I/O。

金額以字串傳輸（§11）、新台幣整數元（§6）。SALE_IN/BUYOUT_OUT/CONSIGNMENT_PAYOUT_OUT
多由系統交易自動產生；此處的手動端點主要供 MANUAL_ADJUST。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.modules.cashdrawer.models import CashMovement, CashSession
from app.shared.enums import CashMovementType, CashSessionStatus

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


def _whole(value: Decimal, *, allow_negative: bool) -> Decimal:
    if not allow_negative and value < 0:
        raise ValueError("金額不可為負")
    if value != value.to_integral_value():
        raise ValueError("金額必須為整數元（無角分）")
    return value


class CashSessionOpenRequest(BaseModel):
    """開帳：期初零用金。"""

    opening_float: NTDAmount

    @field_validator("opening_float")
    @classmethod
    def _check(cls, v: Decimal) -> Decimal:
        return _whole(v, allow_negative=False)


class CashSessionCloseRequest(BaseModel):
    """結帳：點數後的實際現金。"""

    counted_amount: NTDAmount

    @field_validator("counted_amount")
    @classmethod
    def _check(cls, v: Decimal) -> Decimal:
        return _whole(v, allow_negative=False)


class CashMovementCreateRequest(BaseModel):
    """記一筆現金異動（MANUAL_ADJUST 可正可負；其餘類型非負）。"""

    type: CashMovementType
    amount: NTDAmount
    note: str = Field(min_length=1, max_length=200)  # 事由必填（留痕，§5）

    @field_validator("note")
    @classmethod
    def _note_not_blank(cls, value: str) -> str:
        """事由去空白後不得為空（純空白等同無留痕，Codex P2）。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("事由不可為空白")
        return stripped

    @field_validator("amount")
    @classmethod
    def _check(cls, v: Decimal) -> Decimal:
        # 允許負值；MANUAL_ADJUST 才可負，其他類型的非負限制在 service 層守。
        return _whole(v, allow_negative=True)


class CashSessionRead(BaseModel):
    """現金班別輸出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    status: CashSessionStatus
    opening_float: NTDAmount
    opened_by: int
    opened_at: datetime
    closed_by: int | None
    closed_at: datetime | None
    counted_amount: NTDAmount | None
    expected_amount: NTDAmount | None
    variance: NTDAmount | None

    @classmethod
    def from_model(cls, session: CashSession) -> "CashSessionRead":
        return cls.model_validate(session)


class CashMovementRead(BaseModel):
    """現金異動輸出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    session_id: int
    type: CashMovementType
    amount: NTDAmount
    ref_type: str | None
    ref_id: int | None
    note: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, movement: CashMovement) -> "CashMovementRead":
        return cls.model_validate(movement)
