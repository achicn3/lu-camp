"""寄售帳篷上官網（docs/97 §6）：POS「官網帳篷」分頁的端點。店員、店長都可以用。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.menu.photos import MAX_UPLOAD_BYTES
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.router import get_online_order_client
from app.modules.onlineorder.tent_publish_service import TentPublishService
from app.modules.onlineorder.tent_schemas import (
    TentListingRead,
    TentListingWriteRequest,
    TentPhotoOrderRequest,
    TentPresaleRead,
    TentPresaleWriteRequest,
    TentPublishStatusRead,
    TentReservedRead,
)
from app.modules.onlineorder.tent_service import TentListingService
from app.shared.exceptions import (
    MenuPhotoInvalid,
    OnlineTentListingNotEditable,
    OnlineTentListingNotFound,
    OnlineTentPhotoOrderInvalid,
    OnlineTentPhotoRejected,
    OnlineTentPresaleUnavailable,
)

router = APIRouter(prefix="/online-order/tents", tags=["online-order"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[CurrentUser, Depends(get_current_user)]
ClientDep = Annotated[OnlineOrderClient | None, Depends(get_online_order_client)]

_ERRORS = (
    MenuPhotoInvalid,
    OnlineTentListingNotEditable,
    OnlineTentListingNotFound,
    OnlineTentPhotoOrderInvalid,
    OnlineTentPhotoRejected,
    OnlineTentPresaleUnavailable,
)


def _error(exc: Exception) -> HTTPException:
    if isinstance(
        exc, (OnlineTentListingNotEditable, OnlineTentPhotoRejected, OnlineTentPresaleUnavailable)
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (MenuPhotoInvalid, OnlineTentPhotoOrderInvalid)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="照片超過 10 MB，請先縮小再上傳",
        )
    return data


@router.get("", response_model=list[TentListingRead], operation_id="listTentListings")
async def list_tent_listings(session: SessionDep, user: AuthDep) -> list[TentListingRead]:
    """在庫的寄售品與官網資料。"""
    return await TentListingService(session).list_candidates(user.store_id)


@router.get("/status", response_model=TentPublishStatusRead, operation_id="getTentPublishStatus")
async def get_tent_publish_status(
    session: SessionDep, user: AuthDep, client: ClientDep
) -> TentPublishStatusRead:
    """官網同步狀態：上次推送時間、錯誤、POS 上有沒有還沒推上去的變更。"""
    return await TentPublishService(session, client).status(user.store_id, now=datetime.now(UTC))


@router.post("/publish", response_model=TentPublishStatusRead, operation_id="publishTents")
async def publish_tents(
    session: SessionDep, user: AuthDep, client: ClientDep
) -> TentPublishStatusRead:
    """立刻推一次（存檔後畫面會呼叫）；內容沒變就不推。雲端出錯只記下原因，不回錯誤碼。"""
    svc = TentPublishService(session, client)
    now = datetime.now(UTC)
    await svc.publish(user.store_id, now=now)
    await session.commit()
    return await svc.status(user.store_id, now=now)


@router.get(
    "/by-item-code/{item_code}",
    response_model=TentReservedRead,
    operation_id="getTentReservedByItemCode",
)
async def get_tent_reserved(item_code: str, session: SessionDep, user: AuthDep) -> TentReservedRead:
    """POS 掃到商品時查：官網上是不是標「已有人保留」（提醒不擋）。"""
    reserved = await TentListingService(session).reserved_for_code(user.store_id, item_code)
    return TentReservedRead(reserved=reserved)


# ── 預售（docs/102）：帳篷還在賣家家裡就先上官網 ──


@router.get("/presales", response_model=list[TentPresaleRead], operation_id="listTentPresales")
async def list_tent_presales(session: SessionDep, user: AuthDep) -> list[TentPresaleRead]:
    """還沒取消、還沒轉寄售的預售（含賣家聯絡方式，只在 POS）。"""
    return await TentListingService(session).list_presales(user.store_id)


@router.post(
    "/presales",
    response_model=TentPresaleRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTentPresale",
)
async def create_tent_presale(
    body: TentPresaleWriteRequest, session: SessionDep, user: AuthDep
) -> TentPresaleRead:
    try:
        result = await TentListingService(session).create_presale(
            user.store_id, body, actor_user_id=user.id
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.put(
    "/presales/{listing_id}", response_model=TentPresaleRead, operation_id="saveTentPresale"
)
async def save_tent_presale(
    listing_id: int, body: TentPresaleWriteRequest, session: SessionDep, user: AuthDep
) -> TentPresaleRead:
    try:
        result = await TentListingService(session).save_presale(
            user.store_id, listing_id, body, actor_user_id=user.id
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/presales/{listing_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="cancelTentPresale",
)
async def cancel_tent_presale(listing_id: int, session: SessionDep, user: AuthDep) -> None:
    """賣家不賣了／帳篷沒送來：官網拿掉，POS 留紀錄。"""
    try:
        await TentListingService(session).cancel_presale(
            user.store_id, listing_id, actor_user_id=user.id
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()


@router.post(
    "/presales/{listing_id}/photos",
    response_model=TentPresaleRead,
    operation_id="addTentPresalePhoto",
)
async def add_tent_presale_photo(
    listing_id: int,
    session: SessionDep,
    user: AuthDep,
    file: Annotated[UploadFile, File(description="JPEG／PNG／WebP／HEIC，10 MB 以內")],
) -> TentPresaleRead:
    data = await _read_upload(file)
    try:
        result = await TentListingService(session).add_presale_photo(
            user.store_id, listing_id, data
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.put(
    "/presales/{listing_id}/photos/order",
    response_model=TentPresaleRead,
    operation_id="reorderTentPresalePhotos",
)
async def reorder_tent_presale_photos(
    listing_id: int, body: TentPhotoOrderRequest, session: SessionDep, user: AuthDep
) -> TentPresaleRead:
    try:
        result = await TentListingService(session).reorder_presale_photos(
            user.store_id, listing_id, body.photos
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.delete(
    "/presales/{listing_id}/photos/{sha256}",
    response_model=TentPresaleRead,
    operation_id="removeTentPresalePhoto",
)
async def remove_tent_presale_photo(
    listing_id: int, sha256: str, session: SessionDep, user: AuthDep
) -> TentPresaleRead:
    try:
        result = await TentListingService(session).remove_presale_photo(
            user.store_id, listing_id, sha256
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


# ── 在庫寄售品 ──


@router.put("/{item_id}", response_model=TentListingRead, operation_id="saveTentListing")
async def save_tent_listing(
    item_id: int, body: TentListingWriteRequest, session: SessionDep, user: AuthDep
) -> TentListingRead:
    try:
        result = await TentListingService(session).save(
            user.store_id, item_id, body, actor_user_id=user.id
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/{item_id}/photos", response_model=TentListingRead, operation_id="addTentListingPhoto"
)
async def add_tent_listing_photo(
    item_id: int,
    session: SessionDep,
    user: AuthDep,
    file: Annotated[UploadFile, File(description="JPEG／PNG／WebP／HEIC，10 MB 以內")],
) -> TentListingRead:
    """加一張照片到最後：轉成 WebP、長邊 1200、去掉 EXIF（含 GPS），同菜單照片。"""
    data = await _read_upload(file)
    try:
        result = await TentListingService(session).add_photo(
            user.store_id, item_id, data, actor_user_id=user.id
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.put(
    "/{item_id}/photos/order",
    response_model=TentListingRead,
    operation_id="reorderTentListingPhotos",
)
async def reorder_tent_listing_photos(
    item_id: int, body: TentPhotoOrderRequest, session: SessionDep, user: AuthDep
) -> TentListingRead:
    try:
        result = await TentListingService(session).reorder_photos(
            user.store_id, item_id, body.photos
        )
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result


@router.delete(
    "/{item_id}/photos/{sha256}",
    response_model=TentListingRead,
    operation_id="removeTentListingPhoto",
)
async def remove_tent_listing_photo(
    item_id: int, sha256: str, session: SessionDep, user: AuthDep
) -> TentListingRead:
    try:
        result = await TentListingService(session).remove_photo(user.store_id, item_id, sha256)
    except _ERRORS as exc:
        await session.rollback()
        raise _error(exc) from exc
    await session.commit()
    return result
