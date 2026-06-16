"""inventory 唯讀查詢路由（T19-pre-B）：掃碼查件、序號品/數量品/散裝堆列表。

只做 I/O 與驗證（§2）；全部需認證、以 token 的 store_id 範圍過濾（§4）。
寫入（建檔/改價/狀態轉移）不在此 router——由 acquisition/sales 等流程經
service 進行，庫存頁的改價/調整功能屬後續任務。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.inventory.schemas import (
    BrandCreate,
    BrandRead,
    BulkLotRead,
    CatalogProductRead,
    CategoryCreate,
    CategoryRead,
    CategoryTargetUpdate,
    PricingRuleRead,
    PricingRulesUpdate,
    ProductModelCreate,
    ProductModelRead,
    SerializedItemRead,
)
from app.modules.inventory.service import InventoryService
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import BulkLotStatus, OwnershipType, SerializedItemStatus

router = APIRouter(tags=["inventory"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@router.get(
    "/serialized-items/by-code/{item_code}",
    response_model=SerializedItemRead,
    operation_id="getSerializedItemByCode",
)
async def get_serialized_by_code(
    item_code: str, session: SessionDep, user: CurrentUserDep
) -> SerializedItemRead:
    """POS 掃碼查件：以 item_code 取序號品（他店/不存在一律 404，不洩漏跨店資料）。"""
    item = await InventoryService(session).get_serialized_by_code(user.store_id, item_code)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此識別碼的序號品")
    return SerializedItemRead.model_validate(item)


@router.get(
    "/serialized-items",
    response_model=list[SerializedItemRead],
    operation_id="listSerializedItems",
)
async def list_serialized(
    session: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[SerializedItemStatus | None, Query(alias="status")] = None,
    ownership_type: Annotated[OwnershipType | None, Query(alias="ownership")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SerializedItemRead]:
    items = await InventoryService(session).list_serialized(
        user.store_id,
        status=status_filter,
        ownership_type=ownership_type,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [SerializedItemRead.model_validate(item) for item in items]


@router.get(
    "/catalog-products",
    response_model=list[CatalogProductRead],
    operation_id="listCatalogProducts",
)
async def list_catalog(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    low_stock: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CatalogProductRead]:
    products = await InventoryService(session).list_catalog(
        user.store_id, q=q, low_stock=low_stock, limit=limit, offset=offset
    )
    return [CatalogProductRead.model_validate(product) for product in products]


@router.get(
    "/bulk-lots/by-code/{lot_code}",
    response_model=BulkLotRead,
    operation_id="getBulkLotByCode",
)
async def get_bulk_lot_by_code(
    lot_code: str, session: SessionDep, user: CurrentUserDep
) -> BulkLotRead:
    """POS 掃堆標籤：以 lot_code 取散裝堆（docs/04；標籤條碼即 Code 128 編 lot_code）。"""
    lot = await InventoryService(session).get_bulk_lot_by_code(user.store_id, lot_code)
    if lot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此識別碼的散裝堆")
    return BulkLotRead.model_validate(lot)


# ── 品牌 / 型號（收購頁 combobox；查無即建）──


@router.get("/brands", response_model=list[BrandRead], operation_id="listBrands")
async def list_brands(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[BrandRead]:
    brands = await InventoryService(session).list_brands(user.store_id, q=q, limit=limit)
    return [BrandRead.model_validate(brand) for brand in brands]


@router.post("/brands", response_model=BrandRead, operation_id="createBrand")
async def create_brand(
    payload: BrandCreate, session: SessionDep, user: CurrentUserDep
) -> BrandRead:
    """建立品牌（同名 get_or_create 冪等）；store 範圍。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="品牌名稱不可空白"
        )
    brand = await InventoryService(session).get_or_create_brand(user.store_id, name)
    await session.commit()
    return BrandRead.model_validate(brand)


@router.get(
    "/product-models", response_model=list[ProductModelRead], operation_id="listProductModels"
)
async def list_product_models(
    session: SessionDep,
    user: CurrentUserDep,
    brand_id: Annotated[int | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=150)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ProductModelRead]:
    models = await InventoryService(session).list_product_models(
        user.store_id, brand_id=brand_id, q=q, limit=limit
    )
    return [ProductModelRead.model_validate(model) for model in models]


