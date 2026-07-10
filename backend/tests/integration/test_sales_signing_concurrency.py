"""K5 簽收×作廢併發（Codex 第五輪）：TRANSACTION_ACK 簽名須與銷售作廢/退貨序列化。

以獨立 session 真併發：作廢方鎖住銷售列＋標 VOID（不 commit，持鎖），簽名方必須**卡在
銷售列鎖**上（_ensure_sale_ackable FOR UPDATE），待作廢 commit 後才續行、看到 VOID →
任務作廢＋拒簽——最終狀態不可能是 SIGNED。
"""

import asyncio
import base64
import zlib
from decimal import Decimal

from sqlalchemy import delete, select, text

import app.core.db as app_db
from app.core.audit import AuditLog
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.sales.schemas import SaleLineCreateRequest
from app.modules.sales.service import SalesService
from app.modules.signing.models import SignatureTask
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleInvoiceStatus, SignatureTaskKind, SignatureTaskStatus, UserRole
from app.shared.exceptions import SignatureTaskInvalidated


def _png() -> str:
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return len(d).to_bytes(4, "big") + t + d + zlib.crc32(t + d).to_bytes(4, "big")

    raw = bytearray()
    for y in range(80):
        raw.append(0)
        for _x in range(200):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    ihdr = (200).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    png = magic + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(
        b"IEND", b""
    )
    return base64.b64encode(png).decode()


