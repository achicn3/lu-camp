"""contacts 編輯併發不變量（T21-a；Codex 對抗式審查 high）。

驗證 PATCH 的 FOR UPDATE 列鎖能序列化「清 national_id」與「加 SELLER」兩個並發編輯，
使 SELLER/CONSIGNOR↔national_id 不變量不被競態破壞。

以**確定性交錯**取代隨機並行（後者依排程時序、無法穩定重現競態）：交易 A 清 national_id
後持鎖不提交 → 啟動交易 B 加 SELLER（撞鎖等待）→ A 提交釋鎖 → B 於鎖內以最新狀態
（enc 已為 NULL）重驗不變量、必須被擋下（AcquisitionRequiresNationalId）。
若移除列鎖，B 會以舊快照通過檢查、最終寫出「SELLER 卻無 national_id」的壞列。

需真正兩條交易並行，故用獨立 session（各自 commit），不走 db_session 回滾隔離；
結束在 finally 清掉自建列，不留殘餘。
"""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import delete

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.modules.contacts.models import Contact
from app.modules.contacts.schemas import ContactUpdate
from app.modules.contacts.service import ContactService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import ContactRole, UserRole
from app.shared.exceptions import AcquisitionRequiresNationalId, StoreCreditMemberRequired

NATIONAL_ID = "A123456789"


