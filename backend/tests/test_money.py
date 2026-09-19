"""core/money.py — NT$ 整數元四捨五入與定價輔助。"""

import inspect
from decimal import Decimal

import pytest

from app.core.money import (
    commission,
    consignment_breakdown,
    consignment_split,
    discounted_price,
    round_ntd,
    round_up_to_listed_step,
    split_tax_inclusive,
    suggested_listed_price,
    suggested_price,
)
from app.shared.exceptions import (
    InvalidCommissionPct,
    InvalidDiscountPct,
    InvalidMargin,
    InvalidTaxRate,
)

# 營業稅率：測試用的固定值（正式環境一律取自 settings，§6 不得寫死）。
RATE = Decimal("0.05")


def test_round_ntd_half_up() -> None:
    assert round_ntd(Decimal("100.5")) == 101
    assert round_ntd(Decimal("100.4")) == 100
    assert round_ntd(Decimal("0.5")) == 1
    assert round_ntd(Decimal("2.5")) == 3


def test_suggested_price_margin_zero_equals_cost() -> None:
    assert suggested_price(Decimal("1000"), 0, RATE) == 1050


def test_suggested_price_margin_99() -> None:
    # 1000 / (1 - 0.99) = 1000 / 0.01 = 100000
    assert suggested_price(Decimal("1000"), 99, RATE) == 105000


def test_suggested_price_typical_rounds_to_integer_ntd() -> None:
    # 未稅 600/0.55 = 1090.909…；×1.05 = 1145.4545… → ROUND_HALF_UP → 1145
    assert suggested_price(Decimal("600"), 45, RATE) == 1145


def test_suggested_price_precision_at_non_five_percent_rates() -> None:
    """非 5% 稅率不得因中途截斷而少一元。

    先除後乘會讓 Decimal 在 28 位有效位數處截斷：cost 282／margin 1／稅率 7.25%
    得 305，但精確值是 305.5 → 應為 306。5% 下看不出來，而稅率是可設定的。
    """
    assert suggested_price(Decimal("282"), 1, Decimal("0.0725")) == 306


def test_suggested_price_requires_explicit_tax_rate() -> None:
    """`tax_rate` 必須是必填、**沒有預設值**（ADR-016 決策 3）。

    給預設值等於把 5% 藏進程式碼（違反 §6「稅率不得寫死」），而且設定讀取失敗時
    會靜默用錯的稅率算價，比明確報錯更危險。
    """
    sig = inspect.signature(suggested_price)
    assert sig.parameters["tax_rate"].default is inspect.Parameter.empty


def test_suggested_price_tax_rate_zero_falls_back_to_legacy_formula() -> None:
    """稅率 0 時必須回到 2026-08-23 前的舊式。

    這條的用處是：日後有人把 tax_rate 誤傳成 0，測試會證明那是「沒加稅」，
    而不是新公式壞了。
    """
    assert suggested_price(Decimal("600"), 45, Decimal(0)) == 1091
    assert suggested_price(Decimal("1000"), 45, Decimal(0)) == 1818


def test_suggested_price_rounds_once_not_twice() -> None:
    """釘住「只四捨五入一次」——挑的必須是兩種實作會分家的輸入，否則這條測試沒有鑑別力。

    600/45%：未稅 1090.909…
      單次取整：1090.909… × 1.05 = 1145.4545… → **1145**
      兩段式  ：先取整未稅 1091 → × 1.05 = 1145.55 → **1146**
    """
    assert suggested_price(Decimal("600"), 45, RATE) == 1145
    # 對照組：1000/45% 在兩種實作下同為 1909，單看它分不出來，故不能只靠這一條。
    assert suggested_price(Decimal("1000"), 45, RATE) == 1909


@pytest.mark.parametrize("rate", [Decimal("-0.01"), Decimal("1"), Decimal("1.5")])
def test_suggested_price_invalid_tax_rate_raises(rate: Decimal) -> None:
    with pytest.raises(InvalidTaxRate):
        suggested_price(Decimal("1000"), 45, rate)


@pytest.mark.parametrize("margin", [100, 150, -1])
def test_suggested_price_invalid_margin_raises(margin: int) -> None:
    with pytest.raises(InvalidMargin):
        suggested_price(Decimal("1000"), margin, RATE)


def test_split_tax_inclusive_exact() -> None:
    # 105 含稅、稅率 5% → net 100、tax 5
    net, tax = split_tax_inclusive(Decimal("105"), Decimal("0.05"))
    assert (net, tax) == (100, 5)


