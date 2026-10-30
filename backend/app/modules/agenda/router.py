"""agenda 路由：行事曆彙整、今天、待辦、重複待辦、事件（docs/99 §4、§8、§9）。

店員、管理者都能用；「只限管理者」的項目店員看不到（404）、也不能建立（403）。
「今天」一律以門市時區（台北）的今天計。
"""

from collections.abc import Awaitable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.core.time import store_date, utc_now
from app.modules.agenda.schemas import (
    AgendaEventCreate,
    AgendaEventOccurrenceRequest,
    AgendaEventRead,
    AgendaEventUpdate,
    AgendaFeedRead,
    AgendaOccurrenceUpdate,
    AgendaSeriesCreate,
    AgendaSeriesRead,
    AgendaSeriesUpdate,
    AgendaSkipOverdueRead,
    AgendaTaskCreate,
    AgendaTaskRead,
    AgendaTaskStatusRequest,
    AgendaTaskUpdate,
    AgendaTodayRead,
)
from app.modules.agenda.service import AgendaService, Viewer
from app.shared.enums import UserRole
from app.shared.exceptions import (
    AgendaForbidden,
    AgendaItemInvalid,
    AgendaItemNotFound,
    DomainError,
    InvalidRecurrence,
)

router = APIRouter(prefix="/agenda", tags=["agenda"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StaffDep = Annotated[CurrentUser, Depends(get_current_user)]


def _viewer(user: CurrentUser) -> Viewer:
    return Viewer(user_id=user.id, is_manager=user.role == UserRole.MANAGER)


def _today() -> date:
    return store_date(utc_now())


def _http_error(exc: DomainError) -> HTTPException:
    if isinstance(exc, AgendaItemNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AgendaForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


async def _read[T](session: AsyncSession, reading: Awaitable[T]) -> T:
    try:
        return await reading
    except (AgendaItemNotFound, AgendaItemInvalid, AgendaForbidden, InvalidRecurrence) as exc:
        await session.rollback()
        raise _http_error(exc) from exc


async def _write[T](session: AsyncSession, writing: Awaitable[T]) -> T:
    result = await _read(session, writing)
    await session.commit()
    return result


# ── 彙整與今天 ────────────────────────────────────────────


@router.get("/items", response_model=AgendaFeedRead, operation_id="listAgendaItems")
async def list_agenda_items(
    session: SessionDep,
    user: StaffDep,
    start: Annotated[date, Query(description="第一天（含）")],
    end: Annotated[date, Query(description="最後一天（含）；一次最多 93 天")],
) -> AgendaFeedRead:
    items = await _read(
        session,
        AgendaService(session).feed(user.store_id, _viewer(user), start, end, today=_today()),
    )
    return AgendaFeedRead(start=start, end=end, items=items)


@router.get("/today", response_model=AgendaTodayRead, operation_id="getAgendaToday")
async def get_agenda_today(session: SessionDep, user: StaffDep) -> AgendaTodayRead:
    """首頁、開店前檢查與導覽列數字用。"""
    return await _read(
        session,
        AgendaService(session).today_summary(user.store_id, _viewer(user), today=_today()),
    )


# ── 單次待辦 ──────────────────────────────────────────────


@router.get(
    "/tasks/undated", response_model=list[AgendaTaskRead], operation_id="listUndatedAgendaTasks"
)
async def list_undated_agenda_tasks(
    session: SessionDep,
    user: StaffDep,
    include_resolved: Annotated[bool, Query()] = False,
) -> list[AgendaTaskRead]:
    """「沒有日期」清單（預設只列還沒完成的）。"""
    return await AgendaService(session).list_undated(
        user.store_id, _viewer(user), include_resolved=include_resolved
    )


@router.post(
    "/tasks",
    response_model=AgendaTaskRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgendaTask",
)
async def create_agenda_task(
    payload: AgendaTaskCreate, session: SessionDep, user: StaffDep
) -> AgendaTaskRead:
    return await _write(
        session, AgendaService(session).create_task(user.store_id, _viewer(user), payload)
    )


@router.get("/tasks/{task_id}", response_model=AgendaTaskRead, operation_id="getAgendaTask")
async def get_agenda_task(task_id: int, session: SessionDep, user: StaffDep) -> AgendaTaskRead:
    return await _read(
        session, AgendaService(session).get_task(user.store_id, _viewer(user), task_id)
    )


@router.patch("/tasks/{task_id}", response_model=AgendaTaskRead, operation_id="updateAgendaTask")
async def update_agenda_task(
    task_id: int, payload: AgendaTaskUpdate, session: SessionDep, user: StaffDep
) -> AgendaTaskRead:
    return await _write(
        session,
        AgendaService(session).update_task(user.store_id, _viewer(user), task_id, payload),
    )


@router.delete(
    "/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteAgendaTask"
)
async def delete_agenda_task(task_id: int, session: SessionDep, user: StaffDep) -> None:
    """刪除＝封存。重複待辦的某一次不能刪，要用略過（422）。"""
    await _write(
        session, AgendaService(session).archive_task(user.store_id, _viewer(user), task_id)
    )


@router.post(
    "/tasks/{task_id}/status", response_model=AgendaTaskRead, operation_id="setAgendaTaskStatus"
)
async def set_agenda_task_status(
    task_id: int, payload: AgendaTaskStatusRequest, session: SessionDep, user: StaffDep
) -> AgendaTaskRead:
    """完成／略過／取消完成（按錯了要能取消）。"""
    return await _write(
        session,
        AgendaService(session).set_task_status(
            user.store_id, _viewer(user), task_id, payload.status
        ),
    )


# ── 重複待辦 ──────────────────────────────────────────────


@router.get(
    "/task-series", response_model=list[AgendaSeriesRead], operation_id="listAgendaTaskSeries"
)
async def list_agenda_task_series(session: SessionDep, user: StaffDep) -> list[AgendaSeriesRead]:
    return await AgendaService(session).list_series(user.store_id, _viewer(user), today=_today())


@router.post(
    "/task-series",
    response_model=AgendaSeriesRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgendaTaskSeries",
)
async def create_agenda_task_series(
    payload: AgendaSeriesCreate, session: SessionDep, user: StaffDep
) -> AgendaSeriesRead:
    return await _write(
        session,
        AgendaService(session).create_series(user.store_id, _viewer(user), payload, today=_today()),
    )


@router.get(
    "/task-series/{series_id}",
    response_model=AgendaSeriesRead,
    operation_id="getAgendaTaskSeries",
)
async def get_agenda_task_series(
    series_id: int, session: SessionDep, user: StaffDep
) -> AgendaSeriesRead:
    return await _read(
        session,
        AgendaService(session).get_series(user.store_id, _viewer(user), series_id, today=_today()),
    )


@router.patch(
    "/task-series/{series_id}",
    response_model=AgendaSeriesRead,
    operation_id="updateAgendaTaskSeries",
)
async def update_agenda_task_series(
    series_id: int, payload: AgendaSeriesUpdate, session: SessionDep, user: StaffDep
) -> AgendaSeriesRead:
    """改規則或開始日時，之前沒做的那幾次照舊留著提醒，新規則從今天起算。"""
    return await _write(
        session,
        AgendaService(session).update_series(
            user.store_id, _viewer(user), series_id, payload, today=_today()
        ),
    )


@router.delete(
    "/task-series/{series_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteAgendaTaskSeries",
)
async def delete_agenda_task_series(series_id: int, session: SessionDep, user: StaffDep) -> None:
    """之後不再出現；已完成、已略過的那幾次留作紀錄。"""
    await _write(
        session, AgendaService(session).archive_series(user.store_id, _viewer(user), series_id)
    )


@router.post(
    "/task-series/{series_id}/skip-overdue",
    response_model=AgendaSkipOverdueRead,
    operation_id="skipOverdueAgendaTaskSeries",
)
async def skip_overdue_agenda_task_series(
    series_id: int, session: SessionDep, user: StaffDep
) -> AgendaSkipOverdueRead:
    """今天以前沒做的那幾次全部略過。"""
    skipped = await _write(
        session,
        AgendaService(session).skip_overdue(
            user.store_id, _viewer(user), series_id, today=_today()
        ),
    )
    return AgendaSkipOverdueRead(skipped=skipped)


@router.post(
    "/task-series/{series_id}/occurrences/{occurrence_on}/status",
    response_model=AgendaTaskRead,
    operation_id="setAgendaOccurrenceStatus",
)
async def set_agenda_occurrence_status(
    series_id: int,
    occurrence_on: date,
    payload: AgendaTaskStatusRequest,
    session: SessionDep,
    user: StaffDep,
) -> AgendaTaskRead:
    """完成／略過重複待辦的某一次。"""
    return await _write(
        session,
        AgendaService(session).set_occurrence_status(
            user.store_id, _viewer(user), series_id, occurrence_on, payload.status
        ),
    )


@router.patch(
    "/task-series/{series_id}/occurrences/{occurrence_on}",
    response_model=AgendaTaskRead,
    operation_id="updateAgendaOccurrence",
)
async def update_agenda_occurrence(
    series_id: int,
    occurrence_on: date,
    payload: AgendaOccurrenceUpdate,
    session: SessionDep,
    user: StaffDep,
) -> AgendaTaskRead:
    """只改這一次（改期、時間、備註、負責人）。"""
    return await _write(
        session,
        AgendaService(session).update_occurrence(
            user.store_id, _viewer(user), series_id, occurrence_on, payload
        ),
    )


# ── 事件 ──────────────────────────────────────────────────


@router.get("/events", response_model=list[AgendaEventRead], operation_id="listAgendaEvents")
async def list_agenda_events(session: SessionDep, user: StaffDep) -> list[AgendaEventRead]:
    return await AgendaService(session).list_events(user.store_id, _viewer(user))


@router.post(
    "/events",
    response_model=AgendaEventRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgendaEvent",
)
async def create_agenda_event(
    payload: AgendaEventCreate, session: SessionDep, user: StaffDep
) -> AgendaEventRead:
    return await _write(
        session, AgendaService(session).create_event(user.store_id, _viewer(user), payload)
    )


@router.get("/events/{event_id}", response_model=AgendaEventRead, operation_id="getAgendaEvent")
async def get_agenda_event(event_id: int, session: SessionDep, user: StaffDep) -> AgendaEventRead:
    return await _read(
        session, AgendaService(session).get_event(user.store_id, _viewer(user), event_id)
    )


@router.patch(
    "/events/{event_id}", response_model=AgendaEventRead, operation_id="updateAgendaEvent"
)
async def update_agenda_event(
    event_id: int, payload: AgendaEventUpdate, session: SessionDep, user: StaffDep
) -> AgendaEventRead:
    return await _write(
        session,
        AgendaService(session).update_event(user.store_id, _viewer(user), event_id, payload),
    )


@router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteAgendaEvent",
)
async def delete_agenda_event(event_id: int, session: SessionDep, user: StaffDep) -> None:
    await _write(
        session, AgendaService(session).archive_event(user.store_id, _viewer(user), event_id)
    )


@router.post(
    "/events/{event_id}/occurrences",
    response_model=AgendaEventRead,
    operation_id="setAgendaEventOccurrence",
)
async def set_agenda_event_occurrence(
    event_id: int,
    payload: AgendaEventOccurrenceRequest,
    session: SessionDep,
    user: StaffDep,
) -> AgendaEventRead:
    """取消或恢復重複事件的某一次（例：這個週一照常營業）。"""
    return await _write(
        session,
        AgendaService(session).set_event_occurrence_cancelled(
            user.store_id, _viewer(user), event_id, payload.on, cancelled=payload.cancelled
        ),
    )
