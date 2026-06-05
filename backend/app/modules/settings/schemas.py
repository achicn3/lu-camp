"""settings 的 Pydantic schema：讀取與 PATCH 更新。

比率/百分數於邊界以 Field 約束驗證（§9）：tax_rate 0≤rate<1、commission 0–100、margin 0–99。
tax_rate 以字串傳輸（§11，避免浮點誤差）。
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.modules.settings.models import StoreSettings

RateOut = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class SettingsRead(BaseModel):
    """單店設定輸出。"""

    model_config = ConfigDict(from_attributes=True)

    store_id: int
    einvoice_enabled: bool
    tax_rate: RateOut
    default_commission_pct: int
    default_margin_pct: int

    @classmethod
    def from_model(cls, settings: StoreSettings) -> "SettingsRead":
        return cls.model_validate(settings)


class SettingsUpdateRequest(BaseModel):
    """PATCH 設定：所有欄位可選，僅更新有帶入者（exclude_unset）。"""

    einvoice_enabled: bool | None = None
    tax_rate: Annotated[Decimal, Field(ge=0, lt=1)] | None = None
    default_commission_pct: Annotated[int, Field(ge=0, le=100)] | None = None
    default_margin_pct: Annotated[int, Field(ge=0, le=99)] | None = None
