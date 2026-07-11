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
from app.shared.enums import StoreCreditSourceType, UserRole


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
    # 撥款回應帶帳本權威事實（2026-07-11 裁示：憑證聯要印撥入後購物金總額）：
    # 實發（含溢價）與本筆分錄的 balance_after。
    assert body["payout_credit_granted"] == "1100"
    assert body["payout_credit_balance_after"] == "1100"
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
    assert body["payout_credit_granted"] == "660"
    assert body["payout_credit_balance_after"] == "660"
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
    assert body["payout_credit_granted"] is None
    assert body["payout_credit_balance_after"] is None
    assert await StoreCreditService(db_session).get_balance(store_id, seller_id) == Decimal(0)


async def test_balance_after_accumulates_prior_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已有餘額再收購入帳：balance_after ＝ 既有餘額 ＋ 本筆實發（帳本分錄值，非另查活餘額）。"""
    token, store_id, seller_id = await _seed(db_session, open_drawer=False)
    for _ in range(2):
        resp = await client.post(
            "/api/v1/acquisitions",
            json=_buyout_payload(seller_id, payout_method="STORE_CREDIT"),
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["payout_credit_granted"] == "1100"
    assert body["payout_credit_balance_after"] == "2200"  # 1100（前筆）+ 1100（本筆）
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == Decimal(2200)


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


async def test_retry_fingerprint_canonicalizes_money_forms(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 key、語意相同但金額形式不同（1000／"1000"／"1000.0"）→ 一律冪等回原單
    （Codex：形式差異不得把合法重試打成 409）。"""
    token, _store_id, seller_id = await _seed(db_session)

    def payload(cost: object) -> dict[str, object]:
        return {
            "type": "BUYOUT",
            "contact_id": seller_id,
            "items": [
                {"name": "帳篷", "grade": "A", "acquisition_cost": cost, "listed_price": "1800"}
            ],
        }

    first = await client.post(
        "/api/v1/acquisitions", json=payload("1000"), headers=_auth(token, idem="canon-key")
    )
    assert first.status_code == 201
    for variant in (1000, "1000.0"):
        retry = await client.post(
            "/api/v1/acquisitions",
            json=payload(variant),
            headers=_auth(token, idem="canon-key"),
        )
        assert retry.status_code == 201, retry.text
        assert retry.json()["acquisition_id"] == first.json()["acquisition_id"]


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


async def test_service_rejects_invalid_split_even_bypassing_schema(
    db_session: AsyncSession,
) -> None:
    """service 邊界完整驗證（Codex high）：model_construct 繞過 Pydantic 帶
    零/負現金部分 → 拒絕且零落地（無收購/現金/帳本）。"""
    from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
    from app.modules.acquisition.service import AcquisitionService
    from app.shared.enums import AcquisitionType as AT
    from app.shared.enums import Grade
    from app.shared.enums import PayoutMethod as PM
    from app.shared.exceptions import InvalidPayoutSplit

    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    item = AcquisitionItemIn.model_construct(
        name="帳篷",
        grade=Grade.A,
        listed_price=Decimal(1800),
        acquisition_cost=Decimal(1000),
        brand_id=None,
        product_model_id=None,
        commission_pct=None,
    )
    svc = AcquisitionService(db_session)
    for bad_cash in (Decimal(0), Decimal(-100)):
        data = AcquisitionCreate.model_construct(
            type=AT.BUYOUT,
            contact_id=seller_id,
            note=None,
            items=[item],
            lot=None,
            payout_method=PM.SPLIT,
            payout_split_cash=bad_cash,
        )
        import pytest

        with pytest.raises(InvalidPayoutSplit):
            await svc.create_acquisition(
                store_id, clerk_id, data, idempotency_key=f"bypass-{bad_cash}"
            )
        await db_session.rollback()
        _token, store_id, seller_id = await _seed(db_session)
        clerk_id = (await db_session.execute(select(User.id))).scalar_one()
        svc = AcquisitionService(db_session)
    count = await db_session.scalar(select(func.count()).select_from(Acquisition))
    assert count == 0


