"""店員推薦（店主 2026-10-10 裁示）：線上發布分頁一份有序清單。

可挑餐飲品項、手沖體驗卡、帶著走商品；客人掃碼直接進完整菜單，「店員推薦」排第一並預設打開。
只引用既有品項，不存價格或庫存；發佈時只帶這次有發佈的。跨模組只經 menu service（CLAUDE.md §2）。
"""

from collections.abc import Collection
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.menu.service import MenuService
from app.modules.onlineorder.experience_repository import MenuExperienceRepository
from app.modules.onlineorder.models import OnlineStaffPicks
from app.modules.onlineorder.picks_repository import StaffPicksRepository
from app.modules.onlineorder.presentation_schemas import StaffPicks
from app.modules.onlineorder.retail_service import RetailListingService
from app.shared.exceptions import OnlineStaffPicksInvalid


class StaffPicksService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StaffPicksRepository(session)

    async def get(self, store_id: int) -> StaffPicks:
        row = await self._repo.get(store_id)
        return StaffPicks.model_validate({"items": [] if row is None else row.items})

    async def save(self, store_id: int, body: StaffPicks, *, actor_user_id: int) -> StaffPicks:
        """整份覆寫並寫稽核；每一項都要是本店上架中的東西，不可重複。"""
        await self._validate(store_id, body)
        row = await self._repo.get(store_id, for_update=True)
        before = None if row is None else {"items": row.items}
        after = body.model_dump(mode="json")
        if row is None:
            row = OnlineStaffPicks(store_id=store_id)
        row.items = after["items"]
        await self._repo.save(row)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_ONLINE_STAFF_PICKS",
            entity_type="online_staff_picks",
            entity_id=str(row.id),
            before=before,
            after=after,
        )
        return body

    async def _validate(self, store_id: int, body: StaffPicks) -> None:
        keys = [(ref.kind, ref.id) for ref in body.items]
        if len(set(keys)) != len(keys):
            raise OnlineStaffPicksInvalid("店員推薦裡有重複的商品")
        listed: set[int] | None = None
        menu = MenuService(self._session)
        experiences = MenuExperienceRepository(self._session)
        for ref in body.items:
            if ref.kind == "item":
                item = await menu.get(store_id, ref.id)
                if item is None or item.archived_at is not None:
                    raise OnlineStaffPicksInvalid("店員推薦的餐飲品項找不到或已封存")
            elif ref.kind == "experience":
                if await experiences.get(store_id, ref.id) is None:
                    raise OnlineStaffPicksInvalid("店員推薦的手沖體驗找不到")
            else:
                if listed is None:
                    rows = await RetailListingService(self._session).list_for_store(store_id)
                    listed = {row.catalog_product_id for row in rows}
                if ref.id not in listed:
                    raise OnlineStaffPicksInvalid("店員推薦的帶著走商品還沒上線")

    async def snapshot_picks(
        self,
        store_id: int,
        item_ids: Collection[int],
        experience_ids: Collection[int],
        retail_ids: Collection[int],
    ) -> list[dict[str, Any]]:
        """發佈用：照店主排的順序，只留這次有發佈的。"""
        row = await self._repo.get(store_id)
        if row is None:
            return []
        published = {
            "item": set(item_ids),
            "experience": set(experience_ids),
            "retail": set(retail_ids),
        }
        return [dict(ref) for ref in row.items if ref["id"] in published[ref["kind"]]]
