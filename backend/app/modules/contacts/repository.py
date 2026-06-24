"""contacts 的資料存取層（唯一直接碰 ORM 的層）。"""

from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.models import Contact


class ContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, contact: Contact) -> Contact:
        self._session.add(contact)
        await self._session.flush()
        return contact

    async def save(self, contact: Contact) -> Contact:
        """持久化既有（已附掛）聯絡人的變更；flush 觸發 DB 唯一約束（最終去重防線）。"""
        self._session.add(contact)
        await self._session.flush()
        return contact

    async def adjust_member_points(self, store_id: int, contact_id: int, delta: int) -> bool:
        """原子調整會員點數（UPDATE ... = points + delta；條件含「不得為負」）。

        以單一條件式 UPDATE 避免讀-改-寫競態（比照 inventory 扣量模式）；
        對象不存在/跨店/將使點數為負 → 不動作、回 False。
        """
        stmt = (
            update(Contact)
            .where(
                Contact.id == contact_id,
                Contact.store_id == store_id,
                Contact.member_points + delta >= 0,
            )
            .values(member_points=Contact.member_points + delta)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def get(self, store_id: int, contact_id: int) -> Contact | None:
        stmt = select(Contact).where(Contact.id == contact_id, Contact.store_id == store_id)
        result: Contact | None = await self._session.scalar(stmt)
        return result

    async def get_for_update(self, store_id: int, contact_id: int) -> Contact | None:
        """SELECT … FOR UPDATE 取得列並 populate_existing（D-1 模式）：序列化同列的編輯，
        確保 roles/national_id 不變量在持鎖期間以**最新committed 狀態**重驗，杜絕併發競態
        （Codex 對抗式審查 high：一交易清 national_id、另一交易加 SELLER 各以舊快照通過）。"""
        stmt = (
            select(Contact)
            .where(Contact.id == contact_id, Contact.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: Contact | None = await self._session.scalar(stmt)
        return result

    async def get_by_blind_index(self, store_id: int, blind_index: str) -> Contact | None:
        stmt = select(Contact).where(
            Contact.store_id == store_id,
            Contact.national_id_blind_index == blind_index,
        )
        result: Contact | None = await self._session.scalar(stmt)
        return result

    async def get_by_phone(self, store_id: int, phone: str) -> Contact | None:
        """以手機號碼於本店精確查找（手機同店唯一）。"""
        stmt = select(Contact).where(Contact.store_id == store_id, Contact.phone == phone)
        result: Contact | None = await self._session.scalar(stmt)
        return result

    async def search(
        self, store_id: int, role: str | None, q: str | None, *, limit: int, offset: int
    ) -> list[Contact]:
        """以姓名/電話模糊搜尋；national_id 不可明文/部分搜尋，故不納入。分頁（docs/04）。"""
        stmt = select(Contact).where(Contact.store_id == store_id)
        if role is not None:
            # ARRAY 包含該角色（@>）。
            stmt = stmt.where(Contact.roles.contains([role]))
        if q is not None:
            like = f"%{q}%"
            stmt = stmt.where(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
        result = await self._session.scalars(stmt.order_by(Contact.id).limit(limit).offset(offset))
        return list(result)
