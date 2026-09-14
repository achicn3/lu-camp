"""Brother QL-810W 標籤真機驅動測試（免實機：注入假傳輸、影像以像素斷言）。

標籤紙為 DK-22210（29mm 連續，brother_ql label "29"，可印寬 306 dots @300dpi）。
版面為橫式（高固定 306）：品名 / Code128 條碼 + 識別碼 / NT$ 價格；送印前轉直向
（寬 306），由 brother_ql `convert` 轉光柵指令。中文以 repo 內建 Noto Sans TC 渲染。
"""

from __future__ import annotations

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

import agent.drivers.brother_label as label_driver
from agent.config import PrinterEndpoint, label_font_path_from_env
from agent.devices import AgentDevices, default_fake_devices
from agent.drivers.brother_label import (
    _BARCODE_TOP,
    _BARCODE_TOP_WRAPPED,
    _CONDITION_GAP_PX,
    _MARGIN,
    _SINGLE,
    _SINGLE_BRANDED,
    _TEXT_WIDTH_DOTS,
    _WRAPPED,
    _WRAPPED_BRANDED,
    LABEL_HEIGHT_DOTS,
    MAX_LABEL_WIDTH_DOTS,
    BrotherLabelPrinter,
    LabelContentTooWide,
    _Bands,
    build_label_image,
)
from agent.errors import DeviceOffline, DeviceTimeout
from agent.fakes import FakeLabelPrinter, LabelCall
from agent.main import create_app

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

        row_a = [image.getpixel((x, _BARCODE_TOP + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, _BARCODE_TOP + 60)) for x in range(image.width)]
        assert row_a == row_b  # 垂直 bar：不同高度的列樣式相同
        assert 0 in row_a  # 有黑 bar

    def test_long_name_wraps_and_caps_width(self) -> None:
        """長品名：降字級換行（最多三行）、標籤長度不超過 ≈40mm
        （與短品名單行標籤同級大小，使用者裁示 2026-06-11）。

        對 `_TEXT_WIDTH_DOTS` 斷言而非整體長度上限：2026-09-14 放寬的是**條碼**撐得到的
        寬度，長品名不得變成長條這條規則沒有變。
        """
        image = build_label_image(
            "ITM-0003", "Snow Peak 雪峰 Amenity Dome M 五人帳篷二手極新", 12800, _FONT
        )
        assert image.width <= _TEXT_WIDTH_DOTS
        assert _TEXT_WIDTH_DOTS / 300 * 25.4 <= 41.0  # 品名撐出來的長度仍 ≈ 40mm

    def test_wrapped_layout_keeps_vertical_barcode(self) -> None:
        """兩行版面的條碼帶位置下移後，bar 仍為垂直線且存在。"""

        image = build_label_image(
            "ITM-0003", "Snow Peak 雪峰 Amenity Dome M 五人帳篷二手極新", 12800, _FONT
        )
        row_a = [image.getpixel((x, _BARCODE_TOP_WRAPPED + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, _BARCODE_TOP_WRAPPED + 60)) for x in range(image.width)]
        assert row_a == row_b
        assert 0 in row_a

    def test_system_generated_sku_fits(self) -> None:
        """系統自動編號（`AUTO-` ＋ 12 碼 hex，共 17 字）一律要印得出來。

        採購頁建一般商品時商品編號可留白、由系統自動編；若這種編號印不出標籤，
        等於「一般商品補印」對所有沒手填編號的商品都是死的（裁示 2026-09-14 放寬長度）。
        17 字的 Code128 實測需 406–516 dots，隨字元組合浮動，故整個範圍都要蓋到。
        """
        worst = "AUTO-ABCDEFABCDEF"  # 全字母＝Code128 最不緊湊的編法
        for brand, condition in ((None, None), ("Snow Peak", "全新")):
            image = build_label_image(
                worst, "營繩 4mm", 180, _FONT, brand=brand, condition=condition
            )
            assert image.width <= MAX_LABEL_WIDTH_DOTS

    def test_text_wrapping_is_not_loosened_by_the_wider_cap(self) -> None:
        """放寬長度上限**只讓條碼撐得更寬**，不得讓品名變晚換行。

        品名換行基準綁在 `_TEXT_WIDTH_DOTS`（維持原值），不是綁在放寬後的長度上限；
        綁錯的話，原本會換行的中長品名會改成單行大字，既有標籤的版面就跟著變了。
        """
        assert _TEXT_WIDTH_DOTS < MAX_LABEL_WIDTH_DOTS  # 真的是兩個不同的數
        probe = ImageDraw.Draw(Image.new("L", (1, 1), 255))
        font = ImageFont.truetype(_FONT, _SINGLE["name_font_px"])
        # 找一個寬度落在「舊基準塞不下、放寬後的上限塞得下」之間的品名
        name = "防"
        while probe.textlength(name, font=font) <= _TEXT_WIDTH_DOTS - 2 * _MARGIN:
            name += "防"
        assert probe.textlength(name, font=font) <= MAX_LABEL_WIDTH_DOTS - 2 * _MARGIN
        image = build_label_image("ITM-0001", name, 100, _FONT)
        # 有換行才會用到 _WRAPPED 的條碼位置；沒換行代表基準被放寬污染了
        row_a = [image.getpixel((x, _WRAPPED["barcode_top"] + 10)) for x in range(image.width)]
        assert 0 in row_a, "這個長度的品名仍應換行（條碼落在換行版的位置）"

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

        a = build_label_image("ITM-0001", "帳篷", 1000, _FONT)
        b = build_label_image("LOT-9999", "帳篷", 1000, _FONT)
        row_a = [a.getpixel((x, _BARCODE_TOP + 10)) for x in range(min(a.width, b.width))]
        row_b = [b.getpixel((x, _BARCODE_TOP + 10)) for x in range(min(a.width, b.width))]
        assert row_a != row_b


