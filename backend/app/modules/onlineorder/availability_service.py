"""Reconcile current POS menu availability with the published cloud menu."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.service import MenuService, remaining_today, today
from app.modules.onlineorder.availability_repository import AvailabilityRepository

AvailabilityPayload = dict[str, Any]
RECONFIRM_INTERVAL = timedelta(seconds=60)


class AvailabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AvailabilityRepository(session)

    async def capture(self, store_id: int) -> AvailabilityPayload | None:
        """Persist changed full state under a store row lock; caller commits before sending."""
        if await self._repo.latest_publication(store_id) is None:
            return None
        state = await self._repo.locked_state(store_id)
        # Re-read after waiting for the lock: an earlier tick may have seen an older publish.
        publication = await self._repo.latest_publication(store_id)
        assert publication is not None
        menu = MenuService(self._session)
        day = today()
        items = await menu.list_items(store_id, include_unavailable=True)
        groups = await menu.list_option_groups(store_id)
        current: AvailabilityPayload = {
            "items": [
                {
                    "id": item.id,
                    "available": item.is_available,
                    "remaining": remaining_today(item, day),
                }
                for item in sorted(items, key=lambda item: item.id)
            ],
            "options": [
                {
                    "id": option.id,
                    "available": option.is_available,
                    "remaining": remaining_today(option, day),
                }
                for option in sorted(
                    (option for group in groups for option in group.options),
                    key=lambda option: option.id,
                )
            ],
        }
        if state.menu_version == publication.version and state.payload == current:
            if state.delivery_state == "DELIVERED":
                if datetime.now(UTC) - state.updated_at < RECONFIRM_INTERVAL:
                    return None
                # Replay the same revision to repair a lost cloud overlay.
                state.delivery_state = "PENDING"
                await self._session.flush()
                return self._body(state.menu_version, state.revision, state.payload)
            if state.delivery_state == "PENDING":
                return self._body(state.menu_version, state.revision, state.payload)
        state.menu_version = publication.version
        state.revision += 1
        state.payload = current
        state.delivery_state = "PENDING"
        state.last_error = None
        await self._session.flush()
        return self._body(state.menu_version, state.revision, current)

    async def mark_delivered(self, store_id: int, version: int, revision: int) -> bool:
        """A late ACK may only clear the exact version and revision it acknowledged."""
        state = await self._repo.locked_state(store_id)
        if (
            state.menu_version != version
            or state.revision != revision
            or state.delivery_state != "PENDING"
        ):
            return False
        state.delivery_state = "DELIVERED"
        state.last_error = None
        await self._session.flush()
        return True

    async def mark_conflict(self, store_id: int, version: int, revision: int, error: str) -> bool:
        """Record cloud version conflict; the next capture gets a new revision."""
        return await self._mark_error(store_id, version, revision, error, "CONFLICT")

    async def mark_error(self, store_id: int, version: int, revision: int, error: str) -> bool:
        """Keep the pending payload for an identical retry after an uncertain delivery."""
        return await self._mark_error(store_id, version, revision, error, "PENDING")

    async def mark_revision_conflict(
        self, store_id: int, version: int, revision: int, current_revision: int, error: str
    ) -> bool:
        """Raise the revision floor, then recapture fresh menu data on the next tick."""
        state = await self._repo.locked_state(store_id)
        if (
            state.menu_version != version
            or state.revision != revision
            or state.delivery_state != "PENDING"
        ):
            return False
        state.revision = max(state.revision, current_revision)
        state.delivery_state = "CONFLICT"
        state.last_error = error[:200]
        await self._session.flush()
        return True

    async def _mark_error(
        self, store_id: int, version: int, revision: int, error: str, delivery_state: str
    ) -> bool:
        state = await self._repo.locked_state(store_id)
        if (
            state.menu_version != version
            or state.revision != revision
            or state.delivery_state != "PENDING"
        ):
            return False
        state.delivery_state = delivery_state
        state.last_error = error[:200]
        await self._session.flush()
        return True

    @staticmethod
    def _body(version: int, revision: int, payload: AvailabilityPayload) -> AvailabilityPayload:
        return {"menu_version": version, "revision": revision, **payload}
