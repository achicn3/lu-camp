"""備份排程 tick（docs/31 §3）：lifespan 內的輕量背景任務,到期驅動、與登入/session/開關機無關。

整庫 pg_dump 天然涵蓋所有分店（共用同一個 Postgres DB）,故一次備份即全體;記於**主店**（最小
store_id）名下、以其 settings 與上次成功時間判斷到期。單一在跑守衛由 BackupService＋部分唯一索引擋。

觸發時機:每 backup_tick_seconds 醒一次做 is_backup_due 判斷（不固定鐘點、不靠 cron）。24/7 不關機
→ 於離峰醒來發現到期即備份;晚上關機 → 開機後 tick 補跑。後端沒起來 → 不備份,健康度頁告警。
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.backup.backend import BackupBackend, SubprocessR2Backend
from app.modules.backup.models import BackupRun, RestoreRun
from app.modules.backup.restore import (
    RestoreBackend,
    RestoreVerifier,
    SqlRestoreVerifier,
    SubprocessR2RestoreBackend,
)
from app.modules.backup.restore_service import RestoreService
from app.modules.backup.service import BackupService, backup_tz, is_backup_due
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.shared.enums import BackupStatus, BackupTrigger, RestoreStatus
from app.shared.exceptions import BackupAlreadyRunning, BackupError, RestoreError

logger = logging.getLogger(__name__)

# 保留背景任務參照,避免被 GC 提前回收（asyncio 只持弱參照）。
_background_tasks: set[asyncio.Task[None]] = set()


def db_name_from_url(database_url: str) -> str:
    """從 DATABASE_URL 取出資料庫名（pg_dump 對象）。"""
    return make_url(database_url).database or "postgres"


def build_backup_backend() -> BackupBackend | None:
    """由 config 建真後端;R2/AES 口令未設定（空字串）→ 回 None（tick 不備份,改由健康度頁告警,
    非靜默失敗）。db_user 由 DATABASE_URL 取。憑證/口令來自 .env.r2,不入 DB/log。"""
    cfg = get_settings()
    if not cfg.r2_backup_passphrase.strip() or not cfg.r2_access_key_id.strip():
        return None
    url = make_url(cfg.database_url)
    try:
        return SubprocessR2Backend(
            docker_bin=cfg.backup_docker_bin,
            db_container=cfg.backup_db_container,
            db_user=url.username or "postgres",
            local_dir=cfg.backup_local_dir,
            passphrase=cfg.r2_backup_passphrase,
            r2_endpoint=cfg.r2_endpoint,
            r2_access_key_id=cfg.r2_access_key_id,
            r2_secret_access_key=cfg.r2_secret_access_key,
            r2_bucket=cfg.r2_bucket,
        )
    except BackupError:
        return None


async def _primary_store_id(session: AsyncSession) -> int | None:
    """主店＝最小 store_id（整庫備份的帳面擁有者;多店共用一個 DB,一次 dump 即全體）。"""
    return await session.scalar(select(func.min(Store.id)))


async def run_due_backups(
    session: AsyncSession,
    backend: BackupBackend,
    *,
    db_name: str,
    now: datetime | None = None,
) -> bool:
    """到期則跑一次整庫備份（記於主店名下）,回傳是否有觸發。

    背景流程,不在請求脈絡:呼叫端（tick loop）負責 commit。撞單一在跑守衛（BackupAlreadyRunning）
    ＝有另一次備份進行中,跳過。run_backup 內部已把 dump/上傳失敗如實記 FAILED,不外拋。
    """
    now = now or datetime.now(UTC)
    store_id = await _primary_store_id(session)
    if store_id is None:
        return False
    settings = await StoreSettingsService(session).get_effective_settings(store_id)
    last = await BackupService(session, backend).last_success_at(store_id)
    if not is_backup_due(now=now, last_success=last, settings=settings, tz=backup_tz()):
        return False
    try:
        run = await BackupService(session, backend).run_backup(
            store_id, db_name=db_name, trigger=BackupTrigger.SCHEDULED, actor_user_id=None
        )
    except BackupAlreadyRunning:
        return False
    # 回傳「這次確實成功」而非只是「有嘗試」:否則 FAILED 也會觸發不可逆的修剪（Codex 第三輪 #1）。
    return run.status is BackupStatus.SUCCEEDED


async def _tick_once(
    session_factory: async_sessionmaker[AsyncSession], backend: BackupBackend, db_name: str
) -> bool:
    """一次 tick：開自有 session、判斷到期並執行、commit。任何例外只記 log 不讓迴圈掛掉。"""
    async with session_factory() as session:
        try:
            triggered = await run_due_backups(session, backend, db_name=db_name)
            await session.commit()
        except Exception:  # 背景任務永不因單次失敗中止;下次 tick 再試
            await session.rollback()
            logger.exception("backup scheduler tick failed")
            return False
        if triggered:  # SUCCEEDED 已 commit → 才做不可逆的修剪（Codex #1）
            try:
                await BackupService(session, backend).prune_old(db_name)
            except Exception:
                logger.warning("backup prune after scheduled backup failed", exc_info=True)
        return triggered


async def _run_manual_backup(run_id: int, store_id: int) -> None:
    """對已建的 RUNNING 列（手動觸發）於背景做外部程序並記終態,用自有 session＋commit。

    端點已 commit RUNNING 並立即回應;此處續跑實際 dump（可能數十秒),前端輪詢 /backup/runs 取狀態。
    """
    backend = build_backup_backend()
    if backend is None:  # 理論上端點已擋（未設定→503）;防禦性再確認
        logger.error("manual backup launched but R2 not configured, run_id=%s", run_id)
        return
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            svc = BackupService(session, backend)
            run = await svc.get_run(store_id, run_id)
            if run is None or run.status is not BackupStatus.RUNNING:
                return  # 已被別處處理（例如逾時回收）
            result = await svc.execute_run(run)
            db_name = result.db_name
            succeeded = result.status is BackupStatus.SUCCEEDED
            await session.commit()
        except Exception:  # execute_run 內部已把 dump 失敗記 FAILED;此處防 commit/session 例外
            await session.rollback()
            logger.exception("manual backup execution failed run_id=%s", run_id)
            return
        if succeeded:  # SUCCEEDED 已 commit → 才做不可逆的修剪（Codex #1）
            try:
                await BackupService(session, backend).prune_old(db_name)
            except Exception:
                logger.warning("backup prune after manual backup failed", exc_info=True)


def launch_manual_backup(run_id: int, store_id: int) -> None:
    """啟動背景手動備份任務並保留參照（fire-and-forget,失敗記 log 不影響請求）。"""
    task = asyncio.create_task(
        _run_manual_backup(run_id, store_id), name=f"manual-backup-{run_id}"
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def build_restore_backend() -> RestoreBackend | None:
    """由 config 建真還原後端;R2/口令未設定 → 回 None（端點回 503）。"""
    cfg = get_settings()
    if not cfg.r2_backup_passphrase.strip() or not cfg.r2_access_key_id.strip():
        return None
    url = make_url(cfg.database_url)
    try:
        return SubprocessR2RestoreBackend(
            docker_bin=cfg.backup_docker_bin,
            db_container=cfg.backup_db_container,
            db_user=url.username or "postgres",
            local_dir=cfg.backup_local_dir,
            passphrase=cfg.r2_backup_passphrase,
            r2_endpoint=cfg.r2_endpoint,
            r2_access_key_id=cfg.r2_access_key_id,
            r2_secret_access_key=cfg.r2_secret_access_key,
            r2_bucket=cfg.r2_bucket,
        )
    except RestoreError:
        return None


def build_restore_verifier() -> RestoreVerifier:
    """四驗器：連還原後的 throwaway 庫（base_url＝正式 DATABASE_URL,只換 db 名）。"""
    return SqlRestoreVerifier(base_url=get_settings().database_url)


async def _run_restore(restore_id: int, store_id: int) -> None:
    """背景執行還原到 throwaway 庫＋四驗並記終態（自有 session＋commit）。正式庫全程未被觸碰。"""
    backend = build_restore_backend()
    if backend is None:
        logger.error("restore launched but R2 not configured, restore_id=%s", restore_id)
        return
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            svc = RestoreService(session, backend, build_restore_verifier())
            run = await svc.get_restore(store_id, restore_id)
            if run is None or run.status is not RestoreStatus.RUNNING:
                return
            await svc.reap_old_restores(store_id, keep_run_id=restore_id)  # 先回收舊 throwaway 庫
            await svc.execute_restore(run)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("restore execution failed restore_id=%s", restore_id)
            await _terminalize_restore_failed(restore_id, store_id, backend)


async def _terminalize_restore_failed(
    restore_id: int, store_id: int, backend: RestoreBackend
) -> None:
    """worker 未預期失敗 → 用新 session 把還原轉 FAILED＋drop 庫,不讓它永遠 RUNNING（Codex #5）。"""
    try:
        async with get_sessionmaker()() as session:
            svc = RestoreService(session, backend, build_restore_verifier())
            if await svc.terminalize_failed(store_id, restore_id, "還原工作未預期中斷,標記為失敗"):
                await session.commit()
    except Exception:
        logger.exception("failed to terminalize restore restore_id=%s", restore_id)


