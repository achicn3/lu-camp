"""真機狀態驅動（RealStatusProvider，網路 TCP 探測）單元測試。

全程用 mock（patch `socket.create_connection`）：不依賴真實硬體。
**兩台裝置皆網路**（Brother + EPSON），A 級統一 TCP 9100 探測。

註：原 T16 的 EPSON USB 測試（mock `Usb`、`DeviceNotFoundError`、`USBError` 權限、
USB finite timeout）已隨「EPSON 改網路版」的事實變更而移除——USB 情境不再存在，
非為消審查意見而弱化測試。以**等量的網路情境**取代並守住誠實原則：
- 連不上（refused/timeout/unreachable/DNS）→ 離線、`probe_error=None`。
- 非預期例外 → `online=False` 但 `probe_error` 如實記，不偽裝成離線。
"""

from __future__ import annotations

import socket
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.config import PrinterEndpoint
from agent.drivers.status_real import RealStatusProvider, real_status_provider_from_env
from agent.interfaces import DeviceKind, DeviceStatus, DeviceStatusProvider

_BROTHER_HOST = "192.168.0.41"
_EPSON_HOST = "192.168.0.42"
_KITCHEN_HOST = "192.168.0.46"


def make_provider(**kwargs: Any) -> RealStatusProvider:
    """以預設網路端點建立 RealStatusProvider（不真的連硬體）。"""
    defaults: dict[str, Any] = {
        "brother": PrinterEndpoint(host=_BROTHER_HOST, port=9100, timeout=2.0),
        "epson": PrinterEndpoint(host=_EPSON_HOST, port=9100, timeout=2.0),
    }
    defaults.update(kwargs)
    return RealStatusProvider(**defaults)


@contextmanager
def _mock_tcp(outcomes: Mapping[str, Any]) -> Generator[MagicMock, None, None]:
    """patch `socket.create_connection`，依目標 host 決定行為（確保不碰真實硬體）。

    outcomes: host -> 行為。
      - "ok"：連線成功，回傳支援 context manager 的 mock socket。
      - Exception 實例：connect 拋此例外。
    未列出的 host 預設連線被拒（避免漏網真連）。
    """

    def fake_create_connection(address: tuple[str, int], timeout: float | None = None) -> Any:
        outcome = outcomes.get(address[0], ConnectionRefusedError("refused"))
        if isinstance(outcome, Exception):
            raise outcome
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        return sock

    with patch("socket.create_connection", side_effect=fake_create_connection) as mock_conn:
        yield mock_conn


def _by_kind(statuses: list[DeviceStatus], kind: DeviceKind) -> DeviceStatus:
    return next(s for s in statuses if s.kind == kind)


# ─────────────────────────────────────────────
# EPSON-only（測 A：Brother 未接，brother=None）
# ─────────────────────────────────────────────


def test_epson_only_poll_omits_brother() -> None:
    """brother=None → poll 只回 EPSON 收據機 + 依附錢櫃，不列管 Brother 標籤機。"""
    provider = make_provider(brother=None)
    with _mock_tcp({_EPSON_HOST: "ok"}):
        statuses = provider.poll()
    kinds = [s.kind for s in statuses]
    assert DeviceKind.LABEL_PRINTER not in kinds
    assert kinds == [DeviceKind.RECEIPT_PRINTER, DeviceKind.CASH_DRAWER]
    drawer = _by_kind(statuses, DeviceKind.CASH_DRAWER)
    assert drawer.online is True  # 依附 EPSON，EPSON 在線則錢櫃在線


# ─────────────────────────────────────────────
# A 級在線（兩台皆 TCP 9100 探測）
# ─────────────────────────────────────────────


def test_brother_online_when_tcp_succeeds() -> None:
    """TCP 連線成功 → Brother online=True、last_seen 有值、無 probe_error。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    brother = _by_kind(statuses, DeviceKind.LABEL_PRINTER)
    assert brother.online is True
    assert brother.last_seen is not None
    assert brother.probe_error is None
    assert brother.driver == "real"
    assert brother.validated_on_hardware is True


def test_epson_online_when_tcp_succeeds() -> None:
    """TCP 連線成功 → EPSON online=True（不依賴 DLE EOT 狀態回應）。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    epson = _by_kind(statuses, DeviceKind.RECEIPT_PRINTER)
    assert epson.online is True
    assert epson.last_seen is not None
    assert epson.probe_error is None
    assert epson.driver == "real"
    assert epson.validated_on_hardware is True


# ─────────────────────────────────────────────
# 連不上 → 合理離線、probe_error=None（誠實原則）
# ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError("connection refused"),
        TimeoutError("timed out"),
        OSError("No route to host"),
        socket.gaierror("name resolution failed"),  # DNS 失敗亦為 OSError 子類
    ],
)
def test_epson_offline_when_unreachable_without_probe_error(error: Exception) -> None:
    """連線被拒/逾時/不通/DNS 失敗 → online=False、probe_error=None（單純離線、非錯誤）。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: error}):
        statuses = provider.poll()
    epson = _by_kind(statuses, DeviceKind.RECEIPT_PRINTER)
    assert epson.online is False
    assert epson.last_seen is None
    assert epson.probe_error is None


def test_brother_offline_when_unreachable() -> None:
    """Brother 逾時 → online=False、probe_error=None。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: TimeoutError("timed out"), _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    brother = _by_kind(statuses, DeviceKind.LABEL_PRINTER)
    assert brother.online is False
    assert brother.last_seen is None
    assert brother.probe_error is None


