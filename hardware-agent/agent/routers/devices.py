"""裝置狀態路由（T16 A 級）。

GET /devices/status：回傳所有受管裝置的即時狀態（online/last_seen 心跳）。
透過 `agent.main.get_devices` 依賴取得 AgentDevices，呼叫 `status_provider.poll()`，
不含任何業務邏輯。
"""

from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter
from pydantic import BaseModel

from agent.deps import DevicesDep
from agent.interfaces import DeviceStatus

router = APIRouter()


class DevicesStatusResponse(BaseModel):
    """GET /devices/status 回傳體。"""

    devices: list[DeviceStatus]


@router.get(
    "/devices/status",
    response_model=DevicesStatusResponse,
    operation_id="getDevicesStatus",
)
async def get_devices_status(devices: DevicesDep) -> DevicesStatusResponse:
    """輪詢所有受管裝置狀態（A 級：online + last_seen 心跳）。

    回傳 `unsupported` 列出該機型查不到的狀態項；前端顯示「不支援」而非「故障」。

    `poll()` 對真機驅動是同步阻塞 I/O（TCP/USB、數秒逾時），故卸載到 worker thread，
    避免阻塞事件迴圈、拖垮 /health、/print/*、/drawer/open 等其他請求。
    """
    statuses = await anyio.to_thread.run_sync(devices.status_provider.poll)
    return DevicesStatusResponse(devices=statuses)
