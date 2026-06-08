"""後端店家抬頭 client（T15）。

收據／明細聯的抬頭（店名/統編/地址/電話）**單一事實來源為後端 `stores` 表**
（CLAUDE.md §4），不放 agent 設定檔以免漂移。本 client 依 `store_id` 向後端
`GET /api/v1/stores/{id}/receipt-header` 取得並**快取（per store_id、懶載入）**，
抗後端暫時斷線；取不到且無快取時丟 `StoreHeaderUnavailable`，由列印路由轉 503
——**絕不印出沒有店名/統編的收據**。後端 URL／服務 token 由環境變數設定。
"""

from __future__ import annotations

import os

import httpx
from pydantic import ValidationError

from agent.interfaces import StoreHeader


class StoreHeaderUnavailable(Exception):
    """後端店家抬頭取不到且無快取（不可印出無抬頭收據）。"""


class StoreHeaderClient:
    """向後端取店家抬頭，per store_id 快取。"""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport  # 測試可注入 httpx.MockTransport
        self._cache: dict[int, StoreHeader] = {}

    async def get_header(self, store_id: int) -> StoreHeader:
        """回傳該店抬頭。

        **抓取優先、快取備援**：每次先向後端取最新抬頭（單一事實來源為後端 `stores`，
        故統編/地址等更正會即時反映、不會永遠印舊值）；成功才覆蓋快取並回傳。後端暫時
        不可用或回傳資料異常時，退回**最後一次成功取得**的抬頭（仍含店名/統編、把關不破），
        以抗後端暫時斷線；若連快取都沒有則丟 `StoreHeaderUnavailable`（由列印路由轉 503）。
        """
        try:
            header = await self._fetch(store_id)
        except StoreHeaderUnavailable:
            cached = self._cache.get(store_id)
            if cached is not None:
                return cached
            raise
        self._cache[store_id] = header
        return header

    async def _fetch(self, store_id: int) -> StoreHeader:
        """向後端取一次抬頭並驗證；任何不可用（連線/格式/缺店名統編）丟 StoreHeaderUnavailable。"""
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        url = f"{self._base_url}/api/v1/stores/{store_id}/receipt-header"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.get(url, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise StoreHeaderUnavailable(f"無法取得 store {store_id} 抬頭：{exc}") from exc
        try:
            header = StoreHeader.model_validate(resp.json())
        except (ValueError, ValidationError) as exc:
            # 後端回 200 但 body 非 JSON／schema 不符：視為抬頭不可用，由列印路由轉 503
            # （而非讓未處理例外變成 500）。ValueError 涵蓋 json.JSONDecodeError。
            raise StoreHeaderUnavailable(f"store {store_id} 抬頭格式異常：{exc}") from exc
        if not header.name.strip() or not header.tax_id or not header.tax_id.strip():
            # 後端允許 tax_id=null（門市暫未設統編），但列印端要求抬頭完整：
            # 絕不印出沒有店名/統編的收據。視為不可用、不寫快取，由列印路由轉 503。
            raise StoreHeaderUnavailable(f"store {store_id} 抬頭缺少店名或統一編號，拒絕列印")
        return header


def build_store_header_client() -> StoreHeaderClient:
    """由環境變數建立 client（AGENT_BACKEND_URL、AGENT_SERVICE_TOKEN）。"""
    base_url = os.environ.get("AGENT_BACKEND_URL", "http://localhost:8000")
    token = os.environ.get("AGENT_SERVICE_TOKEN")
    return StoreHeaderClient(base_url=base_url, token=token)
