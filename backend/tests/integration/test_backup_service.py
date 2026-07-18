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
    # 未啟用 → 永不到期
    s.backup_enabled = False
    assert is_backup_due(now=now, last_success=None, settings=s) is False
    s.backup_enabled = True
    # 從未成功過 → 立即到期(首次)
    assert is_backup_due(now=now, last_success=None, settings=s) is True
    # 距上次 <24h → 未到期
    assert is_backup_due(now=now, last_success=now - timedelta(hours=10), settings=s) is False
    # 距上次 ≥24h 且已過離峰 → 到期
    assert is_backup_due(now=now, last_success=now - timedelta(hours=25), settings=s) is True
    # 到期但未過離峰(03:00 < 04:00)且落後未達 1.5×間隔 → 先不跑(等離峰)
    early = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    assert is_backup_due(now=early, last_success=early - timedelta(hours=25), settings=s) is False
    # 到期、未過離峰、但落後 >1.5×間隔(36h) → 強制補(不再等離峰)
    assert is_backup_due(now=early, last_success=early - timedelta(hours=40), settings=s) is True


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
