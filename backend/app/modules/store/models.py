"""store 模型：門市/分店主檔（多分店租戶根）。

store 為租戶根主檔，本身不帶 store_id（不自我參照）；其餘業務表一律外鍵到此。
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Store(Base, TimestampMixin):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(8))
    invoice_track_info: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))
