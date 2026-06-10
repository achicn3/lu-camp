"""電子發票證明聯條碼內容（einvoice_format）單元測試。

Golden data 取自財政部財政資訊中心「電子發票證明聯一維及二維條碼規格說明」v1.9
（2022/05）：一維條碼範例 `10404UZ176908720122`；二維條碼範例（UTF-8 編碼）左方
`AB112233441020523999900000144000001540000000001234567…:**********:3:3:1:乾電池:1:105:`、
右方 `**口罩:1:210:牛奶:1:25`。加密驗證資訊因金鑰不公開，無法比對範例密文，改以
規格演算法（AES-128-CBC、IV=Dt8lyToo17X/XkXaQvihuA==、Base64）做解密回程驗證。
"""

from __future__ import annotations

import base64
from datetime import date, time

import pytest
import qrcode
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from agent.drivers.einvoice_format import (
    barcode_text,
    encrypt_verification,
    qr_pair_text,
    roc_period_label,
)
from agent.drivers.escpos_raster import MAX_PAIR_QR_VERSION
from agent.interfaces import InvoicePayload, SaleLinePayload

_KEY_HEX = "0123456789abcdef0123456789abcdef"  # 測試用 dummy 金鑰（16 bytes hex）
_SPEC_IV_B64 = "Dt8lyToo17X/XkXaQvihuA=="  # 官方規格參考原始碼之固定 IV


def _line(description: str, qty: int, unit_price: str) -> SaleLinePayload:
    return SaleLinePayload(
        line_type="ITEM",
        description=description,
        qty=qty,
        unit_price=unit_price,
        line_total=str(int(unit_price) * qty),
    )


def _spec_example_invoice() -> InvoicePayload:
    """規格書二維條碼範例的發票（民國 102/05/23、AB11223344、隨機碼 9999）。"""
    return InvoicePayload(
        sale_id=1,
        invoice_number="AB11223344",
        invoice_date=date(2013, 5, 23),  # 民國 102 年
        invoice_time=time(12, 30, 0),
        random_code="9999",
        sales_amount="324",  # hex 144
        tax_amount="16",
        total_amount="340",  # hex 154
        seller_tax_id="01234567",
        seller_name="測試商店",
        buyer_tax_id=None,
        lines=[_line("乾電池", 1, "105"), _line("口罩", 1, "210"), _line("牛奶", 1, "25")],
    )


class TestBarcodeText:
    def test_matches_spec_example(self) -> None:
        """一維條碼範例：104年3-4月、UZ17690872、隨機碼0122 → 10404UZ176908720122。"""
        invoice = _spec_example_invoice().model_copy(
            update={
                "invoice_number": "UZ17690872",
                "invoice_date": date(2015, 3, 7),  # 民國 104 年 3 月 → 期別雙數月 04
                "random_code": "0122",
            }
        )
        assert barcode_text(invoice) == "10404UZ176908720122"

    def test_even_month_uses_own_period(self) -> None:
        """雙數月（6月）期別即當月。"""
        invoice = _spec_example_invoice()  # 5 月 → 期別 06
        assert barcode_text(invoice)[:5] == "10206"
        assert len(barcode_text(invoice)) == 19


class TestRocPeriodLabel:
    def test_label_format(self) -> None:
        assert roc_period_label(date(2013, 5, 23)) == "102年05-06月"
        assert roc_period_label(date(2026, 1, 2)) == "115年01-02月"


class TestEncryptVerification:
    def test_is_24_chars_and_roundtrips(self) -> None:
        """24 碼 Base64；以規格之 AES-128-CBC + 固定 IV 解密可還原字軌+隨機碼。"""
        token = encrypt_verification("AB11223344", "9999", _KEY_HEX)
        assert len(token) == 24
        cipher = Cipher(
            algorithms.AES(bytes.fromhex(_KEY_HEX)),
            modes.CBC(base64.b64decode(_SPEC_IV_B64)),
        )
        decryptor = cipher.decryptor()
        padded = decryptor.update(base64.b64decode(token)) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        assert plain == b"AB112233449999"

    def test_deterministic(self) -> None:
        assert encrypt_verification("AB11223344", "9999", _KEY_HEX) == encrypt_verification(
            "AB11223344", "9999", _KEY_HEX
        )


