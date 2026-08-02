"""贈品／折扣原因代碼的預設值與開店佈建。

**為什麼要有這支**：原因代碼是贈品的必填欄位。一間沒有任何原因代碼的門市根本送不出
贈品——POS 的選單會是空的。b5d7f9a1c3e2 那支 migration 只替「當下已存在」的門市塞了
預設值，之後新開的店不會有；所以建店流程必須自己佈建一次。

預設值與 migration 完全一致（新舊門市看到同一組），店家日後可自行停用或新增。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sales.models import DiscountReason, GiftReason

DEFAULT_GIFT_REASONS: tuple[tuple[str, str, bool], ...] = (
    ("PROMOTION", "活動贈品", False),
    ("COMPLAINT", "客訴補償", True),
    ("LOYALTY", "熟客回饋", False),
    ("DISPLAY", "展示品或即期品", False),
    ("OTHER", "其他", True),
)
DEFAULT_DISCOUNT_REASONS: tuple[tuple[str, str, bool], ...] = (
    ("DEFECT", "商品瑕疵", True),
    ("COMPLAINT", "客訴補償", True),
    ("LOYALTY", "熟客優惠", False),
    ("DISPLAY", "即期或展示品", False),
    ("MANAGER", "店長授權", True),
    ("PROMOTION", "活動優惠", False),
    ("OTHER", "其他", True),
)


async def ensure_default_reasons(session: AsyncSession, store_id: int) -> int:
    """替門市補齊預設原因代碼；回傳實際新增的筆數。

    冪等：已存在（同 store_id + code）的一律跳過，不覆寫店家改過的名稱或停用狀態。
    """
    added = 0
    for model, rows in (
        (GiftReason, DEFAULT_GIFT_REASONS),
        (DiscountReason, DEFAULT_DISCOUNT_REASONS),
    ):
        existing = set(
            (
                await session.scalars(
                    select(model.code).where(model.store_id == store_id)
                )
            ).all()
        )
        for order, (code, name, requires_note) in enumerate(rows):
            if code in existing:
                continue
            session.add(
                model(
                    store_id=store_id,
                    code=code,
                    name=name,
                    requires_note=requires_note,
                    sort_order=order,
                )
            )
            added += 1
    await session.flush()
    return added
