"""LINE Pay Offline v4 結帳整合（docs/30 P2 §4）。

驗證：成功收款（非現金、不進抽屜、手續費快照、linepay_transactions 落庫）、fail-closed 拒付
整筆回滾、check-first 冪等重用（不重複扣款）、未啟用/缺冪等鍵/缺付款碼守衛、傳輸錯誤 fail-closed。

以真 LinePayClient 包一個「腳本化傳輸替身」測——經過真實簽章/序列化，只替換網路 I/O。
"""

from decimal import Decimal

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
    LinePayStatus,
    OwnershipType,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    TenderType,
    UserRole,
)
from app.shared.exceptions import InvalidSaleTender, LinePayChargeFailed, LinePayTransportError

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
    """作廢退款用替身：check→未完成、pay→成功、refund→可設定成功/已退款/失敗。"""

    def __init__(self, *, refund_resp: dict[str, object]) -> None:
        self.refund_resp = refund_resp
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
