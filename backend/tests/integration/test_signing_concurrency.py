"""signing 併發不變量：同店同時最多一件 PENDING 簽署任務（Codex 第二輪 high）。

需要真正的兩條交易並行，故用獨立 session（各自 commit），不走 db_session 回滾隔離；
最終防線是 signature_tasks 的 partial unique index（status='PENDING'），且輸家必須拿到
可重試的 SignatureTaskConflict（不得漏出裸 IntegrityError → 500）。
測試結束在 finally 清掉自建的列，不留殘餘。
"""

import asyncio

from sqlalchemy import delete, select

import app.core.db as app_db
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.modules.contacts.models import Contact
from app.modules.signing.models import SignatureTask
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import PayoutMethod, SignatureTaskKind, SignatureTaskStatus, UserRole
from app.shared.exceptions import (
    SignatureTaskConflict,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
)


async def test_concurrent_create_keeps_single_pending() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發簽署店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="conc-sign", password_hash="h", role=UserRole.CLERK
        )
        contact = Contact(
            store_id=store.id,
            name="併發客",
            phone="0955555555",
            national_id_enc=get_pii_cipher().encrypt("A123456789"),
            national_id_blind_index=national_id_blind_index("A123456789"),
            roles=["SELLER"],
        )
        s.add_all([clerk, contact])
        await s.flush()
        store_id, clerk_id, contact_id = store.id, clerk.id, contact.id
        await s.commit()

    try:

        async def create_once() -> bool:
            async with sm() as s:
                try:
                    await SigningService(s).create_task(
                        store_id,
                        SignatureTaskCreate(
                            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                            contact_id=contact_id,
                            content={"total": "100"},
                        ),
                        created_by=clerk_id,
                    )
                    await s.commit()
                    return True
                except SignatureTaskConflict:
                    await s.rollback()
                    return False

        # 兩條獨立交易同時建立：可能其一撞唯一索引（收斂為 SignatureTaskConflict），
        # 也可能後者先作廢前者再建（合法的「重推＝舊單作廢」）。裸 IntegrityError
        # 逸出即測試失敗。不變量：結束時同店恰好一件 PENDING。
        results = await asyncio.gather(create_once(), create_once())
        assert any(results)

        async with sm() as s:
            pending = (
                await s.scalars(
                    select(SignatureTask).where(
                        SignatureTask.store_id == store_id,
                        SignatureTask.status == SignatureTaskStatus.PENDING,
                    )
                )
            ).all()
            assert len(pending) == 1
    finally:
        async with sm() as s:
            await s.execute(delete(SignatureTask).where(SignatureTask.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.id == contact_id))
            await s.execute(delete(User).where(User.id == clerk_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            # 條款版本為全域列（無 store_id）：僅在本測試種下時清除會誤刪他測資料，
            # 但 lazy 種子冪等且 lucamp_pytest 每輪重建，留存無害。
            await s.commit()


async def test_repush_and_old_sign_serialized() -> None:
    """重推 vs 舊頁簽名的競態：per-store advisory lock 使兩者序列化（Codex 第八輪 medium）。

    無鎖時，重推的 cancel_pending_tasks 可在客人送簽「之間」錯過剛被簽的舊列，留下
    「對已被取代內容的有效簽名」＋一張新待簽任務。加鎖後結果必為乾淨的二擇一：
    重推先 → 舊任務 CANCELLED、簽名 409；簽名先 → 舊任務 SIGNED、重推另建新待簽。
    不變量（兩序皆須成立）：同店 PENDING ≤ 1，且舊任務落於 SIGNED/CANCELLED 終態。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="重推競態店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="repush-clerk", password_hash="h", role=UserRole.CLERK
        )
        contact = Contact(
            store_id=store.id,
            name="重推客",
            phone="0955000111",
            national_id_enc=get_pii_cipher().encrypt("B123456780"),
            national_id_blind_index=national_id_blind_index("B123456780"),
            roles=["SELLER"],
        )
        s.add_all([clerk, contact])
        await s.flush()
        store_id, clerk_id, contact_id = store.id, clerk.id, contact.id
        old_task = SignatureTask(
            store_id=store_id,
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content={"total": "100"},
            agreement_version_id=(await SigningService(s)._get_or_seed_current_agreement()).id,
            created_by=clerk_id,
        )
        s.add(old_task)
        await s.flush()
        old_id = old_task.id
        await s.commit()

    png = _nonblank_png_base64()

    try:

        async def repush() -> str:
            async with sm() as s:
                try:
                    await SigningService(s).create_task(
                        store_id,
                        SignatureTaskCreate(
                            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                            contact_id=contact_id,
                            content={"total": "200"},
                        ),
                        created_by=clerk_id,
                    )
                    await s.commit()
                    return "repushed"
                except SignatureTaskConflict:
                    await s.rollback()
                    return "conflict"

        async def sign_old() -> str:
            async with sm() as s:
                try:
                    await SigningService(s).sign_task(
                        store_id,
                        old_id,
                        signature_image_base64=png,
                        chosen_payout=PayoutMethod.CASH,
                    )
                    await s.commit()
                    return "signed"
                except (SignatureTaskNotPending, SignatureTaskNotFound):
                    await s.rollback()
                    return "rejected"

        await asyncio.gather(repush(), sign_old())

        async with sm() as s:
            pending = (
                await s.scalars(
                    select(SignatureTask).where(
                        SignatureTask.store_id == store_id,
                        SignatureTask.status == SignatureTaskStatus.PENDING,
                    )
                )
            ).all()
            assert len(pending) <= 1
            old = await s.get(SignatureTask, old_id)
            assert old is not None
            assert old.status in (SignatureTaskStatus.SIGNED, SignatureTaskStatus.CANCELLED)
            # 簽名先勝：舊任務 SIGNED，其簽名影像必實際落地（非半完成狀態）。
            if old.status is SignatureTaskStatus.SIGNED:
                assert old.signature_image is not None
    finally:
        async with sm() as s:
            await s.execute(delete(SignatureTask).where(SignatureTask.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.id == contact_id))
            await s.execute(delete(User).where(User.id == clerk_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


def _nonblank_png_base64() -> str:
    """非空白簽名 PNG（200×80 RGBA、中段黑色筆跡），滿足伺服端可見墨跡門檻。"""
    import base64
    import zlib

    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    width, height = 200, 80
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()
