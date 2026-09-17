"""openingcheck 業務邏輯：今天做到哪、打勾、略過、自訂項目增刪。

`completed` 只涵蓋**後端管得到的**部分（開帳＋自訂項目）；裝置狀態由前端直接問
hardware-agent（印標籤走同一條路），所以畫面上的「全部完成」可能比這裡嚴格。
略過的 key 存在後端，是因為裁示要求「每店每日共用」——在收銀電腦略過的，手機上也算略過。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import store_date, utc_now
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.openingcheck.models import OpeningCheck, OpeningCheckItem
from app.modules.openingcheck.repository import OpeningCheckRepository
from app.modules.openingcheck.schemas import OpeningCheckItemRead, OpeningCheckTodayRead

CASH_SESSION_KEY = "cash_session"


class OpeningCheckService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OpeningCheckRepository(session)

    async def _today(self, store_id: int) -> OpeningCheck:
        """取（或建立）今天的狀態列。營業日以店面時區計，跨午夜營業才不會切錯天。"""
        business_date = store_date(utc_now())
        existing = await self._repo.get_check(store_id, business_date)
        if existing is not None:
            return existing
        return await self._repo.add_check(
            OpeningCheck(store_id=store_id, business_date=business_date)
        )

    async def today(self, store_id: int) -> OpeningCheckTodayRead:
        """今天的檢查狀態。"""
        check = await self._today(store_id)
        items = await self._repo.list_items(store_id)
        # 與現存項目取交集：項目被刪掉後，殘留的 id 不該讓今天永遠完成不了。
        done_ids = {item_id for item_id in check.done_item_ids if item_id in {i.id for i in items}}
        skipped = list(check.skipped_keys)
        cash_open = (
            await CashDrawerService(self._session).get_current_session(store_id)
        ) is not None
        cash_ok = cash_open or CASH_SESSION_KEY in skipped
        return OpeningCheckTodayRead(
            business_date=check.business_date,
            cash_session_open=cash_open,
            items=[
                OpeningCheckItemRead(
                    id=item.id, label=item.label, href=item.href, done=item.id in done_ids
                )
                for item in items
            ],
            skipped_keys=skipped,
            completed=cash_ok and all(item.id in done_ids for item in items),
        )

    async def set_item_done(
        self, store_id: int, item_id: int, *, done: bool
    ) -> OpeningCheckTodayRead | None:
        """勾／取消勾一條自訂事項（店員日常操作）。找不到項目→None。"""
        item = await self._repo.get_item(store_id, item_id)
        if item is None:
            return None
        check = await self._today(store_id)
        current = set(check.done_item_ids)
        if done:
            current.add(item_id)
        else:
            current.discard(item_id)
        check.done_item_ids = sorted(current)
        await self._session.flush()
        return await self.today(store_id)

    async def skip(self, store_id: int, key: str) -> OpeningCheckTodayRead:
        """今天略過一個自動項目（裁示：不必填原因）。只算今天，明天會再檢查一次。"""
        check = await self._today(store_id)
        if key not in check.skipped_keys:
            check.skipped_keys = [*check.skipped_keys, key]
            await self._session.flush()
        return await self.today(store_id)

    async def create_item(
        self, store_id: int, *, label: str, href: str | None
    ) -> OpeningCheckItem:
        """新增自訂事項（設定頁，限管理者）。"""
        existing = await self._repo.list_items(store_id)
        return await self._repo.add_item(
            OpeningCheckItem(
                store_id=store_id,
                label=label.strip(),
                href=(href or "").strip() or None,
                sort_order=len(existing),
            )
        )

    async def archive_item(self, store_id: int, item_id: int) -> bool:
        """刪除自訂事項＝封存：已經勾過的歷史紀錄還指著它。找不到→False。"""
        item = await self._repo.get_item(store_id, item_id)
        if item is None:
            return False
        item.archived_at = datetime.now(UTC)
        await self._session.flush()
        return True
