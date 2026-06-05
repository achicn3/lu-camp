"""每店設定的預設值（docs/01 P）。

集中於此，供 model server_default、service 建列、以及 GET 未建列時的有效值共用，
避免預設散落各處而漂移。稅率/抽成/毛利率為 §6/§7 的設定化參數，不得寫死於業務邏輯。
"""

from decimal import Decimal

DEFAULT_EINVOICE_ENABLED = False
DEFAULT_TAX_RATE = Decimal("0.05")  # 營業稅率 5%
DEFAULT_COMMISSION_PCT = 50  # 寄售抽成預設（整數百分數，§7.2）
DEFAULT_MARGIN_PCT = 45  # 定價輔助目標毛利率（整數百分數，§7.9）
