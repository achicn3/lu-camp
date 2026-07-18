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

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.backup.backend import BackupBackend, SubprocessR2Backend
from app.modules.backup.service import BackupService, is_backup_due
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.shared.enums import BackupTrigger
from app.shared.exceptions import BackupAlreadyRunning, BackupError

logger = logging.getLogger(__name__)


def db_name_from_url(database_url: str) -> str:
    """從 DATABASE_URL 取出資料庫名（pg_dump 對象）。"""
    return make_url(database_url).database or "postgres"


def build_backup_backend() -> BackupBackend | None:
    """由 config 建真後端;R2/AES 口令未設定（空字串）→ 回 None（tick 不備份,改由健康度頁告警,
    非靜默失敗）。db_user 由 DATABASE_URL 取。憑證/口令來自 .env.r2,不入 DB/log。"""
    cfg = get_settings()
    if not cfg.backup_passphrase.strip() or not cfg.r2_access_key_id.strip():
        return None
    url = make_url(cfg.database_url)
    try:
        return SubprocessR2Backend(
            docker_bin=cfg.backup_docker_bin,
            db_container=cfg.backup_db_container,
            db_user=url.username or "postgres",
            local_dir=cfg.backup_local_dir,
            passphrase=cfg.backup_passphrase,
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
    if not is_backup_due(now=now, last_success=last, settings=settings):
        return False
    try:
        await BackupService(session, backend).run_backup(
            store_id, db_name=db_name, trigger=BackupTrigger.SCHEDULED, actor_user_id=None
        )
    except BackupAlreadyRunning:
        return False
    return True


async def _tick_once(
    session_factory: async_sessionmaker[AsyncSession], backend: BackupBackend, db_name: str
) -> bool:
    """一次 tick：開自有 session、判斷到期並執行、commit。任何例外只記 log 不讓迴圈掛掉。"""
    async with session_factory() as session:
        try:
            triggered = await run_due_backups(session, backend, db_name=db_name)
            await session.commit()
            return triggered
        except Exception:  # 背景任務永不因單次失敗中止;下次 tick 再試
            await session.rollback()
            logger.exception("backup scheduler tick failed")
            return False


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
