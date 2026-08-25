"""退貨的發票處置 × 各種付款渠道（ADR-014 缺口補測）。

`test_returns_invoice_void.py` 只用純現金驗證接線與守衛；門市實際會遇到購物金、LINE Pay、
台灣Pay 與混合付款。此檔補上「錢怎麼退」與「發票怎麼處置」**同時發生**時的正確性：

1. 作廢／折讓的判定與付款渠道無關（發票金額是商品含稅總額，不是外部退款差額）。
2. 退款分配仍是購物金優先、外部渠道只退差額，且累計不得超過原付款。
3. 台灣Pay 的手動退款確認與紙本收回、簽名同意三者互不取代。
4. LINE Pay 退款是**外部呼叫**：後續步驟失敗必須整筆回滾，且重試不得重複退款。
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import Invoice, InvoiceAllowance
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import LinePayTransaction
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.signing.models import SignatureTask
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    EInvoiceAction,
    Grade,
    InvoiceStatus,
    InvoiceVoidReason,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    SignatureTaskKind,
    SignatureTaskStatus,
    TenderType,
    UserRole,
)
from app.shared.exceptions import LinePayChargeFailed, ReturnConflict
from tests.integration.customer_display_helpers import (
    prepare_signed_store_credit_cart,
    return_consent_content,
)
from tests.integration.test_sales_einvoice import _FakeSerializer
from tests.integration.test_sales_linepay import (
    _REFUND_SUCCESS,
    RefundTransport,
    _client,
    _linepay_cart_kwargs,
)


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    """門市＋店長＋會員＋開帳，並啟用電子發票與 LINE Pay。"""
    store = Store(name="退貨渠道門市", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id,
        username=f"rt-clerk-{store.id}",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    session.add(clerk)
    await session.flush()
    await StoreSettingsService(session).update_settings(
        store.id,
        actor_user_id=None,
        patch=SettingsUpdateRequest(
            linepay_enabled=True,
            linepay_fee_pct=Decimal("0.02"),
        ),
    )
    # einvoice_enabled 直接寫欄位：啟用閘門要求 AMEGO_APP_KEY 環境變數，本檔測的是退貨×發票
    # 處置，不是啟用閘門（該閘門另有測試）。
    settings = await session.scalar(select(StoreSettings).where(StoreSettings.store_id == store.id))
    assert settings is not None
    settings.einvoice_enabled = True
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    member = Contact(
        store_id=store.id, name="渠道測試客", roles=["MEMBER"], phone=f"09{store.id:08d}"
    )
    session.add(member)
    await session.flush()
    return store.id, clerk.id, member.id


async def _item(session: AsyncSession, store_id: int, code: str, price: str) -> str:
    item = await InventoryService(session).create_serialized_item(
        store_id,
        item_code=code,
        name=f"品-{code}",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(price),
        acquisition_cost=Decimal("100"),
    )
    return item.item_code


def _lines(*codes: str) -> list[SaleLineInput]:
    return [SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=c) for c in codes]


async def _issue(session: AsyncSession, store_id: int, sale_id: int, tmp_path: Path) -> Invoice:
    """把該筆銷售的發票推到 ISSUED（本月、有紙本），以驅動「同月整筆退＝作廢」。"""
    einvoice = EInvoiceService(session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale_id)
    assert invoice is not None
    invoice.invoice_no = f"AB{sale_id:08d}"
    invoice.invoice_date = datetime.now(UTC).date()
    invoice.invoice_time = "12:34:56"
    invoice.random_number = "1234"
    invoice.print_mark = True
    await session.flush()
    queue_id = next(
        i.id for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ISSUE
    )
    await einvoice.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, queue_id, success=True)
    return invoice


async def _consent(
    session: AsyncSession,
    store_id: int,
    sale_id: int,
    *,
    contact_id: int,
    created_by: int,
    return_lines: dict[int, int],
) -> int:
    task = SignatureTask(
        store_id=store_id,
        kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
        contact_id=contact_id,
        content=await return_consent_content(
            session, store_id=store_id, sale_id=sale_id, return_lines=return_lines
        ),
        content_sha256="c" * 64,
        signature_sha256="s" * 64,
        evidence_hash="e" * 64,
        status=SignatureTaskStatus.SIGNED,
        signed_at=datetime.now(UTC),
        ref_type="sale",
        ref_id=sale_id,
        created_by=created_by,
    )
    session.add(task)
    await session.flush()
    return int(task.id)


def _tenders(customer_return: object) -> list[tuple[TenderType, Decimal]]:
    return [
        (t.tender_type, t.amount)
        for t in sorted(customer_return.refund_tenders, key=lambda t: t.tender_type.value)  # type: ignore[attr-defined]
    ]


# ── A1：純購物金 ─────────────────────────────────────────────────────────────


async def test_store_credit_full_return_voids_invoice_and_restores_credit(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """購物金付款整筆退：發票作廢（全額）、購物金全額回補、抽屜不動。

    發票金額是商品含稅總額，與「客人是用購物金付的」無關——作廢的是整張發票。
    """
    store_id, clerk_id, member_id = await _seed(db_session)
    code = await _item(db_session, store_id, f"SC-{store_id}", "1000")
    credit = StoreCreditService(db_session)
    await credit.adjust(
        store_id,
        member_id,
        amount=Decimal("1000"),
        reason="測試入帳",
        created_by=clerk_id,
        idempotency_key=f"sc-seed-{store_id}",
    )
    signed = await prepare_signed_store_credit_cart(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member_id,
            "lines": [{"line_type": "SERIALIZED", "item_code": code, "qty": 1}],
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "1000"}],
        },
    )
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_lines(code),
        buyer_contact_id=member_id,
        tenders=[TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal("1000"))],
        idempotency_key=f"sc-sale-{store_id}",
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    invoice = await _issue(db_session, store_id, sale.id, tmp_path)
    assert await credit.get_balance(store_id, member_id) == Decimal("0")
    sale_lines = await SalesService(db_session).get_lines(sale.id)
    cash_before = await db_session.scalar(
        select(CashMovement.id).where(CashMovement.type == CashMovementType.SALE_REFUND_OUT)
    )

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_lines[0].id, 1)],
        reason="整筆退",
        actor_user_id=clerk_id,
        idempotency_key=f"sc-ret-{store_id}",
        invoice_recalled=True,
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=member_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: 1},
        ),
    )

    assert _tenders(customer_return) == [(TenderType.STORE_CREDIT, Decimal("1000"))]
    assert await credit.get_balance(store_id, member_id) == Decimal("1000")
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    assert invoice.void_reason is InvoiceVoidReason.FULL_RETURN
    refreshed = await SalesService(db_session).get_sale(store_id, sale.id)
    assert refreshed is not None and refreshed.status is SaleStatus.RETURNED
    # 購物金退款不進錢櫃
    cash_after = await db_session.scalar(
        select(CashMovement.id).where(CashMovement.type == CashMovementType.SALE_REFUND_OUT)
    )
    assert cash_after == cash_before


# ── A2：購物金＋現金 ─────────────────────────────────────────────────────────


async def test_credit_plus_cash_full_return_voids_invoice_and_splits_refund(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """購物金 400＋現金 600 整筆退：發票作廢，退款購物金優先、現金補差額。"""
    store_id, clerk_id, member_id = await _seed(db_session)
    code = await _item(db_session, store_id, f"CC-{store_id}", "1000")
    credit = StoreCreditService(db_session)
    await credit.adjust(
        store_id,
        member_id,
        amount=Decimal("400"),
        reason="測試入帳",
        created_by=clerk_id,
        idempotency_key=f"cc-seed-{store_id}",
    )
    signed = await prepare_signed_store_credit_cart(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member_id,
            "lines": [{"line_type": "SERIALIZED", "item_code": code, "qty": 1}],
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "400"},
                {"tender_type": "CASH", "amount": "600"},
            ],
        },
    )
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_lines(code),
        buyer_contact_id=member_id,
        tenders=[
            TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal("400")),
            TenderInput(tender_type=TenderType.CASH, amount=Decimal("600")),
        ],
        idempotency_key=f"cc-sale-{store_id}",
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    invoice = await _issue(db_session, store_id, sale.id, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale.id)

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_lines[0].id, 1)],
        reason="整筆退",
        actor_user_id=clerk_id,
        idempotency_key=f"cc-ret-{store_id}",
        invoice_recalled=True,
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=member_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: 1},
        ),
    )

    assert _tenders(customer_return) == [
        (TenderType.CASH, Decimal("600")),
        (TenderType.STORE_CREDIT, Decimal("400")),
    ]
    assert await credit.get_balance(store_id, member_id) == Decimal("400")
    cash_out = await db_session.scalar(
        select(CashMovement).where(
            CashMovement.ref_type == "return",
            CashMovement.ref_id == customer_return.id,
        )
    )
    assert cash_out is not None and cash_out.amount == Decimal("600")
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    assert invoice.void_reason is InvoiceVoidReason.FULL_RETURN


# ── A3／A4：LINE Pay（純與混合）────────────────────────────────────────────


async def test_linepay_full_return_voids_invoice_and_refunds_platform(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """純 LINE Pay 整筆退：平台全額退款 ＋ 原發票作廢。"""
    store_id, clerk_id, _member_id = await _seed(db_session)
    code = await _item(db_session, store_id, f"LP-{store_id}", "1000")
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    lines = _lines(code)
    tenders = [
        TenderInput(
            tender_type=TenderType.LINE_PAY,
            amount=Decimal("1000"),
            line_pay_one_time_key="OTK-void-1",
        )
    ]
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=lines,
        tenders=tenders,
        idempotency_key=f"lp-sale-{store_id}",
        linepay_client=_client(transport),
        **await _linepay_cart_kwargs(
            db_session,
            store_id=store_id,
            clerk_id=clerk_id,
            lines=lines,
            tenders=tenders,
        ),
    )
    invoice = await _issue(db_session, store_id, sale.id, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale.id)

    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_lines[0].id, 1)],
        reason="整筆退",
        actor_user_id=clerk_id,
        idempotency_key=f"lp-ret-{store_id}",
        linepay_client=_client(transport),
        invoice_recalled=True,
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=_member_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: 1},
        ),
    )

    assert _tenders(customer_return) == [(TenderType.LINE_PAY, Decimal("1000"))]
    assert transport.refund_calls == 1
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None and txn.refunded_amount == Decimal("1000")
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    assert invoice.void_reason is InvoiceVoidReason.FULL_RETURN


async def test_credit_plus_linepay_partial_then_full_keeps_allowance_and_caps_refund(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """購物金 300＋LINE Pay 700 的三件單，分兩次退完（B 組 7）。

    - 第一次退一件（$400）→ 部分退貨 → **折讓**，退款全走購物金 300、LINE Pay 100
    - 第二次退完剩餘（$600）→ 雖是累計全退，但原發票**已開過折讓 → 仍折讓、不得作廢**
    - 兩次退款加總 = $1000 = 原付款；購物金回到 300，LINE Pay 累計退 700（不多退）
    """
    store_id, clerk_id, member_id = await _seed(db_session)
    codes = [
        await _item(db_session, store_id, f"ML{i}-{store_id}", p)
        for i, p in ((1, "400"), (2, "300"), (3, "300"))
    ]
    credit = StoreCreditService(db_session)
    await credit.adjust(
        store_id,
        member_id,
        amount=Decimal("300"),
        reason="測試入帳",
        created_by=clerk_id,
        idempotency_key=f"ml-seed-{store_id}",
    )
    transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    signed = await prepare_signed_store_credit_cart(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member_id,
            "lines": [{"line_type": "SERIALIZED", "item_code": c, "qty": 1} for c in codes],
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "300"},
                {
                    "tender_type": "LINE_PAY",
                    "amount": "700",
                    "line_pay_one_time_key": "OTK-mixed-ret",
                },
            ],
        },
    )
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_lines(*codes),
        buyer_contact_id=member_id,
        tenders=[
            TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal("300")),
            TenderInput(
                tender_type=TenderType.LINE_PAY,
                amount=Decimal("700"),
                line_pay_one_time_key="OTK-mixed-ret",
            ),
        ],
        idempotency_key=f"ml-sale-{store_id}",
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
        linepay_client=_client(transport),
    )
    invoice = await _issue(db_session, store_id, sale.id, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale.id)
    returns = ReturnsService(db_session)

    first = await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_lines[0].id, 1)],
        reason="先退一件",
        actor_user_id=clerk_id,
        idempotency_key=f"ml-ret1-{store_id}",
        linepay_client=_client(transport),
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=member_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: 1},
        ),
    )
    assert _tenders(first) == [
        (TenderType.LINE_PAY, Decimal("100")),
        (TenderType.STORE_CREDIT, Decimal("300")),
    ]

    second = await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[
            ReturnLineInput(sale_lines[1].id, 1),
            ReturnLineInput(sale_lines[2].id, 1),
        ],
        reason="退完剩餘",
        actor_user_id=clerk_id,
        idempotency_key=f"ml-ret2-{store_id}",
        linepay_client=_client(transport),
        invoice_recalled=True,
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=member_id,
            created_by=clerk_id,
            return_lines={sale_lines[1].id: 1, sale_lines[2].id: 1},
        ),
    )
    assert _tenders(second) == [(TenderType.LINE_PAY, Decimal("600"))]

    # 錢：購物金回到 300、LINE Pay 累計退 700，合計 1000＝原付款，一元不多。
    assert await credit.get_balance(store_id, member_id) == Decimal("300")
    txn = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale.id)
    )
    assert txn is not None and txn.refunded_amount == Decimal("700")

    # 發票：全程折讓，原發票未被作廢；折讓總額＝商品含稅總額 1000（非外部退款 700）。
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED
    assert invoice.void_reason is None
    allowances = (
        await db_session.scalars(
            select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
        )
    ).all()
    assert sum((a.total for a in allowances), Decimal(0)) == Decimal("1000")
    refreshed = await SalesService(db_session).get_sale(store_id, sale.id)
    assert refreshed is not None and refreshed.status is SaleStatus.RETURNED


async def test_cumulative_refund_cannot_exceed_original_payment(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """守衛回歸：可退餘量用盡後再退 → 擋下，不可能多退錢。"""
    store_id, clerk_id, member_id = await _seed(db_session)
    code = await _item(db_session, store_id, f"OV-{store_id}", "500")
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_lines(code),
        idempotency_key=f"ov-sale-{store_id}",
    )
    await _issue(db_session, store_id, sale.id, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale.id)
    returns = ReturnsService(db_session)
    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_lines[0].id, 1)],
        reason="整筆退",
        actor_user_id=clerk_id,
        idempotency_key=f"ov-ret1-{store_id}",
        invoice_recalled=True,
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=member_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: 1},
        ),
    )
    with pytest.raises(ReturnConflict, match="已全數退貨"):
        await returns.create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(sale_lines[0].id, 1)],
            reason="再退一次",
            actor_user_id=clerk_id,
            idempotency_key=f"ov-ret2-{store_id}",
            invoice_recalled=True,
        )


# ── A5：台灣Pay 的三重確認 ──────────────────────────────────────────────────


async def test_taiwan_pay_needs_manual_refund_ack_on_top_of_paper_and_consent(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """台灣Pay 整筆退：紙本收回、買受人同意、手動退款確認**三者缺一不可**。

    三道要求彼此不可取代——少任何一項都必須擋在退款之前。
    """
    store_id, clerk_id, member_id = await _seed(db_session)
    code = await _item(db_session, store_id, f"TP-{store_id}", "800")
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=_lines(code),
        tenders=[TenderInput(tender_type=TenderType.TAIWAN_PAY, amount=Decimal("800"))],
        idempotency_key=f"tp-sale-{store_id}",
    )
    invoice = await _issue(db_session, store_id, sale.id, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale.id)
    returns = ReturnsService(db_session)

    async def _attempt(*, recalled: bool, with_consent: bool, twpay_ack: bool, key: str) -> None:
        await returns.create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(sale_lines[0].id, 1)],
            reason="台灣Pay 整筆退",
            actor_user_id=clerk_id,
            idempotency_key=key,
            invoice_recalled=recalled,
            taiwan_pay_refund_confirmed=twpay_ack,
            consent_signature_task_id=(
                await _consent(
                    db_session,
                    store_id,
                    sale.id,
                    contact_id=member_id,
                    created_by=clerk_id,
                    return_lines={sale_lines[0].id: 1},
                )
                if with_consent
                else None
            ),
        )

    with pytest.raises(ReturnConflict, match="收回"):
        await _attempt(recalled=False, with_consent=True, twpay_ack=True, key="tp-a")
    with pytest.raises(ReturnConflict, match="同意"):
        await _attempt(recalled=True, with_consent=False, twpay_ack=True, key="tp-b")
    with pytest.raises(ReturnConflict, match="台灣Pay"):
        await _attempt(recalled=True, with_consent=True, twpay_ack=False, key="tp-c")

    # 三項都齊備才成立（前面每次失敗都擋在建單之前，沒留下任何退貨列）。
    assert (
        await db_session.scalar(
            select(CashMovement.id).where(
                CashMovement.ref_type == "return", CashMovement.store_id == store_id
            )
        )
        is None
    )
    done = await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_lines[0].id, 1)],
        reason="台灣Pay 整筆退",
        actor_user_id=clerk_id,
        idempotency_key="tp-ok",
        invoice_recalled=True,
        taiwan_pay_refund_confirmed=True,
        consent_signature_task_id=await _consent(
            db_session,
            store_id,
            sale.id,
            contact_id=member_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: 1},
        ),
    )
    assert _tenders(done) == [(TenderType.TAIWAN_PAY, Decimal("800"))]
    # 台灣Pay 無 API：不進錢櫃、由店員手動退款，系統只留退貨憑證與發票作廢。
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    assert invoice.void_reason is InvoiceVoidReason.FULL_RETURN


# ── C10：LINE Pay 已退款、後續失敗 ─────────────────────────────────────────


async def test_linepay_refund_failure_leaves_no_return_and_no_invoice_change(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """LINE Pay 退款被平台拒絕 → 整筆退貨不成立，原發票**不得**被作廢。

    發票作廢排在退款之後正是為此：退不了錢就不該動客人的發票。
    """
    store_id, clerk_id, member_id = await _seed(db_session)
    code = await _item(db_session, store_id, f"LF-{store_id}", "900")
    ok_transport = RefundTransport(refund_resp=_REFUND_SUCCESS)
    lines = _lines(code)
    tenders = [
        TenderInput(
            tender_type=TenderType.LINE_PAY,
            amount=Decimal("900"),
            line_pay_one_time_key="OTK-fail-1",
        )
    ]
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=lines,
        tenders=tenders,
        idempotency_key=f"lf-sale-{store_id}",
        linepay_client=_client(ok_transport),
        **await _linepay_cart_kwargs(
            db_session,
            store_id=store_id,
            clerk_id=clerk_id,
            lines=lines,
            tenders=tenders,
        ),
    )
    invoice = await _issue(db_session, store_id, sale.id, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale.id)
    reject = RefundTransport(refund_resp={"returnCode": "1104", "returnMessage": "拒絕"})

    with pytest.raises(LinePayChargeFailed):
        await ReturnsService(db_session).create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(sale_lines[0].id, 1)],
            reason="整筆退",
            actor_user_id=clerk_id,
            idempotency_key=f"lf-ret-{store_id}",
            linepay_client=_client(reject),
            invoice_recalled=True,
            consent_signature_task_id=await _consent(
                db_session,
                store_id,
                sale.id,
                contact_id=member_id,
                created_by=clerk_id,
                return_lines={sale_lines[0].id: 1},
            ),
        )

    # 退款失敗當下：發票作廢那一步根本沒跑到（它排在退款之後），故發票仍是已開立。
    # 呼叫端會回滾整筆交易；此處驗的是「失敗點之前沒有先動發票」這個順序保證。
    assert invoice.status is InvoiceStatus.ISSUED
    assert invoice.void_reason is None
    assert sale.status is SaleStatus.COMPLETED
    assert sale.invoice_status is SaleInvoiceStatus.ISSUED
    assert reject.refund_calls == 1