async def test_payout_failures_leave_nothing_even_if_caller_commits(
    db_session: AsyncSession,
) -> None:
    """預檢先於寫入（Codex 第五輪 high）：直呼 service、catch 例外**不回滾就 commit**
    ——也不得留下收購/庫存/現金/帳本任何一筆。"""
    import pytest

    from app.modules.acquisition.schemas import AcquisitionCreate
    from app.modules.acquisition.service import AcquisitionService
    from app.modules.inventory.models import SerializedItem
    from app.shared.exceptions import DomainError

    _token, store_id, seller_id = await _seed(db_session, member=False)  # 非會員
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    data = AcquisitionCreate.model_validate(
        _buyout_payload(seller_id, payout_method="STORE_CREDIT")
    )
    svc = AcquisitionService(db_session)
    with pytest.raises(DomainError):
        await svc.create_acquisition(store_id, clerk_id, data, idempotency_key="dirty-commit")
    # 故意不 rollback、直接 commit（粗心呼叫者情境）
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(Acquisition)) == 0
    assert await db_session.scalar(select(func.count()).select_from(SerializedItem)) == 0
    assert await db_session.scalar(select(func.count()).select_from(CashMovement)) == 0


async def test_zero_total_store_credit_is_422_not_500(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """零成本＋STORE_CREDIT（第十三輪 medium）：領域層 422，不落到 DB CHECK 500。"""
    token, _store_id, seller_id = await _seed(db_session, open_drawer=False)
    payload = _buyout_payload(seller_id, payout_method="STORE_CREDIT")
    payload["items"][0]["acquisition_cost"] = "0"  # type: ignore[index]
    resp = await client.post("/api/v1/acquisitions", json=payload, headers=_auth(token))
    assert resp.status_code == 422
    # 零元 CASH 同樣拒（第十五輪：不留「入庫卻無撥款副作用」的單）。
    # 前一個 422 的 router rollback 連 seed 一起回復（測試共用交易）→ 重 seed。
    token, _store_id, seller_id = await _seed(db_session, open_drawer=False)
    cash_payload = _buyout_payload(seller_id)
    cash_payload["items"][0]["acquisition_cost"] = "0"  # type: ignore[index]
    resp = await client.post("/api/v1/acquisitions", json=cash_payload, headers=_auth(token))
    assert resp.status_code == 422, resp.text


async def test_db_rejects_inconsistent_payout_shapes(db_session: AsyncSession) -> None:
    """形狀 CHECK（Codex 第十一輪 medium）：直插「STORE_CREDIT 卻有付現」「SPLIT
    缺購物金腿」一律 IntegrityError。"""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()

    async def _raw(method: str, cash: object, credit: object, total: object) -> None:
        await db_session.execute(
            text(
                "INSERT INTO acquisitions"
                " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                "  created_at, updated_at)"
                " VALUES (:sid, 'BUYOUT', :cid, :uid, :total, :method, :cash, :credit,"
                "  now(), now())"
            ),
            {
                "sid": store_id,
                "cid": seller_id,
                "uid": clerk_id,
                "method": method,
                "cash": cash,
                "credit": credit,
                "total": total,
            },
        )

    with pytest.raises(IntegrityError):
        await _raw("STORE_CREDIT", 500, 1000, 500)  # 購物金單卻有付現
    await db_session.rollback()
    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    with pytest.raises(IntegrityError):
        await _raw("SPLIT", 400, 0, 400)  # SPLIT 缺購物金腿
    await db_session.rollback()
    # NULL 旁路（第十二輪 high）：UNKNOWN 不得放行
    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    with pytest.raises(IntegrityError):
        await _raw("STORE_CREDIT", 0, None, 0)  # credit 腿 NULL
    await db_session.rollback()
    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    with pytest.raises(IntegrityError):
        await _raw("SPLIT", 400, 600, None)  # total NULL
    await db_session.rollback()
    # 全 NULL CASH（第十四輪 high）：BUYOUT 不可有「無撥款」頭
    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    with pytest.raises(IntegrityError):
        await _raw("CASH", None, None, None)
    await db_session.rollback()


