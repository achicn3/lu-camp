"""ESC/POS 收據機真機驅動（T15）。

實作 `agent.interfaces.ReceiptPrinter`，把銷售與店家抬頭排版成 ESC/POS 位元組寫到
`SupportsWrite`（實機為 EPSON TM-T82III 的連線；測試用 byte buffer 斷言版面，免實機）。
金額皆為字串整數元（§6），驅動不做金額運算、只如實排版。電子發票列印為 placeholder
（內容待發票收尾階段，docs/14 §5），僅輸出佔位標記、不偽造發票內容。
"""

from __future__ import annotations

from agent.escpos_printer import ESC, GS, SupportsWrite
from agent.interfaces import InvoicePayload, SalePayload, StoreHeader

_INIT = ESC + b"@"  # 初始化印表機
_ALIGN_CENTER = ESC + b"a" + bytes([1])
_ALIGN_LEFT = ESC + b"a" + bytes([0])
_CUT = GS + b"V" + bytes([0])  # full cut


def _line(text: str) -> bytes:
    return text.encode() + b"\n"


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
    out += _line("-" * 32)
    return bytes(out)


def _totals_block(sale: SalePayload) -> bytes:
    out = bytearray()
    out += _line("-" * 32)
    out += _line(f"未稅　 {sale.subtotal}")
    out += _line(f"營業稅 {sale.tax}")
    out += _line(f"總計　 {sale.total}")
    out += _line(f"付款方式：{sale.payment_method}")
    return bytes(out)


class EscposReceiptPrinter:
    """以 ESC/POS 位元組列印收據／明細聯的真機驅動。"""

    def __init__(self, writer: SupportsWrite) -> None:
        self._writer = writer

    def _emit_doc(self, sale: SalePayload, header: StoreHeader, *, title: str) -> None:
        out = bytearray()
        out += _INIT
        out += _header_block(header)
        out += _ALIGN_CENTER + _line(title) + _ALIGN_LEFT
        for line in sale.lines:
            out += _line(f"{line.description} x{line.qty}  {line.unit_price}  {line.line_total}")
        out += _totals_block(sale)
        out += _CUT
        self._writer.write(bytes(out))

    def print_receipt(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="收據")

    def print_detail(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="商品明細聯")

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """placeholder：發票版面/欄位待發票收尾階段；此處僅輸出佔位、不偽造發票內容。"""
        out = bytearray()
        out += _INIT
        out += _line(f"[電子發票待發票收尾階段] sale_id={invoice.sale_id}")
        out += _CUT
        self._writer.write(bytes(out))
