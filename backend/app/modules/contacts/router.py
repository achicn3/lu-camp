"""contacts 路由：I/O 與權限，業務邏輯委派 service。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
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
    contact = await ContactService(session).create_contact(user.store_id, payload)
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
    """編輯會員（docs/17 §5.2、裁示 #3）。

    一般欄位 + 電話：CLERK 可改；變更 national_id / roles：限 MANAGER（否則 403）。
    national_id 變更去重的最終防線為 DB 唯一約束，並發撞重 → IntegrityError → 回滾 + 409。
    """
    provided = payload.model_fields_set
    if ("national_id" in provided or "roles" in provided) and user.role != UserRole.MANAGER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="變更身分證字號或角色需 MANAGER 權限",
        )
    svc = ContactService(session)
    try:
        contact = await svc.update_contact(
            user.store_id, contact_id, payload, provided, actor_user_id=user.id
        )
    except (DuplicateContact, MemberRemovalBlocked) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AcquisitionRequiresNationalId as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IntegrityError as exc:  # 並發撞同一 blind index（去重最終防線）
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="national_id 重複，無法更新"
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
