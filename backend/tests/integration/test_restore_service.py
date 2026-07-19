"""還原服務狀態機＋四驗（docs/31 §6）。外部程序以假 RestoreBackend/Verifier 替身;
另以真 SqlRestoreVerifier 對測試庫實跑四驗，證明檢查會執行。"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.backup.models import RestoreRun
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
from app.shared.exceptions import RestoreAlreadyRunning, RestoreError


class FakeRestoreBackend(RestoreBackend):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str, int]] = []
        self.dropped: list[str] = []

    async def fetch_and_restore(
        self, *, r2_key: str, target_db: str, expected_sha256: str, expected_size: int
    ) -> None:
        self.calls.append((r2_key, target_db, expected_sha256, expected_size))
        if self.fail:
            raise RestoreError("R2 下載失敗（測試）")

    async def drop_database(self, *, target_db: str) -> None:
        self.dropped.append(target_db)


class FakeVerifier(RestoreVerifier):
    def __init__(self, results: list[VerificationResult]) -> None:
        self.results = results
        self.called = False

    async def verify(
        self, *, target_db: str, expected_manifest: dict[str, int] | None = None
    ) -> list[VerificationResult]:
        self.called = True
        self.manifest_seen = expected_manifest
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
    assert backend.dropped == []  # 沒建庫→不需丟


@pytest.mark.asyncio
async def test_restore_failed_when_backend_errors(db_session: AsyncSession) -> None:
    # 下載/還原失敗 → FAILED，四驗不執行（不把未還原的記成 VERIFIED）。
    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    verifier = FakeVerifier(_all_ok())
    backend = FakeRestoreBackend(fail=True)
    run = await RestoreService(db_session, backend, verifier).run_restore(
        store_id,
        source_r2_key="backups/x.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    assert run.status == RestoreStatus.FAILED
    assert run.last_error and "下載" in run.last_error
    assert run.verifications is None
    assert verifier.called is False
    assert "lucamp_restore_test" in backend.dropped  # 失敗→丟棄 throwaway（Codex #4）


@pytest.mark.asyncio
async def test_restore_failed_when_a_check_fails(db_session: AsyncSession) -> None:
    # 還原成功但四驗有一項不過 → FAILED，仍記下四驗結果供診斷。
    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    results = _all_ok()
    results[1] = VerificationResult("table_counts", False, "sales 查詢失敗")
    backend = FakeRestoreBackend()
    run = await RestoreService(db_session, backend, FakeVerifier(results)).run_restore(
        store_id,
        source_r2_key="backups/x.dump.enc",
        actor_user_id=user_id,
        restore_db_name="lucamp_restore_test",
    )
    assert run.status == RestoreStatus.FAILED
    assert run.last_error and "table_counts" in run.last_error
    assert run.verifications is not None and run.verifications["all_ok"] is False
    assert "lucamp_restore_test" in backend.dropped  # 四驗不過→丟棄 throwaway（Codex #4）


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


@pytest.mark.asyncio
async def test_reap_old_restores_drops_failed_and_old_verified(db_session: AsyncSession) -> None:
    # Codex 第三輪 #4：回收舊 throwaway 庫——丟 FAILED 與較舊 VERIFIED,留最新 VERIFIED＋當前這次。
    store_id, user_id = await _store_and_user(db_session)

    def _mk(status: RestoreStatus, db: str) -> RestoreRun:
        r = RestoreRun(
            store_id=store_id, status=status, source_r2_key="backups/x.dump.enc",
            restore_db_name=db, actor_user_id=user_id,
        )
        db_session.add(r)
        return r

    r_old_ok = _mk(RestoreStatus.VERIFIED, "lucamp_restore_a")
    _mk(RestoreStatus.FAILED, "lucamp_restore_b")
    r_new_ok = _mk(RestoreStatus.VERIFIED, "lucamp_restore_c")
    r_current = _mk(RestoreStatus.RUNNING, "lucamp_restore_d")
    await db_session.flush()
    backend = FakeRestoreBackend()
    await RestoreService(db_session, backend, FakeVerifier(_all_ok())).reap_old_restores(
        store_id, keep_run_id=r_current.id
    )
    # 丟：最舊 VERIFIED(a) + FAILED(b)；留：最新 VERIFIED(c) + 當前 RUNNING(d)
    assert set(backend.dropped) == {"lucamp_restore_a", "lucamp_restore_b"}
    assert r_new_ok.restore_db_name == "lucamp_restore_c"  # 保留供切換
    assert r_old_ok.id < r_new_ok.id


@pytest.mark.asyncio
async def test_restore_single_flight_guard(db_session: AsyncSession) -> None:
    # Codex 第四輪 #4：已有 RUNNING 還原 → 再觸發被守衛擋（RestoreAlreadyRunning）。
    store_id, user_id = await _store_and_user(db_session)
    await _seed_source_backup(db_session, store_id)
    db_session.add(
        RestoreRun(
            store_id=store_id, status=RestoreStatus.RUNNING, source_r2_key="backups/x.dump.enc",
            restore_db_name="lucamp_restore_a", actor_user_id=user_id,
        )
    )
    await db_session.flush()
    with pytest.raises(RestoreAlreadyRunning):
        await RestoreService(db_session, FakeRestoreBackend(), FakeVerifier(_all_ok())).run_restore(
            store_id, source_r2_key="backups/x.dump.enc", actor_user_id=user_id,
            restore_db_name="lucamp_restore_b",
        )


@pytest.mark.asyncio
async def test_terminalize_failed_marks_failed_and_drops(db_session: AsyncSession) -> None:
    # Codex 第四輪 #5：worker 未預期失敗後把 RUNNING 還原轉 FAILED＋drop 其庫。
    store_id, user_id = await _store_and_user(db_session)
    r = RestoreRun(
        store_id=store_id, status=RestoreStatus.RUNNING, source_r2_key="backups/x.dump.enc",
        restore_db_name="lucamp_restore_z", actor_user_id=user_id,
    )
    db_session.add(r)
    await db_session.flush()
    backend = FakeRestoreBackend()
    ok = await RestoreService(db_session, backend, FakeVerifier(_all_ok())).terminalize_failed(
        store_id, r.id, "worker 中斷"
    )
    assert ok is True
    await db_session.refresh(r)
    assert r.status == RestoreStatus.FAILED and r.finished_at is not None
    assert "lucamp_restore_z" in backend.dropped


@pytest.mark.asyncio
async def test_verifier_manifest_detects_empty_restore() -> None:
    # Codex #3：manifest 說某表有資料、還原後卻空 → table_counts 失敗（擋空/半殘還原）。
    base = get_settings().database_url
    target_db = make_url(base).database
    assert target_db is not None
    results = await SqlRestoreVerifier(base_url=base).verify(
        target_db=target_db, expected_manifest={"sales": 5}  # 測試庫 sales 已 commit 資料為 0
    )
    by_name = {r.name: r for r in results}
    assert by_name["table_counts"].ok is False
    assert "空表" in by_name["table_counts"].detail


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
    # alembic_head：測試庫非 lucamp_restore_ 且版本受他分支污染 → 不自動升級,只驗有跑並回報版本。
    # 升級到 head 的正確性由真還原演練佐證,且防呆保證非還原庫絕不被自動升級（Codex #2）。
    assert "restored=" in by_name["alembic_head"].detail
    assert by_name["table_counts"].ok is True, by_name["table_counts"].detail
    assert by_name["backend_usable"].ok is True
    assert by_name["signature_bytea"].ok is True  # 0 筆亦視為通過（無損壞）


def test_is_ancestor_recognizes_head_and_parent() -> None:
    # Codex #2：head 的直系祖先應被視為「可升級到 head 的舊版本」；亂數版本不是。
    from app.modules.backup.restore import _is_ancestor, alembic_head

    head = alembic_head()
    assert _is_ancestor(head, head) is True  # head 本身
    assert _is_ancestor("e2a9c4b7f1d3", head) is True  # 前一版（backup migration 的 down_revision）
    assert _is_ancestor("not_a_real_revision", head) is False