async def test_concurrent_clear_national_id_vs_add_seller_keeps_invariant() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發會員店")
        s.add(store)
        await s.flush()
        actor = User(store_id=store.id, username="mgrc", password_hash="h", role=UserRole.MANAGER)
        s.add(actor)
        await s.flush()
        contact = Contact(
            store_id=store.id,
            name="會員",
            roles=[ContactRole.MEMBER.value],
            national_id_enc=get_pii_cipher().encrypt(NATIONAL_ID),
            national_id_blind_index=national_id_blind_index(NATIONAL_ID),
        )
        s.add(contact)
        await s.flush()
        store_id, contact_id, actor_id = store.id, contact.id, actor.id
        await s.commit()

    session_a = sm()
    session_b = sm()
    try:
        # A：清 national_id（持鎖，尚未提交）。
        await ContactService(session_a).update_contact(
            store_id,
            contact_id,
            ContactUpdate(national_id=None),
            {"national_id"},
            actor_user_id=actor_id,
        )

        # B：加 SELLER；因撞 A 的列鎖而等待，故以背景 task 執行。
        async def add_seller() -> None:
            await ContactService(session_b).update_contact(
                store_id,
                contact_id,
                ContactUpdate(roles=[ContactRole.MEMBER, ContactRole.SELLER]),
                {"roles"},
                actor_user_id=actor_id,
            )
            await session_b.commit()

        b_task = asyncio.create_task(add_seller())
        await asyncio.sleep(0.3)  # 讓 B 抵達鎖等待點（A 尚持鎖時）
        assert not b_task.done()  # B 被列鎖擋住，尚未完成

        await session_a.commit()  # 釋鎖：B 進入鎖內、以最新狀態（enc=NULL）重驗

        # 鎖內重驗：SELLER 卻無 national_id → 被不變量擋下。無列鎖時此處不會拋。
        with pytest.raises(AcquisitionRequiresNationalId):
            await b_task
        await session_b.rollback()

        async with sm() as s:
            final = await s.get(Contact, contact_id)
            assert final is not None
            has_acq_role = ContactRole.SELLER.value in final.roles
            # 核心不變量：絕不出現「賣方卻無 national_id」。
            assert not (has_acq_role and final.national_id_enc is None)
            assert final.national_id_enc is None  # A 的清除已生效
    finally:
        await session_a.close()
        await session_b.close()
        async with sm() as s:
            # 成功的 PATCH 會寫 audit_log（FK → users），須先刪稽核再刪 user。
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.id == contact_id))
            await s.execute(delete(User).where(User.id == actor_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_concurrent_remove_member_vs_first_store_credit_keeps_invariant() -> None:
    """移除 MEMBER ⇄ 並發首筆購物金入帳：兩者於 contact 列互斥（Codex 對抗式審查 high）。

    A 移除 MEMBER 後持鎖不提交 → B 入帳（adjust）在 _require_member 鎖同列而等待 →
    A 提交（MEMBER 已除）→ B 取得鎖、讀到最新角色（無 MEMBER）→ 拒絕入帳。
    若 storecredit 入帳不鎖 contact 列，B 會以舊快照（仍是會員）通過並替非會員建立購物金。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發入帳店")
        s.add(store)
        await s.flush()
        actor = User(store_id=store.id, username="mgrd", password_hash="h", role=UserRole.MANAGER)
        s.add(actor)
        await s.flush()
        contact = Contact(store_id=store.id, name="會員", roles=[ContactRole.MEMBER.value])
        s.add(contact)
        await s.flush()
        store_id, contact_id, actor_id = store.id, contact.id, actor.id
        await s.commit()

    session_a = sm()
    session_b = sm()
    try:
        # A：移除 MEMBER（持鎖，尚未提交；此時尚無購物金，守衛放行）。
        await ContactService(session_a).update_contact(
            store_id,
            contact_id,
            ContactUpdate(roles=[]),
            {"roles"},
            actor_user_id=actor_id,
        )

        # B：首筆購物金入帳；_require_member 鎖同列而等待。
        async def first_credit() -> None:
            await StoreCreditService(session_b).adjust(
                store_id,
                contact_id,
                amount=Decimal(100),
                reason="seed",
                created_by=actor_id,
                idempotency_key="race-adjust",
            )

        b_task = asyncio.create_task(first_credit())
        await asyncio.sleep(0.3)
        assert not b_task.done()  # B 被 contact 列鎖擋住

        await session_a.commit()  # 釋鎖：B 取得鎖、讀到「已非會員」

        with pytest.raises(StoreCreditMemberRequired):
            await b_task
        await session_b.rollback()

        async with sm() as s:
            final = await s.get(Contact, contact_id)
            assert final is not None
            assert ContactRole.MEMBER.value not in final.roles  # A 的移除已生效
            # 非會員不得有購物金：B 被擋、未建立帳戶。
            assert not await StoreCreditService(s).has_store_credit(store_id, contact_id)
    finally:
        await session_a.close()
        await session_b.close()
        async with sm() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.id == contact_id))
            await s.execute(delete(User).where(User.id == actor_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_concurrent_clear_national_id_vs_auto_tag_seller_keeps_invariant() -> None:
    """自動打標（收購流程）與手動編輯走的是**不同的寫入路徑**，同一條不變量都要守住。

    `ensure_seller_role` 是這次新增的路徑（店員不必手動勾角色）。上一支測試證明手動
    改角色時擋得住「清空證號 ⇄ 加賣方」的競態；這支證明自動打標也擋得住——否則收購
    流程會留下沒有身分證字號的賣方，正好繞過防贓物的登記要求。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="自動打標併發店")
        s.add(store)
        await s.flush()
        actor = User(store_id=store.id, username="mgrd", password_hash="h", role=UserRole.MANAGER)
        s.add(actor)
        await s.flush()
        contact = Contact(
            store_id=store.id,
            name="會員",
            roles=[ContactRole.MEMBER.value],
            national_id_enc=get_pii_cipher().encrypt(NATIONAL_ID),
            national_id_blind_index=national_id_blind_index(NATIONAL_ID),
        )
        s.add(contact)
        await s.flush()
        store_id, contact_id, actor_id = store.id, contact.id, actor.id
        await s.commit()

    session_a = sm()
    session_b = sm()
    try:
        # A：清 national_id（持鎖，尚未提交）。
        await ContactService(session_a).update_contact(
            store_id,
            contact_id,
            ContactUpdate(national_id=None),
            {"national_id"},
            actor_user_id=actor_id,
        )

        async def auto_tag() -> None:
            await ContactService(session_b).ensure_seller_role(
                store_id, contact_id, actor_user_id=actor_id
            )
            await session_b.commit()

        b_task = asyncio.create_task(auto_tag())
        await asyncio.sleep(0.3)
        assert not b_task.done()  # 被 A 的列鎖擋住

        await session_a.commit()  # 釋鎖：B 進鎖內、以最新狀態（enc=NULL）重驗

        with pytest.raises(AcquisitionRequiresNationalId):
            await b_task
        await session_b.rollback()

        async with sm() as s:
            final = await s.get(Contact, contact_id)
            assert final is not None
            assert ContactRole.SELLER.value not in final.roles
            assert final.national_id_enc is None
    finally:
        await session_a.close()
        await session_b.close()
        async with sm() as s:
            # 本檔的測試會真的 commit（跨 session 行鎖沒得假裝），所以**必須**清掉自建列
            # ——否則殘留的 contact 會讓後面的備份測試看到多一筆而失敗（實測踩過）。
            # 稽核列有 FK → users，須先刪稽核再刪 user。
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(Contact).where(Contact.id == contact_id))
            await s.execute(delete(User).where(User.id == actor_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
