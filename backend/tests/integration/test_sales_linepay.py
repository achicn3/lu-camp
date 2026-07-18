"""LINE Pay Offline v4 結帳整合（docs/30 P2 §4）。

驗證：成功收款（非現金、不進抽屜、手續費快照、linepay_transactions 落庫）、fail-closed 拒付
整筆回滾、check-first 冪等重用（不重複扣款）、未啟用/缺冪等鍵/缺付款碼守衛、傳輸錯誤 fail-closed。

以真 LinePayClient 包一個「腳本化傳輸替身」測——經過真實簽章/序列化，只替換網路 I/O。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.linepay import LinePayClient, LinePayTransport
from app.modules.sales.models import LinePayTransaction, Sale, SaleTender
from app.modules.sales.service import SalesService
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    Grade,
    LinePayRefundStatus,
    LinePayStatus,
    OwnershipType,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    TenderType,
    UserRole,
)
from app.shared.exceptions import (
    InvalidSaleTender,
    InvalidStateTransition,
    LinePayChargeFailed,
    LinePayRefundAmbiguous,
    LinePayTransportError,
    ManualRefundRequired,
)

_CHECK_NOT_FOUND: dict[str, object] = {
    "returnCode": "1150",
    "returnMessage": "Transaction record not found.",
}
_PAY_SUCCESS: dict[str, object] = {
    "returnCode": "0000",
    "returnMessage": "Success.",
    "info": {"transactionId": 2026071802368895010, "orderId": "x"},
}
_PAY_REJECT: dict[str, object] = {"returnCode": "1133", "returnMessage": "invalid OneTimeKey"}
_CHECK_COMPLETE: dict[str, object] = {
    "returnCode": "0000",
    "returnMessage": "Success.",
    "info": {"transactionId": 2026071802368895010, "status": "COMPLETE"},
}


class ScriptedTransport(LinePayTransport):
    """腳本化傳輸：依 URL 分派 check/pay，記錄呼叫次數；pay 可設定拋傳輸錯誤。"""

    def __init__(
        self,
        *,
        check_resp: dict[str, object],
        pay_resp: dict[str, object] | None = None,
        pay_error: Exception | None = None,
    ) -> None:
        self.check_resp = check_resp
        self.pay_resp = pay_resp
        self.pay_error = pay_error
        self.check_calls = 0
        self.pay_calls = 0

    async def send(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> dict[str, object]:
        if url.endswith("/check"):
            self.check_calls += 1
            return self.check_resp
        if url.endswith("/oneTimeKeys/pay"):
            self.pay_calls += 1
            if self.pay_error is not None:
                raise self.pay_error
            assert self.pay_resp is not None
            return self.pay_resp
        raise AssertionError(f"未預期的 URL：{url}")


def _client(transport: LinePayTransport) -> LinePayClient:
    return LinePayClient(
        channel_id="2010746859",
        channel_secret="secret",
        base_url="https://sandbox-api-pay.line.me",
        transport=transport,
        nonce_factory=lambda: "fixed-nonce",
    )


async def _seed(session: AsyncSession, *, linepay_enabled: bool = True) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    # 設定：啟用 LINE Pay、費率 2%（get-or-create 帶其餘 defaults）。
    await StoreSettingsService(session).update_settings(
        store.id,
        actor_user_id=None,
        patch=SettingsUpdateRequest(
            linepay_enabled=linepay_enabled, linepay_fee_pct=Decimal("0.02")
        ),
    )
    return store.id, clerk.id


async def _seed_item(session: AsyncSession, store_id: int, *, code: str, price: str) -> None:
    await InventoryService(session).create_serialized_item(
        store_id,
        item_code=code,
        name=f"品-{code}",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(price),
        acquisition_cost=Decimal("100"),
    )


def _line(code: str) -> list[SaleLineInput]:
    return [SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]


def _tender(amount: str) -> list[TenderInput]:
    return [
        TenderInput(
            tender_type=TenderType.LINE_PAY,
            amount=Decimal(amount),
            line_pay_one_time_key="OTK-scanned-123",
        )
    ]


@pytest.mark.asyncio
async def test_linepay_success_non_cash_with_fee_and_txn(db_session: AsyncSession) -> None:
    # 無開帳（非現金不需開帳）：LINE Pay 收款仍成立。
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="S1", price="1000")
    transport = ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=_PAY_SUCCESS)

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_line("S1"),
        tenders=_tender("1000"),
        idempotency_key="pos-key-1",
        linepay_client=_client(transport),
    )

    assert sale.payment_method == PaymentMethod.LINE_PAY
    assert transport.check_calls == 1 and transport.pay_calls == 1
    # tender：手續費 = 1000 × 2% = 20，非現金不減 amount
    tender = await db_session.scalar(select(SaleTender).where(SaleTender.sale_id == sale.id))
    assert tender is not None
    assert tender.tender_type == TenderType.LINE_PAY
    assert tender.amount == Decimal("1000") and tender.fee_amount == Decimal("20")
    # linepay_transactions：COMPLETE、交易號以字串精確保存（19 位無失真）
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None
    assert txn.transaction_id == "2026071802368895010"
    assert txn.amount == Decimal("1000") and txn.refunded_amount == Decimal("0")
    assert txn.order_id.startswith(f"LP-{store_id}-")
    # 非現金、不進抽屜：無現金流水
    cash_count = await db_session.scalar(
        select(func.count()).select_from(CashMovement).where(CashMovement.store_id == store_id)
    )
    assert cash_count == 0


@pytest.mark.asyncio
async def test_margin_report_payment_fee_and_net_margin(db_session: AsyncSession) -> None:
    # docs/30 §7 決策 1：手續費獨立支出行——認列營收/gross 不含，net_margin = gross − 手續費。
    store_id, clerk_id = await _seed(db_session)
    transport = ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=_PAY_SUCCESS)
    # 自有序號品：成交 1000、成本 100 → gross_margin = 900；LINE Pay fee = 1000×2% = 20。
    sale = await _make_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id, code="M1"
    )
    now = datetime.now(UTC)
    bd = await SalesService(db_session).margin_breakdown(
        store_id, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert bd.gross_margin == Decimal("900")
    assert bd.payment_fee_total == Decimal("20")
    assert bd.net_margin == Decimal("880")  # gross − 手續費（獨立支出行）
    # 依 tender 分列：LINE_PAY 收款 1000、手續費 20
    lp = [m for m in bd.payment_methods if m[0] == "LINE_PAY"]
    assert lp == [("LINE_PAY", Decimal("1000"), Decimal("20"))]
    assert sale.payment_method == PaymentMethod.LINE_PAY


@pytest.mark.asyncio
async def test_linepay_reject_fails_closed_rolls_back(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="S2", price="500")
    transport = ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=_PAY_REJECT)

    # fail-closed：拒付 → 拋 LinePayChargeFailed，create_sale 全程不 commit（router 回滾整筆，
    # 序號品/收款皆不落地）。此 harness 單交易回滾會連 seed 一併清，故以「明確拋出」為 fail-closed
    # 的服務層保證；未 commit 即無已完成單。
    with pytest.raises(LinePayChargeFailed):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=_line("S2"),
            tenders=_tender("500"),
            idempotency_key="pos-key-2",
            linepay_client=_client(transport),
        )


@pytest.mark.asyncio
async def test_linepay_check_first_reuses_completed_without_recharge(
    db_session: AsyncSession,
) -> None:
    # 前次已扣款（回應遺失、本地回滾）→ 重試時 check 回 COMPLETE → 不再呼叫 pay。
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="S3", price="800")
    transport = ScriptedTransport(check_resp=_CHECK_COMPLETE, pay_resp=_PAY_SUCCESS)

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_line("S3"),
        tenders=_tender("800"),
        idempotency_key="pos-key-3",
        linepay_client=_client(transport),
    )
    assert transport.check_calls == 1
    assert transport.pay_calls == 0  # 未重複扣款
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None and txn.transaction_id == "2026071802368895010"


@pytest.mark.asyncio
async def test_linepay_transport_error_fails_closed(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="S4", price="300")
    transport = ScriptedTransport(
        check_resp=_CHECK_NOT_FOUND, pay_error=LinePayTransportError("網路逾時")
    )
    # 傳輸錯誤（結果未知）→ fail-closed：沿 LinePayTransportError 上拋，整筆不成立（router 回滾）。
    with pytest.raises(LinePayTransportError):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=_line("S4"),
            tenders=_tender("300"),
            idempotency_key="pos-key-4",
            linepay_client=_client(transport),
        )


@pytest.mark.asyncio
async def test_linepay_disabled_rejected(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed(db_session, linepay_enabled=False)
    await _seed_item(db_session, store_id, code="S5", price="100")
    transport = ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=_PAY_SUCCESS)
    with pytest.raises(LinePayChargeFailed):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=_line("S5"),
            tenders=_tender("100"),
            idempotency_key="pos-key-5",
            linepay_client=_client(transport),
        )
    assert transport.pay_calls == 0  # 未啟用：連 API 都不呼叫


@pytest.mark.asyncio
async def test_linepay_requires_idempotency_key(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="S6", price="100")
    transport = ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=_PAY_SUCCESS)
    with pytest.raises(InvalidSaleTender):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=_line("S6"),
            tenders=_tender("100"),
            idempotency_key=None,
            linepay_client=_client(transport),
        )


_REFUND_SUCCESS: dict[str, object] = {
    "returnCode": "0000",
    "returnMessage": "Success.",
    "info": {"refundTransactionId": 2026071802368895211},
}
_REFUND_ALREADY: dict[str, object] = {
    "returnCode": "1165",
    "returnMessage": "The transaction has already been refunded.",
}
_REFUND_REJECT: dict[str, object] = {"returnCode": "9000", "returnMessage": "refund error"}


class RefundTransport(LinePayTransport):
    """作廢退款用替身：check→未完成、pay→成功、refund→可設定成功/已退款/失敗或拋傳輸錯誤。"""

    def __init__(
        self,
        *,
        refund_resp: dict[str, object] | None = None,
        refund_error: Exception | None = None,
    ) -> None:
        self.refund_resp = refund_resp
        self.refund_error = refund_error
        self.refund_calls = 0

    async def send(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> dict[str, object]:
        if url.endswith("/check"):
            return _CHECK_NOT_FOUND
        if url.endswith("/oneTimeKeys/pay"):
            return _PAY_SUCCESS
        if url.endswith("/refund"):
            self.refund_calls += 1
            if self.refund_error is not None:
                raise self.refund_error
            assert self.refund_resp is not None
            return self.refund_resp
        raise AssertionError(url)


async def _make_linepay_sale(
    db_session: AsyncSession,
    transport: LinePayTransport,
    *,
    store_id: int,
    clerk_id: int,
    code: str,
) -> Sale:
    await _seed_item(db_session, store_id, code=code, price="1000")
    return await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_line(code),
        tenders=_tender("1000"),
        idempotency_key=f"key-{code}",
        linepay_client=_client(transport),
    )


@pytest.mark.asyncio
async def test_void_linepay_sale_refunds_and_marks_refunded(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    sale = await _make_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id, code="V1"
    )
    voided = await SalesService(db_session).void_sale(
        sale, clerk_id, linepay_client=_client(transport)
    )
    assert voided.invoice_status == SaleInvoiceStatus.VOID
    assert transport.refund_calls == 1
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None
    assert txn.status == LinePayStatus.REFUNDED
    assert txn.refunded_amount == Decimal("1000")


@pytest.mark.asyncio
async def test_void_linepay_already_refunded_on_platform_is_idempotent(
    db_session: AsyncSession,
) -> None:
    # 平台回 1165（已退款）→ 視為成功、標 REFUNDED（不因重試而卡住作廢）。
    store_id, clerk_id = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_ALREADY)
    sale = await _make_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id, code="V2"
    )
    voided = await SalesService(db_session).void_sale(
        sale, clerk_id, linepay_client=_client(transport)
    )
    assert voided.invoice_status == SaleInvoiceStatus.VOID
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None and txn.status == LinePayStatus.REFUNDED


@pytest.mark.asyncio
async def test_void_linepay_refund_failure_fails_closed(db_session: AsyncSession) -> None:
    # 退款被拒 → LinePayChargeFailed（作廢整筆回滾，不留已作廢卻未退款的單）。
    store_id, clerk_id = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_REJECT)
    sale = await _make_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id, code="V3"
    )
    with pytest.raises(LinePayChargeFailed):
        await SalesService(db_session).void_sale(
            sale, clerk_id, linepay_client=_client(transport)
        )


async def _make_2line_linepay_sale(
    db_session: AsyncSession, transport: LinePayTransport, *, store_id: int, clerk_id: int
) -> Sale:
    """建 2 行 LINE Pay 銷售（S-A 600 + S-B 400 = 1000），供部分/全額退貨測試。"""
    await _seed_item(db_session, store_id, code="RA", price="600")
    await _seed_item(db_session, store_id, code="RB", price="400")
    return await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="RA"),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="RB"),
        ],
        tenders=[
            TenderInput(
                tender_type=TenderType.LINE_PAY,
                amount=Decimal("1000"),
                line_pay_one_time_key="OTK-2line",
            )
        ],
        idempotency_key="key-2line",
        linepay_client=_client(transport),
    )


async def _line_id(db_session: AsyncSession, sale_id: int, item_code: str) -> int:
    from app.modules.inventory.models import SerializedItem
    from app.modules.sales.models import SaleLine

    row = await db_session.scalar(
        select(SaleLine)
        .join(SerializedItem, SaleLine.serialized_item_id == SerializedItem.id)
        .where(SaleLine.sale_id == sale_id, SerializedItem.item_code == item_code)
    )
    assert row is not None
    return row.id


@pytest.mark.asyncio
async def test_return_linepay_partial_refunds_line_amount(db_session: AsyncSession) -> None:
    # docs/30 §5 裁示：退貨可只退部分——退一行 → refund 該行金額、累加 refunded_amount，
    # 未全退保持 COMPLETE。
    from app.modules.returns.service import ReturnLineInput, ReturnsService

    store_id, clerk_id = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    sale = await _make_2line_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id
    )
    ra_line = await _line_id(db_session, sale.id, "RA")  # 600

    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(ra_line, 1)],
        reason="客人退貨",
        actor_user_id=clerk_id,
        idempotency_key="ret-partial-1",
        linepay_client=_client(transport),
    )
    assert transport.refund_calls == 1
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None
    assert txn.refunded_amount == Decimal("600")  # 只退 RA 行
    assert txn.status == LinePayStatus.COMPLETE  # 未全退 → 保持 COMPLETE
    # 非現金退款：不記現金退出流水
    refund_moves = await db_session.scalar(
        select(func.count()).select_from(CashMovement).where(CashMovement.store_id == store_id)
    )
    assert refund_moves == 0


@pytest.mark.asyncio
async def test_return_linepay_full_marks_refunded(db_session: AsyncSession) -> None:
    from app.modules.returns.service import ReturnLineInput, ReturnsService

    store_id, clerk_id = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    sale = await _make_2line_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id
    )
    ra = await _line_id(db_session, sale.id, "RA")
    rb = await _line_id(db_session, sale.id, "RB")
    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(ra, 1), ReturnLineInput(rb, 1)],
        reason="全退",
        actor_user_id=clerk_id,
        idempotency_key="ret-full-1",
        linepay_client=_client(transport),
    )
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None
    assert txn.refunded_amount == Decimal("1000") and txn.status == LinePayStatus.REFUNDED


@pytest.mark.asyncio
async def test_return_linepay_refund_failure_fails_closed(db_session: AsyncSession) -> None:
    from app.modules.returns.service import ReturnLineInput, ReturnsService

    store_id, clerk_id = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    sale = await _make_2line_linepay_sale(
        db_session, transport, store_id=store_id, clerk_id=clerk_id
    )
    ra = await _line_id(db_session, sale.id, "RA")
    # 退款被拒 → LinePayChargeFailed（退貨整筆回滾，不留已退貨卻未退款）
    reject = RefundTransport(refund_resp=_REFUND_REJECT)
    with pytest.raises(LinePayChargeFailed):
        await ReturnsService(db_session).create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(ra, 1)],
            reason="退款失敗測試",
            actor_user_id=clerk_id,
            idempotency_key="ret-fail-1",
            linepay_client=_client(reject),
        )


_CHECK_COMPLETE_WRONG_AMOUNT: dict[str, object] = {
    "returnCode": "0000",
    "returnMessage": "Success.",
    "info": {
        "transactionId": 2026071802368895010,
        "status": "COMPLETE",
        "payInfo": [{"method": "BALANCE", "amount": 500}],
    },
}


@pytest.mark.asyncio
async def test_charge_check_first_rejects_amount_mismatch(db_session: AsyncSession) -> None:
    # Codex finding #2：check-first 回 COMPLETE 但平台金額（500）≠本次（1000）→ 拒絕重用。
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="AM", price="1000")
    transport = ScriptedTransport(
        check_resp=_CHECK_COMPLETE_WRONG_AMOUNT, pay_resp=_PAY_SUCCESS
    )
    with pytest.raises(LinePayChargeFailed):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=_line("AM"),
            tenders=_tender("1000"),
            idempotency_key="am-key",
            linepay_client=_client(transport),
        )
    assert transport.pay_calls == 0  # 金額不符即拒、不改呼 pay


@pytest.mark.asyncio
async def test_durable_refund_skips_when_already_succeeded(db_session: AsyncSession) -> None:
    # Codex finding #1：同 refund_key 第二次呼叫見 durable SUCCEEDED → 不重呼平台（防重退）。
    store_id, _clerk = await _seed(db_session)
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    svc = SalesService(db_session)
    key = f"test-succ-{uuid4()}"
    await svc._durable_line_pay_refund(
        store_id=store_id, order_id="LP-x", refund_key=key, amount=Decimal("100"),
        client=_client(transport),
    )
    await svc._durable_line_pay_refund(
        store_id=store_id, order_id="LP-x", refund_key=key, amount=Decimal("100"),
        client=_client(transport),
    )
    assert transport.refund_calls == 1  # 第二次跳過、不重退


@pytest.mark.asyncio
async def test_refund_different_key_cannot_over_refund(db_session: AsyncSession) -> None:
    # Codex 第三輪 #2：退款已 durable SUCCEEDED 但本地回滾，換**不同 refund_key** 重試同 order →
    # 依 order 累計校準補回已退額 → 超退拒（不因換鍵繞過而重複退款）。
    store_id, clerk_id = await _seed(db_session)
    sale = await _make_linepay_sale(
        db_session, RefundTransport(refund_resp=_REFUND_SUCCESS),
        store_id=store_id, clerk_id=clerk_id, code="OR",
    )
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None
    svc = SalesService(db_session)
    # 模擬：某退款已 durable 成立（ledger SUCCEEDED），但本地 refunded_amount 回滾為 0
    await svc._durable_line_pay_refund(
        store_id=store_id, order_id=txn.order_id,
        refund_key=f"s{store_id}:return:OLD-{uuid4()}", amount=Decimal("1000"),
        client=_client(RefundTransport(refund_resp=_REFUND_SUCCESS)),
    )
    assert txn.refunded_amount == Decimal("0")  # 本地未更新（模擬回滾）
    bad = RefundTransport(refund_resp=_REFUND_SUCCESS)
    with pytest.raises(LinePayChargeFailed):
        await svc.refund_line_pay_amount(
            store_id, sale.id, Decimal("1000"), _client(bad),
            refund_key=f"s{store_id}:return:NEW-{uuid4()}",
        )
    assert bad.refund_calls == 0  # 換鍵超退：未再呼叫平台


@pytest.mark.asyncio
async def test_refund_same_key_retry_no_double_count(db_session: AsyncSession) -> None:
    # Codex 第三輪 #2：同 refund_key 重試（前次已成立、本地回滾）→ 不重呼平台、不重複計額。
    store_id, clerk_id = await _seed(db_session)
    sale = await _make_linepay_sale(
        db_session, RefundTransport(refund_resp=_REFUND_SUCCESS),
        store_id=store_id, clerk_id=clerk_id, code="SK",
    )
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None
    svc = SalesService(db_session)
    key = f"s{store_id}:return:{uuid4()}"
    await svc._durable_line_pay_refund(
        store_id=store_id, order_id=txn.order_id, refund_key=key, amount=Decimal("1000"),
        client=_client(RefundTransport(refund_resp=_REFUND_SUCCESS)),
    )
    retry = RefundTransport(refund_resp=_REFUND_SUCCESS)
    result = await svc.refund_line_pay_amount(
        store_id, sale.id, Decimal("1000"), _client(retry), refund_key=key
    )
    assert result is True
    assert retry.refund_calls == 0  # 已成立 → 跳過、不重退
    assert txn.refunded_amount == Decimal("1000")
    assert txn.status == LinePayStatus.REFUNDED


@pytest.mark.asyncio
async def test_resolve_pending_refund_unblocks(db_session: AsyncSession) -> None:
    # Codex 第三輪 #3：卡住的 PENDING 退款可由店長於退款對帳頁解決（SUCCEEDED/FAILED），不永久卡死。
    store_id, clerk_id = await _seed(db_session)
    svc = SalesService(db_session)
    key = f"s{store_id}:return:{uuid4()}"
    with pytest.raises(LinePayTransportError):
        await svc._durable_line_pay_refund(
            store_id=store_id, order_id="LP-r", refund_key=key, amount=Decimal("100"),
            client=_client(RefundTransport(refund_error=LinePayTransportError("網路逾時"))),
        )
    pending = await svc.list_pending_linepay_refunds(store_id)
    attempt = next(a for a in pending if a.refund_key == key)
    resolved = await svc.resolve_linepay_refund(
        store_id, attempt.id, resolution=LinePayRefundStatus.SUCCEEDED, actor_user_id=clerk_id
    )
    assert resolved.status == LinePayRefundStatus.SUCCEEDED
    # 解決後不再是 PENDING（後續退貨/作廢的依 order 對帳會據此補回，不再擋 ambiguous）
    assert all(a.refund_key != key for a in await svc.list_pending_linepay_refunds(store_id))
    # 非 PENDING 不可再解決
    with pytest.raises(InvalidStateTransition):
        await svc.resolve_linepay_refund(
            store_id, attempt.id, resolution=LinePayRefundStatus.FAILED, actor_user_id=clerk_id
        )


@pytest.mark.asyncio
async def test_durable_refund_rejects_content_mismatch(db_session: AsyncSession) -> None:
    # Codex 第二輪 #1：同 refund_key 但金額（或店/訂單）不符 → 拒絕重用（不把別筆退款誤記成完成）。
    store_id, _clerk = await _seed(db_session)
    svc = SalesService(db_session)
    key = f"test-mismatch-{uuid4()}"
    await svc._durable_line_pay_refund(
        store_id=store_id, order_id="LP-z", refund_key=key, amount=Decimal("100"),
        client=_client(RefundTransport(refund_resp=_REFUND_SUCCESS)),
    )
    # 同 key、不同金額 → ambiguous（拒重用既有 SUCCEEDED 列）
    bad = RefundTransport(refund_resp=_REFUND_SUCCESS)
    with pytest.raises(LinePayRefundAmbiguous):
        await svc._durable_line_pay_refund(
            store_id=store_id, order_id="LP-z", refund_key=key, amount=Decimal("200"),
            client=_client(bad),
        )
    assert bad.refund_calls == 0


@pytest.mark.asyncio
async def test_durable_refund_pending_then_ambiguous(db_session: AsyncSession) -> None:
    # Codex finding #1：呼叫平台後傳輸錯誤（結果未定）→ 保留 PENDING；重試見 PENDING → ambiguous。
    store_id, _clerk = await _seed(db_session)
    svc = SalesService(db_session)
    key = f"test-amb-{uuid4()}"
    err = RefundTransport(refund_error=LinePayTransportError("網路逾時"))
    with pytest.raises(LinePayTransportError):
        await svc._durable_line_pay_refund(
            store_id=store_id, order_id="LP-y", refund_key=key, amount=Decimal("100"),
            client=_client(err),
        )
    assert err.refund_calls == 1
    retry = RefundTransport(refund_resp=_REFUND_SUCCESS)
    with pytest.raises(LinePayRefundAmbiguous):
        await svc._durable_line_pay_refund(
            store_id=store_id, order_id="LP-y", refund_key=key, amount=Decimal("100"),
            client=_client(retry),
        )
    assert retry.refund_calls == 0  # 結果未定 → 不盲目重退


@pytest.mark.asyncio
async def test_void_taiwanpay_requires_manual_refund_ack(db_session: AsyncSession) -> None:
    # Codex finding #3：台灣Pay 無 API 退款；作廢須店員手動退款確認，否則擋（不靜默作廢仍扣款）。
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="TW", price="500")
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_line("TW"),
        tenders=[TenderInput(tender_type=TenderType.TAIWAN_PAY, amount=Decimal("500"))],
        idempotency_key="tw-key",
    )
    assert sale.payment_method == PaymentMethod.TAIWAN_PAY
    with pytest.raises(ManualRefundRequired):
        await SalesService(db_session).void_sale(sale, clerk_id)
    # 帶手動退款確認 → 放行
    voided = await SalesService(db_session).void_sale(
        sale, clerk_id, manual_refund_ack=True
    )
    assert voided.invoice_status == SaleInvoiceStatus.VOID


@pytest.mark.asyncio
async def test_linepay_requires_one_time_key(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed(db_session)
    await _seed_item(db_session, store_id, code="S7", price="100")
    transport = ScriptedTransport(check_resp=_CHECK_NOT_FOUND, pay_resp=_PAY_SUCCESS)
    with pytest.raises(InvalidSaleTender):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=_line("S7"),
            tenders=[TenderInput(tender_type=TenderType.LINE_PAY, amount=Decimal("100"))],
            idempotency_key="pos-key-7",
            linepay_client=_client(transport),
        )
