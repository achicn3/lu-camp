"""T16 真機狀態驅動（RealStatusProvider）單元測試。

全程用 mock：不依賴真實硬體。
測試覆蓋：
- Brother QL-810W TCP 在線（socket 成功）
- Brother QL-810W TCP 離線（socket 逾時/失敗）
- EPSON TM-T82III USB 在線（Usb.open 成功 + is_online True）
- EPSON TM-T82III USB 離線（Usb.open 拋例外）
- 錢櫃狀態依附 EPSON
- unsupported 欄位正確（Brother 標 B 級三項）
- driver="real"、validated_on_hardware=False

重要：每個測試必須同時 mock socket.create_connection 與 Usb，
避免任一路徑真的嘗試連接硬體（OSError/RuntimeError）。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from escpos.exceptions import DeviceNotFoundError  # type: ignore[import-untyped]
from usb.core import USBError  # type: ignore[import-untyped]

from agent.drivers.status_real import RealStatusProvider
from agent.interfaces import DeviceKind


def make_provider(**kwargs: Any) -> RealStatusProvider:
    """以預設參數建立 RealStatusProvider（不真的連硬體）。"""
    defaults: dict[str, Any] = {
        "brother_host": "192.168.1.100",
        "brother_port": 9100,
        "brother_timeout": 2.0,
        "epson_vendor_id": 0x04B8,
        "epson_product_id": 0x0202,
    }
    defaults.update(kwargs)
    return RealStatusProvider(**defaults)


@contextmanager
def _mock_all_hw(
    *,
    tcp_result: Any = None,
    tcp_error: Exception | None = None,
    epson_printer: MagicMock | None = None,
) -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """同時 mock TCP（socket.create_connection）與 USB（Usb 類別），
    確保測試不碰任何真實硬體。

    Args:
        tcp_result: socket.create_connection 的回傳值（預設 MagicMock）。
        tcp_error: 若提供，socket.create_connection 改拋此例外。
        epson_printer: Usb() 回傳的 mock 物件（預設在線的 MagicMock）。
    """
    if epson_printer is None:
        epson_printer = MagicMock()
        epson_printer.is_online.return_value = True

    if tcp_result is None and tcp_error is None:
        tcp_result = MagicMock()

    tcp_kwargs: dict[str, Any] = {}
    if tcp_error is not None:
        tcp_kwargs["side_effect"] = tcp_error
    else:
        tcp_kwargs["return_value"] = tcp_result

    with (
        patch("socket.create_connection", **tcp_kwargs) as mock_conn,
        patch("agent.drivers.status_real.Usb", return_value=epson_printer) as mock_usb,
    ):
        yield mock_conn, mock_usb


# ─────────────────────────────────────────────
# Brother QL-810W A 級 TCP 探測
# ─────────────────────────────────────────────


def test_brother_online_when_tcp_succeeds() -> None:
    """TCP 連線成功 → Brother 狀態 online=True、last_seen 有值。"""
    provider = make_provider()
    mock_sock = MagicMock()
    with _mock_all_hw(tcp_result=mock_sock) as (mock_conn, _):
        statuses = provider.poll()
    brother = next(s for s in statuses if s.kind == DeviceKind.LABEL_PRINTER)
    assert brother.online is True
    assert brother.last_seen is not None
    assert brother.driver == "real"
    assert brother.validated_on_hardware is False
    mock_conn.assert_called_once_with(("192.168.1.100", 9100), timeout=2.0)
    mock_sock.close.assert_called_once()


def test_brother_offline_when_tcp_timeout() -> None:
    """TCP 逾時 → Brother 狀態 online=False、last_seen=None。"""
    provider = make_provider()
    with _mock_all_hw(tcp_error=TimeoutError("timed out")):
        statuses = provider.poll()
    brother = next(s for s in statuses if s.kind == DeviceKind.LABEL_PRINTER)
    assert brother.online is False
    assert brother.last_seen is None


def test_brother_offline_when_tcp_connection_refused() -> None:
    """TCP 連線被拒 → Brother 狀態 online=False。"""
    provider = make_provider()
    with _mock_all_hw(tcp_error=OSError("connection refused")):
        statuses = provider.poll()
    brother = next(s for s in statuses if s.kind == DeviceKind.LABEL_PRINTER)
    assert brother.online is False


def test_brother_unsupported_contains_b_grade_keys() -> None:
    """Brother Wi-Fi 下 B 級三項（paper_out/cover_open/error）必須列入 unsupported。"""
    provider = make_provider()
    with _mock_all_hw():
        statuses = provider.poll()
    brother = next(s for s in statuses if s.kind == DeviceKind.LABEL_PRINTER)
    assert "paper_out" in brother.unsupported
    assert "cover_open" in brother.unsupported
    assert "error" in brother.unsupported


# ─────────────────────────────────────────────
# EPSON TM-T82III A 級 USB 探測
# ─────────────────────────────────────────────


def test_epson_online_when_usb_open_and_is_online_true() -> None:
    """USB 開啟成功且 is_online()=True → EPSON 狀態 online=True。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.is_online.return_value = True
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    epson = next(s for s in statuses if s.kind == DeviceKind.RECEIPT_PRINTER)
    assert epson.online is True
    assert epson.last_seen is not None
    assert epson.driver == "real"
    assert epson.validated_on_hardware is False
    mock_printer.open.assert_called_once()
    mock_printer.is_online.assert_called_once()
    mock_printer.close.assert_called_once()


