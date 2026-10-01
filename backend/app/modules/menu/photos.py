"""菜單照片轉檔（docs/44 §3.4）：純函式，不碰資料庫。

- 只收 JPEG／PNG／WebP／HEIC（iPhone 預設），上限 10 MB、5000 萬像素以內
  （iPhone 4800 萬像素主鏡頭剛好過）。
- 先依 EXIF 方向轉正，再**丟掉 EXIF 與 XMP**（含 GPS——照片會公開在線上菜單）。
- 長邊縮到 1200px（不放大），一律輸出 WebP；檔名用內容雜湊，同一張照片傳兩次只存一份。
"""

import hashlib
import io
import warnings
from dataclasses import dataclass

import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

from app.shared.exceptions import MenuPhotoInvalid

pillow_heif.register_heif_opener()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_LONG_SIDE = 1200
# 像素上限：擋「檔案很小、解開上億像素」的壓縮炸彈。iPhone 4800 萬像素（8064×6048）要能過。
MAX_PIXELS = 50_000_000
WEBP_QUALITY = 82

_ACCEPTED_FORMATS = ("JPEG", "PNG", "WEBP", "HEIF")
_FORMAT_HINT = "只接受 JPEG、PNG、WebP、HEIC（手機拍的）照片"


@dataclass(frozen=True)
class ProcessedPhoto:
    content: bytes
    sha256: str
    width: int
    height: int


def _open(data: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=_ACCEPTED_FORMATS)
    except UnidentifiedImageError as exc:
        raise MenuPhotoInvalid(_FORMAT_HINT) from exc
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise MenuPhotoInvalid("照片像素太大，請先縮小再上傳") from exc
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise MenuPhotoInvalid("照片像素太大，請先縮小再上傳")
    try:
        image.load()
    except (OSError, ValueError, SyntaxError) as exc:
        raise MenuPhotoInvalid("照片檔案損壞，請重新拍一張或換一張") from exc
    return image


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)


def process_photo(data: bytes) -> ProcessedPhoto:
    """把上傳的照片轉成可公開的 WebP；不合格就丟 `MenuPhotoInvalid`（訊息可直接給店員看）。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise MenuPhotoInvalid("照片超過 10 MB，請先縮小再上傳")
    if not data:
        raise MenuPhotoInvalid(_FORMAT_HINT)
    source = _open(data)
    # exif_transpose 回傳轉正後的新圖；之後存檔不帶 exif／xmp，GPS 就不會留下。
    upright = ImageOps.exif_transpose(source)
    image = upright.convert("RGBA" if _has_alpha(upright) else "RGB")
    image.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    save_kwargs: dict[str, object] = {"quality": WEBP_QUALITY, "method": 6, "exif": b"", "xmp": b""}
    icc = source.info.get("icc_profile")
    if isinstance(icc, bytes):
        # 色彩描述檔（iPhone 是 Display P3）沒有個資，留著顏色才不會變灰。
        save_kwargs["icc_profile"] = icc
    image.save(buf, format="WEBP", **save_kwargs)
    content = buf.getvalue()
    return ProcessedPhoto(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        width=image.width,
        height=image.height,
    )