# ─────────────────────────────────────────────
# 非預期例外 → probe_error 如實記，不偽裝成離線（誠實原則）
# ─────────────────────────────────────────────


def test_epson_unexpected_error_is_surfaced_not_masked() -> None:
    """非 OSError 的非預期例外（設定/程式錯誤）→ online=False 但 probe_error 如實標示，
    **不可偽裝成單純離線**（ADR-010、使用者要求）。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: RuntimeError("unexpected boom")}):
        statuses = provider.poll()
    epson = _by_kind(statuses, DeviceKind.RECEIPT_PRINTER)
    assert epson.online is False
    assert epson.probe_error is not None
    assert "unexpected boom" in epson.probe_error
    # 錢櫃依附 EPSON：不可顯示成正常，且要帶出錯誤
    drawer = _by_kind(statuses, DeviceKind.CASH_DRAWER)
    assert drawer.online is False
    assert drawer.probe_error is not None


def test_brother_unexpected_error_is_surfaced_not_masked() -> None:
    """Brother 的非預期例外同樣如實標 probe_error，不偽裝成離線。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: RuntimeError("brother boom"), _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    brother = _by_kind(statuses, DeviceKind.LABEL_PRINTER)
    assert brother.online is False
    assert brother.probe_error is not None
    assert "brother boom" in brother.probe_error


# ─────────────────────────────────────────────
# 錢櫃：依附 EPSON 推定
# ─────────────────────────────────────────────


def test_cash_drawer_online_when_epson_online() -> None:
    """EPSON 在線 → 錢櫃 online=True（依附推定）。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    drawer = _by_kind(statuses, DeviceKind.CASH_DRAWER)
    assert drawer.online is True
    assert drawer.driver == "real"
    assert drawer.validated_on_hardware is True


def test_cash_drawer_offline_when_epson_offline() -> None:
    """EPSON 離線 → 錢櫃 online=False。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: ConnectionRefusedError("refused")}):
        statuses = provider.poll()
    drawer = _by_kind(statuses, DeviceKind.CASH_DRAWER)
    assert drawer.online is False


# ─────────────────────────────────────────────
# unsupported：B 級皆不做（產品裁示）
# ─────────────────────────────────────────────


def test_b_grade_keys_are_unsupported_on_all_devices() -> None:
    """兩台印表機 B 級（缺紙/上蓋/錯誤）皆 unsupported；錢櫃開關偵測 unsupported。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    brother = _by_kind(statuses, DeviceKind.LABEL_PRINTER)
    epson = _by_kind(statuses, DeviceKind.RECEIPT_PRINTER)
    drawer = _by_kind(statuses, DeviceKind.CASH_DRAWER)
    for key in ("paper_out", "cover_open", "error"):
        assert key in brother.unsupported
        assert key in epson.unsupported
    assert "drawer_open" in drawer.unsupported


# ─────────────────────────────────────────────
# 有限逾時 + 打到設定的 host/port（IP 不寫死）
# ─────────────────────────────────────────────


def test_tcp_probe_uses_finite_timeout() -> None:
    """探測必須帶有限 timeout（避免輪詢卡住 /devices/status）。"""
    provider = make_provider(
        brother=PrinterEndpoint(host=_BROTHER_HOST, timeout=1.5),
        epson=PrinterEndpoint(host=_EPSON_HOST, timeout=1.5),
    )
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}) as mock_conn:
        provider.poll()
    assert mock_conn.call_count == 2
    for call in mock_conn.call_args_list:
        assert call.kwargs["timeout"] == 1.5


def test_tcp_probe_targets_configured_host_and_port() -> None:
    """探測必須打到設定注入的 host/port（IP 來自設定、非寫死）。"""
    provider = make_provider(epson=PrinterEndpoint(host="10.0.0.9", port=9100, timeout=2.0))
    with _mock_tcp({_BROTHER_HOST: "ok", "10.0.0.9": "ok"}) as mock_conn:
        provider.poll()
    targets = {call.args[0] for call in mock_conn.call_args_list}
    assert ("10.0.0.9", 9100) in targets
    assert (_BROTHER_HOST, 9100) in targets


# ─────────────────────────────────────────────
# 回傳結構 / Protocol / 由環境變數建立
# ─────────────────────────────────────────────


def test_poll_returns_three_devices() -> None:
    """poll() 必須回傳三台裝置（Brother + EPSON + 錢櫃）。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    assert len(statuses) == 3
    kinds = {s.kind for s in statuses}
    assert kinds == {
        DeviceKind.LABEL_PRINTER,
        DeviceKind.RECEIPT_PRINTER,
        DeviceKind.CASH_DRAWER,
    }


