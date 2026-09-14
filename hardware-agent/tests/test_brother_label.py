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
from agent.fakes import LabelCall

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


class TestBrandLine:
    """品牌獨立一行（裁示 2026-09-14）：有品牌才印，沒品牌整行不留白。"""

    def test_brand_occupies_its_own_line_above_the_name(self) -> None:
        """品牌自成一行：換品牌只改品牌帶，品名帶逐像素不變（沒有跟品名擠在一起）。"""
        from agent.drivers.brother_label import _SINGLE_BRANDED

        a = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand="Snow Peak")
        b = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand="Coleman")
        assert a.height == b.height == LABEL_HEIGHT_DOTS
        assert a.width == b.width  # 兩個品牌都比品名/條碼窄，不影響長度

        name_top = _SINGLE_BRANDED["name_top"]
        brand_band = range(_SINGLE_BRANDED["brand_top"], name_top)
        name_band = range(name_top, _SINGLE_BRANDED["barcode_top"])

        def rows(img: object, band: range) -> list[list[int]]:
            width: int = img.width  # type: ignore[attr-defined]
            get = img.getpixel  # type: ignore[attr-defined]
            return [[get((x, y)) for x in range(width)] for y in band]

        # 小字經抗鋸齒後不一定有純黑像素，用灰階門檻判斷「有沒有著墨」。
        assert any(px < 200 for row in rows(a, brand_band) for px in row), "有品牌就要印出品牌行"
        assert rows(a, brand_band) != rows(b, brand_band), "不同品牌，品牌行要不同"
        assert rows(a, name_band) == rows(b, name_band), "品名帶不得受品牌影響"

    def test_blank_brand_prints_nothing_extra(self) -> None:
        """沒品牌＝整行不印（不是印空字串後留一條白帶）：與不給品牌的輸出逐像素相同。"""
        plain = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        for empty in (None, "", "   "):
            same = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand=empty)
            assert same.tobytes() == plain.tobytes(), f"brand={empty!r} 不應改變版面"

    def test_branded_layout_keeps_vertical_barcode(self) -> None:
        """品牌行把條碼帶往下推之後，bar 仍是垂直線且存在（沒被品牌行蓋掉）。"""
        from agent.drivers.brother_label import _SINGLE_BRANDED

        image = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand="Snow Peak")
        top = _SINGLE_BRANDED["barcode_top"]
        row_a = [image.getpixel((x, top + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, top + 60)) for x in range(image.width)]
        assert row_a == row_b
        assert 0 in row_a

    def test_branded_wrapped_layout_keeps_vertical_barcode(self) -> None:
        from agent.drivers.brother_label import _WRAPPED_BRANDED

        image = build_label_image(
            "ITM-0003", "雪峰 Amenity Dome M 五人帳篷二手極新", 12800, _FONT, brand="Snow Peak"
        )
        top = _WRAPPED_BRANDED["barcode_top"]
        row_a = [image.getpixel((x, top + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, top + 60)) for x in range(image.width)]
        assert row_a == row_b
        assert 0 in row_a

    def test_long_brand_is_truncated_not_widened(self) -> None:
        """過長品牌截斷加「…」：截斷點之後的差異不影響輸出，且不撐破長度上限。"""
        from agent.drivers.brother_label import MAX_LABEL_WIDTH_DOTS

        base = "超長品牌名稱測試用文字" * 4
        a = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand=base + "Ａ")
        b = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand=base + "Ｂ")
        assert a.tobytes() == b.tobytes()
        assert a.width <= MAX_LABEL_WIDTH_DOTS


