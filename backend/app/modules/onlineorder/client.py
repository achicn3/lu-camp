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

    async def _send(
        self, method: str, path: str, body: bytes = b""
    ) -> tuple[int, dict[str, object]]:
        """送一個簽章請求，回 (狀態碼, JSON)。

        連不上 → OnlineOrderPushFailed；狀態碼由呼叫端判斷。
        """
        headers = {
            "Content-Type": "application/json",
            **sign_request(self._secret, method, path, body),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as http:
                resp = await http.request(method, path, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "online order request failed", extra={"path": path, "error": type(exc).__name__}
            )
            raise OnlineOrderPushFailed("連不上線上點餐雲端") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {}

    async def pull_orders(self) -> dict[str, object]:
        """拉還沒匯入的新單（兼心跳）。雲端回錯 → OnlineOrderPushFailed。"""
        code, data = await self._send("GET", "/integration/orders")
        if code >= 400:
            raise OnlineOrderPushFailed(f"線上點餐雲端拉單失敗（{code}）")
        return data

    async def report_status(self, remote_id: str, payload: dict[str, str]) -> tuple[int, str]:
        """回報一張單的狀態；回 (狀態碼, 雲端錯誤代碼)。連不上 → OnlineOrderPushFailed。"""
        body = json.dumps(payload, separators=(",", ":")).encode()
        code, data = await self._send("POST", f"/integration/orders/{remote_id}/status", body)
        return code, str(data.get("error", ""))

    async def set_accepting(self, accepting: bool) -> dict[str, object]:
        body = json.dumps({"accepting": accepting}).encode()
        code, data = await self._send("PUT", "/integration/store-status", body)
        if code >= 400:
            raise OnlineOrderPushFailed(f"線上點餐雲端拒收（{code}），請稍後再試")
        return data

    async def put_json(self, path: str, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        await self._put(path, body, "application/json")

    async def put_photo(self, sha256: str, content: bytes) -> None:
        await self._put(f"/integration/photos/{sha256}", content, "image/webp")

    async def put_font(self, sha256: str, content: bytes) -> None:
        await self._put(f"/integration/fonts/{sha256}", content, "font/woff2")
