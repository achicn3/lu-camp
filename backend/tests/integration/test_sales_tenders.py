"""SC-3 銷售多 tender + 作廢沖正（docs/16 §1.6/§3.2/§3.3）。

驗證：CASH/STORE_CREDIT/MIXED 收款、Σ tenders = total、購物金扣抵走帳本 DEBIT、
現金部分走錢櫃 SALE_IN（非全額，I-9）、餘額不足整筆回滾、作廢沖回購物金、
冪等含收款組成、純購物金不要求開帳。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.models import SaleTender
from app.modules.store.models import Store
from app.modules.storecredit.models import StoreCreditLedger
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    StoreCreditEntryType,
    StoreCreditSourceType,
    UserRole,
)


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


async def _seed(session: AsyncSession, *, open_drawer: bool = True) -> tuple[str, str, int, int]:
    """建店+店員+經理（預設開帳），回 (clerk_token, manager_token, store_id, clerk_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    if open_drawer:
        await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    return clerk_token, mgr_token, store.id, clerk.id


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id, sku="SKU1", name="飲料", unit_price=Decimal(price), quantity_on_hand=qty
    )
    session.add(product)
    await session.flush()
    return product.id


async def _seed_member_with_credit(
    session: AsyncSession, store_id: int, clerk_id: int, balance: int
) -> int:
    """建會員並以「完整背書收購」入帳 balance（premium 0 → 餘額 = balance）。

    帶真實 acquisition header + serialized_item，使 SC-2 雙向綁定/背書守衛於 COMMIT 也成立
    （real-commit 測試需要；savepoint 測試亦無妨）。
    """
    member = Contact(store_id=store_id, name="會員", roles=["MEMBER"], national_id_enc="enc")
    session.add(member)
    await session.flush()
    acq_id = await session.scalar(
        text(
            "INSERT INTO acquisitions"
            " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
            "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
            "  created_at, updated_at)"
            " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, :amt,"
            "  now(), now()) RETURNING id"
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
        {"sid": store_id, "code": f"SC-CRED-{member.id}", "amt": balance, "aid": acq_id},
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


def _catalog_line(catalog_id: int, qty: int) -> dict[str, object]:
    return {"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": qty}


async def test_default_no_tenders_is_single_cash(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """省略 tenders → 單一 CASH 全額（向後相容）：payment_method=CASH、一筆 tender。"""
    token, _mgr, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 2)]}, headers=_auth(token, "k1")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payment_method"] == "CASH"
    assert body["total"] == "200"
    assert len(body["tenders"]) == 1
    assert body["tenders"][0]["tender_type"] == "CASH"
    assert body["tenders"][0]["amount"] == "200"


async def test_full_store_credit_tender(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """純購物金付款：帳本 DEBIT、餘額減少、不產生現金異動（I-9）、payment_method=STORE_CREDIT。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 2)],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}],
        },
        headers=_auth(token, "sc1"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payment_method"] == "STORE_CREDIT"
    sale_id = body["id"]
    # 餘額 500 → 300
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal(300)
    # 帳本有一筆 DEBIT/SALE
    debit = await db_session.scalar(
        select(StoreCreditLedger).where(
            StoreCreditLedger.source_type == StoreCreditSourceType.SALE,
            StoreCreditLedger.source_id == sale_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.DEBIT,
        )
    )
    assert debit is not None and debit.signed_amount == Decimal(-200)
    # 不產生 SALE_IN 現金異動
    cash_count = await db_session.scalar(
        select(func.count())
        .select_from(CashMovement)
        .where(CashMovement.ref_type == "sale", CashMovement.ref_id == sale_id)
    )
    assert cash_count == 0


async def test_mixed_tender_splits_cash_and_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """混合付款：現金部分入錢櫃（非全額）、購物金部分扣帳本、payment_method=MIXED。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 5)],  # total 500
            "buyer_contact_id": member_id,
            "tenders": [
                {"tender_type": "CASH", "amount": "300"},
                {"tender_type": "STORE_CREDIT", "amount": "200"},
            ],
        },
        headers=_auth(token, "mix1"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payment_method"] == "MIXED"
    sale_id = body["id"]
    # 現金異動只記 300（現金部分）
    cash_amount = await db_session.scalar(
        select(CashMovement.amount).where(
            CashMovement.ref_type == "sale",
            CashMovement.ref_id == sale_id,
            CashMovement.type == CashMovementType.SALE_IN,
        )
    )
    assert cash_amount == Decimal(300)
    # 購物金扣 200 → 餘額 300
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal(300)


async def test_tender_sum_mismatch_422(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Σ tenders ≠ total → 422，整筆不成立。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 2)],  # total 200
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "150"}],
        },
        headers=_auth(token, "bad1"),
    )
    assert resp.status_code == 422, resp.text
    assert await db_session.scalar(select(func.count()).select_from(SaleTender)) == 0


