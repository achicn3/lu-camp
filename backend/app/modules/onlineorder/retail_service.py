"""線上「帶回家」零售商品（docs/63 §13、M1d）：從現有一般商品挑上線。

不存價格、成本、庫存：售價是商品含稅 `unit_price`、庫存是 `quantity_on_hand`、分類是商品分類。
照片沿用菜單照片表（同一家店、依內容雜湊去重）。跨模組只經 inventory／menu service（CLAUDE.md §2）。
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.inventory.models import CatalogProduct
from app.modules.inventory.service import InventoryService
from app.modules.menu.service import MenuService
from app.modules.onlineorder.models import OnlineRetailListing
from app.modules.onlineorder.retail_repository import RetailListingRepository
from app.modules.onlineorder.retail_schemas import RetailListingRead, RetailListingWriteRequest
from app.shared.exceptions import OnlineRetailListingDuplicate, OnlineRetailListingNotFound

_ENTITY = "online_retail_listing"


class RetailListingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = RetailListingRepository(session)
        self._inventory = InventoryService(session)
        self._menu = MenuService(session)

    async def list_for_store(self, store_id: int) -> list[RetailListingRead]:
        rows = await self._repo.list_for_store(store_id)
        return [await self._read(store_id, row) for row in rows]

    async def _product(self, store_id: int, product_id: int) -> CatalogProduct:
        product = await self._inventory.get_catalog(store_id, product_id)
        if product is None:
            raise OnlineRetailListingNotFound(f"找不到一般商品 {product_id}")
        return product

    async def _read(self, store_id: int, row: OnlineRetailListing) -> RetailListingRead:
        product = await self._product(store_id, row.catalog_product_id)
        names = (
            await self._inventory.category_names(store_id, [product.category_id])
            if product.category_id is not None
            else {}
        )
        return RetailListingRead(
            **RetailListingWriteRequest.model_validate(row).model_dump(),
            id=row.id,
            photo_sha256=row.photo_sha256,
            product_name=product.name,
            unit_price=product.unit_price,
            quantity_on_hand=product.quantity_on_hand,
            product_active=product.is_active,
            category_name=names.get(product.category_id) if product.category_id else None,
        )

    async def _listing(
        self, store_id: int, listing_id: int, *, for_update: bool = False
    ) -> OnlineRetailListing:
        row = await self._repo.get(store_id, listing_id, for_update=for_update)
        if row is None:
            raise OnlineRetailListingNotFound(f"找不到帶回家商品 {listing_id}")
        return row

    async def _ensure_unique(self, store_id: int, product_id: int, listing_id: int | None) -> None:
        existing = await self._repo.by_product(store_id, product_id)
        if existing is not None and existing.id != listing_id:
            raise OnlineRetailListingDuplicate("這個商品已經上線了，請直接編輯那一筆")

    async def _save(self, row: OnlineRetailListing) -> None:
        # 兩個人同時加同一個商品：唯一鍵擋下的那位也要看到同樣的說明。
        try:
            async with self._session.begin_nested():
                await self._repo.save(row)
        except IntegrityError as exc:
            raise OnlineRetailListingDuplicate("這個商品已經上線了，請直接編輯那一筆") from exc

    async def create(
        self, store_id: int, body: RetailListingWriteRequest, *, actor_user_id: int
    ) -> RetailListingRead:
        """挑一個商品上線並寫稽核。"""
        await self._product(store_id, body.catalog_product_id)
        await self._ensure_unique(store_id, body.catalog_product_id, None)
        row = OnlineRetailListing(store_id=store_id, **body.model_dump())
        await self._save(row)
        await self._audit(store_id, actor_user_id, "CREATE", row.id, None, body.model_dump())
        return await self._read(store_id, row)

    async def update(
        self,
        store_id: int,
        listing_id: int,
        body: RetailListingWriteRequest,
        *,
        actor_user_id: int,
    ) -> RetailListingRead:
        """整筆替換並寫稽核（前後值）。"""
        row = await self._listing(store_id, listing_id, for_update=True)
        await self._product(store_id, body.catalog_product_id)
        await self._ensure_unique(store_id, body.catalog_product_id, row.id)
        before = RetailListingWriteRequest.model_validate(row).model_dump()
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        await self._save(row)
        await self._audit(store_id, actor_user_id, "UPDATE", row.id, before, body.model_dump())
        return await self._read(store_id, row)

    async def delete(self, store_id: int, listing_id: int, *, actor_user_id: int) -> None:
        """下線（只刪呈現設定；商品、庫存與歷史訂單不受影響）。"""
        row = await self._listing(store_id, listing_id, for_update=True)
        before = RetailListingWriteRequest.model_validate(row).model_dump()
        await self._repo.delete(row)
        await self._audit(store_id, actor_user_id, "DELETE", listing_id, before, None)

    async def set_photo(
        self, store_id: int, listing_id: int, data: bytes, *, actor_user_id: int
    ) -> RetailListingRead:
        """上傳／更換照片：先轉檔（不持鎖），再鎖住這筆換上。不合格丟 `MenuPhotoInvalid`。"""
        sha = await self._menu.store_photo(store_id, data)
        return await self._change_photo(store_id, listing_id, sha, actor_user_id)

    async def clear_photo(
        self, store_id: int, listing_id: int, *, actor_user_id: int
    ) -> RetailListingRead:
        """拿掉照片（照片本身保留，已發佈的線上菜單可能還在引用）。"""
        return await self._change_photo(store_id, listing_id, None, actor_user_id)

    async def _change_photo(
        self, store_id: int, listing_id: int, sha: str | None, actor_user_id: int
    ) -> RetailListingRead:
        row = await self._listing(store_id, listing_id, for_update=True)
        before = row.photo_sha256
        row.photo_sha256 = sha
        await self._repo.save(row)
        if before != sha:
            await self._audit(
                store_id,
                actor_user_id,
                "PHOTO",
                row.id,
                {"photo_sha256": before},
                {"photo_sha256": sha},
            )
        return await self._read(store_id, row)

    async def _audit(
        self,
        store_id: int,
        actor_user_id: int,
        verb: str,
        listing_id: int,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> None:
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action=f"{verb}_ONLINE_RETAIL_LISTING",
            entity_type=_ENTITY,
            entity_id=str(listing_id),
            before=None if before is None else _json(before),
            after=None if after is None else _json(after),
        )


def _json(values: dict[str, object]) -> dict[str, object]:
    """稽核存 JSON：列舉存值。"""
    return {k: getattr(v, "value", v) for k, v in values.items()}
