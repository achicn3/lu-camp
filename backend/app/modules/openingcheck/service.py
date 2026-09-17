"""openingcheck 業務邏輯：今天做到哪、打勾、略過、自訂項目增刪。

`completed` 只涵蓋**後端管得到的**部分（開帳＋自訂項目）；裝置狀態由前端直接問
hardware-agent（印標籤走同一條路），所以畫面上的「全部完成」可能比這裡嚴格。
略過的 key 存在後端，是因為裁示要求「每店每日共用」——在收銀電腦略過的，手機上也算略過。
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.time import store_date, utc_now
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.openingcheck.models import OpeningCheck, OpeningCheckItem
from app.modules.openingcheck.repository import OpeningCheckRepository
from app.modules.openingcheck.schemas import (
    CashSessionState,
    OpeningCheckItemRead,
    OpeningCheckTodayRead,
)
from app.shared.exceptions import OpeningCheckConflict

CASH_SESSION_KEY = "cash_session"


class OpeningCheckService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OpeningCheckRepository(session)

    async def _today_for_write(self, store_id: int) -> OpeningCheck:
        """取（或建立）今天的狀態列並鎖住它。營業日以店面時區計（跨午夜營業不會切錯天）。

        鎖：打勾與略過都是「讀整列→改→整列寫回」，兩台裝置同時操作而不鎖的話，
        後 commit 的會把前一個的勾靜默吃掉——而這功能的賣點正是多台裝置共用一份狀態。
        建立：只有寫入時才建列（GET 維持唯讀）；當天第一筆兩台同時進來會撞唯一鍵，
        撞到就改讀對方建好的那列。
        """
        business_date = store_date(utc_now())
        existing = await self._repo.get_check(store_id, business_date, for_update=True)
        if existing is not None:
            return existing
        try:
            async with self._session.begin_nested():
                return await self._repo.add_check(
                    OpeningCheck(store_id=store_id, business_date=business_date)
                )
        except IntegrityError as exc:
            seeded = await self._repo.get_check(store_id, business_date, for_update=True)
            if seeded is None:
                raise OpeningCheckConflict("開店前檢查狀態建立衝突，請重試") from exc
            return seeded

    async def today(self, store_id: int) -> OpeningCheckTodayRead:
        """今天的檢查狀態（**唯讀**：沒有人打勾/略過之前不留任何一列）。"""
        business_date = store_date(utc_now())
        check = await self._repo.get_check(store_id, business_date)
        items = await self._repo.list_items(store_id)
        # 與現存項目取交集：項目被刪掉後，殘留的 id 不該讓今天永遠完成不了。
        existing_ids = {i.id for i in items}
        done_ids = {
            item_id
            for item_id in (check.done_item_ids if check is not None else [])
            if item_id in existing_ids
        }
        skipped = list(check.skipped_keys) if check is not None else []
        session = await CashDrawerService(self._session).get_current_session(store_id)
        # 昨天忘記關帳 ≠ 今天已開帳：只看「有沒有 OPEN 的班別」會讓今天的現金收入
        # 被算進昨天的班別，對帳永遠對不平（§7 不變量 4）。
        if session is None:
            state = CashSessionState.NONE
        elif store_date(session.opened_at) == business_date:
            state = CashSessionState.OPEN_TODAY
        else:
            state = CashSessionState.STALE
        cash_ok = state is CashSessionState.OPEN_TODAY or CASH_SESSION_KEY in skipped
        return OpeningCheckTodayRead(
            business_date=business_date,
            cash_session_state=state,
            cash_session_open=state is CashSessionState.OPEN_TODAY,
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
        check = await self._today_for_write(store_id)
        current = set(check.done_item_ids)
        if done:
            current.add(item_id)
        else:
            current.discard(item_id)
        check.done_item_ids = sorted(current)
        await self._session.flush()
        return await self.today(store_id)

    async def skip(self, store_id: int, key: str, *, skipped: bool = True) -> OpeningCheckTodayRead:
        """略過／取消略過一個自動項目（裁示：不必填原因）。只算今天。"""
        check = await self._today_for_write(store_id)
        current = list(check.skipped_keys)
        if skipped and key not in current:
            check.skipped_keys = [*current, key]
            await self._session.flush()
        elif not skipped and key in current:
            check.skipped_keys = [k for k in current if k != key]
            await self._session.flush()
        return await self.today(store_id)

    async def create_item(
        self, store_id: int, *, label: str, href: str | None, actor_user_id: int
    ) -> OpeningCheckItem:
        """新增自訂事項（設定頁，限管理者；寫稽核——這是設定變更，§5）。"""
        existing = await self._repo.list_items(store_id)
        item = await self._repo.add_item(
            OpeningCheckItem(
                store_id=store_id,
                label=label.strip(),
                href=(href or "").strip() or None,
                # 用現有最大序號 +1 而不是筆數：封存過的項目會讓筆數重複。
                sort_order=max((i.sort_order for i in existing), default=-1) + 1,
            )
        )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_OPENING_CHECK_ITEM",
            entity_type="opening_check_item",
            entity_id=str(item.id),
            after={"label": item.label, "href": item.href},
        )
        return item

    async def archive_item(self, store_id: int, item_id: int, *, actor_user_id: int) -> bool:
        """刪除自訂事項＝封存：已經勾過的歷史紀錄還指著它。找不到→False。"""
        item = await self._repo.get_item(store_id, item_id)
        if item is None:
            return False
        item.archived_at = datetime.now(UTC)
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="DELETE_OPENING_CHECK_ITEM",
            entity_type="opening_check_item",
            entity_id=str(item_id),
            before={"label": item.label, "href": item.href},
        )
        return True
