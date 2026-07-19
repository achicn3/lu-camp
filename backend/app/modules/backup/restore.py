"""還原後端與四驗（docs/31 §6）：把「下載→解密→建全新庫→pg_restore」與「四項驗證」包成可注入介面。

**絕不就地覆蓋正式庫**：一律還原到 throwaway 庫（`lucamp_restore_<stamp>`）並驗證;切換（repoint+
重啟）另由受控腳本做（app 不能一邊連正式庫一邊把自己換掉,單機中途失敗兩頭落空）。

四驗（docs/31 §6）：①alembic current=head ②關鍵表可查/筆數 ③簽名 BYTEA 抽驗 sha256 可讀
④起後端可用（SELECT 1）。任一不過 → 該次還原記 FAILED,不顯示綠燈。外部程序失敗一律 raise
RestoreError（訊息不含祕密）。
"""

import asyncio
import contextlib
import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.backup.backend import _sha256_and_size
from app.shared.exceptions import RestoreError

logger = logging.getLogger(__name__)
_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"
_SUBPROC_TIMEOUT = 600  # 掛住的 docker/openssl/pg_restore 逾時即失敗,不讓還原永久 RUNNING
# 序列化 migrate-forward：alembic 的 in-process context 非執行緒安全,同一行程一次只跑一個升級。
_MIGRATE_LOCK = asyncio.Lock()
# throwaway 還原庫名安全樣式（防命令注入;只允許小寫英數底線,≤63＝PG 識別上限）。
_SAFE_DB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
# 關鍵表：涵蓋交易/現金/會員PII/庫存/簽署/購物金/稽核/租戶/使用者/設定——schema 完整性抽驗。
_KEY_TABLES = (
    "sales",
    "contacts",
    "signature_tasks",
    "serialized_items",
    "cash_sessions",
    "store_credit_ledger",
    "audit_log",
    "stores",
    "users",
    "settings",
)


@dataclass(frozen=True)
class VerificationResult:
    """單項驗證結果（落 restore_runs.verifications JSONB;供 UI 呈現）。"""

    name: str
    ok: bool
    detail: str


class RestoreBackend(Protocol):
    """還原後端：下載→驗完整性→解密→建全新庫→pg_restore（做完或 raise RestoreError）。

    expected_sha256/expected_size 來自來源 SUCCEEDED BackupRun：下載後先比對,不符即拒還原
    （擋損毀檔／錯快照／他環境物件）。
    """

    async def fetch_and_restore(
        self, *, r2_key: str, target_db: str, expected_sha256: str, expected_size: int
    ) -> None: ...

    async def drop_database(self, *, target_db: str) -> None:
        """丟棄一個 throwaway 還原庫（清理 FAILED/過舊者,避免累積塞爆磁碟）。"""
        ...


class RestoreVerifier(Protocol):
    """對還原後的 throwaway 庫做四驗,回結果清單。expected_manifest＝備份時的 key-table 筆數。"""

    async def verify(
        self, *, target_db: str, expected_manifest: dict[str, int] | None = None
    ) -> list[VerificationResult]: ...


def alembic_head() -> str:
    """本程式碼庫期望的 alembic head（還原庫須與之相符或可升級到此,schema 才相容）。"""
    head = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()
    if head is None:
        raise RestoreError("無法取得 alembic head")
    return head


def _is_ancestor(rev: str, head: str) -> bool:
    """rev 是否為 head 的祖先版本（含 head 本身）——即這份舊備份可循 migration 升級到 head。"""
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI)))
    return any(r.revision == rev for r in script.iterate_revisions(head, "base"))


def _upgrade_to_head(target_async_url: str) -> None:
    """把指定（throwaway 還原）庫升級到 alembic head。目標 URL **走 per-call Config**（非全域
    os.environ,避免併發互踩;env.py 另有 lucamp_restore_ 白名單防呆,杜絕誤打正式庫）。同步呼叫,由
    asyncio.to_thread 包起,並由 _MIGRATE_LOCK 序列化（alembic in-process context 非執行緒安全）。"""
    if not (make_url(target_async_url).database or "").startswith("lucamp_restore_"):
        raise RestoreError("migrate-forward 目標非 throwaway 還原庫,拒絕")
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", target_async_url)
    command.upgrade(cfg, "head")


