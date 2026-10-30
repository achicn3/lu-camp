"""staff 資料存取（唯一能碰 staff_members 的層）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.staff.models import StaffMember


class StaffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_members(self, store_id: int, *, include_inactive: bool) -> list[StaffMember]:
        stmt = select(StaffMember).where(StaffMember.store_id == store_id)
        if not include_inactive:
            stmt = stmt.where(StaffMember.is_active.is_(True))
        rows = await self._session.scalars(stmt.order_by(StaffMember.id))
        return list(rows.all())

    async def get(self, store_id: int, member_id: int) -> StaffMember | None:
        member: StaffMember | None = await self._session.scalar(
            select(StaffMember).where(StaffMember.store_id == store_id, StaffMember.id == member_id)
        )
        return member

    async def name_taken(self, store_id: int, name: str, *, except_id: int | None) -> bool:
        stmt = select(StaffMember.id).where(
            StaffMember.store_id == store_id, StaffMember.name == name
        )
        if except_id is not None:
            stmt = stmt.where(StaffMember.id != except_id)
        return await self._session.scalar(stmt.limit(1)) is not None

    async def user_taken(self, store_id: int, user_id: int, *, except_id: int | None) -> bool:
        stmt = select(StaffMember.id).where(
            StaffMember.store_id == store_id, StaffMember.user_id == user_id
        )
        if except_id is not None:
            stmt = stmt.where(StaffMember.id != except_id)
        return await self._session.scalar(stmt.limit(1)) is not None

    async def names_for(self, store_id: int, member_ids: set[int]) -> dict[int, str]:
        if not member_ids:
            return {}
        rows = await self._session.execute(
            select(StaffMember.id, StaffMember.name).where(
                StaffMember.store_id == store_id, StaffMember.id.in_(member_ids)
            )
        )
        return {member_id: name for member_id, name in rows.all()}

    async def add(self, member: StaffMember) -> StaffMember:
        self._session.add(member)
        await self._session.flush()
        return member
