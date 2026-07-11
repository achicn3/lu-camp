"""簽名 PNG → 列印點陣（docs/23 K6）：8-bit RGBA 子集解碼、墨跡門檻、縮放至紙寬。

後端 signing 只收 8-bit RGBA（type 6＝canvas toDataURL 唯一輸出）且已驗證結構；
代理端解碼採同一子集，非此子集/超限即拒（不嘗試渲染不明影像當簽名證據）。
"""

from __future__ import annotations

import base64
import zlib

import pytest

from agent.drivers.signature_png import SignatureImageError, signature_rows


def _png_rgba(width: int, height: int, ink_rows: range) -> bytes:
    """產生最小 8-bit RGBA PNG：ink_rows 範圍內為黑（不透明），其餘白。"""
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: None
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if y in ink_rows else b"\xff\xff\xff\xff"
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    return (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def test_decodes_ink_band_and_scales_to_width() -> None:
    png = _png_rgba(1200, 400, ink_rows=range(150, 250))
    rows = signature_rows(base64.b64encode(png).decode(), max_width_dots=360)
    # 寬度縮至上限內
    assert 0 < len(rows[0]) <= 360
    # 中段有墨跡、頂/底無
    mid = rows[len(rows) // 2]
    assert any(mid)
    assert not any(rows[0])
    assert not any(rows[-1])
    # 高度依比例縮（1200→≤360 ⇒ 400→約 ≤120）
    assert len(rows) <= 140


def test_small_image_not_upscaled() -> None:
    png = _png_rgba(200, 80, ink_rows=range(20, 40))
    rows = signature_rows(base64.b64encode(png).decode(), max_width_dots=360)
    assert len(rows[0]) == 200  # 原尺寸保留（只縮不放大）
    assert len(rows) == 80
    assert any(any(r) for r in rows)


def test_rejects_non_png() -> None:
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(b"not a png at all").decode(), max_width_dots=360)


def test_rejects_non_rgba_color_type() -> None:
    # 灰階（color type 0）不在子集
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    raw = bytearray()
    for _y in range(10):
        raw.append(0)
        raw += b"\x00" * 10
    ihdr = (10).to_bytes(4, "big") + (10).to_bytes(4, "big") + b"\x08\x00\x00\x00\x00"
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_rejects_oversize_dimensions() -> None:
    png = _png_rgba(5000, 10, ink_rows=range(0))
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_blank_image_rejected() -> None:
    """全白（無墨跡）不是簽名——拒印，避免留下空白簽名證據聯。"""
    png = _png_rgba(300, 100, ink_rows=range(0))
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_inflate_bomb_rejected_without_exhaustion() -> None:
    """小 IHDR 配炸彈 IDAT（解壓遠超宣告尺寸）→ 422 而非吃光記憶體（Codex K6 第一輪：
    decompressobj max_length 硬限輸出）。"""
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    # 宣告 10×10，IDAT 實際解壓出 50MB 的零
    ihdr = (10).to_bytes(4, "big") + (10).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    bomb = zlib.compress(b"\x00" * 50_000_000)
    png = magic + chunk(b"IHDR", ihdr) + chunk(b"IDAT", bomb) + chunk(b"IEND", b"")
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_tiny_canvas_rejected() -> None:
    """過小畫布（低於後端 150×50 門檻）→ 拒（1×1 黑點不是簽名；Codex K6 第三輪）。"""
    png = _png_rgba(100, 40, ink_rows=range(10, 30))
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_under_ink_threshold_rejected() -> None:
    """墨跡 <100 原始像素（幾點雜訊）→ 拒（與後端 _MIN_INK_PIXELS 一致）。"""
    png = _png_rgba(300, 100, ink_rows=range(0))
    # 手工造 50 個墨點：以 300×100 全白為基底重建一張帶 50 墨點的 PNG
    import zlib as _z

    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + _z.crc32(ctype + data).to_bytes(4, "big")
        )

    raw = bytearray()
    for y in range(100):
        raw.append(0)
        for x in range(300):
            raw += b"\x00\x00\x00\xff" if (y == 50 and x < 50) else b"\xff\xff\xff\xff"
    ihdr = (300).to_bytes(4, "big") + (100).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", _z.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def _chunk(ctype: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big")
        + ctype
        + data
        + zlib.crc32(ctype + data).to_bytes(4, "big")
    )


def _base_parts(width: int = 200, height: int = 80) -> tuple[bytes, bytes, bytes]:
    """回 (magic, IHDR chunk, IDAT chunk)：可重組出結構變體。"""
    magic = b"\x89PNG\r\n\x1a\n"
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    ihdr_data = (
        width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    )
    return magic, _chunk(b"IHDR", ihdr_data), _chunk(b"IDAT", zlib.compress(bytes(raw)))


def test_bad_crc_rejected() -> None:
    magic, ihdr, idat = _base_parts()
    corrupted = bytearray(ihdr)
    corrupted[-1] ^= 0xFF  # 破壞 CRC
    png = magic + bytes(corrupted) + idat + _chunk(b"IEND", b"")
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_interlaced_rejected() -> None:
    magic, _ihdr, idat = _base_parts()
    ihdr_data = (200).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"\x08\x06\x00\x00\x01"
    png = magic + _chunk(b"IHDR", ihdr_data) + idat + _chunk(b"IEND", b"")
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_forbidden_chunk_rejected() -> None:
    magic, ihdr, idat = _base_parts()
    png = magic + ihdr + _chunk(b"bKGD", b"\x00\x00\x00\x00\x00\x00") + idat + _chunk(b"IEND", b"")
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_unknown_critical_chunk_rejected() -> None:
    magic, ihdr, idat = _base_parts()
    png = magic + ihdr + _chunk(b"PLTE", b"\x00\x00\x00") + idat + _chunk(b"IEND", b"")
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_noncontiguous_idat_rejected() -> None:
    magic, ihdr, idat = _base_parts()
    png = (
        magic
        + ihdr
        + idat
        + _chunk(b"tEXt", b"k\x00v")
        + _chunk(b"IDAT", b"")
        + _chunk(b"IEND", b"")
    )
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)


def test_trailing_data_after_iend_rejected() -> None:
    magic, ihdr, idat = _base_parts()
    png = magic + ihdr + idat + _chunk(b"IEND", b"") + b"TRAILING"
    with pytest.raises(SignatureImageError):
        signature_rows(base64.b64encode(png).decode(), max_width_dots=360)
