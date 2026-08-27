"""ESC/POS 收據機真機驅動（T15）。

實作 `agent.interfaces.ReceiptPrinter`，把銷售與店家抬頭排版成 ESC/POS 位元組寫到
`SupportsWrite`（實機為 EPSON TM-T82III 的連線；測試用 byte buffer 斷言版面，免實機）。
金額皆為字串整數元（§6），驅動不做金額運算、只如實排版。電子發票證明聯版面依
「電子發票實施作業要點」附件一格式一，條碼內容依條碼規格 v1.9（`einvoice_format`）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from datetime import UTC
from zoneinfo import ZoneInfo

from agent.config import DEFAULT_ENCODING
from agent.drivers.einvoice_format import roc_period_label
from agent.drivers.escpos_raster import (
    PRINT_WIDTH_DOTS,
    code39_rows,
    qr_pair_rows,
    raster_command,
)
from agent.drivers.signature_png import signature_rows
from agent.escpos_printer import ESC, FS, GS, SupportsWrite
from agent.interfaces import (
    AcquisitionReceiptPayload,
    InvoicePayload,
    KitchenTicketPayload,
    SaleLinePayload,
    SalePayload,
    StoreHeader,
)

_INIT = ESC + b"@"  # 初始化印表機
_ALIGN_CENTER = ESC + b"a" + bytes([1])
_ALIGN_LEFT = ESC + b"a" + bytes([0])
_CUT = GS + b"V" + bytes([0])  # full cut
# 切刀位於印字頭上方約 1.2cm；切紙前先進紙，讓最後內容（總計區）通過切刀，
# 否則結尾會被切掉並殘留到下一張頂端（實機驗證 2026-06-08）。
_FEED_BEFORE_CUT = b"\n" * 5
# EPSON TM-T82III 中文：FS & 進多位元組中文模式、FS . 離開（實機驗證 2026-06-08）。
# **哪一套字型由機器的字型 ROM 決定**（Big5 機 vs GB18030 機），故編碼由 `line_encoder`
# 依設定注入，此處只負責進出中文模式。
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
# 出餐單（docs/35）沒有金額欄，品名欄可比收據寬得多；數量欄放得下 "x9999"。
_KITCHEN_QTY_W = 5


LineEncoder = Callable[[str], bytes]
"""把一行文字編成某台印表機看得懂的位元組（含換行）。"""


def line_encoder(encoding: str) -> LineEncoder:
    """做出一個綁定某台印表機字型編碼的排版函式。

    **編碼隨機器走、不是全域常數**：本店兩台 TM-T82III 的字型 ROM 不同（`GS I 69`
    分別回報 `TAIWAN BIG-5` 與 `CHINA GB18030`），同一份 Big5 位元組送到後者，印出來
    是整捲亂碼（實機 2026-08-27）。見 ADR-018。
    編不出的字以 ? 取代，避免單一冷僻字中斷整張列印。
    """

    def emit(text: str) -> bytes:
        return text.encode(encoding, errors="replace") + b"\n"

    return emit


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
    """一列品項：品名靠左（過長截斷），單價／數量／總價靠右，固定欄寬對齊。

    總價印**實付**（`net_amount`）：`line_total` 不含臨時折扣，印它會讓客人手上的明細
    加總大於底下的應付總額。舊版呼叫端沒帶 → 退回 line_total。
    """
    return (
        _pad_left_field(line.description, _NAME_W)
        + _pad_right_field(line.unit_price, _UNIT_W)
        + _pad_right_field(str(line.qty), _QTY_W)
        + _pad_right_field(line.net_amount or line.line_total, _TOTAL_W)
    )


def _item_sub_rows(line: SaleLinePayload, emit: LineEncoder) -> bytes:
    """品項下方的補充列：贈品標記、活動折扣、店家折扣（代理只印、不算）。

    **都放子列、不接在品名後面**：品名欄是固定寬度，接上去只要品名長一點就會連標記
    一起被截掉——實測 EPSON 上「反光營繩 4mm(贈)」的 (贈) 就是這樣消失的。

    discount_amount 為**整行**折讓（後端＝每件折讓×數量），故連同數量一起印，讓
    「原價×數量 − 折讓 = 總價」對得起來；qty>1 不會被誤讀成每件折讓。無折扣 → 空。
    """
    out = bytearray()
    if line.line_kind == "GIFT":
        out += emit("  ★ 贈品")
    if line.discount_amount not in ("", "0") and line.original_unit_price is not None:
        out += emit(f"  原價{line.original_unit_price} x{line.qty} 折-{line.discount_amount}")
    # 店家臨時折扣也要印：金額已改認實付，紙上若只有活動折扣，客人會看到
    # 「單價 × 數量」對不上總價卻找不到原因。
    if line.manual_discount_amount not in ("", "0"):
        out += emit(f"  店家折扣 -{line.manual_discount_amount}")
    return bytes(out)


_ITEM_HEADER = (
    _pad_left_field("品項", _NAME_W)
    + _pad_right_field("單價", _UNIT_W)
    + _pad_right_field("數量", _QTY_W)
    + _pad_right_field("總價", _TOTAL_W)
)


def _header_block(header: StoreHeader, emit: LineEncoder) -> bytes:
    out = bytearray()
    out += _ALIGN_CENTER
    out += emit(header.name)
    out += _ALIGN_LEFT
    if header.tax_id:
        out += emit(f"統一編號：{header.tax_id}")
    if header.address:
        out += emit(f"地址：{header.address}")
    if header.phone:
        out += emit(f"電話：{header.phone}")
    out += emit(_SEP)
    return bytes(out)


def _totals_block(sale: SalePayload, emit: LineEncoder) -> bytes:
    out = bytearray()
    out += emit(_SEP)
    # 活動折扣（docs/21）：有折讓時顯示折讓總額與活動名（代理只印後端算好的值）。
    if sale.total_discount not in ("", "0"):
        out += emit(f"活動折扣 -{sale.total_discount}")
        if sale.campaign_name:
            out += emit(f"活動：{sale.campaign_name}")
    out += emit(f"未稅　 {sale.subtotal}")
    out += emit(f"營業稅 {sale.tax}")
    out += emit(f"總計　 {sale.total}")
    if sale.tenders:
        out += emit("付款明細：")
        for tender in sale.tenders:
            out += emit(f"  {_payment_label(tender.tender_type)} {tender.amount}")
    else:
        out += emit(f"付款方式：{_payment_label(sale.payment_method)}")
    return bytes(out)


def _payment_label(method: str) -> str:
    """付款方式中文標籤（收據為客人看的，不印英文代碼）；未知值原樣印出（不靜默改寫）。"""
    return {
        "CASH": "現金",
        "STORE_CREDIT": "購物金",
        "LINE_PAY": "LINE Pay",
        "TAIWAN_PAY": "台灣Pay",
        "MIXED": "混合付款",
    }.get(method, method)


_STORE_TZ = ZoneInfo("Asia/Taipei")  # 店面時區（單店臺灣；未來多店改由 payload/設定帶入）


def _sale_metadata_block(sale: SalePayload, emit: LineEncoder) -> bytes:
    created_at = (
        sale.created_at.replace(tzinfo=UTC) if sale.created_at.tzinfo is None else sale.created_at
    )
    local_created_at = created_at.astimezone(_STORE_TZ)
    return emit(f"交易編號 #{sale.id}") + emit(
        f"交易時間 {local_created_at.strftime('%Y-%m-%d %H:%M')}"
    )


def _store_credit_signature_block(sale: SalePayload, emit: LineEncoder) -> bytes:
    """購物金折抵/剩餘＋客戶簽名影像（docs/23 K6，D6）：欄位缺省時輸出空、不改既有版面。

    簽名 PNG 已由後端 signing 驗證（8-bit RGBA、非空白）；此處再經 signature_rows 同子集
    解碼——不合法即拋 SignatureImageError（呼叫端轉 422，不印出壞影像當證據）。
    """
    out = bytearray()
    if sale.store_credit_deducted is not None:
        out += emit(_SEP)
        out += emit(f"購物金折抵 -{sale.store_credit_deducted}")
        if sale.store_credit_remaining is not None:
            out += emit(f"購物金剩餘  {sale.store_credit_remaining}")
    if sale.signature_png_base64 is not None:
        out += emit(_SEP)
        out += emit("客戶簽名：")
        out += _ALIGN_CENTER
        out += raster_command(signature_rows(sale.signature_png_base64, max_width_dots=360))
        out += _ALIGN_LEFT
    return bytes(out)


class EscposReceiptPrinter:
    """以 ESC/POS 位元組列印收據／明細聯／電子發票證明聯的真機驅動。

    Args:
        writer: 位元組輸出端（實機為 EPSON 網路連線；測試為 byte buffer）。
        encoding: 這台機器字型 ROM 的中文編碼（Big5 機 `big5`、GB18030 機 `gbk`）。
            預設 `big5` ＝ 既有單機部署行為；由 `agent.config.PrinterEndpoint.encoding`
            帶入，**不得**在此寫死。見 ADR-018。
    """

    def __init__(self, writer: SupportsWrite, *, encoding: str = DEFAULT_ENCODING) -> None:
        self._writer = writer
        self._emit = line_encoder(encoding)

    def _emit_doc(self, sale: SalePayload, header: StoreHeader, *, title: str) -> None:
        emit = self._emit
        out = bytearray()
        out += _INIT
        out += _SET_PRINT_AREA  # 印字區=408 dots：置中光柵（簽名）以紙寬為基準，右緣不被裁切
        out += _ENTER_CHINESE  # 整份文件以中文（Big5）模式列印，ASCII 仍如實
        out += _header_block(header, emit)
        out += _ALIGN_CENTER + emit(title) + _ALIGN_LEFT
        out += _sale_metadata_block(sale, emit)
        out += emit(_SEP)
        out += emit(_ITEM_HEADER)  # 欄位標題列：品項 / 單價 / 總價
        out += emit(_SEP)
        for line in sale.lines:
            out += emit(_item_row(line))
            out += _item_sub_rows(line, emit)
        out += _totals_block(sale, emit)
        out += _store_credit_signature_block(sale, emit)
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))

    def print_receipt(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="收據")

    def print_detail(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="商品明細聯")

    def print_acquisition(self, receipt: AcquisitionReceiptPayload, header: StoreHeader) -> None:
        """列印收購憑證聯（docs/23 K6）：切結品項/總額/撥款＋客戶簽名（存證聯）。"""
        emit = self._emit
        out = bytearray()
        out += _INIT
        out += _SET_PRINT_AREA  # 同上：簽名置中光柵須以 408-dot 印字區為基準
        out += _ENTER_CHINESE
        out += _header_block(header, emit)
        out += _ALIGN_CENTER + emit("收購憑證聯") + _ALIGN_LEFT
        out += emit(f"收購單號 #{receipt.acquisition_id}")
        # 憑證時間以**店面時區**呈現（Codex K6 第四輪）：後端 signed_at 為 UTC，直接 strftime
        # 會差八小時、可能跨日，毀損證據時點。naive 值視為 UTC。
        local_dt = (
            receipt.created_at.replace(tzinfo=UTC)
            if receipt.created_at.tzinfo is None
            else receipt.created_at
        ).astimezone(_STORE_TZ)
        out += emit(f"日期 {local_dt.strftime('%Y-%m-%d %H:%M')}")
        out += emit(f"賣方 {receipt.seller_name}")
        out += emit(_SEP)
        for item in receipt.items:
            out += emit(
                _pad_left_field(item.name, _NAME_W + _UNIT_W)
                + _pad_right_field(item.amount, _QTY_W + _TOTAL_W)
            )
        out += emit(_SEP)
        out += emit(f"收購總額 {receipt.total}")
        payout_label = "購物金" if receipt.payout_method == "STORE_CREDIT" else "現金"
        out += emit(f"撥款方式：{payout_label}")
        if receipt.store_credit_granted is not None:
            out += emit(f"撥入購物金 +{receipt.store_credit_granted}")
        if receipt.store_credit_balance_after is not None:
            out += emit(f"購物金總額 {receipt.store_credit_balance_after}")
        out += emit(_SEP)
        out += emit("賣方簽名：")
        out += _ALIGN_CENTER
        out += raster_command(signature_rows(receipt.signature_png_base64, max_width_dots=360))
        out += _ALIGN_LEFT
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))

    def print_kitchen_ticket(self, ticket: KitchenTicketPayload) -> None:
        """列印出餐單（docs/35）：放大的標題與桌號＋餐飲品項。

        **無店家抬頭、無金額**——這是給吧台核對出餐的內部作業單，不是客人的憑證。
        桌號放大（雙倍字）：吧台是隔著距離掃一眼，與內文同字級等於沒印。
        """
        emit = self._emit
        out = bytearray()
        out += _INIT
        out += _SET_PRINT_AREA
        out += _ENTER_CHINESE
        out += _ALIGN_CENTER
        out += _DOUBLE_ON + emit("出餐單") + _DOUBLE_OFF
        out += _ALIGN_LEFT
        out += emit(_SEP)
        out += _ALIGN_CENTER
        if ticket.service_mode == "DINE_IN":
            # table_no 於 payload 已驗證為非空白（fail closed），此處不再防禦性補值。
            out += _DOUBLE_ON + emit(f"內用 桌號 {(ticket.table_no or '').strip()}") + _DOUBLE_OFF
        else:
            out += _DOUBLE_ON + emit("外帶") + _DOUBLE_OFF
        out += _ALIGN_LEFT
        out += emit(_SEP)
        for line in ticket.lines:
            out += emit(
                _pad_left_field(line.description, _WIDTH - _KITCHEN_QTY_W)
                + _pad_right_field(f"x{line.qty}", _KITCHEN_QTY_W)
            )
        out += emit(_SEP)
        local_created_at = (
            ticket.created_at.replace(tzinfo=UTC)
            if ticket.created_at.tzinfo is None
            else ticket.created_at
        ).astimezone(_STORE_TZ)
        out += emit(
            _pad_left_field(f"#{ticket.sale_id}", _WIDTH - 11)
            + _pad_right_field(local_created_at.strftime("%m-%d %H:%M"), 11)
        )
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))

    def print_raw(self, data: bytes) -> None:
        """原樣輸出外部產生的 ESC/POS（見協定說明）。**不做任何改寫**——
        內容含加密驗證資訊與點陣圖，任何加工都可能讓條碼掃不出來。"""
        self._writer.write(data)

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """列印電子發票證明聯（附件一格式一；記載順序固定、不得增刪/變更）。

        順序：營業人識別標章 → 「電子發票證明聯」 → 年期別 → 字軌號碼 →
        交易日期時間 → 隨機碼/總計 → 賣方（買方）統編 → 一維條碼 → 左右二維條碼。
        """
        # 條碼/QR 內容**一律以 Amego 回傳為準**（docs/24；Codex 第十八輪）：payload 三欄
        # 必填，無本地 AES 後備——半套/自算內容不得被印成證明聯。
        left_qr, right_qr = invoice.qrcode_left_content, invoice.qrcode_right_content
        barcode = invoice.barcode_content
        number = invoice.invoice_number
        emit = self._emit
        out = bytearray()
        out += _INIT
        out += _SET_PRINT_AREA  # 須在 ESC @ 之後（避免被重設）、版面內容之前
        out += _ENTER_CHINESE
        out += _ALIGN_CENTER
        out += emit(invoice.seller_name)  # 1. 營業人識別標章（文字）
        out += _DOUBLE_ON
        out += emit("電子發票證明聯")  # 2. 字高 ≥0.5cm
        out += _BOLD_ON
        out += emit(roc_period_label(invoice.invoice_date))  # 3. 年期別（粗體）
        out += emit(f"{number[:2]}-{number[2:]}")  # 4. 字軌號碼（粗體）
        out += _BOLD_OFF
        out += _DOUBLE_OFF
        out += _ALIGN_LEFT
        # 5. 交易日期時間：西元年-月-日 時:分:秒
        out += emit(f"{invoice.invoice_date.isoformat()} {invoice.invoice_time.isoformat()}")
        out += emit(f"隨機碼:{invoice.random_code} 總計:{invoice.total_amount}")  # 6/7
        buyer_part = f" 買方{invoice.buyer_tax_id}" if invoice.buyer_tax_id else ""
        out += emit(f"賣方{invoice.seller_tax_id}{buyer_part}")  # 8/9
        out += _ALIGN_CENTER
        out += raster_command(code39_rows(barcode))  # 11. 一維條碼 ≥0.5cm 高
        out += b"\n"
        out += raster_command(qr_pair_rows(left_qr, right_qr))  # 12. 二維條碼 ×2 左右並列
        out += _ALIGN_LEFT
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))