async def test_store_credit_tender_without_buyer_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """購物金付款但無買方 → 422（扣誰的購物金）。"""
    token, _mgr, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 1)],
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "100"}],
        },
        headers=_auth(token, "nobuyer"),
    )
    assert resp.status_code == 422, resp.text


async def test_insufficient_store_credit_rolls_back_whole_sale() -> None:
    """購物金餘額不足 → 409，整筆回滾：不建單、不扣庫存、不扣購物金。

    用真交易（獨立 sessionmaker + 真 commit）驗回滾後狀態——共用 savepoint 的 db_session
    被 router rollback 連種子一起丟，無法區分。"""
    import app.core.db as app_db

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        token, _mgr, store_id, clerk_id = await _seed(s)
        cat = await _seed_catalog(s, store_id, price="100", qty=10)
        member_id = await _seed_member_with_credit(s, store_id, clerk_id, 100)
        await s.commit()

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/sales",
                json={
                    "lines": [_catalog_line(cat, 5)],  # total 500 > 餘額 100
                    "buyer_contact_id": member_id,
                    "tenders": [{"tender_type": "STORE_CREDIT", "amount": "500"}],
                },
                headers=_auth(token, "insuf"),
            )
        assert resp.status_code == 409, resp.text
        async with sm() as s:
            # 整筆回滾：庫存未扣（仍 10）、餘額仍 100、無 sale/tender
            cat_row = await s.get(CatalogProduct, cat)
            assert cat_row is not None and cat_row.quantity_on_hand == 10
            assert await StoreCreditService(s).get_balance(store_id, member_id) == Decimal(100)
            assert await s.scalar(select(func.count()).select_from(SaleTender)) == 0
    finally:
        async with sm() as s:
            from sqlalchemy import delete

            from app.modules.cashdrawer.models import CashSession

            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(
                text("DELETE FROM serialized_items WHERE store_id = :s"), {"s": store_id}
            )
            await s.execute(text("DELETE FROM acquisitions WHERE store_id = :s"), {"s": store_id})
            await s.execute(delete(CatalogProduct).where(CatalogProduct.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_duplicate_tender_type_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一種 tender_type 出現兩次 → 422。"""
    token, _mgr, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 2)],
            "tenders": [
                {"tender_type": "CASH", "amount": "100"},
                {"tender_type": "CASH", "amount": "100"},
            ],
        },
        headers=_auth(token, "dup"),
    )
    assert resp.status_code == 422, resp.text


async def test_pure_store_credit_needs_no_open_drawer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """純購物金付款不碰現金（I-9）→ 未開帳也可結帳。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session, open_drawer=False)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 2)],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}],
        },
        headers=_auth(token, "nodrawer"),
    )
    assert resp.status_code == 201, resp.text


async def test_cash_tender_still_needs_open_drawer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """含現金 tender 仍要求開帳：未開帳 → 409。"""
    token, _mgr, store_id, _ = await _seed(db_session, open_drawer=False)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, "needdrawer")
    )
    assert resp.status_code == 409, resp.text


async def test_void_reverses_store_credit_tender(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """作廢購物金銷售：REVERSAL 入回餘額、點數沖回。"""
    token, mgr_token, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    created = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_catalog_line(cat, 2)],  # total 200
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}],
        },
        headers=_auth(token, "void-sc"),
    )
    assert created.status_code == 201, created.text
    sale_id = created.json()["id"]
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal(300)

    voided = await client.post(
        f"/api/v1/sales/{sale_id}/void", headers={"Authorization": f"Bearer {mgr_token}"}
    )
    assert voided.status_code == 200, voided.text
    # 購物金入回 → 餘額回到 500
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal(500)
    # 帳本有一筆 REVERSAL/SALE_VOID
    rev = await db_session.scalar(
        select(StoreCreditLedger).where(
            StoreCreditLedger.source_type == StoreCreditSourceType.SALE_VOID,
            StoreCreditLedger.source_id == sale_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.REVERSAL,
        )
    )
    assert rev is not None and rev.signed_amount == Decimal(200)