class TestBrandLine:
    """品牌獨立一行（裁示 2026-09-14）：有品牌才印，沒品牌整行不留白。"""

    def test_brand_occupies_its_own_line_above_the_name(self) -> None:
        """品牌自成一行：換品牌只改品牌帶，品名帶逐像素不變（沒有跟品名擠在一起）。"""

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

        image = build_label_image("ITM-0001", "帳篷", 1000, _FONT, brand="Snow Peak")
        top = _SINGLE_BRANDED["barcode_top"]
        row_a = [image.getpixel((x, top + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, top + 60)) for x in range(image.width)]
        assert row_a == row_b
        assert 0 in row_a

    def test_branded_wrapped_layout_keeps_vertical_barcode(self) -> None:

        image = build_label_image(
            "ITM-0003", "雪峰 Amenity Dome M 五人帳篷二手極新", 12800, _FONT, brand="Snow Peak"
        )
        top = _WRAPPED_BRANDED["barcode_top"]
        row_a = [image.getpixel((x, top + 10)) for x in range(image.width)]
        row_b = [image.getpixel((x, top + 60)) for x in range(image.width)]
        assert row_a == row_b
        assert 0 in row_a

    @pytest.mark.parametrize(
        ("label", "name", "brand", "bands"),
        [
            ("單行無品牌", "Gypsy", None, _SINGLE),
            ("換行無品牌", "gjpqy " * 12, None, _WRAPPED),
            ("單行有品牌", "Gypsy", "Jpqgy Peak", _SINGLE_BRANDED),
            ("換行有品牌", "gjpqy " * 12, "Jpqgy Peak", _WRAPPED_BRANDED),
        ],
    )
    def test_no_variant_lets_text_bleed_into_the_barcode_band(
        self, label: str, name: str, brand: str | None, bands: _Bands
    ) -> None:
        """四種版面的條碼帶內，**每一列**都必須一模一樣。

        只抽驗兩列漏得掉降部滲墨：品名的 p/y/g/j 尾巴垂進條碼帶，bar 上緣多出墨點，
        掃描器就可能讀錯。這裡整帶逐列比對。無品牌那兩種原本各滲 7／4 列（main 既有，
        2026-09-15 一併修）——既然已為同類缺陷（錢字號被削）動過這兩個版面，
        就沒有理由只修一半。
        """
        image = build_label_image("ITM-0001", name, 1000, _FONT, brand=brand, condition="二手")
        top, height = bands["barcode_top"], bands["barcode_height"]
        rows = {
            tuple(image.getpixel((x, y)) for x in range(image.width))
            for y in range(top, top + height + 1)
        }
        assert len(rows) == 1, f"{label}：條碼帶有 {len(rows)} 種列樣式，有東西滲進來"

    @pytest.mark.parametrize("price", [0, 1000, 12800])
    def test_wrapped_price_and_condition_are_not_clipped(
        self, monkeypatch: pytest.MonkeyPatch, price: int
    ) -> None:
        args = ("ITM-0001", "Snow Peak 雪峰 Amenity Dome 五人帳篷", price, _FONT)
        image = build_label_image(*args, brand="Snow Peak", condition="二手")
        monkeypatch.setattr(label_driver, "LABEL_HEIGHT_DOTS", 350)
        reference = build_label_image(*args, brand="Snow Peak", condition="二手")
        bounds = ImageChops.invert(reference).getbbox()
        assert bounds is not None and bounds[3] <= image.height
        assert image.tobytes() == reference.crop((0, 0, image.width, image.height)).tobytes()

    @pytest.mark.parametrize(
        ("label", "name", "brand", "condition"),
        [
            ("單行無品牌", "帳篷", None, None),
            ("換行無品牌", "Snow Peak 雪峰 Amenity Dome 五人帳篷二手極新", None, None),
            ("單行有品牌", "帳篷", "Snow Peak", "二手"),
            ("換行有品牌", "Snow Peak 雪峰 Amenity Dome 五人帳篷二手極新", "Snow Peak", "二手"),
        ],
    )
    @pytest.mark.parametrize("price", [0, 180, 12800])
    def test_no_variant_clips_anything_at_the_bottom(
        self,
        monkeypatch: pytest.MonkeyPatch,
        label: str,
        name: str,
        brand: str | None,
        condition: str | None,
        price: int,
    ) -> None:
        """四種版面都不得有任何東西被下邊界切掉。

        `NT$` 的錢字號尾巴比數字低一截，四種版面各自的 price_top 都要為它留位置——
        無品牌那兩種原本各切掉 6／4 dots（main 既有，2026-09-15 一併修）。
        做法沿用：在加高的畫布上重畫一次當基準，比對正常高度版本有沒有被裁掉內容。
        """
        args = ("ITM-0001", name, price, _FONT)
        image = build_label_image(*args, brand=brand, condition=condition)
        monkeypatch.setattr(label_driver, "LABEL_HEIGHT_DOTS", 400)
        reference = build_label_image(*args, brand=brand, condition=condition)
        bounds = ImageChops.invert(reference).getbbox()
        assert bounds is not None, f"{label}：整張空白"
        assert bounds[3] <= image.height, (
            f"{label}（NT${price}）：內容畫到 y={bounds[3]}，超出標籤高度 {image.height}"
        )
        assert image.tobytes() == reference.crop((0, 0, image.width, image.height)).tobytes()

    def test_long_brand_is_truncated_not_widened(self) -> None:
        """過長品牌截斷加「…」：截斷點之後的差異不影響輸出，且不撐破長度上限。"""

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
