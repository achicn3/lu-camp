"""線上點餐（店內端）API 輸出。"""

from datetime import datetime

from pydantic import BaseModel

from app.modules.onlineorder.service import OnlineOrderStatus, PublishResult, TableLink


class OnlineTableRead(BaseModel):
    label: str
    service_mode: str
    code: str
    # 客人掃 QR 開的網址；未設定雲端網址時為空字串。
    url: str

    @classmethod
    def from_link(cls, link: TableLink) -> "OnlineTableRead":
        return cls(label=link.label, service_mode=link.service_mode, code=link.code, url=link.url)


class OnlineOrderStatusRead(BaseModel):
    configured: bool
    last_version: int | None
    last_published_at: datetime | None
    tables: list[OnlineTableRead]

    @classmethod
    def from_status(cls, s: OnlineOrderStatus) -> "OnlineOrderStatusRead":
        return cls(
            configured=s.configured,
            last_version=s.last_version,
            last_published_at=s.last_published_at,
            tables=[OnlineTableRead.from_link(t) for t in s.tables],
        )


class OnlineMenuPublishRead(BaseModel):
    version: int
    published_at: datetime
    item_count: int
    photos_pushed: int
    font_pushed: bool

    @classmethod
    def from_result(cls, r: PublishResult) -> "OnlineMenuPublishRead":
        return cls(
            version=r.version,
            published_at=r.published_at,
            item_count=r.item_count,
            photos_pushed=r.photos_pushed,
            font_pushed=r.font_pushed,
        )
