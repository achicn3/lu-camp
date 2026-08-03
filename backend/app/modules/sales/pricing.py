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


def _allocate_largest_remainder(
    amount: Decimal, weights: Sequence[tuple[str, Decimal]]
) -> list[tuple[str, Decimal]]:
    """把 `amount` 依權重分攤成整數元：**每筆非負、每行至少留 1 元實付**、總和恰為 `amount`。

    先各自取整數部分（無條件捨去、並夾在該行的可折上限內），再把剩下的元數依
    「小數部分大到小」逐一發放給**還沒到上限**的行；同分時以原順序決定，分攤才可重現
    ——退貨要依當初的分攤金額退款。

    **兩個都踩過的坑**：

    1. 「前 N−1 筆四捨五入、最後一筆吃差額」：各自進位後會超發，最後一筆拿到**負數**分攤，
       把該行實付推到原價之上。實例：51、51、51、47 分攤 2 元 → 1、1、1、−1（末行 47→48）。
    2. 發放尾差時不看每行上限：金額 1、3 分攤 2 元，順序 [1,3] 會把首行折成 0 元而被拒、
       反序卻成功——**同一籃商品能不能結帳竟取決於掃描順序**。故此處明確以
       「每行最多折到剩 1 元」為容量，容量不足才整筆拒絕（訊息指向贈品）。
    """
    base = sum((w for _, w in weights), Decimal(0))
    if base <= 0:
        raise InvalidDiscount("沒有可分攤的金額")
    # 每行至少要留 1 元實付：折到 0 元＝變相贈品（見模組說明的兩條紅線）。
    caps = {key: max(Decimal(0), weight - 1) for key, weight in weights}
    if amount > sum(caps.values(), Decimal(0)):
        raise InvalidDiscount(
            "折扣後會有商品變成 0 元：免費請改用贈品，這樣才統計得到贈品的數量與成本"
        )
    floors: list[tuple[str, Decimal, Decimal]] = []
    for key, weight in weights:
        exact = amount * weight / base
        whole = min(Decimal(int(exact)), caps[key])  # 無條件捨去後夾在容量內
        floors.append((key, whole, exact - Decimal(int(exact))))
    shares = {key: whole for key, whole, _ in floors}
    leftover = int(amount - sum(shares.values(), Decimal(0)))
    # 小數部分大者優先；同分以原順序。已達上限者跳過，剩餘元數往下一個可收的行走。
    order = sorted(range(len(floors)), key=lambda i: (-floors[i][2], i))
    while leftover > 0:
        progressed = False
        for index in order:
            if leftover == 0:
                break
            key = floors[index][0]
            if shares[key] < caps[key]:
                shares[key] += 1
                leftover -= 1
                progressed = True
        if not progressed:  # 容量已於上方檢查過，理論上不可達；防呆避免無限迴圈。
            raise InvalidDiscount("折扣無法分攤（可折金額不足）")
    return [(key, shares[key]) for key, _ in weights]


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

    # **套用順序固定：先全部單品折扣，再整單折扣**（見模組說明）。
    # 依呼叫端給的陣列順序執行的話，店員先點整單再點單品 vs 反過來，同樣兩筆折扣會算出
    # 不同的應付金額（例：600/400 兩行，單品折 200 + 整單 10% → 720 或 700）。
    # 客人付多少不該取決於店員的點擊順序。組內維持原相對次序，分攤仍可重現。
    ordered_requests = [r for r in requests if r.scope is AdjustmentScope.ITEM] + [
        r for r in requests if r.scope is not AdjustmentScope.ITEM
    ]
    for request in ordered_requests:
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

        allocations = _allocate_largest_remainder(amount, [(k, remaining[k]) for k in eligible])

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
