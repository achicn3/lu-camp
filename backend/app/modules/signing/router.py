"""signing 路由：店務端 /signing（發起/查詢/作廢/取簽名圖）＋手持端 /kiosk（輪詢/簽名）。

只做 I/O 與驗證；業務邏輯在 service。角色圍堵（docs/23 D4）雙向：
- /signing 走 get_current_user → KIOSK 於中央被 403；
- /kiosk 走 get_kiosk_user → 僅 KIOSK 可通過，店務帳號 403。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, get_kiosk_user
from app.modules.signing.models import AgreementVersion, SignatureTask
from app.modules.signing.schemas import (
    KioskSignRequest,
    KioskTaskRead,
    SignatureTaskCreate,
    SignatureTaskRead,
)
from app.modules.signing.service import SigningService
from app.shared.enums import SignatureTaskKind, SignatureTaskStatus
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    ContactNotFound,
    InvalidKioskPayout,
    InvalidSignatureImage,
    SignatureContentMismatch,
    SignatureTaskConflict,
    SignatureTaskInvalidated,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
)

staff_router = APIRouter(prefix="/signing", tags=["signing"])
kiosk_router = APIRouter(prefix="/kiosk", tags=["kiosk"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StaffDep = Annotated[CurrentUser, Depends(get_current_user)]
KioskDep = Annotated[CurrentUser, Depends(get_kiosk_user)]


def _to_read(
    task: SignatureTask,
    agreement: AgreementVersion | None,
    *,
    bound_acquisition_id: int | None = None,
    bound_sale_id: int | None = None,
) -> SignatureTaskRead:
    return SignatureTaskRead(
        bound_acquisition_id=bound_acquisition_id,
        bound_sale_id=bound_sale_id,
        id=task.id,
        store_id=task.store_id,
        kind=task.kind,
        status=task.status,
        contact_id=task.contact_id,
        content=task.content,
        agreement_version=agreement.version if agreement is not None else None,
        chosen_payout=task.chosen_payout,
        has_signature=task.signature_image is not None,
        signed_at=task.signed_at,
        cancelled_at=task.cancelled_at,
        ref_type=task.ref_type,
        ref_id=task.ref_id,
        created_at=task.created_at,
    )


def _to_kiosk_read(task: SignatureTask, agreement: AgreementVersion | None) -> KioskTaskRead:
    base = _to_read(task, agreement)
    return KioskTaskRead(
        **base.model_dump(),
        agreement_title=agreement.title if agreement is not None else None,
        agreement_body=agreement.body if agreement is not None else None,
    )


# ── 店務端 ──────────────────────────────────────────────────────────────


@staff_router.post(
    "/tasks",
    response_model=SignatureTaskRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSignatureTask",
)
async def create_signature_task(
    body: SignatureTaskCreate, session: SessionDep, user: StaffDep
) -> SignatureTaskRead:
    service = SigningService(session)
    try:
        task = await service.create_task(user.store_id, body, created_by=user.id)
    except ContactNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AcquisitionRequiresNationalId as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except SignatureContentMismatch as exc:
        # 交易紀錄簽收的 ref/買方/狀態不符（docs/23 K5b：內容以後端銷售單為準）。
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except SignatureTaskConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    agreement = await service.get_agreement_for_task(task)
    result = _to_read(task, agreement)
    await session.commit()
    return result


@staff_router.get(
    "/tasks", response_model=list[SignatureTaskRead], operation_id="listSignatureTasks"
)
async def list_signature_tasks(
    session: SessionDep,
    user: StaffDep,
    task_status: Annotated[SignatureTaskStatus | None, Query(alias="status")] = None,
    kind: Annotated[SignatureTaskKind | None, Query()] = None,
    contact_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SignatureTaskRead]:
    service = SigningService(session)
    tasks = await service.list_tasks(
        user.store_id, task_status, kind=kind, contact_id=contact_id, limit=limit, offset=offset
    )
    return [_to_read(t, await service.get_agreement_for_task(t)) for t in tasks]


@staff_router.get(
    "/tasks/{task_id}", response_model=SignatureTaskRead, operation_id="getSignatureTask"
)
async def get_signature_task(
    task_id: int, session: SessionDep, user: StaffDep
) -> SignatureTaskRead:
    service = SigningService(session)
    task = await service.get_task(user.store_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="簽署任務不存在")
    # 反向綁定（調閱證據→跳轉單據）：任務建立在單據之前，ref_id 不會回填；
    # 綁定事實記在對方單據的 signature_task_id，經對方 service 反查（§2）。
    bound_acq_id: int | None = None
    bound_sale_id: int | None = None
    if task.kind is SignatureTaskKind.ACQUISITION_AFFIDAVIT:
        from app.modules.acquisition.service import AcquisitionService

        acq = await AcquisitionService(session).find_by_signature_task(user.store_id, task.id)
        bound_acq_id = acq.id if acq is not None else None
    elif task.kind is SignatureTaskKind.STORE_CREDIT_USE:
        from app.modules.sales.service import SalesService

        sale = await SalesService(session).find_sale_by_signature_task(user.store_id, task.id)
        bound_sale_id = sale.id if sale is not None else None
    return _to_read(
        task,
        await service.get_agreement_for_task(task),
        bound_acquisition_id=bound_acq_id,
        bound_sale_id=bound_sale_id,
    )


@staff_router.post(
    "/tasks/{task_id}/cancel",
    response_model=SignatureTaskRead,
    operation_id="cancelSignatureTask",
)
async def cancel_signature_task(
    task_id: int, session: SessionDep, user: StaffDep
) -> SignatureTaskRead:
    service = SigningService(session)
    try:
        task = await service.cancel_task(user.store_id, task_id)
    except SignatureTaskNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SignatureTaskNotPending as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    agreement = await service.get_agreement_for_task(task)
    result = _to_read(task, agreement)
    await session.commit()
    return result


@staff_router.get(
    "/tasks/{task_id}/signature",
    operation_id="getSignatureImage",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def get_signature_image(task_id: int, session: SessionDep, user: StaffDep) -> Response:
    """取簽名 PNG 原圖（K6 憑證聯列印用）。未簽名 → 404。"""
    task = await SigningService(session).get_task(user.store_id, task_id)
    if task is None or task.signature_image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="尚無簽名影像")
    return Response(content=task.signature_image, media_type="image/png")


# ── 手持端 ──────────────────────────────────────────────────────────────


@kiosk_router.get(
    "/tasks/current",
    response_model=KioskTaskRead | None,
    operation_id="getCurrentKioskTask",
)
async def get_current_kiosk_task(session: SessionDep, user: KioskDep) -> KioskTaskRead | None:
    """手持端輪詢：最新待簽任務；無任務回 null（前端顯示待機畫面）。"""
    service = SigningService(session)
    task = await service.latest_pending_task(user.store_id)
    if task is None:
        return None
    return _to_kiosk_read(task, await service.get_agreement_for_task(task))


@kiosk_router.get("/tasks/{task_id}", response_model=KioskTaskRead, operation_id="getKioskTask")
async def get_kiosk_task(task_id: int, session: SessionDep, user: KioskDep) -> KioskTaskRead:
    """手持端重讀指定任務（簽名頁確認狀態未被店員作廢）。

    僅限 PENDING：已簽/已作廢/不存在一律 404——手持裝置不得憑 ID 枚舉歷史內容快照。
    """
    service = SigningService(session)
    task = await service.get_pending_task_for_kiosk(user.store_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="簽署任務不存在或已結束")
    return _to_kiosk_read(task, await service.get_agreement_for_task(task))


@kiosk_router.post(
    "/tasks/{task_id}/sign", response_model=KioskTaskRead, operation_id="signKioskTask"
)
async def sign_kiosk_task(
    task_id: int, body: KioskSignRequest, session: SessionDep, user: KioskDep
) -> KioskTaskRead:
    service = SigningService(session)
    try:
        task = await service.sign_task(
            user.store_id,
            task_id,
            signature_image_base64=body.signature_image_base64,
            chosen_payout=body.chosen_payout,
            idempotency_key=body.idempotency_key,
        )
    except SignatureTaskNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SignatureTaskNotPending as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SignatureTaskInvalidated as exc:
        # 簽名當下發現 ref 銷售已作廢/退貨：service 已把任務改 CANCELLED——**提交**此作廢
        # （非 rollback，否則任務留在 PENDING、手持端會一直輪詢到；Codex K5 第五輪 high）。
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (InvalidSignatureImage, InvalidKioskPayout) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    agreement = await service.get_agreement_for_task(task)
    result = _to_kiosk_read(task, agreement)
    await session.commit()
    return result
