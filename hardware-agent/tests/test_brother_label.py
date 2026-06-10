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
