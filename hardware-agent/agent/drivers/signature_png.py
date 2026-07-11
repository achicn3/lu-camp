"""簽名 PNG → 列印點陣（docs/23 K6，D6）：8-bit RGBA 子集解碼＋墨跡門檻＋縮放至紙寬。

只接受後端 signing 模組驗證過的同一子集（8-bit RGBA，color type 6＝HTML canvas
`toDataURL('image/png')` 的唯一輸出）：代理不嘗試渲染其他色型/位深的不明影像當簽名證據。
墨跡語意與後端一致（alpha ≥ 64 且亮度 < 200），縮放採「來源格內任一墨點即黑」的
保墨降採樣（只縮不放大），避免細筆劃在縮圖時消失。
"""

from __future__ import annotations

import base64
import binascii
import zlib

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_DIMENSION = 4096  # 與後端簽名驗證同上限
_MAX_RAW_BYTES = 4_000_000  # 解壓後掃描線上限（綁住 zlib 解壓記憶體）
_INK_ALPHA_MIN = 64
_INK_DARKNESS_MAX = 200
# 與後端 signing 相同的簽名合法性門檻（Codex K6 第三輪）：LAN 上直呼 agent 的 payload 也
# 不得印出後端會拒收的「簽名」（過小畫布/幾點墨跡不是簽名）。
_MIN_WIDTH = 150
_MIN_HEIGHT = 50
_MIN_INK_PIXELS = 100


class SignatureImageError(ValueError):
    """簽名影像不可用（非 PNG／非 8-bit RGBA／超限／空白）。"""


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


_FORBIDDEN_CHUNKS = frozenset({b"tRNS", b"bKGD"})  # 改變透明度/合成語意（與後端一致）


def _scan_chunks(png: bytes) -> tuple[bytes, bytes]:
    """逐 chunk 結構驗證並回傳 (IHDR data, 串接 IDAT)——與後端 signing 同規則
    （Codex K6 第四輪）：長度/CRC/type ASCII、IHDR 恰一次且居首（13 bytes）、IDAT ≥1 且
    連續、IEND 零長度居末無尾隨、critical 僅 IHDR/IDAT/IEND、禁 tRNS/bKGD。"""
    if not png.startswith(_PNG_MAGIC):
        raise SignatureImageError("簽名影像非 PNG")
    pos = len(_PNG_MAGIC)
    ihdr: bytes | None = None
    idat = bytearray()
    seen_idat = False
    idat_ended = False
    first = True
    while True:
        if pos + 12 > len(png):
            raise SignatureImageError("PNG chunk 結構不完整")
        length = int.from_bytes(png[pos : pos + 4], "big")
        ctype = png[pos + 4 : pos + 8]
        data_end = pos + 8 + length
        if data_end + 4 > len(png):
            raise SignatureImageError("PNG chunk 長度超出檔尾")
        if not all(65 <= b <= 90 or 97 <= b <= 122 for b in ctype):
            raise SignatureImageError("PNG chunk type 不合法")
        crc = int.from_bytes(png[data_end : data_end + 4], "big")
        if zlib.crc32(png[pos + 4 : data_end]) != crc:
            raise SignatureImageError("PNG chunk CRC 錯誤")
        data = png[pos + 8 : data_end]
        if first:
            if ctype != b"IHDR" or length != 13:
                raise SignatureImageError("PNG 首個 chunk 必須為 13-byte IHDR")
            ihdr = data
            first = False
        elif ctype == b"IHDR":
            raise SignatureImageError("PNG 重複 IHDR")
        elif ctype == b"IDAT":
            if idat_ended:
                raise SignatureImageError("PNG IDAT 不連續")
            seen_idat = True
            idat += data
        elif ctype == b"IEND":
            if length != 0:
                raise SignatureImageError("IEND 必須零長度")
            if data_end + 4 != len(png):
                raise SignatureImageError("IEND 後不得有尾隨資料")
            break
        else:
            if seen_idat:
                idat_ended = True  # IDAT 之後出現他型 chunk＝IDAT 段結束（再現 IDAT 即不連續）
            if not ctype[0] & 0x20:  # 大寫開頭＝critical，白名單外拒收
                raise SignatureImageError("PNG 含未知 critical chunk")
            if ctype in _FORBIDDEN_CHUNKS:
                raise SignatureImageError("PNG 含禁止的透明度/合成 chunk")
        pos = data_end + 4
    if ihdr is None or not seen_idat:
        raise SignatureImageError("PNG 缺 IHDR/IDAT")
    return ihdr, bytes(idat)


