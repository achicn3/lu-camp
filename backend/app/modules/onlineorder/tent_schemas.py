"""寄售帳篷官網資料（docs/97 §3、§6）的輸入輸出。售價、品牌、名稱一律來自序號品，不另存。"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StrictBool, field_validator

from app.core.money import format_ntd

NTDOut = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]

MAX_LIST_ITEMS = 20
MAX_ITEM_CHARS = 100
MAX_COLOR_CHARS = 30
MAX_VIDEO_URL_CHARS = 300

# 店員貼的 YouTube 網址：watch、youtu.be、shorts、embed、live 都可以（官網用同一套規則解析）。
_YOUTUBE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtu\.be/|youtube(?:-nocookie)?\.com/"
    r"(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/))[\w-]{11}(?:[?&#/].*)?$"
)


def youtube_url_ok(url: str) -> bool:
    return _YOUTUBE.match(url) is not None


def _clean_list(values: list[str]) -> list[str]:
    items = [v.strip() for v in values if v.strip()]
    if len(items) > MAX_LIST_ITEMS:
        raise ValueError(f"最多 {MAX_LIST_ITEMS} 項")
    if any(len(v) > MAX_ITEM_CHARS for v in items):
        raise ValueError(f"每一項最多 {MAX_ITEM_CHARS} 個字")
    return items


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class TentListingWriteRequest(BaseModel):
    """整筆替換。缺件／瑕疵／配件一項一筆，空白項目自動去掉；空白字串視為沒填。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    published: StrictBool = False
    reserved: StrictBool = False
    color: str | None = None
    nights: int | None = Field(default=None, ge=0, le=9999, strict=True)
    missing: list[str] = Field(default_factory=list)
    defects: list[str] = Field(default_factory=list)
    extras: list[str] = Field(default_factory=list)
    video_url: str | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        cleaned = _blank_to_none(value)
        if cleaned is not None and len(cleaned) > MAX_COLOR_CHARS:
            raise ValueError(f"顏色最多 {MAX_COLOR_CHARS} 個字")
        return cleaned

    @field_validator("missing", "defects", "extras")
    @classmethod
    def _lists(cls, value: list[str]) -> list[str]:
        return _clean_list(value)

    @field_validator("video_url")
    @classmethod
    def _video(cls, value: str | None) -> str | None:
        cleaned = _blank_to_none(value)
        if cleaned is None:
            return None
        if len(cleaned) > MAX_VIDEO_URL_CHARS or not youtube_url_ok(cleaned):
            raise ValueError("請貼 YouTube 影片網址（一般網址、youtu.be 短網址或 Shorts 都可以）")
        return cleaned


class TentListingRead(TentListingWriteRequest):
    """POS「官網帳篷」清單的一列：序號品本身的欄位＋官網資料（還沒填過就是預設值）。"""

    item_id: int
    item_code: str
    name: str
    brand_name: str | None
    listed_price: NTDOut
    """含稅售價（序號品本身）。"""
    photos: list[str]
    """照片內容雜湊，第一張是封面；圖檔在 `GET /menu-photos/{sha}.webp`。"""
    updated_at: datetime | None
    """官網資料最後修改時間；還沒填過＝null。"""


class TentPhotoOrderRequest(BaseModel):
    """照片新順序：必須剛好是現有的那幾張。"""

    model_config = ConfigDict(extra="forbid")

    photos: list[str] = Field(max_length=8)


class TentReservedRead(BaseModel):
    """POS 結帳提醒用：這件在官網上是不是「已有人保留」。"""

    reserved: bool


class TentPublishStatusRead(BaseModel):
    """官網同步狀態（POS「官網帳篷」最上方顯示）。"""

    configured: bool
    """有沒有設定雲端；沒設定就不會推。"""
    pending: bool
    """POS 上的內容和官網上的不一樣（還沒推、或上次推失敗）。"""
    pushed_at: datetime | None
    last_error: str | None
    last_attempt_at: datetime | None
