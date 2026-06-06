"""共用相依與回應型別（無循環匯入的中介層）。

`agent.main` 與各 `agent.routers.*` 都從這裡取得 `get_devices`/`DevicesDep`/`OkResponse`，
**而非從 `agent.main` 互相匯入**——避免 `main` 於模組層 `app = create_app()` include router、
router 又反向匯入 `main` 造成的循環匯入（partially initialized module）。

本模組只依賴 `agent.devices`，不匯入 `agent.main` 或任何 router，故可被任一 router 獨立匯入。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel

from agent.devices import AgentDevices


class OkResponse(BaseModel):
    """通用成功回應。"""

    status: str


async def get_devices(request: Request) -> AgentDevices:
    """從 app.state 取得注入的裝置組合，供路由依賴。

    宣告為 async：此查找只讀 `app.state`、無 I/O，async 依賴會在事件迴圈上直接
    執行，避免被 FastAPI/AnyIO 派到 worker thread（在受限 ASGI 環境會卡住請求）。
    """
    devices: AgentDevices = request.app.state.devices
    return devices


DevicesDep = Annotated[AgentDevices, Depends(get_devices)]
