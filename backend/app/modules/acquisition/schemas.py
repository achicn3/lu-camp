"""acquisition 的 Pydantic schema：輸入驗證 + 輸出。

金額以字串傳輸（§11）、新台幣整數元（§6）：NTDAmount 序列化為字串，並驗證為非負整數元。
依 type 做欄位互斥/必填驗證；服務層另有同義的領域守門（供直接呼叫的單元測試）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.modules.acquisition.models import Acquisition
from app.shared.enums import AcquisitionType, BulkAcquisitionBasis, Grade, PayoutMethod

# 金額：輸出序列化為字串；輸入可吃字串或數字（Pydantic 轉 Decimal）。
NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]

COMMISSION_PCT_MIN = 0
COMMISSION_PCT_MAX = 100


def _ensure_whole_nonneg(value: Decimal, field: str) -> Decimal:
    if value < 0:
        raise ValueError(f"{field} 不可為負")
    if value != value.to_integral_value():
        raise ValueError(f"{field} 必須為整數元（無角分）")
    return value


class AcquisitionItemIn(BaseModel):
    """序號單品入庫明細（BUYOUT/CONSIGNMENT）。grade 限 S-D（E 走散裝）。"""

    name: str = Field(min_length=1)
    grade: Grade
    listed_price: NTDAmount
    brand_id: int | None = None
    product_model_id: int | None = None
    acquisition_cost: NTDAmount | None = None
    commission_pct: int | None = Field(default=None, ge=COMMISSION_PCT_MIN, le=COMMISSION_PCT_MAX)

    @field_validator("grade")
    @classmethod
    def _grade_not_e(cls, v: Grade) -> Grade:
        if v == Grade.E:
            raise ValueError("E 級為散裝批，請改用 BULK_LOT 的 lot 欄位")
        return v

    @field_validator("listed_price", "acquisition_cost")
    @classmethod
    def _whole_nonneg(cls, v: Decimal | None) -> Decimal | None:
        return v if v is None else _ensure_whole_nonneg(v, "金額")


class AcquisitionLotIn(BaseModel):
    """E 級散裝批入庫（BULK_LOT）。"""

    name: str = Field(min_length=1)
    acquisition_cost: NTDAmount
    acquisition_basis: BulkAcquisitionBasis
    total_qty: int = Field(gt=0)
    unit_price: NTDAmount
    brand_id: int | None = None
    label: str | None = None

    @field_validator("acquisition_cost", "unit_price")
    @classmethod
    def _whole_nonneg(cls, v: Decimal) -> Decimal:
        return _ensure_whole_nonneg(v, "金額")


class AcquisitionCreate(BaseModel):
    """收購單輸入。BUYOUT/CONSIGNMENT 走 items；BULK_LOT 走 lot。"""

    type: AcquisitionType
    contact_id: int
    note: str | None = None
    items: list[AcquisitionItemIn] | None = None
    lot: AcquisitionLotIn | None = None
    # 撥款方式（SC-2）：BUYOUT/BULK_LOT 適用；SPLIT 須帶現金部分（購物金部分
    # ＝應付總額−現金部分，由後端推導，避免兩數加總不一致）。
    payout_method: PayoutMethod = PayoutMethod.CASH
    payout_split_cash: NTDAmount | None = None

    @model_validator(mode="after")
    def _check_payout(self) -> Self:
        if self.type == AcquisitionType.CONSIGNMENT:
            if self.payout_method != PayoutMethod.CASH or self.payout_split_cash is not None:
                raise ValueError("CONSIGNMENT 不撥款，不可指定撥款方式/拆分")
            return self
        if self.payout_method == PayoutMethod.SPLIT:
            if self.payout_split_cash is None or self.payout_split_cash <= 0:
                raise ValueError("SPLIT 必須提供大於 0 的現金部分（payout_split_cash）")
        elif self.payout_split_cash is not None:
            raise ValueError("僅 SPLIT 可提供 payout_split_cash")
        return self

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.type == AcquisitionType.BULK_LOT:
            if self.lot is None or self.items:
                raise ValueError("BULK_LOT 必須提供 lot 且不得提供 items")
            return self
        # BUYOUT / CONSIGNMENT
        if not self.items or self.lot is not None:
            raise ValueError(f"{self.type} 必須提供至少一筆 items 且不得提供 lot")
        for item in self.items:
            if self.type == AcquisitionType.BUYOUT:
                if item.acquisition_cost is None:
                    raise ValueError("BUYOUT 每筆 item 必須提供 acquisition_cost")
                if item.commission_pct is not None:
                    raise ValueError("BUYOUT item 不應提供 commission_pct")
            else:  # CONSIGNMENT
                if item.commission_pct is None:
                    raise ValueError("CONSIGNMENT 每筆 item 必須提供 commission_pct")
        return self


class AcquisitionResult(BaseModel):
    """收購成功結果：回傳可辨識的收購單號與待列印識別碼。"""

    acquisition_id: int
    type: AcquisitionType
    contact_id: int
    total_cash_paid: NTDAmount | None
    payout_method: PayoutMethod
    payout_cash_amount: NTDAmount | None
    payout_credit_cash_equivalent: NTDAmount | None
    item_codes: list[str]
    lot_code: str | None


class AcquisitionRead(BaseModel):
    """收購單查詢輸出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    type: AcquisitionType
    contact_id: int
    clerk_user_id: int
    total_cash_paid: NTDAmount | None
    payout_method: PayoutMethod
    payout_cash_amount: NTDAmount | None
    payout_credit_cash_equivalent: NTDAmount | None
    note: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, acquisition: Acquisition) -> "AcquisitionRead":
        return cls.model_validate(acquisition)
