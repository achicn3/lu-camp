"""手開紙本發票登記（docs/36）。

字軌用完/平台故障時，店家以向國稅局領用的紙本備用發票當場開給客人。系統要能登記，
否則該銷售永遠「未開立」，而且**字軌恢復後有人按重試，平台就真的會再開一張**——
同一筆交易兩張發票。防這件事是本功能最主要的動機。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, time
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.amego import AmegoClient
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.sales.models import Sale, SaleTender
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    EInvoiceAction,
    EInvoiceIssueChannel,
    InvoiceStatus,
    SaleInvoiceStatus,
    SaleStatus,
    TenderType,
    UploadStatus,
    UserRole,
)
from app.shared.exceptions import ManualPaperInvoiceOperation

TAX_RATE = Decimal("0.05")


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession) -> tuple[int, int, str, str]:
    """回 (store_id, sale_id, manager_token, clerk_token)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    manager = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([manager, clerk])
    await session.flush()
    sale = Sale(
        store_id=store.id,
        clerk_user_id=clerk.id,
        subtotal=Decimal(1000),
        tax=Decimal(50),
        total=Decimal(1050),
        invoice_status=SaleInvoiceStatus.PENDING_ISSUE,
    )
    session.add(sale)
    await session.flush()
    return (
        store.id,
        sale.id,
        encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "invoice_no": "ZA10029999",
        "invoice_date": "2026-08-17",
        "invoice_time": "14:32:00",
        "total": "1050",
        "random_number": "1234",
    }
    body.update(overrides)
    return body


async def _pending_invoice(session: AsyncSession, store_id: int, sale_id: int) -> Invoice:
    return await EInvoiceService(session).create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )


# ── 登記 ──


async def test_register_marks_issued_and_cancels_the_pending_queue_row(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """登記後：發票 ISSUED、來源 MANUAL_PAPER、**待送 F0401 轉 CANCELLED**。

    最後那一項是重點：不取消的話，字軌恢復後任何人按「重試開立」，平台會真的再開一張。
    """
    store_id, sale_id, mgr, _ = await _seed(db_session)
    invoice = await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()

    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice",
        json=_body(),
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invoice_no"] == "ZA10029999"
    assert resp.json()["issue_channel"] == "MANUAL_PAPER"

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED
    assert invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER
    assert invoice.invoice_date == date(2026, 8, 17)
    assert invoice.invoice_time == "14:32:00"
    # 平台條碼內容留空 → 前端據此擋下證明聯列印（紙本已在客人手上）
    assert invoice.barcode_text is None

    sale = await db_session.get(Sale, sale_id)
    assert sale is not None
    await db_session.refresh(sale)
    assert sale.invoice_status is SaleInvoiceStatus.ISSUED

    queue = (
        await db_session.scalars(
            select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.invoice_id == invoice.id)
        )
    ).all()
    issue_rows = [q for q in queue if q.action is EInvoiceAction.ISSUE]
    assert issue_rows and all(q.status is UploadStatus.CANCELLED for q in issue_rows)


async def test_register_writes_audit_log(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """人工輸入稅務號碼屬敏感操作（CLAUDE.md §5）。"""
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()

    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    rows = await db_session.scalars(
        select(AuditLog.action).where(AuditLog.store_id == store_id)
    )
    actions = list(rows.all())
    assert "REGISTER_MANUAL_INVOICE" in actions


async def test_register_requires_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, _, clerk = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(clerk)
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("invoice_no", "12345678AB"),  # 格式錯（字軌 2 碼 + 8 位數）
        ("invoice_no", "ZA1002999"),  # 少一位
        ("random_number", "12a4"),  # 隨機碼須 4 位數字
    ],
)
async def test_register_rejects_malformed_input(
    client: httpx.AsyncClient, db_session: AsyncSession, field: str, value: str
) -> None:
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice",
        json=_body(**{field: value}),
        headers=_auth(mgr),
    )
    assert resp.status_code == 422


async def test_register_rejects_amount_mismatch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """登記手開發票不是改金額的後門。"""
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice",
        json=_body(total="999"),
        headers=_auth(mgr),
    )
    assert resp.status_code == 409
    assert "金額" in resp.json()["detail"]


