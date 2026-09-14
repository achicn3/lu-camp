"""Brother QL-810W 標籤機真機驅動（T18；brother_ql 光柵協定、網路 TCP 9100）。

依 G2 查證（docs/15）：Brother 無 Linux/Python 官方 SDK，跨平台實務採社群
**`brother_ql`** 做光柵協定轉換（`BrotherQLRaster` + `convert`）。**傳輸層不用
brother_ql 的 network 後端**——其 `socket.connect` 未設逾時，印表機不可達時會掛住
到 OS 預設逾時（可達數分鐘）；改以自有的帶逾時 TCP 送出（與 `status_real._tcp_probe`
／`escpos_network` 同模式），連線/逾時錯誤在此邊界翻成 `agent.errors` 的
`DeviceError`（ADR-010 誠實原則，不吞例外假裝成功）。

標籤紙為 **DK-22210（29mm 連續）**：brother_ql label `"29"`、可印寬 306 dots
（300dpi）。版面橫式（高固定 306 dots = 29mm），由上而下：品牌（選填，獨立一行）、
品名（中文，repo 內建 Noto Sans TC）、Code128 條碼＋識別碼（序號品 item_code／散裝堆
lot_code／一般商品 sku）、NT$ 價格＋全新或二手標示（選填，同行靠右）；送印前轉直向
（寬 306）交 `convert`。B 級狀態（缺紙/上蓋）網路下不可讀，標 unsupported
（docs/15 §2），不在此驅動範圍。

2026-09-14 裁示加上品牌與全新/二手：品牌自成一行、沒品牌就整行不印、成色（S–D）不印。
沒給品牌時版面與這次變更前**逐像素相同**，既有標籤不會因此改樣。
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import TypedDict, cast

from barcode import Code128
from brother_ql.conversion import convert
from brother_ql.raster import BrotherQLRaster
from PIL import Image, ImageDraw, ImageFont

from agent.config import PrinterEndpoint
from agent.errors import DeviceOffline, DeviceTimeout

_MODEL = "QL-810W"
_LABEL_ID = "29"  # DK-22210 29mm 連續（brother_ql LabelsManager 之 identifier）
LABEL_HEIGHT_DOTS = 306  # label "29" 之 dots_printable 寬（橫式版面的高）
# 標籤長度上限 480 dots ≈ 40.6mm（300dpi）：與短品名單行標籤（如 8 字品名 ≈40mm）
# 同級大小（使用者裁示 2026-06-11），長品名換行/截斷而非變長條。
MAX_LABEL_WIDTH_DOTS = 480
_MARGIN = 12
# 品名：單行塞得下就用單行（短品名維持原版面）；塞不下降字級換行（最多
# 三行、超出截斷加「…」），同時條碼/識別碼/價格帶下移縮排，挪出空間。字級與行距見 _Bands。
_ELLIPSIS = "…"


class _Bands(TypedDict):
    """一種版面變體的各帶位置／字級。

    高度固定 306 dots，所有帶必須排得下。**字級不等於行高**：Noto Sans TC 56px 的
    ascent+descent 是 82 dots，排版時要用實際渲染高度算，不能拿字級當行高
    （品牌行的英文降部就是這樣壓到品名帶的）。
    """

    brand_top: int  # 品牌行 y（無品牌的變體用不到，填 0）
    brand_font_px: int
    name_top: int
    name_font_px: int
    name_line_step: int
    name_max_lines: int
    barcode_top: int
    barcode_height: int
    code_top: int
    code_font_px: int
    price_top: int
    price_font_px: int
    condition_font_px: int


# 四種變體：品名單行/換行 × 有無品牌行。無品牌的兩種維持原版面一像素不動（既有標籤
# 不因這次變更而改樣）。有品牌的兩種空間從三處借：品名降一級、條碼縮高（仍 ≈7.5mm，
# 遠高於 Code128 可掃下限）、換行版品名由三行改兩行——品牌已承擔一部分辨識，
# 硬留三行會把條碼壓到掃不動。整張標籤不加長，維持 40mm 上限裁示。
_SINGLE: _Bands = {
    "brand_top": 0,
    "brand_font_px": 0,
    "name_top": 6,
    "name_font_px": 56,
    "name_line_step": 38,
    "name_max_lines": 1,
    "barcode_top": 78,
    "barcode_height": 120,
    "code_top": 206,
    "code_font_px": 30,
    "price_top": 240,
    "price_font_px": 56,
    "condition_font_px": 34,
}
_WRAPPED: _Bands = {
    "brand_top": 0,
    "brand_font_px": 0,
    "name_top": 4,
    "name_font_px": 34,
    "name_line_step": 38,
    "name_max_lines": 3,
    "barcode_top": 124,
    "barcode_height": 88,
    "code_top": 216,
    "code_font_px": 24,
    "price_top": 248,
    "price_font_px": 48,
    "condition_font_px": 28,
}
_SINGLE_BRANDED: _Bands = {
    "brand_top": 0,  # 24px：實際佔 0–34
    "brand_font_px": 24,
    "name_top": 36,  # 48px：中文約到 96、含英文降部到 105
    "name_font_px": 48,
    "name_line_step": 38,
    "name_max_lines": 1,
    "barcode_top": 108,
    "barcode_height": 90,
    "code_top": 202,  # 26px：到 238
    "code_font_px": 26,
    "price_top": 240,  # 48px 數字：到 296
    "price_font_px": 48,
    "condition_font_px": 30,
}
_WRAPPED_BRANDED: _Bands = {
    "brand_top": 0,
    "brand_font_px": 24,
    "name_top": 36,  # 32px 兩行（36、72）：到 118
    "name_font_px": 32,
    "name_line_step": 36,
    "name_max_lines": 2,
    "barcode_top": 124,
    "barcode_height": 84,
    "code_top": 212,  # 24px：到 246
    "code_font_px": 24,
    "price_top": 250,  # 44px 數字：到 301
    "price_font_px": 44,
    "condition_font_px": 26,
}
_BARCODE_TOP = _SINGLE["barcode_top"]
_BARCODE_TOP_WRAPPED = _WRAPPED["barcode_top"]
_BARCODE_MODULE_PX = 2  # Code128 窄條 2px ≈ 0.17mm @300dpi（縮版；熱感直印可掃）
_BARCODE_QUIET_PX = 24  # 條碼左右靜區（≥10 倍窄條）
_CONDITION_GAP_PX = 20  # 價格與全新/二手標示之間的最小留白
_SEND_TIMEOUT_S = 10.0  # 光柵資料送出逾時（量大於探測逾時，比照 brother_ql _write）

SenderFn = Callable[[PrinterEndpoint, bytes], None]


class LabelContentTooWide(Exception):
    """標籤內容（條碼/識別碼/價格/全新或二手標示）在最小可印尺寸下仍超出長度上限。

    這幾項都**不可截斷**（條碼截斷即印出掃起來是錯的碼；「二手」被切成「二」會誤導
    客人），故如實拒印；由 `agent.main` handler 轉 422（請求內容問題，非裝置故障）。
    品名與品牌不在此列——它們會換行或截斷補「…」，不會走到這裡。
    """


def _send_raster(endpoint: PrinterEndpoint, instructions: bytes) -> None:
    """帶逾時的 TCP 9100 raw 送出（連線逾時用探測逾時、送出逾時放寬到 10 秒）。"""
    with socket.create_connection((endpoint.host, endpoint.port), timeout=endpoint.timeout) as s:
        s.settimeout(_SEND_TIMEOUT_S)
        s.sendall(instructions)


def _code128_modules(code: str) -> list[bool]:
    """Code128 模組樣式（python-barcode `build()` 之 '1'/'0' 字串，不自寫編碼表）。"""
    pattern: str = Code128(code).build()[0]
    return [char == "1" for char in pattern]


def _wrap_lines(
    probe: ImageDraw.ImageDraw,
    name: str,
    font: ImageFont.FreeTypeFont,
    limit: int,
    max_lines: int,
) -> list[str]:
    """品名貪婪換行為最多 `max_lines` 行（以渲染寬度斷行）；裝不完則末行截斷補「…」。"""
    lines: list[str] = []
    current = ""
    truncated = False
    for index, char in enumerate(name):
        if probe.textlength(current + char, font=font) <= limit:
            current += char
            continue
        lines.append(current)
        current = char
        if len(lines) == max_lines:
            truncated = index < len(name)  # 還有裝不下的內容
            break
    if len(lines) < max_lines:
        lines.append(current)
        return [line for line in lines if line]
    if truncated:
        lines[-1] = _ellipsize(probe, lines[-1], font, limit)
    return lines


def _ellipsize(
    probe: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, limit: int
) -> str:
    """一律補「…」並往回削到塞得下（用於「後面還有內容被切掉」的那一行）。"""
    trimmed = text
    while trimmed and probe.textlength(trimmed + _ELLIPSIS, font=font) > limit:
        trimmed = trimmed[:-1]
    return trimmed + _ELLIPSIS


def _fit_single_line(
    probe: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, limit: int
) -> str:
    """塞得下就原樣，塞不下才截斷補「…」（品牌行用；品牌不是條碼，截斷不會印出錯的資訊）。"""
    if probe.textlength(text, font=font) <= limit:
        return text
    return _ellipsize(probe, text, font, limit)


def build_label_image(
    code: str,
    name: str,
    price: int,
    font_path: str,
    *,
    brand: str | None = None,
    condition: str | None = None,
) -> Image.Image:
    """組橫式標籤影像（'L' 灰階、白底黑字、高固定 `LABEL_HEIGHT_DOTS`）。

    寬度依內容（品名/條碼/價格的最大寬）伸縮，**上限 `MAX_LABEL_WIDTH_DOTS`**
    （≈40mm，與短品名單行標籤同級大小）：品名單行 56px 塞得下用單行；塞不下降
    34px 換行（最多三行、超出截斷加「…」），條碼/識別碼/價格帶同步下移縮排。
    29mm 連續紙長度自由。

    Args:
        brand: 品牌，獨立一行印在品名上方（裁示 2026-09-14）。`None`／空白＝**整行不印**，
            版面退回無品牌變體（不留白帶）；過長則截斷補「…」。
        condition: 全新／二手標示（如「二手 A」「全新」），與價格同一行靠右。`None`／空白
            ＝不印。標示不可截斷（「二手」被切成「二」會誤導客人），過長即拒印。
    """
    brand_text = (brand or "").strip()
    condition_text = (condition or "").strip()
    modules = _code128_modules(code)
    barcode_width = len(modules) * _BARCODE_MODULE_PX + 2 * _BARCODE_QUIET_PX

    probe = ImageDraw.Draw(Image.new("L", (1, 1), 255))
    name_limit = MAX_LABEL_WIDTH_DOTS - 2 * _MARGIN
    single_bands, wrapped_bands = (
        (_SINGLE_BRANDED, _WRAPPED_BRANDED) if brand_text else (_SINGLE, _WRAPPED)
    )
    single_font = ImageFont.truetype(font_path, single_bands["name_font_px"])
    if probe.textlength(name, font=single_font) <= name_limit:
        bands, name_font, name_lines = single_bands, single_font, [name]
    else:
        bands = wrapped_bands
        name_font = ImageFont.truetype(font_path, bands["name_font_px"])
        name_lines = _wrap_lines(probe, name, name_font, name_limit, bands["name_max_lines"])
    code_font = ImageFont.truetype(font_path, bands["code_font_px"])
    price_font = ImageFont.truetype(font_path, bands["price_font_px"])

    brand_font: ImageFont.FreeTypeFont | None = None
    if brand_text:
        brand_font = ImageFont.truetype(font_path, bands["brand_font_px"])
        brand_text = _fit_single_line(probe, brand_text, brand_font, name_limit)

    price_text = f"NT${price}"
    price_width = int(probe.textlength(price_text, font=price_font))
    condition_font: ImageFont.FreeTypeFont | None = None
    condition_width = 0
    price_row_width = price_width
    if condition_text:
        condition_font = ImageFont.truetype(font_path, bands["condition_font_px"])
        condition_width = int(probe.textlength(condition_text, font=condition_font))
        price_row_width = price_width + _CONDITION_GAP_PX + condition_width

    name_width = max(int(probe.textlength(line, font=name_font)) for line in name_lines)
    brand_width = 0 if brand_font is None else int(probe.textlength(brand_text, font=brand_font))
    code_width = int(probe.textlength(code, font=code_font))
    width = max(brand_width, name_width, barcode_width, code_width, price_row_width) + 2 * _MARGIN
    if width > MAX_LABEL_WIDTH_DOTS:
        # 品名/品牌已換行或截斷受控；會超寬的只有條碼、識別碼、價格與全新/二手標示，
        # 這幾項都不可截斷（截斷即印出錯的碼或誤導的標示），如實拒印。
        raise LabelContentTooWide(
            f"標籤內容超出長度上限 {MAX_LABEL_WIDTH_DOTS} dots"
            f"（≈{MAX_LABEL_WIDTH_DOTS / 300 * 25.4:.0f}mm）：需 {width} dots。"
            f"識別碼（{len(code)} 字）、價格或標示過長，條碼不可截斷，請縮短識別碼。"
        )

    image = Image.new("L", (width, LABEL_HEIGHT_DOTS), 255)
    draw = ImageDraw.Draw(image)
    if brand_font is not None:
        draw.text((_MARGIN, bands["brand_top"]), brand_text, font=brand_font, fill=0)
    name_top, name_step = bands["name_top"], bands["name_line_step"]
    for line_index, line in enumerate(name_lines):
        draw.text((_MARGIN, name_top + line_index * name_step), line, font=name_font, fill=0)
    bar_left = (width - barcode_width) // 2 + _BARCODE_QUIET_PX
    bar_top, bar_height = bands["barcode_top"], bands["barcode_height"]
    for index, module in enumerate(modules):
        if module:
            x = bar_left + index * _BARCODE_MODULE_PX
            draw.rectangle((x, bar_top, x + _BARCODE_MODULE_PX - 1, bar_top + bar_height), fill=0)
    draw.text(((width - code_width) // 2, bands["code_top"]), code, font=code_font, fill=0)
    price_top = bands["price_top"]
    draw.text((_MARGIN, price_top), price_text, font=price_font, fill=0)
    if condition_font is not None:
        # 與價格同一行、靠右；基線對齊（小字往下壓到與大字底部齊）。
        baseline = price_top + bands["price_font_px"] - bands["condition_font_px"]
        left = width - _MARGIN - condition_width
        draw.text((left, baseline), condition_text, font=condition_font, fill=0)
    return image


class BrotherLabelPrinter:
    """實作 `agent.interfaces.LabelPrinter` 的 Brother QL-810W 網路真機驅動。

    Args:
        endpoint: Brother 連線端點（IP/port/逾時，由 `agent.config` 注入、不寫死）。
        font_path: 標籤字型（預設 repo 內建 Noto Sans TC，見 `agent.config`）。
        sender: 光柵指令送出函式（測試注入假傳輸；預設帶逾時 TCP 9100）。
    """

    def __init__(
        self,
        endpoint: PrinterEndpoint,
        *,
        font_path: str,
        sender: SenderFn = _send_raster,
    ) -> None:
        self._endpoint = endpoint
        self._font_path = font_path
        self._sender = sender

    def print_label(
        self,
        code: str,
        name: str,
        price: int,
        *,
        brand: str | None = None,
        condition: str | None = None,
    ) -> None:
        """列印商品標籤；連線/逾時錯誤翻成 DeviceError（不吞例外假裝成功）。"""
        landscape = build_label_image(
            code, name, price, self._font_path, brand=brand, condition=condition
        )
        portrait = landscape.transpose(Image.Transpose.ROTATE_90)  # 寬 306 交 convert
        raster = BrotherQLRaster(_MODEL)
        # brother_ql 無型別 stub；convert 回光柵指令 bytes，以 cast 收斂。
        instructions = cast(
            bytes, convert(raster, [portrait], _LABEL_ID, rotate=0, cut=True, dither=False)
        )
        try:
            self._sender(self._endpoint, instructions)
        except TimeoutError as exc:  # TimeoutError 為 OSError 子類，須先攔
            raise DeviceTimeout(
                f"Brother {self._endpoint.host}:{self._endpoint.port} 列印逾時：{exc}"
            ) from exc
        except OSError as exc:  # 連線被拒/不可達/中斷
            raise DeviceOffline(
                f"Brother {self._endpoint.host}:{self._endpoint.port} 連線失敗：{exc}"
            ) from exc
