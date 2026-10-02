"""連到線上點餐雲端（Worker）的 HTTP client（docs/44 §5.2）：每個請求都簽章。

雲端永遠不連進店內；只有店內往外連（路線 B）。失敗一律轉成 `OnlineOrderPushFailed`，
訊息可直接給店員看。
"""

import json
import logging

import httpx

from app.modules.onlineorder.signing import sign_request
from app.shared.exceptions import OnlineOrderPushFailed

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0


class OnlineOrderClient:
    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        store_id: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # 這組雲端服務的店；其他店不得使用（service 會擋）。
        self.store_id = store_id
        self._secret = secret
        self._transport = transport

    async def _put(self, path: str, body: bytes, content_type: str) -> int:
        headers = {"Content-Type": content_type, **sign_request(self._secret, "PUT", path, body)}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as http:
                resp = await http.put(path, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "online order push failed", extra={"path": path, "error": type(exc).__name__}
            )
            raise OnlineOrderPushFailed("連不上線上點餐雲端，請確認網路後再按一次發佈") from exc
        if resp.status_code >= 400:
            logger.warning(
                "online order push rejected", extra={"path": path, "status": resp.status_code}
            )
            raise OnlineOrderPushFailed(
                f"線上點餐雲端拒收（{resp.status_code}），請稍後再試；一直失敗請聯絡管理者"
            )
        return resp.status_code

    async def put_json(self, path: str, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        await self._put(path, body, "application/json")

    async def put_photo(self, sha256: str, content: bytes) -> None:
        await self._put(f"/integration/photos/{sha256}", content, "image/webp")

    async def put_font(self, sha256: str, content: bytes) -> None:
        await self._put(f"/integration/fonts/{sha256}", content, "font/woff2")
