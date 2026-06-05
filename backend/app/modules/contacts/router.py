"""contacts 路由：I/O 與權限，業務邏輯委派 service。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.contacts.schemas import (
    ContactCreate,
    ContactLookupRequest,
    ContactNationalIdRead,
    ContactRead,
)
from app.modules.contacts.service import ContactService
from app.shared.enums import ContactRole, UserRole

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
