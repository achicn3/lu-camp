"""backup 路由（docs/31 §5，MANAGER）：健康度、清單、手動觸發。

手動觸發＝插 RUNNING＋commit 後「立即」回應,實際 dump 交背景任務;前端輪詢 /backup/runs 取狀態。
R2/口令未設定 → 手動觸發回 503（不靜默假成功）。設定（間隔/保留/離峰/啟用）沿用既有 /settings。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings as get_app_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.backup.backend import BackupArtifact, BackupBackend
from app.modules.backup.scheduler import (
    build_backup_backend,
    db_name_from_url,
    launch_manual_backup,
)
from app.modules.backup.schemas import BackupHealthRead, BackupRunRead
from app.modules.backup.service import BackupService
from app.shared.enums import BackupTrigger, UserRole
from app.shared.exceptions import BackupAlreadyRunning

router = APIRouter(prefix="/backup", tags=["backup"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]


@router.get("/health", response_model=BackupHealthRead, operation_id="getBackupHealth")
async def get_backup_health(session: SessionDep, user: ManagerDep) -> BackupHealthRead:
    health = await BackupService(session, _noop_backend()).get_health(user.store_id)
    return BackupHealthRead.model_validate(health)


@router.get("/runs", response_model=list[BackupRunRead], operation_id="listBackupRuns")
async def list_backup_runs(
    session: SessionDep,
    user: ManagerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[BackupRunRead]:
    runs = await BackupService(session, _noop_backend()).list_runs(user.store_id, limit=limit)
    return [BackupRunRead.model_validate(r) for r in runs]


@router.post(
    "/runs",
    response_model=BackupRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="triggerBackup",
)
async def trigger_backup(session: SessionDep, user: ManagerDep) -> BackupRunRead:
    """立即備份（手動）：插 RUNNING＋commit 後回應,背景續跑實際 dump。R2 未設定 → 503。"""
    backend = build_backup_backend()
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="備份未設定（R2 憑證/AES 口令未提供,見 .env.r2）,無法手動備份",
        )
    db_name = db_name_from_url(get_app_settings().database_url)
    try:
        run = await BackupService(session, backend).start_run(
            user.store_id, db_name=db_name, trigger=BackupTrigger.MANUAL, actor_user_id=user.id
        )
    except BackupAlreadyRunning as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    launch_manual_backup(run.id, user.store_id)  # 背景執行實際 dump;前端輪詢狀態
    return BackupRunRead.model_validate(run)


class _NoopBackend(BackupBackend):
    """讀取端（健康度/清單）不需真後端;給 BackupService 一個不會被呼叫的占位替身。"""

    async def create_and_upload(self, *, db_name: str, stamp: str) -> BackupArtifact:
        raise RuntimeError("read-only backup endpoint must not run backup")

    async def prune(self, *, db_name: str, keep: int) -> None:
        raise RuntimeError("read-only backup endpoint must not prune")


def _noop_backend() -> BackupBackend:
    return _NoopBackend()
