"""SC-5b §5B 效益指標報表整合測試（docs/16 §5B）：直接量測/估計值、α 代理、匯出、權限。

銷售/收款以 raw SQL 直插（DEFERRABLE 收款守衛於真正 COMMIT 才驗，測試以 savepoint+rollback
隔離，不觸發；金額皆構造為自洽，符合領域語意）。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import StoreCreditSourceType, UserRole

EFF_URL = "/api/v1/reports/store-credit/effectiveness"
WIDE_RANGE = {"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"}


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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員甲", roles=["MEMBER"])
    session.add_all([mgr, clerk, member])
    await session.flush()
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return mgr_token, clerk_token, store.id, member.id, mgr.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _add_acquisition(
    session: AsyncSession, store_id: int, uid: int, contact_id: int, method: str
) -> None:
    # 依 payout 形狀 CHECK 構造金額：STORE_CREDIT→只購物金等值；CASH→現金=總付出。
    cash = 0 if method == "STORE_CREDIT" else 100
    credit = 100 if method == "STORE_CREDIT" else 0
    await session.execute(
        text(
            "INSERT INTO acquisitions"
            " (store_id, type, contact_id, clerk_user_id, total_cash_paid, payout_method,"
            "  payout_cash_amount, payout_credit_cash_equivalent, created_at, updated_at)"
            " VALUES (:sid, 'BUYOUT', :cid, :uid, :cash, :m, :cash, :credit, now(), now())"
        ),
        {
            "sid": store_id,
            "cid": contact_id,
            "uid": uid,
            "m": method,
            "cash": cash,
            "credit": credit,
        },
    )


async def test_take_rate_premium_and_alpha(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    # take_rate：1 筆購物金 + 1 筆現金 → 0.5。
    await _add_acquisition(db_session, store_id, mgr_id, member_id, "STORE_CREDIT")
    await _add_acquisition(db_session, store_id, mgr_id, member_id, "CASH")
    # avg_premium：cash_equivalent 1000、溢價 0.1 → signed 1100 → (1100−1000)/1000 = 0.1。
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.1"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )
    # 一筆兌付（DEBIT）→ α 代理：會員無歷史消費 → 新增傾向高 → α=1.0、樣本不足。
    await svc.debit(
        store_id,
        member_id,
        amount=Decimal(200),
        source_type=StoreCreditSourceType.SALE,
        source_id=1,
        created_by=mgr_id,
    )

    resp = await client.get(EFF_URL, params=WIDE_RANGE, headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["take_rate"] == "0.5"
    assert body["avg_premium_rate"] == "0.1"
    assert body["alpha_incremental"] == "1"
    assert body["redemption_count"] == 1
    assert body["alpha_sample_insufficient"] is True
    # 剛入帳 → 未滿 180 天 → β 無樣本 → null；Δ 連帶 null。
    assert body["beta_retention"] is None
    assert body["delta_per_1000"] is None
    assert set(body["estimate_fields"]) == {"beta_retention", "alpha_incremental", "delta_per_1000"}
    assert "代理法" in body["alpha_method_note"]


async def test_gross_margin_and_excess_spend(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    # 自有序號品售出：售價 1000、成本 600 → 買斷毛利 400、收入 1000 → m=0.4。
    sale_id = await db_session.scalar(
        text(
            "INSERT INTO sales (store_id, clerk_user_id, subtotal, tax, total, created_at,"
            " updated_at) VALUES (:sid, :uid, 952, 48, 1000, now(), now()) RETURNING id"
        ),
        {"sid": store_id, "uid": mgr_id},
    )
    item_id = await db_session.scalar(
        text(
            "INSERT INTO serialized_items (store_id, item_code, name, grade, ownership_type,"
            " acquisition_cost, listed_price, status, created_at, updated_at)"
            " VALUES (:sid, 'IT-1', '帳篷', 'A', 'OWNED', 600, 1000, 'SOLD', now(), now())"
            " RETURNING id"
        ),
        {"sid": store_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO sale_lines (store_id, sale_id, line_type, serialized_item_id,"
            " description, qty, unit_price, line_total, created_at, updated_at)"
            " VALUES (:sid, :sale, 'SERIALIZED', :item, '帳篷', 1, 1000, 1000, now(), now())"
        ),
        {"sid": store_id, "sale": sale_id, "item": item_id},
    )
    # 含購物金 tender 的銷售：total 1000 = 購物金 400 + 現金 600 → excess_spend=600/1000=0.6。
    es_sale = await db_session.scalar(
        text(
            "INSERT INTO sales (store_id, clerk_user_id, buyer_contact_id, subtotal, tax, total,"
            " created_at, updated_at) VALUES (:sid, :uid, :cid, 952, 48, 1000, now(), now())"
            " RETURNING id"
        ),
        {"sid": store_id, "uid": mgr_id, "cid": member_id},
    )
    for ttype, amount in (("STORE_CREDIT", 400), ("CASH", 600)):
        await db_session.execute(
            text(
                "INSERT INTO sale_tenders (store_id, sale_id, tender_type, amount, created_at,"
                " updated_at) VALUES (:sid, :sale, :t, :amt, now(), now())"
            ),
            {"sid": store_id, "sale": es_sale, "t": ttype, "amt": amount},
        )

    resp = await client.get(EFF_URL, params=WIDE_RANGE, headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gross_margin_m"] == "0.4"
    assert body["excess_spend_rate"] == "0.6"


async def test_margin_includes_bulk_and_consignment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """毛利涵蓋自有散裝（每件成本×數量）與寄售序號（收入計入、店家收入認抽成）。"""
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    sale_id = await db_session.scalar(
        text(
            "INSERT INTO sales (store_id, clerk_user_id, subtotal, tax, total, created_at,"
            " updated_at) VALUES (:sid, :uid, 952, 48, 1000, now(), now()) RETURNING id"
        ),
        {"sid": store_id, "uid": mgr_id},
    )
    # 自有散裝：每件成本 = 300/10 = 30；售 5 件 → 成本 150、售價 500 → 毛利 350。
    lot_id = await db_session.scalar(
        text(
            "INSERT INTO bulk_lots (store_id, lot_code, name, grade, acquisition_cost,"
            " acquisition_basis, unit_price, total_qty, remaining_qty, status, created_at,"
            " updated_at) VALUES (:sid, 'LOT-1', '雜物', 'E', 300, 'BAG', 100, 10, 5,"
            " 'ON_SALE', now(), now()) RETURNING id"
        ),
        {"sid": store_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO sale_lines (store_id, sale_id, line_type, bulk_lot_id, description,"
            " qty, unit_price, line_total, created_at, updated_at)"
            " VALUES (:sid, :sale, 'BULK_LOT', :lot, '雜物', 5, 100, 500, now(), now())"
        ),
        {"sid": store_id, "sale": sale_id, "lot": lot_id},
    )
    # 寄售序號：售價 500 計入收入；店家收入認抽成 250。
    item_id = await db_session.scalar(
        text(
            "INSERT INTO serialized_items (store_id, item_code, name, grade, ownership_type,"
            " consignor_id, commission_pct, listed_price, status, created_at, updated_at)"
            " VALUES (:sid, 'CON-1', '寄售帳篷', 'A', 'CONSIGNMENT', :cid, 50, 500, 'SOLD',"
            " now(), now()) RETURNING id"
        ),
        {"sid": store_id, "cid": member_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO sale_lines (store_id, sale_id, line_type, serialized_item_id,"
            " description, qty, unit_price, line_total, created_at, updated_at)"
            " VALUES (:sid, :sale, 'SERIALIZED', :item, '寄售帳篷', 1, 500, 500, now(), now())"
        ),
        {"sid": store_id, "sale": sale_id, "item": item_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO consignment_settlements (store_id, serialized_item_id, sale_id, gross,"
            " commission_pct, commission_amount, payout_amount, status, created_at, updated_at)"
            " VALUES (:sid, :item, :sale, 500, 50, 250, 250, 'PENDING', now(), now())"
        ),
        {"sid": store_id, "item": item_id, "sale": sale_id},
    )

    resp = await client.get(EFF_URL, params=WIDE_RANGE, headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    # 收入 = 500(散裝)+500(寄售) = 1000；分子 = 350(買斷)+250(抽成) = 600 → m = 0.6。
    assert resp.json()["gross_margin_m"] == "0.6"


async def test_effectiveness_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _mgr, clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get(EFF_URL, params=WIDE_RANGE, headers=_auth(clerk))
    assert resp.status_code == 403


async def test_effectiveness_rejects_bad_range(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get(
        EFF_URL,
        params={"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422


async def test_effectiveness_csv_export(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    await _add_acquisition(db_session, store_id, mgr_id, member_id, "STORE_CREDIT")
    resp = await client.get(
        EFF_URL, params={**WIDE_RANGE, "format": "csv"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    text_body = resp.content.decode("utf-8-sig")
    assert "選用率 take_rate" in text_body
    assert "代理法" in text_body  # α 欄標示估計值（代理法）
    assert "α 代理法說明" in text_body
