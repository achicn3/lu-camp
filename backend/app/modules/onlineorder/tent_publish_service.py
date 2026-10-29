"""寄售帳篷清單推到雲端（docs/97 §4.3）：內容雜湊沒變就不推；雲端說缺照片就補傳再推一次。

背景工作每 60 秒呼叫一次，店員存檔後畫面也會呼叫一次。POS 不記哪些照片推過——雲端有沒有才算數
（售出後雲端會刪照片，退貨回庫時再補）。雲端連不上或拒收只記下原因、下次重試，不影響 POS 其他功能。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.service import MenuService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.models import OnlineTentPublication
from app.modules.onlineorder.tent_repository import TentListingRepository
from app.modules.onlineorder.tent_schemas import TentPublishStatusRead
from app.modules.onlineorder.tent_service import TentListingService, feed_sha256, taipei_iso
from app.shared.exceptions import OnlineOrderPushFailed

_MAX_ERROR_CHARS = 300


@dataclass(frozen=True)
class TentPublishResult:
    configured: bool
    pushed: bool
    last_error: str | None


class TentPublishService:
    def __init__(self, session: AsyncSession, client: OnlineOrderClient | None) -> None:
        self._session = session
        self._client = client
        self._repo = TentListingRepository(session)
        self._tents = TentListingService(session)
        self._menu = MenuService(session)

    def _client_for(self, store_id: int) -> OnlineOrderClient | None:
        """設定的雲端只服務一家店，別家店一律當作沒設定。"""
        if self._client is None or self._client.store_id != store_id:
            return None
        return self._client

    async def publish(self, store_id: int, *, now: datetime) -> TentPublishResult:
        """內容有變才推；結果寫進推送紀錄（呼叫端負責 commit）。"""
        client = self._client_for(store_id)
        if client is None:
            return TentPublishResult(configured=False, pushed=False, last_error=None)
        await self._tents.clear_reserved_for_sold(store_id)
        feed = await self._tents.build_feed(store_id, now=now)
        digest = feed_sha256(feed)
        pub = await self._publication(store_id, client.base_url)
        if pub.content_sha256 == digest:
            return TentPublishResult(configured=True, pushed=False, last_error=pub.last_error)
        # 版本用時間（毫秒），但一定比上次大：時鐘倒退也只會往前。
        version = max(int(now.timestamp() * 1000), (pub.version or 0) + 1)
        payload = {"version": version, "syncedAt": taipei_iso(now), **feed}
        error = await self._push(client, payload)
        pub.last_attempt_at = now
        pub.version = version
        if error is None:
            pub.content_sha256 = digest
            pub.pushed_at = now
        pub.last_error = error
        await self._repo.save_publication(pub)
        return TentPublishResult(configured=True, pushed=error is None, last_error=error)

    async def status(self, store_id: int, *, now: datetime) -> TentPublishStatusRead:
        client = self._client_for(store_id)
        if client is None:
            return TentPublishStatusRead(
                configured=False,
                pending=False,
                pushed_at=None,
                last_error=None,
                last_attempt_at=None,
            )
        pub = await self._repo.publication(store_id, client.base_url)
        digest = feed_sha256(await self._tents.build_feed(store_id, now=now))
        return TentPublishStatusRead(
            configured=True,
            pending=pub is None or pub.content_sha256 != digest,
            pushed_at=None if pub is None else pub.pushed_at,
            last_error=None if pub is None else pub.last_error,
            last_attempt_at=None if pub is None else pub.last_attempt_at,
        )

    async def _publication(self, store_id: int, cloud: str) -> OnlineTentPublication:
        """鎖住這家店對這個雲端的紀錄：背景工作與店員按的同時發生時只會有一個在推。"""
        pub = await self._repo.publication(store_id, cloud, for_update=True)
        if pub is None:
            pub = OnlineTentPublication(store_id=store_id, cloud=cloud)
            await self._repo.save_publication(pub)
            pub = await self._repo.publication(store_id, cloud, for_update=True) or pub
        return pub

    async def _push(self, client: OnlineOrderClient, payload: dict[str, object]) -> str | None:
        """推清單；缺照片就補傳後再推一次。成功回 None，否則回給店員看的原因。"""
        try:
            code, data = await client.put_tents(payload)
            if code == 409 and data.get("error") == "missing_photo":
                await self._upload_missing(client, payload, data.get("missing"))
                code, data = await client.put_tents(payload)
        except OnlineOrderPushFailed as exc:
            return str(exc)[:_MAX_ERROR_CHARS]
        if 200 <= code < 300:
            return None
        reason = str(data.get("error") or "")
        return f"官網雲端拒收（{code} {reason}），會自動再試".strip()[:_MAX_ERROR_CHARS]

    async def _upload_missing(
        self, client: OnlineOrderClient, payload: dict[str, object], missing: object
    ) -> None:
        tents = payload.get("tents")
        wanted = {
            sha for t in (tents if isinstance(tents, list) else []) for sha in t.get("photos", [])
        }
        for sha in missing if isinstance(missing, list) else []:
            if not isinstance(sha, str) or sha not in wanted:
                continue
            content = await self._menu.photo_content(sha)
            if content is not None:
                await client.put_tent_photo(sha, content)
