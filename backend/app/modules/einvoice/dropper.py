"""Turnkey 拋檔器：把 MIG XML **原子地**落入 Turnkey 的存證來源目錄。

流程（docs/14 §5.3、docs/18 §7.2）：於**同一檔案系統**寫暫存檔 → flush + fsync →
`os.replace` 原子改名進 `UpCast/B2SSTORAGE/<訊息類型>/SRC/<檔名>.xml`。Turnkey 排程
自行撿走上傳。**絕不可讓 Turnkey 看到寫一半的檔**，故先寫暫存再原子改名。

本模組為純檔案操作、無 DB、無網路、與憑證無關，可完全離線單元測試。回傳落檔路徑與
內容 sha256，供佇列列記錄與對帳（每筆交付都有 checksum）。
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.shared.enums import EInvoiceMessageType
from app.shared.exceptions import EInvoiceDropError

# Turnkey 拋檔根目錄下的固定子路徑（MIG 4.0+ 已把 B2B/B2C 存證整併為 B2S）。
_DROP_SUBPATH = ("UpCast", "B2SSTORAGE")
_SRC_DIRNAME = "SRC"


@dataclass(frozen=True)
class DropResult:
    """一次成功拋檔的結果：最終落檔路徑與內容 sha256（十六進位）。"""

    path: Path
    sha256: str


class EInvoiceDropper:
    """把 XML bytes 原子落入 Turnkey `<root>/UpCast/B2SSTORAGE/<msg>/SRC/`。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def src_dir(self, message_type: EInvoiceMessageType) -> Path:
        """該訊息類型的 SRC 拋檔目錄（不建立）。"""
        return self._root.joinpath(*_DROP_SUBPATH, message_type.value, _SRC_DIRNAME)

    def drop(self, message_type: EInvoiceMessageType, filename: str, payload: bytes) -> DropResult:
        """原子落檔並回傳路徑與 sha256。

        `filename` 僅接受單純檔名（不含目錄分隔或 `..`），避免路徑逃逸把檔案寫到
        SRC 目錄之外。內容以 UTF-8 XML bytes 由呼叫端（序列化器）提供。
        """
        if not filename or filename != Path(filename).name or filename in {".", ".."}:
            raise EInvoiceDropError("拋檔檔名不合法（不可含路徑分隔或 ..）")

        target_dir = self.src_dir(message_type)
        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = target_dir / filename

        digest = hashlib.sha256(payload).hexdigest()

        # 於同目錄寫暫存檔 → fsync → 原子改名（同檔案系統，os.replace 為原子）。
        fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".xml")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, final_path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise EInvoiceDropError("拋檔寫入失敗") from exc

        return DropResult(path=final_path, sha256=digest)
