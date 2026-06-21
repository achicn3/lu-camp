"""sales API 整合測試（POST/GET/void/print-detail、idempotency；§11 合約形狀）。"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.models import Sale
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


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


async def _seed(session: AsyncSession, *, open_drawer: bool = True) -> tuple[str, int, int]:
    """建店+店員（開帳），回 (clerk_token, store_id, clerk_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    if open_drawer:
        await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _seed_manager(session: AsyncSession, store_id: int) -> str:
    mgr = User(store_id=store_id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add(mgr)
    await session.flush()
    return encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store_id)


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id, sku="SKU1", name="飲料", unit_price=Decimal(price), quantity_on_hand=qty
    )
    session.add(product)
    await session.flush()
    return product.id


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


def _catalog_line(catalog_id: int, qty: int) -> dict[str, object]:
    return {"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": qty}


async def test_create_sale_happy_path(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="105", qty=10)
    resp = await client.post(
        "/api/v1/sales",
        json={"lines": [_catalog_line(cat, 2)]},
        headers=_auth(token, idem="k1"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == "210"  # 105 * 2，字串傳輸
    assert body["subtotal"] == "200"  # 210 / 1.05
    assert body["tax"] == "10"
    assert body["invoice_status"] == "NOT_ISSUED"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["line_total"] == "210"


async def test_create_requires_idempotency_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token)
    )
    assert resp.status_code == 422  # 缺 Idempotency-Key 標頭


async def test_idempotent_replay_creates_one_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    payload = {"lines": [_catalog_line(cat, 3)]}

    first = await client.post("/api/v1/sales", json=payload, headers=_auth(token, idem="dup"))
    second = await client.post("/api/v1/sales", json=payload, headers=_auth(token, idem="dup"))

    assert first.status_code == 201
    assert second.status_code in (200, 201)
    assert first.json()["id"] == second.json()["id"]  # 回同一單
    # 只建一筆 sale。
    sale_count = await db_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.store_id == store_id)
    )
    assert sale_count == 1
    # 庫存只扣一次（10 - 3 = 7）。
    product = await db_session.get(CatalogProduct, cat)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 7


async def test_blank_idempotency_key_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="")
    )
    assert resp.status_code == 422  # 空 Idempotency-Key（min_length=1）


async def test_same_key_different_payload_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    first = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="reuse")
    )
    assert first.status_code == 201
    # 同 key、不同購物車內容（qty 2）→ 409，不靜默丟單。
    conflict = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 2)]}, headers=_auth(token, idem="reuse")
    )
    assert conflict.status_code == 409
    # 第二筆未落地：庫存只被第一筆扣 1（10 - 1 = 9）。
    product = await db_session.get(CatalogProduct, cat)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 9


async def test_no_open_session_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session, open_drawer=False)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="k")
    )
    assert resp.status_code == 409


async def test_insufficient_stock_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=1)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 5)]}, headers=_auth(token, idem="k")
    )
    assert resp.status_code == 409


async def test_get_sale_and_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    created = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="k")
    )
    sale_id = created.json()["id"]
    got = await client.get(f"/api/v1/sales/{sale_id}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["id"] == sale_id
    assert len(got.json()["lines"]) == 1

    missing = await client.get("/api/v1/sales/999999", headers=_auth(token))
    assert missing.status_code == 404


async def test_list_sales(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="a")
    )
    await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="b")
    )
    resp = await client.get("/api/v1/sales", headers=_auth(token))
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_void_requires_manager_and_marks_void(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    mgr_token = await _seed_manager(db_session, store_id)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    created = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="k")
    )
    sale_id = created.json()["id"]

    # 店員不可作廢。
    forbidden = await client.post(f"/api/v1/sales/{sale_id}/void", headers=_auth(token))
    assert forbidden.status_code == 403

    # 店長作廢 → invoice_status=VOID，並寫稽核。
    voided = await client.post(f"/api/v1/sales/{sale_id}/void", headers=_auth(mgr_token))
    assert voided.status_code == 200
    assert voided.json()["invoice_status"] == "VOID"
    audits = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "VOID_SALE"))
    ).all()
    assert len(audits) == 1

    # 重複作廢 → 409。
    again = await client.post(f"/api/v1/sales/{sale_id}/void", headers=_auth(mgr_token))
    assert again.status_code == 409