def test_split_tax_inclusive_invariant_net_plus_tax_equals_total() -> None:
    # 100 / 1.05 = 95.238… → net 95、tax = 100 - 95 = 5（保證不差一元）
    net, tax = split_tax_inclusive(Decimal("100"), Decimal("0.05"))
    assert net == 95
    assert tax == 5
    assert net + tax == 100


@pytest.mark.parametrize("total", [Decimal("0"), Decimal("1"), Decimal("33"), Decimal("99999")])
def test_split_tax_inclusive_always_sums_to_total(total: Decimal) -> None:
    net, tax = split_tax_inclusive(total, Decimal("0.05"))
    assert net + tax == int(total)
    assert net >= 0
    assert tax >= 0


def test_split_tax_inclusive_zero_rate_no_tax() -> None:
    net, tax = split_tax_inclusive(Decimal("100"), Decimal("0"))
    assert (net, tax) == (100, 0)


def test_split_tax_inclusive_rounds_total_before_splitting() -> None:
    # 含稅總額先 round_ntd 到整數元（100.6 → 101），稅再由整數總額推算：
    # net = round_ntd(100.6 / 1.05) = round_ntd(95.81) = 96、tax = 101 - 96 = 5
    net, tax = split_tax_inclusive(Decimal("100.6"), Decimal("0.05"))
    assert net == 96
    assert tax == 5
    assert net + tax == 101


@pytest.mark.parametrize("rate", [Decimal("-0.01"), Decimal("1"), Decimal("1.5")])
def test_split_tax_inclusive_invalid_rate_raises(rate: Decimal) -> None:
    with pytest.raises(InvalidTaxRate):
        split_tax_inclusive(Decimal("100"), rate)


def test_commission_default_50() -> None:
    # 售價 3000、抽成 50% → 1500；應付寄售人 = 3000 - 1500 = 1500
    assert commission(Decimal("3000"), 50) == 1500


def test_commission_rounds_half_up() -> None:
    # 999 × 50 / 100 = 499.5 → ROUND_HALF_UP → 500
    assert commission(Decimal("999"), 50) == 500


@pytest.mark.parametrize(
    ("gross", "pct", "expected"),
    [(Decimal("1000"), 0, 0), (Decimal("1000"), 100, 1000), (Decimal("1234"), 30, 370)],
)
def test_commission_bounds(gross: Decimal, pct: int, expected: int) -> None:
    assert commission(gross, pct) == expected


@pytest.mark.parametrize("pct", [-1, 101, 150])
def test_commission_invalid_pct_raises(pct: int) -> None:
    with pytest.raises(InvalidCommissionPct):
        commission(Decimal("1000"), pct)


def test_discounted_price_nine_tenths() -> None:
    # 九折（10% off）：1000 × 90% = 900
    assert discounted_price(Decimal("1000"), 10) == 900


def test_discounted_price_rounds_half_up() -> None:
    # 999 × 95% = 949.05 → 949；333 × 85% = 283.05 → 283
    assert discounted_price(Decimal("999"), 5) == 949
    # 1 × 50% = 0.5 → ROUND_HALF_UP → 1（折後不為 0）
    assert discounted_price(Decimal("1"), 50) == 1


@pytest.mark.parametrize(
    ("price", "pct", "expected"),
    [
        (Decimal("1000"), 1, 990),
        (Decimal("1000"), 99, 10),
        (Decimal("0"), 50, 0),
        (Decimal("250"), 20, 200),
    ],
)
def test_discounted_price_bounds(price: Decimal, pct: int, expected: int) -> None:
    result = discounted_price(price, pct)
    assert result == expected
    assert 0 <= result <= price  # 折後介於 0 與原價之間


@pytest.mark.parametrize("pct", [0, 100, -1, 150])
def test_discounted_price_invalid_pct_raises(pct: int) -> None:
    with pytest.raises(InvalidDiscountPct):
        discounted_price(Decimal("1000"), pct)


# ── 行動支付手續費納入建議售價（裁示 2026-09-09）────────────────────────
#
# 手續費是店家被金流商抽走的錢，不加進標價的話目標毛利就達不到。採「精確補償」：
# 讓**扣掉手續費之後的未稅實得**正好等於 cost ÷ (1 − margin)，而不是把費率直接乘上去。
FEE = Decimal("0.022")  # 兩種行動支付取較高者；測試用固定值


def test_suggested_price_without_fee_is_unchanged() -> None:
    """不帶手續費時與舊行為完全相同——既有呼叫端不受影響。"""
    assert suggested_price(Decimal("1000"), 45, RATE) == suggested_price(
        Decimal("1000"), 45, RATE, Decimal(0)
    )


