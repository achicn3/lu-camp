"""退貨發票處置同意（RETURN_INVOICE_CONSENT）的簽署任務建立。

重點有二：
1. **買受人可以是非會員**——零售退貨多半是臨櫃客人，`signature_tasks.contact_id` 若強制
   非空，等於讓所有匿名交易的已開發票退貨全部無法完成。故此類型（且僅此類型）允許無會員。
2. **同意內容一律由後端依銷售單與政策重建**，不採信客端敘述——否則店務端可讓客人簽下
   與事實不符的「同意書」（沿 TRANSACTION_ACK 的既有原則）。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.einvoice.models import Invoice
from app.modules.inventory.service import InventoryService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleLineType,
    SignatureTaskKind,
    SignatureTaskStatus,
    UserRole,
)
from app.shared.exceptions import ContactNotFound, SignatureTaskConflict
from tests.integration.customer_display_helpers import (
    ensure_paired_customer_display,
    signature_png_base64,
)


async def _seed(session: AsyncSession) -> tuple[int, int, str]:
    store = Store(name="門市", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    session.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    item = await InventoryService(session).create_serialized_item(
        store.id,
        item_code="SN-CONSENT-1",
        name="相機",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(1050),
        acquisition_cost=Decimal(500),
    )
    return store.id, clerk.id, item.item_code


async def _anonymous_sale_with_issued_invoice(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    code: str,
    *,
    buyer_contact_id: int | None = None,
) -> int:
    """建立一筆銷售（預設**無會員**＝匿名）並讓其發票處於已開立狀態。"""
    sale = await SalesService(session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
        buyer_contact_id=buyer_contact_id,
    )
    invoice = await session.scalar(
        text("SELECT id FROM invoices WHERE sale_id = :sid").bindparams(sid=sale.id)
    )
    assert invoice is not None
    row = await session.get(Invoice, int(invoice))
    assert row is not None
    row.status = InvoiceStatus.ISSUED
    row.invoice_no = "AB10000001"
    row.invoice_date = datetime.now(UTC).date()
    row.print_mark = True
    await session.flush()
    return int(sale.id)


async def _create_consent_task(
    session: AsyncSession,
    *,
    store_id: int,
    clerk_id: int,
    sale_id: int,
    lines: list[dict[str, int]],
    contact_id: int | None = None,
) -> int:
    terminal, _device = await ensure_paired_customer_display(
        session, store_id=store_id, actor_user_id=clerk_id
    )
    task = await SigningService(session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
            contact_id=contact_id,
            content={"lines": lines},
            terminal_id=terminal.id,
            ref_type="sale",
            ref_id=sale_id,
        ),
        created_by=clerk_id,
    )
    return int(task.id)


async def test_anonymous_customer_can_be_asked_to_consent(db_session: AsyncSession) -> None:
    """臨櫃客人（非會員）也能簽同意書——否則匿名交易的發票退貨全數卡死。"""
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _anonymous_sale_with_issued_invoice(db_session, store_id, clerk_id, code)
    lines = await SalesService(db_session).get_lines(sale_id)

    task_id = await _create_consent_task(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        sale_id=sale_id,
        lines=[{"sale_line_id": lines[0].id, "qty": 1}],
    )

    svc = SigningService(db_session)
    task = await svc.get_task(store_id, task_id)
    assert task is not None
    assert task.contact_id is None


async def test_consent_content_is_rebuilt_from_the_sale_not_the_client(
    db_session: AsyncSession,
) -> None:
    """客端夾帶的敘述一律不進快照；顯示金額與處置方式由後端依銷售單與政策重建。"""
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _anonymous_sale_with_issued_invoice(db_session, store_id, clerk_id, code)
    lines = await SalesService(db_session).get_lines(sale_id)

    await ensure_paired_customer_display(db_session, store_id=store_id, actor_user_id=clerk_id)
    task = await SigningService(db_session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
            contact_id=None,
            content={
                "lines": [{"sale_line_id": lines[0].id, "qty": 1}],
                "refund_total": "99999",  # 客端亂寫的金額
                "invoice_action_label": "什麼都不會發生",  # 客端亂寫的處置
            },
            ref_type="sale",
            ref_id=sale_id,
        ),
        created_by=clerk_id,
    )

    assert task.content["sale_ref"] == f"#{sale_id}"
    assert task.content["refund_total"] == "1050"
    # 匿名整筆退＋本月開立的發票 → 作廢原發票
    assert task.content["invoice_action_label"] == "作廢原發票"
    assert task.content["invoice_no"] == "AB10000001"


async def test_partial_return_consent_says_allowance(db_session: AsyncSession) -> None:
    store_id, clerk_id, _code = await _seed(db_session)
    bulk = await InventoryService(db_session).create_bulk_lot(
        store_id,
        lot_code="LOT-CONSENT-1",
        name="散裝糖果",
        grade=Grade.E,
        acquisition_cost=Decimal(200),
        acquisition_basis=BulkAcquisitionBasis.UNSPECIFIED,
        unit_price=Decimal(100),
        total_qty=5,
    )
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=bulk.id, qty=3)],
    )
    invoice_id = await db_session.scalar(
        text("SELECT id FROM invoices WHERE sale_id = :sid").bindparams(sid=sale.id)
    )
    row = await db_session.get(Invoice, int(invoice_id or 0))
    assert row is not None
    row.status = InvoiceStatus.ISSUED
    row.invoice_no = "AB10000002"
    row.invoice_date = datetime.now(UTC).date()
    await db_session.flush()
    lines = await SalesService(db_session).get_lines(sale.id)

    task_id = await _create_consent_task(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        sale_id=sale.id,
        lines=[{"sale_line_id": lines[0].id, "qty": 1}],
    )
    task = await SigningService(db_session).get_task(store_id, task_id)
    assert task is not None
    assert task.content["invoice_action_label"] == "開立折讓單"
    assert task.content["refund_total"] == "100"


async def test_other_kinds_still_require_a_contact(db_session: AsyncSession) -> None:
    """只有退貨同意可無會員；其餘類型缺會員仍應被擋（守住既有保證）。"""
    store_id, clerk_id, _code = await _seed(db_session)
    await ensure_paired_customer_display(db_session, store_id=store_id, actor_user_id=clerk_id)
    with pytest.raises(ContactNotFound):
        await SigningService(db_session).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=None,
                content={},
            ),
            created_by=clerk_id,
        )


async def test_database_rejects_null_contact_for_other_kinds(db_session: AsyncSession) -> None:
    """DB 層 CHECK 是最後一道牆：非退貨同意的任務不得無會員。"""
    _store_id, _clerk_id, _code = await _seed(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO signature_tasks "
                "(store_id, kind, status, contact_id, content, created_by, created_at) "
                "VALUES (:s, 'TRANSACTION_ACK', 'PENDING', NULL, '{}'::jsonb, :u, now())"
            ).bindparams(s=_store_id, u=_clerk_id)
        )
    await db_session.rollback()


async def test_consent_task_must_reference_a_sale(db_session: AsyncSession) -> None:
    store_id, clerk_id, _code = await _seed(db_session)
    await ensure_paired_customer_display(db_session, store_id=store_id, actor_user_id=clerk_id)
    with pytest.raises(SignatureTaskConflict):
        await SigningService(db_session).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
                contact_id=None,
                content={},
                ref_type=None,
                ref_id=None,
            ),
            created_by=clerk_id,
        )


async def test_anonymous_full_return_completes_end_to_end(db_session: AsyncSession) -> None:
    """匿名客人簽完同意 → 整筆退貨成立、原發票作廢。"""
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _anonymous_sale_with_issued_invoice(db_session, store_id, clerk_id, code)
    lines = await SalesService(db_session).get_lines(sale_id)
    task_id = await _create_consent_task(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        sale_id=sale_id,
        lines=[{"sale_line_id": lines[0].id, "qty": 1}],
    )
    svc = SigningService(db_session)
    _terminal, device = await ensure_paired_customer_display(
        db_session, store_id=store_id, actor_user_id=clerk_id
    )
    await svc.acknowledge_task(store_id, device.id, task_id)
    await svc.sign_task(
        store_id,
        task_id,
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=None,
        idempotency_key="consent-anon-1",
    )

    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=lines[0].id, qty=1)],
        reason="不喜歡",
        actor_user_id=clerk_id,
        idempotency_key="anon-return-1",
        invoice_recalled=True,
        consent_signature_task_id=task_id,
    )

    task = await svc.get_task(store_id, task_id)
    assert task is not None and task.status is SignatureTaskStatus.CONSUMED
    invoice = await db_session.scalar(
        text("SELECT status FROM invoices WHERE sale_id = :sid").bindparams(sid=sale_id)
    )
    assert invoice in {"VOID", "VOID_PENDING"}


async def test_member_sale_still_records_the_signer(db_session: AsyncSession) -> None:
    """有會員的交易：同意必須記在**該買方**名下（證據要能指出是誰簽的）。"""
    store_id, clerk_id, code = await _seed(db_session)
    member = Contact(store_id=store_id, name="王小明", roles=["MEMBER"], phone="0912345678")
    db_session.add(member)
    await db_session.flush()
    sale_id = await _anonymous_sale_with_issued_invoice(
        db_session, store_id, clerk_id, code, buyer_contact_id=member.id
    )
    lines = await SalesService(db_session).get_lines(sale_id)
    task_id = await _create_consent_task(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        sale_id=sale_id,
        lines=[{"sale_line_id": lines[0].id, "qty": 1}],
        contact_id=member.id,
    )
    task = await SigningService(db_session).get_task(store_id, task_id)
    assert task is not None and task.contact_id == member.id


async def test_signer_must_be_the_buyer_of_that_sale(db_session: AsyncSession) -> None:
    """簽署人由銷售單決定，不採信客端（Codex 對抗審查 #4）。

    否則能把甲的同意掛到乙的單上，或把有會員買受人的證據降級成匿名——兩者都讓同意書
    指不出「是誰同意的」。
    """
    store_id, clerk_id, code = await _seed(db_session)
    buyer = Contact(store_id=store_id, name="買方", roles=["MEMBER"], phone="0911111111")
    other = Contact(store_id=store_id, name="路人", roles=["MEMBER"], phone="0922222222")
    db_session.add_all([buyer, other])
    await db_session.flush()
    sale_id = await _anonymous_sale_with_issued_invoice(
        db_session, store_id, clerk_id, code, buyer_contact_id=buyer.id
    )
    lines = await SalesService(db_session).get_lines(sale_id)
    scope = [{"sale_line_id": lines[0].id, "qty": 1}]

    # 掛到別的會員 → 拒絕
    with pytest.raises(SignatureTaskConflict, match="買方"):
        await _create_consent_task(
            db_session,
            store_id=store_id,
            clerk_id=clerk_id,
            sale_id=sale_id,
            lines=scope,
            contact_id=other.id,
        )
    # 有買方卻降級成匿名 → 拒絕
    with pytest.raises(SignatureTaskConflict, match="買方"):
        await _create_consent_task(
            db_session,
            store_id=store_id,
            clerk_id=clerk_id,
            sale_id=sale_id,
            lines=scope,
            contact_id=None,
        )


async def test_anonymous_sale_cannot_name_a_member_as_signer(db_session: AsyncSession) -> None:
    """匿名交易不可硬指一位會員當簽署人（會捏造出「某會員同意過」的證據）。"""
    store_id, clerk_id, code = await _seed(db_session)
    member = Contact(store_id=store_id, name="無關會員", roles=["MEMBER"], phone="0933333333")
    db_session.add(member)
    await db_session.flush()
    sale_id = await _anonymous_sale_with_issued_invoice(db_session, store_id, clerk_id, code)
    lines = await SalesService(db_session).get_lines(sale_id)
    with pytest.raises(SignatureTaskConflict, match="匿名"):
        await _create_consent_task(
            db_session,
            store_id=store_id,
            clerk_id=clerk_id,
            sale_id=sale_id,
            lines=[{"sale_line_id": lines[0].id, "qty": 1}],
            contact_id=member.id,
        )