def _decode_rgba(png: bytes) -> tuple[int, int, bytearray]:
    """解出 (width, height, RGBA bytes)。僅支援 8-bit RGBA、非交錯、單一連續 IDAT 串流。"""
    ihdr, idat_bytes = _scan_chunks(png)
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    bit_depth, color_type = ihdr[8], ihdr[9]
    compression, filter_method, interlace = ihdr[10], ihdr[11], ihdr[12]
    if bit_depth != 8 or color_type != 6:
        raise SignatureImageError("僅支援 8-bit RGBA（canvas 簽名輸出）")
    if compression != 0 or filter_method != 0 or interlace != 0:
        raise SignatureImageError("PNG 壓縮/濾波/交錯設定不支援（canvas 不會輸出）")
    if not (0 < width <= _MAX_DIMENSION and 0 < height <= _MAX_DIMENSION):
        raise SignatureImageError("簽名影像尺寸超限")
    if width < _MIN_WIDTH or height < _MIN_HEIGHT:
        raise SignatureImageError("簽名影像畫布過小，非合法簽名（與後端門檻一致）")
    idat = idat_bytes
    stride = width * 4
    expected = (stride + 1) * height
    if expected > _MAX_RAW_BYTES:
        raise SignatureImageError("簽名影像原始資料超限")
    # 有界解壓（Codex K6 第一輪 high）：zlib.decompress 的 bufsize 只是初始緩衝提示、非上限，
    # 小 IHDR 配炸彈 IDAT 可先撐爆記憶體才被長度檢查擋下。改 decompressobj 以 max_length 硬限
    # 輸出，超限（有 unconsumed_tail）或串流未完/有殘料一律拒。
    dobj = zlib.decompressobj()
    try:
        raw = dobj.decompress(bytes(idat), expected + 1)
    except zlib.error as exc:
        raise SignatureImageError("PNG IDAT 解壓失敗") from exc
    if dobj.unconsumed_tail or not dobj.eof or dobj.unused_data not in (b"",):
        raise SignatureImageError("PNG IDAT 解壓超限或串流不完整")
    if len(raw) != expected:
        raise SignatureImageError("PNG 掃描線長度不符")

    out = bytearray(stride * height)
    prev = bytearray(stride)
    for y in range(height):
        base = y * (stride + 1)
        filt = raw[base]
        line = bytearray(raw[base + 1 : base + 1 + stride])
        if filt == 1:  # Sub
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                up_left = prev[i - 4] if i >= 4 else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif filt != 0:
            raise SignatureImageError("PNG filter 不合法")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return width, height, out


def signature_rows(image_base64: str, *, max_width_dots: int) -> list[list[bool]]:
    """把簽名 PNG（base64）轉為列印點陣 rows（True＝黑點），寬度縮至 max_width_dots 內。

    保墨降採樣：目標點對應的來源格內**任一**墨跡像素即為黑（細筆劃不因縮圖消失）；
    只縮不放大。全無墨跡 → SignatureImageError（不印空白簽名證據）。
    """
    try:
        png = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureImageError("簽名影像 base64 不合法") from exc
    width, height, rgba = _decode_rgba(png)

    scale = 1 if width <= max_width_dots else -(-width // max_width_dots)  # ceil
    out_w = -(-width // scale)
    out_h = -(-height // scale)
    rows = [[False] * out_w for _ in range(out_h)]
    ink_pixels = 0
    stride = width * 4
    for y in range(height):
        row_base = y * stride
        ty = y // scale
        target = rows[ty]
        for x in range(width):
            i = row_base + x * 4
            a = rgba[i + 3]
            if a < _INK_ALPHA_MIN:
                continue
            luma = (rgba[i] + rgba[i + 1] + rgba[i + 2]) // 3
            if luma >= _INK_DARKNESS_MAX:
                continue
            target[x // scale] = True
            ink_pixels += 1
    # 墨跡門檻與後端一致（≥100 原始墨點）：幾點雜訊不是簽名，不可印成簽名證據。
    if ink_pixels < _MIN_INK_PIXELS:
        raise SignatureImageError("簽名影像墨跡不足（空白/雜訊），不可作為簽名證據列印")
    return rows