async def test_register_rejects_already_issued_invoice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, mgr, _ = await _seed(db_session)
    invoice = await _pending_invoice(db_session, store_id, sale_id)
    invoice.status = InvoiceStatus.ISSUED
    invoice.invoice_no = "ZA10020001"
    await db_session.flush()
    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
    )
    assert resp.status_code == 409


async def test_duplicate_invoice_no_in_same_store_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同店同號碼不可登記兩次（既有部分唯一索引擋下）。"""
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    other_sale = Sale(
        store_id=store_id,
        clerk_user_id=(await db_session.scalar(select(User.id).where(User.role == UserRole.CLERK))),
        subtotal=Decimal(1000),
        tax=Decimal(50),
        total=Decimal(1050),
        invoice_status=SaleInvoiceStatus.PENDING_ISSUE,
    )
    db_session.add(other_sale)
    await db_session.flush()
    await _pending_invoice(db_session, store_id, other_sale.id)
    await db_session.flush()

    first = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/v1/einvoice/sales/{other_sale.id}/manual-invoice",
        json=_body(),
        headers=_auth(mgr),
    )
    assert second.status_code == 409


# ── 防重複開立：登記後不可再送平台 ──


class _ExplodingTransport:
    """任何一次呼叫都代表「送出了 F0401」——本測試的失敗條件。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        raise AssertionError(f"不應對平台發出任何請求（{url}）")


async def test_issue_for_sale_sends_nothing_after_manual_registration(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """**本功能存在的理由**：登記手開後，字軌恢復再按「重試開立」不可送出 F0401。

    以「一被呼叫就炸」的 transport 斷言真的沒有對平台發出請求——不是只看回應碼。
    """
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    assert (
        await client.post(
            f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
        )
    ).status_code == 200

    svc = EInvoiceService(db_session)
    amego = AmegoClient(
        seller_tax_id="12345678",
        app_key="test-key",
        transport=_ExplodingTransport(),
        base_url="https://invoice-api.amego.tw",
    )
    invoice = await svc.issue_for_sale(store_id, sale_id, client=amego)
    assert invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER
    # 也不得留下任何待送/可重送的佇列列
    rows = (
        await db_session.scalars(
            select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id)
        )
    ).all()
    assert all(
        row.status not in (UploadStatus.PENDING, UploadStatus.FAILED) for row in rows
    )


# ── 出口擋下 ──