async def test_idempotent_replay_with_tenders_debits_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 key 重送（含相同收款組成）→ 回原單、購物金只扣一次。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    payload = {
        "lines": [_catalog_line(cat, 2)],
        "buyer_contact_id": member_id,
        "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}],
    }
    first = await client.post("/api/v1/sales", json=payload, headers=_auth(token, "idem-sc"))
    second = await client.post("/api/v1/sales", json=payload, headers=_auth(token, "idem-sc"))
    assert first.status_code == 201
    assert second.status_code in (200, 201)
    assert first.json()["id"] == second.json()["id"]
    # 只扣一次 → 餘額 300
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal(300)


async def test_zero_total_sale_rejected_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """零總額銷售 → 422（不可落到 amount=0 的 tender CHECK 違反/500）。"""
    token, _mgr, store_id, _ = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="0", qty=10)
    resp = await client.post(
        "/api/v1/sales", json={"lines": [_catalog_line(cat, 1)]}, headers=_auth(token, "zero")
    )
    assert resp.status_code == 422, resp.text
    assert await db_session.scalar(select(func.count()).select_from(SaleTender)) == 0


async def test_tender_order_does_not_affect_idempotency(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收款明細順序不影響冪等指紋：同 key、相同組成但順序顛倒 → 回原單（非 409）。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    base = {"lines": [_catalog_line(cat, 5)], "buyer_contact_id": member_id}  # total 500
    first = await client.post(
        "/api/v1/sales",
        json={
            **base,
            "tenders": [
                {"tender_type": "CASH", "amount": "300"},
                {"tender_type": "STORE_CREDIT", "amount": "200"},
            ],
        },
        headers=_auth(token, "order"),
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/api/v1/sales",
        json={
            **base,
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "200"},
                {"tender_type": "CASH", "amount": "300"},
            ],
        },
        headers=_auth(token, "order"),
    )
    assert second.status_code in (200, 201), second.text
    assert first.json()["id"] == second.json()["id"]


async def test_db_guard_rejects_unbalanced_tenders_at_commit() -> None:
    """DB 對平守衛（第一輪 P3）：直插使 Σ tenders ≠ sales.total → COMMIT 被擋。"""
    import app.core.db as app_db

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="對平店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="bal", password_hash="h", role=UserRole.CLERK)
        s.add(clerk)
        await s.flush()
        store_id, clerk_id = store.id, clerk.id
        await s.commit()

    try:
        async with sm() as s:
            sale_id = await s.scalar(
                text(
                    "INSERT INTO sales"
                    " (store_id, clerk_user_id, subtotal, tax, total, awarded_points,"
                    "  payment_method, invoice_status, status, created_at, updated_at)"
                    " VALUES (:sid, :uid, 190, 10, 200, 0, 'CASH', 'NOT_ISSUED', 'COMPLETED',"
                    "  now(), now()) RETURNING id"
                ),
                {"sid": store_id, "uid": clerk_id},
            )
            # 只插 150 的收款，與 total 200 不對平
            await s.execute(
                text(
                    "INSERT INTO sale_tenders (store_id, sale_id, tender_type, amount,"
                    "  created_at, updated_at)"
                    " VALUES (:sid, :saleid, 'CASH', 150, now(), now())"
                ),
                {"sid": store_id, "saleid": sale_id},
            )
            with pytest.raises(DBAPIError):
                await s.commit()
    finally:
        async with sm() as s:
            from sqlalchemy import delete

            await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
            await s.execute(text("DELETE FROM sales WHERE store_id = :s"), {"s": store_id})
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_same_key_different_tenders_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 key 但收款組成不同 → 409（收款組成納入指紋）。"""
    token, _mgr, store_id, clerk_id = await _seed(db_session)
    cat = await _seed_catalog(db_session, store_id, price="100", qty=10)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 500)
    base = {"lines": [_catalog_line(cat, 2)], "buyer_contact_id": member_id}
    first = await client.post(
        "/api/v1/sales",
        json={**base, "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}]},
        headers=_auth(token, "samekey"),
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/sales",
        json={**base, "tenders": [{"tender_type": "CASH", "amount": "200"}]},
        headers=_auth(token, "samekey"),
    )
    assert second.status_code == 409, second.text