def _validate_db_name(target_db: str) -> None:
    if not _SAFE_DB_NAME.match(target_db):
        raise RestoreError("還原庫名不合法（僅允許小寫英數底線、≤63）")


class SubprocessR2RestoreBackend:
    """真還原後端：boto3 下載 → openssl 解密 → docker 建庫 → pg_restore。憑證/口令建構子注入。"""

    def __init__(
        self,
        *,
        docker_bin: str,
        db_container: str,
        db_user: str,
        local_dir: str,
        passphrase: str,
        r2_endpoint: str,
        r2_access_key_id: str,
        r2_secret_access_key: str,
        r2_bucket: str,
    ) -> None:
        if not passphrase.strip() or not r2_access_key_id.strip() or not r2_bucket.strip():
            raise RestoreError("還原憑證未設定（R2/AES 口令）——請確認 .env.r2")
        self._docker = docker_bin
        self._container = db_container
        self._user = db_user
        self._dir = Path(local_dir)
        self._passphrase = passphrase
        self._endpoint = r2_endpoint
        self._akid = r2_access_key_id
        self._secret = r2_secret_access_key
        self._bucket = r2_bucket

    def _run(
        self, args: list[str], *, stdin_from: Path | None = None, env: dict[str, str] | None = None
    ) -> None:
        run_env = {**os.environ, **env} if env else None
        try:
            if stdin_from is not None:
                with stdin_from.open("rb") as inp:
                    subprocess.run(
                        args, stdin=inp, capture_output=True, check=True, env=run_env,
                        timeout=_SUBPROC_TIMEOUT,
                    )
            else:
                subprocess.run(
                    args, capture_output=True, check=True, env=run_env, timeout=_SUBPROC_TIMEOUT
                )
        except subprocess.TimeoutExpired as exc:
            raise RestoreError(f"還原子程序逾時（{args[0]}）") from exc
        except subprocess.CalledProcessError as exc:
            raise RestoreError(f"還原子程序失敗（{args[0]} rc={exc.returncode}）") from exc
        except OSError as exc:
            raise RestoreError(f"還原子程序無法執行：{exc.__class__.__name__}") from exc

    def _rm_container_file(self, path: str) -> None:
        """刪容器內明文(finally 用;不 raise,但失敗必記 log——靜默失敗會留整庫明文,Codex #4）。"""
        try:
            r = subprocess.run(
                [self._docker, "exec", self._container, "rm", "-f", path],
                capture_output=True, timeout=60, check=False,
            )
            if r.returncode != 0:
                logger.warning("container cleanup failed rc=%s path=%s", r.returncode, path)
        except Exception:
            logger.warning("container plaintext cleanup errored path=%s", path, exc_info=True)

    def _client(self) -> object:
        import boto3  # type: ignore[import-untyped]

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._akid,
            aws_secret_access_key=self._secret,
            region_name="auto",
        )

    def _do(self, r2_key: str, target_db: str, expected_sha256: str, expected_size: int) -> None:
        _validate_db_name(target_db)
        self._dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._dir, 0o700)
        enc_local = self._dir / f"{target_db}.dump.enc"
        dump_local = self._dir / f"{target_db}.dump"
        d, c, u = self._docker, self._container, self._user
        container_dump = f"/tmp/lucamp_restore_{target_db}.dump"
        try:
            # 1) 下載
            try:
                self._client().download_file(self._bucket, r2_key, str(enc_local))  # type: ignore[attr-defined]
            except Exception as exc:
                raise RestoreError(f"R2 下載失敗：{exc.__class__.__name__}") from exc
            if not enc_local.is_file() or enc_local.stat().st_size == 0:
                raise RestoreError("下載檔為空")
            # 1b) 完整性驗證（解密前）：與來源備份 sha256/大小比對,不符即拒（擋損毀/錯快照/他物件）
            sha, size = _sha256_and_size(enc_local)
            if size != expected_size or sha != expected_sha256:
                raise RestoreError("下載檔完整性不符（sha256/大小與備份紀錄不符）——拒絕還原")
            # 2) 解密（AES-256-CBC + PBKDF2 20 萬次）;口令走 env、不進 argv
            self._run(
                [
                    "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                    "-in", str(enc_local), "-out", str(dump_local), "-pass", "env:LU_BACKUP_PASS",
                ],
                env={"LU_BACKUP_PASS": self._passphrase},
            )
            if not dump_local.is_file() or dump_local.stat().st_size == 0:
                raise RestoreError("解密結果為空（口令錯誤或檔案損毀）")
            # 3) 複製進容器（唯一名）
            self._run([d, "cp", str(dump_local), f"{c}:{container_dump}"])
            # 4) 建全新庫（若殘留同名先移除;絕不碰正式庫）
            self._run([d, "exec", c, "psql", "-U", u, "-d", "postgres",
                       "-c", f'DROP DATABASE IF EXISTS "{target_db}"',
                       "-c", f'CREATE DATABASE "{target_db}"'])
            # 5) pg_restore 進 throwaway 庫
            self._run([d, "exec", c, "pg_restore", "-U", u, "-d", target_db, "--no-owner",
                       container_dump])
        finally:
            # 明文/密文/容器暫存各出口都清（不留可讀資料落地）
            dump_local.unlink(missing_ok=True)
            enc_local.unlink(missing_ok=True)
            self._rm_container_file(container_dump)

    async def fetch_and_restore(
        self, *, r2_key: str, target_db: str, expected_sha256: str, expected_size: int
    ) -> None:
        await asyncio.to_thread(self._do, r2_key, target_db, expected_sha256, expected_size)

    def _drop(self, target_db: str) -> None:
        _validate_db_name(target_db)
        if not target_db.startswith("lucamp_restore_"):  # 防呆:只丟 throwaway 還原庫
            raise RestoreError("drop_database 目標非 throwaway 還原庫,拒絕")
        d, c, u = self._docker, self._container, self._user
        # WITH (FORCE) 踢掉殘餘連線後刪（PG13+）;僅丟還原庫,絕不動正式庫
        self._run([d, "exec", c, "psql", "-U", u, "-d", "postgres",
                   "-c", f'DROP DATABASE IF EXISTS "{target_db}" WITH (FORCE)'])

    async def drop_database(self, *, target_db: str) -> None:
        await asyncio.to_thread(self._drop, target_db)


