"""收購作廢整合測試（F6.5）：對稱反轉庫存/現金/購物金、硬性擋下、權限、併發。

作廢端點限 MANAGER；現金反轉記 ACQUISITION_VOID_IN（落當前開帳 session）；硬擋
已售出庫存／購物金已花用沖回會負／付現但無開帳；重複作廢 409；跨店 404。
"""

import itertools
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    BulkLotStatus,
    CashMovementType,
    SerializedItemStatus,
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


_idem = itertools.count()


async def _seed(
    db: AsyncSession, *, member: bool = True, open_drawer: bool = True
) -> tuple[str, str, int, int]:
    """建店/店員/經理/(會員)賣方，回 (clerk_token, manager_token, store_id, contact_id)。"""
    store = Store(name="門市")
    db.add(store)
    await db.flush()
    clerk = User(
        store_id=store.id, username=f"clk{store.id}", password_hash="h", role=UserRole.CLERK
    )
    mgr = User(
        store_id=store.id, username=f"mgr{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    roles = ["SELLER", "MEMBER"] if member else ["SELLER"]
    seller = Contact(store_id=store.id, name="賣方", roles=roles, national_id_enc="enc")
    db.add_all([clerk, mgr, seller])
    await db.flush()
    if open_drawer:
        await CashDrawerService(db).open_session(store.id, clerk.id, Decimal("5000"))
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        store.id,
        seller.id,
    )


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idem if idem is not None else f"void-key-{next(_idem)}",
    }


async def _clerk_id(db: AsyncSession, store_id: int) -> int:
    """該店店員的真實 user_id（供直接呼叫 service 時的 closed_by/created_by/opened_by）。"""
    uid = await db.scalar(
        select(User.id).where(User.store_id == store_id, User.role == UserRole.CLERK)
    )
    assert uid is not None
    return uid


def _buyout(contact_id: int, **payout: object) -> dict[str, object]:
    return {
        "type": "BUYOUT",
        "contact_id": contact_id,
        "items": [
            {"name": "帳篷", "grade": "A", "acquisition_cost": "1000", "listed_price": "1800"}
        ],
        **payout,
    }


async def _create_buyout(
    client: httpx.AsyncClient, token: str, contact_id: int, **payout: object
) -> int:
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout(contact_id, **payout), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["acquisition_id"])


async def _movement_sum(db: AsyncSession, store_id: int, mtype: CashMovementType) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.store_id == store_id, CashMovement.type == mtype
        )
    )
    return Decimal(total if total is not None else 0)


async def _void_in(db: AsyncSession, store_id: int) -> Decimal:
    """作廢退款（ACQUISITION_VOID_IN 進帳）總額。"""
    return await _movement_sum(db, store_id, CashMovementType.ACQUISITION_VOID_IN)


# ── 成功路徑 ──


async def test_void_cash_buyout_reverses_cash_and_unstocks(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)  # CASH 預設

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "打錯金額"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reversed_cash"] == "1000"
    assert body["reversed_credit"] == "0"
    # 現金退回（ACQUISITION_VOID_IN 進帳）
    assert await _void_in(db_session, store_id) == Decimal("1000")
    # 庫存退場（WRITTEN_OFF）
    items = (
        await db_session.scalars(
            select(SerializedItem).where(SerializedItem.acquisition_id == acq_id)
        )
    ).all()
    assert items and all(it.status == SerializedItemStatus.WRITTEN_OFF for it in items)
    # 收購標記作廢
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None and acq.voided_at is not None and acq.voided_by is not None


