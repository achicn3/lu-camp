"""returns API schemas。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.modules.returns.models import CustomerReturn
from app.shared.enums import TenderType

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class ReturnLineRequest(BaseModel):
    sale_line_id: int
    qty: int = Field(gt=0)


class ReturnCreateRequest(BaseModel):
    sale_id: int
    reason: str = Field(min_length=1, max_length=500)
    lines: list[ReturnLineRequest] = Field(min_length=1)
    taiwan_pay_refund_confirmed: bool = False
    # 店員已向客人收回紙本發票證明聯（僅累計全退且原發票有紙本時要求；未確認即拒絕退貨）。
    invoice_recalled: bool = False
    # 買受人同意（作業要點第 9 點）：折讓與作廢皆須客人於顧客螢幕簽名，帶已簽任務 id。
    consent_signature_task_id: int | None = None


class ReturnPreviewRequest(BaseModel):
    """退貨預覽：店員勾選品項後、實際送出前，先問後端這次會怎麼處理發票。

    不可放在 sale detail——店員尚未選定退哪些品項時，後端無從得知是否會構成累計全退。
    """

    sale_id: int
    lines: list[ReturnLineRequest] = Field(min_length=1)


class ReturnPreviewRead(BaseModel):
    """預覽結果。**僅供畫面提示**，送出時後端會以當下狀態重新判斷一次（條件可能已變）。"""

    is_full_return: bool
    invoice_action: str
    requires_paper_recall: bool
    requires_customer_consent: bool
    reason: str


class ReturnLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sale_line_id: int
    qty: int
    refund_amount: NTDAmount


class ReturnTenderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_type: TenderType
    amount: NTDAmount


class ReturnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    sale_id: int
    refund_amount: NTDAmount
    reason: str
    clerk_user_id: int
    created_at: datetime
    lines: list[ReturnLineRead]
    refund_tenders: list[ReturnTenderRead]

    @classmethod
    def from_model(cls, customer_return: CustomerReturn) -> "ReturnRead":
        return cls.model_validate(customer_return)
