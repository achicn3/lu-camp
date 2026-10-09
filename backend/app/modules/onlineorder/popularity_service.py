"""線上點餐的人氣標籤（docs/63 §7 M2b；店主 2026-10-10 裁示）。

只用 POS 真實成交：餐飲品項近 N 天（台北日）淨銷量＝成交份數（沒作廢、不含贈品）− 退貨份數。
每個分類前三名、淨銷量達門檻才上榜（1＝「人氣 No.1」、2–3＝「人氣推薦」）；同分照菜單排序。
不收集客人的瀏覽或點擊。跨模組只經 sales／returns／menu service（CLAUDE.md §2）。
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.time import store_date, store_day_bounds, utc_now
from app.modules.menu.service import MenuService
from app.modules.onlineorder.models import OnlineMenuPopularity
from app.modules.onlineorder.popularity_repository import PopularityRepository
from app.modules.onlineorder.presentation_schemas import (
    PopularityRankRead,
    PopularityRead,
    PopularityWriteRequest,
)
from app.modules.returns.service import ReturnsService
from app.modules.sales.service import SalesService

_DEFAULT = PopularityWriteRequest(is_active=True, window_days=30, min_qty=10)
_TOP = 3


class PopularityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PopularityRepository(session)
        self._menu = MenuService(session)

    async def _settings(self, store_id: int) -> PopularityWriteRequest:
        row = await self._repo.get(store_id)
        if row is None:
            return _DEFAULT
        return PopularityWriteRequest.model_validate(
            {"is_active": row.is_active, "window_days": row.window_days, "min_qty": row.min_qty}
        )

    async def read(self, store_id: int) -> PopularityRead:
        """目前設定＋依設定算出的榜（不論開關，給店主預覽）。"""
        settings = await self._settings(store_id)
        return PopularityRead(
            **settings.model_dump(), ranking=await self._ranking(store_id, settings)
        )

    async def save(
        self, store_id: int, body: PopularityWriteRequest, *, actor_user_id: int
    ) -> PopularityRead:
        """更新設定並寫稽核。"""
        row = await self._repo.get(store_id, for_update=True)
        before = (
            None
            if row is None
            else {
                "is_active": row.is_active,
                "window_days": row.window_days,
                "min_qty": row.min_qty,
            }
        )
        if row is None:
            row = OnlineMenuPopularity(store_id=store_id)
        row.is_active = body.is_active
        row.window_days = body.window_days
        row.min_qty = body.min_qty
        await self._repo.save(row)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_ONLINE_MENU_POPULARITY",
            entity_type="online_menu_popularity",
            entity_id=str(row.id),
            before=before,
            after=body.model_dump(),
        )
        return await self.read(store_id)

    async def ranks(self, store_id: int) -> dict[int, int]:
        """{餐飲品項: 名次}；關掉就是空的。給客人頁（跟著可售狀態同步推上雲端）。"""
        settings = await self._settings(store_id)
        if not settings.is_active:
            return {}
        return {row.item_id: row.rank for row in await self._ranking(store_id, settings)}

    async def _ranking(
        self, store_id: int, settings: PopularityWriteRequest
    ) -> list[PopularityRankRead]:
        first_day = store_date(utc_now()) - timedelta(days=settings.window_days - 1)
        since, _ = store_day_bounds(first_day)
        sold = await SalesService(self._session).menu_qty_by_item_since(store_id, since)
        returned = await ReturnsService(self._session).returned_menu_qty_by_item_since(
            store_id, since
        )
        items = [
            item
            for item in await self._menu.list_items(store_id, include_unavailable=True)
            if item.archived_at is None
        ]
        result: list[PopularityRankRead] = []
        for category in sorted(
            await self._menu.list_categories(store_id), key=lambda c: (c.sort_order, c.id)
        ):
            ranked = sorted(
                (
                    (sold.get(item.id, 0) - returned.get(item.id, 0), item)
                    for item in items
                    if item.category_id == category.id
                ),
                key=lambda pair: (-pair[0], pair[1].sort_order, pair[1].id),
            )
            qualified = [(qty, item) for qty, item in ranked if qty >= settings.min_qty][:_TOP]
            result += [
                PopularityRankRead(
                    category=category.name, item_id=item.id, name=item.name, rank=rank, qty=qty
                )
                for rank, (qty, item) in enumerate(qualified, start=1)
            ]
        return result