class TestConditionMarker:
    """全新／二手標示（裁示 2026-09-14）：與價格同一行靠右，客人一眼看得到。"""

    def test_condition_is_rendered(self) -> None:
        plain = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        marked = build_label_image("ITM-0001", "帳篷", 1000, _FONT, condition="二手")
        assert marked.height == LABEL_HEIGHT_DOTS
        assert _dark_pixels(marked) > _dark_pixels(plain)

    def test_blank_condition_prints_nothing_extra(self) -> None:
        plain = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        for empty in (None, "", "  "):
            same = build_label_image("ITM-0001", "帳篷", 1000, _FONT, condition=empty)
            assert same.tobytes() == plain.tobytes(), f"condition={empty!r} 不應改變版面"

    def test_different_conditions_render_differently(self) -> None:
        used = build_label_image("ITM-0001", "帳篷", 1000, _FONT, condition="二手")
        new = build_label_image("ITM-0001", "帳篷", 1000, _FONT, condition="全新")
        assert used.tobytes() != new.tobytes()

    def test_condition_sits_right_of_price_with_a_gap(self) -> None:
        """價格靠左、標示靠右：同一行、之間有一整段留白、且標示貼著右邊界。

        （不用「圖片中線」切左右——「NT$1000」本身就跨過中線。改用價格行內最長的
        一段空白當分界，那才是版面真正的間隙。）
        """
        from agent.drivers.brother_label import _CONDITION_GAP_PX, _MARGIN, _SINGLE

        image = build_label_image("ITM-0001", "帳篷", 1000, _FONT, condition="二手")
        top = _SINGLE["price_top"]
        dark = {
            x
            for y in range(top, min(image.height, top + _SINGLE["price_font_px"]))
            for x in range(image.width)
            if image.getpixel((x, y)) == 0
        }
        assert dark, "價格行應有著墨"

        blank_runs: list[tuple[int, int]] = []
        run_start: int | None = None
        for x in range(min(dark), max(dark) + 1):
            if x in dark:
                if run_start is not None:
                    blank_runs.append((run_start, x))
                    run_start = None
            elif run_start is None:
                run_start = x
        assert blank_runs, "價格與標示之間要有留白"
        gap_start, gap_end = max(blank_runs, key=lambda r: r[1] - r[0])
        assert gap_end - gap_start >= _CONDITION_GAP_PX, "間隙要夠寬，不能黏在一起"
        assert any(x > gap_end for x in dark), "間隙右側要有標示"
        assert max(dark) <= image.width - _MARGIN, "標示不得超出右邊界"
        assert max(dark) > image.width - _MARGIN - 120, "標示要靠右對齊"

    def test_overwide_condition_is_rejected_not_oversized(self) -> None:
        with pytest.raises(LabelContentTooWide):
            build_label_image("ITM-0001", "帳篷", 1000, _FONT, condition="二手" * 40)


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


class TestLabelRequestPassesBrandAndCondition:
    async def test_brand_and_condition_reach_the_printer(self) -> None:
        """/print/label 的品牌與標示要原樣交到驅動，不可在路由層被吃掉。"""
        import httpx

        from agent.devices import AgentDevices, default_fake_devices
        from agent.fakes import FakeLabelPrinter
        from agent.main import create_app

        label_printer = FakeLabelPrinter()
        base = default_fake_devices()
        app = create_app(
            AgentDevices(
                label_printer=label_printer,
                receipt_printer=base.receipt_printer,
                cash_drawer=base.cash_drawer,
                status_provider=base.status_provider,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/print/label",
                json={
                    "code": "ITM-0001",
                    "name": "帳篷",
                    "price": 1000,
                    "brand": "Snow Peak",
                    "condition": "二手",
                },
            )
        assert resp.status_code == 200
        assert label_printer.labels == [LabelCall("ITM-0001", "帳篷", 1000, "Snow Peak", "二手")]

    async def test_omitting_brand_and_condition_still_works(self) -> None:
        """舊版前端（只送 code/name/price）不得因為新欄位而壞掉。"""
        import httpx

        from agent.devices import AgentDevices, default_fake_devices
        from agent.fakes import FakeLabelPrinter
        from agent.main import create_app

        label_printer = FakeLabelPrinter()
        base = default_fake_devices()
        app = create_app(
            AgentDevices(
                label_printer=label_printer,
                receipt_printer=base.receipt_printer,
                cash_drawer=base.cash_drawer,
                status_provider=base.status_provider,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/print/label", json={"code": "ITM-0001", "name": "帳篷", "price": 1000}
            )
        assert resp.status_code == 200
        assert label_printer.labels == [LabelCall("ITM-0001", "帳篷", 1000, None, None)]


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
