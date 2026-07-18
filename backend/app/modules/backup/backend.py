"""備份後端抽象（docs/31 §4）：把「dump→驗證→加密→上傳→修剪」的外部程序（docker/openssl/boto3）
包成可注入介面,service 的狀態機才可用假替身單元測試,不真的 dump/上傳。

真實作 `SubprocessR2Backend` 為 docs/28 runbook 的程式化版本(已人工演練驗證):所有步驟任一失敗
即 raise BackupError(假備份是最大風險——絕不把失敗記成功)。R2 憑證/AES 口令由建構子注入(來自
`.env.r2`,不入 DB/log/例外訊息)。
"""

import asyncio
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.shared.exceptions import BackupError


@dataclass(frozen=True)
class BackupArtifact:
    """一次成功備份的產物中繼資料(落 backup_runs;不含祕密)。"""

    file_name: str
    r2_key: str
    sha256: str
    size_bytes: int


class BackupBackend(Protocol):
    """備份後端介面。create_and_upload 做完整流程或 raise BackupError;prune 修剪保留份數。"""

    async def create_and_upload(self, *, db_name: str, stamp: str) -> BackupArtifact: ...

    async def prune(self, *, db_name: str, keep: int) -> None: ...


def _sha256_and_size(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


class SubprocessR2Backend:
    """真後端:docker exec pg_dump → pg_restore --list 驗 → 加密 → sha256 → boto3 上傳 R2 → 修剪。

    與 docs/28 §1 同流程。所有阻塞呼叫以 asyncio.to_thread 移出事件迴圈。憑證/口令建構子注入。
    """

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
            raise BackupError("備份憑證未設定(R2/AES 口令)——請確認 .env.r2")
        self._docker = docker_bin
        self._container = db_container
        self._user = db_user
        self._dir = Path(local_dir)
        self._passphrase = passphrase
        self._endpoint = r2_endpoint
        self._akid = r2_access_key_id
        self._secret = r2_secret_access_key
        self._bucket = r2_bucket

    def _run(self, args: list[str], *, stdout_to: Path | None = None) -> None:
        """跑一個子程序;非 0 → BackupError(訊息不含憑證)。"""
        try:
            if stdout_to is not None:
                with stdout_to.open("wb") as out:
                    subprocess.run(args, stdout=out, stderr=subprocess.PIPE, check=True)
            else:
                subprocess.run(args, capture_output=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise BackupError(f"備份子程序失敗({args[0]} rc={exc.returncode})") from exc
        except OSError as exc:
            raise BackupError(f"備份子程序無法執行:{exc.__class__.__name__}") from exc

    def _do(self, db_name: str, stamp: str) -> BackupArtifact:
        self._dir.mkdir(parents=True, exist_ok=True)
        dump_local = self._dir / f"{db_name}_{stamp}.dump"
        enc_local = self._dir / f"{db_name}_{stamp}.dump.enc"
        d, c, u = self._docker, self._container, self._user
        # 1) 容器內 dump(custom format,含 BYTEA 簽名)
        self._run(
            [d, "exec", c, "pg_dump", "-U", u, "-Fc", "-d", db_name, "-f", "/tmp/backup.dump"]
        )
        # 2) 驗 dump 可讀(空/壞檔在此擋下)
        self._run([d, "exec", c, "pg_restore", "--list", "/tmp/backup.dump"])
        # 3) 複製出容器
        self._run([d, "exec", c, "cat", "/tmp/backup.dump"], stdout_to=dump_local)
        if not dump_local.is_file() or dump_local.stat().st_size == 0:
            raise BackupError("dump 檔為空,拒絕記成功")
        # 4) 加密(AES-256-CBC + PBKDF2 20 萬次)
        self._run([
            "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
            "-in", str(dump_local), "-out", str(enc_local), "-pass", f"pass:{self._passphrase}",
        ])
        dump_local.unlink(missing_ok=True)  # 明文 dump 用畢即刪(只留加密檔)
        sha, size = _sha256_and_size(enc_local)
        # 5) 上傳 R2
        key = f"backups/{enc_local.name}"
        self._upload(enc_local, key)
        return BackupArtifact(file_name=enc_local.name, r2_key=key, sha256=sha, size_bytes=size)

    def _client(self) -> object:
        import boto3  # type: ignore[import-untyped]  # 函式內 import:boto3 較重,僅備份路徑需要

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._akid,
            aws_secret_access_key=self._secret,
            region_name="auto",
        )

    def _upload(self, path: Path, key: str) -> None:
        try:
            self._client().upload_file(str(path), self._bucket, key)  # type: ignore[attr-defined]
        except Exception as exc:  # boto3/botocore 各種例外統一收斂;訊息不含祕密
            raise BackupError(f"R2 上傳失敗:{exc.__class__.__name__}") from exc

    async def create_and_upload(self, *, db_name: str, stamp: str) -> BackupArtifact:
        return await asyncio.to_thread(self._do, db_name, stamp)

    def _prune(self, db_name: str, keep: int) -> None:
        prefix = f"backups/{db_name}_"
        try:
            client = self._client()
            resp = client.list_objects_v2(Bucket=self._bucket, Prefix=prefix)  # type: ignore[attr-defined]
            keys = sorted(o["Key"] for o in resp.get("Contents", []))
            for old in keys[:-keep] if keep > 0 else keys:
                client.delete_object(Bucket=self._bucket, Key=old)  # type: ignore[attr-defined]
        except Exception as exc:
            raise BackupError(f"R2 修剪失敗:{exc.__class__.__name__}") from exc
        # 本地同步修剪(刪最舊,只留 keep 份加密檔)
        local = sorted(self._dir.glob(f"{db_name}_*.dump.enc"))
        for old_path in local[:-keep] if keep > 0 else local:
            old_path.unlink(missing_ok=True)

    async def prune(self, *, db_name: str, keep: int) -> None:
        await asyncio.to_thread(self._prune, db_name, keep)
