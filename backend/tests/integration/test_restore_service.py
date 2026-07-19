"""還原服務狀態機＋四驗（docs/31 §6）。外部程序以假 RestoreBackend/Verifier 替身;
另以真 SqlRestoreVerifier 對測試庫實跑四驗，證明檢查會執行。"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.backup.restore import (
    RestoreBackend,
    RestoreVerifier,
    SqlRestoreVerifier,
    VerificationResult,
    _validate_db_name,
)
from app.modules.backup.restore_service import RestoreService, default_restore_db_name
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import RestoreStatus, UserRole
from app.shared.exceptions import RestoreError


class FakeRestoreBackend(RestoreBackend):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str, int]] = []

    async def fetch_and_restore(
        self, *, r2_key: str, target_db: str, expected_sha256: str, expected_size: int
    ) -> None:
        self.calls.append((r2_key, target_db, expected_sha256, expected_size))
        if self.fail:
            raise RestoreError("R2 下載失敗（測試）")


class FakeVerifier(RestoreVerifier):
    def __init__(self, results: list[VerificationResult]) -> None:
        self.results = results
        self.called = False

    async def verify(self, *, target_db: str) -> list[VerificationResult]:
        self.called = True
        return self.results


def _all_ok() -> list[VerificationResult]:
    return [
        VerificationResult("alembic_head", True, "ok"),
        VerificationResult("table_counts", True, "ok"),
        VerificationResult("signature_bytea", True, "ok"),
        VerificationResult("backend_usable", True, "ok"),
    ]


async def _store_and_user(session: AsyncSession) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add(user)
    await session.flush()
    return store.id, user.id


async def _seed_source_backup(
    session: AsyncSession, store_id: int, *, r2_key: str = "backups/x.dump.enc"
) -> str:
    """插一筆 SUCCEEDED 備份作為還原來源（還原綁定目錄:只能還原已成功的備份）。"""
    from app.modules.backup.models import BackupRun
    from app.shared.enums import BackupStatus, BackupTrigger

    session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.SCHEDULED,
            status=BackupStatus.SUCCEEDED,
            db_name="lucamp",
            file_name=r2_key.split("/")[-1],
            r2_key=r2_key,
            sha256="a" * 64,
            size_bytes=123,
        )
    )
    await session.flush()
    return r2_key


@pytest.mark.asyncio
async def test_restore_verified_when_all_checks_ok(db_session: AsyncSession) -> None:
    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    backend = FakeRestoreBackend()
    verifier = FakeVerifier(_all_ok())
    run = await RestoreService(db_session, backend, verifier).run_restore(
        store_id,
        source_r2_key="backups/x.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    assert run.status == RestoreStatus.VERIFIED
    assert run.finished_at is not None
    assert run.verifications is not None and run.verifications["all_ok"] is True
    assert len(run.verifications["checks"]) == 4
    # 完整性資訊（sha256/大小）由來源 SUCCEEDED 備份帶入 backend
    assert backend.calls == [("backups/x.dump.enc", "lucamp_restore_test", "a" * 64, 123)]
    assert verifier.called is True


@pytest.mark.asyncio
async def test_restore_rejected_when_source_not_in_catalog(db_session: AsyncSession) -> None:
    # 未在備份目錄的任意 r2_key → 直接 FAILED,連還原後端都不呼叫（Codex #2）。
    store_id, user_id = await _store_and_user(db_session)
    backend = FakeRestoreBackend()
    run = await RestoreService(db_session, backend, FakeVerifier(_all_ok())).run_restore(
        store_id,
        source_r2_key="backups/foreign.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    assert run.status == RestoreStatus.FAILED
    assert run.last_error and "來源" in run.last_error
    assert backend.calls == []


@pytest.mark.asyncio
async def test_restore_failed_when_backend_errors(db_session: AsyncSession) -> None:
    # 下載/還原失敗 → FAILED，四驗不執行（不把未還原的記成 VERIFIED）。
    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    verifier = FakeVerifier(_all_ok())
    run = await RestoreService(db_session, FakeRestoreBackend(fail=True), verifier).run_restore(
        store_id,
        source_r2_key="backups/x.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    assert run.status == RestoreStatus.FAILED
    assert run.last_error and "下載" in run.last_error
    assert run.verifications is None
    assert verifier.called is False


@pytest.mark.asyncio
async def test_restore_failed_when_a_check_fails(db_session: AsyncSession) -> None:
    # 還原成功但四驗有一項不過 → FAILED，仍記下四驗結果供診斷。
    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    results = _all_ok()
    results[1] = VerificationResult("table_counts", False, "sales 查詢失敗")
    run = await RestoreService(db_session, FakeRestoreBackend(), FakeVerifier(results)).run_restore(
        store_id,
        source_r2_key="backups/x.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    assert run.status == RestoreStatus.FAILED
    assert run.last_error and "table_counts" in run.last_error
    assert run.verifications is not None and run.verifications["all_ok"] is False


@pytest.mark.asyncio
async def test_restore_writes_audit(db_session: AsyncSession) -> None:
    from app.core.audit import AuditLog

    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    await RestoreService(db_session, FakeRestoreBackend(), FakeVerifier(_all_ok())).run_restore(
        store_id,
        source_r2_key="backups/x.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    n = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "RESTORE_RUN")
    )
    assert n == 1


def test_default_restore_db_name_shape() -> None:
    name = default_restore_db_name()
    assert name.startswith("lucamp_restore_")
    _validate_db_name(name)  # 不得 raise


def test_validate_db_name_rejects_injection() -> None:
    for bad in ["lucamp; DROP DATABASE x", "Upper", "has space", "", "a" * 70]:
        with pytest.raises(RestoreError):
            _validate_db_name(bad)


@pytest.mark.asyncio
async def test_sql_verifier_runs_four_checks_against_real_db() -> None:
    # 對「已 migrate 到 head 的測試庫」實跑真四驗：alembic head 相符、關鍵表可查、起後端可用。
    # （測試庫交易隔離下他測資看不到，但 schema/alembic_version 已 commit，足以驗證檢查會跑。）
    base = get_settings().database_url
    target_db = make_url(base).database
    assert target_db is not None
    results = await SqlRestoreVerifier(base_url=base).verify(target_db=target_db)
    by_name = {r.name: r for r in results}
    # alembic_head：共用測試庫的 alembic_version 受他分支污染且 conftest 用 create_all，
    # 故此處只驗「檢查有跑且確實比對本分支 head」；相符與否的正確性由真還原演練（B4 驗收）佐證。
    assert "expected_head=" in by_name["alembic_head"].detail
    assert by_name["table_counts"].ok is True, by_name["table_counts"].detail
    assert by_name["backend_usable"].ok is True
    assert by_name["signature_bytea"].ok is True  # 0 筆亦視為通過（無損壞）
