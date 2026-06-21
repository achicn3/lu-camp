"""ESC/POS 收據機真機驅動（T15）。

實作 `agent.interfaces.ReceiptPrinter`，把銷售與店家抬頭排版成 ESC/POS 位元組寫到
`SupportsWrite`（實機為 EPSON TM-T82III 的連線；測試用 byte buffer 斷言版面，免實機）。
金額皆為字串整數元（§6），驅動不做金額運算、只如實排版。電子發票證明聯版面依
「電子發票實施作業要點」附件一格式一，條碼內容依條碼規格 v1.9（`einvoice_format`）。
"""

from __future__ import annotations

import unicodedata

from agent.config import MissingDeviceConfigError
from agent.drivers.einvoice_format import barcode_text, qr_pair_text, roc_period_label
from agent.drivers.escpos_raster import (
    PRINT_WIDTH_DOTS,
    code39_rows,
    qr_pair_rows,
    raster_command,
)
from agent.escpos_printer import ESC, FS, GS, SupportsWrite
from agent.interfaces import InvoicePayload, SaleLinePayload, SalePayload, StoreHeader

_INIT = ESC + b"@"  # 初始化印表機
_ALIGN_CENTER = ESC + b"a" + bytes([1])
_ALIGN_LEFT = ESC + b"a" + bytes([0])
_CUT = GS + b"V" + bytes([0])  # full cut
# 切刀位於印字頭上方約 1.2cm；切紙前先進紙，讓最後內容（總計區）通過切刀，
# 否則結尾會被切掉並殘留到下一張頂端（實機驗證 2026-06-08）。
_FEED_BEFORE_CUT = b"\n" * 5
# EPSON TM-T82III 繁體中文：FS & 進中文（Big5）模式、FS . 離開（實機驗證 2026-06-08）。
# ASCII（< 0x80）在中文模式下仍以單位元組如實列印，故整份文件包在中文模式即可。
_ENTER_CHINESE = FS + b"&"
_EXIT_CHINESE = FS + b"."
# 證明聯標題字級：附件一規定「電子發票證明聯」「年期別」「字軌號碼」字高 ≥0.5cm、
# 後兩者粗體；雙倍字（24×2 dots = 6mm）達標。GS ! 控 ASCII、FS W 控中文（Big5）字級。
_DOUBLE_ON = GS + b"!" + bytes([0x11]) + FS + b"W" + bytes([1])
_DOUBLE_OFF = GS + b"!" + bytes([0x00]) + FS + b"W" + bytes([0])
_BOLD_ON = ESC + b"E" + bytes([1])
_BOLD_OFF = ESC + b"E" + bytes([0])

# 品項表格欄寬（半形單位）：此台 TM-T82III 實機量得一行可印 34 半形（中文全形佔 2，
# 實機尺規驗證 2026-06-08）。欄序：品名靠左（過長截斷）、單價/數量/總價靠右對齊成固定欄，
# 標題列同寬，欄位才對得齊；欄寬合計須 = 34。數量獨立成欄，品名不再帶「x數量」後綴。
_WIDTH = 34
_UNIT_W = 7
_QTY_W = 5  # 數量欄（右靠）：標題「數量」佔 4 半形 + 1 空白與單價分隔；數量值至 9999 容得下
_TOTAL_W = 7
_NAME_W = _WIDTH - _UNIT_W - _QTY_W - _TOTAL_W  # 15：較窄，長品名截斷更早
# 列印區寬度：與 escpos_raster.PRINT_WIDTH_DOTS 共用（408 = 34 半形 × 12 dots，實機
# 尺規量測）。此台裝 58mm 紙、但印表機印區仍照 80mm（576 dots）設定，置中會以 576
# 為基準 → 內容整體右偏且右側遭裁切（實機驗證 2026-06-10）；證明聯以 GS W 將印區
# 設為實際紙寬，讓 ESC a 置中（標題/條碼/QR）以 408 dots 為基準。
_SET_PRINT_AREA = GS + b"W" + bytes([PRINT_WIDTH_DOTS & 0xFF, PRINT_WIDTH_DOTS >> 8])
_SEP = "-" * _WIDTH
_TRUNCATE_MARK = ".."  # ASCII 截斷標記（Big5 安全、寬度確定）


def _line(text: str) -> bytes:
    # 繁中以 Big5 編碼（TM-T82III 字庫）；非 Big5 字以 ? 取代，避免單一字中斷整張列印。
    return text.encode("big5", errors="replace") + b"\n"


