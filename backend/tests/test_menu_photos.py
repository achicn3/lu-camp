"""菜單照片轉檔（docs/44 §3.4）：任何來源一律轉 WebP、長邊縮到 1200、去掉 EXIF（含 GPS）。

照片會公開在線上菜單上，**GPS 留在檔案裡等於把店主家的位置貼出去**，所以去 EXIF 是硬性要求。
"""

import asyncio
import hashlib
import io

import pillow_heif
import pytest
from PIL import Image

from app.modules.menu.photos import (
    MAX_LONG_SIDE,
    MAX_UPLOAD_BYTES,
    process_photo,
    process_photo_async,
)
from app.shared.exceptions import MenuPhotoInvalid

pillow_heif.register_heif_opener()

_EXIF_ORIENTATION = 0x0112
_EXIF_GPS_IFD = 0x8825


def _encode(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def _jpeg_with_gps(size: tuple[int, int], orientation: int = 1) -> bytes:
    image = Image.new("RGB", size, (180, 120, 60))
    exif = Image.Exif()
    exif[_EXIF_ORIENTATION] = orientation
    exif.get_ifd(_EXIF_GPS_IFD)[2] = (25.0, 2.0, 0.0)  # GPSLatitude
    return _encode(image, "JPEG", exif=exif.tobytes())


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_jpeg_becomes_webp_without_exif_and_scaled() -> None:
    source = _jpeg_with_gps((3000, 1500))
    assert Image.open(io.BytesIO(source)).getexif().get_ifd(_EXIF_GPS_IFD)  # 前提：來源真的有 GPS
    photo = process_photo(source)
    out = _open(photo.content)
    assert out.format == "WEBP"
    assert (photo.width, photo.height) == out.size == (MAX_LONG_SIDE, 600)
    assert len(out.getexif()) == 0
    assert "exif" not in out.info
    assert photo.sha256 == hashlib.sha256(photo.content).hexdigest()


def test_orientation_is_applied_before_exif_is_dropped() -> None:
    # 手機直拍：像素是橫的、EXIF 說要轉 90 度。去掉 EXIF 前不先轉，照片會躺著。
    photo = process_photo(_jpeg_with_gps((2000, 1000), orientation=6))
    assert (photo.width, photo.height) == (600, MAX_LONG_SIDE)


def test_small_image_is_not_upscaled() -> None:
    photo = process_photo(_encode(Image.new("RGB", (400, 300), "white"), "PNG"))
    assert (photo.width, photo.height) == (400, 300)


def test_png_transparency_is_kept() -> None:
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    photo = process_photo(_encode(image, "PNG"))
    assert _open(photo.content).mode == "RGBA"


def test_heic_from_iphone_is_accepted() -> None:
    photo = process_photo(_encode(Image.new("RGB", (64, 48), (10, 200, 10)), "HEIF"))
    assert _open(photo.content).format == "WEBP"
    assert (photo.width, photo.height) == (64, 48)


def test_webp_input_is_accepted() -> None:
    photo = process_photo(_encode(Image.new("RGB", (50, 50), "red"), "WEBP"))
    assert (photo.width, photo.height) == (50, 50)


def test_same_photo_gives_same_hash() -> None:
    source = _jpeg_with_gps((800, 600))
    assert process_photo(source).sha256 == process_photo(source).sha256


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        b"%PDF-1.4 fake",
    ],
)
def test_non_images_are_rejected(data: bytes) -> None:
    with pytest.raises(MenuPhotoInvalid):
        process_photo(data)


def test_other_image_formats_are_rejected() -> None:
    with pytest.raises(MenuPhotoInvalid, match="JPEG"):
        process_photo(_encode(Image.new("RGB", (10, 10)), "GIF"))


def test_too_large_upload_is_rejected() -> None:
    with pytest.raises(MenuPhotoInvalid, match="10 MB"):
        process_photo(b"\xff" * (MAX_UPLOAD_BYTES + 1))


def test_decompression_bomb_is_rejected() -> None:
    # 檔案很小、解開卻是上億像素：不擋會把正式機記憶體吃光。
    bomb = _encode(Image.new("1", (12000, 12000)), "PNG")
    assert len(bomb) < MAX_UPLOAD_BYTES
    with pytest.raises(MenuPhotoInvalid, match="太大"):
        process_photo(bomb)


def test_truncated_image_is_rejected() -> None:
    source = _jpeg_with_gps((800, 600))
    with pytest.raises(MenuPhotoInvalid):
        process_photo(source[: len(source) // 2])


def test_xmp_location_is_dropped() -> None:
    # 有些 App 把 GPS 寫在 XMP 而不是 EXIF；兩個都要丟。
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b"<exif:GPSLatitude>25,2.0N</exif:GPSLatitude></x:xmpmeta>"
    )
    source = _encode(Image.new("RGB", (80, 60), "blue"), "JPEG", xmp=xmp)
    assert b"GPSLatitude" in source  # 前提：來源真的帶 XMP
    photo = process_photo(source)
    assert b"GPSLatitude" not in photo.content
    assert "xmp" not in _open(photo.content).info


async def test_async_conversion_keeps_event_loop_responsive() -> None:
    """Codex 對抗審查 O1d 第三輪：轉檔在事件迴圈上跑會讓整個後端（含 POS）卡住約一秒。"""
    big = _encode(Image.effect_noise((4000, 3000), 20).convert("RGB"), "JPEG", quality=80)
    gaps: list[float] = []
    done = asyncio.Event()

    async def heartbeat() -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            await asyncio.sleep(0.01)
            now = loop.time()
            gaps.append(now - last)
            last = now
            if done.is_set():
                return

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)  # 讓心跳先開始計時
    photo = await process_photo_async(big)
    done.set()
    await beat
    assert photo.width == MAX_LONG_SIDE
    assert max(gaps) < 0.15, f"事件迴圈被卡住 {max(gaps):.3f} 秒"


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_truncated_header_is_rejected_not_server_error(fmt: str) -> None:
    """Codex 對抗審查 O1d 第四輪：檔頭就斷掉的圖，錯誤發生在開檔階段，也要回「檔案損壞」。"""
    source = _encode(Image.new("RGB", (300, 200), "green"), fmt)
    for cut in (12, 24, 40):
        with pytest.raises(MenuPhotoInvalid):
            process_photo(source[:cut])
