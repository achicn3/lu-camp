"""Validated online menu presentation settings; no product, price or stock data."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class MenuPresentationUpdateRequest(BaseModel):
    """Replace all presentation settings; omitted fields restore conservative defaults."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    flavor_description: str | None = Field(default=None, max_length=120)
    audience_description: str | None = Field(default=None, max_length=120)
    is_recommended: StrictBool = False
    is_new: StrictBool = False
    limited_on: date | None = None
    show_remaining: StrictBool = True
    low_stock_threshold: int = Field(default=5, ge=0, le=9999, strict=True)
    hide_sold_out: StrictBool = False


class MenuPresentationRead(MenuPresentationUpdateRequest):
    menu_item_id: int
