"""備份服務狀態機 + 到期判斷（docs/31 §3/§4）。外部程序以假 BackupBackend 替身,不真的 dump/上傳。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.backup.backend import BackupArtifact, BackupBackend
from app.modules.backup.models import BackupRun
from app.modules.backup.service import BackupService, is_backup_due
from app.modules.settings.service import _new_settings
from app.modules.store.models import Store
from app.shared.enums import BackupStatus, BackupTrigger
from app.shared.exceptions import BackupAlreadyRunning, BackupError


class FakeBackend(BackupBackend):
    """假後端:可設定成功(回 artifact)或失敗(raise)。記錄修剪呼叫。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.create_calls = 0
        self.prune_calls: list[int] = []

    async def create_and_upload(self, *, db_name: str, stamp: str) -> BackupArtifact:
        self.create_calls += 1
        if self.fail:
            raise BackupError("dump 失敗(測試)")
        return BackupArtifact(
            file_name=f"{db_name}_{stamp}.dump.enc",
            r2_key=f"backups/{db_name}_{stamp}.dump.enc",
            sha256="a" * 64,
            size_bytes=12345,
        )

    async def prune(self, *, db_name: str, keep: int) -> None:
        self.prune_calls.append(keep)


async def _store(session: AsyncSession) -> int:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    return store.id


@pytest.mark.asyncio
async def test_run_backup_success_records_succeeded_and_prunes(db_session: AsyncSession) -> None:
    store_id = await _store(db_session)
    backend = FakeBackend()
    run = await BackupService(db_session, backend).run_backup(
        store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
    )
    assert run.status == BackupStatus.SUCCEEDED
    assert run.r2_key and run.sha256 == "a" * 64 and run.size_bytes == 12345
    assert run.finished_at is not None
    assert backend.create_calls == 1
    assert backend.prune_calls == [30]  # 預設保留 30 份


@pytest.mark.asyncio
async def test_run_backup_failure_records_failed_not_success(db_session: AsyncSession) -> None:
    # 假備份是最大風險:dump 失敗 → 記 FAILED + last_error,絕不記成功。
    store_id = await _store(db_session)
    backend = FakeBackend(fail=True)
    run = await BackupService(db_session, backend).run_backup(
        store_id, db_name="lucamp", trigger=BackupTrigger.SCHEDULED, actor_user_id=None
    )
    assert run.status == BackupStatus.FAILED
    assert run.last_error and "失敗" in run.last_error
    assert run.r2_key is None and run.finished_at is not None
    assert backend.prune_calls == []  # 失敗不修剪


@pytest.mark.asyncio
async def test_single_run_guard(db_session: AsyncSession) -> None:
    store_id = await _store(db_session)
    # 先造一筆 RUNNING(模擬另一次備份進行中)
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.SCHEDULED,
            status=BackupStatus.RUNNING,
            db_name="lucamp",
        )
    )
    await db_session.flush()
    backend = FakeBackend()
    with pytest.raises(BackupAlreadyRunning):
        await BackupService(db_session, backend).run_backup(
            store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
        )
    assert backend.create_calls == 0  # 守衛擋下,未動外部程序


def test_is_backup_due() -> None:
    s = _new_settings(1)
    s.backup_enabled = True
    s.backup_interval_hours = 24
    s.backup_offpeak_hour = 4
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)  # 05:00,已過離峰 04:00

    def due(now_: datetime, ls: datetime | None) -> bool:
        return is_backup_due(now=now_, last_success=ls, settings=s, tz=UTC)

    # 未啟用 → 永不到期
    s.backup_enabled = False
    assert due(now, None) is False
    s.backup_enabled = True
    # 從未成功過 → 立即到期(首次)
    assert due(now, None) is True
    # 距上次 <24h → 未到期
    assert due(now, now - timedelta(hours=10)) is False
    # 距上次 ≥24h 且已過離峰 → 到期
    assert due(now, now - timedelta(hours=25)) is True
    # 到期但未過離峰(03:00 < 04:00)且落後未達 1.5×間隔 → 先不跑(等離峰)
    early = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    assert due(early, early - timedelta(hours=25)) is False
    # 到期、未過離峰、但落後 >1.5×間隔(36h) → 強制補(不再等離峰)
    assert due(early, early - timedelta(hours=40)) is True


