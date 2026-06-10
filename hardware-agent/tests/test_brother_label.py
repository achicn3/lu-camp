"""Brother QL-810W 標籤真機驅動測試（免實機：注入假傳輸、影像以像素斷言）。

標籤紙為 DK-22210（29mm 連續，brother_ql label "29"，可印寬 306 dots @300dpi）。
版面為橫式（高固定 306）：品名 / Code128 條碼 + 識別碼 / NT$ 價格；送印前轉直向
（寬 306），由 brother_ql `convert` 轉光柵指令。中文以 repo 內建 Noto Sans TC 渲染。
"""

from __future__ import annotations

import pytest

from agent.config import PrinterEndpoint, label_font_path_from_env
from agent.drivers.brother_label import (
    LABEL_HEIGHT_DOTS,
    BrotherLabelPrinter,
    LabelContentTooWide,
    build_label_image,
)
from agent.errors import DeviceOffline, DeviceTimeout

_EP = PrinterEndpoint(host="192.0.2.45")  # TEST-NET 假位址；真機 IP 由環境提供
_FONT = label_font_path_from_env()  # 預設 repo 內建字型


def _dark_pixels(image: object) -> int:
    histogram: list[int] = image.histogram()  # type: ignore[attr-defined]
    return histogram[0]  # 'L' 模式像素值 0（黑）的數量


class TestBuildLabelImage:
    def test_height_is_printable_width_of_29mm_tape(self) -> None:
        image = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        assert image.height == LABEL_HEIGHT_DOTS == 306

    def test_renders_content_pixels(self) -> None:
        image = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        assert _dark_pixels(image) > 1000  # 品名/條碼/價格都有著墨

    def test_width_grows_with_longer_name(self) -> None:
        short = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        long = build_label_image("ITM-0001", "防水雙人露營帳篷豪華版", 1000, _FONT)
        assert long.width > short.width

    def test_barcode_bars_are_vertical_and_present(self) -> None:
        """條碼帶內的 bar 為垂直線：帶內任兩列的黑白樣式一致、且確實有 bar。"""
        image = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        from agent.drivers.brother_label import _BARCODE_TOP

        row_a = [image.getpixel((x, _BARCODE_TOP + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, _BARCODE_TOP + 60)) for x in range(image.width)]
        assert row_a == row_b  # 垂直 bar：不同高度的列樣式相同
        assert 0 in row_a  # 有黑 bar

    def test_long_name_wraps_and_caps_width(self) -> None:
        """長品名：降字級換行（最多三行）、標籤長度不超過 ≈40mm 上限
        （與短品名單行標籤同級大小，使用者裁示 2026-06-11）。"""
        from agent.drivers.brother_label import MAX_LABEL_WIDTH_DOTS

        image = build_label_image(
            "ITM-0003", "Snow Peak 雪峰 Amenity Dome M 五人帳篷二手極新", 12800, _FONT
        )
        assert image.width <= MAX_LABEL_WIDTH_DOTS
        assert MAX_LABEL_WIDTH_DOTS / 300 * 25.4 <= 41.0  # 上限 ≈ 40mm

    def test_wrapped_layout_keeps_vertical_barcode(self) -> None:
        """兩行版面的條碼帶位置下移後，bar 仍為垂直線且存在。"""
        from agent.drivers.brother_label import _BARCODE_TOP_WRAPPED

        image = build_label_image(
            "ITM-0003", "Snow Peak 雪峰 Amenity Dome M 五人帳篷二手極新", 12800, _FONT
        )
        row_a = [image.getpixel((x, _BARCODE_TOP_WRAPPED + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, _BARCODE_TOP_WRAPPED + 60)) for x in range(image.width)]
        assert row_a == row_b
        assert 0 in row_a

    def test_overlong_code_is_rejected_not_oversized(self) -> None:
        """識別碼過長（條碼在最小窄條下仍超出長度上限）→ 如實拒印（條碼不可截斷，
        截斷會印出掃起來是錯的碼）；不得默默印出超過上限的長標籤。"""
        with pytest.raises(LabelContentTooWide):
            build_label_image("X" * 64, "帳篷", 1000, _FONT)

    def test_wide_price_is_rejected_not_oversized(self) -> None:
        with pytest.raises(LabelContentTooWide):
            build_label_image("ITM-0001", "帳篷", 10**30, _FONT)

    def test_overlong_name_truncated_with_stable_output(self) -> None:
        """超過兩行的品名截斷加「…」：截斷點之後的內容差異不影響輸出（確實截斷）。"""
        base = "防水露營帳篷豪華版" * 5  # 45 字，兩行裝不下
        a = build_label_image("ITM-0001", base + "Ａ", 100, _FONT)
        b = build_label_image("ITM-0001", base + "Ｂ", 100, _FONT)
        assert a.tobytes() == b.tobytes()

    def test_different_codes_render_different_barcodes(self) -> None:
        from agent.drivers.brother_label import _BARCODE_TOP

        a = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        b = build_label_image("LOT-9999", "帳篷", 1000, _FONT)
        row_a = [a.getpixel((x, _BARCODE_TOP + 10)) for x in range(min(a.width, b.width))]
        row_b = [b.getpixel((x, _BARCODE_TOP + 10)) for x in range(min(a.width, b.width))]
        assert row_a != row_b


class _SendRecorder:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[PrinterEndpoint, bytes]] = []
        self.exc = exc

    def __call__(self, endpoint: PrinterEndpoint, instructions: bytes) -> None:
        if self.exc is not None:
            raise self.exc
        self.calls.append((endpoint, instructions))


class TestLabelTooWideHttpMapping:
    async def test_print_label_with_overlong_code_returns_422(self) -> None:
        """經 /print/label 真機驅動路徑：內容超寬 → 422（不送印、不印超長標籤）。"""
        import httpx

        from agent.devices import AgentDevices, default_fake_devices
        from agent.main import create_app

        recorder = _SendRecorder()
        base = default_fake_devices()
        app = create_app(
            AgentDevices(
                label_printer=BrotherLabelPrinter(_EP, font_path=_FONT, sender=recorder),
                receipt_printer=base.receipt_printer,
                cash_drawer=base.cash_drawer,
                status_provider=base.status_provider,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/print/label", json={"code": "X" * 64, "name": "帳篷", "price": 1000}
            )
        assert resp.status_code == 422
        assert resp.json()["error"] == "LabelContentTooWide"
        assert recorder.calls == []  # 未送任何位元組到印表機


class TestBrotherLabelPrinter:
    def test_sends_raster_instructions_to_endpoint(self) -> None:
        recorder = _SendRecorder()
        printer = BrotherLabelPrinter(_EP, font_path=_FONT, sender=recorder)
        printer.print_label("ITM-0001", "帳篷", 1000)
        assert len(recorder.calls) == 1
        endpoint, instructions = recorder.calls[0]
        assert endpoint == _EP
        assert isinstance(instructions, bytes) and len(instructions) > 1000  # 光柵指令非空

    def test_timeout_maps_to_device_timeout(self) -> None:
        printer = BrotherLabelPrinter(
            _EP, font_path=_FONT, sender=_SendRecorder(exc=TimeoutError("send timeout"))
        )
        with pytest.raises(DeviceTimeout):
            printer.print_label("ITM-0001", "帳篷", 1000)

    def test_connection_refused_maps_to_device_offline(self) -> None:
        printer = BrotherLabelPrinter(
            _EP, font_path=_FONT, sender=_SendRecorder(exc=ConnectionRefusedError("refused"))
        )
        with pytest.raises(DeviceOffline):
            printer.print_label("ITM-0001", "帳篷", 1000)