async def test_void_reflected_in_cash_reconciliation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """對帳：BUYOUT_OUT 出帳 1000、作廢 ACQUISITION_VOID_IN 進帳 1000 → expected 回到開帳零用金。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    cash = CashDrawerService(db_session)
    session = await cash.get_current_session(store_id)
    assert session is not None
    expected = await cash.expected_amount(session)
    assert expected == Decimal("5000")  # 5000 − 1000(BUYOUT_OUT) + 1000(VOID_IN)


async def test_void_store_credit_buyout_reverses_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session, open_drawer=False)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    sc = StoreCreditService(db_session)
    assert await sc.get_balance(store_id, seller_id) > 0

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "賣方反悔"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    assert await sc.get_balance(store_id, seller_id) == Decimal(0)  # 沖回到 0
    # 無現金異動
    assert await _void_in(db_session, store_id) == Decimal(0)


async def test_void_bulk_lot_writes_off(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    payload = {
        "type": "BULK_LOT",
        "contact_id": seller_id,
        "lot": {
            "name": "雜物",
            "grade": "E",
            "acquisition_cost": "800",
            "acquisition_basis": "BAG",
            "total_qty": 10,
            "unit_price": "100",
        },
    }
    resp = await client.post("/api/v1/acquisitions", json=payload, headers=_auth(clerk))
    assert resp.status_code == 201, resp.text
    acq_id = int(resp.json()["acquisition_id"])

    void = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert void.status_code == 200, void.text
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.acquisition_id == acq_id))
    assert lot is not None and lot.status == BulkLotStatus.WRITTEN_OFF and lot.remaining_qty == 0


# ── 硬性擋下 ──


async def test_void_blocked_when_item_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.acquisition_id == acq_id)
    )
    assert item is not None
    await InventoryService(db_session).sell_serialized_item(item.id)  # 售出

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 409
    # 整筆不動：仍無退款現金、收購未作廢
    assert await _void_in(db_session, store_id) == Decimal(0)
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None and acq.voided_at is None


async def test_void_blocked_when_credit_spent(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session, open_drawer=False)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    sc = StoreCreditService(db_session)
    # 花掉部分購物金（模擬消費）：沖回會使餘額為負
    await sc.debit(
        store_id,
        seller_id,
        amount=Decimal("100"),
        source_type=StoreCreditSourceType.SALE,
        source_id=999,
        created_by=await _clerk_id(db_session, store_id),
    )
    # commit 讓消費落在 savepoint 之外：void 失敗回滾只回退作廢本身、不清掉這筆消費
    await db_session.commit()
    balance_before = await sc.get_balance(store_id, seller_id)

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 409
    assert await sc.get_balance(store_id, seller_id) == balance_before  # 未動


async def test_void_cash_buyout_blocked_without_open_session(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)  # 開帳建單
    acq_id = await _create_buyout(client, clerk, seller_id)
    # 關帳：之後無開帳 session 可收退款
    cash = CashDrawerService(db_session)
    session = await cash.get_current_session(store_id)
    assert session is not None
    uid = await _clerk_id(db_session, store_id)
    await cash.close_session(session, Decimal("4000"), closed_by=uid)

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 409
    assert "開帳" in resp.json()["detail"]


# ── 冪等／權限／跨店 ──


async def test_double_void_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    first = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert second.status_code == 409
    # 不雙重沖回：只一筆退款進帳
    assert await _void_in(db_session, store_id) == Decimal("1000")


async def test_void_requires_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(clerk)
    )
    assert resp.status_code == 403


async def test_void_reason_required(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": ""}, headers=_auth(mgr)
    )
    assert resp.status_code == 422


async def test_void_not_found_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _clerk, mgr, _store_id, _seller_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/acquisitions/99999/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 404


async def test_void_cross_store_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk_a, _mgr_a, _store_a, seller_a = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk_a, seller_a)
    # B 店經理（另一 store）不可作廢 A 店收購 → 查無（store 範圍）
    _clerk_b, mgr_b, _store_b, _seller_b = await _seed(db_session)
    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr_b)
    )
    assert resp.status_code == 404


async def test_void_closed_session_lands_in_current_open(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """原開帳已關，作廢退款落『當前』開帳 session（現行紅字，不改歷史）。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    cash = CashDrawerService(db_session)
    uid = await _clerk_id(db_session, store_id)
    s1 = await cash.get_current_session(store_id)
    assert s1 is not None
    await cash.close_session(s1, Decimal("4000"), closed_by=uid)
    # 開新班
    s2 = await cash.open_session(store_id, uid, Decimal("3000"))

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    # 退款落在 s2（當前開帳）
    void_in = await db_session.scalar(
        select(CashMovement).where(CashMovement.type == CashMovementType.ACQUISITION_VOID_IN)
    )
    assert void_in is not None and void_in.session_id == s2.id
