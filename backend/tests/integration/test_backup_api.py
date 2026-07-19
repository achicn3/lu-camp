"""backup API 整合測試（docs/31 §5）：健康度/清單/手動觸發、MANAGER 權限、R2 未設定→503。

實際 dump 由背景任務跑;此處 monkeypatch build_backup_backend/launch_manual_backup,
只驗端點同步部分（插 RUNNING＋202＋形狀＋守衛）。背景執行另在 service 層驗。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.backup.backend import BackupArtifact, BackupBackend
from app.modules.backup.models import BackupRun, RestoreRun
from app.modules.backup.restore import RestoreBackend
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import BackupStatus, BackupTrigger, RestoreStatus, UserRole


class _FakeBackend(BackupBackend):
    async def create_and_upload(self, *, db_name: str, stamp: str) -> BackupArtifact:
        return BackupArtifact(
            file_name=f"{db_name}_{stamp}.dump.enc",
            r2_key=f"backups/{db_name}_{stamp}.dump.enc",
            sha256="a" * 64,
            size_bytes=1,
        )

    async def prune(self, *, db_name: str, keep: int) -> None:
        return None


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed_user(session: AsyncSession, role: UserRole) -> tuple[str, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username=f"u{role.value}", password_hash="h", role=role)
    session.add(user)
    await session.flush()
    token = encode_access_token(user_id=user.id, role=role.value, store_id=store.id)
    return token, store.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_shape(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, _ = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.get("/api/v1/backup/health", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["interval_hours"] == 24
    assert body["retention"] == 30
    assert body["offpeak_hour"] == 21
    assert body["last_success_at"] is None
    assert body["last_success_age_hours"] is None
    assert body["due_now"] is True  # 從未成功過 → 到期
    assert body["running"] is False


@pytest.mark.asyncio
async def test_health_manager_only(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, _ = await _seed_user(db_session, UserRole.CLERK)
    resp = await client.get("/api/v1/backup/health", headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_runs(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id = await _seed_user(db_session, UserRole.MANAGER)
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.SCHEDULED,
            status=BackupStatus.SUCCEEDED,
            db_name="lucamp",
            file_name="lucamp_x.dump.enc",
            r2_key="backups/lucamp_x.dump.enc",
            sha256="b" * 64,
            size_bytes=123,
        )
    )
    await db_session.flush()
    resp = await client.get("/api/v1/backup/runs", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "SUCCEEDED"
    assert body[0]["r2_key"] == "backups/lucamp_x.dump.enc"


@pytest.mark.asyncio
async def test_trigger_returns_503_when_unconfigured(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 測試環境 .env 無 R2_*/BACKUP_PASSPHRASE → build_backup_backend() 回 None → 503（不假成功）。
    token, _ = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.post("/api/v1/backup/runs", headers=_auth(token))
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_trigger_accepts_and_inserts_running(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R2 已設定（假後端）→ 202 + 插一列 RUNNING;背景任務以 no-op 攔下（不真的起 asyncio 任務）。
    import app.modules.backup.router as router_mod

    launched: list[tuple[int, int]] = []

    def _fake_launch(run_id: int, store_id: int) -> None:
        launched.append((run_id, store_id))

    monkeypatch.setattr(router_mod, "build_backup_backend", lambda: _FakeBackend())
    monkeypatch.setattr(router_mod, "launch_manual_backup", _fake_launch)
    token, store_id = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.post("/api/v1/backup/runs", headers=_auth(token))
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "RUNNING"
    assert body["trigger"] == "MANUAL"
    assert body["actor_user_id"] is not None
    assert launched == [(body["id"], store_id)]  # 背景任務有被排入


@pytest.mark.asyncio
async def test_trigger_conflict_when_running(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.modules.backup.router as router_mod

    monkeypatch.setattr(router_mod, "build_backup_backend", lambda: _FakeBackend())
    monkeypatch.setattr(router_mod, "launch_manual_backup", lambda run_id, store_id: None)
    token, store_id = await _seed_user(db_session, UserRole.MANAGER)
    db_session.add(
        BackupRun(
            store_id=store_id,
            trigger=BackupTrigger.SCHEDULED,
            status=BackupStatus.RUNNING,
            db_name="lucamp",
        )
    )
    await db_session.flush()
    resp = await client.post("/api/v1/backup/runs", headers=_auth(token))
    assert resp.status_code == 409


# --- 還原（docs/31 §6）：清單＋強卡控觸發 ------------------------------------------


class _FakeRestoreBackend(RestoreBackend):
    async def fetch_and_restore(
        self, *, r2_key: str, target_db: str, expected_sha256: str, expected_size: int
    ) -> None:
        return None

    async def drop_database(self, *, target_db: str) -> None:
        return None


async def _seed_backup(
    session: AsyncSession, store_id: int, r2_key: str = "backups/lucamp_x.dump.enc"
) -> None:
    """插一筆 SUCCEEDED 備份作為還原來源（還原綁定目錄）。"""
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


@pytest.mark.asyncio
async def test_list_restores(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id = await _seed_user(db_session, UserRole.MANAGER)
    # actor_user_id 需存在的 user；_seed_user 已建一個 MANAGER（id 取自該 store 的 user）
    from app.modules.user.models import User as U

    uid = await db_session.scalar(select(U.id).where(U.store_id == store_id))
    db_session.add(
        RestoreRun(
            store_id=store_id,
            status=RestoreStatus.VERIFIED,
            source_r2_key="backups/lucamp_x.dump.enc",
            restore_db_name="lucamp_restore_x",
            actor_user_id=uid,
            verifications={"all_ok": True, "checks": []},
        )
    )
    await db_session.flush()
    resp = await client.get("/api/v1/backup/restores", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "VERIFIED"
    assert body[0]["restore_db_name"] == "lucamp_restore_x"


@pytest.mark.asyncio
async def test_restore_requires_acknowledge(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={
            "source_r2_key": "backups/lucamp_x.dump.enc",
            "confirm_text": "lucamp_x.dump.enc",
            "acknowledge": False,
        },
    )
    assert resp.status_code == 400
    assert "知情" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restore_confirm_text_must_match_filename(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={
            "source_r2_key": "backups/lucamp_x.dump.enc",
            "confirm_text": "亂打",
            "acknowledge": True,
        },
    )
    assert resp.status_code == 400
    assert "lucamp_x.dump.enc" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restore_503_when_unconfigured(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 卡控全過＋來源在目錄，但 R2 未設定 → 503（測試環境無 R2）。
    token, store_id = await _seed_user(db_session, UserRole.MANAGER)
    await _seed_backup(db_session, store_id)
    resp = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={
            "source_r2_key": "backups/lucamp_x.dump.enc",
            "confirm_text": "lucamp_x.dump.enc",
            "acknowledge": True,
        },
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_restore_404_when_source_not_in_catalog(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 卡控全過但 source_r2_key 不是本店已成功備份 → 404（擋任意/他環境物件，Codex #2）。
    token, _ = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={
            "source_r2_key": "backups/foreign.dump.enc",
            "confirm_text": "foreign.dump.enc",
            "acknowledge": True,
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_accepts_and_inserts_running(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.modules.backup.router as router_mod

    launched: list[tuple[int, int]] = []

    def _fake_launch(restore_id: int, store_id: int) -> None:
        launched.append((restore_id, store_id))

    monkeypatch.setattr(router_mod, "build_restore_backend", lambda: _FakeRestoreBackend())
    monkeypatch.setattr(router_mod, "launch_restore", _fake_launch)
    token, store_id = await _seed_user(db_session, UserRole.MANAGER)
    await _seed_backup(db_session, store_id)
    resp = await client.post(
        "/api/v1/backup/restore",
        headers=_auth(token),
        json={
            "source_r2_key": "backups/lucamp_x.dump.enc",
            "confirm_text": "lucamp_x.dump.enc",
            "acknowledge": True,
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "RUNNING"
    assert body["source_r2_key"] == "backups/lucamp_x.dump.enc"
    assert body["restore_db_name"].startswith("lucamp_restore_")
    assert launched == [(body["id"], store_id)]
