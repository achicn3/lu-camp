"""contacts 的資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.models import Contact


class ContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, contact: Contact) -> Contact:
        self._session.add(contact)
        await self._session.flush()
        return contact

    async def get(self, store_id: int, contact_id: int) -> Contact | None:
        stmt = select(Contact).where(Contact.id == contact_id, Contact.store_id == store_id)
        result: Contact | None = await self._session.scalar(stmt)
        return result

    async def get_by_blind_index(self, store_id: int, blind_index: str) -> Contact | None:
        stmt = select(Contact).where(
            Contact.store_id == store_id,
            Contact.national_id_blind_index == blind_index,
        )
        result: Contact | None = await self._session.scalar(stmt)
        return result

    async def search(self, store_id: int, role: str | None, q: str | None) -> list[Contact]:
        """以姓名/電話模糊搜尋；national_id 不可明文/部分搜尋，故不納入。"""
        stmt = select(Contact).where(Contact.store_id == store_id)
        if role is not None:
            # ARRAY 包含該角色（@>）。
            stmt = stmt.where(Contact.roles.contains([role]))
        if q is not None:
            like = f"%{q}%"
            stmt = stmt.where(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
        result = await self._session.scalars(stmt.order_by(Contact.id))
        return list(result)
