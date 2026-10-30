"""寄售帳篷上官網（docs/97）：POS 維護官網資料、組出推給官網的清單。

不存售價、品牌、名稱、狀態——一律讀序號品本身。只有在庫的寄售品可以編輯；店員、店長都能改。
推給官網的清單只送白名單欄位（寄售人、成本、抽成、備註一律不出店，CLAUDE.md §5），
售出的不帶照片、售出 30 天後消失（裁示 8）。跨模組只經 inventory／menu service（CLAUDE.md §2）。

預售（docs/102）：帳篷還在賣家家裡就先上官網，沒有序號品；品牌／名稱／售價與賣家聯絡方式存在
同一列的 presale_*（賣家資料只在 POS）。到店建寄售單時由收購服務呼叫 `take_presale` 接上序號品。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.inventory.models import SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.menu.service import MenuService
from app.modules.onlineorder.models import OnlineTentListing
from app.modules.onlineorder.tent_repository import TentListingRepository
from app.modules.onlineorder.tent_schemas import (
    TentListingRead,
    TentListingWriteRequest,
    TentPresaleRead,
    TentPresaleWriteRequest,
)
from app.shared.enums import OwnershipType, SerializedItemStatus
from app.shared.exceptions import (
    OnlineTentListingNotEditable,
    OnlineTentListingNotFound,
    OnlineTentPhotoOrderInvalid,
    OnlineTentPhotoRejected,
    OnlineTentPresaleUnavailable,
)

SOLD_DAYS = 30
MAX_PHOTOS = 8
# 一家店同時在庫的寄售品不會到這麼多；超過時清單只列前面這些。
MAX_CANDIDATES = 500
TAIPEI = ZoneInfo("Asia/Taipei")
_ENTITY = "online_tent_listing"
_NOT_EDITABLE = "只有在庫的寄售品可以放上官網"
_PRESALE_CODE_LEN = 10
_PRESALE_GONE = "這筆預售已經取消或已經轉成寄售"
# 預售欄位 ↔ 資料表欄位（官網資料欄位同名，見 TentListingWriteRequest）
_PRESALE_COLUMNS = {
    "brand_id": "presale_brand_id",
    "name": "presale_name",
    "price": "presale_price",
    "seller_name": "presale_seller_name",
    "seller_phone": "presale_seller_phone",
    "note": "presale_note",
}


def new_presale_code(store_id: int) -> str:
    """預售編號，如 ``P1-3F9A2B7C4D``（同序號品編號的格式，前綴 P 表示還沒到店）。"""
    return f"P{store_id}-{uuid4().hex[:_PRESALE_CODE_LEN].upper()}"


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
            row, _created = await self._create(store_id, item.id, body, replace_on_conflict=True)
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
            # 只是幫照片建一筆空的；若同時有人剛存檔建好了，
            # 就用那筆、不套空白預設值（Codex 第二輪）。
            row, created = await self._create(
                store_id, item.id, TentListingWriteRequest(), replace_on_conflict=False
            )
            if created:
                await self._audit(
                    store_id,
                    actor_user_id,
                    "CREATE",
                    row.id,
                    None,
                    TentListingWriteRequest().model_dump(),
                )
        await self._append_photo(row, sha)
        return await self._fresh_read(store_id, item, row)

    async def remove_photo(self, store_id: int, item_id: int, sha: str) -> TentListingRead:
        """拿掉一張（照片檔本身留在 POS，別處可能還在用）。"""
        item, row = await self._photo_target(store_id, item_id)
        await self._remove_photo(row, sha)
        return await self._fresh_read(store_id, item, row)

    async def reorder_photos(self, store_id: int, item_id: int, shas: list[str]) -> TentListingRead:
        """換照片順序，第一張是封面；內容必須剛好是現有那幾張。"""
        item, row = await self._photo_target(store_id, item_id)
        await self._reorder_photos(row, shas)
        return await self._fresh_read(store_id, item, row)

    # ── 預售（docs/102）──

    async def list_presales(self, store_id: int) -> list[TentPresaleRead]:
        """還沒取消、還沒轉寄售的預售（新的在前）。"""
        rows = await self._repo.open_presales(store_id)
        photos = await self._repo.photos([r.id for r in rows])
        brands = await self._presale_brands(store_id, rows)
        return [self._presale_read(r, photos[r.id], brands) for r in rows]

    async def create_presale(
        self, store_id: int, body: TentPresaleWriteRequest, *, actor_user_id: int
    ) -> TentPresaleRead:
        await self._check_brand(store_id, body.brand_id)
        row = OnlineTentListing(store_id=store_id, presale_code=new_presale_code(store_id))
        self._apply_presale(row, body)
        await self._repo.save(row)
        await self._audit(
            store_id,
            actor_user_id,
            "CREATE",
            row.id,
            None,
            body.model_dump(mode="json"),
            noun="PRESALE",
        )
        return await self._fresh_presale_read(store_id, row)

    async def save_presale(
        self, store_id: int, listing_id: int, body: TentPresaleWriteRequest, *, actor_user_id: int
    ) -> TentPresaleRead:
        """整筆替換並寫稽核（前後值）。"""
        row = await self._open_presale(store_id, listing_id)
        await self._check_brand(store_id, body.brand_id)
        before = self._presale_fields(row)
        self._apply_presale(row, body)
        await self._repo.save(row)
        await self._audit(
            store_id,
            actor_user_id,
            "UPDATE",
            row.id,
            before,
            body.model_dump(mode="json"),
            noun="PRESALE",
        )
        return await self._fresh_presale_read(store_id, row)

    async def cancel_presale(self, store_id: int, listing_id: int, *, actor_user_id: int) -> None:
        """賣家不賣了／帳篷沒送來：官網拿掉，POS 留紀錄。"""
        row = await self._open_presale(store_id, listing_id)
        row.presale_cancelled_at = datetime.now(UTC)
        await self._repo.save(row)
        await self._audit(
            store_id,
            actor_user_id,
            "CANCEL",
            row.id,
            self._presale_fields(row),
            {},
            noun="PRESALE",
        )

    async def add_presale_photo(
        self, store_id: int, listing_id: int, data: bytes
    ) -> TentPresaleRead:
        """加一張照片到最後（先轉檔、不持鎖）。不合格丟 `MenuPhotoInvalid`。"""
        await self._open_presale(store_id, listing_id, for_update=False)
        sha = await self._menu.store_photo(store_id, data)
        row = await self._open_presale(store_id, listing_id)
        await self._append_photo(row, sha)
        return await self._fresh_presale_read(store_id, row)

    async def remove_presale_photo(
        self, store_id: int, listing_id: int, sha: str
    ) -> TentPresaleRead:
        row = await self._open_presale(store_id, listing_id)
        await self._remove_photo(row, sha)
        return await self._fresh_presale_read(store_id, row)

    async def reorder_presale_photos(
        self, store_id: int, listing_id: int, shas: list[str]
    ) -> TentPresaleRead:
        row = await self._open_presale(store_id, listing_id)
        await self._reorder_photos(row, shas)
        return await self._fresh_presale_read(store_id, row)

    async def take_presale(
        self, store_id: int, listing_id: int, item_id: int, *, actor_user_id: int
    ) -> None:
        """到店建寄售單（收購服務呼叫，同一交易）：把預售接上剛建好的序號品。

        官網資料、照片、上架與保留全部沿用；接不上丟 `OnlineTentPresaleUnavailable`，
        呼叫端讓整張寄售單失敗（不留下寄售成功、官網沒接上的半套）。
        """
        row = await self._repo.presale(store_id, listing_id, for_update=True)
        if row is None or row.serialized_item_id is not None or row.presale_cancelled_at:
            raise OnlineTentPresaleUnavailable(_PRESALE_GONE)
        row.serialized_item_id = item_id
        row.converted_at = datetime.now(UTC)
        await self._repo.save(row)
        await self._audit(
            store_id,
            actor_user_id,
            "CONVERT",
            row.id,
            None,
            {"serialized_item_id": item_id},
            noun="PRESALE",
        )

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
        presales = [
            r for r in rows if r.serialized_item_id is None and r.presale_cancelled_at is None
        ]
        live: list[tuple[OnlineTentListing, SerializedItem]] = []
        for r in rows:
            if r.serialized_item_id is None:
                continue
            item = await self._inventory.get_serialized_by_id(store_id, r.serialized_item_id)
            if item is not None:
                live.append((r, item))
        photos = await self._repo.photos([r.id for r in [*presales, *(r for r, _ in live)]])
        brands = await self._brands(store_id, [i for _, i in live])
        presale_brands = await self._presale_brands(store_id, presales)
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
                    # 售出後照片、影片都不放（裁示 8、2026-10-30 售出隱藏影片）
                    "video": None if sold else row.video_url,
                    "price": int(item.listed_price),
                    "status": status,
                    "listedAt": row.created_at.astimezone(TAIPEI).date().isoformat(),
                    "updatedAt": taipei_iso(updated),
                    "presale": False,
                    # 轉寄售前的預售編號：官網舊網址轉到新編號
                    "formerIds": [row.presale_code] if row.presale_code else [],
                }
            )
        for row in presales:
            tents.append(
                {
                    "id": row.presale_code,
                    "brand": (
                        presale_brands.get(row.presale_brand_id)
                        if row.presale_brand_id is not None
                        else None
                    ),
                    "name": row.presale_name,
                    "color": row.color,
                    "nights": row.nights,
                    "missing": list(row.missing),
                    "defects": list(row.defects),
                    "extras": list(row.extras),
                    "photos": photos[row.id],
                    "video": row.video_url,
                    "price": int(row.presale_price or 0),
                    "status": "reserved" if row.reserved else "available",
                    "listedAt": row.created_at.astimezone(TAIPEI).date().isoformat(),
                    "updatedAt": taipei_iso(row.updated_at),
                    "presale": True,
                    "formerIds": [],
                }
            )
        return {"soldDays": SOLD_DAYS, "tents": tents}

    async def clear_reserved_for_sold(self, store_id: int) -> int:
        """已售出卻還勾著保留的清掉，退貨回庫時才不會又變成保留中（docs/97 §6）。"""
        # 預售還沒有序號品，不在此列（docs/102）
        rows = [
            (r, r.serialized_item_id)
            for r in await self._repo.published(store_id)
            if r.reserved and r.serialized_item_id is not None
        ]
        stale: list[int] = []
        for row, item_id in rows:
            item = await self._inventory.get_serialized_by_id(store_id, item_id)
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
    ) -> tuple[SerializedItem, OnlineTentListing]:
        item = await self._editable_item(store_id, item_id)
        row = await self._repo.by_item(store_id, item.id, for_update=True)
        if row is None:
            raise OnlineTentListingNotFound("這件還沒有照片")
        return item, row

    async def _append_photo(self, row: OnlineTentListing, sha: str) -> None:
        current = (await self._repo.photos([row.id]))[row.id]
        if sha in current:
            raise OnlineTentPhotoRejected("這張照片已經傳過了")
        if len(current) >= MAX_PHOTOS:
            raise OnlineTentPhotoRejected(f"每頂帳篷最多 {MAX_PHOTOS} 張照片，請先刪掉一張")
        await self._repo.replace_photos(row, [*current, sha])

    async def _remove_photo(self, row: OnlineTentListing, sha: str) -> None:
        current = (await self._repo.photos([row.id]))[row.id]
        if sha not in current:
            raise OnlineTentListingNotFound("找不到這張照片")
        await self._repo.replace_photos(row, [s for s in current if s != sha])

    async def _reorder_photos(self, row: OnlineTentListing, shas: list[str]) -> None:
        current = (await self._repo.photos([row.id]))[row.id]
        if sorted(shas) != sorted(current):
            raise OnlineTentPhotoOrderInvalid("照片有變動，請重新整理後再排一次")
        await self._repo.replace_photos(row, shas)

    async def _open_presale(
        self, store_id: int, listing_id: int, *, for_update: bool = True
    ) -> OnlineTentListing:
        row = await self._repo.presale(store_id, listing_id, for_update=for_update)
        if row is None:
            raise OnlineTentListingNotFound(f"找不到預售 {listing_id}")
        if row.serialized_item_id is not None or row.presale_cancelled_at is not None:
            raise OnlineTentPresaleUnavailable(_PRESALE_GONE)
        return row

    async def _check_brand(self, store_id: int, brand_id: int) -> None:
        if await self._inventory.get_brand(store_id, brand_id) is None:
            raise OnlineTentListingNotFound(f"找不到品牌 {brand_id}")

    @staticmethod
    def _apply_presale(row: OnlineTentListing, body: TentPresaleWriteRequest) -> None:
        for key, value in body.model_dump().items():
            setattr(row, _PRESALE_COLUMNS.get(key, key), value)

    @staticmethod
    def _presale_fields(row: OnlineTentListing) -> dict[str, object]:
        listing = TentListingWriteRequest.model_validate(row).model_dump(mode="json")
        presale = {key: getattr(row, column) for key, column in _PRESALE_COLUMNS.items()}
        presale["price"] = str(presale["price"])
        return {**listing, **presale}

    async def _presale_brands(self, store_id: int, rows: list[OnlineTentListing]) -> dict[int, str]:
        ids = sorted({r.presale_brand_id for r in rows if r.presale_brand_id is not None})
        return await self._inventory.brand_names(store_id, ids) if ids else {}

    async def _fresh_presale_read(self, store_id: int, row: OnlineTentListing) -> TentPresaleRead:
        await self._session.refresh(row)
        photos = (await self._repo.photos([row.id]))[row.id]
        return self._presale_read(row, photos, await self._presale_brands(store_id, [row]))

    @staticmethod
    def _presale_read(
        row: OnlineTentListing, photos: list[str], brands: dict[int, str]
    ) -> TentPresaleRead:
        assert row.presale_code is not None and row.presale_brand_id is not None
        listing = TentListingWriteRequest.model_validate(row).model_dump()
        return TentPresaleRead(
            **listing,
            brand_id=row.presale_brand_id,
            name=row.presale_name or "",
            price=row.presale_price or 0,
            seller_name=row.presale_seller_name or "",
            seller_phone=row.presale_seller_phone or "",
            note=row.presale_note,
            listing_id=row.id,
            presale_code=row.presale_code,
            brand_name=brands.get(row.presale_brand_id),
            photos=photos,
            updated_at=row.updated_at,
        )

    async def _create(
        self,
        store_id: int,
        item_id: int,
        body: TentListingWriteRequest,
        *,
        replace_on_conflict: bool,
    ) -> tuple[OnlineTentListing, bool]:
        """建一筆並回 (那筆, 是不是這次建的)。

        兩邊同時第一次建同一件：唯一鍵擋下的那位改用對方剛建的那筆。`replace_on_conflict`
        ＝存檔（整筆替換，照自己的內容蓋過去）；上傳照片只是需要一筆，不可以蓋掉別人存的內容。
        """
        row = OnlineTentListing(store_id=store_id, serialized_item_id=item_id, **body.model_dump())
        try:
            async with self._session.begin_nested():
                await self._repo.save(row)
        except IntegrityError:
            existing = await self._repo.by_item(store_id, item_id, for_update=True)
            if existing is None:
                raise
            if replace_on_conflict:
                for key, value in body.model_dump().items():
                    setattr(existing, key, value)
                await self._repo.save(existing)
            return existing, False
        return row, True

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
        *,
        noun: str = "LISTING",
    ) -> None:
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action=f"{verb}_ONLINE_TENT_{noun}",
            entity_type=_ENTITY,
            entity_id=str(listing_id),
            before=before,
            after=after,
        )
