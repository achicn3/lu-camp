"""backup 路由（docs/31 §5，MANAGER）：健康度、清單、手動觸發。

手動觸發＝插 RUNNING＋commit 後「立即」回應,實際 dump 交背景任務;前端輪詢 /backup/runs 取狀態。
R2/口令未設定 → 手動觸發回 503（不靜默假成功）。設定（間隔/保留/離峰/啟用）沿用既有 /settings。
"""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings as get_app_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.backup.backend import BackupArtifact, BackupBackend
from app.modules.backup.repository import BackupRepository
from app.modules.backup.restore import RestoreBackend, RestoreVerifier, VerificationResult
from app.modules.backup.restore_service import RestoreService, default_restore_db_name
from app.modules.backup.scheduler import (
    build_backup_backend,
    build_restore_backend,
    db_name_from_url,
    launch_manual_backup,
    launch_restore,
)
from app.modules.backup.schemas import (
    BackupHealthRead,
    BackupRunRead,
    RestoreRunRead,
    RestoreTriggerRequest,
)
from app.modules.backup.service import BackupService
from app.shared.enums import BackupTrigger, UserRole
from app.shared.exceptions import BackupAlreadyRunning, RestoreAlreadyRunning

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


@router.get(
    "/restores", response_model=list[RestoreRunRead], operation_id="listRestoreRuns"
)
async def list_restore_runs(
    session: SessionDep,
    user: ManagerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[RestoreRunRead]:
    svc = RestoreService(session, _noop_restore_backend(), _noop_restore_verifier())
    runs = await svc.list_restores(user.store_id, limit=limit)
    return [RestoreRunRead.model_validate(r) for r in runs]


@router.post(
    "/restore",
    response_model=RestoreRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="triggerRestore",
)
async def trigger_restore(
    payload: RestoreTriggerRequest, session: SessionDep, user: ManagerDep
) -> RestoreRunRead:
    """觸發還原到 throwaway 庫＋四驗（高危,強卡控）。正式庫不受影響;VERIFIED 後切換另跑受控腳本。

    卡控：①MANAGER（require_role）②知情勾選 acknowledge ③打字確認 confirm_text＝該備份「檔名」。
    """
    if not payload.acknowledge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="請先勾選知情同意（還原到獨立驗證庫、不影響現行資料）",
        )
    expected = os.path.basename(payload.source_r2_key)
    if payload.confirm_text.strip() != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"確認字串不符：請輸入該備份檔名「{expected}」",
        )
    # 綁定到目錄：只能還原本店「已成功」的備份（擋任意/他環境 r2_key）。
    source = await BackupRepository(session).get_succeeded_by_r2_key(
        user.store_id, payload.source_r2_key
    )
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到對應的已成功備份（只能還原本店備份紀錄中的檔案）",
        )
    backend = build_restore_backend()
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="還原未設定（R2 憑證/AES 口令未提供,見 .env.r2）",
        )
    svc = RestoreService(session, backend, _noop_restore_verifier())
    try:
        run = await svc.start_restore(
            user.store_id,
            source_r2_key=payload.source_r2_key,
            actor_user_id=user.id,
            restore_db_name=default_restore_db_name(),
        )
    except RestoreAlreadyRunning as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    launch_restore(run.id, user.store_id)  # 背景執行還原＋四驗;前端輪詢
    return RestoreRunRead.model_validate(run)


class _NoopBackend(BackupBackend):
    """讀取端（健康度/清單）不需真後端;給 BackupService 一個不會被呼叫的占位替身。"""

    async def create_and_upload(self, *, db_name: str, stamp: str) -> BackupArtifact:
        raise RuntimeError("read-only backup endpoint must not run backup")

    async def prune(self, *, db_name: str, keep_keys: set[str]) -> None:
        raise RuntimeError("read-only backup endpoint must not prune")


def _noop_backend() -> BackupBackend:
    return _NoopBackend()


class _NoopRestoreBackend:
    """讀取端（清單）不需真後端;不會被呼叫的占位替身。"""

    async def fetch_and_restore(
        self, *, r2_key: str, target_db: str, expected_sha256: str, expected_size: int
    ) -> None:
        raise RuntimeError("read-only restore endpoint must not restore")

    async def drop_database(self, *, target_db: str) -> None:
        raise RuntimeError("read-only restore endpoint must not drop databases")


class _NoopRestoreVerifier:
    async def verify(
        self, *, target_db: str, expected_manifest: dict[str, int] | None = None
    ) -> list[VerificationResult]:
        raise RuntimeError("read-only restore endpoint must not verify")


def _noop_restore_backend() -> RestoreBackend:
    return _NoopRestoreBackend()


def _noop_restore_verifier() -> RestoreVerifier:
    return _NoopRestoreVerifier()