def test_is_backup_due_uses_local_timezone() -> None:
    # Codex #6：離峰以「當地時區」判定,不可拿 UTC 的 hour 直接比。offpeak=21（台灣打烊後）。
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Taipei")  # UTC+8
    s = _new_settings(1)
    s.backup_enabled = True
    s.backup_interval_hours = 24
    s.backup_offpeak_hour = 21
    last = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)  # 已到期,但兩情境都 <1.5×間隔(36h)
    # 13:00 UTC = 21:00 台灣（elapsed 25h）→ 已過當地離峰 → 到期
    at_2100_local = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)
    assert is_backup_due(now=at_2100_local, last_success=last, settings=s, tz=tz) is True
    # 21:00 UTC = 隔日 05:00 台灣（當地 hour 5 < 21，elapsed 33h < 36h）→ 不跑
    #（舊 bug 會誤用 UTC hour 21 ≥ 21 而在台灣清晨 05:00 觸發）
    at_0500_local = datetime(2026, 7, 18, 21, 0, tzinfo=UTC)
    assert is_backup_due(now=at_0500_local, last_success=last, settings=s, tz=tz) is False


@pytest.mark.asyncio
async def test_run_backup_writes_audit(db_session: AsyncSession) -> None:
    from app.core.audit import AuditLog

    store_id = await _store(db_session)
    await BackupService(db_session, FakeBackend()).run_backup(
        store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
    )
    n = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "BACKUP_RUN")
    )
    assert n == 1


@pytest.mark.asyncio
async def test_reconcile_orphaned_jobs_marks_running_failed(db_session: AsyncSession) -> None:
    # Codex #4：重啟時把上次遺留的 RUNNING 備份/還原標記 FAILED（不讓 UI 永遠輪詢）。
    from app.modules.backup.models import RestoreRun
    from app.modules.backup.scheduler import reconcile_orphaned_jobs
    from app.modules.user.models import User
    from app.shared.enums import RestoreStatus, UserRole

    store_id = await _store(db_session)
    user = User(store_id=store_id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.MANUAL,
            status=BackupStatus.RUNNING,
            db_name="lucamp",
        )
    )
    db_session.add(
        RestoreRun(
            store_id=store_id,
            status=RestoreStatus.RUNNING,
            source_r2_key="backups/x.dump.enc",
            restore_db_name="lucamp_restore_x",
            actor_user_id=user.id,
        )
    )
    await db_session.flush()
    nb, nr = await reconcile_orphaned_jobs(db_session)
    assert nb == 1 and nr == 1
    b = await db_session.scalar(select(BackupRun).where(BackupRun.store_id == store_id))
    r = await db_session.scalar(select(RestoreRun).where(RestoreRun.store_id == store_id))
    assert b is not None and b.status == BackupStatus.FAILED and b.finished_at is not None
    assert r is not None and r.status == RestoreStatus.FAILED and r.finished_at is not None


# --- 排程 tick（docs/31 §3）：run_due_backups 到期驅動、記於主店名下 -----------------


@pytest.mark.asyncio
async def test_run_due_backups_triggers_when_due(db_session: AsyncSession) -> None:
    # 從未成功過 → 到期 → 觸發一次 SCHEDULED 備份,記於主店（最小 store_id）名下。
    from app.modules.backup.scheduler import run_due_backups

    store_id = await _store(db_session)
    backend = FakeBackend()
    triggered = await run_due_backups(db_session, backend, db_name="lucamp")
    assert triggered is True
    assert backend.create_calls == 1
    run = await db_session.scalar(select(BackupRun).where(BackupRun.store_id == store_id))
    assert run is not None
    assert run.status == BackupStatus.SUCCEEDED
    assert run.trigger == BackupTrigger.SCHEDULED
    assert run.actor_user_id is None  # 排程無操作者


@pytest.mark.asyncio
async def test_run_due_backups_skips_when_not_due(db_session: AsyncSession) -> None:
    # 剛成功過（10 分鐘前）→ 未到期 → 不觸發。
    from app.modules.backup.scheduler import run_due_backups

    store_id = await _store(db_session)
    recent = datetime.now(UTC) - timedelta(minutes=10)
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.SCHEDULED,
            status=BackupStatus.SUCCEEDED,
            db_name="lucamp",
            finished_at=recent,
        )
    )
    await db_session.flush()
    backend = FakeBackend()
    triggered = await run_due_backups(db_session, backend, db_name="lucamp")
    assert triggered is False
    assert backend.create_calls == 0


@pytest.mark.asyncio
async def test_run_due_backups_skips_when_disabled(db_session: AsyncSession) -> None:
    # backup_enabled=false → 到期判斷永遠 False → tick 不備份（手動仍可）。
    from app.modules.backup.scheduler import run_due_backups
    from app.modules.settings.service import _new_settings

    store_id = await _store(db_session)
    settings = _new_settings(store_id)  # 持久化一列並停用備份（get_effective_settings 才會讀到）
    settings.backup_enabled = False
    db_session.add(settings)
    await db_session.flush()
    backend = FakeBackend()
    triggered = await run_due_backups(db_session, backend, db_name="lucamp")
    assert triggered is False
    assert backend.create_calls == 0