def launch_restore(restore_id: int, store_id: int) -> None:
    """啟動背景還原任務並保留參照（fire-and-forget）。"""
    task = asyncio.create_task(_run_restore(restore_id, store_id), name=f"restore-{restore_id}")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def reconcile_orphaned_jobs(session: AsyncSession) -> tuple[int, int]:
    """把上次行程遺留的 RUNNING 備份/還原標記 FAILED（Codex #4）。呼叫端負責 commit。

    背景備份/還原是行程內任務;崩潰或部署會讓 RUNNING 列孤兒化、UI 永遠輪詢。開機無任何進行中
    工作,故把所有 RUNNING 視為中斷 → FAILED，讓使用者可重試、UI 停止空轉。回傳 (備份數, 還原數)。
    """
    now = datetime.now(UTC)
    b = await session.execute(
        update(BackupRun)
        .where(BackupRun.status == BackupStatus.RUNNING)
        .values(
            status=BackupStatus.FAILED,
            finished_at=now,
            last_error="重啟時偵測到中斷的備份,標記為失敗",
        )
    )
    r = await session.execute(
        update(RestoreRun)
        .where(RestoreRun.status == RestoreStatus.RUNNING)
        .values(
            status=RestoreStatus.FAILED,
            finished_at=now,
            last_error="重啟時偵測到中斷的還原,標記為失敗",
        )
    )
    await session.flush()
    nb = int(getattr(b, "rowcount", 0) or 0)
    nr = int(getattr(r, "rowcount", 0) or 0)
    if nb or nr:
        logger.info("reconciled orphaned jobs on startup: backups=%s restores=%s", nb, nr)
    return nb, nr


async def reconcile_orphaned_jobs_on_startup() -> tuple[int, int]:
    """啟動 hook：自有 session＋commit 跑一次孤兒回收。"""
    factory = get_sessionmaker()
    async with factory() as session:
        result = await reconcile_orphaned_jobs(session)
        await session.commit()
        return result


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    """背景排程主迴圈:每 backup_tick_seconds 醒一次判斷到期。stop_event 一設即優雅結束。"""
    cfg = get_settings()
    if not cfg.backup_scheduler_enabled:
        logger.info("backup scheduler disabled (backup_scheduler_enabled=false)")
        return
    factory = get_sessionmaker()
    db_name = db_name_from_url(cfg.database_url)
    while not stop_event.is_set():
        backend = build_backup_backend()
        if backend is not None:
            await _tick_once(factory, backend, db_name)
        else:
            # R2 未設定:不備份（健康度頁會顯示落後告警,而非靜默假成功）
            logger.debug("backup scheduler: R2 not configured, skipping tick")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=cfg.backup_tick_seconds)
