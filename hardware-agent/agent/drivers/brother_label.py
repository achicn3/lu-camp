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
_MARGIN = 12
_NAME_FONT_PX = 56
_NAME_TOP = 6
_BARCODE_TOP = 78
_BARCODE_HEIGHT = 120  # 120 dots = 10mm bar 高
_BARCODE_MODULE_PX = 3  # Code128 窄條 3px = 0.254mm @300dpi（X-dim 達標）
_BARCODE_QUIET_PX = 30  # 條碼左右靜區（≥10 倍窄條）
_CODE_FONT_PX = 30
_CODE_TOP = 206
_PRICE_FONT_PX = 56
_PRICE_TOP = 240
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


def build_label_image(code: str, name: str, price: int, font_path: str) -> Image.Image:
    """組橫式標籤影像（'L' 灰階、白底黑字、高固定 `LABEL_HEIGHT_DOTS`）。

    寬度依內容（品名/條碼/價格的最大寬）伸縮——29mm 連續紙長度自由。
    """
    name_font = ImageFont.truetype(font_path, _NAME_FONT_PX)
    code_font = ImageFont.truetype(font_path, _CODE_FONT_PX)
    price_font = ImageFont.truetype(font_path, _PRICE_FONT_PX)
    modules = _code128_modules(code)
    barcode_width = len(modules) * _BARCODE_MODULE_PX + 2 * _BARCODE_QUIET_PX

    probe = ImageDraw.Draw(Image.new("L", (1, 1), 255))
    price_text = f"NT${price}"
    name_width = int(probe.textlength(name, font=name_font))
    code_width = int(probe.textlength(code, font=code_font))
    price_width = int(probe.textlength(price_text, font=price_font))
    width = max(name_width, barcode_width, code_width, price_width) + 2 * _MARGIN

    image = Image.new("L", (width, LABEL_HEIGHT_DOTS), 255)
    draw = ImageDraw.Draw(image)
    draw.text((_MARGIN, _NAME_TOP), name, font=name_font, fill=0)
    bar_left = (width - barcode_width) // 2 + _BARCODE_QUIET_PX
    for index, module in enumerate(modules):
        if module:
            x = bar_left + index * _BARCODE_MODULE_PX
            draw.rectangle(
                (x, _BARCODE_TOP, x + _BARCODE_MODULE_PX - 1, _BARCODE_TOP + _BARCODE_HEIGHT),
                fill=0,
            )
    draw.text(((width - code_width) // 2, _CODE_TOP), code, font=code_font, fill=0)
    draw.text((_MARGIN, _PRICE_TOP), price_text, font=price_font, fill=0)
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