async def test_void_is_blocked_for_manual_paper(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    assert (
        await client.post(
            f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
        )
    ).status_code == 200

    with pytest.raises(ManualPaperInvoiceOperation, match="紙本"):
        await EInvoiceService(db_session).void_invoice_for_sale(
            store_id, sale_id, actor_user_id=None
        )


async def test_allowance_is_blocked_for_manual_paper(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, mgr, _ = await _seed(db_session)
    invoice = await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    assert (
        await client.post(
            f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
        )
    ).status_code == 200

    with pytest.raises(ManualPaperInvoiceOperation, match="紙本"):
        await EInvoiceService(db_session).record_allowance(
            store_id, invoice_id=invoice.id, total=Decimal(100)
        )


async def test_manual_registration_leaves_proof_printing_blocked(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """證明聯不可印：平台條碼內容為空，前端據此擋下（紙本已在客人手上）。"""
    store_id, sale_id, mgr, _ = await _seed(db_session)
    invoice = await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
    )
    await db_session.refresh(invoice)
    assert invoice.barcode_text is None
    assert invoice.qrcode_left is None
    assert invoice.qrcode_right is None


def test_manual_invoice_time_is_optional_in_schema() -> None:
    """時間可省略（紙本上不一定寫得清楚）；省略時不強迫店員亂填。"""
    from app.modules.einvoice.schemas import ManualInvoiceRegisterRequest

    req = ManualInvoiceRegisterRequest.model_validate(
        {"invoice_no": "ZA10029999", "invoice_date": "2026-08-17", "total": "1050"}
    )
    assert req.invoice_time is None
    assert req.random_number is None
    assert req.invoice_date == date(2026, 8, 17)
    assert isinstance(time(14, 32), time)  # 型別可用性


# ── 對抗審查：已送出但結果未知，不可登記 ──


async def test_register_refuses_when_f0401_may_already_be_at_the_platform(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """佇列列已被認領（xml_path/dropped_at 已寫）＝平台可能已收到 F0401。

    此時登記手開會取消掉那一列，平台之後若真的開出發票 → 手開紙本 + 電子發票 = 兩張，
    而且遲到的成功回執會因佇列已 CANCELLED 被當成衝突，再也對不回來。
    這正是本功能唯一要防的事，必須擋在登記之前，要求先完成對帳。
    （既有的 void_invoice_for_sale 早就用 xml_path 區分「平台可能已收到」與「從未收過」。）
    """
    store_id, sale_id, mgr, _ = await _seed(db_session)
    invoice = await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    row = await db_session.scalar(
        select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.invoice_id == invoice.id)
    )
    assert row is not None
    # 模擬 Amego 認領後 HTTP 逾時：狀態仍 PENDING，但已認領（結果未知）。
    row.xml_path = "amego:/json/f0401#a0"
    row.dropped_at = datetime.now(UTC)
    # 先 commit：端點被擋下時會 rollback，未 commit 的前置資料會一起消失，
    # 就驗不到「什麼都沒被改到」。
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
    )
    assert resp.status_code == 409, resp.text
    assert "對帳" in resp.json()["detail"]

    # router 會 rollback（物件被逐出 session），重新查詢確認什麼都沒改到
    fresh_invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert fresh_invoice is not None
    assert fresh_invoice.status is InvoiceStatus.PENDING  # 未被登記
    fresh_row = await db_session.scalar(
        select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.invoice_id == fresh_invoice.id)
    )
    assert fresh_row is not None
    assert fresh_row.status is UploadStatus.PENDING  # 未被取消


async def test_register_still_allowed_after_platform_explicitly_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """平台**明確拒絕**（FAILED）＝已知沒開出來，仍可登記手開紙本。

    retry 會把 xml_path/dropped_at 清掉；FAILED 是平台給的確定答案，不是結果未知。
    """
    store_id, sale_id, mgr, _ = await _seed(db_session)
    invoice = await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    row = await db_session.scalar(
        select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.invoice_id == invoice.id)
    )
    assert row is not None
    row.status = UploadStatus.FAILED
    row.last_error = "字軌號碼不足"
    await db_session.flush()

    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    await db_session.refresh(row)
    assert row.status is UploadStatus.CANCELLED


# ── 對抗審查：號碼必須是 ASCII 數字 ──


@pytest.mark.parametrize(
    "invoice_no",
    [
        "ZA１２３４５６７８",  # 全形數字
        "ZA١٢٣٤٥٦٧٨",  # 阿拉伯-印度數字
        "ZA1234５678",  # 混合
    ],
)
def test_invoice_no_rejects_non_ascii_digits(invoice_no: str) -> None:
    """Pydantic 的 `\\d` 接受 Unicode 數字：全形號碼會被存成另一個字串，
    資料庫唯一索引視為不同號碼 → 對帳永遠對不起來。必須限定 ASCII。"""
    from pydantic import ValidationError

    from app.modules.einvoice.schemas import ManualInvoiceRegisterRequest

    with pytest.raises(ValidationError):
        ManualInvoiceRegisterRequest.model_validate(
            {"invoice_no": invoice_no, "invoice_date": "2026-08-17", "total": "1050"}
        )


def test_random_number_rejects_non_ascii_digits() -> None:
    from pydantic import ValidationError

    from app.modules.einvoice.schemas import ManualInvoiceRegisterRequest

    with pytest.raises(ValidationError):
        ManualInvoiceRegisterRequest.model_validate(
            {
                "invoice_no": "ZA12345678",
                "invoice_date": "2026-08-17",
                "total": "1050",
                "random_number": "１２３４",
            }
        )


# ── 對抗審查：作廢守衛必須在動到錢之前 ──


