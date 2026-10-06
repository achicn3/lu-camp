"""線上點餐（店內端）業務邏輯（docs/44 §3.5、§4.1、§5.3）。

「發佈到線上點餐」依序推：手寫字型子集 → 被引用的照片 → 桌位碼 → 菜單快照。
雲端每一步都冪等（照片／字型推過回 204、菜單版本只往前），中途失敗重按一次即可。
跨模組只用 menu／settings／store 的 service（CLAUDE.md §2）。
"""

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.menu.service import MenuService, today
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.font import subset_font
from app.modules.onlineorder.models import OnlineMenuPublication, OnlineTableCode
from app.modules.onlineorder.presentation_service import MenuPresentationService
from app.modules.onlineorder.repository import OnlineOrderRepository
from app.modules.onlineorder.snapshot import Snapshot, build_snapshot, snapshot_text
from app.modules.settings.service import StoreSettingsService
from app.modules.store.service import StoreService
from app.shared.enums import ServiceMode
from app.shared.exceptions import OnlineOrderNotConfigured, OnlineTableNotFound

TAKEOUT_LABEL = "外帶"
PHOTO = "PHOTO"
FONT = "FONT"
_NOT_CONFIGURED = "尚未設定線上點餐（雲端網址或整合密鑰），請聯絡管理者"


@dataclass(frozen=True)
class PublishResult:
    version: int
    published_at: datetime
    item_count: int
    photos_pushed: int
    font_pushed: bool


@dataclass(frozen=True)
class TableLink:
    label: str
    service_mode: str
    code: str
    url: str


@dataclass(frozen=True)
class OnlineOrderStatus:
    configured: bool
    last_version: int | None
    last_published_at: datetime | None
    tables: list[TableLink]


def _new_code() -> str:
    # 128 位元隨機，base64url（Worker 只收 16–64 個 [A-Za-z0-9_-]）。
    return secrets.token_urlsafe(16)


