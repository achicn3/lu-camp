"""組合包袋裝條碼路由（ADR-028）：管理者建立／列出／停用，店員 POS 掃碼。

只做 I/O 與驗證；業務邏輯在 pack_service。領域例外對應 HTTP：NotFound→404、
CampaignConflict→409、BundlePackInvalid／InvalidCampaignTarget→422。建立與停用於 service 寫稽核。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.campaigns.pack_service import BundlePackService, PackScan, PackView
from app.modules.campaigns.schemas import (
    BundlePackCreateRequest,
    BundlePackItemRead,
    BundlePackRead,
    BundlePackScanItemRead,
    BundlePackScanRead,
)
from app.shared.exceptions import (
    BundlePackInvalid,
    BundlePackNotFound,
    CampaignConflict,
    CampaignNotFound,
    InvalidCampaignTarget,
)

router = APIRouter(tags=["bundle-packs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def _read(view: PackView) -> BundlePackRead:
    pack = view.pack
    return BundlePackRead(
        id=pack.id,
        store_id=pack.store_id,
        campaign_id=pack.campaign_id,
        code=pack.code,
        name=pack.name,
        is_active=pack.is_active,
        created_at=pack.created_at,
        items=[
            BundlePackItemRead(
                item_type=i.item_type, target_id=i.target_id, qty=i.qty, label=i.label
            )
            for i in view.items
        ],
    )


def _scan_read(scan: PackScan) -> BundlePackScanRead:
    return BundlePackScanRead(
        id=scan.pack.id,
        code=scan.pack.code,
        name=scan.pack.name,
        campaign_id=scan.pack.campaign_id,
        campaign_name=scan.campaign_name,
        bundle_price=scan.bundle_price,
        campaign_effective=scan.campaign_effective,
        items=[
            BundlePackScanItemRead(
                item_type=i.item_type,
                target_id=i.target_id,
                qty=i.qty,
                code=i.code,
                name=i.name,
                unit_price=i.unit_price,
                note=i.note,
                brand_id=i.brand_id,
                stock=i.stock,
                available=i.available,
                unavailable_reason=i.unavailable_reason,
            )
            for i in scan.items
        ],
    )


async def _view(service: BundlePackService, store_id: int, pack_id: int) -> PackView:
    pack = await service.get(store_id, pack_id)
    views = await service.list_packs(store_id, pack.campaign_id)
    return next(v for v in views if v.pack.id == pack_id)


@router.post(
    "/campaigns/{campaign_id}/packs",
    response_model=BundlePackRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBundlePack",
)
async def create_bundle_pack(
    campaign_id: int, body: BundlePackCreateRequest, session: SessionDep, user: ManagerDep
) -> BundlePackRead:
    """建一袋：袋裡內容單獨結帳必須剛好湊成這個組合價的一組（ADR-028）。"""
    service = BundlePackService(session)
    try:
        pack = await service.create_pack(
            user.store_id,
            campaign_id,
            name=body.name,
            items=body.items,
            actor_user_id=user.id,
        )
        read = _read(await _view(service, user.store_id, pack.id))
    except CampaignNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (BundlePackInvalid, InvalidCampaignTarget) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return read


@router.get(
    "/campaigns/{campaign_id}/packs",
    response_model=list[BundlePackRead],
    operation_id="listBundlePacks",
)
async def list_bundle_packs(
    campaign_id: int, session: SessionDep, user: ManagerDep
) -> list[BundlePackRead]:
    try:
        views = await BundlePackService(session).list_packs(user.store_id, campaign_id)
    except CampaignNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [_read(v) for v in views]


@router.post(
    "/bundle-packs/{pack_id}/deactivate",
    response_model=BundlePackRead,
    operation_id="deactivateBundlePack",
)
async def deactivate_bundle_pack(
    pack_id: int, session: SessionDep, user: ManagerDep
) -> BundlePackRead:
    """停用：之後掃不到（袋子拆了、標籤作廢）。"""
    service = BundlePackService(session)
    try:
        await service.deactivate(user.store_id, pack_id, actor_user_id=user.id)
        read = _read(await _view(service, user.store_id, pack_id))
    except BundlePackNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    return read


@router.get(
    "/bundle-packs/by-code/{code}",
    response_model=BundlePackScanRead,
    operation_id="getBundlePackByCode",
)
async def get_bundle_pack_by_code(
    code: str, session: SessionDep, user: CurrentUserDep
) -> BundlePackScanRead:
    """POS 掃袋裝條碼：袋裡每件商品的現況與組合價是否生效。停用／他店 → 404。"""
    try:
        scan = await BundlePackService(session).scan(user.store_id, code)
    except BundlePackNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _scan_read(scan)
