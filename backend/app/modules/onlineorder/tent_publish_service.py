"""寄售帳篷清單推到雲端（docs/97 §4.3）：內容雜湊沒變就不推；雲端說缺照片就補傳再推一次。

背景工作每 60 秒呼叫一次，店員存檔後畫面也會呼叫一次。POS 不記哪些照片推過——雲端有沒有才算數
（售出後雲端會刪照片，退貨回庫時再補）。雲端連不上或拒收只記下原因、下次重試，不影響 POS 其他功能：
連雲端之前一律先 commit，**不會拿著資料庫連線等雲端**（等很久時不能讓 POS 其他功能搶不到連線）。

內容沒變也每 30 分鐘重送一次：雲端會再確認照片都在（不在就回缺照片、這裡補傳），
極少數交錯時被誤刪的照片也會自己補回（Codex 對抗審查）。
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.service import MenuService
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.models import OnlineTentPublication
from app.modules.onlineorder.tent_repository import TentListingRepository
from app.modules.onlineorder.tent_schemas import TentPublishStatusRead
from app.modules.onlineorder.tent_service import TentListingService, feed_sha256, taipei_iso
from app.shared.exceptions import OnlineOrderPushFailed

_MAX_ERROR_CHARS = 300
REVERIFY_AFTER = timedelta(minutes=30)
# 同一個後端行程裡一次只推一份（背景工作與店員按的「立即更新」）。不用資料列鎖：
# 那要拿著交易等雲端回應。
_PUBLISH_LOCK = asyncio.Lock()


@dataclass
class _PublishState:
    # 推送進行中又有人要推：記一筆，進行中那份推完馬上再推一輪最新內容（不排隊、不拿連線等）。
    rerun: bool = False


_STATE = _PublishState()


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
        """內容有變（或上次失敗、或超過 30 分鐘沒確認）才推；結果寫進推送紀錄並 commit。"""
        client = self._client_for(store_id)
        if client is None:
            return TentPublishResult(configured=False, pushed=False, last_error=None)
        # 先放開呼叫端已開的交易（例如驗證登入時查過資料庫），再看要不要推。
        await self._session.commit()
        if _PUBLISH_LOCK.locked():
            # 已經有一份在推：不排隊（排隊時呼叫端可能還拿著連線，Codex 第二輪），
            # 留話給進行中那份，它推完會再推一輪最新內容。
            _STATE.rerun = True
            return TentPublishResult(configured=True, pushed=False, last_error=None)
        async with _PUBLISH_LOCK:
            rounds = [await self._publish_locked(store_id, client, now)]
            while _STATE.rerun:
                _STATE.rerun = False
                rounds.append(await self._publish_locked(store_id, client, datetime.now(UTC)))
        return TentPublishResult(
            configured=True,
            pushed=any(r.pushed for r in rounds),
            last_error=rounds[-1].last_error,
        )

    async def _publish_locked(
        self, store_id: int, client: OnlineOrderClient, now: datetime
    ) -> TentPublishResult:
        await self._tents.clear_reserved_for_sold(store_id)
        feed = await self._tents.build_feed(store_id, now=now)
        digest = feed_sha256(feed)
        pub = await self._publication(store_id, client.base_url)
        if _up_to_date(pub, digest, now):
            await self._session.commit()
            return TentPublishResult(configured=True, pushed=False, last_error=None)
        # 版本用時間（毫秒），但一定比上次大：時鐘倒退也只會往前。
        version = max(int(now.timestamp() * 1000), (pub.version or 0) + 1)
        payload = {"version": version, "syncedAt": taipei_iso(now), **feed}
        await self._session.commit()
        error = await self._push(client, payload)
        pub = await self._publication(store_id, client.base_url)
        pub.last_attempt_at = now
        pub.version = version
        if error is None:
            pub.content_sha256 = digest
            pub.pushed_at = now
        pub.last_error = error
        await self._repo.save_publication(pub)
        await self._session.commit()
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
            # 上次推失敗也算還沒上官網：雲端可能停在別的內容（回應遺失時）。
            pending=pub is None or pub.content_sha256 != digest or pub.last_error is not None,
            pushed_at=None if pub is None else pub.pushed_at,
            last_error=None if pub is None else pub.last_error,
            last_attempt_at=None if pub is None else pub.last_attempt_at,
        )

    async def _publication(self, store_id: int, cloud: str) -> OnlineTentPublication:
        pub = await self._repo.publication(store_id, cloud)
        if pub is None:
            pub = OnlineTentPublication(store_id=store_id, cloud=cloud)
            await self._repo.save_publication(pub)
        return pub

    async def _push(self, client: OnlineOrderClient, payload: dict[str, object]) -> str | None:
        """推清單；缺照片就補傳後再推一次。成功回 None，否則回給店員看的原因。"""
        try:
            code, data = await client.put_tents(payload)
            if code == 409 and data.get("error") == "missing_photo":
                photos = await self._missing_photos(payload, data.get("missing"))
                for sha, content in photos.items():
                    await client.put_tent_photo(sha, content)
                code, data = await client.put_tents(payload)
        except OnlineOrderPushFailed as exc:
            return str(exc)[:_MAX_ERROR_CHARS]
        if 200 <= code < 300:
            return None
        reason = str(data.get("error") or "")
        return f"官網雲端拒收（{code} {reason}），會自動再試".strip()[:_MAX_ERROR_CHARS]

    async def _missing_photos(
        self, payload: dict[str, object], missing: object
    ) -> dict[str, bytes]:
        """先把要補傳的照片從資料庫讀出來、commit 放開連線，再上傳。只補這份清單用到的。"""
        tents = payload.get("tents")
        wanted = {
            sha for t in (tents if isinstance(tents, list) else []) for sha in t.get("photos", [])
        }
        out: dict[str, bytes] = {}
        for sha in missing if isinstance(missing, list) else []:
            if not isinstance(sha, str) or sha not in wanted:
                continue
            content = await self._menu.photo_content(sha)
            if content is not None:
                out[sha] = content
        await self._session.commit()
        return out


def _up_to_date(pub: OnlineTentPublication, digest: str, now: datetime) -> bool:
    """內容沒變、上次成功、而且 30 分鐘內確認過，才可以不推。"""
    return (
        pub.content_sha256 == digest
        and pub.last_error is None
        and pub.pushed_at is not None
        and now - pub.pushed_at < REVERIFY_AFTER
    )