def _disp_width(text: str) -> int:
    """列印顯示寬度（半形）：東亞全形/寬字元佔 2，其餘 1。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in text)


def _pad_left_field(text: str, width: int) -> str:
    """靠左欄：補空白到 width 半形；過長則截斷並加 `..`（仍精準補滿 width）。"""
    if _disp_width(text) <= width:
        return text + " " * (width - _disp_width(text))
    keep = width - len(_TRUNCATE_MARK)
    out, used = "", 0
    for char in text:
        char_w = 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        if used + char_w > keep:
            break
        out += char
        used += char_w
    return out + _TRUNCATE_MARK + " " * (width - used - len(_TRUNCATE_MARK))


def _pad_right_field(text: str, width: int) -> str:
    """靠右欄：左補空白到 width 半形（數字欄用）。"""
    return " " * max(0, width - _disp_width(text)) + text


def _item_row(line: SaleLinePayload) -> str:
    """一列品項：品名靠左（過長截斷），單價／數量／總價靠右，固定欄寬對齊。"""
    return (
        _pad_left_field(line.description, _NAME_W)
        + _pad_right_field(line.unit_price, _UNIT_W)
        + _pad_right_field(str(line.qty), _QTY_W)
        + _pad_right_field(line.line_total, _TOTAL_W)
    )


def _discount_sub_rows(line: SaleLinePayload) -> bytes:
    """有活動折扣的品項，於下方加一列原價/折讓（代理只印、不算）。無折扣 → 空。"""
    if line.discount_amount in ("", "0") or line.original_unit_price is None:
        return b""
    return _line(f"  原價{line.original_unit_price} 折-{line.discount_amount}")


_ITEM_HEADER = (
    _pad_left_field("品項", _NAME_W)
    + _pad_right_field("單價", _UNIT_W)
    + _pad_right_field("數量", _QTY_W)
    + _pad_right_field("總價", _TOTAL_W)
)


def _header_block(header: StoreHeader) -> bytes:
    out = bytearray()
    out += _ALIGN_CENTER
    out += _line(header.name)
    out += _ALIGN_LEFT
    if header.tax_id:
        out += _line(f"統一編號：{header.tax_id}")
    if header.address:
        out += _line(f"地址：{header.address}")
    if header.phone:
        out += _line(f"電話：{header.phone}")
    out += _line(_SEP)
    return bytes(out)


def _totals_block(sale: SalePayload) -> bytes:
    out = bytearray()
    out += _line(_SEP)
    # 活動折扣（docs/21）：有折讓時顯示折讓總額與活動名（代理只印後端算好的值）。
    if sale.total_discount not in ("", "0"):
        out += _line(f"活動折扣 -{sale.total_discount}")
        if sale.campaign_name:
            out += _line(f"活動：{sale.campaign_name}")
    out += _line(f"未稅　 {sale.subtotal}")
    out += _line(f"營業稅 {sale.tax}")
    out += _line(f"總計　 {sale.total}")
    out += _line(f"付款方式：{sale.payment_method}")
    return bytes(out)


class EscposReceiptPrinter:
    """以 ESC/POS 位元組列印收據／明細聯／電子發票證明聯的真機驅動。

    Args:
        writer: 位元組輸出端（實機為 EPSON 網路連線；測試為 byte buffer）。
        einvoice_aes_key: 電子發票 QR 加密驗證資訊之 AES 金鑰（hex，環境變數
            `AGENT_EINVOICE_AES_KEY` 提供）；未設時列印證明聯即報設定缺漏。
    """

    def __init__(self, writer: SupportsWrite, *, einvoice_aes_key: str | None = None) -> None:
        self._writer = writer
        self._einvoice_aes_key = einvoice_aes_key

    def _emit_doc(self, sale: SalePayload, header: StoreHeader, *, title: str) -> None:
        out = bytearray()
        out += _INIT
        out += _ENTER_CHINESE  # 整份文件以中文（Big5）模式列印，ASCII 仍如實
        out += _header_block(header)
        out += _ALIGN_CENTER + _line(title) + _ALIGN_LEFT
        out += _line(_ITEM_HEADER)  # 欄位標題列：品項 / 單價 / 總價
        out += _line(_SEP)
        for line in sale.lines:
            out += _line(_item_row(line))
            out += _discount_sub_rows(line)
        out += _totals_block(sale)
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))

    def print_receipt(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="收據")

    def print_detail(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="商品明細聯")

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """列印電子發票證明聯（附件一格式一；記載順序固定、不得增刪/變更）。

        順序：營業人識別標章 → 「電子發票證明聯」 → 年期別 → 字軌號碼 →
        交易日期時間 → 隨機碼/總計 → 賣方（買方）統編 → 一維條碼 → 左右二維條碼。
        """
        if self._einvoice_aes_key is None:
            raise MissingDeviceConfigError(
                "環境變數 AGENT_EINVOICE_AES_KEY 未設定；電子發票 QR 加密驗證資訊"
                "需要 AES 金鑰（hex），請於 env/.env 提供（金鑰不入 repo）。"
            )
        left_qr, right_qr = qr_pair_text(invoice, self._einvoice_aes_key)
        number = invoice.invoice_number
        out = bytearray()
        out += _INIT
        out += _SET_PRINT_AREA  # 須在 ESC @ 之後（避免被重設）、版面內容之前
        out += _ENTER_CHINESE
        out += _ALIGN_CENTER
        out += _line(invoice.seller_name)  # 1. 營業人識別標章（文字）
        out += _DOUBLE_ON
        out += _line("電子發票證明聯")  # 2. 字高 ≥0.5cm
        out += _BOLD_ON
        out += _line(roc_period_label(invoice.invoice_date))  # 3. 年期別（粗體）
        out += _line(f"{number[:2]}-{number[2:]}")  # 4. 字軌號碼（粗體）
        out += _BOLD_OFF
        out += _DOUBLE_OFF
        out += _ALIGN_LEFT
        # 5. 交易日期時間：西元年-月-日 時:分:秒
        out += _line(f"{invoice.invoice_date.isoformat()} {invoice.invoice_time.isoformat()}")
        out += _line(f"隨機碼:{invoice.random_code} 總計:{invoice.total_amount}")  # 6/7
        buyer_part = f" 買方{invoice.buyer_tax_id}" if invoice.buyer_tax_id else ""
        out += _line(f"賣方{invoice.seller_tax_id}{buyer_part}")  # 8/9
        out += _ALIGN_CENTER
        out += raster_command(code39_rows(barcode_text(invoice)))  # 11. 一維條碼 ≥0.5cm 高
        out += b"\n"
        out += raster_command(qr_pair_rows(left_qr, right_qr))  # 12. 二維條碼 ×2 左右並列
        out += _ALIGN_LEFT
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))
