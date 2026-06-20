"""收購作廢整合測試（F6.5）：對稱反轉庫存/現金/購物金、硬性擋下、權限、併發。

作廢端點限 MANAGER；現金反轉記 ACQUISITION_VOID_IN（落當前開帳 session）；硬擋
已售出庫存／購物金已花用沖回會負／付現但無開帳；重複作廢 409；跨店 404。
"""

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.store.models import Store
from app.modules.storecredit.repository import StoreCreditRepository
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    BulkLotStatus,
    CashMovementType,
    PayoutMethod,
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


async def test_double_void_returns_409(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
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


async def test_void_requires_manager(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    clerk, _mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(clerk)
    )
    assert resp.status_code == 403


async def test_void_reason_required(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    empty = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": ""}, headers=_auth(mgr)
    )
    assert empty.status_code == 422
    # 純空白字元也須擋下（通過 min_length 卻 strip 後為空；Codex F6.5）
    blank = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "   "}, headers=_auth(mgr)
    )
    assert blank.status_code == 422


async def test_void_not_found_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _clerk, mgr, _store_id, _seller_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/acquisitions/99999/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 404


async def test_void_cross_store_is_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
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


# ── 寄售類型不支援作廢（Codex 高風險①）──


async def test_void_consignment_is_unsupported_422_no_side_effects(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """寄售收購不支援作廢（寄售品仍屬寄售人、不在 OWNED 庫存讀層）→ 422 且零副作用。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session, open_drawer=False)
    payload = {
        "type": "CONSIGNMENT",
        "contact_id": seller_id,
        "items": [{"name": "睡袋", "grade": "B", "listed_price": "1200", "commission_pct": 50}],
    }
    create = await client.post("/api/v1/acquisitions", json=payload, headers=_auth(clerk))
    assert create.status_code == 201, create.text
    acq_id = int(create.json()["acquisition_id"])

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422, resp.text
    # 零副作用：寄售品仍在庫、收購未作廢、無退款、無稽核
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.acquisition_id == acq_id)
    )
    assert item is not None and item.status == SerializedItemStatus.IN_STOCK
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None and acq.voided_at is None
    assert await _void_in(db_session, store_id) == Decimal(0)
    audit_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.store_id == store_id, AuditLog.action == "VOID_ACQUISITION")
    )
    assert audit_count == 0


# ── 稽核不含 free-form 原因（避免 PII 落入不可變稽核；Codex ②）──


async def test_void_audit_excludes_free_form_reason(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """作廢原因可能含 PII（姓名/身分證/電話）；稽核 before/after 不得出現，僅存 void_reason 欄。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    reason = "賣方 王小明 A123456789 0912345678 反悔"

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": reason}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text

    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.store_id == store_id, AuditLog.action == "VOID_ACQUISITION")
    )
    assert audit is not None
    blob = f"{audit.before} {audit.after}"
    for pii in ("王小明", "A123456789", "0912345678", reason):
        assert pii not in blob
    # 原因仍保存在其設計歸屬欄位（acquisitions.void_reason）
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None and acq.void_reason == reason


async def test_void_reason_not_exposed_via_acquisition_read(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """GET /acquisitions/{id}（店員可讀）不得回傳 void_reason——自由文字可能含 PII（§5）。"""
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    reason = "賣方 王小明 A123456789 0912345678 反悔"
    void = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": reason}, headers=_auth(mgr)
    )
    assert void.status_code == 200, void.text

    # 店員讀取已作廢收購：voided_at 可見，但原因不外洩
    read = await client.get(f"/api/v1/acquisitions/{acq_id}", headers=_auth(clerk))
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["voided_at"] is not None
    assert "void_reason" not in body
    for pii in ("王小明", "A123456789", "0912345678"):
        assert pii not in read.text


# ── 作廢須涵蓋整批、不可只看分頁首頁（Codex 高風險②）──


async def _create_buyout_n_items(
    client: httpx.AsyncClient, token: str, contact_id: int, n: int
) -> int:
    items = [
        {"name": f"item{i}", "grade": "A", "acquisition_cost": "1", "listed_price": "2"}
        for i in range(n)
    ]
    resp = await client.post(
        "/api/v1/acquisitions",
        json={"type": "BUYOUT", "contact_id": contact_id, "items": items},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["acquisition_id"])


async def test_void_guard_catches_sold_item_beyond_first_page(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """201 件 BUYOUT：售出最小 id（最早建立）那件——舊分頁讀層 id.desc().limit(200) 會漏看；
    無分頁讀層應擋下 → 409，整筆不動。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout_n_items(client, clerk, seller_id, 201)
    first_item = await db_session.scalar(
        select(SerializedItem)
        .where(SerializedItem.acquisition_id == acq_id)
        .order_by(SerializedItem.id)
        .limit(1)
    )
    assert first_item is not None
    await InventoryService(db_session).sell_serialized_item(first_item.id)

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 409, resp.text
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None and acq.voided_at is None
    assert await _void_in(db_session, store_id) == Decimal(0)


async def test_void_writes_off_all_items_beyond_first_page(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """201 件 BUYOUT 作廢 → 全部 201 件 WRITTEN_OFF（不漏分頁第 2 頁）。"""
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout_n_items(client, clerk, seller_id, 201)

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    rows = (
        await db_session.scalars(
            select(SerializedItem).where(SerializedItem.acquisition_id == acq_id)
        )
    ).all()
    assert len(rows) == 201
    assert all(r.status == SerializedItemStatus.WRITTEN_OFF for r in rows)


# ── 已作廢收購不得污染 take_rate 報表（Codex ②）──


async def test_voided_acquisition_excluded_from_take_rate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已作廢的 STORE_CREDIT 收購撥款已沖回、零效果，不得計入 take_rate 分母。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session, open_drawer=False)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    repo = AcquisitionRepository(db_session)
    lo = datetime.now(UTC) - timedelta(days=1)
    hi = datetime.now(UTC) + timedelta(days=1)
    before = await repo.count_payouts_by_method(store_id, lo, hi)
    assert before.get(PayoutMethod.STORE_CREDIT, 0) >= 1  # 作廢前計入

    resp = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    after = await repo.count_payouts_by_method(store_id, lo, hi)
    assert after.get(PayoutMethod.STORE_CREDIT, 0) == 0  # 作廢後排除


async def test_voided_credit_excluded_from_liability_aging(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已作廢的 STORE_CREDIT 收購入帳不再列為帳齡發出列/Σ正向（_not_reversed；Codex F6.5）。

    同一會員另有一筆未作廢的有效購物金；作廢其一後，Σ正向應等於餘額（consumed=0，
    排除已沖正的原始 CREDIT），且發出列只剩未作廢那一筆。
    """
    clerk, mgr, store_id, seller_id = await _seed(db_session, open_drawer=False)
    voided = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")  # 有效、不作廢

    resp = await client.post(
        f"/api/v1/acquisitions/{voided}/void", json={"reason": "x"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text

    sc = StoreCreditService(db_session)
    balance = await sc.get_balance(store_id, seller_id)
    repo = StoreCreditRepository(db_session)
    psum = await repo.positive_sum_by_contact(store_id)
    seller_lots = [amt for c, amt, _ in await repo.positive_lots(store_id) if c == seller_id]
    assert psum.get(seller_id) == balance  # consumed=0：Σ正向==餘額（已排除作廢入帳）
    assert len(seller_lots) == 1  # 只剩未作廢那筆發出列