async def test_void_in_progress_serializes_ack_sign() -> None:
    """作廢持銷售列鎖進行中時，簽收簽名須卡鎖；作廢 commit 後簽名被拒、任務 CANCELLED。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="簽收競態店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="c", password_hash="h", role=UserRole.CLERK)
        member = Contact(store_id=store.id, name="買家", phone="0912000333", roles=["MEMBER"])
        product = CatalogProduct(
            store_id=store.id,
            sku="ACK1",
            name="飲料",
            unit_price=Decimal("100"),
            quantity_on_hand=5,
        )
        s.add_all([clerk, member, product])
        await s.flush()
        store_id, clerk_id, member_id = store.id, clerk.id, member.id
        await CashDrawerService(s).open_session(store_id, clerk_id, Decimal("1000"))
        sale = await SalesService(s).create_sale(
            store_id,
            clerk_id,
            lines=[
                SaleLineCreateRequest(
                    line_type="CATALOG", catalog_product_id=product.id, qty=1
                ).to_input()
            ],
            buyer_contact_id=member_id,
            idempotency_key="ack-race-sale",
        )
        sale_id = sale.id
        task = await SigningService(s).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.TRANSACTION_ACK,
                contact_id=member_id,
                content={},
                ref_type="sale",
                ref_id=sale_id,
            ),
            created_by=clerk_id,
        )
        task_id = task.id
        await s.commit()

    try:
        # 作廢方：鎖住銷售列＋標 VOID，持鎖不 commit。
        async with sm() as void_s:
            locked = await void_s.scalar(
                select(Sale).where(Sale.id == sale_id).with_for_update()
            )
            assert locked is not None
            locked.invoice_status = SaleInvoiceStatus.VOID
            await void_s.flush()  # 持有銷售行鎖

            async def do_sign() -> str:
                async with sm() as sign_s:
                    try:
                        await SigningService(sign_s).sign_task(
                            store_id, task_id, signature_image_base64=_png(), chosen_payout=None
                        )
                        await sign_s.commit()
                        return "signed"
                    except SignatureTaskInvalidated:
                        await sign_s.commit()  # 任務作廢須提交（router 同款語意）
                        return "invalidated"

            sign_run = asyncio.create_task(do_sign())
            await asyncio.sleep(0.5)
            # 簽名方應卡在銷售行鎖上（_ensure_sale_ackable FOR UPDATE），尚未完成。
            assert not sign_run.done(), "簽收簽名未被銷售行鎖序列化"
            await void_s.commit()  # 釋放鎖，銷售已 VOID
            outcome = await sign_run
        assert outcome == "invalidated", outcome
        # 最終狀態：任務 CANCELLED、絕非 SIGNED。
        async with sm() as s:
            final = await s.scalar(
                select(SignatureTask.status).where(SignatureTask.id == task_id)
            )
            assert final is SignatureTaskStatus.CANCELLED, final
    finally:
        # 真收購/銷售副作用（庫存/現金/稽核）；暫停本 session FK 觸發，由葉往根順序無關清列。
        async with sm() as s:
            await s.execute(text("SET session_replication_role = replica"))
            for model in (
                SaleTender,
                SaleLine,
                StockMovement,
                SignatureTask,
                CashMovement,
                CashSession,
                AuditLog,
            ):
                await s.execute(delete(model).where(model.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(delete(CatalogProduct).where(CatalogProduct.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_signed_mixed_tender_keeps_cash_first_lock_order() -> None:
    """簽署混合收款（現金＋購物金）不得先鎖購物金帳戶再等現金班別（Codex K5 第十輪 AB-BA）。

    佈局：A 方持有 cash_session 行鎖（模擬進行中的 SPLIT 收購已鎖現金）→ B 方簽署混合結帳
    必須**卡在現金鎖上、且未持有帳戶鎖**——此時 A 再鎖同會員的購物金帳戶列必須立即成功
    （若 B 先鎖了帳戶＝舊碼鎖序倒置，這一步會互等死結）。A commit 後 B 順利完成。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="鎖序店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="c", password_hash="h", role=UserRole.CLERK)
        member = Contact(store_id=store.id, name="買家", phone="0912000444", roles=["MEMBER"])
        product = CatalogProduct(
            store_id=store.id,
            sku="LOCK1",
            name="爐具",
            unit_price=Decimal("300"),
            quantity_on_hand=5,
        )
        s.add_all([clerk, member, product])
        await s.flush()
        store_id, clerk_id, member_id, product_id = store.id, clerk.id, member.id, product.id
        session_row = await CashDrawerService(s).open_session(store_id, clerk_id, Decimal("1000"))
        cash_session_id = session_row.id
        # 入帳購物金 500（完整背書收購）
        acq_id = await s.scalar(
            text(
                "INSERT INTO acquisitions"
                " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                "  created_at, updated_at)"
                " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 500, now(), now())"
                " RETURNING id"
            ),
            {"sid": store_id, "cid": member_id, "uid": clerk_id},
        )
        await s.execute(
            text(
                "INSERT INTO serialized_items"
                " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
                "  listed_price, acquisition_id, created_at, updated_at)"
                " VALUES (:sid, 'LOCKCRED', '收購品', 'A', 'OWNED', 500, 500, :aid, now(), now())"
            ),
            {"sid": store_id, "aid": acq_id},
        )
        from app.modules.storecredit.service import StoreCreditService
        from app.shared.enums import StoreCreditSourceType

        await StoreCreditService(s).credit(
            store_id,
            member_id,
            cash_equivalent=Decimal("500"),
            premium_rate=Decimal("0"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=acq_id,
            created_by=clerk_id,
        )
        # 簽署：折抵 100、合計 300（現金 200＋購物金 100）
        svc = SigningService(s)
        task = await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.STORE_CREDIT_USE,
                contact_id=member_id,
                content={"debit": "100", "sale_total": "300"},
            ),
            created_by=clerk_id,
        )
        await svc.sign_task(
            store_id, task.id, signature_image_base64=_png(), chosen_payout=None
        )
        task_id = task.id
        await s.commit()

    try:
        async with sm() as a_s:
            # A：鎖 cash_session 行（模擬 SPLIT 收購的現金階段）
            locked_cs = await a_s.scalar(
                select(CashSession).where(CashSession.id == cash_session_id).with_for_update()
            )
            assert locked_cs is not None

            async def do_checkout() -> str:
                async with sm() as b_s:
                    from app.modules.sales.schemas import SaleTenderRequest

                    sale = await SalesService(b_s).create_sale(
                        store_id,
                        clerk_id,
                        lines=[
                            SaleLineCreateRequest(
                                line_type="CATALOG", catalog_product_id=product_id, qty=1
                            ).to_input()
                        ],
                        buyer_contact_id=member_id,
                        tenders=[
                            SaleTenderRequest(tender_type="CASH", amount=Decimal("200")).to_input(),
                            SaleTenderRequest(
                                tender_type="STORE_CREDIT", amount=Decimal("100")
                            ).to_input(),
                        ],
                        idempotency_key="lock-order-mixed",
                        signature_task_id=task_id,
                    )
                    await b_s.commit()
                    return f"sale-{sale.id}"

            checkout_run = asyncio.create_task(do_checkout())
            await asyncio.sleep(0.6)
            # B 應卡在現金鎖上（未完成）
            assert not checkout_run.done(), "混合結帳未被現金班別行鎖擋住（鎖序可能又變）"
            # 關鍵：A 此刻鎖同會員帳戶列必須**立即**成功——若 B 已先持帳戶鎖＝AB-BA。
            from app.modules.storecredit.models import StoreCreditAccount

            probe = await asyncio.wait_for(
                a_s.scalar(
                    select(StoreCreditAccount)
                    .where(
                        StoreCreditAccount.store_id == store_id,
                        StoreCreditAccount.contact_id == member_id,
                    )
                    .with_for_update()
                ),
                timeout=3.0,
            )
            assert probe is not None
            await a_s.commit()  # 釋放兩把鎖，B 續行
            outcome = await checkout_run
        assert outcome.startswith("sale-"), outcome
    finally:
        async with sm() as s:
            await s.execute(text("SET session_replication_role = replica"))
            for model in (SaleTender, SaleLine, StockMovement, SignatureTask, CashMovement,
                          CashSession, AuditLog):
                await s.execute(delete(model).where(model.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(
                text("DELETE FROM store_credit_ledger WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(
                text("DELETE FROM store_credit_accounts WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(
                text("DELETE FROM serialized_items WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(
                text("DELETE FROM acquisitions WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(delete(CatalogProduct).where(CatalogProduct.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_checkout_binding_serializes_with_signed_task_cancel() -> None:
    """結帳綁定讀取持任務行鎖時，作廢須卡鎖等待（Codex K5 第十二輪）：兩者只能整體先後，
    不可能「銷售已扣款、任務證據卻寫作廢」。"""
    from app.modules.storecredit.service import StoreCreditService
    from app.shared.enums import StoreCreditSourceType
    from app.shared.exceptions import SignatureTaskNotPending

    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="綁定作廢競態店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="c", password_hash="h", role=UserRole.CLERK)
        member = Contact(store_id=store.id, name="買家", phone="0912000555", roles=["MEMBER"])
        s.add_all([clerk, member])
        await s.flush()
        store_id, clerk_id, member_id = store.id, clerk.id, member.id
        acq_id = await s.scalar(
            text(
                "INSERT INTO acquisitions"
                " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
                "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
                "  created_at, updated_at)"
                " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 500, now(), now())"
                " RETURNING id"
            ),
            {"sid": store_id, "cid": member_id, "uid": clerk_id},
        )
        await s.execute(
            text(
                "INSERT INTO serialized_items"
                " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
                "  listed_price, acquisition_id, created_at, updated_at)"
                " VALUES (:sid, 'RACECRED', '收購品', 'A', 'OWNED', 500, 500, :aid, now(), now())"
            ),
            {"sid": store_id, "aid": acq_id},
        )
        await StoreCreditService(s).credit(
            store_id,
            member_id,
            cash_equivalent=Decimal("500"),
            premium_rate=Decimal("0"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=acq_id,
            created_by=clerk_id,
        )
        svc = SigningService(s)
        task = await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.STORE_CREDIT_USE,
                contact_id=member_id,
                content={"debit": "100", "sale_total": "100"},
            ),
            created_by=clerk_id,
        )
        await svc.sign_task(
            store_id, task.id, signature_image_base64=_png(), chosen_payout=None
        )
        task_id = task.id
        await s.commit()

    try:
        async with sm() as bind_s:
            # 結帳綁定讀取：FOR UPDATE 鎖任務列（持鎖，模擬結帳交易進行中）
            got = await SigningService(bind_s).get_signed_store_credit_task(
                store_id, task_id, contact_id=member_id
            )
            assert got.id == task_id

            async def do_cancel() -> str:
                async with sm() as c_s:
                    try:
                        await SigningService(c_s).cancel_task(store_id, task_id)
                        await c_s.commit()
                        return "cancelled"
                    except SignatureTaskNotPending:
                        await c_s.rollback()
                        return "rejected"

            cancel_run = asyncio.create_task(do_cancel())
            await asyncio.sleep(0.5)
            # 作廢應卡在任務行鎖上，尚未完成。
            assert not cancel_run.done(), "作廢未被結帳綁定的任務行鎖序列化"
            await bind_s.rollback()  # 模擬結帳最終未成立 → 釋放鎖
            outcome = await cancel_run
        # 結帳未成立 → 作廢成功收回授權；反向順序（結帳先 commit）由既有 409 測試覆蓋。
        assert outcome == "cancelled", outcome
    finally:
        async with sm() as s:
            await s.execute(text("SET session_replication_role = replica"))
            await s.execute(delete(SignatureTask).where(SignatureTask.store_id == store_id))
            await s.execute(
                text("DELETE FROM store_credit_ledger WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(
                text("DELETE FROM store_credit_accounts WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(
                text("DELETE FROM serialized_items WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(
                text("DELETE FROM acquisitions WHERE store_id = :sid"), {"sid": store_id}
            )
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
