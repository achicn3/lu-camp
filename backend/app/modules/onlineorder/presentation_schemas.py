"""Validated online menu presentation settings; no product, price or stock data."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.shared.enums import BrewCardArt, BrewCardTheme, BrewDrawEffect, MenuUpsellRole


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
    role: MenuUpsellRole | None = None


class MenuPresentationRead(MenuPresentationUpdateRequest):
    menu_item_id: int


class ExperienceInclude(BaseModel):
    """體驗包含的一項（例：咖啡豆／這支豆子現磨、單杯份量）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=20)
    detail: str | None = Field(default=None, max_length=60)


class MenuExperienceWriteRequest(BaseModel):
    """手沖體驗卡：引用既有品項＋預選選項；價格、成本、庫存一律來自原品項。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    menu_item_id: StrictInt = Field(gt=0)
    option_ids: list[StrictInt] = Field(default_factory=list, max_length=10)
    title: str = Field(min_length=1, max_length=30)
    tag: str | None = Field(default=None, max_length=12)
    origin: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    includes: list[ExperienceInclude] = Field(default_factory=list, max_length=5)
    theme: BrewCardTheme = BrewCardTheme.PEACH
    art: BrewCardArt = BrewCardArt.NONE
    effect: BrewDrawEffect = BrewDrawEffect.RANDOM
    is_active: StrictBool = True
    sort_order: int = Field(default=0, ge=0, le=9999, strict=True)


class MenuExperienceRead(MenuExperienceWriteRequest):
    id: int
