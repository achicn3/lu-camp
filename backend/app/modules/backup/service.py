"""備份業務邏輯（docs/31 §4）：狀態機（RUNNING→SUCCEEDED/FAILED）＋單一在跑守衛＋稽核，
以及到期判斷純函式（tick 用）。外部程序經注入的 BackupBackend，本層只管狀態與流程。
"""

from dataclasses import dataclass
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
# 超過此時長仍 RUNNING 視為中斷（行程死亡/斷電/OOM）→ 記 FAILED,避免單一在跑守衛永久卡住、
# 再也無法備份（本身即「假備份/卡死」風險的一種）。正常備份數秒~數十秒完成。
_STALE_RUNNING = timedelta(minutes=30)


@dataclass(frozen=True)
class BackupHealth:
    """備份健康度快照（service→router;router 再轉 BackupHealthRead schema）。"""

    enabled: bool
    interval_hours: int
    retention: int
    offpeak_hour: int
    last_success_at: datetime | None
    last_success_age_hours: float | None
    due_now: bool
    running: bool


def _aware(dt: datetime) -> datetime:
    """DB 取回的 datetime 若為 naive（無時區）視為 UTC,以便與 now(UTC) 相減不炸。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


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

    async def start_run(
        self,
        store_id: int,
        *,
        db_name: str,
        trigger: BackupTrigger,
        actor_user_id: int | None,
    ) -> BackupRun:
        """插一列 RUNNING（單一在跑守衛：已有 RUNNING → BackupAlreadyRunning），尚未做外部程序。

        供手動觸發：呼叫端可在此後 commit 並「立即」回 RUNNING 給前端輪詢,實際 dump 交背景任務
        （execute_run）續跑。並發終極防線＝backup_runs 部分唯一索引（commit 時擋）。
        逾時的 RUNNING（疑似行程中斷）先回收為 FAILED,避免永久卡住。
        """
        await self._reap_stale_running(store_id)
        if await self._repo.get_running(store_id) is not None:
            raise BackupAlreadyRunning("已有一筆備份進行中,請稍候")
        return await self._repo.add_run(
            BackupRun(
                store_id=store_id,
                trigger=trigger,
                status=BackupStatus.RUNNING,
                db_name=db_name,
                actor_user_id=actor_user_id,
            )
        )

    async def execute_run(self, run: BackupRun) -> BackupRun:
        """對一列已存在的 RUNNING 做外部程序並記終態（docs/31 §4）。run 須已綁在本 session。

        成功:記 SUCCEEDED＋file/r2_key/sha256/size,再 best-effort 修剪保留份數（修剪失敗不翻覆
        備份,仍算成功）。任一步失敗:記 FAILED＋last_error（假備份是最大風險,失敗絕不記成功）。
        兩種終態都寫 audit_log。
        """
        try:
            artifact = await self._backend.create_and_upload(db_name=run.db_name, stamp=_stamp())
        except Exception as exc:  # BackupError 或任何外部程序例外 → 如實記 FAILED
            run.status = BackupStatus.FAILED
            run.last_error = str(exc)[:_ERR_MAX]
            run.finished_at = datetime.now(UTC)
            await self._session.flush()
            await self._audit(run.store_id, run, run.actor_user_id, ok=False)
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
            keep = await self._retention(run.store_id)
            await self._backend.prune(db_name=run.db_name, keep=keep)
        except Exception as exc:  # 修剪不得翻覆已成功的備份
            run.last_error = f"備份成功,但修剪舊檔失敗:{str(exc)[:200]}"
            await self._session.flush()
        await self._audit(run.store_id, run, run.actor_user_id, ok=True)
        return run

    async def run_backup(
        self,
        store_id: int,
        *,
        db_name: str,
        trigger: BackupTrigger,
        actor_user_id: int | None,
    ) -> BackupRun:
        """執行一次備份並記狀態（插 RUNNING → 外部程序 → 終態）。排程 tick／測試用（同 session）。

        已有 RUNNING → BackupAlreadyRunning（單一在跑守衛）。手動端改用 start_run＋背景執行。
        """
        run = await self.start_run(
            store_id, db_name=db_name, trigger=trigger, actor_user_id=actor_user_id
        )
        return await self.execute_run(run)

    async def get_health(self, store_id: int, *, now: datetime | None = None) -> "BackupHealth":
        """健康度快照（docs/31 §5）：啟用/間隔/保留/離峰、上次成功與落後時數、是否到期/在跑。"""
        from app.modules.settings.service import StoreSettingsService

        now = now or datetime.now(UTC)
        settings = await StoreSettingsService(self._session).get_effective_settings(store_id)
        last = await self._repo.last_success_at(store_id)
        running = await self._repo.get_running(store_id) is not None
        age_hours = None if last is None else (now - _aware(last)).total_seconds() / 3600.0
        return BackupHealth(
            enabled=settings.backup_enabled,
            interval_hours=settings.backup_interval_hours,
            retention=settings.backup_retention,
            offpeak_hour=settings.backup_offpeak_hour,
            last_success_at=last,
            last_success_age_hours=age_hours,
            due_now=is_backup_due(now=now, last_success=last, settings=settings),
            running=running,
        )

    async def get_run(self, store_id: int, run_id: int) -> BackupRun | None:
        return await self._repo.get_run(store_id, run_id)

    async def list_runs(self, store_id: int, *, limit: int = 30) -> list[BackupRun]:
        return await self._repo.list_runs(store_id, limit=limit)

    async def last_success_at(self, store_id: int) -> datetime | None:
        return await self._repo.last_success_at(store_id)

    async def _reap_stale_running(self, store_id: int) -> None:
        """逾時仍 RUNNING（行程中斷/斷電）→ 記 FAILED,釋放單一在跑守衛。正常備份數秒~數十秒。"""
        running = await self._repo.get_running(store_id)
        if running is None:
            return
        if datetime.now(UTC) - _aware(running.started_at) <= _STALE_RUNNING:
            return
        running.status = BackupStatus.FAILED
        running.last_error = "備份逾時未完成（疑似行程中斷/斷電）,標記為失敗"
        running.finished_at = datetime.now(UTC)
        await self._session.flush()
        await self._audit(store_id, running, running.actor_user_id, ok=False)

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
