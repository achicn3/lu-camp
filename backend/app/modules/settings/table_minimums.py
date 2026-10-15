"""Per-table online-order minimums; an absent rule means unrestricted."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.core.money import format_ntd


class TableAmountMinimum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["amount"]
    amount: Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)] = Field(
        gt=0, le=9999999, max_digits=7, decimal_places=0
    )


class TablePersonMinimum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["per_person_item"]


TableMinimum = Annotated[TableAmountMinimum | TablePersonMinimum, Field(discriminator="mode")]