async def test_void_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, store_id, _ = await _seed(db_session)
    mgr_token = await _seed_manager(db_session, store_id)
    resp = await client.post("/api/v1/sales/999999/void", headers=_auth(mgr_token))
    assert resp.status_code == 404


async def test_print_detail_writes_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    created = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="k")
    )
    sale_id = created.json()["id"]
    resp = await client.post(f"/api/v1/sales/{sale_id}/print-detail", headers=_auth(token))
    assert resp.status_code == 200
    audits = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "PRINT_SALE_DETAIL"))
    ).all()
    assert len(audits) == 1


async def test_create_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/sales", json={"lines": []}, headers={"Idempotency-Key": "k"})
    assert resp.status_code == 401


async def test_serialized_qty_must_be_one_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _, _ = await _seed(db_session)
    # SERIALIZED qty=2 → 422（schema 擋下，不靜默只賣 1）。
    qty2 = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": "S1", "qty": 2}]},
        headers=_auth(token, idem="k1"),
    )
    assert qty2.status_code == 422
    # SERIALIZED 帶多餘 ref → 422。
    extra = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": "S1", "catalog_product_id": 1}]},
        headers=_auth(token, idem="k2"),
    )
    assert extra.status_code == 422


async def test_print_detail_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, _, _ = await _seed(db_session)
    resp = await client.post("/api/v1/sales/999999/print-detail", headers=_auth(token))
    assert resp.status_code == 404


async def test_list_sales_with_date_range(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, idem="d")
    )
    inside = await client.get(
        "/api/v1/sales?from=2020-01-01T00:00:00&to=2999-01-01T00:00:00", headers=_auth(token)
    )
    assert inside.status_code == 200
    assert len(inside.json()) == 1
    future = await client.get("/api/v1/sales?from=2999-01-01T00:00:00", headers=_auth(token))
    assert future.json() == []


async def test_list_sales_pagination(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=100)
    for i in range(3):
        await client.post(
            "/api/v1/sales",
            json={"lines": [_catalog_line(cat, 1)]},
            headers=_auth(token, idem=f"p{i}"),
        )
    # limit 限制筆數（id desc）。
    page1 = await client.get("/api/v1/sales?limit=2", headers=_auth(token))
    assert len(page1.json()) == 2
    # offset 正確：第 3 筆。
    page2 = await client.get("/api/v1/sales?limit=2&offset=2", headers=_auth(token))
    assert len(page2.json()) == 1
    assert page2.json()[0]["id"] != page1.json()[0]["id"]
    # 超過上限 → 422。
    over = await client.get("/api/v1/sales?limit=201", headers=_auth(token))
    assert over.status_code == 422
    neg = await client.get("/api/v1/sales?offset=-1", headers=_auth(token))
    assert neg.status_code == 422


async def test_create_unexpected_error_rolls_back(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("非領域錯誤")

    monkeypatch.setattr(SalesService, "create_sale", _boom)
    # router 的 except Exception 分支：先 rollback 再 raise（非領域錯誤交由框架轉 500）。
    with pytest.raises(RuntimeError):
        await client.post(
            "/api/v1/sales",
            json={"lines": [_catalog_line(cat, 1)]},
            headers=_auth(token, idem="k"),
        )


async def test_quote_endpoint_returns_discounted_total(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """POST /sales/quote（唯讀）：生效活動下回折後總額；數量品庫存不被扣（試算不動庫存）。"""
    from datetime import UTC, datetime, timedelta

    from app.modules.campaigns.service import CampaignService

    token, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    now = datetime.now(UTC)
    camp = await CampaignService(db_session).create_campaign(
        store_id,
        name="開幕九折",
        discount_pct=10,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=True,
        applies_consignment=False,
        created_by=clerk_id,
    )
    await CampaignService(db_session).activate(store_id, camp.id, actor_user_id=clerk_id)

    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [_catalog_line(cat, 2)]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == "180"  # 90 × 2（九折）
    assert body["campaign_name"] == "開幕九折"
    assert body["lines"][0]["discount_amount"] == "20"
    # 試算唯讀：庫存未變
    product = await db_session.get(CatalogProduct, cat)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 10
