"""還原業務邏輯（docs/31 §6）：狀態機（RUNNING→VERIFIED/FAILED）＋四驗留痕＋稽核。

**只還原到 throwaway 全新庫並驗證,絕不動正式庫**；VERIFIED 才代表「救得回」,切換另由受控腳本做。
兩階段（start_restore/execute_restore）供端點「立即回 RUNNING＋背景執行」。還原屬高危,一律 audit。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.backup.models import RestoreRun
from app.modules.backup.repository import BackupRepository
from app.modules.backup.restore import (
    RestoreBackend,
    RestoreVerifier,
    results_to_json,
)
from app.shared.enums import RestoreStatus
from app.shared.exceptions import RestoreError

_ERR_MAX = 2000


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def default_restore_db_name(now: datetime | None = None) -> str:
    """throwaway 還原庫名：lucamp_restore_<時戳>（不與正式庫同名,絕不就地覆蓋）。"""
    ts = (now or datetime.now(UTC)).strftime("%Y%m%d_%H%M%S")
    return f"lucamp_restore_{ts}"


class RestoreService:
    def __init__(
        self, session: AsyncSession, backend: RestoreBackend, verifier: RestoreVerifier
    ) -> None:
        self._session = session
        self._repo = BackupRepository(session)
        self._backend = backend
        self._verifier = verifier

    async def start_restore(
        self, store_id: int, *, source_r2_key: str, actor_user_id: int, restore_db_name: str
    ) -> RestoreRun:
        """插一列 RUNNING（尚未做外部程序）。端點可在此後 commit 並立即回 RUNNING 供前端輪詢。"""
        return await self._repo.add_restore(
            RestoreRun(
                store_id=store_id,
                status=RestoreStatus.RUNNING,
                source_r2_key=source_r2_key,
                restore_db_name=restore_db_name,
                actor_user_id=actor_user_id,
            )
        )

    async def execute_restore(self, run: RestoreRun) -> RestoreRun:
        """對一列 RUNNING 做「還原到全新庫＋四驗」並記終態（run 須綁在本 session）。

        還原或四驗任一失敗 → FAILED＋last_error（不把未驗證的記成 VERIFIED）。全過 → VERIFIED。
        兩種終態都寫 audit_log。正式庫全程未被觸碰。
        """
        try:
            await self._backend.fetch_and_restore(
                r2_key=run.source_r2_key, target_db=run.restore_db_name
            )
        except RestoreError as exc:
            return await self._fail(run, str(exc))
        except Exception as exc:  # 任何外部程序例外一律如實記 FAILED
            return await self._fail(run, f"還原未預期錯誤：{exc.__class__.__name__}")
        results = await self._verifier.verify(target_db=run.restore_db_name)
        run.verifications = results_to_json(results)
        if all(r.ok for r in results):
            run.status = RestoreStatus.VERIFIED
        else:
            run.status = RestoreStatus.FAILED
            failed = [r.name for r in results if not r.ok]
            run.last_error = f"四驗未全過：{', '.join(failed)}"[:_ERR_MAX]
        run.finished_at = datetime.now(UTC)
        await self._session.flush()
        await self._audit(run)
        return run

    async def run_restore(
        self, store_id: int, *, source_r2_key: str, actor_user_id: int, restore_db_name: str
    ) -> RestoreRun:
        """一次還原（start＋execute，同 session）。測試用；端點用兩階段＋背景執行。"""
        run = await self.start_restore(
            store_id,
            source_r2_key=source_r2_key,
            actor_user_id=actor_user_id,
            restore_db_name=restore_db_name,
        )
        return await self.execute_restore(run)

    async def list_restores(self, store_id: int, *, limit: int = 30) -> list[RestoreRun]:
        return await self._repo.list_restores(store_id, limit=limit)

    async def get_restore(self, store_id: int, restore_id: int) -> RestoreRun | None:
        return await self._repo.get_restore(store_id, restore_id)

    async def _fail(self, run: RestoreRun, message: str) -> RestoreRun:
        run.status = RestoreStatus.FAILED
        run.last_error = message[:_ERR_MAX]
        run.finished_at = datetime.now(UTC)
        await self._session.flush()
        await self._audit(run)
        return run

    async def _audit(self, run: RestoreRun) -> None:
        await write_audit_log(
            self._session,
            store_id=run.store_id,
            actor_user_id=run.actor_user_id,
            action="RESTORE_RUN",
            entity_type="restore_run",
            entity_id=str(run.id),
            before={"status": RestoreStatus.RUNNING.value},
            after={
                "status": run.status.value,
                "source_r2_key": run.source_r2_key,
                "restore_db_name": run.restore_db_name,
                "verifications": run.verifications,
            },
        )
