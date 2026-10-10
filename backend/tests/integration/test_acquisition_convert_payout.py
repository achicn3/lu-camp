"""收購撥款「購物金改成付現」（店主 2026-10-09／10-10）。

客人選了購物金、送出後反悔要現金：管理者在收購紀錄按「改成付現」（原因只有一種，稽核記固定代碼、不收自由文字）——當初撥的購物金（含溢價）
全數沖回、從抽屜付出溢價前的價值（記一般收購付現 BUYOUT_OUT）、收購單撥款方式改成現金、寫稽核。
商品與批次不動。只限全額購物金撥款、沒作廢過的單；購物金已花用、沒開帳、非管理者都擋下。
之後若作廢這筆，照現金單收回現金、不再扣購物金。
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
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.cashdrawer.models import CashMovement
from app.modules.inventory.models import SerializedItem
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


async def _convert(client: httpx.AsyncClient, token: str, acq_id: int) -> httpx.Response:
    return await client.post(
        f"/api/v1/acquisitions/{acq_id}/convert-payout-to-cash", headers=_auth(token)
    )


async def _commit_checks(db: AsyncSession) -> None:
    """收購／購物金的守衛是 COMMIT 時才檢查的 deferred trigger；測試不 commit，這裡強制立刻檢查。"""
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


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
        "reversed_credit": str(granted),
        "cash_paid": "1000",
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
        select(AuditLog).where(AuditLog.action == "CONVERT_ACQUISITION_PAYOUT_TO_CASH")
    )
    assert audit is not None and audit.entity_id == str(acq_id)
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


async def test_cash_or_voided_acquisitions_cannot_be_converted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _store_id, seller_id = await _seed(db_session)
    cash_acq = await _create_buyout(client, clerk, seller_id)
    resp = await _convert(client, mgr, cash_acq)
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
