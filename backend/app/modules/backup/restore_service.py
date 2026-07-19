"""還原業務邏輯（docs/31 §6）：狀態機（RUNNING→VERIFIED/FAILED）＋四驗留痕＋稽核。

**只還原到 throwaway 全新庫並驗證,絕不動正式庫**；VERIFIED 才代表「救得回」,切換另由受控腳本做。
兩階段（start_restore/execute_restore）供端點「立即回 RUNNING＋背景執行」。還原屬高危,一律 audit。
"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

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

logger = logging.getLogger(__name__)
_ERR_MAX = 2000


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def default_restore_db_name(now: datetime | None = None) -> str:
    """throwaway 還原庫名：lucamp_restore_<時戳>_<uuid8>。

    加短 UUID：同秒觸發的兩次還原也拿到唯一庫名 → 目標庫與所有暫存路徑（host/容器）皆唯一,不會
    互相覆蓋或競爭 DROP/CREATE/pg_restore（Codex 第三輪 #2）。不與正式庫同名,絕不就地覆蓋。"""
    ts = (now or datetime.now(UTC)).strftime("%Y%m%d_%H%M%S")
    return f"lucamp_restore_{ts}_{uuid4().hex[:8]}"


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
        兩種終態都寫 audit_log。正式庫全程未被觸碰。來源必須是目錄內的 SUCCEEDED 備份,並在下載後
        以其 sha256/大小驗完整性（擋錯快照/損毀/他物件）。
        """
        source = await self._repo.get_succeeded_by_r2_key(run.store_id, run.source_r2_key)
        if source is None or source.sha256 is None or source.size_bytes is None:
            return await self._fail(run, "來源不在備份目錄或缺完整性資訊——拒絕還原")
        try:
            await self._backend.fetch_and_restore(
                r2_key=run.source_r2_key,
                target_db=run.restore_db_name,
                expected_sha256=source.sha256,
                expected_size=source.size_bytes,
            )
        except RestoreError as exc:
            return await self._fail(run, str(exc), drop=True)
        except Exception as exc:  # 任何外部程序例外一律如實記 FAILED
            return await self._fail(run, f"還原未預期錯誤：{exc.__class__.__name__}", drop=True)
        results = await self._verifier.verify(target_db=run.restore_db_name)
        run.verifications = results_to_json(results)
        if all(r.ok for r in results):
            run.status = RestoreStatus.VERIFIED
        else:
            failed = [r.name for r in results if not r.ok]
            return await self._fail(run, f"四驗未全過：{', '.join(failed)}", drop=True)
        run.finished_at = datetime.now(UTC)
        await self._session.flush()
        await self._audit(run)
        return run

    async def _drop_throwaway(self, target_db: str) -> None:
        """丟棄 throwaway 還原庫（best-effort;失敗只記 log,不影響狀態）。"""
        try:
            await self._backend.drop_database(target_db=target_db)
        except Exception:
            logger.warning("failed to drop throwaway restore db %s", target_db, exc_info=True)

    async def reap_old_restores(self, store_id: int, *, keep_run_id: int) -> None:
        """回收舊 throwaway 還原庫（Codex #4）：丟棄 FAILED 者與較舊的 VERIFIED,只留**最新一份
        VERIFIED**（供切換）＋當前這次;避免重試/演練累積整庫塞爆磁碟。進行中(RUNNING)者不動。"""
        runs = await self._repo.list_restores(store_id, limit=200)
        verified = sorted(
            (r for r in runs if r.status == RestoreStatus.VERIFIED and r.id != keep_run_id),
            key=lambda r: r.id,
            reverse=True,
        )
        keep_verified = verified[0].id if verified else None
        for r in runs:
            if r.id == keep_run_id or r.status == RestoreStatus.RUNNING:
                continue
            if r.status == RestoreStatus.VERIFIED and r.id == keep_verified:
                continue  # 保留最新一份 VERIFIED 供切換
            await self._drop_throwaway(r.restore_db_name)

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

    async def _fail(self, run: RestoreRun, message: str, *, drop: bool = False) -> RestoreRun:
        run.status = RestoreStatus.FAILED
        run.last_error = message[:_ERR_MAX]
        run.finished_at = datetime.now(UTC)
        if drop:  # 部分/未驗證的 throwaway 庫無用又佔磁碟 → 立即丟棄（Codex 第三輪 #4）
            await self._drop_throwaway(run.restore_db_name)
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
