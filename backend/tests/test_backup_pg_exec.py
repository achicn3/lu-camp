"""`pg_exec` 的契約測試：同一組備份/還原流程，在兩種 Postgres 部署下各自組出正確的命令。

這層是純函式（只組 argv 與 env，不真的跑子程序），故為單元測試；真的跑 pg_dump 的驗證
在 `tests/integration/test_backup_service.py` 與實機演練。
"""

from pathlib import Path

import pytest

from app.modules.backup.pg_exec import DockerPgExec, LocalPgExec, build_pg_exec
from app.shared.exceptions import BackupError


def _docker() -> DockerPgExec:
    return DockerPgExec(docker_bin="docker", container="lu-camp-db-1", user="lucamp")


def _local() -> LocalPgExec:
    return LocalPgExec(
        bin_dir="/opt/homebrew/opt/postgresql@16/bin",
        host="127.0.0.1",
        port=5432,
        user="lucamp",
        password="s3cret",
    )


class TestDockerPgExec:
    def test_argv_前面接上_docker_exec_與容器名(self) -> None:
        assert _docker().argv("pg_dump", "-Fc", "-d", "lucamp") == [
            "docker",
            "exec",
            "lu-camp-db-1",
            "pg_dump",
            "-U",
            "lucamp",
            "-Fc",
            "-d",
            "lucamp",
        ]

    def test_不需要密碼_容器內以_superuser_連本機_socket(self) -> None:
        assert _docker().env() == {}

    def test_暫存檔落在容器內的_tmp_且沿用呼叫端給的檔名(self) -> None:
        """檔名由呼叫端指定：scheduler 啟動時以 lucamp_backup_* / lucamp_restore_* 的 glob
        掃除殘留的容器內整庫明文,前綴一改那道清理就會漏掉。"""
        remote = _docker().staged_path(
            name="lucamp_backup_lucamp_20260924.dump",
            local=Path("/var/backups/lucamp_20260924.dump"),
        )
        assert remote == "/tmp/lucamp_backup_lucamp_20260924.dump"

    def test_需要進出容器(self) -> None:
        assert _docker().needs_staging is True

    def test_複製出容器用_docker_exec_cat(self) -> None:
        assert _docker().copy_out_argv("/tmp/x.dump") == [
            "docker",
            "exec",
            "lu-camp-db-1",
            "cat",
            "/tmp/x.dump",
        ]

    def test_複製進容器用_docker_cp(self) -> None:
        assert _docker().copy_in_argv(Path("/var/x.dump"), "/tmp/x.dump") == [
            "docker",
            "cp",
            "/var/x.dump",
            "lu-camp-db-1:/tmp/x.dump",
        ]

    def test_清容器內明文(self) -> None:
        assert _docker().cleanup_argv("/tmp/x.dump") == [
            "docker",
            "exec",
            "lu-camp-db-1",
            "rm",
            "-f",
            "/tmp/x.dump",
        ]


class TestLocalPgExec:
    def test_argv_直接指到_bin_並帶上連線參數(self) -> None:
        assert _local().argv("pg_dump", "-Fc", "-d", "lucamp") == [
            "/opt/homebrew/opt/postgresql@16/bin/pg_dump",
            "-h",
            "127.0.0.1",
            "-p",
            "5432",
            "-U",
            "lucamp",
            "-Fc",
            "-d",
            "lucamp",
        ]

    def test_密碼走環境變數不進_argv(self) -> None:
        """與既有的 AES 口令同一原則：祕密不得出現在 argv／ps。"""
        exec_ = _local()
        assert exec_.env() == {"PGPASSWORD": "s3cret"}
        assert "s3cret" not in " ".join(exec_.argv("pg_dump", "-d", "lucamp"))

    def test_bin_dir_留空時就用_PATH_上的工具(self) -> None:
        exec_ = LocalPgExec(bin_dir="", host="127.0.0.1", port=5432, user="u", password="p")
        assert exec_.argv("pg_dump")[0] == "pg_dump"

    def test_暫存檔就是本機那個檔_不需要搬進搬出(self) -> None:
        local = Path("/var/backups/lucamp_20260924.dump")
        assert _local().staged_path(name="lucamp_backup_x.dump", local=local) == str(local)
        assert _local().needs_staging is False

    def test_沒有容器可清_回_None(self) -> None:
        """流程的 finally 本來就會 unlink 本機檔；這裡回 None 代表「不必另外清」。"""
        assert _local().cleanup_argv("/var/backups/x.dump") is None

    def test_不支援進出容器的呼叫(self) -> None:
        """needs_staging 為 False 時流程不該呼叫這兩支；真的叫到就是流程寫錯,要大聲失敗。"""
        with pytest.raises(BackupError):
            _local().copy_out_argv("/var/x.dump")
        with pytest.raises(BackupError):
            _local().copy_in_argv(Path("/var/x.dump"), "/var/x.dump")


class TestBuildPgExec:
    def test_docker_模式(self) -> None:
        exec_ = build_pg_exec(
            mode="docker",
            docker_bin="docker",
            container="lu-camp-db-1",
            pg_bin_dir="",
            host="127.0.0.1",
            port=5432,
            user="lucamp",
            password="p",
        )
        assert isinstance(exec_, DockerPgExec)

    def test_local_模式(self) -> None:
        exec_ = build_pg_exec(
            mode="local",
            docker_bin="docker",
            container="",
            pg_bin_dir="/usr/local/bin",
            host="127.0.0.1",
            port=5432,
            user="lucamp",
            password="p",
        )
        assert isinstance(exec_, LocalPgExec)

    def test_未知模式即失敗_不默默退回_docker(self) -> None:
        """打錯字退回預設值 = 備份跑去打不存在的容器、每天失敗；寧可啟動就擋下。"""
        with pytest.raises(BackupError):
            build_pg_exec(
                mode="Docker ",
                docker_bin="docker",
                container="c",
                pg_bin_dir="",
                host="h",
                port=5432,
                user="u",
                password="p",
            )

    def test_docker_模式缺容器名即失敗(self) -> None:
        with pytest.raises(BackupError):
            build_pg_exec(
                mode="docker",
                docker_bin="docker",
                container="  ",
                pg_bin_dir="",
                host="h",
                port=5432,
                user="u",
                password="p",
            )

    def test_local_模式缺密碼即失敗(self) -> None:
        """空密碼連 TCP 會卡在互動式提示直到逾時，不如啟動就講清楚。"""
        with pytest.raises(BackupError):
            build_pg_exec(
                mode="local",
                docker_bin="",
                container="",
                pg_bin_dir="/usr/local/bin",
                host="h",
                port=5432,
                user="u",
                password="",
            )