@pytest.mark.asyncio
async def test_run_due_backups_no_store_noop(db_session: AsyncSession) -> None:
    # 尚無任何店（極早期）→ 無主店 → 安全跳過,不炸。
    from app.modules.backup.scheduler import run_due_backups

    backend = FakeBackend()
    triggered = await run_due_backups(db_session, backend, db_name="lucamp")
    assert triggered is False
    assert backend.create_calls == 0


@pytest.mark.asyncio
async def test_run_due_backups_respects_single_run_guard(db_session: AsyncSession) -> None:
    # 已有 RUNNING（另一次進行中）→ tick 跳過,不重複觸發。
    from app.modules.backup.scheduler import run_due_backups

    store_id = await _store(db_session)
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.MANUAL,
            status=BackupStatus.RUNNING,
            db_name="lucamp",
        )
    )
    await db_session.flush()
    backend = FakeBackend()
    triggered = await run_due_backups(db_session, backend, db_name="lucamp")
    assert triggered is False
    assert backend.create_calls == 0


def test_build_backup_backend_none_when_unconfigured() -> None:
    # R2/口令未設定（測試環境 .env 無 R2_* / BACKUP_PASSPHRASE）→ 回 None（tick 不備份,不炸）。
    from app.modules.backup.scheduler import build_backup_backend, db_name_from_url

    assert build_backup_backend() is None
    assert db_name_from_url("postgresql+asyncpg://u:p@h:1/lucamp") == "lucamp"


# --- 手動兩階段（start_run→execute_run）＋逾時回收＋健康度（docs/31 §4/§5）--------------


@pytest.mark.asyncio
async def test_start_then_execute_two_phase(db_session: AsyncSession) -> None:
    store_id = await _store(db_session)
    svc = BackupService(db_session, FakeBackend())
    run = await svc.start_run(
        store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
    )
    assert run.status == BackupStatus.RUNNING and run.finished_at is None
    done = await svc.execute_run(run)
    assert done.status == BackupStatus.SUCCEEDED
    assert done.r2_key and done.finished_at is not None


@pytest.mark.asyncio
async def test_stale_running_reaped_then_new_backup(db_session: AsyncSession) -> None:
    # 逾時 RUNNING（40 分鐘前、疑似行程中斷）→ start_run 先回收為 FAILED,再開新的一次。
    store_id = await _store(db_session)
    stale = BackupRun(
        store_id=store_id,
        trigger=BackupTrigger.SCHEDULED,
        status=BackupStatus.RUNNING,
        db_name="lucamp",
        started_at=datetime.now(UTC) - timedelta(minutes=40),
    )
    db_session.add(stale)
    await db_session.flush()
    run = await BackupService(db_session, FakeBackend()).run_backup(
        store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
    )
    assert run.status == BackupStatus.SUCCEEDED  # 新備份成功
    await db_session.refresh(stale)
    assert stale.status == BackupStatus.FAILED  # 舊的逾時列被回收
    assert stale.last_error and "逾時" in stale.last_error


@pytest.mark.asyncio
async def test_recent_running_not_reaped(db_session: AsyncSession) -> None:
    # 剛開始（5 分鐘前）的 RUNNING 不算逾時 → 守衛仍擋（BackupAlreadyRunning）。
    store_id = await _store(db_session)
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.MANUAL,
            status=BackupStatus.RUNNING,
            db_name="lucamp",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await db_session.flush()
    with pytest.raises(BackupAlreadyRunning):
        await BackupService(db_session, FakeBackend()).run_backup(
            store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
        )


@pytest.mark.asyncio
async def test_get_health(db_session: AsyncSession) -> None:
    store_id = await _store(db_session)
    svc = BackupService(db_session, FakeBackend())
    # 從未成功 → 到期、無上次時間
    h0 = await svc.get_health(store_id)
    assert h0.enabled is True and h0.due_now is True
    assert h0.last_success_at is None and h0.last_success_age_hours is None
    assert h0.running is False
    # 成功一次後 → 有上次時間、age≈0、未到期
    await svc.run_backup(
        store_id, db_name="lucamp", trigger=BackupTrigger.MANUAL, actor_user_id=None
    )
    h1 = await svc.get_health(store_id)
    assert h1.last_success_at is not None
    assert h1.last_success_age_hours is not None and h1.last_success_age_hours < 1
    assert h1.due_now is False
