"""contacts 路由：I/O 與權限，業務邏輯委派 service。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.contacts.member_schemas import (
    MemberConsignmentsRead,
    MemberOverviewRead,
    MemberPurchaseDetailRead,
    MemberPurchaseRead,
    MemberSourcedItemRead,
)
from app.modules.contacts.member_service import MemberService
from app.modules.contacts.schemas import (
    ContactCreate,
    ContactLookupRequest,
    ContactNationalIdRead,
    ContactRead,
    ContactUpdate,
)
from app.modules.contacts.service import ContactService
from app.shared.enums import ContactRole, UserRole
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    DuplicateContact,
    InvalidNationalId,
    MemberRemovalBlocked,
)

router = APIRouter(prefix="/contacts", tags=["contacts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]


@router.post(
    "",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createContact",
)
async def create_contact(
    payload: ContactCreate, session: SessionDep, user: CurrentUserDep
) -> ContactRead:
    try:
        contact = await ContactService(session).create_contact(user.store_id, payload)
    except InvalidNationalId as exc:
        # 訊息不含輸入值（PII）；於建檔當下回 422 擋下手動輸入錯誤。
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except DuplicateContact as exc:
        # 手機撞號（同店唯一）→ 409，請改以手機查找既有會員。
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityError as exc:  # 並發撞同一手機/blind index（唯一約束最終防線）
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="聯絡人重複（手機或身分證字號）"
        ) from exc
    # get_session 不自動 commit（各寫入端點自行 commit）；缺此行則建檔不落地（F6 E2E 抓到的
    # 既有 bug：POST /contacts 之前未 commit，正式環境建檔即遺失）。updateContact 已有 commit。
    await session.commit()
    return ContactRead.from_model(contact)


# national_id 放 body（不進 URL / access log）；以 blind index 精確查重。
@router.post("/lookup", response_model=ContactRead | None, operation_id="lookupContact")
async def lookup_contact(
    payload: ContactLookupRequest, session: SessionDep, user: CurrentUserDep
) -> ContactRead | None:
    contact = await ContactService(session).lookup_by_national_id(
        user.store_id, payload.national_id
    )
    return ContactRead.from_model(contact) if contact is not None else None


@router.get("", response_model=list[ContactRead], operation_id="listContacts")
async def list_contacts(
    session: SessionDep,
    user: CurrentUserDep,
    role: Annotated[ContactRole | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ContactRead]:
    contacts = await ContactService(session).search(
        user.store_id, role.value if role is not None else None, q, limit=limit, offset=offset
    )
    return [ContactRead.from_model(c) for c in contacts]


@router.get("/{contact_id}", response_model=ContactRead, operation_id="getContact")
async def get_contact(contact_id: int, session: SessionDep, user: CurrentUserDep) -> ContactRead:
    contact = await ContactService(session).get_contact(user.store_id, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到聯絡人")
    return ContactRead.from_model(contact)


@router.patch("/{contact_id}", response_model=ContactRead, operation_id="updateContact")
async def update_contact(
    contact_id: int, payload: ContactUpdate, session: SessionDep, user: CurrentUserDep
) -> ContactRead:
    """編輯會員（docs/17 §5.2、裁示 #3，2026-06-24 放寬補登）。

    一般欄位 + 電話：CLERK 可改。national_id / roles 變更原則限 MANAGER；**例外（補登）**：
    CLERK 可為「尚無 national_id」的聯絡人**設定**（非清空/非覆蓋）身分證字號，且角色**只增不減**，
    以支援收購櫃檯一條龍把買斷會員升級為賣方。改既有/清空 national_id、移除角色仍限 MANAGER。
    national_id/手機 去重的最終防線為 DB 唯一約束，並發撞重 → IntegrityError → 回滾 + 409。
    """
    provided = payload.model_fields_set
    svc = ContactService(session)
    touches_pii = "national_id" in provided
    touches_roles = "roles" in provided
    if (touches_pii or touches_roles) and user.role != UserRole.MANAGER.value:
        # 補登例外：存在的聯絡人才談權限（不存在交由 service 回 404）。
        current = await svc.get_contact(user.store_id, contact_id)
        if current is not None:
            allowed = True
            if touches_pii:
                # 僅允許「原本無 → 設定非空值」；覆蓋既有或清空一律需 MANAGER。
                allowed = current.national_id_enc is None and bool(payload.national_id)
            if touches_roles:
                before = set(current.roles)
                after = {r.value for r in (payload.roles or [])}
                allowed = allowed and after.issuperset(before)  # 角色只增不減
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="變更身分證字號或角色需 MANAGER 權限（補登除外）",
                )
    try:
        contact = await svc.update_contact(
            user.store_id, contact_id, payload, provided, actor_user_id=user.id
        )
    except (DuplicateContact, MemberRemovalBlocked) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (AcquisitionRequiresNationalId, InvalidNationalId) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IntegrityError as exc:  # 並發撞同一手機/blind index（去重最終防線）
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="聯絡人重複（手機或身分證字號），無法更新"
        ) from exc
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到聯絡人")
    await session.commit()
    return ContactRead.from_model(contact)


@router.get(
    "/{contact_id}/national-id",
    response_model=ContactNationalIdRead,
    operation_id="revealContactNationalId",
)
async def reveal_national_id(
    contact_id: int, session: SessionDep, manager: ManagerDep
) -> ContactNationalIdRead:
    national_id = await ContactService(session).reveal_national_id(
        manager.store_id, contact_id, manager.id
    )
    if national_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="找不到聯絡人或無 national_id"
        )
    return ContactNationalIdRead(national_id=national_id)


# ── 會員中心（唯讀彙整；T21-c、docs/17 §5.2）：CLERK+ 可讀，store 範圍 ──

_NOT_FOUND = "找不到會員"


@router.get(
    "/{contact_id}/overview", response_model=MemberOverviewRead, operation_id="getMemberOverview"
)
async def get_member_overview(
    contact_id: int, session: SessionDep, user: CurrentUserDep
) -> MemberOverviewRead:
    """會員彙整：profile + 點數 + 購物金餘額 + PENDING 寄售應撥 + 計數 + 近期消費。"""
    overview = await MemberService(session).get_overview(user.store_id, contact_id)
    if overview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return overview


@router.get(
    "/{contact_id}/purchases",
    response_model=list[MemberPurchaseRead],
    operation_id="listMemberPurchases",
)
async def list_member_purchases(
    contact_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MemberPurchaseRead]:
    """會員消費紀錄（可選日期區間、分頁；日期過濾在分頁前套用）。"""
    rows = await MemberService(session).list_purchases(
        user.store_id, contact_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return rows


@router.get(
    "/{contact_id}/purchases/{sale_id}",
    response_model=MemberPurchaseDetailRead,
    operation_id="getMemberPurchaseDetail",
)
async def get_member_purchase_detail(
    contact_id: int, sale_id: int, session: SessionDep, user: CurrentUserDep
) -> MemberPurchaseDetailRead:
    """單筆消費明細（lines + tenders）；非該會員的單 → 404。"""
    detail = await MemberService(session).get_purchase_detail(user.store_id, contact_id, sale_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到該會員的消費紀錄")
    return detail


@router.get(
    "/{contact_id}/consignments",
    response_model=MemberConsignmentsRead,
    operation_id="listMemberConsignments",
)
async def list_member_consignments(
    contact_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MemberConsignmentsRead:
    """會員寄售品 + 結算狀態 + PENDING 應撥加總（裁示 #2）。"""
    result = await MemberService(session).list_consignments(
        user.store_id, contact_id, limit=limit, offset=offset
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return result


@router.get(
    "/{contact_id}/sourced-items",
    response_model=list[MemberSourcedItemRead],
    operation_id="listMemberSourcedItems",
)
async def list_member_sourced_items(
    contact_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    source_type: Annotated[str | None, Query()] = None,
    item_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MemberSourcedItemRead]:
    """會員帶來的商品（買斷+寄售合併清單；可選 source_type/status 過濾；不含成本）。"""
    rows = await MemberService(session).list_sourced_items(
        user.store_id,
        contact_id,
        source_type=source_type,
        status=item_status,
        limit=limit,
        offset=offset,
    )
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return rows
