"""寄售帳篷上官網（docs/97）：POS 維護官網資料、組出推給官網的清單。

不存售價、品牌、名稱、狀態——一律讀序號品本身。只有在庫的寄售品可以編輯；店員、店長都能改。
推給官網的清單只送白名單欄位（寄售人、成本、抽成、備註一律不出店，CLAUDE.md §5），
售出的不帶照片、售出 30 天後消失（裁示 8）。跨模組只經 inventory／menu service（CLAUDE.md §2）。
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.inventory.models import SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.menu.service import MenuService
from app.modules.onlineorder.models import OnlineTentListing
from app.modules.onlineorder.tent_repository import TentListingRepository
from app.modules.onlineorder.tent_schemas import TentListingRead, TentListingWriteRequest
from app.shared.enums import OwnershipType, SerializedItemStatus
from app.shared.exceptions import (
    OnlineTentListingNotEditable,
    OnlineTentListingNotFound,
    OnlineTentPhotoOrderInvalid,
    OnlineTentPhotoRejected,
)

SOLD_DAYS = 30
MAX_PHOTOS = 8
# 一家店同時在庫的寄售品不會到這麼多；超過時清單只列前面這些。
MAX_CANDIDATES = 500
TAIPEI = ZoneInfo("Asia/Taipei")
_ENTITY = "online_tent_listing"
_NOT_EDITABLE = "只有在庫的寄售品可以放上官網"


def feed_sha256(feed: dict[str, Any]) -> str:
    """清單內容雜湊（鍵排序、緊湊格式）：內容沒變就不推（docs/97 §4.3）。"""
    text = json.dumps(feed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def taipei_iso(moment: datetime) -> str:
    """台北時間、到秒（官網直接拿字串的月日時分顯示）。"""
    return moment.astimezone(TAIPEI).isoformat(timespec="seconds")


class TentListingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TentListingRepository(session)
        self._inventory = InventoryService(session)
        self._menu = MenuService(session)

    # ── POS 畫面 ──

    async def list_candidates(self, store_id: int) -> list[TentListingRead]:
        """在庫的寄售品，連同官網資料（還沒填過的給預設值）。"""
        items = await self._inventory.list_serialized(
            store_id,
            status=SerializedItemStatus.IN_STOCK,
            ownership_type=OwnershipType.CONSIGNMENT,
            limit=MAX_CANDIDATES,
        )
        listings = await self._repo.by_items(store_id, [i.id for i in items])
        photos = await self._repo.photos([r.id for r in listings.values()])
        brands = await self._brands(store_id, items)
        return [
            self._read(
                item,
                listings.get(item.id),
                photos.get(listings[item.id].id, []) if item.id in listings else [],
                brands,
            )
            for item in items
        ]

    async def save(
        self, store_id: int, item_id: int, body: TentListingWriteRequest, *, actor_user_id: int
    ) -> TentListingRead:
        """整筆替換官網資料並寫稽核（前後值）。"""
        item = await self._editable_item(store_id, item_id)
        row = await self._repo.by_item(store_id, item.id, for_update=True)
        after = body.model_dump()
        if row is None:
            row = await self._create(store_id, item.id, body)
            await self._audit(store_id, actor_user_id, "CREATE", row.id, None, after)
        else:
            before = TentListingWriteRequest.model_validate(row).model_dump()
            for key, value in after.items():
                setattr(row, key, value)
            await self._repo.save(row)
            await self._audit(store_id, actor_user_id, "UPDATE", row.id, before, after)
        return await self._fresh_read(store_id, item, row)

    async def add_photo(
        self, store_id: int, item_id: int, data: bytes, *, actor_user_id: int
    ) -> TentListingRead:
        """加一張照片到最後（先轉檔、不持鎖）。不合格丟 `MenuPhotoInvalid`。"""
        await self._editable_item(store_id, item_id)
        sha = await self._menu.store_photo(store_id, data)
        item = await self._editable_item(store_id, item_id)
        row = await self._repo.by_item(store_id, item.id, for_update=True)
        if row is None:
            row = await self._create(store_id, item.id, TentListingWriteRequest())
            await self._audit(
                store_id,
                actor_user_id,
                "CREATE",
                row.id,
                None,
                TentListingWriteRequest().model_dump(),
            )
        current = (await self._repo.photos([row.id]))[row.id]
        if sha in current:
            raise OnlineTentPhotoRejected("這張照片已經傳過了")
        if len(current) >= MAX_PHOTOS:
            raise OnlineTentPhotoRejected(f"每頂帳篷最多 {MAX_PHOTOS} 張照片，請先刪掉一張")
        await self._repo.replace_photos(row, [*current, sha])
        return await self._fresh_read(store_id, item, row)

    async def remove_photo(self, store_id: int, item_id: int, sha: str) -> TentListingRead:
        """拿掉一張（照片檔本身留在 POS，別處可能還在用）。"""
        item, row, current = await self._photo_target(store_id, item_id)
        if sha not in current:
            raise OnlineTentListingNotFound("找不到這張照片")
        await self._repo.replace_photos(row, [s for s in current if s != sha])
        return await self._fresh_read(store_id, item, row)

    async def reorder_photos(self, store_id: int, item_id: int, shas: list[str]) -> TentListingRead:
        """換照片順序，第一張是封面；內容必須剛好是現有那幾張。"""
        item, row, current = await self._photo_target(store_id, item_id)
        if sorted(shas) != sorted(current):
            raise OnlineTentPhotoOrderInvalid("照片有變動，請重新整理後再排一次")
        await self._repo.replace_photos(row, shas)
        return await self._fresh_read(store_id, item, row)

    async def reserved_for_code(self, store_id: int, item_code: str) -> bool:
        """POS 結帳提醒：這件在官網上顯示「已有人保留」嗎（裁示 6，提醒不擋）。"""
        item = await self._inventory.get_serialized_by_code(store_id, item_code)
        if item is None or item.status is not SerializedItemStatus.IN_STOCK:
            return False
        row = await self._repo.by_item(store_id, item.id)
        return row is not None and row.published and row.reserved

    # ── 推給官網的清單 ──

    async def build_feed(self, store_id: int, *, now: datetime) -> dict[str, Any]:
        """組出要推給官網的清單（不含推送時間與版本，見 docs/97 §4）。"""
        rows = await self._repo.published(store_id)
        items = [
            await self._inventory.get_serialized_by_id(store_id, r.serialized_item_id) for r in rows
        ]
        live = [(r, i) for r, i in zip(rows, items, strict=True) if i is not None]
        photos = await self._repo.photos([r.id for r, _ in live])
        brands = await self._brands(store_id, [i for _, i in live])
        cutoff = now - timedelta(days=SOLD_DAYS)
        tents: list[dict[str, Any]] = []
        for row, item in live:
            status = self._feed_status(row, item, cutoff)
            if status is None:
                continue
            sold = status == "sold"
            updated = (
                item.sold_date if sold and item.sold_date else max(row.updated_at, item.updated_at)
            )
            tents.append(
                {
                    "id": item.item_code,
                    "brand": brands.get(item.brand_id) if item.brand_id is not None else None,
                    "name": item.name,
                    "color": row.color,
                    "nights": row.nights,
                    "missing": list(row.missing),
                    "defects": list(row.defects),
                    "extras": list(row.extras),
                    "photos": [] if sold else photos[row.id],
                    "video": row.video_url,
                    "price": int(item.listed_price),
                    "status": status,
                    "listedAt": row.created_at.astimezone(TAIPEI).date().isoformat(),
                    "updatedAt": taipei_iso(updated),
                }
            )
        return {"soldDays": SOLD_DAYS, "tents": tents}

    async def clear_reserved_for_sold(self, store_id: int) -> int:
        """已售出卻還勾著保留的清掉，退貨回庫時才不會又變成保留中（docs/97 §6）。"""
        rows = [r for r in await self._repo.published(store_id) if r.reserved]
        stale: list[int] = []
        for row in rows:
            item = await self._inventory.get_serialized_by_id(store_id, row.serialized_item_id)
            if item is None or item.status is not SerializedItemStatus.IN_STOCK:
                stale.append(row.id)
        return await self._repo.clear_reserved(store_id, stale)

    # ── 內部 ──

    @staticmethod
    def _feed_status(row: OnlineTentListing, item: SerializedItem, cutoff: datetime) -> str | None:
        if item.ownership_type is not OwnershipType.CONSIGNMENT:
            return None
        if item.status is SerializedItemStatus.IN_STOCK:
            return "reserved" if row.reserved else "available"
        if item.status is SerializedItemStatus.SOLD and item.sold_date and item.sold_date >= cutoff:
            return "sold"
        return None

    async def _item(self, store_id: int, item_id: int) -> SerializedItem:
        item = await self._inventory.get_serialized_by_id(store_id, item_id)
        if item is None:
            raise OnlineTentListingNotFound(f"找不到商品 {item_id}")
        return item

    async def _editable_item(self, store_id: int, item_id: int) -> SerializedItem:
        item = await self._item(store_id, item_id)
        if (
            item.ownership_type is not OwnershipType.CONSIGNMENT
            or item.status is not SerializedItemStatus.IN_STOCK
        ):
            raise OnlineTentListingNotEditable(_NOT_EDITABLE)
        return item

    async def _photo_target(
        self, store_id: int, item_id: int
    ) -> tuple[SerializedItem, OnlineTentListing, list[str]]:
        item = await self._editable_item(store_id, item_id)
        row = await self._repo.by_item(store_id, item.id, for_update=True)
        if row is None:
            raise OnlineTentListingNotFound("這件還沒有照片")
        return item, row, (await self._repo.photos([row.id]))[row.id]

    async def _create(
        self, store_id: int, item_id: int, body: TentListingWriteRequest
    ) -> OnlineTentListing:
        row = OnlineTentListing(store_id=store_id, serialized_item_id=item_id, **body.model_dump())
        # 兩個人同時第一次存同一件：唯一鍵擋下的那位改成更新對方剛建的那筆。
        try:
            async with self._session.begin_nested():
                await self._repo.save(row)
        except IntegrityError:
            existing = await self._repo.by_item(store_id, item_id, for_update=True)
            if existing is None:
                raise
            for key, value in body.model_dump().items():
                setattr(existing, key, value)
            await self._repo.save(existing)
            return existing
        return row

    async def _brands(self, store_id: int, items: list[SerializedItem]) -> dict[int, str]:
        ids = sorted({i.brand_id for i in items if i.brand_id is not None})
        return await self._inventory.brand_names(store_id, ids) if ids else {}

    async def _fresh_read(
        self, store_id: int, item: SerializedItem, row: OnlineTentListing
    ) -> TentListingRead:
        await self._session.refresh(row)
        photos = (await self._repo.photos([row.id]))[row.id]
        return self._read(item, row, photos, await self._brands(store_id, [item]))

    @staticmethod
    def _read(
        item: SerializedItem,
        row: OnlineTentListing | None,
        photos: list[str],
        brands: dict[int, str],
    ) -> TentListingRead:
        fields = (
            TentListingWriteRequest.model_validate(row).model_dump()
            if row is not None
            else TentListingWriteRequest().model_dump()
        )
        return TentListingRead(
            **fields,
            item_id=item.id,
            item_code=item.item_code,
            name=item.name,
            brand_name=brands.get(item.brand_id) if item.brand_id is not None else None,
            listed_price=item.listed_price,
            photos=photos,
            updated_at=row.updated_at if row is not None else None,
        )

    async def _audit(
        self,
        store_id: int,
        actor_user_id: int,
        verb: str,
        listing_id: int,
        before: dict[str, object] | None,
        after: dict[str, object],
    ) -> None:
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action=f"{verb}_ONLINE_TENT_LISTING",
            entity_type=_ENTITY,
            entity_id=str(listing_id),
            before=before,
            after=after,
        )
