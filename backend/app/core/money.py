"""金額工具：新台幣整數元（無角分），一律用 Decimal、ROUND_HALF_UP。

本檔提供 round_ntd、定價輔助 suggested_price、發票稅額拆分 split_tax_inclusive
與寄售抽成 commission。
"""

from decimal import ROUND_HALF_UP, Decimal

from app.shared.exceptions import (
    InvalidCommissionPct,
    InvalidDiscountPct,
    InvalidMargin,
    InvalidTaxRate,
)

MARGIN_MIN = 0
MARGIN_MAX = 99
COMMISSION_PCT_MIN = 0
COMMISSION_PCT_MAX = 100
DISCOUNT_PCT_MIN = 1
DISCOUNT_PCT_MAX = 99
# PostgreSQL Numeric(12,0) 可精確保存的最大新台幣整數元。
MAX_NTD = Decimal("999999999999")


def ensure_ntd_fits_numeric_12(
    value: Decimal,
    *,
    field: str = "金額",
    absolute: bool = False,
) -> Decimal:
    """確認金額可精確寫入 PostgreSQL Numeric(12,0)，保留原 Decimal。"""
    if abs(value) > MAX_NTD:
        qualifier = "絕對值" if absolute else ""
        raise ValueError(f"{field}{qualifier}不可超過 {MAX_NTD}")
    return value


def format_ntd(value: Decimal | int) -> str:
    r"""金額對外輸出的唯一格式：純十進位，永不帶科學記號。

    **為什麼不能用 `str(value)`**：PostgreSQL 的 numeric 經 asyncpg 讀回來時，帶尾隨零
    的值會是 `Decimal('3E+4')` 而不是 `Decimal('30000')`，`str()` 就原樣輸出 `'3E+4'`。
    前端一律以 `^-?\d+$` 解析金額字串，帶 E 即判為無效 → 畫面顯示 `3E+4`、
    **條碼標籤印成 NT$0**（實測：三萬元的商品貼出 0 元標籤）。

    所有模組的 `NTDAmount` 序列化器都必須用這支，不要各自寫 `str(d)`——
    報表模組先前已單獨修過同一個問題，但沒有推廣，於是其餘 12 個模組繼續踩。
    """
    # 也收 int：`round_ntd()` 回的是 int，呼叫端不該為了型別再包一次 Decimal
    # （包漏一處就又是一個 str() 的科學記號缺口）。int 本來就不會有指數形式。
    return format(Decimal(value), "f")


def format_rate(value: Decimal) -> str:
    """比率/倍數對外輸出：定點、不帶科學記號。

    與 `format_ntd` 分開是刻意的——兩者目前輸出相同，但語意不同：金額是整數元，
    比率有小數。混用會讓「金額格式化器」的守衛測試誤收費率欄位，也讓日後任何依
    金額語意調整 `format_ntd`（例如收整到元）的人，順手把稅率變成 0。
    """
    return format(value, "f")


def round_ntd(value: Decimal) -> int:
    """四捨五入（ROUND_HALF_UP）到整數元。"""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def discounted_price(unit_price: Decimal, discount_pct: int) -> int:
    """折後含稅單價 = round_ntd(unit_price × (100 − discount_pct) / 100)（docs/21 門市活動）。

    discount_pct 為整數百分數，限 1–99（0 無意義、100＝免費不允許）。
    unit_price ≥ 0 時保證 0 ≤ 折後 ≤ 原價。每行折讓由呼叫端以 (原 − 折後) × qty 計。
    """
    if not DISCOUNT_PCT_MIN <= discount_pct <= DISCOUNT_PCT_MAX:
        raise InvalidDiscountPct(
            f"discount_pct 須介於 {DISCOUNT_PCT_MIN}-{DISCOUNT_PCT_MAX}，收到 {discount_pct}"
        )
    return round_ntd(unit_price * Decimal(100 - discount_pct) / Decimal(100))


