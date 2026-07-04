"""einvoice API 整合測試（T14 殼）：發票查詢、佇列檢視/重送、回執記錄 + RBAC。"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import EInvoiceMessageType, UserRole

TAX_RATE = Decimal("0.05")


class _FakeSerializer:
    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        return b"<Invoice/>"

    def serialize_allowance(self, allowance: object, message_type: EInvoiceMessageType) -> bytes:
        return b"<Allowance/>"


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
    )
    session.add(sale)
    await session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id)
    clk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return store.id, sale.id, mgr_token, clk_token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_get_invoice_returns_amounts_as_strings(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, mgr_token, _ = await _seed(db_session)
    invoice = await EInvoiceService(db_session).create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )

    resp = await client.get(f"/api/v1/invoices/{invoice.id}", headers=_auth(mgr_token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["net"] == "1000"
    assert body["tax"] == "50"
    assert body["total"] == "1050"
    assert body["status"] == "PENDING"  # 尚未平台核可
    assert body["invoice_no"] is None


async def test_get_invoice_404_for_unknown(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, _sale_id, mgr_token, _ = await _seed(db_session)
    resp = await client.get("/api/v1/invoices/999999", headers=_auth(mgr_token))
    assert resp.status_code == 404


async def test_list_queue_requires_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, _sale_id, _mgr, clk_token = await _seed(db_session)
    resp = await client.get("/api/v1/einvoice/queue", headers=_auth(clk_token))
    assert resp.status_code == 403


async def test_list_queue_shows_pending_after_create(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, mgr_token, _ = await _seed(db_session)
    await EInvoiceService(db_session).create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    resp = await client.get("/api/v1/einvoice/queue?status=PENDING", headers=_auth(mgr_token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["message_type"] == "F0401"
    assert body["items"][0]["status"] == "PENDING"


async def test_result_then_retry_flow(
    client: httpx.AsyncClient, db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id, mgr_token, _ = await _seed(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = (await svc.list_queue(store_id))[0].id
    # 回執前必須已拋檔（F5）：以 service 模擬排程拋檔。
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    # 記錄失敗回執 → FAILED
    fail = await client.post(
        f"/api/v1/einvoice/queue/{queue_id}/result",
        json={"success": False, "message": "E0001"},
        headers=_auth(mgr_token),
    )
    assert fail.status_code == 200
    assert fail.json()["status"] == "FAILED"

    # 重送 → PENDING、attempts+1
    retry = await client.post(f"/api/v1/einvoice/queue/{queue_id}/retry", headers=_auth(mgr_token))
    assert retry.status_code == 200
    assert retry.json()["status"] == "PENDING"
    assert retry.json()["attempts"] == 1


async def test_result_on_undropped_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, sale_id, mgr_token, _ = await _seed(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = (await svc.list_queue(store_id))[0].id  # 尚未拋檔

    resp = await client.post(
        f"/api/v1/einvoice/queue/{queue_id}/result",
        json={"success": True},
        headers=_auth(mgr_token),
    )
    assert resp.status_code == 409  # EInvoiceResultNotApplicable


async def test_result_incomplete_issue_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession, tmp_path: Path
) -> None:
    # 平台回成功、但發票缺字軌/日期/時間/隨機碼 → 422（非 500），保留可處理的錯誤語意。
    store_id, sale_id, mgr_token, _ = await _seed(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = (await svc.list_queue(store_id))[0].id
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    resp = await client.post(
        f"/api/v1/einvoice/queue/{queue_id}/result",
        json={"success": True},
        headers=_auth(mgr_token),
    )
    assert resp.status_code == 422


async def test_retry_pending_conflicts(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id, sale_id, mgr_token, _ = await _seed(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = (await svc.list_queue(store_id))[0].id  # PENDING
    resp = await client.post(f"/api/v1/einvoice/queue/{queue_id}/retry", headers=_auth(mgr_token))
    assert resp.status_code == 409
