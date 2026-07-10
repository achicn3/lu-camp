"""settings 的 Pydantic schema：讀取與 PATCH 更新。

比率/百分數於邊界以 Field 約束驗證（§9）：tax_rate 0≤rate<1、commission 0–100、margin 0–99。
tax_rate 以字串傳輸（§11，避免浮點誤差）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.modules.settings.models import PremiumRateHistory, StoreSettings

RateOut = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]
RateOutOpt = Annotated[
    Decimal | None, PlainSerializer(lambda d: None if d is None else str(d), return_type=str | None)
]
NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]
# 溢價率政策硬界線：與 SC-1 帳本 DB 經濟守衛（premium_rate_applied ∈ [0, 0.20]）一致。
_RATE_HARD_MAX = Decimal("0.2000")


class SettingsRead(BaseModel):
    """單店設定輸出。"""

    model_config = ConfigDict(from_attributes=True)

    store_id: int
    einvoice_enabled: bool
    tax_rate: RateOut
    default_commission_pct: int
    default_margin_pct: int
    allow_clerk_manage_categories: bool
    require_acquisition_affidavit: bool
    require_store_credit_signing: bool
    premium_rate: RateOut
    premium_rate_min: RateOut
    premium_rate_max: RateOut
    monthly_fixed_cash_outflow: NTDAmount
    store_credit_min_spend: NTDAmount
    store_credit_engine_params: dict[str, Any]

    @classmethod
    def from_model(cls, settings: StoreSettings) -> "SettingsRead":
        return cls.model_validate(settings)


class SettingsUpdateRequest(BaseModel):
    """PATCH 設定：所有欄位可選，僅更新有帶入者（exclude_unset）。

    溢價率相關夾在政策硬界線 [0, 20%]（與帳本 DB 守衛一致）；min≤max 與 premium∈[min,max]
    的動態關係由 service 驗證（界線可被同一 PATCH 一併更動）。
    """

    einvoice_enabled: bool | None = None
    tax_rate: Annotated[Decimal, Field(ge=0, lt=1)] | None = None
    default_commission_pct: Annotated[int, Field(ge=0, le=100)] | None = None
    default_margin_pct: Annotated[int, Field(ge=0, le=99)] | None = None
    allow_clerk_manage_categories: bool | None = None
    require_acquisition_affidavit: bool | None = None
    require_store_credit_signing: bool | None = None
    premium_rate: Annotated[Decimal, Field(ge=0, le=_RATE_HARD_MAX)] | None = None
    premium_rate_min: Annotated[Decimal, Field(ge=0, le=_RATE_HARD_MAX)] | None = None
    premium_rate_max: Annotated[Decimal, Field(ge=0, le=_RATE_HARD_MAX)] | None = None
    # 上界對齊 DB Numeric(12,0)（最多 12 位數），避免溢位 500（Codex SC-5a P2）。
    monthly_fixed_cash_outflow: (
        Annotated[Decimal, Field(ge=0, le=Decimal("999999999999"))] | None
    ) = None
    # 購物金低消門檻（整數元；0＝不限制）。上界對齊 DB Numeric(12,0)。
    store_credit_min_spend: Annotated[Decimal, Field(ge=0, le=Decimal("999999999999"))] | None = (
        None
    )
    store_credit_engine_params: dict[str, Any] | None = None
    # 溢價率變更事由（選填；寫入 premium_rate_history 留痕）。
    premium_change_reason: Annotated[str, Field(max_length=200)] | None = None

    @field_validator("monthly_fixed_cash_outflow", "store_credit_min_spend")
    @classmethod
    def _whole_ntd(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value != value.to_integral_value():
            raise ValueError("金額必須為整數元")
        return value

    @field_validator("premium_rate", "premium_rate_min", "premium_rate_max")
    @classmethod
    def _rate_scale(cls, value: Decimal | None) -> Decimal | None:
        # DB 為 Numeric(5,4)：限四位小數，避免 API/留痕記 5dp 而 DB 落 4dp 不一致（Codex P2）。
        if value is not None and value != value.quantize(Decimal("0.0001")):
            raise ValueError("溢價率最多四位小數")
        return value


class PremiumRateHistoryRead(BaseModel):
    """溢價率變更留痕輸出（docs/16 §1.3）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    changed_by: int
    changed_at: datetime
    old_rate: RateOut
    new_rate: RateOut
    suggested_rate_at_change: RateOutOpt
    reason: str | None

    @classmethod
    def from_model(cls, row: PremiumRateHistory) -> "PremiumRateHistoryRead":
        return cls.model_validate(row)