def test_suggested_price_with_fee_compensates_exactly() -> None:
    """收 1000、毛利 45%、稅 5%、手續費 2.2% → 1954。

    驗算：未稅目標 1000/0.55 = 1818.18；含稅 P 需滿足
    P/1.05 − P×0.022 = 1818.18 → P = 1954.14 → 1954。
    """
    assert suggested_price(Decimal("1000"), 45, RATE, FEE) == 1954


def test_suggested_price_with_fee_really_hits_the_target_margin() -> None:
    """反推驗證：照這個價賣掉、扣掉手續費之後，未稅實得要回到目標毛利。

    只比對區間而非精確值——整數元收整必然有一元上下的誤差，重點是不能系統性少賺。
    """
    cost, margin = Decimal("1000"), 45
    price = Decimal(suggested_price(cost, margin, RATE, FEE))
    net_after_fee = price / (Decimal(1) + RATE) - price * FEE
    realised_margin = (net_after_fee - cost) / net_after_fee * 100
    assert Decimal("44.9") < realised_margin < Decimal("45.1")


def test_suggested_price_zero_fee_matches_old_formula() -> None:
    """費率 0 退化為舊式，向後相容。"""
    assert suggested_price(Decimal("1000"), 0, RATE, Decimal(0)) == 1050


def test_suggested_price_rejects_fee_that_swallows_the_whole_price() -> None:
    """手續費×(1+稅率) ≥ 1 會讓分母 ≤ 0：那是無意義的設定，明確拒絕而不是回怪數字。"""
    with pytest.raises(InvalidTaxRate):
        suggested_price(Decimal("1000"), 45, RATE, Decimal("0.96"))


def test_suggested_price_rejects_negative_fee() -> None:
    with pytest.raises(InvalidTaxRate):
        suggested_price(Decimal("1000"), 45, RATE, Decimal("-0.01"))


# ── 寄售分帳：寄售人依「未稅」售價拿份額（裁示 2026-09-11）─────────────
#
# 發票是店家對全額開的，營業稅全由店家繳。舊規則以含稅售價算抽成，寄售人拿含稅價的
# 一半，等於店家替寄售人吸收了他那份稅：1050 的寄售品寄售人拿 525、店家繳完 50 元稅
# 只剩 475。新規則讓雙方對半分的是「未稅」售價 1000：寄售人 500、店家 500（另代收 50 稅）。


def test_consignment_split_pays_consignor_on_tax_exclusive_price() -> None:
    commission_amount, payout = consignment_split(Decimal("1050"), 50, RATE)
    assert payout == 500  # 寄售人拿未稅 1000 的一半
    # 抽成欄記店家留下的含稅部分（未稅抽成 500 ＋ 代收稅 50），
    # DB 約束 commission_amount + payout_amount = gross 才能成立。
    assert commission_amount == 550


def test_consignment_split_always_balances_to_gross() -> None:
    """DB 有 CHECK commission_amount + payout_amount = gross：任何輸入都不能破這條。"""
    for gross in (1, 7, 99, 100, 101, 105, 1050, 2111, 45678):
        for pct in (0, 1, 30, 37, 50, 99, 100):
            commission_amount, payout = consignment_split(Decimal(gross), pct, RATE)
            assert commission_amount + payout == gross, (gross, pct)
            assert commission_amount >= 0 and payout >= 0, (gross, pct)


def test_consignment_split_store_keeps_its_share_plus_the_whole_tax() -> None:
    """店家留下的 = 未稅抽成 ＋ 整筆營業稅；寄售人一毛稅都不負擔。"""
    gross = Decimal("2111")
    net, tax = split_tax_inclusive(gross, RATE)
    commission_amount, payout = consignment_split(gross, 37, RATE)
    assert commission_amount - tax == commission(Decimal(net), 37)  # 店家的未稅抽成
    assert payout == net - commission(Decimal(net), 37)  # 寄售人拿未稅的剩餘份額


def test_consignment_split_rounds_the_store_share_like_before() -> None:
    """沿用舊規則的捨入慣例：四捨五入的是店家抽成，寄售人拿剩下的。

    未稅 101、抽 50%：店家 round(50.5)=51，寄售人 50。
    """
    commission_amount, payout = consignment_split(Decimal("106"), 50, RATE)  # 106/1.05 → 101
    assert payout == 50
    assert commission_amount == 56  # 51 ＋ 稅 5


def test_consignment_split_zero_tax_matches_old_rule() -> None:
    """稅率 0 時退化為舊規則（抽成以售價計），向後相容。"""
    commission_amount, payout = consignment_split(Decimal("1050"), 50, Decimal(0))
    assert (commission_amount, payout) == (525, 525)