async def test_void_guard_runs_before_any_refund(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """紙本發票的銷售作廢必須在**任何副作用之前**被擋下。

    原本守衛在 void_invoice_for_sale 裡，而它在現金/購物金反轉與**不可逆的 LINE Pay 退款**
    之後才執行：守衛拋錯 → 主交易回滾，但已送出的外部退款收不回來，結果是客人拿到錢、
    單子卻還有效、發票也沒作廢，而且重試永遠被同一守衛拒絕。
    以「錢有沒有動」當判準：擋下時不得留下任何退款流水。
    """
    store_id, sale_id, mgr, _ = await _seed(db_session)
    clerk_id = await db_session.scalar(select(User.id).where(User.role == UserRole.CLERK))
    assert clerk_id is not None
    await CashDrawerService(db_session).open_session(store_id, clerk_id, Decimal("1000"))
    await _pending_invoice(db_session, store_id, sale_id)
    db_session.add(
        SaleTender(
            store_id=store_id, sale_id=sale_id, tender_type=TenderType.CASH, amount=Decimal(1050)
        )
    )
    await db_session.flush()
    assert (
        await client.post(
            f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
        )
    ).status_code == 200

    sale = await db_session.get(Sale, sale_id)
    assert sale is not None
    with pytest.raises(ManualPaperInvoiceOperation, match="紙本"):
        await SalesService(db_session).void_sale(sale, actor_user_id=clerk_id)

    refunds = (
        await db_session.scalars(
            select(CashMovement).where(
                CashMovement.store_id == store_id,
                CashMovement.type == CashMovementType.SALE_REFUND_OUT,
            )
        )
    ).all()
    assert refunds == [], "守衛在退款之後才擋 → 錢已經動了"
    assert sale.status is not SaleStatus.VOIDED


async def test_void_guard_runs_before_the_taiwanpay_manual_refund_prompt(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """紙本守衛必須早於**台灣Pay 的人工退款提示**（Codex 對抗審查第二輪 critical #2）。

    台灣Pay 沒有退款 API：系統會先要求店員自己去 App 把錢退給客人、勾確認後再作廢。
    若守衛排在那道提示之後，店員會照做→**錢真的退出去了**→再作廢才被擋，
    留下客人已退款、單子仍有效、紙本發票也沒作廢的狀態，且重試永遠被同一守衛拒絕。
    判準：連「請先去 App 退款」都不該說出口，第一次就要說「紙本不能自動作廢」。
    """
    store_id, sale_id, mgr, _ = await _seed(db_session)
    clerk_id = await db_session.scalar(select(User.id).where(User.role == UserRole.CLERK))
    assert clerk_id is not None
    await CashDrawerService(db_session).open_session(store_id, clerk_id, Decimal("1000"))
    await _pending_invoice(db_session, store_id, sale_id)
    db_session.add(
        SaleTender(
            store_id=store_id,
            sale_id=sale_id,
            tender_type=TenderType.TAIWAN_PAY,
            amount=Decimal(1050),
        )
    )
    await db_session.flush()
    assert (
        await client.post(
            f"/api/v1/einvoice/sales/{sale_id}/manual-invoice", json=_body(), headers=_auth(mgr)
        )
    ).status_code == 200

    sale = await db_session.get(Sale, sale_id)
    assert sale is not None
    # 未帶 manual_refund_ack：若守衛排在後面，這裡會先丟 ManualRefundRequired（叫店員去退款）
    with pytest.raises(ManualPaperInvoiceOperation, match="紙本"):
        await SalesService(db_session).void_sale(sale, actor_user_id=clerk_id)


@pytest.mark.parametrize("bad_time", ["14:32:00.123456", "14:32:00+08:00"])
async def test_register_rejects_time_that_would_overflow_the_column(
    client: httpx.AsyncClient, db_session: AsyncSession, bad_time: str
) -> None:
    """帶微秒/時區的時間要在邊界回 422，不是撞 VARCHAR(8) 變 500。"""
    store_id, sale_id, mgr, _ = await _seed(db_session)
    await _pending_invoice(db_session, store_id, sale_id)
    await db_session.flush()
    resp = await client.post(
        f"/api/v1/einvoice/sales/{sale_id}/manual-invoice",
        json=_body(invoice_time=bad_time),
        headers=_auth(mgr),
    )
    assert resp.status_code == 422
