"""Validated cloud payment warnings; these are observations, never payment decisions."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

from app.core.money import format_ntd


class OnlinePaymentAlert(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    table_label: str | None = None
    service_mode: Literal["DINE_IN", "TAKEOUT"]
    total: Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)] = Field(ge=0)
    status: Literal["PENDING", "CONFIRMING"]
    transaction_id: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    started_at: datetime