class TestQrPairText:
    def test_left_prefix_53_matches_spec_example(self) -> None:
        """前 53 碼（加密資訊前）與規格書範例完全一致。"""
        left, _right = qr_pair_text(_spec_example_invoice(), _KEY_HEX)
        assert left[:53] == "AB112233441020523999900000144000001540000000001234567"

    def test_left_extension_and_right_match_spec_example(self) -> None:
        """77 碼後接 :自用區:筆數:總筆數:編碼參數(1=UTF-8)，品項首筆於左、餘接右。"""
        left, right = qr_pair_text(_spec_example_invoice(), _KEY_HEX)
        assert left[53 + 24 :] == ":**********:3:3:1:乾電池:1:105:"
        assert right == "**口罩:1:210:牛奶:1:25"

    def test_amount_hex_is_lowercase_left_padded(self) -> None:
        """金額十六進位小寫、不足 8 碼左補 0（規格參考原始碼 ToString("x8")）。"""
        invoice = _spec_example_invoice().model_copy(
            update={"sales_amount": "3000", "total_amount": "3150"}
        )
        left, _ = qr_pair_text(invoice, _KEY_HEX)
        assert left[21:29] == "00000bb8"
        assert left[29:37] == "00000c4e"

    def test_b2b_buyer_tax_id_recorded(self) -> None:
        invoice = _spec_example_invoice().model_copy(update={"buyer_tax_id": "12345678"})
        left, _ = qr_pair_text(invoice, _KEY_HEX)
        assert left[37:45] == "12345678"

    def test_single_item_right_is_marker_only(self) -> None:
        """品項僅一筆時右方僅含起始符號 **（規格：右方首2碼固定 **）。"""
        invoice = _spec_example_invoice().model_copy(update={"lines": [_line("乾電池", 1, "105")]})
        left, right = qr_pair_text(invoice, _KEY_HEX)
        assert right == "**"
        assert left.endswith(":1:乾電池:1:105")

    def test_recorded_count_spans_both_qrs_per_spec_example(self) -> None:
        """欄位「二維條碼記載完整品目筆數」記錄**左右兩個** QR 合計（規格範例：
        左 1 筆、右 2 筆 → 3:3），非僅左方筆數。"""
        left, right = qr_pair_text(_spec_example_invoice(), _KEY_HEX)
        fields = left[77:].split(":")
        assert fields[2] == "3"  # 記載筆數＝兩 QR 合計 3 筆
        assert fields[3] == "3"  # 總筆數 3 筆
        assert right.count(":") == 5  # 右 QR 實際接續 2 筆（每筆 3 欄、首筆無前導冒號）

    def test_truncates_items_when_qr_would_exceed_paper(self) -> None:
        """品項多到雙 QR 塞不下紙寬（版本上限）時：少記品項、筆數欄如實反映
        「記載筆數 < 總筆數」（規格欄位 10/11 與補充說明 3 允許品目不記錄）。"""
        many = [_line(f"露營用品第{i:03d}號標準品", 1, str(10 + i)) for i in range(120)]
        invoice = _spec_example_invoice().model_copy(update={"lines": many})
        left, right = qr_pair_text(invoice, _KEY_HEX)
        for payload in (left, right):
            probe = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
            probe.add_data(payload.encode("utf-8"))
            assert probe.best_fit() <= MAX_PAIR_QR_VERSION  # 兩個 QR 都印得下
        fields = left[77:].split(":")
        recorded, total = int(fields[2]), int(fields[3])
        assert total == 120
        assert 0 < recorded < total  # 如實回報：只記載部分品項
        assert right.startswith("**露營用品第001號標準品:")  # 首筆在左、第二筆起接右

    def test_colon_in_item_name_is_sanitized(self) -> None:
        """品名不得含半形冒號（規格：避免使用間隔符號）；以全形冒號取代。"""
        invoice = _spec_example_invoice().model_copy(update={"lines": [_line("AB:CD", 1, "10")]})
        left, _ = qr_pair_text(invoice, _KEY_HEX)
        assert ":AB：CD:1:10" in left
        assert ":AB:CD:" not in left


class TestInvoicePayloadValidation:
    def test_rejects_bad_invoice_number(self) -> None:
        with pytest.raises(ValueError):
            InvoicePayload(
                **{**_spec_example_invoice().model_dump(), "invoice_number": "1234567890"}
            )

    def test_rejects_bad_random_code(self) -> None:
        with pytest.raises(ValueError):
            InvoicePayload(**{**_spec_example_invoice().model_dump(), "random_code": "12a4"})

    def test_rejects_bad_seller_tax_id(self) -> None:
        with pytest.raises(ValueError):
            InvoicePayload(**{**_spec_example_invoice().model_dump(), "seller_tax_id": "1234"})

    def test_rejects_zero_total(self) -> None:
        """不得開立零元發票（規格第肆章參數說明）。"""
        with pytest.raises(ValueError):
            InvoicePayload(**{**_spec_example_invoice().model_dump(), "total_amount": "0"})

    def test_rejects_empty_lines(self) -> None:
        with pytest.raises(ValueError):
            InvoicePayload(**{**_spec_example_invoice().model_dump(), "lines": []})
