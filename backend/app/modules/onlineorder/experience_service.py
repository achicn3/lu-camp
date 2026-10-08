"""手沖體驗卡（docs/63 §4「手沖體驗」、M1c）：既有品項＋預選選項的另一種呈現。

不存價格、成本、庫存：售價由原品項加預選選項算，成交仍記原品項與選項 ID。
預選選項必須掛在該品項上、未封存，每個群組不超過可選上限；其餘必選項由客人補選。
跨模組只經 menu service（CLAUDE.md §2）。
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.menu.service import MenuItemDetail, MenuService
from app.modules.onlineorder.experience_repository import MenuExperienceRepository
from app.modules.onlineorder.models import OnlineMenuExperience
from app.modules.onlineorder.presentation_schemas import (
    MenuExperienceRead,
    MenuExperienceWriteRequest,
)
from app.shared.exceptions import (
    MenuItemNotFound,
    OnlineExperienceInvalid,
    OnlineExperienceNotFound,
)

_ENTITY = "online_menu_experience"


def preset_problem(detail: MenuItemDetail, option_ids: Sequence[int]) -> str | None:
    """預選選項有什麼問題（沒有則 None）：要掛在品項上、不重複、每群組不超過上限。"""
    if len(set(option_ids)) != len(option_ids):
        return "預選的選項重複了"
    chosen = set(option_ids)
    found: set[int] = set()
    for group in detail.option_groups:
        picked = [o.id for o in group.options if o.id in chosen]
        if len(picked) > group.group.max_select:
            return f"「{group.group.name}」最多只能預選 {group.group.max_select} 項"
        found.update(picked)
    if found != chosen:
        return "預選的選項沒有掛在這個品項上（或已封存）"
    return None


def _public(row: OnlineMenuExperience) -> dict[str, Any]:
    """公開到雲端的欄位：不含店別、時間與店內命名（品項用 item_id）。"""
    return {
        "id": row.id,
        "item_id": row.menu_item_id,
        "option_ids": list(row.option_ids),
        "title": row.title,
        "tag": row.tag,
        "origin": row.origin,
        "notes": row.notes,
        "description": row.description,
        "includes": [dict(i) for i in row.includes],
        "theme": row.theme.value,
        "art": row.art.value,
        "effect": row.effect.value,
    }


class MenuExperienceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MenuExperienceRepository(session)
        self._menu = MenuService(session)

    async def list_for_store(self, store_id: int) -> list[MenuExperienceRead]:
        return [
            MenuExperienceRead.model_validate(r) for r in await self._repo.list_for_store(store_id)
        ]

    async def _validated(self, store_id: int, body: MenuExperienceWriteRequest) -> None:
        # 鎖原品項：與封存、真刪、改掛群組互斥，驗過的預選在交易內不會被改掉。
        item = await self._menu.get(store_id, body.menu_item_id, for_update=True)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {body.menu_item_id}")
        [detail] = await self._menu.describe_items(store_id, [item])
        problem = preset_problem(detail, body.option_ids)
        if problem is not None:
            raise OnlineExperienceInvalid(problem)

    async def create(
        self, store_id: int, body: MenuExperienceWriteRequest, *, actor_user_id: int
    ) -> MenuExperienceRead:
        """新增一張體驗卡並寫稽核。"""
        await self._validated(store_id, body)
        row = OnlineMenuExperience(store_id=store_id, **body.model_dump())
        await self._repo.save(row)
        await self._audit(store_id, actor_user_id, "CREATE", row.id, None, body)
        return MenuExperienceRead.model_validate(row)

    async def update(
        self,
        store_id: int,
        experience_id: int,
        body: MenuExperienceWriteRequest,
        *,
        actor_user_id: int,
    ) -> MenuExperienceRead:
        """整張替換並寫稽核（前後值）。"""
        row = await self._repo.get(store_id, experience_id, for_update=True)
        if row is None:
            raise OnlineExperienceNotFound(f"找不到體驗卡 {experience_id}")
        await self._validated(store_id, body)
        before = MenuExperienceWriteRequest.model_validate(row)
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        await self._repo.save(row)
        await self._audit(store_id, actor_user_id, "UPDATE", row.id, before, body)
        return MenuExperienceRead.model_validate(row)

    async def delete(self, store_id: int, experience_id: int, *, actor_user_id: int) -> None:
        """刪除體驗卡（只是呈現設定，原品項與歷史訂單不受影響）。"""
        row = await self._repo.get(store_id, experience_id, for_update=True)
        if row is None:
            raise OnlineExperienceNotFound(f"找不到體驗卡 {experience_id}")
        before = MenuExperienceWriteRequest.model_validate(row)
        await self._repo.delete(row)
        await self._audit(store_id, actor_user_id, "DELETE", experience_id, before, None)

    async def title_of(self, store_id: int, experience_id: int) -> str | None:
        """體驗卡現在的標題；卡片已刪回 None（呼叫端改用送單當時的品名）。"""
        row = await self._repo.get(store_id, experience_id)
        return None if row is None else row.title

    async def _audit(
        self,
        store_id: int,
        actor_user_id: int,
        verb: str,
        experience_id: int,
        before: MenuExperienceWriteRequest | None,
        after: MenuExperienceWriteRequest | None,
    ) -> None:
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action=f"{verb}_ONLINE_MENU_EXPERIENCE",
            entity_type=_ENTITY,
            entity_id=str(experience_id),
            before=None if before is None else before.model_dump(mode="json"),
            after=None if after is None else after.model_dump(mode="json"),
        )

    async def snapshot_experiences(
        self, store_id: int, shown: Mapping[int, MenuItemDetail]
    ) -> list[dict[str, Any]]:
        """發佈用：啟用中、原品項有上架、預選仍合法的卡（依排序）；其餘略過不發佈。"""
        result: list[dict[str, Any]] = []
        for row in await self._repo.list_for_store(store_id):
            detail = shown.get(row.menu_item_id)
            if not row.is_active or detail is None:
                continue
            if preset_problem(detail, row.option_ids) is not None:
                continue
            result.append(_public(row))
        return result
