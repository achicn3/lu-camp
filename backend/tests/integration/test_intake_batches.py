"""收購佇列 I1：批次、估價列、當日 A 編號、處置與退還（docs/42 §3–§5）。

現場流程：報到收件（配當日 A 編號）→ 逐列估價（隨時存檔）→ 估完進「待確認」→ 叫號時逐列標處置
（可部分接受）→ 之後的簽署、付款屬 I3。取消整批也不刪任何列，退還客人逐列記。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.intake.service import IntakeService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

PATH = "/api/v1/intake-batches"


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


async def _store(db: AsyncSession, name: str = "門市") -> tuple[int, int, dict[str, str]]:
    """回傳 (store_id, contact_id, 店員授權標頭)。"""
    store = Store(name=name)
    db.add(store)
    await db.flush()
    clerk = User(
        store_id=store.id, username=f"clk{store.id}", password_hash="h", role=UserRole.CLERK
    )
    contact = Contact(store_id=store.id, name="王小明")
    db.add_all([clerk, contact])
    await db.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return store.id, contact.id, {"Authorization": f"Bearer {token}"}


async def _batch(
    client: httpx.AsyncClient, contact_id: int, auth: dict[str, str]
) -> dict[str, Any]:
    resp = await client.post(
        PATH, json={"contact_id": contact_id, "declared_item_count": 3}, headers=auth
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _line(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "short_name": "黑色折疊椅",
        "qty": 2,
        "acquisition_type": "BUYOUT",
        "reference_price": "1000",
        "discount_pct": 50,
        "expected_listed_price": "500",
        "suggested_cost": "256",
        "deal_cost": "250",
    }
    base.update(overrides)
    return base


# ── 報到：當日 A 編號 ─────────────────────────────────────────────────


async def test_create_batch_gets_daily_a_number(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    first = await _batch(client, contact_id, auth)
    second = await _batch(client, contact_id, auth)
    assert (first["ticket_label"], second["ticket_label"]) == ("A001", "A002")
    assert first["status"] == "PENDING_ESTIMATE"
    assert first["declared_item_count"] == 3
    assert first["contact_name"] == "王小明"


async def test_numbering_restarts_each_taipei_day(db_session: AsyncSession) -> None:
    store_id, contact_id, _auth = await _store(db_session)
    clerk_id = await db_session.scalar(select(User.id).where(User.store_id == store_id))
    assert clerk_id is not None
    service = IntakeService(db_session)
    # 台北 9/24 23:30（UTC 15:30）與 9/25 00:30（UTC 16:30）
    late = await service.create_batch(
        store_id,
        contact_id=contact_id,
        declared_item_count=1,
        actor_user_id=clerk_id,
        now=datetime(2026, 9, 24, 15, 30, tzinfo=UTC),
    )
    early = await service.create_batch(
        store_id,
        contact_id=contact_id,
        declared_item_count=1,
        actor_user_id=clerk_id,
        now=datetime(2026, 9, 24, 16, 30, tzinfo=UTC),
    )
    assert (late.ticket_no, early.ticket_no) == (1, 1)


async def test_contact_must_belong_to_the_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, _contact, auth = await _store(db_session)
    _other_store, other_contact, _ = await _store(db_session, "別店")
    resp = await client.post(
        PATH, json={"contact_id": other_contact, "declared_item_count": 1}, headers=auth
    )
    assert resp.status_code == 404, resp.text


# ── 估價 ──────────────────────────────────────────────────────────────


async def test_adding_lines_saves_estimates_and_totals(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _batch(client, contact_id, auth)
    resp = await client.post(f"{PATH}/{batch['id']}/lines", json=_line(), headers=auth)
    assert resp.status_code == 201, resp.text
    await client.post(
        f"{PATH}/{batch['id']}/lines",
        json=_line(short_name="露營桌", qty=1, deal_cost="800", suggested_cost="820"),
        headers=auth,
    )
    got = (await client.get(f"{PATH}/{batch['id']}", headers=auth)).json()
    assert got["status"] == "ESTIMATING"
    assert [ln["line_no"] for ln in got["lines"]] == [1, 2]
    assert (got["line_count"], got["item_count"]) == (2, 3)
    assert got["deal_total"] == "1300"  # 250×2 + 800


async def test_line_can_be_edited_and_deleted_while_estimating(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _batch(client, contact_id, auth)
    line = (await client.post(f"{PATH}/{batch['id']}/lines", json=_line(), headers=auth)).json()
    resp = await client.patch(
        f"{PATH}/{batch['id']}/lines/{line['id']}",
        json={"deal_cost": "300", "grade": "A"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["deal_cost"], resp.json()["grade"]) == ("300", "A")
    resp = await client.delete(f"{PATH}/{batch['id']}/lines/{line['id']}", headers=auth)
    assert resp.status_code == 204, resp.text


@pytest.mark.parametrize(
    "bad",
    [
        {"short_name": "  "},
        {"qty": 0},
        {"discount_pct": 50, "reference_price": None},  # 折數估價要有原價
        {"discount_pct": 101},
        {"deal_cost": "-1"},
        {"acquisition_type": "CONSIGNMENT", "commission_pct": None, "deal_cost": None},
    ],
)
async def test_invalid_lines_are_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, bad: dict[str, object]
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _batch(client, contact_id, auth)
    resp = await client.post(f"{PATH}/{batch['id']}/lines", json=_line(**bad), headers=auth)
    assert resp.status_code == 422, (bad, resp.text)


async def test_consignment_line_uses_commission_not_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _batch(client, contact_id, auth)
    resp = await client.post(
        f"{PATH}/{batch['id']}/lines",
        json=_line(
            acquisition_type="CONSIGNMENT", commission_pct=50, deal_cost=None, suggested_cost=None
        ),
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    got = (await client.get(f"{PATH}/{batch['id']}", headers=auth)).json()
    assert got["deal_total"] == "0"  # 寄售不付收購款


# ── 估完 → 待確認 → 逐列處置 ──────────────────────────────────────────


async def _ready_batch(
    client: httpx.AsyncClient, contact_id: int, auth: dict[str, str]
) -> dict[str, Any]:
    batch = await _batch(client, contact_id, auth)
    await client.post(f"{PATH}/{batch['id']}/lines", json=_line(qty=3), headers=auth)
    resp = await client.post(f"{PATH}/{batch['id']}/ready", headers=auth)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_ready_requires_a_deal_price_on_every_paid_line(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _batch(client, contact_id, auth)
    assert (await client.post(f"{PATH}/{batch['id']}/ready", headers=auth)).status_code == 409
    await client.post(f"{PATH}/{batch['id']}/lines", json=_line(deal_cost=None), headers=auth)
    resp = await client.post(f"{PATH}/{batch['id']}/ready", headers=auth)
    assert resp.status_code == 409, resp.text


async def test_partial_accept_and_return_to_customer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """3 張收 2 張：成交總額只算 2 張；沒收的那張要記已退還。"""
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _ready_batch(client, contact_id, auth)
    assert batch["status"] == "AWAITING_CONFIRM"
    line_id = batch["lines"][0]["id"]
    resp = await client.patch(
        f"{PATH}/{batch['id']}/lines/{line_id}/disposition",
        json={"disposition": "ACCEPTED", "accepted_qty": 2, "returned_to_customer": True},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    got = (await client.get(f"{PATH}/{batch['id']}", headers=auth)).json()
    assert got["accepted_total"] == "500"
    assert got["accepted_item_count"] == 2
    assert got["lines"][0]["returned_to_customer"] is True


async def test_disposition_quantities_are_checked(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _ready_batch(client, contact_id, auth)
    line_id = batch["lines"][0]["id"]
    for body in [
        {"disposition": "ACCEPTED", "accepted_qty": 4},
        {"disposition": "ACCEPTED", "accepted_qty": 0},
        {"disposition": "CUSTOMER_KEPT", "accepted_qty": 1},
    ]:
        resp = await client.patch(
            f"{PATH}/{batch['id']}/lines/{line_id}/disposition", json=body, headers=auth
        )
        assert resp.status_code == 422, (body, resp.text)


async def test_lines_are_never_deleted_after_ready(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _ready_batch(client, contact_id, auth)
    line_id = batch["lines"][0]["id"]
    resp = await client.delete(f"{PATH}/{batch['id']}/lines/{line_id}", headers=auth)
    assert resp.status_code == 409, resp.text


async def test_editing_price_after_ready_is_still_allowed_before_signing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """叫號議價時店員可改成交價（裁示 5）。"""
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _ready_batch(client, contact_id, auth)
    line_id = batch["lines"][0]["id"]
    resp = await client.patch(
        f"{PATH}/{batch['id']}/lines/{line_id}", json={"deal_cost": "220"}, headers=auth
    )
    assert resp.status_code == 200, resp.text


# ── 取消 ──────────────────────────────────────────────────────────────


async def test_cancel_keeps_lines_and_allows_recording_returns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _ready_batch(client, contact_id, auth)
    resp = await client.post(
        f"{PATH}/{batch['id']}/cancel", json={"reason": "客人改天再來"}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELLED"
    assert len(resp.json()["lines"]) == 1
    line_id = batch["lines"][0]["id"]
    # 取消後不能再改價，但可以記「已退還客人」
    edit = await client.patch(
        f"{PATH}/{batch['id']}/lines/{line_id}", json={"deal_cost": "1"}, headers=auth
    )
    assert edit.status_code == 409, edit.text
    returned = await client.patch(
        f"{PATH}/{batch['id']}/lines/{line_id}/disposition",
        json={"disposition": "CUSTOMER_KEPT", "accepted_qty": 0, "returned_to_customer": True},
        headers=auth,
    )
    assert returned.status_code == 200, returned.text


# ── 佇列清單 ──────────────────────────────────────────────────────────


async def test_queue_lists_open_batches_of_this_store_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    _other, other_contact, other_auth = await _store(db_session, "別店")
    open_batch = await _batch(client, contact_id, auth)
    cancelled = await _batch(client, contact_id, auth)
    await client.post(f"{PATH}/{cancelled['id']}/cancel", json={"reason": "放棄"}, headers=auth)
    await _batch(client, other_contact, other_auth)

    queue = (await client.get(PATH, headers=auth)).json()
    assert [b["id"] for b in queue] == [open_batch["id"]]
    everything = (await client.get(PATH, params={"include_closed": True}, headers=auth)).json()
    assert {b["id"] for b in everything} == {open_batch["id"], cancelled["id"]}


async def test_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.get(PATH)).status_code == 401


async def test_batch_of_another_store_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, contact_id, auth = await _store(db_session)
    _other, _c, other_auth = await _store(db_session, "別店")
    batch = await _batch(client, contact_id, auth)
    assert (await client.get(f"{PATH}/{batch['id']}", headers=other_auth)).status_code == 404
    assert Decimal(batch["deal_total"]) == 0


@pytest.mark.parametrize("field", ["short_name", "qty", "acquisition_type"])
async def test_required_fields_cannot_be_cleared(
    client: httpx.AsyncClient, db_session: AsyncSession, field: str
) -> None:
    """修改時把必填欄位設成 null：回 422，不能變成 500 或寫壞資料。"""
    _store_id, contact_id, auth = await _store(db_session)
    batch = await _batch(client, contact_id, auth)
    line = (await client.post(f"{PATH}/{batch['id']}/lines", json=_line(), headers=auth)).json()
    resp = await client.patch(
        f"{PATH}/{batch['id']}/lines/{line['id']}", json={field: None}, headers=auth
    )
    assert resp.status_code == 422, resp.text
