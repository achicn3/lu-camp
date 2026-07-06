"""signing 併發不變量：同店同時最多一件 PENDING 簽署任務（Codex 第二輪 high）。

需要真正的兩條交易並行，故用獨立 session（各自 commit），不走 db_session 回滾隔離；
最終防線是 signature_tasks 的 partial unique index（status='PENDING'），且輸家必須拿到
可重試的 SignatureTaskConflict（不得漏出裸 IntegrityError → 500）。
測試結束在 finally 清掉自建的列，不留殘餘。
"""

import asyncio

from sqlalchemy import delete, select

import app.core.db as app_db
from app.modules.contacts.models import Contact
from app.modules.signing.models import SignatureTask
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SignatureTaskKind, SignatureTaskStatus, UserRole
from app.shared.exceptions import SignatureTaskConflict


async def test_concurrent_create_keeps_single_pending() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發簽署店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="conc-sign", password_hash="h", role=UserRole.CLERK
        )
        contact = Contact(store_id=store.id, name="併發客", phone="0955555555", roles=["SELLER"])
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
