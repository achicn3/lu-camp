"""臨時折扣的定價與分攤（純函式：無 DB、無 I/O，可完整單元測試）。

**為什麼要獨立成一支純函式模組**：退貨時必須依「當初實際分攤到各行的金額」退款，
不能依當下商品狀態重算。規則一旦散在 service 裡就會漂，部分退貨就開始多退或少退。

## 套用順序（固定，不可調換）

1. 各行金額 = 活動折後金額（`line_total`，由 campaign 決定，本模組不碰）
2. **單品折扣**：逐筆套到指定行
3. **整單折扣**：以「扣掉單品折扣後的餘額」為基礎，依比例分攤到各可折行
4. 應付金額 = Σ 各行實付

以**餘額**（而非原始金額）為分攤基礎是刻意的：否則已被單品折扣打到很低的行，可能分到
超過它剩餘金額的整單折扣，就得夾住再重新分配，分攤結果反而變得無法預測。

## 兩條紅線

- **一般行的實付不得為 0**：折到 0 元＝變相贈品。要免費請用贈品，否則贈品的數量與成本
  在報表上統計不到——這正是需求的第一條原則（贈品不可實作成 100% 折扣）。
- **贈品不參與任何折扣**，其原價價值也絕不混入折扣金額（活動報表直接 SUM 折扣欄位）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.core.money import round_ntd
from app.shared.enums import AdjustmentScope, CalculationMethod, SaleLineKind
from app.shared.exceptions import InvalidDiscount


@dataclass(frozen=True)
class PricingLine:
    """參與定價的一行。`key` 只是本次計算的識別碼（成交前 sale_line 還沒有 id）。"""

    key: str
    kind: SaleLineKind
    line_total: Decimal
    """活動折後金額（贈品恆為 0）。"""
    discountable: bool
    """可否套用臨時折扣。寄售、餐飲、贈品皆為 False（沿用活動折扣的既有排除口徑）。"""
    gift_retail_value: Decimal = Decimal(0)
    """贈品的原價價值（`original_unit_price × qty`）。一般行為 0。"""


@dataclass(frozen=True)
class DiscountRequest:
    """店員輸入的一筆折扣意圖。"""

    scope: AdjustmentScope
    method: CalculationMethod
    value: Decimal
    target_key: str | None = None
    """ITEM 時必填；ORDER 時必須為 None。"""
    reason_id: int | None = None
    reason_name: str | None = None
    note: str | None = None
    """折扣原因與備註：**本模組不使用**，純粹隨請求帶到落盤與稽核。
    放在同一個 DTO 是為了避免 API→定價→落盤之間再多一組幾乎相同的型別。"""


@dataclass(frozen=True)
class AppliedDiscount:
    """一筆折扣的套用結果：實際折了多少、分攤到哪些行。"""

    request: DiscountRequest
    applied_amount: Decimal
    allocations: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class PricingResult:
    applied: tuple[AppliedDiscount, ...]
    manual_discount_by_line: Mapping[str, Decimal]
    net_by_line: Mapping[str, Decimal]
    gross_amount: Decimal
    """一般銷售的牌價小計（活動折後）。**不含贈品**。"""
    item_discount_amount: Decimal
    order_discount_amount: Decimal
    gift_retail_value: Decimal
    net_amount: Decimal
    """應付金額 = Σ 各行實付。"""

    @property
    def total_discount_amount(self) -> Decimal:
        return self.item_discount_amount + self.order_discount_amount


_PERCENT_MAX = Decimal(100)


def _validate_request(request: DiscountRequest) -> None:
    if request.value <= 0:
        raise InvalidDiscount("折扣數值必須大於 0")
    if request.method is CalculationMethod.PERCENTAGE and request.value >= _PERCENT_MAX:
        raise InvalidDiscount("折扣百分比必須小於 100；免費請改用贈品")
    if request.scope is AdjustmentScope.ITEM and request.target_key is None:
        raise InvalidDiscount("單品折扣必須指定要折哪一個商品")
    if request.scope is AdjustmentScope.ORDER and request.target_key is not None:
        raise InvalidDiscount("整單折扣不可指定單一商品")


def _amount_from(method: CalculationMethod, value: Decimal, base: Decimal) -> Decimal:
    """把「使用者輸入值」換算成實際折扣金額（整數元）。"""
    if method is CalculationMethod.FIXED_AMOUNT:
        return Decimal(round_ntd(value))
    return Decimal(round_ntd(base * value / _PERCENT_MAX))


def _guard_remaining(key: str, remaining: Decimal, discount: Decimal) -> None:
    if discount > remaining:
        raise InvalidDiscount(f"折扣金額超過該商品可折金額（剩餘 {remaining} 元）")
    if remaining - discount <= 0:
        raise InvalidDiscount(
            "折扣後金額為 0：免費請改用贈品，這樣才統計得到贈品的數量與成本"
        )
    assert key  # 僅為訊息可讀性保留參數


def apply_discounts(
    lines: Sequence[PricingLine], requests: Sequence[DiscountRequest]
) -> PricingResult:
    """套用臨時折扣並算出每一行的實付金額。

    回傳的 `manual_discount_by_line` 只包含**真的被折到**的行；`net_by_line` 則涵蓋所有行。
    """
    by_key = {line.key: line for line in lines}
    if len(by_key) != len(lines):
        raise InvalidDiscount("明細識別碼重複，無法分攤折扣")

    remaining: dict[str, Decimal] = {line.key: line.line_total for line in lines}
    discount_by_line: dict[str, Decimal] = {}
    applied: list[AppliedDiscount] = []
    item_total = Decimal(0)
    order_total = Decimal(0)

    for request in requests:
        _validate_request(request)
        if request.scope is AdjustmentScope.ITEM:
            key = request.target_key
            assert key is not None  # _validate_request 已保證
            line = by_key.get(key)
            if line is None:
                raise InvalidDiscount(f"要折扣的商品不存在於本次交易（{key}）")
            if not line.discountable:
                raise InvalidDiscount("此商品不可折扣（寄售、餐飲或贈品）")
            amount = _amount_from(request.method, request.value, remaining[key])
            _guard_remaining(key, remaining[key], amount)
            remaining[key] -= amount
            discount_by_line[key] = discount_by_line.get(key, Decimal(0)) + amount
            item_total += amount
            applied.append(AppliedDiscount(request, amount, ((key, amount),)))
            continue

        # ── 整單折扣：以各可折行的**餘額**為基礎，依比例分攤 ──────────────────
        eligible = [line.key for line in lines if line.discountable and remaining[line.key] > 0]
        base = sum((remaining[k] for k in eligible), Decimal(0))
        if not eligible or base <= 0:
            raise InvalidDiscount("本次交易沒有可折扣的商品，無法套用整單折扣")
        amount = _amount_from(request.method, request.value, base)
        if amount > base:
            raise InvalidDiscount(f"折扣金額超過可折扣商品總額（{base} 元）")
        if base - amount <= 0:
            raise InvalidDiscount(
                "折扣後金額為 0：免費請改用贈品，這樣才統計得到贈品的數量與成本"
            )

        allocations: list[tuple[str, Decimal]] = []
        allocated = Decimal(0)
        # 尾差固定落在**最後一筆**可折行：分攤必須可重現，否則退貨時對不上當初的金額。
        for key in eligible[:-1]:
            share = Decimal(round_ntd(amount * remaining[key] / base))
            allocations.append((key, share))
            allocated += share
        allocations.append((eligible[-1], amount - allocated))

        for key, share in allocations:
            _guard_remaining(key, remaining[key], share)
            remaining[key] -= share
            discount_by_line[key] = discount_by_line.get(key, Decimal(0)) + share
        order_total += amount
        applied.append(AppliedDiscount(request, amount, tuple(allocations)))

    return PricingResult(
        applied=tuple(applied),
        manual_discount_by_line=dict(discount_by_line),
        net_by_line=dict(remaining),
        gross_amount=sum(
            (line.line_total for line in lines if line.kind is SaleLineKind.NORMAL), Decimal(0)
        ),
        item_discount_amount=item_total,
        order_discount_amount=order_total,
        gift_retail_value=sum((line.gift_retail_value for line in lines), Decimal(0)),
        net_amount=sum(remaining.values(), Decimal(0)),
    )
