"""線上點餐（店內端）端點（docs/44 §5.3）：發佈菜單、狀態與桌位碼、重發桌位碼。MANAGER 專用。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.menu.photos import MAX_UPLOAD_BYTES
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.experience_service import MenuExperienceService
from app.modules.onlineorder.presentation_schemas import (
    MenuExperienceRead,
    MenuExperienceWriteRequest,
    MenuPresentationRead,
    MenuPresentationUpdateRequest,
)
from app.modules.onlineorder.presentation_service import MenuPresentationService
from app.modules.onlineorder.retail_schemas import RetailListingRead, RetailListingWriteRequest
from app.modules.onlineorder.retail_service import RetailListingService
from app.modules.onlineorder.schemas import (
    OnlineMenuPublishRead,
    OnlineOrderStatusRead,
    OnlineTableRead,
)
from app.modules.onlineorder.service import OnlineOrderService
from app.shared.exceptions import (
    MenuItemNotFound,
    MenuPhotoInvalid,
    OnlineExperienceInvalid,
    OnlineExperienceNotFound,
    OnlineOrderNotConfigured,
    OnlineOrderPushFailed,
    OnlineRetailListingDuplicate,
    OnlineRetailListingNotFound,
    OnlineTableNotFound,
)

router = APIRouter(prefix="/online-order", tags=["online-order"])


def get_online_order_client() -> OnlineOrderClient | None:
    """雲端網址或密鑰沒設定＝None（發佈會回 409 說明）。測試以 dependency override 換成假的雲端。"""
    settings = get_settings()
    if not settings.online_order_base_url or not settings.online_order_secret:
        return None
    return OnlineOrderClient(
        settings.online_order_base_url,
        settings.online_order_secret,
        store_id=settings.online_order_store_id,
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]
ClientDep = Annotated[OnlineOrderClient | None, Depends(get_online_order_client)]


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnlineOrderNotConfigured):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OnlineTableNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


_Errors = (OnlineOrderNotConfigured, OnlineOrderPushFailed, OnlineTableNotFound)


@router.get("/status", response_model=OnlineOrderStatusRead, operation_id="getOnlineOrderStatus")
async def get_online_order_status(
    session: SessionDep, user: ManagerDep, client: ClientDep
) -> OnlineOrderStatusRead:
    result = await OnlineOrderService(session, client).status(user.store_id)
    return OnlineOrderStatusRead.from_status(result)


@router.post("/publish", response_model=OnlineMenuPublishRead, operation_id="publishOnlineMenu")
async def publish_online_menu(
    session: SessionDep, user: ManagerDep, client: ClientDep
) -> OnlineMenuPublishRead:
    try:
        result = await OnlineOrderService(session, client).publish(
            user.store_id, actor_user_id=user.id
        )
    except _Errors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return OnlineMenuPublishRead.from_result(result)


@router.post(
    "/tables/{label}/rotate", response_model=OnlineTableRead, operation_id="rotateOnlineTableCode"
)
async def rotate_online_table_code(
    label: str, session: SessionDep, user: ManagerDep, client: ClientDep
) -> OnlineTableRead:
    service = OnlineOrderService(session, client)
    try:
        link = await service.rotate_table(user.store_id, label, actor_user_id=user.id)
    except _Errors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    # 先把新碼存進本機再推雲端（Codex 對抗審查 O3）：雲端換了碼但回應遺失時，
    # 本機也已經記住新碼，之後任何一次推送都帶新碼，被停用的舊碼不會再被推回去。
    await session.commit()
    try:
        await service.push_tables(user.store_id)
    except OnlineOrderPushFailed as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="新的 QR 已產生，但還沒同步到雲端（舊的 QR 暫時還能用）；"
            "請確認網路後再按一次「發佈到線上點餐」",
        ) from exc
    return OnlineTableRead.from_link(link)


@router.get(
    "/menu-items/{item_id}/presentation",
    response_model=MenuPresentationRead,
    operation_id="getMenuPresentation",
)
async def get_menu_presentation(
    item_id: int, session: SessionDep, user: AuthDep
) -> MenuPresentationRead:
    try:
        return await MenuPresentationService(session).get(user.store_id, item_id)
    except MenuItemNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put(
    "/menu-items/{item_id}/presentation",
    response_model=MenuPresentationRead,
    operation_id="updateMenuPresentation",
)
async def update_menu_presentation(
    item_id: int,
    body: MenuPresentationUpdateRequest,
    session: SessionDep,
    user: ManagerDep,
) -> MenuPresentationRead:
    try:
        result = await MenuPresentationService(session).update(
            user.store_id, item_id, body, actor_user_id=user.id
        )
    except MenuItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    return result


# ── 手沖體驗卡（docs/63 §4、M1c）──


def _experience_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnlineExperienceInvalid):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


_EXPERIENCE_ERRORS = (MenuItemNotFound, OnlineExperienceInvalid, OnlineExperienceNotFound)


@router.get(
    "/experiences", response_model=list[MenuExperienceRead], operation_id="listMenuExperiences"
)
async def list_menu_experiences(session: SessionDep, user: AuthDep) -> list[MenuExperienceRead]:
    return await MenuExperienceService(session).list_for_store(user.store_id)


@router.post(
    "/experiences",
    response_model=MenuExperienceRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMenuExperience",
)
async def create_menu_experience(
    body: MenuExperienceWriteRequest, session: SessionDep, user: ManagerDep
) -> MenuExperienceRead:
    try:
        result = await MenuExperienceService(session).create(
            user.store_id, body, actor_user_id=user.id
        )
    except _EXPERIENCE_ERRORS as exc:
        await session.rollback()
        raise _experience_error(exc) from exc
    await session.commit()
    return result


@router.put(
    "/experiences/{experience_id}",
    response_model=MenuExperienceRead,
    operation_id="updateMenuExperience",
)
async def update_menu_experience(
    experience_id: int, body: MenuExperienceWriteRequest, session: SessionDep, user: ManagerDep
) -> MenuExperienceRead:
    try:
        result = await MenuExperienceService(session).update(
            user.store_id, experience_id, body, actor_user_id=user.id
        )
    except _EXPERIENCE_ERRORS as exc:
        await session.rollback()
        raise _experience_error(exc) from exc
    await session.commit()
    return result


@router.delete(
    "/experiences/{experience_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMenuExperience",
)
async def delete_menu_experience(experience_id: int, session: SessionDep, user: ManagerDep) -> None:
    try:
        await MenuExperienceService(session).delete(
            user.store_id, experience_id, actor_user_id=user.id
        )
    except _EXPERIENCE_ERRORS as exc:
        await session.rollback()
        raise _experience_error(exc) from exc
    await session.commit()


# ── 帶回家零售商品（docs/63 §13、M1d）──


def _retail_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnlineRetailListingDuplicate):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, MenuPhotoInvalid):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


_RETAIL_ERRORS = (OnlineRetailListingDuplicate, OnlineRetailListingNotFound, MenuPhotoInvalid)


@router.get("/retail", response_model=list[RetailListingRead], operation_id="listRetailListings")
async def list_retail_listings(session: SessionDep, user: AuthDep) -> list[RetailListingRead]:
    return await RetailListingService(session).list_for_store(user.store_id)


@router.post(
    "/retail",
    response_model=RetailListingRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRetailListing",
)
async def create_retail_listing(
    body: RetailListingWriteRequest, session: SessionDep, user: ManagerDep
) -> RetailListingRead:
    try:
        result = await RetailListingService(session).create(
            user.store_id, body, actor_user_id=user.id
        )
    except _RETAIL_ERRORS as exc:
        await session.rollback()
        raise _retail_error(exc) from exc
    await session.commit()
    return result


@router.put(
    "/retail/{listing_id}", response_model=RetailListingRead, operation_id="updateRetailListing"
)
async def update_retail_listing(
    listing_id: int, body: RetailListingWriteRequest, session: SessionDep, user: ManagerDep
) -> RetailListingRead:
    try:
        result = await RetailListingService(session).update(
            user.store_id, listing_id, body, actor_user_id=user.id
        )
    except _RETAIL_ERRORS as exc:
        await session.rollback()
        raise _retail_error(exc) from exc
    await session.commit()
    return result


@router.delete(
    "/retail/{listing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteRetailListing",
)
async def delete_retail_listing(listing_id: int, session: SessionDep, user: ManagerDep) -> None:
    try:
        await RetailListingService(session).delete(
            user.store_id, listing_id, actor_user_id=user.id
        )
    except _RETAIL_ERRORS as exc:
        await session.rollback()
        raise _retail_error(exc) from exc
    await session.commit()


@router.post(
    "/retail/{listing_id}/photo",
    response_model=RetailListingRead,
    operation_id="uploadRetailListingPhoto",
)
async def upload_retail_listing_photo(
    listing_id: int,
    session: SessionDep,
    user: ManagerDep,
    file: Annotated[UploadFile, File(description="JPEG／PNG／WebP／HEIC，10 MB 以內")],
) -> RetailListingRead:
    """上傳／更換照片：轉成 WebP、長邊 1200、去掉 EXIF（含 GPS），同菜單品項照片。"""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="照片超過 10 MB，請先縮小再上傳",
        )
    try:
        result = await RetailListingService(session).set_photo(
            user.store_id, listing_id, data, actor_user_id=user.id
        )
    except _RETAIL_ERRORS as exc:
        await session.rollback()
        raise _retail_error(exc) from exc
    await session.commit()
    return result


@router.delete(
    "/retail/{listing_id}/photo",
    response_model=RetailListingRead,
    operation_id="removeRetailListingPhoto",
)
async def remove_retail_listing_photo(
    listing_id: int, session: SessionDep, user: ManagerDep
) -> RetailListingRead:
    try:
        result = await RetailListingService(session).clear_photo(
            user.store_id, listing_id, actor_user_id=user.id
        )
    except _RETAIL_ERRORS as exc:
        await session.rollback()
        raise _retail_error(exc) from exc
    await session.commit()
    return result
