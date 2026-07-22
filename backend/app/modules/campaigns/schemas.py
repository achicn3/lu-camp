"""campaigns API schema（docs/21）。折扣 1-99；金額不在此（活動只存折扣率）。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.core.time import AwareDateTime
from app.shared.enums import CampaignStatus


class CampaignCreateRequest(BaseModel):
    """建立活動（DRAFT）。寄售折扣預設關；品項預設自有序號+自有散裝開（docs/21 §8）。

    寄售品若開折扣（applies_consignment），一律按比例分攤——寄售人按折後價分潤（docs/21 §8.1）。
    """

    name: Annotated[str, Field(min_length=1, max_length=100)]
    discount_pct: Annotated[int, Field(ge=1, le=99)]
    starts_at: AwareDateTime
    ends_at: AwareDateTime
    applies_owned_serialized: bool = True
    applies_owned_bulk: bool = True
    applies_catalog: bool = False
    applies_consignment: bool = False


class CampaignRead(BaseModel):
    id: int
    store_id: int
    name: str
    discount_pct: int
    applies_owned_serialized: bool
    applies_owned_bulk: bool
    applies_catalog: bool
    applies_consignment: bool
    starts_at: datetime
    ends_at: datetime
    status: CampaignStatus
    created_by: int
    created_at: datetime
    updated_at: datetime
