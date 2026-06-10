"""Brother QL-810W 標籤機真機驅動（T18；brother_ql 光柵協定、網路 TCP 9100）。

依 G2 查證（docs/15）：Brother 無 Linux/Python 官方 SDK，跨平台實務採社群
**`brother_ql`** 做光柵協定轉換（`BrotherQLRaster` + `convert`）。**傳輸層不用
brother_ql 的 network 後端**——其 `socket.connect` 未設逾時，印表機不可達時會掛住
到 OS 預設逾時（可達數分鐘）；改以自有的帶逾時 TCP 送出（與 `status_real._tcp_probe`
／`escpos_network` 同模式），連線/逾時錯誤在此邊界翻成 `agent.errors` 的
`DeviceError`（ADR-010 誠實原則，不吞例外假裝成功）。

標籤紙為 **DK-22210（29mm 連續）**：brother_ql label `"29"`、可印寬 306 dots
（300dpi）。版面橫式（高固定 306 dots = 29mm）：品名（中文，repo 內建 Noto Sans
TC）、Code128 條碼＋識別碼（序號品 item_code／散裝堆 lot_code）、NT$ 價格；送印前
轉直向（寬 306）交 `convert`。B 級狀態（缺紙/上蓋）網路下不可讀，標 unsupported
（docs/15 §2），不在此驅動範圍。
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import cast

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
# 品名：單行 56px 塞得下就用單行（短品名維持原版面）；塞不下降 34px 換行（最多
# 三行、超出截斷加「…」），同時條碼/識別碼/價格帶下移縮排，挪出空間。
_NAME_FONT_PX = 56
_NAME_SMALL_FONT_PX = 34
_NAME_TOP = 6
_NAME_TOP_WRAPPED = 4
_NAME_LINE_STEP = 38  # 多行版行距
_NAME_MAX_LINES = 3
_ELLIPSIS = "…"
_SINGLE = {"barcode_top": 78, "barcode_height": 120, "code_top": 206, "price_top": 240}
_WRAPPED = {"barcode_top": 124, "barcode_height": 88, "code_top": 216, "price_top": 248}
_BARCODE_TOP = _SINGLE["barcode_top"]
_BARCODE_TOP_WRAPPED = _WRAPPED["barcode_top"]
_BARCODE_MODULE_PX = 2  # Code128 窄條 2px ≈ 0.17mm @300dpi（縮版；熱感直印可掃）
_BARCODE_QUIET_PX = 24  # 條碼左右靜區（≥10 倍窄條）
_CODE_FONT_PX = 30
_CODE_FONT_PX_WRAPPED = 24
_PRICE_FONT_PX = 56
_PRICE_FONT_PX_WRAPPED = 48
_SEND_TIMEOUT_S = 10.0  # 光柵資料送出逾時（量大於探測逾時，比照 brother_ql _write）

SenderFn = Callable[[PrinterEndpoint, bytes], None]


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
    probe: ImageDraw.ImageDraw, name: str, font: ImageFont.FreeTypeFont, limit: int
) -> list[str]:
    """品名貪婪換行為最多 `_NAME_MAX_LINES` 行（以渲染寬度斷行）；裝不完則末行截斷補「…」。"""
    lines: list[str] = []
    current = ""
    truncated = False
    for index, char in enumerate(name):
        if probe.textlength(current + char, font=font) <= limit:
            current += char
            continue
        lines.append(current)
        current = char
        if len(lines) == _NAME_MAX_LINES:
            truncated = index < len(name)  # 還有裝不下的內容
            break
    if len(lines) < _NAME_MAX_LINES:
        lines.append(current)
        return [line for line in lines if line]
    if truncated:
        last = lines[-1]
        while last and probe.textlength(last + _ELLIPSIS, font=font) > limit:
            last = last[:-1]
        lines[-1] = last + _ELLIPSIS
    return lines


def build_label_image(code: str, name: str, price: int, font_path: str) -> Image.Image:
    """組橫式標籤影像（'L' 灰階、白底黑字、高固定 `LABEL_HEIGHT_DOTS`）。

    寬度依內容（品名/條碼/價格的最大寬）伸縮，**上限 `MAX_LABEL_WIDTH_DOTS`**
    （≈40mm，與短品名單行標籤同級大小）：品名單行 56px 塞得下用單行；塞不下降
    34px 換行（最多三行、超出截斷加「…」），條碼/識別碼/價格帶同步下移縮排。
    29mm 連續紙長度自由。
    """
    modules = _code128_modules(code)
    barcode_width = len(modules) * _BARCODE_MODULE_PX + 2 * _BARCODE_QUIET_PX

    probe = ImageDraw.Draw(Image.new("L", (1, 1), 255))
    name_limit = MAX_LABEL_WIDTH_DOTS - 2 * _MARGIN
    single_font = ImageFont.truetype(font_path, _NAME_FONT_PX)
    if probe.textlength(name, font=single_font) <= name_limit:
        name_font, name_lines, name_top, bands = single_font, [name], _NAME_TOP, _SINGLE
        code_font = ImageFont.truetype(font_path, _CODE_FONT_PX)
        price_font = ImageFont.truetype(font_path, _PRICE_FONT_PX)
    else:
        name_font = ImageFont.truetype(font_path, _NAME_SMALL_FONT_PX)
        name_lines = _wrap_lines(probe, name, name_font, name_limit)
        name_top, bands = _NAME_TOP_WRAPPED, _WRAPPED
        code_font = ImageFont.truetype(font_path, _CODE_FONT_PX_WRAPPED)
        price_font = ImageFont.truetype(font_path, _PRICE_FONT_PX_WRAPPED)

    price_text = f"NT${price}"
    name_width = max(int(probe.textlength(line, font=name_font)) for line in name_lines)
    code_width = int(probe.textlength(code, font=code_font))
    price_width = int(probe.textlength(price_text, font=price_font))
    width = max(name_width, barcode_width, code_width, price_width) + 2 * _MARGIN

    image = Image.new("L", (width, LABEL_HEIGHT_DOTS), 255)
    draw = ImageDraw.Draw(image)
    for line_index, line in enumerate(name_lines):
        draw.text((_MARGIN, name_top + line_index * _NAME_LINE_STEP), line, font=name_font, fill=0)
    bar_left = (width - barcode_width) // 2 + _BARCODE_QUIET_PX
    bar_top, bar_height = bands["barcode_top"], bands["barcode_height"]
    for index, module in enumerate(modules):
        if module:
            x = bar_left + index * _BARCODE_MODULE_PX
            draw.rectangle((x, bar_top, x + _BARCODE_MODULE_PX - 1, bar_top + bar_height), fill=0)
    draw.text(((width - code_width) // 2, bands["code_top"]), code, font=code_font, fill=0)
    draw.text((_MARGIN, bands["price_top"]), price_text, font=price_font, fill=0)
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

    def print_label(self, code: str, name: str, price: int) -> None:
        """列印商品標籤；連線/逾時錯誤翻成 DeviceError（不吞例外假裝成功）。"""
        landscape = build_label_image(code, name, price, self._font_path)
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
