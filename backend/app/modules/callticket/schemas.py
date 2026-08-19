"""叫號單 I/O schema（docs/38）。"""

from datetime import date, datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.shared.enums import CallTicketStatus

# 只允許店員點得開、且不會在瀏覽器上執行東西的協定。
_ALLOWED_LINK_SCHEMES = ("http", "https")


class CallTicketCreateRequest(BaseModel):
    """登記一筆候位（名稱必填，連結與備註選填）。"""

    name: str = Field(min_length=1, max_length=60)
    link: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("名稱不可為空白")
        return stripped

    @field_validator("link", "note")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """空字串正規化為 None——不要存 `""` 又在畫面上顯示成可點的空連結。"""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("link")
    @classmethod
    def _safe_scheme(cls, value: str | None) -> str | None:
        """**這個連結會被店員點開**，只收 http/https。

        `javascript:`／`data:`／`file:` 是會在店員瀏覽器上執行或讀本機檔案的東西，
        一律擋在邊界（422），不要等到前端才防。
        """
        if value is None:
            return None
        scheme = urlparse(value).scheme.lower()
        if scheme not in _ALLOWED_LINK_SCHEMES:
            raise ValueError("連結只接受 http:// 或 https:// 開頭")
        return value


class CallTicketRead(BaseModel):
    """叫號單輸出。`ticket_date` 為**台北營業日**（每日重置的依據）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    ticket_date: date
    ticket_no: int
    name: str
    link: str | None
    note: str | None
    status: CallTicketStatus
    created_at: datetime
    completed_at: datetime | None