def test_consignment_split_rejects_bad_inputs() -> None:
    with pytest.raises(InvalidCommissionPct):
        consignment_split(Decimal("1050"), 101, RATE)
    with pytest.raises(InvalidTaxRate):
        consignment_split(Decimal("1050"), 50, Decimal("1"))


def test_consignment_breakdown_explains_the_split_to_the_consignor() -> None:
    """寄售人看得懂的拆法：含稅 1050 → 未稅 1000、稅 50、店家未稅抽成 500、自己 500。

    三個數字都是從「已存下來的」gross 與 commission_amount 推回去的，不重算分潤——
    畫面與帳上的金額永遠一致（ADR-021）。
    """
    net, tax, commission_net = consignment_breakdown(Decimal("1050"), Decimal("550"), RATE)
    assert (net, tax, commission_net) == (1000, 50, 500)


def test_consignment_breakdown_matches_the_stored_amounts_for_any_input() -> None:
    for gross in (1, 7, 99, 105, 106, 1050, 1800, 2111):
        for pct in (0, 5, 37, 40, 50, 99, 100):
            store_share, payout = consignment_split(Decimal(gross), pct, RATE)
            net, tax, commission_net = consignment_breakdown(
                Decimal(gross), Decimal(store_share), RATE
            )
            assert net + tax == gross, (gross, pct)
            assert commission_net + payout == net, (gross, pct)  # 未稅的兩份加起來＝未稅售價
            assert commission_net + tax == store_share, (gross, pct)


def test_consignment_breakdown_with_zero_tax_has_no_tax_line() -> None:
    net, tax, commission_net = consignment_breakdown(Decimal("1000"), Decimal("500"), Decimal(0))
    assert (net, tax, commission_net) == (1000, 0, 500)


def test_consignment_breakdown_never_shows_a_negative_share_for_legacy_rows() -> None:
    """改制前的舊結算（以含稅價抽成、抽成又低）扣掉稅會變負——畫面不能出現「未稅 −50」。

    ADR-021 §4 明說舊列不回溯改寫，所以這種資料會一直存在；夾在 0，
    抽成與應付仍是當初存下來的金額。
    """
    net, tax, commission_net = consignment_breakdown(Decimal("1050"), Decimal("0"), RATE)
    assert (net, tax) == (1000, 50)
    assert commission_net == 0


# ── 上架售價一律 10 的倍數、無條件進位（2026-09-19 裁示）────────────────────


def test_suggested_listed_price_rounds_up_to_ten() -> None:
    """要填進畫面的建議價必須是 10 的倍數，且只進不退。

    店主要架上價格一律 0 結尾。進位而非四捨五入：退位會讓實際毛利低於目標，
    而這個數字的用途就是「至少賺到目標毛利」。
    """
    # 未稅 600/0.55 = 1090.909…；×1.05 = 1145.4545… → 1145 → 進位 → 1150
    assert suggested_price(Decimal("600"), 45, RATE) == 1145
    assert suggested_listed_price(Decimal("600"), 45, RATE) == 1150


def test_suggested_listed_price_leaves_exact_multiples_alone() -> None:
    """已經是 10 的倍數就不動——無條件進位不是「一律加 10」。"""
    assert suggested_listed_price(Decimal("1000"), 0, RATE) == 1050


def test_suggested_listed_price_keeps_the_fee_compensation() -> None:
    """帶手續費時也走同一條進位：1954 → 1960，不得因為多包一層而漏掉費率。"""
    assert suggested_listed_price(Decimal("1000"), 45, RATE, FEE) == 1960


def test_suggested_listed_price_never_drops_below_the_exact_price() -> None:
    """進位只進不退——任何輸入都不該比毛利式算出來的價格低。"""
    for cost in (Decimal(1), Decimal(87), Decimal(600), Decimal("12345")):
        exact = suggested_price(cost, 45, RATE, FEE)
        assert suggested_listed_price(cost, 45, RATE, FEE) >= exact


def test_round_up_to_listed_step_rounds_up_even_one_dollar_over() -> None:
    """差一元也要進位（21 → 30 是店主給的例子的同一條規則）。"""
    assert round_up_to_listed_step(21) == 30
    assert round_up_to_listed_step(20) == 20
    assert round_up_to_listed_step(1) == 10


def test_round_up_to_listed_step_rejects_negative() -> None:
    """負價沒有意義；靜默回 0 會讓錯誤的輸入變成「免費商品」。"""
    with pytest.raises(ValueError):
        round_up_to_listed_step(-1)


def test_round_up_to_listed_step_zero_stays_zero() -> None:
    """0 元（例如成本 0 的贈品）不該被抬成 10 元。"""
    assert round_up_to_listed_step(0) == 0