def suggested_price(
    acquisition_cost: Decimal,
    margin_pct: int,
    tax_rate: Decimal,
    fee_rate: Decimal = Decimal(0),
) -> int:
    """建議**含稅**上架售價（§7.9；手續費部分為 2026-09-09 裁示）。

        未稅目標 = 收購價 ÷ (1 − margin_pct/100)
        含稅售價 = round_ntd(未稅目標 × (1 + tax_rate) ÷ (1 − fee_rate × (1 + tax_rate)))

    目標毛利對**未稅**售價談：標價含稅（§6），但那 5% 是代政府收的、不是店家毛利。

    `fee_rate` 是行動支付手續費（兩種支付取較高者），店家被金流商抽走、不向客人加收。
    不補進標價的話目標毛利就達不到：收 1000、毛利 45%、稅 5% 的舊式建議價是 1909，
    客人刷行動支付被抽 42 元後未稅實得只剩 1776，實際毛利 43.7%。

    採**精確補償**而非把費率直接乘上去：要的是「扣掉手續費之後的未稅實得」正好等於
    未稅目標，所以除以 (1 − fee×(1+tax)) 而不是乘以 (1+fee)（後者會補償不足）。
    費率 0 時整條式子退化為舊式，向後相容。

    **手續費一律計入標價**（裁示 2026-09-09）：付現金的客人也適用同一個標價，
    一件商品一個價，標籤／POS／客顯才會一致。

    margin_pct 為整數百分數，限 0-99；>=100 或 <0 會除以零/負值，視為錯誤。
    tax_rate、fee_rate 為小數（如 0.05／0.022），取自 settings、不得寫死。
    fee_rate × (1 + tax_rate) ≥ 1 會讓分母 ≤ 0（手續費把整個售價吃光），視為錯誤。
    """
    if not MARGIN_MIN <= margin_pct <= MARGIN_MAX:
        raise InvalidMargin(f"margin_pct 須介於 {MARGIN_MIN}-{MARGIN_MAX}，收到 {margin_pct}")
    if not Decimal(0) <= tax_rate < Decimal(1):
        raise InvalidTaxRate(f"稅率須介於 0（含）至 1（不含），收到 {tax_rate}")
    if fee_rate < Decimal(0):
        raise InvalidTaxRate(f"手續費率不得為負，收到 {fee_rate}")
    taxed = Decimal(1) + tax_rate
    fee_divisor = Decimal(1) - fee_rate * taxed
    if fee_divisor <= Decimal(0):
        raise InvalidTaxRate(f"手續費率過高（費率×(1+稅率) ≥ 1），收到 {fee_rate}")
    # **先乘後除**：先除會讓 Decimal 在 28 位有效位數處截斷，之後再乘就補不回來。
    # 例 cost 282／margin 1／稅率 7.25%：先除後乘得 305，精確值是 305.5 → 應為 306。
    # 5% 下看不出來（實測零誤差），非 5% 的稅率才會現形——而稅率是可設定的。
    return round_ntd(
        acquisition_cost * Decimal(100) * taxed / (Decimal(100 - margin_pct) * fee_divisor)
    )


def split_tax_inclusive(total: Decimal, rate: Decimal) -> tuple[int, int]:
    """將含稅總額拆為（未稅 net, 稅額 tax），保證 net + tax = total（整數元、不差一元）。

    稅於發票總額層級推算一次（不逐項算稅，見 CLAUDE.md §6）：
    `net = round_ntd(total / (1 + rate))`、`tax = total − net`。
    rate 為小數稅率（如 0.05），限 0 ≤ rate < 1。
    """
    if not Decimal(0) <= rate < Decimal(1):
        raise InvalidTaxRate(f"稅率須介於 0（含）至 1（不含），收到 {rate}")
    total_ntd = round_ntd(total)
    net = round_ntd(total / (Decimal(1) + rate))
    tax = total_ntd - net
    return net, tax


def commission(gross: Decimal, pct: int) -> int:
    """寄售抽成金額 = round_ntd(售價 × pct / 100)（§7.2）。

    pct 為整數百分數，限 0–100；超出視為錯誤（避免負抽成或 >全額）。
    應付寄售人 = gross − commission(gross, pct)，由呼叫端相減。
    """
    if not COMMISSION_PCT_MIN <= pct <= COMMISSION_PCT_MAX:
        raise InvalidCommissionPct(
            f"commission_pct 須介於 {COMMISSION_PCT_MIN}-{COMMISSION_PCT_MAX}，收到 {pct}"
        )
    return round_ntd(gross * Decimal(pct) / Decimal(100))
