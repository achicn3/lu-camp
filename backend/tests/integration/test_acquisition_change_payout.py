"""收購撥款方式事後改（店主 2026-10-09／10-10）：客人反悔，購物金 ↔ 現金。

管理者在收購紀錄按「改撥款方式」（原因只有一種，稽核記固定代碼、不收自由文字）。商品與批次不動、
客人不重簽。只限全額單一撥款、沒作廢過的單；沒開帳、非管理者都擋下。
- 購物金 → 現金：當初撥的購物金（含溢價）全數沖回、從抽屜付出溢價前的價值（BUYOUT_OUT）。
  購物金已花用就擋下。之後若作廢，照現金單收回現金、不再扣購物金。
- 現金 → 購物金：客人把現金還回抽屜（ACQUISITION_VOID_IN，報表列「收購退回現金」）、
  照當下設定的溢價率撥購物金；要是會員。
- 購物金只能入帳一次（帳本一筆收購一筆 CREDIT）：曾撥過購物金的單不能再改成購物金。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.money import round_ntd
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.cashdrawer.models import CashMovement
from app.modules.inventory.models import SerializedItem
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.models import StoreCreditLedger
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    CashMovementType,
    PayoutMethod,
    SerializedItemStatus,
    StoreCreditEntryType,
)
from tests.integration.test_acquisition_void import (
    _auth,
    _clerk_id,
    _create_buyout,
    _create_buyout_n_items,
    _seed,
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


async def _change(
    client: httpx.AsyncClient, token: str, acq_id: int, to: str = "CASH"
) -> httpx.Response:
    return await client.post(
        f"/api/v1/acquisitions/{acq_id}/change-payout",
        json={"payout_method": to},
        headers=_auth(token),
    )


async def _convert(client: httpx.AsyncClient, token: str, acq_id: int) -> httpx.Response:
    return await _change(client, token, acq_id, "CASH")


async def _commit_checks(db: AsyncSession) -> None:
    """收購／購物金的守衛是 COMMIT 時才檢查的 deferred trigger；測試不 commit，這裡強制立刻檢查。"""
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # 檢查完切回延後檢查：之後的寫入照正式環境一樣到 COMMIT 才驗（一筆交易內先改收購、後寫帳本）。
    await db.execute(text("SET CONSTRAINTS ALL DEFERRED"))


async def _granted(db: AsyncSession, acq_id: int) -> Decimal:
    entry = await db.scalar(
        select(StoreCreditLedger).where(
            StoreCreditLedger.source_id == acq_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.CREDIT,
        )
    )
    assert entry is not None
    return Decimal(entry.signed_amount)


async def _buyout_out(db: AsyncSession, store_id: int) -> Decimal:
    rows = await db.scalars(
        select(CashMovement.amount).where(
            CashMovement.store_id == store_id, CashMovement.type == CashMovementType.BUYOUT_OUT
        )
    )
    return sum((Decimal(a) for a in rows.all()), Decimal(0))


async def test_convert_reverses_credit_pays_cash_and_switches_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    granted = await _granted(db_session, acq_id)
    assert granted > 1000  # 含溢價

    resp = await _convert(client, mgr, acq_id)

    assert resp.status_code == 200, resp.text
    await _commit_checks(db_session)  # 資料庫守衛（購物金腿 ↔ 帳本、庫存背書）也要過
    assert resp.json() == {
        "acquisition_id": acq_id,
        "payout_method": "CASH",
        "cash": "1000",
        "store_credit": str(granted),
    }
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == 0
    assert await _buyout_out(db_session, store_id) == 1000
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None
    await db_session.refresh(acq)
    assert acq.payout_method is PayoutMethod.CASH
    assert (acq.payout_cash_amount, acq.payout_credit_cash_equivalent, acq.total_cash_paid) == (
        Decimal(1000),
        Decimal(0),
        Decimal(1000),
    )
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.acquisition_id == acq_id)
    )
    assert item is not None and item.status is SerializedItemStatus.IN_STOCK  # 商品不動
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "CHANGE_ACQUISITION_PAYOUT")
    )
    assert audit is not None and audit.entity_id == str(acq_id)
    assert audit.before is not None and audit.before["payout_method"] == "STORE_CREDIT"
    assert audit.after is not None and audit.after["reason"] == "CUSTOMER_CHANGED_MIND"


async def test_records_show_cash_and_whole_void_takes_cash_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    assert (await _convert(client, mgr, acq_id)).status_code == 200

    listing = (await client.get("/api/v1/acquisitions", headers=_auth(mgr))).json()
    row = next(r for r in listing["items"] if r["id"] == acq_id)
    assert row["payout_method"] == "CASH"
    assert row["void_block"] is None  # 不再拿已沖回的購物金判斷「已花用」

    voided = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "測試作廢"}, headers=_auth(mgr)
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["reversed_cash"] == "1000"
    assert voided.json()["reversed_credit"] == "0"
    assert await StoreCreditService(db_session).get_balance(store_id, seller_id) == 0


async def test_cannot_convert_when_credit_already_spent(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    await StoreCreditService(db_session).adjust(
        store_id,
        seller_id,
        amount=Decimal(-1),
        reason="模擬已花掉一點",
        created_by=await _clerk_id(db_session, store_id),
        idempotency_key=f"spent-{acq_id}",
    )

    resp = await _convert(client, mgr, acq_id)

    assert resp.status_code == 409, resp.text
    assert "購物金" in resp.json()["detail"]
    assert await _buyout_out(db_session, store_id) == 0


async def test_cannot_convert_without_open_drawer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session, open_drawer=False)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")

    resp = await _convert(client, mgr, acq_id)

    assert resp.status_code == 409, resp.text
    assert await StoreCreditService(db_session).get_balance(store_id, seller_id) > 0


async def test_only_managers_can_convert(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")

    assert (await _convert(client, clerk, acq_id)).status_code == 403


async def test_voided_or_already_cash_acquisitions_cannot_be_converted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    cash_acq = await _create_buyout(client, clerk, seller_id)
    resp = await _convert(client, mgr, cash_acq)  # 已經是現金
    assert resp.status_code == 422, resp.text

    credit_acq = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    voided = await client.post(
        f"/api/v1/acquisitions/{credit_acq}/void", json={"reason": "作廢"}, headers=_auth(mgr)
    )
    assert voided.status_code == 200, voided.text
    assert (await _convert(client, mgr, credit_acq)).status_code == 409


async def test_converting_twice_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    assert (await _convert(client, mgr, acq_id)).status_code == 200

    again = await _convert(client, mgr, acq_id)

    assert again.status_code == 422, again.text
    assert await _buyout_out(db_session, store_id) == 1000  # 不會付兩次


async def test_db_guard_still_blocks_zeroing_credit_that_was_not_reversed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """資料庫守衛只為「購物金已整筆沖回」放行歸零：沒沖回就直接改欄位，照樣擋下（不能憑空消滅負債）。"""
    clerk, _mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    await db_session.execute(
        text(
            "UPDATE acquisitions SET payout_method = 'CASH', payout_cash_amount = 1000, "
            "payout_credit_cash_equivalent = 0, total_cash_paid = 1000 WHERE id = :id"
        ),
        {"id": acq_id},
    )
    with pytest.raises(DBAPIError, match=r"收購購物金腿必須對應|ACQUISITION CREDIT 分錄必須對應"):
        await _commit_checks(db_session)


# ── 現金 → 購物金（客人改要購物金）────────────────────────────────────


async def _credit_entries(db: AsyncSession, acq_id: int) -> list[StoreCreditLedger]:
    rows = await db.scalars(
        select(StoreCreditLedger)
        .where(StoreCreditLedger.source_id == acq_id)
        .order_by(StoreCreditLedger.id)
    )
    return list(rows.all())


async def _void_in(db: AsyncSession, store_id: int) -> Decimal:
    rows = await db.scalars(
        select(CashMovement.amount).where(
            CashMovement.store_id == store_id,
            CashMovement.type == CashMovementType.ACQUISITION_VOID_IN,
        )
    )
    return sum((Decimal(a) for a in rows.all()), Decimal(0))


async def test_cash_to_store_credit_takes_cash_back_and_grants_credit_with_premium(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)  # 現金 1000
    # 實際上收購早就 COMMIT 過；先把建單時的守衛檢查跑完，才不會拿「剛建、還是現金」的快照去比。
    await _commit_checks(db_session)
    premium = (await StoreSettingsService(db_session).get_effective_settings(store_id)).premium_rate
    expected_credit = Decimal(round_ntd(Decimal(1000) * (1 + Decimal(premium))))

    resp = await _change(client, mgr, acq_id, "STORE_CREDIT")

    assert resp.status_code == 200, resp.text
    await _commit_checks(db_session)
    assert resp.json() == {
        "acquisition_id": acq_id,
        "payout_method": "STORE_CREDIT",
        "cash": "1000",
        "store_credit": str(expected_credit),
    }
    assert await _void_in(db_session, store_id) == 1000  # 客人還的現金進抽屜
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == expected_credit
    acq = await db_session.get(Acquisition, acq_id)
    assert acq is not None
    await db_session.refresh(acq)
    assert acq.payout_method is PayoutMethod.STORE_CREDIT
    assert (acq.payout_cash_amount, acq.payout_credit_cash_equivalent, acq.total_cash_paid) == (
        Decimal(0),
        Decimal(1000),
        Decimal(0),
    )


async def test_cash_to_credit_then_back_to_cash_then_no_more_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """購物金只能入帳一次：現金→購物金→現金可以，再改回購物金就擋下。"""
    clerk, mgr, store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    assert (await _change(client, mgr, acq_id, "STORE_CREDIT")).status_code == 200
    back = await _change(client, mgr, acq_id, "CASH")
    assert back.status_code == 200, back.text
    await _commit_checks(db_session)
    assert await StoreCreditService(db_session).get_balance(store_id, seller_id) == 0

    again = await _change(client, mgr, acq_id, "STORE_CREDIT")

    assert again.status_code == 422, again.text
    assert "購物金" in again.json()["detail"]
    assert len(await _credit_entries(db_session, acq_id)) == 2  # 一筆 CREDIT＋一筆沖正


async def test_store_credit_needs_a_member(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session, member=False)
    acq_id = await _create_buyout(client, clerk, seller_id)

    resp = await _change(client, mgr, acq_id, "STORE_CREDIT")

    assert resp.status_code == 422, resp.text
    assert await _credit_entries(db_session, acq_id) == []


async def test_cash_to_credit_needs_an_open_drawer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    current = (await client.get("/api/v1/cash-sessions/current", headers=_auth(clerk))).json()
    closed = await client.post(
        f"/api/v1/cash-sessions/{current['id']}/close",
        json={"counted_amount": "4000"},
        headers=_auth(clerk),
    )
    assert closed.status_code in (200, 201), closed.text

    resp = await _change(client, mgr, acq_id, "STORE_CREDIT")

    assert resp.status_code == 409, resp.text
    assert await _credit_entries(db_session, acq_id) == []


async def test_records_list_shows_credit_after_cash_to_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout(client, clerk, seller_id)
    assert (await _change(client, mgr, acq_id, "STORE_CREDIT")).status_code == 200

    listing = (await client.get("/api/v1/acquisitions", headers=_auth(mgr))).json()
    row = next(r for r in listing["items"] if r["id"] == acq_id)
    assert row["payout_method"] == "STORE_CREDIT"
    assert row["payout_credit_cash_equivalent"] == "1000"
    assert row["void_block"] is None


async def test_records_list_says_which_way_each_row_can_change(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """清單告訴前端每列能改成什麼（前端照著顯示按鈕）：購物金單→CASH、現金單→STORE_CREDIT，
    撥過購物金又改回現金的單→不能再改（None），作廢的單→None。"""
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    credit_acq = await _create_buyout(client, clerk, seller_id, payout_method="STORE_CREDIT")
    cash_acq = await _create_buyout(client, clerk, seller_id)
    round_trip = await _create_buyout(client, clerk, seller_id)
    voided = await _create_buyout(client, clerk, seller_id)
    await _commit_checks(db_session)
    assert (await _change(client, mgr, round_trip, "STORE_CREDIT")).status_code == 200
    assert (await _change(client, mgr, round_trip, "CASH")).status_code == 200
    assert (
        await client.post(
            f"/api/v1/acquisitions/{voided}/void", json={"reason": "作廢"}, headers=_auth(mgr)
        )
    ).status_code == 200

    listing = (await client.get("/api/v1/acquisitions", headers=_auth(mgr))).json()
    to = {r["id"]: r["payout_change_to"] for r in listing["items"]}
    assert to[credit_acq] == "CASH"
    assert to[cash_acq] == "STORE_CREDIT"
    assert to[round_trip] is None
    assert to[voided] is None


async def test_partly_voided_acquisition_cannot_change_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """作廢過部分商品的單：清單不給改（payout_change_to=None），端點也擋。"""
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    acq_id = await _create_buyout_n_items(client, clerk, seller_id, 2)
    items = (
        await client.get(f"/api/v1/acquisitions/{acq_id}/void-items", headers=_auth(mgr))
    ).json()
    partial = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void",
        json={"reason": "作廢一件", "item_ids": [items[0]["id"]]},
        headers=_auth(mgr),
    )
    assert partial.status_code == 200, partial.text

    listing = (await client.get("/api/v1/acquisitions", headers=_auth(mgr))).json()
    row = next(r for r in listing["items"] if r["id"] == acq_id)
    assert row["payout_change_to"] is None
    assert (await _change(client, mgr, acq_id, "STORE_CREDIT")).status_code == 422
