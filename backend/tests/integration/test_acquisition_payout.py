"""收購撥款整合測試（SC-2；docs/16 §1.7／§3.1）。

CASH | STORE_CREDIT | SPLIT：現金部分走錢櫃（需開帳）、購物金部分入帳本
（需會員、套用當下 settings.premium_rate、與收購同一原子交易）。
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
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
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


async def _seed(
    db: AsyncSession, *, member: bool = True, open_drawer: bool = True
) -> tuple[str, int, int]:
    """建店/店員/(會員)賣方，回 (token, store_id, contact_id)。"""
    store = Store(name="門市")
    db.add(store)
    await db.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    roles = ["SELLER", "MEMBER"] if member else ["SELLER"]
    seller = Contact(store_id=store.id, name="賣方", roles=roles, national_id_enc="enc")
    db.add_all([clerk, seller])
    await db.flush()
    if open_drawer:
        await CashDrawerService(db).open_session(store.id, clerk.id, Decimal("5000"))
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, seller.id


_idem_counter = itertools.count()


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idem if idem is not None else f"payout-key-{next(_idem_counter)}",
    }


def _buyout_payload(contact_id: int, **payout: object) -> dict[str, object]:
    return {
        "type": "BUYOUT",
        "contact_id": contact_id,
        "items": [
            {
                "name": "帳篷",
                "grade": "A",
                "acquisition_cost": "1000",
                "listed_price": "1800",
            }
        ],
        **payout,
    }


async def test_full_store_credit_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """純購物金：不碰現金、不要求開帳；入帳套用 settings 溢價率（預設 0.10）。"""
    token, store_id, seller_id = await _seed(db_session, open_drawer=False)  # 未開帳
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id, payout_method="STORE_CREDIT"),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payout_method"] == "STORE_CREDIT"
    assert body["payout_cash_amount"] == "0"
    assert body["payout_credit_cash_equivalent"] == "1000"
    assert body["total_cash_paid"] == "0"
    # 帳本入帳 1100（1000 × 1.10）
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == Decimal(1100)
    # 零現金異動
    moves = await db_session.scalar(select(func.count()).select_from(CashMovement))
    assert moves == 0


async def test_split_payout(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """SPLIT：現金 400 走錢櫃、購物金 600（等值）入帳本（660）。"""
    token, store_id, seller_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id, payout_method="SPLIT", payout_split_cash="400"),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payout_cash_amount"] == "400"
    assert body["payout_credit_cash_equivalent"] == "600"
    assert body["total_cash_paid"] == "400"
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == Decimal(660)
    amount = await db_session.scalar(select(func.sum(CashMovement.amount)))
    assert amount is not None and abs(Decimal(amount)) == Decimal(400)  # 僅現金部分出帳


async def test_cash_payout_unchanged(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """預設 CASH：行為與既有相同（全額付現、無帳本入帳）。"""
    token, store_id, seller_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_payload(seller_id), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payout_method"] == "CASH"
    assert body["total_cash_paid"] == "1000"
    assert await StoreCreditService(db_session).get_balance(store_id, seller_id) == Decimal(0)


async def test_store_credit_requires_member(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """非會員選購物金 → 422，且整筆回滾（無收購單、無入庫）。"""
    token, _store_id, seller_id = await _seed(db_session, member=False, open_drawer=False)
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id, payout_method="STORE_CREDIT"),
        headers=_auth(token),
    )
    assert resp.status_code == 422
    count = await db_session.scalar(select(func.count()).select_from(Acquisition))
    assert count == 0  # 原子回滾：購物金失敗收購不成立


async def test_split_cash_must_be_less_than_total(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, seller_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id, payout_method="SPLIT", payout_split_cash="1000"),
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_consignment_rejects_payout_method(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, seller_id = await _seed(db_session, open_drawer=False)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "CONSIGNMENT",
            "contact_id": seller_id,
            "payout_method": "STORE_CREDIT",
            "items": [{"name": "帳篷", "grade": "A", "listed_price": "1800", "commission_pct": 50}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_store_credit_payout_retry_is_idempotent(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """重試同 key（Codex high）：回原收購單、不重複入庫/入購物金。"""
    token, store_id, seller_id = await _seed(db_session, open_drawer=False)
    payload = _buyout_payload(seller_id, payout_method="STORE_CREDIT")
    first = await client.post(
        "/api/v1/acquisitions", json=payload, headers=_auth(token, idem="acq-retry")
    )
    retry = await client.post(
        "/api/v1/acquisitions", json=payload, headers=_auth(token, idem="acq-retry")
    )
    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.json()["acquisition_id"] == first.json()["acquisition_id"]
    assert retry.json()["item_codes"] == first.json()["item_codes"]  # 識別碼重建一致
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == Decimal(1100)  # 只入帳一次
    count = await db_session.scalar(select(func.count()).select_from(Acquisition))
    assert count == 1


async def test_same_key_different_payload_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, seller_id = await _seed(db_session)
    await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id),
        headers=_auth(token, idem="acq-conflict"),
    )
    other = dict(_buyout_payload(seller_id))
    other["note"] = "不同內容"
    resp = await client.post(
        "/api/v1/acquisitions", json=other, headers=_auth(token, idem="acq-conflict")
    )
    assert resp.status_code == 409


async def test_missing_idempotency_key_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, seller_id = await _seed(db_session)
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_premium_rate_follows_settings(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """調整 settings.premium_rate → 入帳套用新值且記錄於分錄。"""
    token, store_id, seller_id = await _seed(db_session, open_drawer=False)
    from app.modules.settings.schemas import SettingsUpdateRequest

    await StoreSettingsService(db_session).update_settings(
        store_id,
        actor_user_id=None,
        patch=SettingsUpdateRequest(premium_rate=Decimal("0.1500")),
    )
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_payload(seller_id, payout_method="STORE_CREDIT"),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    assert await StoreCreditService(db_session).get_balance(store_id, seller_id) == Decimal(1150)
    entries = await StoreCreditService(db_session).list_entries(store_id, seller_id)
    assert str(entries[0].premium_rate_applied) == "0.1500"
