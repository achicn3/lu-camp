"""線上點餐（店內端）模型（docs/44 §5.3）：發佈紀錄、桌位碼、已推到雲端的媒體。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class OnlineMenuPublication(Base, TimestampMixin):
    """每次「發佈到線上點餐」一筆。版本號用發佈時間（毫秒），本機與雲端失聯回滾後也只會往前。"""

    __tablename__ = "online_menu_publications"
    __table_args__ = (
        UniqueConstraint("store_id", "version", name="uq_online_menu_publications_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    version: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column()
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OnlineTableCode(Base, TimestampMixin):
    """桌位碼（docs/44 §4.1）：QR 網址 `/t/<code>`。重發＝舊碼 retired、產生新碼。"""

    __tablename__ = "online_table_codes"
    __table_args__ = (
        Index(
            "uq_online_table_codes_active_label",
            "store_id",
            "label",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        UniqueConstraint("code", name="uq_online_table_codes_code"),
        CheckConstraint(
            "service_mode IN ('DINE_IN', 'TAKEOUT')", name="ck_online_table_codes_mode"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    label: Mapped[str] = mapped_column(String(20))
    service_mode: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(64))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnlinePushedMedia(Base, TimestampMixin):
    """已推到雲端的照片／字型（內容雜湊）。推過就不再推；雲端本來就冪等，這只是省流量。"""

    __tablename__ = "online_pushed_media"
    __table_args__ = (
        UniqueConstraint("store_id", "kind", "sha256", name="uq_online_pushed_media"),
        CheckConstraint("kind IN ('PHOTO', 'FONT')", name="ck_online_pushed_media_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    sha256: Mapped[str] = mapped_column(String(64))