async def test_negative_cost_rejected_before_writes(db_session: AsyncSession) -> None:
    """負成本繞過（Codex 第十輪 high）：model_construct 帶負 acquisition_cost →
    純算階段即拒、零落地（不留「負撥款腿、無副作用」的怪單）。"""
    import pytest

    from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
    from app.modules.acquisition.service import AcquisitionService
    from app.shared.enums import AcquisitionType as AT
    from app.shared.enums import Grade
    from app.shared.enums import PayoutMethod as PM
    from app.shared.exceptions import InvalidPayoutSplit

    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    item = AcquisitionItemIn.model_construct(
        name="帳篷",
        grade=Grade.A,
        listed_price=Decimal(1800),
        acquisition_cost=Decimal(-1000),
        brand_id=None,
        product_model_id=None,
        commission_pct=None,
    )
    svc = AcquisitionService(db_session)
    for method in (PM.CASH, PM.STORE_CREDIT):
        data = AcquisitionCreate.model_construct(
            type=AT.BUYOUT,
            contact_id=seller_id,
            note=None,
            items=[item],
            lot=None,
            payout_method=method,
            payout_split_cash=None,
        )
        with pytest.raises(InvalidPayoutSplit):
            await svc.create_acquisition(store_id, clerk_id, data, idempotency_key=f"neg-{method}")
        await db_session.rollback()
        _token, store_id, seller_id = await _seed(db_session)
        clerk_id = (await db_session.execute(select(User.id))).scalar_one()
        svc = AcquisitionService(db_session)
    assert await db_session.scalar(select(func.count()).select_from(Acquisition)) == 0


async def test_raw_string_payout_method_normalized(db_session: AsyncSession) -> None:
    """raw string 撥款方式（Codex 第九輪 high）：model_construct 帶 "SPLIT"（無拆分）
    不得被誤判為全購物金；非法字串如實拒。"""
    import pytest

    from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
    from app.modules.acquisition.service import AcquisitionService
    from app.shared.enums import AcquisitionType as AT
    from app.shared.enums import Grade
    from app.shared.exceptions import InvalidPayoutSplit

    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    item = AcquisitionItemIn.model_construct(
        name="帳篷",
        grade=Grade.A,
        listed_price=Decimal(1800),
        acquisition_cost=Decimal(1000),
        brand_id=None,
        product_model_id=None,
        commission_pct=None,
    )
    svc = AcquisitionService(db_session)
    for raw_method, split in (("SPLIT", None), ("NOT-A-METHOD", None)):
        data = AcquisitionCreate.model_construct(
            type=AT.BUYOUT,
            contact_id=seller_id,
            note=None,
            items=[item],
            lot=None,
            payout_method=raw_method,  # type: ignore[arg-type]  # 故意繞過型別測 raw string
            payout_split_cash=split,
        )
        with pytest.raises(InvalidPayoutSplit):
            await svc.create_acquisition(
                store_id, clerk_id, data, idempotency_key=f"raw-{raw_method}"
            )
        await db_session.rollback()
        _token, store_id, seller_id = await _seed(db_session)
        clerk_id = (await db_session.execute(select(User.id))).scalar_one()
        svc = AcquisitionService(db_session)
    # raw "CASH" 行為等同枚舉 CASH（正規化後正確分類、可成單）
    data = AcquisitionCreate.model_construct(
        type=AT.BUYOUT,
        contact_id=seller_id,
        note=None,
        items=[item],
        lot=None,
        payout_method="CASH",  # type: ignore[arg-type]  # 故意繞過型別測 raw string
        payout_split_cash=None,
    )
    result = await svc.create_acquisition(store_id, clerk_id, data, idempotency_key="raw-cash")
    assert result.payout_method == "CASH"
    assert result.payout_cash_amount == Decimal(1000)