@router.post("/product-models", response_model=ProductModelRead, operation_id="createProductModel")
async def create_product_model(
    payload: ProductModelCreate, session: SessionDep, user: CurrentUserDep
) -> ProductModelRead:
    """建立型號（歸屬指定品牌；同品牌同名 get_or_create 冪等）。品牌須屬本店。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="型號名稱不可空白"
        )
    svc = InventoryService(session)
    if await svc.get_brand(user.store_id, payload.brand_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到本店的此品牌")
    model = await svc.get_or_create_product_model(user.store_id, payload.brand_id, name)
    await session.commit()
    return ProductModelRead.model_validate(model)


# ── 分類 / 定價規則（收購頁定價骨幹）──


@router.get("/categories", response_model=list[CategoryRead], operation_id="listCategories")
async def list_categories(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CategoryRead]:
    cats = await InventoryService(session).list_categories(user.store_id, q=q, limit=limit)
    return [CategoryRead.model_validate(cat) for cat in cats]


@router.post("/categories", response_model=CategoryRead, operation_id="createCategory")
async def create_category(
    payload: CategoryCreate, session: SessionDep, user: CurrentUserDep
) -> CategoryRead:
    """建立分類（查無即建，seed 各成色帶定價規則）；未給 target 用店層級 default_margin_pct。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="分類名稱不可空白"
        )
    default_margin = (
        await StoreSettingsService(session).get_effective_settings(user.store_id)
    ).default_margin_pct
    target = payload.target_margin_pct if payload.target_margin_pct is not None else default_margin
    category = await InventoryService(session).get_or_create_category(
        user.store_id, name, default_target_margin_pct=target
    )
    await session.commit()
    return CategoryRead.model_validate(category)


@router.patch(
    "/categories/{category_id}", response_model=CategoryRead, operation_id="updateCategoryTarget"
)
async def update_category_target(
    category_id: int, payload: CategoryTargetUpdate, session: SessionDep, user: ManagerDep
) -> CategoryRead:
    """更新分類目標毛利率（MANAGER）。"""
    category = await InventoryService(session).update_category_target(
        user.store_id, category_id, payload.target_margin_pct
    )
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到本店的此分類")
    await session.commit()
    return CategoryRead.model_validate(category)


@router.get(
    "/categories/{category_id}/pricing-rules",
    response_model=list[PricingRuleRead],
    operation_id="listCategoryPricingRules",
)
async def list_pricing_rules(
    category_id: int, session: SessionDep, user: CurrentUserDep
) -> list[PricingRuleRead]:
    svc = InventoryService(session)
    if await svc.get_category(user.store_id, category_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到本店的此分類")
    rules = await svc.list_pricing_rules(user.store_id, category_id)
    return [PricingRuleRead.model_validate(rule) for rule in rules]


@router.put(
    "/categories/{category_id}/pricing-rules",
    response_model=list[PricingRuleRead],
    operation_id="updateCategoryPricingRules",
)
async def update_pricing_rules(
    category_id: int, payload: PricingRulesUpdate, session: SessionDep, user: ManagerDep
) -> list[PricingRuleRead]:
    """批次更新分類各成色帶定價規則（MANAGER）。"""
    updates = [
        (r.condition_band, r.discount_ceiling_pct, r.min_margin_pct, r.min_price_multiple)
        for r in payload.rules
    ]
    rules = await InventoryService(session).update_pricing_rules(
        user.store_id, category_id, updates
    )
    if rules is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到本店的此分類")
    await session.commit()
    return [PricingRuleRead.model_validate(rule) for rule in rules]


@router.get("/bulk-lots", response_model=list[BulkLotRead], operation_id="listBulkLots")
async def list_bulk_lots(
    session: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[BulkLotStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BulkLotRead]:
    lots = await InventoryService(session).list_bulk_lots(
        user.store_id, status=status_filter, q=q, limit=limit, offset=offset
    )
    return [BulkLotRead.model_validate(lot) for lot in lots]
