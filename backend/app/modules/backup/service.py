"""備份業務邏輯（docs/31 §4）：狀態機（RUNNING→SUCCEEDED/FAILED）＋單一在跑守衛＋稽核，
以及到期判斷純函式（tick 用）。外部程序經注入的 BackupBackend，本層只管狀態與流程。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.backup.backend import BackupBackend
from app.modules.backup.models import BackupRun
from app.modules.backup.repository import BackupRepository
from app.modules.settings.models import StoreSettings
from app.shared.enums import BackupStatus, BackupTrigger
from app.shared.exceptions import BackupAlreadyRunning

_ERR_MAX = 2000  # last_error 上限（避免超長堆疊塞爆欄位）


def is_backup_due(
    *, now: datetime, last_success: datetime | None, settings: StoreSettings
) -> bool:
    """到期判斷（docs/31 §3）：與 session/登入/開關機無關,只看「距上次成功多久」＋離峰時點。

    未啟用→永不到期(手動仍可)。從未成功過→立即到期(首次備份)。否則:距上次成功 ≥ 間隔,
    **且**已過今日離峰鐘點(now.hour ≥ offpeak),或已落後超過 1.5×間隔則強制補(避免離峰窗一直錯過)。
    """
    if not settings.backup_enabled:
        return False
    interval = timedelta(hours=settings.backup_interval_hours)
    if last_success is None:
        return True
    elapsed = now - last_success
    if elapsed < interval:
        return False
    # 到期了:優先落在離峰(過了離峰鐘點才跑);但落後太久(>1.5×間隔)則不再等離峰、直接補。
    if now.hour >= settings.backup_offpeak_hour:
        return True
    return elapsed >= interval * 1.5


class BackupService:
    def __init__(self, session: AsyncSession, backend: BackupBackend) -> None:
        self._session = session
        self._repo = BackupRepository(session)
        self._backend = backend

    async def run_backup(
        self,
        store_id: int,
        *,
        db_name: str,
        trigger: BackupTrigger,
        actor_user_id: int | None,
    ) -> BackupRun:
        """執行一次備份並記狀態（docs/31 §4）。已有 RUNNING → BackupAlreadyRunning（單一在跑守衛）。

        成功:記 SUCCEEDED＋file/r2_key/sha256/size,再 best-effort 修剪保留份數（修剪失敗不翻覆
        備份,仍算成功）。任一步失敗:記 FAILED＋last_error（假備份是最大風險,失敗絕不記成功）。
        兩種終態都寫 audit_log。並發終極防線＝backup_runs 部分唯一索引(commit 時擋)。
        """
        if await self._repo.get_running(store_id) is not None:
            raise BackupAlreadyRunning("已有一筆備份進行中,請稍候")
        run = await self._repo.add_run(
            BackupRun(
                store_id=store_id,
                trigger=trigger,
                status=BackupStatus.RUNNING,
                db_name=db_name,
                actor_user_id=actor_user_id,
            )
        )
        try:
            artifact = await self._backend.create_and_upload(db_name=db_name, stamp=_stamp())
        except Exception as exc:  # BackupError 或任何外部程序例外 → 如實記 FAILED
            run.status = BackupStatus.FAILED
            run.last_error = str(exc)[:_ERR_MAX]
            run.finished_at = datetime.now(UTC)
            await self._session.flush()
            await self._audit(store_id, run, actor_user_id, ok=False)
            return run
        run.status = BackupStatus.SUCCEEDED
        run.file_name = artifact.file_name
        run.r2_key = artifact.r2_key
        run.sha256 = artifact.sha256
        run.size_bytes = artifact.size_bytes
        run.finished_at = datetime.now(UTC)
        await self._session.flush()
        # 修剪保留份數（best-effort;失敗只記 last_error 提示,不改備份成功狀態）。
        try:
            keep = await self._retention(store_id)
            await self._backend.prune(db_name=db_name, keep=keep)
        except Exception as exc:  # 修剪不得翻覆已成功的備份
            run.last_error = f"備份成功,但修剪舊檔失敗:{str(exc)[:200]}"
            await self._session.flush()
        await self._audit(store_id, run, actor_user_id, ok=True)
        return run

    async def list_runs(self, store_id: int, *, limit: int = 30) -> list[BackupRun]:
        return await self._repo.list_runs(store_id, limit=limit)

    async def last_success_at(self, store_id: int) -> datetime | None:
        return await self._repo.last_success_at(store_id)

    async def _retention(self, store_id: int) -> int:
        from app.modules.settings.service import StoreSettingsService

        settings = await StoreSettingsService(self._session).get_effective_settings(store_id)
        return settings.backup_retention

    async def _audit(
        self, store_id: int, run: BackupRun, actor_user_id: int | None, *, ok: bool
    ) -> None:
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="BACKUP_RUN",
            entity_type="backup_run",
            entity_id=str(run.id),
            before={"status": BackupStatus.RUNNING.value},
            after={
                "status": run.status.value,
                "trigger": run.trigger.value,
                "r2_key": run.r2_key,
                "sha256": run.sha256,
                "size_bytes": run.size_bytes,
                "ok": ok,
            },
        )


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
