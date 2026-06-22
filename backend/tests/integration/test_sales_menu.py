"""sales × menu 整合測試：餐飲行的金流不變量。

- 餐飲行成交（不扣庫存、原價、line_type=MENU）。
- 二手＋餐飲同一購物車。
- 購物金不得折抵內用：store_credit tender ≤ total − 餐飲小計（超出 422）。
- 會員點數只認非餐飲小計。
- 門市活動折扣不套用餐飲。
- quote 回 food_subtotal 與 store_credit_max。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct
from app.modules.menu.models import MenuItem
from app.modules.sales.models import SaleLine
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import SaleLineType, StoreCreditSourceType, UserRole


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


async def _seed(session: AsyncSession) -> tuple[str, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _menu_item(session: AsyncSession, store_id: int, *, name: str, price: str) -> int:
    item = MenuItem(store_id=store_id, name=name, unit_price=Decimal(price))
    session.add(item)
    await session.flush()
    return item.id


async def _catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    p = CatalogProduct(
        store_id=store_id,
        sku="SKU1",
        name="二手雜物",
        unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(p)
    await session.flush()
    return p.id


async def _member_with_credit(
    session: AsyncSession, store_id: int, clerk_id: int, balance: int
) -> int:
    member = Contact(store_id=store_id, name="會員", roles=["MEMBER"], national_id_enc="enc")
    session.add(member)
    await session.flush()
    acq_id = await session.scalar(
        text(
            "INSERT INTO acquisitions"
            " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
            "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
            "  created_at, updated_at)"
            " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, :amt, now(), now())"
            " RETURNING id"
        ),
        {"sid": store_id, "cid": member.id, "uid": clerk_id, "amt": balance},
    )
    await session.execute(
        text(
            "INSERT INTO serialized_items"
            " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
            "  listed_price, acquisition_id, created_at, updated_at)"
            " VALUES (:sid, :code, '收購品', 'A', 'OWNED', :amt, :amt, :aid, now(), now())"
        ),
        {"sid": store_id, "code": f"SC-{member.id}", "amt": balance, "aid": acq_id},
    )
    await StoreCreditService(session).credit(
        store_id,
        member.id,
        cash_equivalent=Decimal(balance),
        premium_rate=Decimal("0"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=acq_id,
        created_by=clerk_id,
    )
    return member.id


def _auth(token: str, idem: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": idem}


def _menu_line(menu_item_id: int, qty: int) -> dict[str, object]:
    return {"line_type": "MENU", "menu_item_id": menu_item_id, "qty": qty}


async def test_menu_only_sale(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed(db_session)
    coffee = await _menu_item(db_session, store_id, name="手沖-耶加", price="180")
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_menu_line(coffee, 2)]}, headers=_auth(token, "m1")
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == "360"
    line = body["lines"][0]
    assert line["line_type"] == "MENU"
    assert line["menu_item_id"] == coffee
    assert line["discount_amount"] == "0"


async def test_mixed_secondhand_and_menu(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    coffee = await _menu_item(db_session, store_id, name="拿鐵", price="150")
    cat = await _catalog(db_session, store_id, price="200", qty=5)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": cat, "qty": 1},
                _menu_line(coffee, 1),
            ]
        },
        headers=_auth(token, "mix1"),
    )
    assert resp.status_code == 201
    assert resp.json()["total"] == "350"
    types = {ln["line_type"] for ln in resp.json()["lines"]}
    assert types == {"CATALOG", "MENU"}


async def test_store_credit_cannot_cover_menu(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id = await _seed(db_session)
    member = await _member_with_credit(db_session, store_id, clerk_id, 1000)
    coffee = await _menu_item(db_session, store_id, name="手沖", price="180")
    cat = await _catalog(db_session, store_id, price="200", qty=5)
    # total=380、餐飲=180 → 購物金最多 200。試圖用 300 購物金 → 422。
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": cat, "qty": 1},
                _menu_line(coffee, 1),
            ],
            "buyer_contact_id": member,
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "300"},
                {"tender_type": "CASH", "amount": "80"},
            ],
        },
        headers=_auth(token, "sc-over"),
    )
    assert resp.status_code == 422
    assert "購物金" in resp.json()["detail"]


async def test_store_credit_up_to_nonfood_ok(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id = await _seed(db_session)
    member = await _member_with_credit(db_session, store_id, clerk_id, 1000)
    coffee = await _menu_item(db_session, store_id, name="手沖", price="180")
    cat = await _catalog(db_session, store_id, price="200", qty=5)
    # total=380、餐飲=180 → 購物金 200（=非餐飲）OK、餐飲 180 現金。
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": cat, "qty": 1},
                _menu_line(coffee, 1),
            ],
            "buyer_contact_id": member,
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "200"},
                {"tender_type": "CASH", "amount": "180"},
            ],
        },
        headers=_auth(token, "sc-ok"),
    )
    assert resp.status_code == 201
    assert await StoreCreditService(db_session).get_balance(store_id, member) == Decimal(800)


async def test_member_points_exclude_menu(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id = await _seed(db_session)
    member = await _member_with_credit(db_session, store_id, clerk_id, 1000)
    coffee = await _menu_item(db_session, store_id, name="手沖", price="500")
    cat = await _catalog(db_session, store_id, price="300", qty=5)
    # total=800、餐飲=500 → 可累點基數=300 → floor(300/100)=3 點。
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": cat, "qty": 1},
                _menu_line(coffee, 1),
            ],
            "buyer_contact_id": member,
        },
        headers=_auth(token, "pts1"),
    )
    assert resp.status_code == 201
    contact = await db_session.scalar(select(Contact).where(Contact.id == member))
    assert contact is not None
    assert contact.member_points == 3


async def test_campaign_discount_not_applied_to_menu(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id = await _seed(db_session)
    coffee = await _menu_item(db_session, store_id, name="手沖", price="200")
    # 開一檔九折、品項全開（含 catalog）的生效活動。
    camp_svc = CampaignService(db_session)
    camp = await camp_svc.create_campaign(
        store_id,
        name="九折",
        discount_pct=10,
        starts_at=datetime.now(UTC) - timedelta(days=1),
        ends_at=datetime.now(UTC) + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=True,
        applies_consignment=False,
        created_by=clerk_id,
    )
    await camp_svc.activate(store_id, camp.id, actor_user_id=clerk_id)
    await db_session.flush()
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_menu_line(coffee, 1)]}, headers=_auth(token, "camp1")
    )
    assert resp.status_code == 201
    # 餐飲不折：仍為原價 200、無折讓、無 campaign 留痕。
    assert resp.json()["total"] == "200"
    line = await db_session.scalar(
        select(SaleLine).where(SaleLine.line_type == SaleLineType.MENU)
    )
    assert line is not None
    assert line.discount_amount == Decimal(0)
    assert line.campaign_id is None


async def test_quote_returns_food_subtotal_and_credit_max(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    coffee = await _menu_item(db_session, store_id, name="手沖", price="180")
    cat = await _catalog(db_session, store_id, price="200", qty=5)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": cat, "qty": 1},
                _menu_line(coffee, 1),
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == "380"
    assert body["food_subtotal"] == "180"
    assert body["store_credit_max"] == "200"
