"""staff 業務邏輯：員工名單（docs/99 §4.4）。增改屬設定變更，寫稽核（CLAUDE.md §5）。"""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.staff.models import StaffMember
from app.modules.staff.repository import StaffRepository
from app.modules.staff.schemas import StaffMemberUpdate
from app.modules.user.service import UserService
from app.shared.enums import UserRole
from app.shared.exceptions import StaffMemberConflict, StaffMemberInvalid, StaffMemberNotFound


def _snapshot(member: StaffMember) -> dict[str, Any]:
    return {"name": member.name, "user_id": member.user_id, "is_active": member.is_active}


class StaffService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StaffRepository(session)

    async def list_members(
        self, store_id: int, *, include_inactive: bool = False
    ) -> list[StaffMember]:
        """員工名單（預設只列在職的），依建立順序。"""
        return await self._repo.list_members(store_id, include_inactive=include_inactive)

    async def get_active_member(self, store_id: int, member_id: int) -> StaffMember | None:
        """在職的本店員工；停用、不存在或別店的回 None（給待辦指定負責人用）。"""
        member = await self._repo.get(store_id, member_id)
        return member if member is not None and member.is_active else None

    async def names_for(self, store_id: int, member_ids: set[int]) -> dict[int, str]:
        """員工 id → 姓名（含已停用的：歷史紀錄要顯示當時的人）。"""
        return await self._repo.names_for(store_id, member_ids)

    async def create_member(
        self, store_id: int, *, name: str, user_id: int | None = None, actor_user_id: int
    ) -> StaffMember:
        """新增員工（限管理者，由 router 把關）。同店同名、帳號已被對應 → StaffMemberConflict。"""
        await self._check_name(store_id, name, except_id=None)
        if user_id is not None:
            await self._check_user(store_id, user_id, except_id=None)
        member = await self._insert(StaffMember(store_id=store_id, name=name, user_id=user_id))
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_STAFF_MEMBER",
            entity_type="staff_member",
            entity_id=str(member.id),
            after=_snapshot(member),
        )
        return member

    async def update_member(
        self, store_id: int, member_id: int, changes: StaffMemberUpdate, *, actor_user_id: int
    ) -> StaffMember:
        """改名、改帳號對應、停用／復職；沒送的欄位不動。"""
        member = await self._repo.get(store_id, member_id)
        if member is None:
            raise StaffMemberNotFound(f"找不到員工 {member_id}")
        before = _snapshot(member)
        fields = changes.model_fields_set
        if "name" in fields and changes.name is not None and changes.name != member.name:
            await self._check_name(store_id, changes.name, except_id=member.id)
            member.name = changes.name
        if "user_id" in fields and changes.user_id != member.user_id:
            if changes.user_id is not None:
                await self._check_user(store_id, changes.user_id, except_id=member.id)
            member.user_id = changes.user_id
        if "is_active" in fields and changes.is_active is not None:
            member.is_active = changes.is_active
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError as exc:  # 同一瞬間兩台改成同名：唯一約束兜底
            raise StaffMemberConflict("員工姓名或帳號重複") from exc
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_STAFF_MEMBER",
            entity_type="staff_member",
            entity_id=str(member.id),
            before=before,
            after=_snapshot(member),
        )
        return member

    async def _insert(self, member: StaffMember) -> StaffMember:
        try:
            async with self._session.begin_nested():
                return await self._repo.add(member)
        except IntegrityError as exc:  # 同一瞬間兩台新增同名：唯一約束兜底
            raise StaffMemberConflict("員工姓名或帳號重複") from exc

    async def _check_name(self, store_id: int, name: str, *, except_id: int | None) -> None:
        if await self._repo.name_taken(store_id, name, except_id=except_id):
            raise StaffMemberConflict(f"已經有叫「{name}」的員工")

    async def _check_user(self, store_id: int, user_id: int, *, except_id: int | None) -> None:
        user = await UserService(self._session).get_user_in_store(store_id, user_id)
        if user is None or user.role is UserRole.KIOSK:
            raise StaffMemberInvalid("這個系統帳號不屬於本店，或是簽署平板的帳號")
        if await self._repo.user_taken(store_id, user_id, except_id=except_id):
            raise StaffMemberConflict("這個系統帳號已經對應到另一位員工")
