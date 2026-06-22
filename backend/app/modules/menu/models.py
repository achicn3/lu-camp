"""menu 模型：餐飲/內用菜單品項（手沖咖啡等現做商品）。

與二手庫存（serialized/bulk）、數量品（catalog）刻意分離：餐飲現做、不扣庫存、
不套門市活動折扣、不可用購物金折抵、報表另列「餐飲營收」。每品項一價（扁平，無選項群組）。

刪除採**封存**（archived_at）而非實刪——歷史 sale_line 以 menu_item_id 外鍵指向，
實刪會破壞參照完整性。POS 只列「未封存且 is_available」者。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class MenuItem(Base, TimestampMixin):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    # 含稅整數元售價（與全系統金額慣例一致，§6）。
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 磚分組（如「咖啡」「茶飲」「點心」）；可空，POS 之後可據此分區。
    category: Mapped[str | None] = mapped_column(String(50))
    # POS 是否可點（上架/停售切換，不影響歷史）。
    is_available: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # 封存（軟刪除）：非 NULL 即從 POS/管理清單隱藏，但保留供歷史 sale_line 參照。
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