class SqlRestoreVerifier:
    """真四驗：連到還原後的 throwaway 庫跑四項檢查。base_url＝正式 async URL,只換 database 名。"""

    def __init__(self, *, base_url: str) -> None:
        self._base = base_url

    def _target_url(self, target_db: str) -> str:
        return make_url(self._base).set(database=target_db).render_as_string(hide_password=False)

    async def verify(
        self, *, target_db: str, expected_manifest: dict[str, int] | None = None
    ) -> list[VerificationResult]:
        _validate_db_name(target_db)
        # 先處理 alembic：舊版本備份(head 的祖先)先把 throwaway 庫升級到 head（獨立引擎），
        # 之後的資料檢查才落在「升級後」的 schema 上（Codex #2）。
        alembic_result = await self._ensure_head(target_db)
        engine = create_async_engine(self._target_url(target_db))
        results: list[VerificationResult] = [alembic_result]
        try:
            async with engine.connect() as conn:
                results.append(await self._check_tables(conn, expected_manifest))
                results.append(await self._check_signatures(conn))
                results.append(await self._check_usable(conn))
        except Exception as exc:  # 連不上還原庫本身即整體失敗
            results.append(VerificationResult("connect", False, f"{exc.__class__.__name__}"))
        finally:
            await engine.dispose()
        return results

    async def _read_version(self, target_db: str) -> str | None:
        engine = create_async_engine(self._target_url(target_db))
        try:
            async with engine.connect() as conn:
                ver: str | None = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                return ver
        except Exception:
            return None
        finally:
            await engine.dispose()

    async def _ensure_head(self, target_db: str) -> VerificationResult:
        """alembic 版本檢查（含升級）：head→通過；head 的祖先→把 throwaway 升級到 head 再確認；
        其餘（未知/更新/無表）→失敗。升級只對 throwaway 還原庫執行,絕不動正式庫（Codex #2）。"""
        head = alembic_head()
        ver = await self._read_version(target_db)
        if ver is None:
            return VerificationResult("alembic_head", False, "無 alembic_version 表")
        if ver == head:
            return VerificationResult("alembic_head", True, f"restored={ver} == head {head}")
        if not _is_ancestor(ver, head):
            return VerificationResult(
                "alembic_head", False, f"restored={ver} 非 head({head})亦非其祖先——不相容"
            )
        # 防呆：只升級 throwaway 還原庫,永不對非 lucamp_restore_ 庫自動升級
        if not target_db.startswith("lucamp_restore_"):
            return VerificationResult(
                "alembic_head", False, f"restored={ver} 為舊版本但目標非還原庫,不自動升級"
            )
        try:
            async with _MIGRATE_LOCK:  # 序列化,避免併發 alembic in-process 互踩
                await asyncio.to_thread(_upgrade_to_head, self._target_url(target_db))
        except Exception as exc:
            return VerificationResult(
                "alembic_head", False, f"升級到 head 失敗:{exc.__class__.__name__}"
            )
        ver2 = await self._read_version(target_db)
        return VerificationResult(
            "alembic_head", ver2 == head, f"restored={ver} → 升級到 {ver2}（head={head}）"
        )

    async def _check_tables(
        self, conn: object, expected_manifest: dict[str, int] | None
    ) -> VerificationResult:
        """關鍵表可查＋與備份時 manifest 比對:備份當時有資料(>0)但還原後為 0 → 判定空/半殘還原失敗
        （Codex #3：不再把「count 查得到含 0 筆」都當通過）。無 manifest(舊備份)則只驗可查。"""
        counts: dict[str, int] = {}
        ok = True
        empties: list[str] = []
        for tbl in _KEY_TABLES:
            try:
                counts[tbl] = int(
                    await conn.scalar(text(f"SELECT count(*) FROM {tbl}"))  # type: ignore[attr-defined]
                )
            except Exception:
                ok = False
                counts[tbl] = -1  # -1＝該表查詢失敗（schema 缺損）
                continue
            if expected_manifest and expected_manifest.get(tbl, 0) > 0 and counts[tbl] == 0:
                ok = False  # 備份時有資料、還原後卻空 → 資料掉了
                empties.append(tbl)
        detail = ", ".join(f"{k}={v}" for k, v in counts.items())
        if empties:
            detail = f"空表(備份時有資料):{','.join(empties)}｜{detail}"
        return VerificationResult("table_counts", ok, detail)

    async def _check_signatures(self, conn: object) -> VerificationResult:
        """簽名 BYTEA 抽驗:確認確實是可解析的 PNG（檔頭魔術位元組），而非只看 sha256 長度(恆真)。"""
        png_magic = b"\x89PNG\r\n\x1a\n"
        try:
            rows = (
                await conn.execute(  # type: ignore[attr-defined]
                    text(
                        "SELECT signature_image FROM signature_tasks "
                        "WHERE signature_image IS NOT NULL LIMIT 5"
                    )
                )
            ).all()
            for (img,) in rows:
                if not (isinstance(img, bytes | memoryview) and bytes(img[:8]) == png_magic):
                    return VerificationResult(
                        "signature_bytea", False, "簽名非合法 PNG（檔頭不符）"
                    )
            return VerificationResult(
                "signature_bytea", True, f"抽驗 {len(rows)} 筆簽名 PNG 檔頭合法"
            )
        except Exception as exc:
            return VerificationResult("signature_bytea", False, f"{exc.__class__.__name__}")

    async def _check_usable(self, conn: object) -> VerificationResult:
        try:
            one = await conn.scalar(text("SELECT 1"))  # type: ignore[attr-defined]
            return VerificationResult("backend_usable", one == 1, "SELECT 1 ok")
        except Exception as exc:
            return VerificationResult("backend_usable", False, f"{exc.__class__.__name__}")


def results_to_json(results: list[VerificationResult]) -> dict[str, object]:
    """四驗結果轉 JSONB 可存形狀（含整體 all_ok）。"""
    return {"all_ok": all(r.ok for r in results), "checks": [asdict(r) for r in results]}
