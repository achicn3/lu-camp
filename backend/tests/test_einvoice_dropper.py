"""einvoice 原子拋檔器單元測試（純檔案、無 DB、與憑證無關）。

驗證：落入 UpCast/B2SSTORAGE/<msg>/SRC/、內容與 sha256 正確、不留暫存檔、非法檔名被擋。
"""

import hashlib
from pathlib import Path

import pytest

from app.modules.einvoice.dropper import EInvoiceDropper
from app.shared.enums import EInvoiceMessageType
from app.shared.exceptions import EInvoiceDropError


def test_drop_writes_into_src_dir_with_checksum(tmp_path: Path) -> None:
    dropper = EInvoiceDropper(tmp_path)
    payload = "<Invoice>測試</Invoice>".encode()

    result = dropper.drop(EInvoiceMessageType.F0401, "F0401-1-1.xml", payload)

    expected_dir = tmp_path / "UpCast" / "B2SSTORAGE" / "F0401" / "SRC"
    assert result.path == expected_dir / "F0401-1-1.xml"
    assert result.path.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()


def test_drop_leaves_no_temp_file(tmp_path: Path) -> None:
    dropper = EInvoiceDropper(tmp_path)
    dropper.drop(EInvoiceMessageType.G0401, "G0401-1-9.xml", b"<x/>")

    src_dir = dropper.src_dir(EInvoiceMessageType.G0401)
    names = sorted(p.name for p in src_dir.iterdir())
    assert names == ["G0401-1-9.xml"]  # 無 .tmp- 殘檔


def test_src_dir_uses_message_type(tmp_path: Path) -> None:
    dropper = EInvoiceDropper(tmp_path)
    assert dropper.src_dir(EInvoiceMessageType.F0501).name == "SRC"
    assert dropper.src_dir(EInvoiceMessageType.F0501).parent.name == "F0501"


@pytest.mark.parametrize("bad", ["../escape.xml", "a/b.xml", "..", ".", ""])
def test_drop_rejects_path_traversal(tmp_path: Path, bad: str) -> None:
    dropper = EInvoiceDropper(tmp_path)
    with pytest.raises(EInvoiceDropError):
        dropper.drop(EInvoiceMessageType.F0401, bad, b"<x/>")