def test_epson_offline_when_device_not_found() -> None:
    """裝置未連接（DeviceNotFoundError）→ 合理離線：online=False、probe_error=None。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.open.side_effect = DeviceNotFoundError("device not found")
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    epson = next(s for s in statuses if s.kind == DeviceKind.RECEIPT_PRINTER)
    assert epson.online is False
    assert epson.last_seen is None
    assert epson.probe_error is None  # 單純離線，不是錯誤


def test_epson_driver_error_is_surfaced_not_masked() -> None:
    """驅動/套件錯誤（如 pyusb 未裝→RuntimeError）→ online=False 但 probe_error 如實標示，
    **不可偽裝成單純離線**（使用者要求：不隱藏真實錯誤）。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.open.side_effect = RuntimeError("usb dependency not installed")
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    epson = next(s for s in statuses if s.kind == DeviceKind.RECEIPT_PRINTER)
    assert epson.online is False
    assert epson.probe_error is not None  # 錯誤被如實標示
    assert "usb dependency not installed" in epson.probe_error
    # 錢櫃依附 EPSON：也不可顯示成正常，且要帶出錯誤
    drawer = next(s for s in statuses if s.kind == DeviceKind.CASH_DRAWER)
    assert drawer.online is False
    assert drawer.probe_error is not None


def test_epson_usb_permission_error_surfaced_not_masked() -> None:
    """udev/libusb 權限錯誤被 escpos 包成 DeviceNotFoundError(__context__=USBError)：
    必須如實標 probe_error，**不可偽裝成單純離線**（最關鍵的部署誤設情境）。"""

    def _raise_wrapped(*_a: object, **_k: object) -> None:
        try:
            raise USBError("Access denied (insufficient permissions)")
        except USBError as e:
            raise DeviceNotFoundError("Unable to open USB printer: access denied") from e

    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.open.side_effect = _raise_wrapped
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    epson = next(s for s in statuses if s.kind == DeviceKind.RECEIPT_PRINTER)
    assert epson.online is False
    assert epson.probe_error is not None
    assert "存取錯誤" in epson.probe_error


def test_epson_offline_when_is_online_returns_false() -> None:
    """USB 開啟成功但 is_online()=False → EPSON 狀態 online=False。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.is_online.return_value = False
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    epson = next(s for s in statuses if s.kind == DeviceKind.RECEIPT_PRINTER)
    assert epson.online is False


def test_epson_survives_close_exception() -> None:
    """printer.close() 拋例外時不應傳播（finally 中靜默捕捉）。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.is_online.return_value = True
    mock_printer.close.side_effect = Exception("close failed")
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    # 不應因 close 例外而崩潰，EPSON 仍應在線
    epson = next(s for s in statuses if s.kind == DeviceKind.RECEIPT_PRINTER)
    assert epson.online is True


# ─────────────────────────────────────────────
# 錢櫃：依附 EPSON 推定
# ─────────────────────────────────────────────


def test_cash_drawer_online_when_epson_online() -> None:
    """EPSON 在線 → 錢櫃也應為 online=True（依附推定）。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.is_online.return_value = True
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    drawer = next(s for s in statuses if s.kind == DeviceKind.CASH_DRAWER)
    assert drawer.online is True
    assert drawer.driver == "real"
    assert drawer.validated_on_hardware is False


def test_cash_drawer_offline_when_epson_offline() -> None:
    """EPSON 離線 → 錢櫃也應為 online=False。"""
    provider = make_provider()
    mock_printer = MagicMock()
    mock_printer.open.side_effect = Exception("USB not found")
    with _mock_all_hw(epson_printer=mock_printer):
        statuses = provider.poll()
    drawer = next(s for s in statuses if s.kind == DeviceKind.CASH_DRAWER)
    assert drawer.online is False


# ─────────────────────────────────────────────
# 回傳清單結構
# ─────────────────────────────────────────────


def test_poll_returns_three_devices() -> None:
    """poll() 必須回傳三台裝置（Brother + EPSON + 錢櫃）。"""
    provider = make_provider()
    with _mock_all_hw():
        statuses = provider.poll()
    assert len(statuses) == 3
    kinds = {s.kind for s in statuses}
    assert DeviceKind.LABEL_PRINTER in kinds
    assert DeviceKind.RECEIPT_PRINTER in kinds
    assert DeviceKind.CASH_DRAWER in kinds


def test_real_provider_satisfies_protocol() -> None:
    """RealStatusProvider 必須滿足 DeviceStatusProvider Protocol（runtime check）。"""
    from agent.interfaces import DeviceStatusProvider

    provider = make_provider()
    assert isinstance(provider, DeviceStatusProvider)


def test_epson_usb_constructed_with_finite_timeout() -> None:
    """EPSON USB 必須帶有限 timeout（避免 is_online() 無限阻塞拖垮 /devices/status）。"""
    provider = make_provider(epson_timeout_ms=1500)
    with _mock_all_hw() as (_, mock_usb):
        provider.poll()
    _, kwargs = mock_usb.call_args
    assert kwargs["timeout"] == 1500
    assert kwargs["idVendor"] == 0x04B8
    assert kwargs["idProduct"] == 0x0202
