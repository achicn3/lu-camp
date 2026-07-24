"""收購作廢併發（F6.5）：兩個並行 void 只一個成功、退款/稽核恰一筆。

真並行（asyncio.gather）兩個獨立交易的 void 請求（不覆寫 get_session）。void_acquisition 先以
SELECT … FOR UPDATE 鎖收購列＋刷新已提交狀態（比照 D-1/sales void），故只一個設 voided_at 成功、
另一個鎖後見已作廢 → AcquisitionAlreadyVoid → 409；ACQUISITION_VOID_IN 退款與 VOID_ACQUISITION
稽核皆恰一筆（不雙重沖回）。
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import delete, func, select, text

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct, SerializedItem, StockMovement
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    CashMovementType,
    Grade,
    PayoutMethod,
    UserRole,
)
from tests.integration.customer_display_helpers import (
    delete_customer_display_rows,
    prepare_signed_store_credit_cart,
)


@pytest_asyncio.fixture
async def real_client() -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_concurrent_void_only_one_succeeds(real_client: httpx.AsyncClient) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發作廢收購店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="acqv-clk", password_hash="h", role=UserRole.CLERK)
        mgr = User(store_id=store.id, username="acqv-mgr", password_hash="h", role=UserRole.MANAGER)
        seller = Contact(store_id=store.id, name="賣方", roles=["SELLER"], national_id_enc="enc")
        s.add_all([clerk, mgr, seller])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("5000"))
        result = await AcquisitionService(s).create_acquisition(
            store.id,
            clerk.id,
            AcquisitionCreate(
                type=AcquisitionType.BUYOUT,
                contact_id=seller.id,
                items=[
                    AcquisitionItemIn(
                        name="帳篷",
                        grade=Grade.A,
                        listed_price=Decimal("1800"),
                        acquisition_cost=Decimal("1000"),
                    )
                ],
            ),
            idempotency_key="acqv-create",
        )
        store_id, acq_id = store.id, result.acquisition_id
        token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
        await s.commit()

    headers = {"Authorization": f"Bearer {token}"}
    try:
        url = f"/api/v1/acquisitions/{acq_id}/void"
        r1, r2 = await asyncio.gather(
            real_client.post(url, json={"reason": "x"}, headers=headers),
            real_client.post(url, json={"reason": "x"}, headers=headers),
        )
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]  # 恰一成功、一被擋

        async with sm() as s:
            acq = await s.get(Acquisition, acq_id)
            assert acq is not None and acq.voided_at is not None
            void_in_count = await s.scalar(
                select(func.count())
                .select_from(CashMovement)
                .where(
                    CashMovement.store_id == store_id,
                    CashMovement.type == CashMovementType.ACQUISITION_VOID_IN,
                )
            )
            assert void_in_count == 1  # 退款恰一筆（不雙重沖回）
            audit_count = await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.store_id == store_id, AuditLog.action == "VOID_ACQUISITION")
            )
            assert audit_count == 1  # 稽核恰一筆
    finally:
        async with sm() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Acquisition).where(Acquisition.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_concurrent_sale_vs_void_no_deadlock(real_client: httpx.AsyncClient) -> None:
    """並行『售出該品』vs『作廢其收購』：鎖序與 sales 一致(先庫存後現金)，不得 DB 死結 500；
    同一序號品不可既賣出又作廢——恰一方成功、另一方乾淨被擋。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="並行銷售作廢店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="svv-clk", password_hash="h", role=UserRole.CLERK)
        mgr = User(store_id=store.id, username="svv-mgr", password_hash="h", role=UserRole.MANAGER)
        seller = Contact(store_id=store.id, name="賣方", roles=["SELLER"], national_id_enc="enc")
        s.add_all([clerk, mgr, seller])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("5000"))
        result = await AcquisitionService(s).create_acquisition(
            store.id,
            clerk.id,
            AcquisitionCreate(
                type=AcquisitionType.BUYOUT,
                contact_id=seller.id,
                items=[
                    AcquisitionItemIn(
                        name="帳篷",
                        grade=Grade.A,
                        listed_price=Decimal("1800"),
                        acquisition_cost=Decimal("1000"),
                    )
                ],
            ),
            idempotency_key="svv-create",
        )
        store_id, acq_id = store.id, result.acquisition_id
        item = await s.scalar(select(SerializedItem).where(SerializedItem.acquisition_id == acq_id))
        assert item is not None
        item_code = item.item_code
        clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
        await s.commit()

    try:
        sale_headers = {"Authorization": f"Bearer {clerk_token}", "Idempotency-Key": "svv-sale"}
        void_headers = {"Authorization": f"Bearer {mgr_token}"}
        r_sale, r_void = await asyncio.gather(
            real_client.post(
                "/api/v1/sales",
                json={"lines": [{"line_type": "SERIALIZED", "item_code": item_code, "qty": 1}]},
                headers=sale_headers,
            ),
            real_client.post(
                f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=void_headers
            ),
        )
        # 無 DB 死結 500
        assert r_sale.status_code != 500, r_sale.text
        assert r_void.status_code != 500, r_void.text
        sale_ok = r_sale.status_code == 201
        void_ok = r_void.status_code == 200
        assert sale_ok ^ void_ok  # 恰一方成功
        if void_ok:
            assert r_sale.status_code >= 400  # 品已退場 → 銷售被擋
        else:
            assert r_void.status_code == 409  # 品已售出 → 作廢被擋
    finally:
        async with sm() as s:
            await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
            await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Acquisition).where(Acquisition.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_concurrent_split_void_vs_credit_first_mixed_sale_no_deadlock(
    real_client: httpx.AsyncClient,
) -> None:
    """SPLIT 作廢(cash_session→account) vs『購物金-先』混合銷售(account→cash_session) 同一 contact：
    sales _apply_tenders 已固定 CASH 先於 STORE_CREDIT，與作廢 cash→credit 同一全域鎖序，不得 AB-BA
    死結 500。若作廢先改變餘額，簽署快照必須讓銷售乾淨失敗；若銷售先鎖定，兩者可依序成功。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="混合收款作廢店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="mx-clk", password_hash="h", role=UserRole.CLERK)
        mgr = User(store_id=store.id, username="mx-mgr", password_hash="h", role=UserRole.MANAGER)
        member = Contact(
            store_id=store.id, name="會員", roles=["SELLER", "MEMBER"], national_id_enc="enc"
        )
        s.add_all([clerk, mgr, member])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("5000"))
        acq_svc = AcquisitionService(s)
        # SPLIT 收購：作廢須同時沖回現金與購物金（鎖 cash_session 與 account）
        acq = await acq_svc.create_acquisition(
            store.id,
            clerk.id,
            AcquisitionCreate(
                type=AcquisitionType.BUYOUT,
                contact_id=member.id,
                payout_method=PayoutMethod.SPLIT,
                payout_split_cash=Decimal("600"),
                items=[
                    AcquisitionItemIn(
                        name="帳篷",
                        grade=Grade.A,
                        listed_price=Decimal("1800"),
                        acquisition_cost=Decimal("1000"),
                    )
                ],
            ),
            idempotency_key="mx-create",
        )
        # 另一筆獨立 STORE_CREDIT 收購給足購物金（作廢只沖回上面那筆 SPLIT 的入帳，這筆不動），
        # 確保銷售扣抵與作廢沖回都不會讓餘額為負——隔離「鎖序」與「餘額相爭」。
        await acq_svc.create_acquisition(
            store.id,
            clerk.id,
            AcquisitionCreate(
                type=AcquisitionType.BUYOUT,
                contact_id=member.id,
                payout_method=PayoutMethod.STORE_CREDIT,
                items=[
                    AcquisitionItemIn(
                        name="睡袋",
                        grade=Grade.A,
                        listed_price=Decimal("3000"),
                        acquisition_cost=Decimal("2000"),
                    )
                ],
            ),
            idempotency_key="mx-credit",
        )
        catalog = CatalogProduct(
            store_id=store.id,
            sku="MX1",
            name="飲料",
            unit_price=Decimal("150"),
            quantity_on_hand=10,
        )
        s.add(catalog)
        await s.flush()
        store_id, acq_id = store.id, acq.acquisition_id
        catalog_id, member_id = catalog.id, member.id
        clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
        signed = await prepare_signed_store_credit_cart(
            s,
            store_id=store.id,
            actor_user_id=clerk.id,
            payload={
                "buyer_contact_id": member.id,
                "lines": [
                    {
                        "line_type": "CATALOG",
                        "catalog_product_id": catalog.id,
                        "qty": 1,
                    }
                ],
                "tenders": [
                    {"tender_type": "STORE_CREDIT", "amount": "100"},
                    {"tender_type": "CASH", "amount": "50"},
                ],
            },
        )
        await s.commit()

    try:
        sale_headers = {"Authorization": f"Bearer {clerk_token}", "Idempotency-Key": "mx-sale"}
        void_headers = {"Authorization": f"Bearer {mgr_token}"}
        r_sale, r_void = await asyncio.gather(
            real_client.post(
                "/api/v1/sales",
                json={
                    "buyer_contact_id": member_id,
                    "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 1}],
                    # 客戶端送『購物金-先』：未修前會先鎖 account 再鎖 cash_session
                    "tenders": [
                        {"tender_type": "STORE_CREDIT", "amount": "100"},
                        {"tender_type": "CASH", "amount": "50"},
                    ],
                    "signature_task_id": signed.signature_task_id,
                    "cart_session_id": signed.cart_session_id,
                    "cart_revision": signed.cart_revision,
                },
                headers=sale_headers,
            ),
            real_client.post(
                f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=void_headers
            ),
        )
        # 無 DB 死結 500；作廢一定成功。銷售若較晚取得鎖，因簽署後餘額已變動而安全拒絕。
        assert r_sale.status_code != 500, r_sale.text
        assert r_void.status_code == 200, r_void.text
        assert r_sale.status_code in {201, 422}, r_sale.text
        if r_sale.status_code == 422:
            assert "餘額已變動" in r_sale.text
    finally:
        async with sm() as s:
            # 帳本 insert-only（ADR-012）連 DELETE 都擋；清理用 TRUNCATE（不觸發列級 trigger）。
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await delete_customer_display_rows(s, store_id=store_id)
            await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
            await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CatalogProduct).where(CatalogProduct.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Acquisition).where(Acquisition.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_concurrent_multi_item_reverse_sale_vs_void_no_deadlock(
    real_client: httpx.AsyncClient,
) -> None:
    """多件同收購、購物車反序的銷售 vs 作廢並發：銷售已依 id 序前置鎖序(與作廢一致)，不得 AB-BA
    死結 500；同一批序號品不可既全售出又全作廢 → 恰一方成功、另一方乾淨被擋。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="多件反序作廢店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="mr-clk", password_hash="h", role=UserRole.CLERK)
        mgr = User(store_id=store.id, username="mr-mgr", password_hash="h", role=UserRole.MANAGER)
        seller = Contact(store_id=store.id, name="賣方", roles=["SELLER"], national_id_enc="enc")
        s.add_all([clerk, mgr, seller])
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("5000"))
        result = await AcquisitionService(s).create_acquisition(
            store.id,
            clerk.id,
            AcquisitionCreate(
                type=AcquisitionType.BUYOUT,
                contact_id=seller.id,
                items=[
                    AcquisitionItemIn(
                        name="帳篷A",
                        grade=Grade.A,
                        listed_price=Decimal("500"),
                        acquisition_cost=Decimal("300"),
                    ),
                    AcquisitionItemIn(
                        name="帳篷B",
                        grade=Grade.A,
                        listed_price=Decimal("500"),
                        acquisition_cost=Decimal("300"),
                    ),
                ],
            ),
            idempotency_key="mr-create",
        )
        store_id, acq_id = store.id, result.acquisition_id
        codes = list(
            (
                await s.scalars(
                    select(SerializedItem.item_code)
                    .where(SerializedItem.acquisition_id == acq_id)
                    .order_by(SerializedItem.id)
                )
            ).all()
        )
        assert len(codes) == 2
        code_low, code_high = codes[0], codes[1]
        clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
        await s.commit()

    try:
        sale_headers = {"Authorization": f"Bearer {clerk_token}", "Idempotency-Key": "mr-sale"}
        void_headers = {"Authorization": f"Bearer {mgr_token}"}
        # 購物車反序（高 id 先）：未修前以購物車序鎖 high→low，與作廢 low→high 反向 → AB-BA
        r_sale, r_void = await asyncio.gather(
            real_client.post(
                "/api/v1/sales",
                json={
                    "lines": [
                        {"line_type": "SERIALIZED", "item_code": code_high, "qty": 1},
                        {"line_type": "SERIALIZED", "item_code": code_low, "qty": 1},
                    ]
                },
                headers=sale_headers,
            ),
            real_client.post(
                f"/api/v1/acquisitions/{acq_id}/void", json={"reason": "x"}, headers=void_headers
            ),
        )
        assert r_sale.status_code != 500, r_sale.text
        assert r_void.status_code != 500, r_void.text
        sale_ok = r_sale.status_code == 201
        void_ok = r_void.status_code == 200
        assert sale_ok ^ void_ok  # 恰一方成功
        if void_ok:
            assert r_sale.status_code >= 400  # 品已退場 → 銷售被擋
        else:
            assert r_void.status_code == 409  # 品已售出 → 作廢被擋
    finally:
        async with sm() as s:
            await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
            await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Acquisition).where(Acquisition.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
