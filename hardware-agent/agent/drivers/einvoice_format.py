"""電子發票證明聯條碼內容（一維 Code39 / 左右二維 QR）。

依財政部財政資訊中心「電子發票證明聯一維及二維條碼規格說明」v1.9（民國 111 年 5 月）：

- 一維條碼（19 碼）：發票年期別 5 碼（民國年 3 + 期別雙數月 2）+ 字軌號碼 10 + 隨機碼 4。
- 左方 QR 前 77 碼：字軌 10 + 開立日期 7（民國 yyyMMdd）+ 隨機碼 4 + 銷售額 8（未稅，
  十六進位小寫、左補 0）+ 總計額 8（含稅，同上）+ 買方統編 8（一般消費者 00000000）+
  賣方統編 8 + 加密驗證資訊 24；其後以 ":" 區隔接：營業人自行使用區 10（未用為 10 個 *）、
  二維條碼記載品目筆數、該張發票品目總筆數、中文編碼參數（1=UTF-8）、品名:數量:單價…。
- 右方 QR：固定以 "**" 起始，接續左方不敷記載之品目。
- 加密驗證資訊：字軌+隨機碼以 AES-128-CBC（金鑰 hex、IV 為規格參考原始碼之固定值）
  加密後 Base64（16 bytes 密文 → 24 碼）。

金鑰由環境提供（`AGENT_EINVOICE_AES_KEY`），不入 repo；此處不做金額運算，
金額皆為呼叫端提供之字串整數元（CLAUDE.md §6）。
"""

from __future__ import annotations

import base64
from datetime import date

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from agent.drivers.escpos_raster import MAX_PAIR_QR_VERSION, qr_version_for
from agent.interfaces import InvoicePayload, SaleLinePayload

# 官方規格第伍章參考原始碼之固定 IV（RijndaelManaged.IV）
_SPEC_IV = base64.b64decode("Dt8lyToo17X/XkXaQvihuA==")
_UNUSED_MERCHANT_AREA = "*" * 10  # 營業人自行使用區未使用時為 10 個 *
_ENCODING_PARAM_UTF8 = "1"  # 中文編碼參數：0=Big5、1=UTF-8、2=Base64


def _roc_year(d: date) -> int:
    return d.year - 1911


def _period_month(d: date) -> int:
    """期別之雙數月份：1-2 月期 → 2、3-4 月期 → 4 …。"""
    return d.month + (d.month % 2)


def roc_period_label(d: date) -> str:
    """證明聯抬頭之年期別，例：「102年05-06月」。"""
    pm = _period_month(d)
    return f"{_roc_year(d)}年{pm - 1:02d}-{pm:02d}月"


def _roc_date7(d: date) -> str:
    """發票開立日期 7 碼：民國年 3 碼 + 月 2 碼 + 日 2 碼。"""
    return f"{_roc_year(d):03d}{d.month:02d}{d.day:02d}"


def _hex8(amount: str) -> str:
    """金額 8 碼十六進位（小寫、不足左補 0；規格參考原始碼 ToString("x8")）。"""
    return f"{int(amount):08x}"


def barcode_text(invoice: InvoicePayload) -> str:
    """一維條碼內容 19 碼：年期別 5 + 字軌 10 + 隨機碼 4。"""
    d = invoice.invoice_date
    return f"{_roc_year(d):03d}{_period_month(d):02d}{invoice.invoice_number}{invoice.random_code}"


def encrypt_verification(invoice_number: str, random_code: str, aes_key_hex: str) -> str:
    """加密驗證資訊 24 碼：AES-128-CBC(字軌+隨機碼, key=hex, IV=規格固定值) 後 Base64。"""
    padder = padding.PKCS7(128).padder()
    padded = padder.update((invoice_number + random_code).encode("ascii")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(bytes.fromhex(aes_key_hex)), modes.CBC(_SPEC_IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _item_fields(line: SaleLinePayload) -> str:
    """品名:數量:單價（品名內半形冒號以全形取代——規格：品名應避免間隔符號）。"""
    return f"{line.description.replace(':', '：')}:{line.qty}:{line.unit_price}"


def _build_pair(invoice: InvoicePayload, prefix77: str, recorded: int) -> tuple[str, str]:
    """以前 `recorded` 筆品目組出左右 QR 內容。

    「二維條碼記載完整品目筆數」依規格為**左右兩個 QR 合計**之記載筆數（規格範例：
    左 1 筆、右 2 筆 → 3:3）、「該張發票交易品目總筆數」為發票全部品目數；兩者在
    品目未全數記載（紙寬容量受限）時不同，規格欄位 10/11 與補充說明 3 允許此情形。
    """
    total = len(invoice.lines)
    head = f":{_UNUSED_MERCHANT_AREA}:{recorded}:{total}:{_ENCODING_PARAM_UTF8}"
    items = invoice.lines[:recorded]
    if not items:
        return prefix77 + head, "**"
    first, rest = items[0], items[1:]
    left = f"{prefix77}{head}:{_item_fields(first)}"
    if not rest:
        return left, "**"
    right = "**" + ":".join(_item_fields(line) for line in rest)
    return left + ":", right


def qr_pair_text(
    invoice: InvoicePayload,
    aes_key_hex: str,
    *,
    max_version: int = MAX_PAIR_QR_VERSION,
) -> tuple[str, str]:
    """產生左、右二維條碼內容。

    品目配置依規格範例：首筆記於左方（結尾補 ":" 接續），其餘接續右方；右方一律
    以 "**" 起始（品目皆已記載於左方時右方僅含起始符號）。品目多到任一 QR 超過
    `max_version`（紙寬內可印之版本上限）時，自後縮減記載品目並如實回報
    「記載筆數 < 總筆數」，避免印出超寬被裁切的 QR。
    """
    buyer = invoice.buyer_tax_id or "00000000"  # 一般消費者
    prefix77 = (
        invoice.invoice_number
        + _roc_date7(invoice.invoice_date)
        + invoice.random_code
        + _hex8(invoice.sales_amount)
        + _hex8(invoice.total_amount)
        + buyer
        + invoice.seller_tax_id
        + encrypt_verification(invoice.invoice_number, invoice.random_code, aes_key_hex)
    )
    for recorded in range(len(invoice.lines), -1, -1):
        left, right = _build_pair(invoice, prefix77, recorded)
        if qr_version_for(left) <= max_version and qr_version_for(right) <= max_version:
            return left, right
    raise ValueError("發票固定欄位即超過 QR 版本上限")  # pragma: no cover（77+head 必塞得下）
