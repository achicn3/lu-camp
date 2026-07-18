"""每店設定的預設值（docs/01 P）。

集中於此，供 model server_default、service 建列、以及 GET 未建列時的有效值共用，
避免預設散落各處而漂移。稅率/抽成/毛利率為 §6/§7 的設定化參數，不得寫死於業務邏輯。
"""

from decimal import Decimal

DEFAULT_EINVOICE_ENABLED = False
DEFAULT_TAX_RATE = Decimal("0.05")  # 營業稅率 5%
DEFAULT_COMMISSION_PCT = 50  # 寄售抽成預設（整數百分數，§7.2）
DEFAULT_MARGIN_PCT = 45  # 定價輔助目標毛利率（整數百分數，§7.9）
DEFAULT_ALLOW_CLERK_MANAGE_CATEGORIES = False  # 分類維護預設限 MANAGER（docs/13 §2）。
DEFAULT_REQUIRE_ACQUISITION_AFFIDAVIT = False  # 收購須手持切結預設關（docs/23 K4；店家就緒後開）。
DEFAULT_REQUIRE_STORE_CREDIT_SIGNING = False  # 購物金扣抵須手持簽名預設關（docs/23 K5）。
DEFAULT_PREMIUM_RATE = Decimal("0.1000")  # 購物金溢價率（docs/16 §1.5 起手 +10%；4dp 與 DB 一致）
DEFAULT_PREMIUM_RATE_MIN = Decimal("0.0000")  # 溢價率下限（docs/16 §6.1，預設 0%）
DEFAULT_PREMIUM_RATE_MAX = Decimal("0.2000")  # 溢價率上限（docs/16 §6.1，預設 20%）
DEFAULT_MONTHLY_FIXED_CASH_OUTFLOW = 0  # 月固定現金支出（整數元；負債健康比分母，手動維護）
# 購物金低消門檻（整數元）：非餐飲消費（total − 餐飲）未達此值則不可用購物金折抵。
# 預設 0＝不限制，僅作為彈性設定（內用餐飲一律不看，與 store_credit_max 口徑一致）。
DEFAULT_STORE_CREDIT_MIN_SPEND = 0
# 建議值引擎可調參數（docs/16 §1.5/§6；SC-5b 引擎使用，本期先落地預設供未來讀取）。
DEFAULT_STORE_CREDIT_ENGINE_PARAMS: dict[str, object] = {
    "window_weights": {"yesterday": 0.05, "d7": 0.25, "d30": 0.40, "d90": 0.20, "yoy": 0.10},
    "alpha_safety": 0.8,
    "liability_ladder": [1.5, 2.0, 2.5],
    "take_rate_band": [0.30, 0.70],
    "take_rate_step": 0.025,
    "beta_n_days": 180,
    "alpha_proxy_window_days": 90,
    "cold_start_min_days": 30,
    "yoy_halfwidth_days": 15,
}

# 備份系統（docs/31）：啟用、間隔（時）、保留份數、離峰時點（0–23）。
DEFAULT_BACKUP_ENABLED = True
DEFAULT_BACKUP_INTERVAL_HOURS = 24
DEFAULT_BACKUP_RETENTION = 30
DEFAULT_BACKUP_OFFPEAK_HOUR = 21  # 晚上 9 點（打烊後離峰）；過此鐘點才算到期，機器開著就晚上跑
