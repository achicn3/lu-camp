"""退款金額的計算（純函式：無 DB、無 I/O）。

## 為什麼用差額法而不是「單價 × 退貨數」

一行的**實付**（`net_amount`）已經含了整單折扣分攤下來的金額，除以數量往往除不盡。
若每次退貨都各自 `round_ntd(實付 ÷ 數量) × 本次退量`，分次退完的加總會與原實付差幾元——
少退是坑客人，多退是店家虧損，而且差幾元永遠對不平。

差額法改問「退到第 x 件時，客人**總共**該拿回多少」，兩次的差就是本次該退：

    entitlement(x) = round_ntd(net_amount × x ÷ qty)
    本次退款       = entitlement(已退 + 本次) − entitlement(已退)

這讓**最後一件自動吸收尾差**，且累計退款恆等於原實付、永不超過。repo 既有的散裝 COGS
與點數沖回都是這個模式。

贈品行的 `net_amount` 為 0 ⟹ 每次退款都是 0：贈品要退回庫存，但沒有錢可退。
"""

from decimal import Decimal

from app.core.money import round_ntd


def refund_entitlement(net_amount: Decimal, line_qty: int, returned_qty: int) -> Decimal:
    """退到第 `returned_qty` 件時，客人累計應拿回的金額。"""
    if line_qty <= 0:
        raise ValueError("銷售明細數量必須大於 0")
    if returned_qty < 0 or returned_qty > line_qty:
        raise ValueError("累計退貨數量必須介於 0 與銷售數量之間")
    if returned_qty == line_qty:
        # 全退必須**恰好**等於原實付，不經四捨五入（否則尾差會漏在店裡或多退給客人）。
        return net_amount
    return Decimal(round_ntd(net_amount * returned_qty / line_qty))


def line_refund_amount(
    net_amount: Decimal, line_qty: int, already_returned: int, qty: int
) -> Decimal:
    """本次退這 `qty` 件應退的金額（差額法）。"""
    if qty <= 0:
        raise ValueError("本次退貨數量必須大於 0")
    return refund_entitlement(
        net_amount, line_qty, already_returned + qty
    ) - refund_entitlement(net_amount, line_qty, already_returned)
