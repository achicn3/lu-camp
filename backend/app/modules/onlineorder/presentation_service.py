"""Online presentation settings refer to existing menu items via the menu service."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.menu.service import MenuService
from app.modules.onlineorder.models import OnlineMenuPresentation
from app.modules.onlineorder.presentation_repository import MenuPresentationRepository
from app.modules.onlineorder.presentation_schemas import (
    MenuPresentationRead,
    MenuPresentationUpdateRequest,
)
from app.shared.exceptions import MenuItemNotFound


class MenuPresentationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MenuPresentationRepository(session)

    async def _require_item(self, store_id: int, item_id: int, *, for_update: bool) -> None:
        item = await MenuService(self._session).get(store_id, item_id, for_update=for_update)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {item_id}")

    async def get(self, store_id: int, item_id: int) -> MenuPresentationRead:
        """Read an active item's settings, including defaults before its first save."""
        await self._require_item(store_id, item_id, for_update=False)
        row = await self._repo.get(store_id, item_id)
        if row is None:
            return MenuPresentationRead(menu_item_id=item_id)
        return MenuPresentationRead.model_validate(row)

    async def update(
        self,
        store_id: int,
        item_id: int,
        settings: MenuPresentationUpdateRequest,
        *,
        actor_user_id: int,
    ) -> MenuPresentationRead:
        """Replace presentation settings and audit before/after in the same transaction."""
        # The source item lock also serializes concurrent first saves, archive and hard delete.
        await self._require_item(store_id, item_id, for_update=True)
        row = await self._repo.get(store_id, item_id)
        before = (
            MenuPresentationUpdateRequest.model_validate(row)
            if row is not None
            else MenuPresentationUpdateRequest()
        )
        if row is None:
            row = OnlineMenuPresentation(store_id=store_id, menu_item_id=item_id)
        for key, value in settings.model_dump().items():
            setattr(row, key, value)
        await self._repo.save(row)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_ONLINE_MENU_PRESENTATION",
            entity_type="online_menu_presentation",
            entity_id=str(item_id),
            before=before.model_dump(mode="json"),
            after=settings.model_dump(mode="json"),
        )
        return MenuPresentationRead.model_validate(row)

    async def snapshot_settings(
        self, store_id: int, item_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """Fetch public settings in one batch; dates survive publication for day expiry."""
        return {
            row.menu_item_id: MenuPresentationUpdateRequest.model_validate(row).model_dump(
                mode="json"
            )
            for row in await self._repo.for_items(store_id, item_ids)
        }