class OnlineOrderService:
    def __init__(self, session: AsyncSession, client: OnlineOrderClient | None) -> None:
        self._session = session
        self._client = client
        self._repo = OnlineOrderRepository(session)

    def _client_for(self, store_id: int) -> OnlineOrderClient | None:
        """這家店有沒有自己的雲端：設定的雲端只服務一家店，別家店一律當作沒設定。"""
        if self._client is None or self._client.store_id != store_id:
            return None
        return self._client

    def _require_client(self, store_id: int) -> OnlineOrderClient:
        client = self._client_for(store_id)
        if client is None:
            raise OnlineOrderNotConfigured(_NOT_CONFIGURED)
        return client

    async def _sync_tables(self, store_id: int) -> list[OnlineTableCode]:
        """讓桌位碼跟設定的桌號一致：新桌號給新碼、拿掉的桌號停用。

        既有的碼不動——印好的 QR 才不會失效。
        """
        settings = await StoreSettingsService(self._session).get_effective_settings(store_id)
        # 外帶碼一定有一組；桌號設定裡若也叫「外帶」就併成那一組（Codex 對抗審查 O3 第三輪：
        # 否則兩筆同名、撞唯一索引，整個發佈失敗）。重複的桌號也只留一筆。
        dine_in = [
            label for label in dict.fromkeys(settings.dine_in_tables) if label != TAKEOUT_LABEL
        ]
        wanted = [(label, ServiceMode.DINE_IN.value) for label in dine_in]
        wanted.append((TAKEOUT_LABEL, ServiceMode.TAKEOUT.value))
        active = {t.label: t for t in await self._repo.active_tables(store_id, for_update=True)}
        now = datetime.now(UTC)
        wanted_labels = {label for label, _ in wanted}
        for label, old in active.items():
            if label not in wanted_labels:
                await self._repo.retire(old, now)
        result: list[OnlineTableCode] = []
        for label, mode in wanted:
            row = active.get(label)
            if row is None:
                row = OnlineTableCode(
                    store_id=store_id, label=label, service_mode=mode, code=_new_code()
                )
                await self._repo.add_table(row)
            result.append(row)
        return result

    async def _push_tables(self, client: OnlineOrderClient, tables: list[OnlineTableCode]) -> None:
        # revision＝推送當下的毫秒時間：雲端只收比上次新的，晚到的舊推送不能把停用的碼推回去。
        await client.put_json(
            "/integration/tables",
            {
                "revision": time.time_ns() // 1_000_000,
                "tables": [
                    {"code": t.code, "label": t.label, "service_mode": t.service_mode}
                    for t in tables
                ],
            },
        )

    async def _snapshot(self, store_id: int, version: int, published_at: datetime) -> Snapshot:
        menu = MenuService(self._session)
        items = await menu.list_items(store_id, include_unavailable=False)
        details = await menu.describe_items(store_id, items)
        categories = await menu.list_categories(store_id)
        store = await StoreService(self._session).get_receipt_header(store_id)
        snapshot = build_snapshot(
            details,
            categories,
            store_name=store.name,
            version=version,
            published_at=published_at,
            font_sha256=None,
            day=today(),
            presentations=await MenuPresentationService(self._session).snapshot_settings(
                store_id, [item.id for item in items]
            ),
        )
        return snapshot

    async def publish(self, store_id: int, *, actor_user_id: int) -> PublishResult:
        """把目前的菜單發佈到線上點餐。雲端失敗丟 `OnlineOrderPushFailed`，本機什麼都不記。"""
        client = self._require_client(store_id)
        published_at = datetime.now(UTC)
        # 版本＝發佈時間（毫秒）；時鐘倒退時仍保證比上一版大。
        version = max(
            int(published_at.timestamp() * 1000), await self._repo.max_version(store_id) + 1
        )
        snapshot = await self._snapshot(store_id, version, published_at)

        # 字型子集吃 CPU（解析 9.5 MB 原檔），丟到背景執行緒，不卡住 POS。
        font = await asyncio.to_thread(subset_font, snapshot_text(snapshot))
        snapshot["font"] = font.sha256
        font_pushed = False
        if not await self._repo.pushed(store_id, FONT, [font.sha256]):
            await client.put_font(font.sha256, font.content)
            await self._repo.mark_pushed(store_id, FONT, font.sha256)
            font_pushed = True

        items = snapshot["items"]
        photos = sorted({str(i["photo"]) for i in items if i["photo"] is not None})
        done = await self._repo.pushed(store_id, PHOTO, photos)
        menu = MenuService(self._session)
        photos_pushed = 0
        for sha in photos:
            if sha in done:
                continue
            content = await menu.photo_content(sha)
            if content is None:
                continue
            await client.put_photo(sha, content)
            await self._repo.mark_pushed(store_id, PHOTO, sha)
            photos_pushed += 1

        await self._push_tables(client, await self._sync_tables(store_id))
        await client.put_json("/integration/menu", snapshot)

        text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        await self._repo.add_publication(
            OnlineMenuPublication(
                store_id=store_id,
                version=version,
                sha256=hashlib.sha256(text.encode()).hexdigest(),
                item_count=len(items),
                published_by=actor_user_id,
                published_at=published_at,
            )
        )
        return PublishResult(version, published_at, len(items), photos_pushed, font_pushed)

    async def rotate_table(self, store_id: int, label: str, *, actor_user_id: int) -> TableLink:
        """重發某桌的碼（只改本機）：舊碼停用、產生新碼。呼叫端提交後再 `push_tables` 推到雲端，
        推送成功的那一刻舊 QR 才真正失效。"""
        client = self._require_client(store_id)
        tables = await self._sync_tables(store_id)
        target = next((t for t in tables if t.label == label), None)
        if target is None:
            raise OnlineTableNotFound(f"找不到桌號：{label}")
        await self._repo.retire(target, datetime.now(UTC))
        fresh = OnlineTableCode(
            store_id=store_id,
            label=target.label,
            service_mode=target.service_mode,
            code=_new_code(),
        )
        await self._repo.add_table(fresh)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="ROTATE_ONLINE_TABLE_CODE",
            entity_type="online_table_code",
            entity_id=str(fresh.id),
            # 不記碼本身：稽核紀錄不該變成拿到碼的管道。
            before={"label": label},
            after={"label": label},
        )
        return TableLink(
            fresh.label, fresh.service_mode, fresh.code, f"{client.base_url}/t/{fresh.code}"
        )

    async def push_tables(self, store_id: int) -> None:
        """把本機目前使用中的桌位碼整份推到雲端（雲端整份取代）。"""
        client = self._require_client(store_id)
        await self._push_tables(client, await self._sync_tables(store_id))

    async def status(self, store_id: int) -> OnlineOrderStatus:
        latest = await self._repo.latest_publication(store_id)
        client = self._client_for(store_id)
        base = client.base_url if client is not None else ""
        tables = await self._repo.active_tables(store_id)
        return OnlineOrderStatus(
            configured=client is not None,
            last_version=latest.version if latest else None,
            last_published_at=latest.published_at if latest else None,
            tables=[
                TableLink(t.label, t.service_mode, t.code, f"{base}/t/{t.code}" if base else "")
                for t in tables
            ],
        )
