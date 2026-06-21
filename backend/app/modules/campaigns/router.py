"""campaigns 路由（docs/21）：門市活動 CRUD 與狀態機，MANAGER 限定。

只做 I/O 與驗證；業務邏輯在 service。領域例外對應 HTTP：NotFound→404、Conflict→409、
InvalidDiscountPct→422。狀態變更皆於 service 寫稽核。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.campaigns.models import Campaign
from app.modules.campaigns.schemas import CampaignCreateRequest, CampaignRead
from app.modules.campaigns.service import CampaignService
from app.shared.enums import CampaignStatus
from app.shared.exceptions import CampaignConflict, CampaignNotFound, InvalidDiscountPct

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


def _to_read(campaign: Campaign) -> CampaignRead:
    return CampaignRead.model_validate(campaign, from_attributes=True)


@router.post(
    "",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createCampaign",
)
async def create_campaign(
    body: CampaignCreateRequest, session: SessionDep, user: ManagerDep
) -> CampaignRead:
    try:
        campaign = await CampaignService(session).create_campaign(
            user.store_id,
            name=body.name,
            discount_pct=body.discount_pct,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            applies_owned_serialized=body.applies_owned_serialized,
            applies_owned_bulk=body.applies_owned_bulk,
            applies_catalog=body.applies_catalog,
            applies_consignment=body.applies_consignment,
            consignment_discount_bearing=body.consignment_discount_bearing,
            created_by=user.id,
        )
    except InvalidDiscountPct as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except CampaignConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_read(campaign)


@router.get("", response_model=list[CampaignRead], operation_id="listCampaigns")
async def list_campaigns(
    session: SessionDep,
    user: ManagerDep,
    campaign_status: Annotated[CampaignStatus | None, Query(alias="status")] = None,
) -> list[CampaignRead]:
    campaigns = await CampaignService(session).list_campaigns(user.store_id, status=campaign_status)
    return [_to_read(c) for c in campaigns]


@router.get("/{campaign_id}", response_model=CampaignRead, operation_id="getCampaign")
async def get_campaign(campaign_id: int, session: SessionDep, user: ManagerDep) -> CampaignRead:
    campaign = await CampaignService(session).get(user.store_id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到活動")
    return _to_read(campaign)


async def _transition(
    session: AsyncSession, store_id: int, campaign_id: int, actor_user_id: int, action: str
) -> Campaign:
    svc = CampaignService(session)
    try:
        if action == "activate":
            return await svc.activate(store_id, campaign_id, actor_user_id=actor_user_id)
        if action == "end":
            return await svc.end(store_id, campaign_id, actor_user_id=actor_user_id)
        return await svc.cancel(store_id, campaign_id, actor_user_id=actor_user_id)
    except CampaignNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/{campaign_id}/activate", response_model=CampaignRead, operation_id="activateCampaign"
)
async def activate_campaign(
    campaign_id: int, session: SessionDep, user: ManagerDep
) -> CampaignRead:
    return _to_read(await _transition(session, user.store_id, campaign_id, user.id, "activate"))


@router.post("/{campaign_id}/end", response_model=CampaignRead, operation_id="endCampaign")
async def end_campaign(campaign_id: int, session: SessionDep, user: ManagerDep) -> CampaignRead:
    return _to_read(await _transition(session, user.store_id, campaign_id, user.id, "end"))


@router.post("/{campaign_id}/cancel", response_model=CampaignRead, operation_id="cancelCampaign")
async def cancel_campaign(campaign_id: int, session: SessionDep, user: ManagerDep) -> CampaignRead:
    return _to_read(await _transition(session, user.store_id, campaign_id, user.id, "cancel"))
