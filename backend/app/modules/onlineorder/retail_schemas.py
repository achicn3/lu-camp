"""線上「帶回家」零售商品（docs/63 §13、M1d）的輸入輸出：引用既有一般商品，不另存價格或庫存。"""

from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictInt,
    field_validator,
)

from app.core.money import format_ntd
from app.shared.enums import MenuUpsellRole

NTDOut = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]
_RETAIL_ROLES = (MenuUpsellRole.BEAN, MenuUpsellRole.DRIP)


class RetailListingWriteRequest(BaseModel):
    """整筆替換；價格、成本、庫存、分類一律來自原商品。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    catalog_product_id: StrictInt = Field(gt=0)
    description: str | None = Field(default=None, max_length=300)
    role: MenuUpsellRole | None = None
    is_active: StrictBool = True
    sort_order: int = Field(default=0, ge=0, le=9999, strict=True)

    @field_validator("role")
    @classmethod
    def _retail_role(cls, value: MenuUpsellRole | None) -> MenuUpsellRole | None:
        """帶回家商品只會被當成咖啡豆或濾掛推薦。"""
        if value is not None and value not in _RETAIL_ROLES:
            raise ValueError("帶回家商品的加購角色只能是咖啡豆或濾掛")
        return value


class RetailListingRead(RetailListingWriteRequest):
    id: int
    photo_sha256: str | None
    product_name: str
    unit_price: NTDOut
    """含稅售價（原商品）。"""
    quantity_on_hand: int
    product_active: bool
    category_name: str | None
