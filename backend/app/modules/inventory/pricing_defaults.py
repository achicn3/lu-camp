"""分類定價規則的 v1 seed 常數（docs/10 §/acquisition 收購定價輔助）。

集中於此，建分類時各成色帶共用同一組「雙重約束」參數；待營運再細分成色帶（schema 不用重來）。
參數語意（前端 `maxAcquisitionCost` 消費）：
- `discount_ceiling_pct`：收購成本最多離轉售價的折扣（cost ≤ resale×(1−ceiling/100)）。
- `min_margin_pct`：最低毛利（cost ≤ resale×(1−margin/100)）；與 ceiling 取較嚴。
- `min_price_multiple`：轉售價 ÷ 成本的最低倍數（cost ≤ resale ÷ multiple；救低價品）。
"""

from decimal import Decimal

from app.shared.enums import Grade

# 成色帶（E 走散裝，無此規則）。
PRICING_BANDS: tuple[Grade, ...] = (Grade.S, Grade.A, Grade.B, Grade.C, Grade.D)

DEFAULT_DISCOUNT_CEILING_PCT = 60
DEFAULT_MIN_MARGIN_PCT = 40
DEFAULT_MIN_PRICE_MULTIPLE = Decimal("2.0")
