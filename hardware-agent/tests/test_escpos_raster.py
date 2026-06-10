"""ESC/POS 點陣圖（escpos_raster）單元測試。

規格依據（財政資訊中心 v1.9）：左方 QR 須 V6（41×41）以上、ECC Level L 以上；
兩個 QR 左右並列、編碼區邊長 ≥1.5cm、四周留白 ≥0.2cm。一維條碼 Code39、
高度 ≥0.5cm（電子發票實施作業要點附件一）。印表機 203dpi（8 dots/mm）。
"""

from __future__ import annotations

from agent.drivers.escpos_raster import (
    GAP_DOTS,
    MODULE_DOTS,
    QUIET_DOTS,
    code39_rows,
    qr_matrix,
    qr_pair_rows,
    raster_command,
)


class TestQrMatrix:
    def test_minimum_version_6(self) -> None:
        """短資料也至少 V6 = 41×41 模組。"""
        matrix = qr_matrix("SHORT")
        assert len(matrix) == 41
        assert all(len(row) == 41 for row in matrix)

    def test_longer_data_grows_version(self) -> None:
        matrix = qr_matrix("X" * 200)
        assert len(matrix) > 41
        assert len(matrix) % 4 == 1  # QR 版本尺寸恆為 4n+17


class TestQrPairRows:
    def test_width_includes_quiet_zones_and_gap(self) -> None:
        rows = qr_pair_rows("LEFTDATA", "**RIGHT")
        width = len(rows[0])
        side = 41 * MODULE_DOTS
        assert width == QUIET_DOTS + side + GAP_DOTS + side + QUIET_DOTS
        assert all(len(row) == width for row in rows)

    def test_height_includes_vertical_quiet(self) -> None:
        rows = qr_pair_rows("LEFTDATA", "**RIGHT")
        assert len(rows) == QUIET_DOTS + 41 * MODULE_DOTS + QUIET_DOTS

    def test_quiet_zones_are_blank(self) -> None:
        rows = qr_pair_rows("LEFTDATA", "**RIGHT")
        side = 41 * MODULE_DOTS
        for row in rows[:QUIET_DOTS]:  # 上方留白
            assert not any(row)
        for row in rows:
            assert not any(row[:QUIET_DOTS])  # 左留白
            assert not any(row[QUIET_DOTS + side : QUIET_DOTS + side + GAP_DOTS])  # 中央間隔
            assert not any(row[-QUIET_DOTS:])  # 右留白

    def test_module_size_meets_15mm(self) -> None:
        """編碼區 41 模組 × MODULE_DOTS ÷ 8 dots/mm ≥ 15mm。"""
        assert 41 * MODULE_DOTS / 8 >= 15.0

    def test_quiet_zone_meets_2mm(self) -> None:
        assert QUIET_DOTS / 8 >= 2.0
        assert GAP_DOTS >= 2 * QUIET_DOTS  # 中央兩側各留 ≥ 0.2cm


class TestCode39Rows:
    def test_height_meets_5mm(self) -> None:
        rows = code39_rows("10404UZ176908720122")
        assert len(rows) / 8 >= 5.0  # ≥ 0.5cm

    def test_rows_identical_and_start_with_bar(self) -> None:
        rows = code39_rows("10404UZ176908720122")
        assert all(row == rows[0] for row in rows)
        assert rows[0][0] is True  # Code39 起始碼以 bar 開頭

    def test_fits_58mm_printable_width(self) -> None:
        """19 碼內容（含起止碼）須塞得進 58mm 紙的可印寬度（34 半形 ≈ 408 dots）。"""
        rows = code39_rows("10404UZ176908720122")
        assert len(rows[0]) <= 408


class TestRasterCommand:
    def test_header_and_bit_packing(self) -> None:
        """GS v 0 0 xL xH yL yH + MSB-first 位元包裝。"""
        rows = [
            [True] + [False] * 7 + [True],  # 0b10000000, 0b10000000
            [False] * 9,
        ]
        data = raster_command(rows)
        assert data[:4] == b"\x1dv0\x00"
        assert data[4:8] == bytes([2, 0, 2, 0])  # 寬 2 bytes、高 2 rows
        assert data[8:] == bytes([0b10000000, 0b10000000, 0, 0])

    def test_width_bytes_rounds_up(self) -> None:
        data = raster_command([[True] * 17])
        assert data[4:8] == bytes([3, 0, 1, 0])
        assert data[8:] == bytes([0xFF, 0xFF, 0b10000000])