def test_real_provider_satisfies_protocol() -> None:
    """RealStatusProvider 必須滿足 DeviceStatusProvider Protocol（runtime check）。"""
    provider = make_provider()
    assert isinstance(provider, DeviceStatusProvider)


def test_from_env_builds_provider_targeting_configured_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """real_status_provider_from_env 由環境變數建立、探測設定的 IP（非寫死）。"""
    monkeypatch.setenv("AGENT_BROTHER_HOST", _BROTHER_HOST)
    monkeypatch.setenv("AGENT_EPSON_HOST", _EPSON_HOST)
    provider = real_status_provider_from_env()
    assert isinstance(provider, RealStatusProvider)
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}) as mock_conn:
        provider.poll()
    targets = {call.args[0] for call in mock_conn.call_args_list}
    assert (_EPSON_HOST, 9100) in targets
    assert (_BROTHER_HOST, 9100) in targets


# ─────────────────────────────────────────────
# 出餐機（第二台 EPSON，選配；docs/35）
# ─────────────────────────────────────────────


def _kitchen_provider() -> RealStatusProvider:
    return make_provider(
        kitchen=PrinterEndpoint(host=_KITCHEN_HOST, port=9100, timeout=2.0)
    )


def test_poll_omits_kitchen_when_not_configured() -> None:
    """沒接第二台就**不得**憑空多出一台：狀態頁列出不存在的裝置比沒列還糟。"""
    provider = make_provider()
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok"}):
        statuses = provider.poll()
    assert [s.id for s in statuses] == ["brother-1", "epson-1", "drawer-1"]


def test_poll_includes_kitchen_when_configured() -> None:
    """接了第二台就必須列管——它多半在廚房，離線了沒人會看到。"""
    with _mock_tcp({_BROTHER_HOST: "ok", _EPSON_HOST: "ok", _KITCHEN_HOST: "ok"}):
        statuses = _kitchen_provider().poll()
    assert [s.id for s in statuses] == ["brother-1", "epson-1", "kitchen-1", "drawer-1"]
    kitchen = next(s for s in statuses if s.id == "kitchen-1")
    assert kitchen.kind is DeviceKind.RECEIPT_PRINTER
    assert kitchen.driver == "real"
    assert kitchen.online is True
    # 尚未實機驗證過，不得宣稱已驗證
    assert kitchen.validated_on_hardware is False


def test_kitchen_offline_is_reported_and_does_not_poison_the_others() -> None:
    """**這條是本功能的重點**：廚房那台掛了要看得出來，且不得連累櫃檯那台。"""
    with _mock_tcp(
        {_BROTHER_HOST: "ok", _EPSON_HOST: "ok", _KITCHEN_HOST: OSError("refused")}
    ):
        statuses = _kitchen_provider().poll()
    by_id = {s.id: s for s in statuses}
    assert by_id["kitchen-1"].online is False
    assert by_id["kitchen-1"].last_seen is None
    # 連線被拒＝合理離線，依既有契約 probe_error 為 None（只留給非預期例外）
    assert by_id["kitchen-1"].probe_error is None
    # 櫃檯那台與錢櫃不受影響
    assert by_id["epson-1"].online is True
    assert by_id["drawer-1"].online is True


def test_kitchen_unexpected_probe_error_is_not_disguised_as_offline() -> None:
    """設定/程式錯誤必須如實標示，**不得**偽裝成單純離線。

    廚房那台本來就常離線（沒開機），若把「IP 打錯」也顯示成一樣的離線，
    店家會一直以為只是沒開機而不去修設定。
    """
    with _mock_tcp(
        {_BROTHER_HOST: "ok", _EPSON_HOST: "ok", _KITCHEN_HOST: ValueError("bad config")}
    ):
        statuses = _kitchen_provider().poll()
    kitchen = next(s for s in statuses if s.id == "kitchen-1")
    assert kitchen.online is False
    assert kitchen.probe_error is not None
    assert _KITCHEN_HOST in kitchen.probe_error


def test_kitchen_probe_targets_its_own_host_not_the_receipt_printer() -> None:
    """必須探測**出餐機自己的** IP：沿用 EPSON 的位址會讓廚房那台掛了仍顯示正常。"""
    with _mock_tcp(
        {_BROTHER_HOST: "ok", _EPSON_HOST: "ok", _KITCHEN_HOST: "ok"}
    ) as mock_conn:
        _kitchen_provider().poll()
    targets = {call.args[0] for call in mock_conn.call_args_list}
    assert (_KITCHEN_HOST, 9100) in targets


def test_epson_offline_does_not_mark_kitchen_offline() -> None:
    """反向也要成立：櫃檯那台掛了，廚房那台仍應照實回報在線。"""
    with _mock_tcp(
        {_BROTHER_HOST: "ok", _EPSON_HOST: OSError("refused"), _KITCHEN_HOST: "ok"}
    ):
        statuses = _kitchen_provider().poll()
    by_id = {s.id: s for s in statuses}
    assert by_id["epson-1"].online is False
    assert by_id["drawer-1"].online is False  # 錢櫃依附 EPSON
    assert by_id["kitchen-1"].online is True
