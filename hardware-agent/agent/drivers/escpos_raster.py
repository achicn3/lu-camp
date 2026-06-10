"""ESC/POS 點陣圖（GS v 0）：證明聯一維 Code39 與左右並列 QR 的點陣產生。

採點陣自繪而非印表機內建條碼指令的原因：
- 兩個 QR 須「左右並列、水平對齊、大小一致」（條碼規格 v1.9），內建 GS ( k 一次只能
  印一個（垂直堆疊），無法並列。
- 19 碼 Code39 以內建 GS k 的最小模組寬（GS w n=2，0.25mm）排版會超出可印寬度；
  自繪以 1 dot（0.125mm）窄條，335 dots ≈ 42mm 可塞進 58mm 紙。
尺寸常數依規格：QR 編碼區邊長 ≥1.5cm、四周留白 ≥0.2cm；一維條碼高度 ≥0.5cm
（電子發票實施作業要點附件一）；TM-T82III 203dpi = 8 dots/mm。

QR 矩陣由 `qrcode` 產生（左方須 V6=41×41 以上、ECC Level L），Code39 模組樣式由
`python-barcode` 產生（`Code39.build()` 回傳 '1'/'0' 樣式字串），不自行手寫編碼表。
"""

from __future__ import annotations

import qrcode
from barcode import Code39

from agent.escpos_printer import GS

MODULE_DOTS = 3  # QR 每模組 3 dots：41 模組 × 3 ÷ 8 dots/mm ≈ 15.4mm ≥ 1.5cm
QUIET_DOTS = 16  # QR 四周留白 16 dots = 2mm ≥ 0.2cm
GAP_DOTS = 32  # 兩 QR 中央間隔（左右各留 0.2cm）
_BARCODE_HEIGHT_DOTS = 60  # 一維條碼高 60 dots = 7.5mm ≥ 0.5cm
_MIN_QR_VERSION = 6  # 規格：左方 QR 須 V6（41×41）以上


def qr_matrix(data: str, *, min_version: int = _MIN_QR_VERSION) -> list[list[bool]]:
    """產生 QR 模組矩陣（UTF-8 位元組、ECC Level L、至少 V6；資料過長自動升版）。"""
    qr = qrcode.QRCode(
        version=min_version,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=0,
    )
    qr.add_data(data.encode("utf-8"))
    qr.make(fit=True)
    return [list(row) for row in qr.get_matrix()]


def _scaled_row(modules: list[bool]) -> list[bool]:
    return [dot for module in modules for dot in (module,) * MODULE_DOTS]


def qr_pair_rows(left_data: str, right_data: str) -> list[list[bool]]:
    """左右並列、上緣對齊、大小一致的雙 QR 點陣（含四周留白與中央間隔）。

    兩 QR 版本不同（資料量差異）時，將較小者升至同版本，確保「大小一致」。
    """
    left = qr_matrix(left_data)
    right = qr_matrix(right_data)
    size = max(len(left), len(right))
    version = (size - 17) // 4
    if len(left) < size:
        left = qr_matrix(left_data, min_version=version)
    if len(right) < size:
        right = qr_matrix(right_data, min_version=version)

    width = QUIET_DOTS + size * MODULE_DOTS + GAP_DOTS + size * MODULE_DOTS + QUIET_DOTS
    blank = [False] * width
    rows: list[list[bool]] = [list(blank) for _ in range(QUIET_DOTS)]
    for left_row, right_row in zip(left, right, strict=True):
        line = (
            [False] * QUIET_DOTS
            + _scaled_row(left_row)
            + [False] * GAP_DOTS
            + _scaled_row(right_row)
            + [False] * QUIET_DOTS
        )
        rows.extend(list(line) for _ in range(MODULE_DOTS))
    rows.extend(list(blank) for _ in range(QUIET_DOTS))
    return rows


def code39_rows(text: str) -> list[list[bool]]:
    """一維 Code39 點陣：python-barcode 模組樣式 × 1 dot 窄條，高 ≥0.5cm。"""
    pattern: str = Code39(text, add_checksum=False).build()[0]
    row = [char == "1" for char in pattern]
    return [list(row) for _ in range(_BARCODE_HEIGHT_DOTS)]


def raster_command(rows: list[list[bool]]) -> bytes:
    """把點陣包成 ESC/POS 光柵指令 GS v 0（m=0，xL xH 為寬度 bytes、yL yH 為高度 dots）。"""
    height = len(rows)
    width = len(rows[0])
    width_bytes = (width + 7) // 8
    out = bytearray(GS + b"v0\x00")
    out += bytes([width_bytes & 0xFF, width_bytes >> 8, height & 0xFF, height >> 8])
    for row in rows:
        for byte_index in range(width_bytes):
            packed = 0
            for bit in range(8):
                x = byte_index * 8 + bit
                if x < width and row[x]:
                    packed |= 0x80 >> bit
            out.append(packed)
    return bytes(out)
