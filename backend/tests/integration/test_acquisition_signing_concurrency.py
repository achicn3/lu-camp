"""K4 身分綁定併發（Codex 第八輪）：收購綁定的身分指紋比對須與 contacts 證號編輯序列化。

以獨立 session 真併發：一方鎖住會員列並改證號（不 commit，持鎖），另一方的收購必須**卡在
會員列鎖**上，待前者 commit（證號變）後才續行、看到新指紋→身分不符 422——證明「比對後、
commit 前被並發改證號」不可能發生。結束在 finally 清列，不留殘餘。
"""

import asyncio
import base64
import zlib
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select, text

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, SerializedItem, StockMovement
from app.modules.signing.models import SignatureTask
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, PayoutMethod, SignatureTaskKind, UserRole
from app.shared.exceptions import SignatureContentMismatch, SignatureTaskConflict


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


async def test_identity_edit_serialized_with_binding() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發身分店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="c", password_hash="h", role=UserRole.CLERK)
        contact = Contact(
            store_id=store.id,
            name="賣家",
            phone="0912000111",
            national_id_enc=get_pii_cipher().encrypt("A123456789"),
            national_id_blind_index=national_id_blind_index("A123456789"),
            roles=["SELLER", "MEMBER"],
        )
        s.add_all([clerk, contact])
        await s.flush()
        store_id, clerk_id, contact_id = store.id, clerk.id, contact.id
        await CashDrawerService(s).open_session(store_id, clerk_id, Decimal("1000"))
        svc = SigningService(s)
        task = await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=contact_id,
                content={"items": [{"name": "相機", "amount": "1800"}], "total": "1800"},
            ),
            created_by=clerk_id,
        )
        await svc.sign_task(
            store_id, task.id, signature_image_base64=_png(), chosen_payout=PayoutMethod.CASH
        )
        task_id = task.id
        await s.commit()

    payload = AcquisitionCreate(
        type="BUYOUT",
        contact_id=contact_id,
        items=[
            AcquisitionItemIn(
                name="相機", grade=Grade.A, listed_price="3000", acquisition_cost="1800"
            )
        ],
        payout_method=PayoutMethod.CASH,
        signature_task_id=task_id,
    )

    try:
        # 編輯方：鎖住會員列＋改證號，持鎖不 commit。
        async with sm() as edit_s:
            locked = await edit_s.scalar(
                select(Contact).where(Contact.id == contact_id).with_for_update()
            )
            assert locked is not None
            locked.national_id_enc = get_pii_cipher().encrypt("A999999999")
            locked.national_id_blind_index = national_id_blind_index("A999999999")
            await edit_s.flush()  # 持有 contact 行鎖，尚未 commit

            async def do_acq() -> str:
                async with sm() as acq_s:
                    try:
                        await AcquisitionService(acq_s).create_acquisition(
                            store_id, clerk_id, payload, idempotency_key="race-1"
                        )
                        await acq_s.commit()
                        return "created"
                    except SignatureContentMismatch:
                        await acq_s.rollback()
                        return "rejected"

            task_acq = asyncio.create_task(do_acq())
            await asyncio.sleep(0.5)
            # 收購方應卡在 contact 行鎖上（FOR UPDATE），尚未完成。
            assert not task_acq.done(), "收購未被 contact 行鎖序列化（可能繞過鎖）"
            await edit_s.commit()  # 釋放鎖，證號已變為 A999
            result = await task_acq
        # 收購續行後看到新指紋 → 身分不符 → 拒絕（不會綁到已失真身分）。
        assert result == "rejected", result
    finally:
        # 本測試以真 session 提交資料；FK 順序清列，不留殘餘干擾其他測試。
        async with sm() as s:
            await s.execute(delete(SignatureTask).where(SignatureTask.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_void_in_progress_serializes_replay() -> None:
    """void 持收購列鎖進行中時，切結回放須卡在列鎖、待 void commit 後看到已作廢→409（第十五輪）。

    杜絕「回放讀到 void 前舊版（voided_at=NULL）而回 201、前端又開櫃/印標」的競態。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="void競態店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="c", password_hash="h", role=UserRole.CLERK)
        contact = Contact(
            store_id=store.id,
            name="賣家",
            phone="0912000222",
            national_id_enc=get_pii_cipher().encrypt("A123456789"),
            national_id_blind_index=national_id_blind_index("A123456789"),
            roles=["SELLER", "MEMBER"],
        )
        s.add_all([clerk, contact])
        await s.flush()
        store_id, clerk_id, contact_id = store.id, clerk.id, contact.id
        await CashDrawerService(s).open_session(store_id, clerk_id, Decimal("5000"))
        svc = SigningService(s)
        task = await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=contact_id,
                content={"items": [{"name": "相機", "amount": "1800"}], "total": "1800"},
            ),
            created_by=clerk_id,
        )
        await svc.sign_task(
            store_id, task.id, signature_image_base64=_png(), chosen_payout=PayoutMethod.CASH
        )
        payload = AcquisitionCreate(
            type="BUYOUT",
            contact_id=contact_id,
            items=[
                AcquisitionItemIn(
                    name="相機", grade=Grade.A, listed_price="3000", acquisition_cost="1800"
                )
            ],
            payout_method=PayoutMethod.CASH,
            signature_task_id=task.id,
        )
        result = await AcquisitionService(s).create_acquisition(
            store_id, clerk_id, payload, idempotency_key="void-race-first"
        )
        acq_id = result.acquisition_id
        await s.commit()

    try:
        # void 進行中：鎖住收購列＋設 voided_at，持鎖不 commit。
        async with sm() as void_s:
            locked = await void_s.scalar(
                select(Acquisition).where(Acquisition.id == acq_id).with_for_update()
            )
            assert locked is not None
            locked.voided_at = datetime.now(UTC)
            await void_s.flush()  # 持有收購行鎖，尚未 commit

            async def do_replay() -> str:
                async with sm() as r_s:
                    try:
                        await AcquisitionService(r_s).create_acquisition(
                            store_id, clerk_id, payload, idempotency_key="void-race-retry-newkey"
                        )
                        await r_s.commit()
                        return "created"
                    except SignatureTaskConflict:
                        await r_s.rollback()
                        return "rejected"

            task_replay = asyncio.create_task(do_replay())
            await asyncio.sleep(0.5)
            # 回放應卡在收購行鎖上（切結查 FOR UPDATE），尚未完成。
            assert not task_replay.done(), "回放未被 void 的收購行鎖序列化"
            await void_s.commit()  # 釋放鎖，收購已作廢
            outcome = await task_replay
        assert outcome == "rejected", outcome
    finally:
        # 本測試建立了帶完整副作用（庫存/現金/稽核）的真收購；清列時暫停本 session 的 FK 觸發
        # （lucamp 為資料表擁有者），即可不受深層 FK 圖牽制、順序無關地清乾淨，不留殘餘。
        async with sm() as s:
            await s.execute(text("SET session_replication_role = replica"))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(BulkLot).where(BulkLot.store_id == store_id))
            await s.execute(delete(Acquisition).where(Acquisition.store_id == store_id))
            await s.execute(delete(SignatureTask).where(SignatureTask.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
