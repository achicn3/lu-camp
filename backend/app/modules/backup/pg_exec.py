"""Postgres 命令列工具（pg_dump／pg_restore／psql）的執行方式抽象。

備份與還原都要跑這些工具,但「這台機器的 Postgres 在哪」有兩種部署:

- `DockerPgExec`：Postgres 跑在 docker 容器裡（docs/28 的原始流程）。工具在容器內,
  dump 檔要先落在容器的 /tmp、再搬進搬出,且**一定要把容器內的整庫明文清掉**。
- `LocalPgExec`：Postgres 原生裝在本機（店內 MacBook 的 Homebrew postgresql@16）。
  工具直接跑、走 TCP 連線,dump 直接寫在本機路徑——**不需要進出容器,也沒有容器明文要清**。

把兩者的差異全部收斂在這個檔,`backend.py`／`restore.py` 的流程才只有一份,
不會出現兩套各自維護的 dump 步驟。

> 為什麼需要 local 模式：店內正式機沒有 docker（資料庫是 Homebrew 原生安裝），
> 原本寫死的 `docker exec` 讓自動備份自 2026-09-18 起 138 次全數失敗、從未成功過。
"""

from dataclasses import dataclass
from pathlib import Path

from app.shared.exceptions import BackupError

_MODE_DOCKER = "docker"
_MODE_LOCAL = "local"


@dataclass(frozen=True)
class DockerPgExec:
    """Postgres 在 docker 容器內：一切經 `docker exec`,以容器內 superuser 連本機 socket。"""

    docker_bin: str
    container: str
    user: str

    @property
    def needs_staging(self) -> bool:
        """容器內外是兩個檔案系統,dump 必須搬進搬出。"""
        return True

    def argv(self, tool: str, *args: str) -> list[str]:
        return [self.docker_bin, "exec", self.container, tool, "-U", self.user, *args]

    def env(self) -> dict[str, str]:
        """容器內走本機 socket,不需要密碼。"""
        return {}

    def staged_path(self, *, name: str, local: Path) -> str:
        """工具看得到的路徑：容器的 /tmp。

        檔名由呼叫端指定而非沿用本機檔名——`scheduler.sweep_container_plaintext_on_startup`
        以 `lucamp_backup_*` / `lucamp_restore_*` 的 glob 掃除崩潰殘留的容器內整庫明文,
        前綴一改那道 PII 清理就會漏掉。
        """
        return f"/tmp/{name}"

    def copy_out_argv(self, staged: str) -> list[str]:
        return [self.docker_bin, "exec", self.container, "cat", staged]

    def copy_in_argv(self, local: Path, staged: str) -> list[str]:
        return [self.docker_bin, "cp", str(local), f"{self.container}:{staged}"]

    def cleanup_argv(self, staged: str) -> list[str] | None:
        """容器內的整庫明文一定要清（含 PII）。"""
        return [self.docker_bin, "exec", self.container, "rm", "-f", staged]


@dataclass(frozen=True)
class LocalPgExec:
    """Postgres 原生裝在本機：工具直接跑,走 TCP,密碼以 PGPASSWORD 注入（不進 argv）。"""

    bin_dir: str
    host: str
    port: int
    user: str
    password: str

    @property
    def needs_staging(self) -> bool:
        """工具與 dump 檔在同一個檔案系統,不必搬。"""
        return False

    def argv(self, tool: str, *args: str) -> list[str]:
        binary = str(Path(self.bin_dir) / tool) if self.bin_dir else tool
        return [binary, "-h", self.host, "-p", str(self.port), "-U", self.user, *args]

    def env(self) -> dict[str, str]:
        """密碼走環境變數,與 AES 口令同一原則：祕密不出現在 argv／ps。"""
        return {"PGPASSWORD": self.password}

    def staged_path(self, *, name: str, local: Path) -> str:
        """本機模式沒有第二個檔案系統：工具直接寫那個本機檔。"""
        return str(local)

    def _no_staging(self) -> BackupError:
        return BackupError("本機 Postgres 模式不需要進出容器,流程呼叫有誤")

    def copy_out_argv(self, staged: str) -> list[str]:
        raise self._no_staging()

    def copy_in_argv(self, local: Path, staged: str) -> list[str]:
        raise self._no_staging()

    def cleanup_argv(self, staged: str) -> list[str] | None:
        """沒有容器暫存要清；本機檔由流程的 finally 自己 unlink。"""
        return None


PgExec = DockerPgExec | LocalPgExec


def build_pg_exec(
    *,
    mode: str,
    docker_bin: str,
    container: str,
    pg_bin_dir: str,
    host: str,
    port: int,
    user: str,
    password: str,
) -> PgExec:
    """依設定建出對應的執行方式；設定不完整就**當場失敗**,不默默退回預設。

    預設值退回是備份系統最糟的失敗方式：看起來有設定、每天靜默失敗,等到要還原才發現沒有備份。
    """
    if mode == _MODE_DOCKER:
        if not container.strip():
            raise BackupError("備份設定不完整：docker 模式必須指定資料庫容器名稱")
        return DockerPgExec(docker_bin=docker_bin, container=container, user=user)
    if mode == _MODE_LOCAL:
        if not password:
            raise BackupError("備份設定不完整：本機 Postgres 模式必須提供資料庫密碼")
        return LocalPgExec(bin_dir=pg_bin_dir, host=host, port=port, user=user, password=password)
    raise BackupError(f"備份設定不完整：不認得的 Postgres 執行模式 {mode!r}")