async def test_blank_idempotency_key_rejected_at_service(db_session: AsyncSession) -> None:
    """空/None 鍵（Codex 第八輪 high）：service 執行期守衛直接拒、零落地。"""
    import pytest

    from app.modules.acquisition.schemas import AcquisitionCreate
    from app.modules.acquisition.service import AcquisitionService
    from app.shared.exceptions import IdempotencyKeyConflict

    _token, store_id, seller_id = await _seed(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    data = AcquisitionCreate.model_validate(_buyout_payload(seller_id))
    svc = AcquisitionService(db_session)
    for bad in (None, "", "   "):
        with pytest.raises(IdempotencyKeyConflict):
            await svc.create_acquisition(
                store_id,
                clerk_id,
                data,
                idempotency_key=bad,  # type: ignore[arg-type]
            )
    assert await db_session.scalar(select(func.count()).select_from(Acquisition)) == 0


async def test_forged_credit_leg_rejected_at_commit() -> None:
    """credit 腿 ↔ 帳本雙向綁定（第十六輪 medium＋第十七輪 P1×2）：
    (a) 有購物金腿、無對應分錄的 header → COMMIT 擋；
    (b) 分錄入錯對象（同店等值但 contact 不符）→ COMMIT 擋；
    (c) 孤兒分錄（source_id 不指向任何收購）憑空鑄造購物金 → COMMIT 擋。"""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    import app.core.db as app_db

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="偽腿店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="forge", password_hash="h", role=UserRole.CLERK)
        seller = Contact(
            store_id=store.id, name="賣方", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        other = Contact(
            store_id=store.id, name="別的會員", roles=["MEMBER"], national_id_enc="enc2"
        )
        s.add_all([clerk, seller, other])
        await s.flush()
        store_id, clerk_id = store.id, clerk.id
        seller_id, other_id = seller.id, other.id
        await s.commit()

    forged_header_sql = text(
        "INSERT INTO acquisitions"
        " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
        "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
        "  created_at, updated_at)"
        " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, :credit,"
        "  now(), now()) RETURNING id"
    )
    try:
        # (a) header 有 credit 腿、帳本無分錄
        async with sm() as s:
            await s.execute(
                forged_header_sql,
                {"sid": store_id, "cid": seller_id, "uid": clerk_id, "credit": 999999},
            )
            with pytest.raises(DBAPIError):
                await s.commit()  # deferrable trigger 於提交時驗

        # (b) 分錄入錯對象：header 給賣方、等值分錄卻記在別的會員帳上
        async with sm() as s:
            acq_id = await s.scalar(
                forged_header_sql,
                {"sid": store_id, "cid": seller_id, "uid": clerk_id, "credit": 1000},
            )
            await StoreCreditService(s).credit(
                store_id,
                other_id,
                cash_equivalent=Decimal(1000),
                premium_rate=Decimal("0.10"),
                source_type=StoreCreditSourceType.ACQUISITION,
                source_id=acq_id,
                created_by=clerk_id,
            )
            with pytest.raises(DBAPIError):
                await s.commit()

        # (c) 孤兒分錄：source_id 不指向任何收購 → 不可憑空鑄造負債
        async with sm() as s:
            await StoreCreditService(s).credit(
                store_id,
                seller_id,
                cash_equivalent=Decimal(500),
                premium_rate=Decimal("0.10"),
                source_type=StoreCreditSourceType.ACQUISITION,
                source_id=424242,
                created_by=clerk_id,
            )
            with pytest.raises(DBAPIError):
                await s.commit()
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM acquisitions"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_committed_credit_acquisition_cannot_be_zeroed_moved_or_deleted() -> None:
    """已產生購物金分錄的收購不可事後 UPDATE 歸零/搬移/DELETE（第十八輪 P1×2）。

    分錄為 insert-only 不可改；若准許收購頭被改成 CASH／改 contact／被刪除，
    就會留下無主或錯置的購物金負債。守衛以「分錄是否存在」為準，於 COMMIT 擋。
    """
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    import app.core.db as app_db

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="不可變更店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="immut", password_hash="h", role=UserRole.CLERK)
        seller = Contact(
            store_id=store.id, name="賣方", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        other = Contact(store_id=store.id, name="別人", roles=["MEMBER"], national_id_enc="enc2")
        s.add_all([clerk, seller, other])
        await s.flush()
        store_id, clerk_id = store.id, clerk.id
        seller_id, other_id = seller.id, other.id
        await s.commit()

    # 先建一筆真實的 STORE_CREDIT 收購（header＋庫存實體＋等值分錄，同交易；三守衛皆過）
    async with sm() as s:
        acq_id = await s.scalar(
            text(
                "INSERT INTO acquisitions"
                " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                "  created_at, updated_at)"
                " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 1000,"
                "  now(), now()) RETURNING id"
            ),
            {"sid": store_id, "cid": seller_id, "uid": clerk_id},
        )
        await s.execute(
            text(
                "INSERT INTO serialized_items"
                " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
                "  listed_price, acquisition_id, created_at, updated_at)"
                " VALUES (:sid, :code, '測試品', 'A', 'OWNED', 1000, 1800, :aid, now(), now())"
            ),
            {"sid": store_id, "code": f"IMMUT-{acq_id}", "aid": acq_id},
        )
        await StoreCreditService(s).credit(
            store_id,
            seller_id,
            cash_equivalent=Decimal(1000),
            premium_rate=Decimal("0.10"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=acq_id,
            created_by=clerk_id,
        )
        await s.commit()  # 正常落地

    try:
        # (a) 改成 CASH／credit 歸零 → COMMIT 擋
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE acquisitions SET payout_method='CASH',"
                    " payout_credit_cash_equivalent=0 WHERE id=:id"
                ),
                {"id": acq_id},
            )
            with pytest.raises(DBAPIError):
                await s.commit()

        # (b) 把已產生分錄的收購搬到別的 contact → COMMIT 擋
        async with sm() as s:
            await s.execute(
                text("UPDATE acquisitions SET contact_id=:cid WHERE id=:id"),
                {"cid": other_id, "id": acq_id},
            )
            with pytest.raises(DBAPIError):
                await s.commit()

        # (c) 刪除已產生分錄的收購 → COMMIT 擋（不可留孤兒負債）。先刪庫存實體
        # 以略過 FK、直擊 DELETE 守衛：帳本分錄仍在 → COMMIT 時被擋。
        async with sm() as s:
            await s.execute(
                text("DELETE FROM serialized_items WHERE acquisition_id=:id"), {"id": acq_id}
            )
            await s.execute(text("DELETE FROM acquisitions WHERE id=:id"), {"id": acq_id})
            with pytest.raises(DBAPIError):
                await s.commit()
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM serialized_items"))
            await s.execute(text("DELETE FROM acquisitions"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_shell_acquisition_without_inventory_cannot_mint_credit() -> None:
    """第十九輪 P2：原生 SQL 插入「空殼收購」（header＋等值分錄，但無任何庫存
    實體）→ COMMIT 時被擋，不可憑空鑄造購物金負債。"""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    import app.core.db as app_db

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="空殼店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="shell", password_hash="h", role=UserRole.CLERK)
        seller = Contact(
            store_id=store.id, name="賣方", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        s.add_all([clerk, seller])
        await s.flush()
        store_id, clerk_id, seller_id = store.id, clerk.id, seller.id
        await s.commit()

    try:
        async with sm() as s:
            acq_id = await s.scalar(
                text(
                    "INSERT INTO acquisitions"
                    " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                    "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                    "  created_at, updated_at)"
                    " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 1000,"
                    "  now(), now()) RETURNING id"
                ),
                {"sid": store_id, "cid": seller_id, "uid": clerk_id},
            )
            # 無 serialized_items／bulk_lots 庫存實體就入帳
            await StoreCreditService(s).credit(
                store_id,
                seller_id,
                cash_equivalent=Decimal(1000),
                premium_rate=Decimal("0.10"),
                source_type=StoreCreditSourceType.ACQUISITION,
                source_id=acq_id,
                created_by=clerk_id,
            )
            with pytest.raises(DBAPIError):
                await s.commit()
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM acquisitions"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_inventory_mutation_cannot_break_credit_backing() -> None:
    """第二十輪 P2：購物金收購正常落地後，事後改庫存實體成本／搬走 acquisition_id
    破壞背書 → COMMIT 時被庫存側守衛擋。"""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    import app.core.db as app_db

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="背書店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="back", password_hash="h", role=UserRole.CLERK)
        seller = Contact(
            store_id=store.id, name="賣方", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        s.add_all([clerk, seller])
        await s.flush()
        store_id, clerk_id, seller_id = store.id, clerk.id, seller.id
        await s.commit()

    async with sm() as s:
        acq_id = await s.scalar(
            text(
                "INSERT INTO acquisitions"
                " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                "  created_at, updated_at)"
                " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 1000,"
                "  now(), now()) RETURNING id"
            ),
            {"sid": store_id, "cid": seller_id, "uid": clerk_id},
        )
        item_id = await s.scalar(
            text(
                "INSERT INTO serialized_items"
                " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
                "  listed_price, acquisition_id, created_at, updated_at)"
                " VALUES (:sid, :code, '測試品', 'A', 'OWNED', 1000, 1800, :aid, now(), now())"
                " RETURNING id"
            ),
            {"sid": store_id, "code": f"BACK-{acq_id}", "aid": acq_id},
        )
        await StoreCreditService(s).credit(
            store_id,
            seller_id,
            cash_equivalent=Decimal(1000),
            premium_rate=Decimal("0.10"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=acq_id,
            created_by=clerk_id,
        )
        await s.commit()  # 正常落地

    try:
        # (a) 事後把庫存成本改 0 → 背書不再等值 → COMMIT 擋
        async with sm() as s:
            await s.execute(
                text("UPDATE serialized_items SET acquisition_cost=0 WHERE id=:id"),
                {"id": item_id},
            )
            with pytest.raises(DBAPIError):
                await s.commit()

        # (b) 事後把庫存搬離本收購（acquisition_id=NULL）→ 背書歸零 → COMMIT 擋
        async with sm() as s:
            await s.execute(
                text("UPDATE serialized_items SET acquisition_id=NULL WHERE id=:id"),
                {"id": item_id},
            )
            with pytest.raises(DBAPIError):
                await s.commit()

        # (c) 事後刪掉庫存實體 → 背書歸零 → COMMIT 擋
        async with sm() as s:
            await s.execute(text("DELETE FROM serialized_items WHERE id=:id"), {"id": item_id})
            with pytest.raises(DBAPIError):
                await s.commit()
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM serialized_items"))
            await s.execute(text("DELETE FROM acquisitions"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_backing_requires_same_store_owned_inventory() -> None:
    """第二十一輪 P2：背書只認本店、店家自有庫存。以寄售品（CONSIGNMENT）湊
    BUYOUT 背書金額 → COMMIT 仍被擋（數字湊得到、卻無自有資產背書）。"""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    import app.core.db as app_db

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="背書資格店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="qual", password_hash="h", role=UserRole.CLERK)
        seller = Contact(
            store_id=store.id, name="賣方", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        s.add_all([clerk, seller])
        await s.flush()
        store_id, clerk_id, seller_id = store.id, clerk.id, seller.id
        await s.commit()

    try:
        async with sm() as s:
            acq_id = await s.scalar(
                text(
                    "INSERT INTO acquisitions"
                    " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                    "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                    "  created_at, updated_at)"
                    " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 1000,"
                    "  now(), now()) RETURNING id"
                ),
                {"sid": store_id, "cid": seller_id, "uid": clerk_id},
            )
            # 用寄售品（CONSIGNMENT，有 consignor）想湊背書金額——不算自有資產
            await s.execute(
                text(
                    "INSERT INTO serialized_items"
                    " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
                    "  consignor_id, commission_pct, listed_price, acquisition_id,"
                    "  created_at, updated_at)"
                    " VALUES (:sid, :code, '寄售品', 'A', 'CONSIGNMENT', 1000, :cons, 50,"
                    "  1800, :aid, now(), now())"
                ),
                {"sid": store_id, "code": f"QUAL-{acq_id}", "cons": seller_id, "aid": acq_id},
            )
            await StoreCreditService(s).credit(
                store_id,
                seller_id,
                cash_equivalent=Decimal(1000),
                premium_rate=Decimal("0.10"),
                source_type=StoreCreditSourceType.ACQUISITION,
                source_id=acq_id,
                created_by=clerk_id,
            )
            with pytest.raises(DBAPIError):
                await s.commit()
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM serialized_items"))
            await s.execute(text("DELETE FROM acquisitions"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_concurrent_service_callers_same_key_replay() -> None:
    """並發同 key 直呼 service（Codex 第七輪 high）：兩邊都拿到同一收購單、
    帳本只入一次（輸家在 service 層轉重放，不冒 IntegrityError）。"""
    import asyncio

    from sqlalchemy import text

    import app.core.db as app_db
    from app.modules.acquisition.schemas import AcquisitionCreate
    from app.modules.acquisition.service import AcquisitionService

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="收購冪等競態店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="acq-race", password_hash="h", role=UserRole.CLERK)
        seller = Contact(
            store_id=store.id, name="競態賣方", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        s.add_all([clerk, seller])
        await s.flush()
        store_id, clerk_id, seller_id = store.id, clerk.id, seller.id
        await s.commit()

    try:
        payload = AcquisitionCreate.model_validate(
            _buyout_payload(seller_id, payout_method="STORE_CREDIT")
        )

        async def _create() -> int:
            async with sm() as s:
                result = await AcquisitionService(s).create_acquisition(
                    store_id, clerk_id, payload, idempotency_key="race-key"
                )
                await s.commit()
                return result.acquisition_id

        ids = await asyncio.gather(_create(), _create())
        assert ids[0] == ids[1]
        async with sm() as s:
            balance = await StoreCreditService(s).get_balance(store_id, seller_id)
            assert balance == Decimal(1100)  # 只入帳一次
            count = await s.scalar(select(func.count()).select_from(Acquisition))
            assert count == 1
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM stock_movements"))
            await s.execute(text("DELETE FROM serialized_items"))
            await s.execute(text("DELETE FROM acquisitions"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_late_payout_failure_rolls_back_via_savepoint(
    db_session: AsyncSession,
) -> None:
    """savepoint 原子性（Codex 第六輪 high）：晚期失敗（溢價超出政策——只能在
    credit 內被擋）後 catch、不回滾、直接 commit → 仍零落地。"""
    import pytest

    from app.modules.acquisition.schemas import AcquisitionCreate
    from app.modules.acquisition.service import AcquisitionService
    from app.modules.inventory.models import SerializedItem
    from app.modules.settings.models import StoreSettings
    from app.shared.exceptions import DomainError

    _token, store_id, seller_id = await _seed(db_session)  # 會員、已開帳
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    # 直插超界溢價（PATCH API 會擋 0.2 以上，模擬設定被外力改壞的晚期失敗）
    db_session.add(StoreSettings(store_id=store_id, premium_rate=Decimal("0.9000")))
    await db_session.flush()
    data = AcquisitionCreate.model_validate(
        _buyout_payload(seller_id, payout_method="SPLIT", payout_split_cash="400")
    )
    svc = AcquisitionService(db_session)
    with pytest.raises(DomainError):
        await svc.create_acquisition(store_id, clerk_id, data, idempotency_key="late-fail")
    await db_session.commit()  # 粗心呼叫者
    assert await db_session.scalar(select(func.count()).select_from(Acquisition)) == 0
    assert await db_session.scalar(select(func.count()).select_from(SerializedItem)) == 0
    assert await db_session.scalar(select(func.count()).select_from(CashMovement)) == 0


async def test_service_level_retry_is_idempotent(db_session: AsyncSession) -> None:
    """service 邊界冪等必填（Codex）：router 之外的呼叫者重試也不得重複付現/入帳。"""
    from app.modules.acquisition.schemas import AcquisitionCreate
    from app.modules.acquisition.service import AcquisitionService

    _token, store_id, seller_id = await _seed(db_session, open_drawer=False)
    data = AcquisitionCreate.model_validate(
        _buyout_payload(seller_id, payout_method="STORE_CREDIT")
    )
    svc = AcquisitionService(db_session)
    clerk_id = (await db_session.execute(select(User.id))).scalar_one()
    first = await svc.create_acquisition(store_id, clerk_id, data, idempotency_key="svc-retry")
    again = await svc.create_acquisition(store_id, clerk_id, data, idempotency_key="svc-retry")
    assert again.acquisition_id == first.acquisition_id
    balance = await StoreCreditService(db_session).get_balance(store_id, seller_id)
    assert balance == Decimal(1100)  # 只入帳一次
    count = await db_session.scalar(select(func.count()).select_from(Acquisition))
    assert count == 1


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
