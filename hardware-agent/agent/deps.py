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
    """通用成功回應。

    `simulated=True` 代表**這次操作**送去的那台是假的：回了成功，但紙不會出來、錢櫃
    不會開。呼叫端必須把這件事講給店員聽，否則他會以為東西已經出來了。
    """

    status: str
    simulated: bool = False


class HealthResponse(OkResponse):
    """健康檢查：另外點名**哪幾台**沒接上真機。

    畫面只說「測試模式」，店員不知道還能不能結帳；說「標籤機沒接上」他就知道
    只有標籤印不出來。
    """

    simulated_devices: list[str] = []


def ok_response(devices: AgentDevices, binding: str) -> OkResponse:
    """成功回應，並如實帶上「這次送去的那台是不是假的」。

    `binding` 是端點用的裝置（見 `AgentDevices.simulated_for`）。每個端點各自寫
    `OkResponse(status="ok")` 的話，日後新增端點很容易漏掉註記——漏掉的後果是店員
    看到「已送出」卻拿不到紙。統一從這裡出，且未知的 binding 會直接 KeyError，
    不會安靜地當成「真的」。
    """
    return OkResponse(status="ok", simulated=devices.simulated_for(binding))


async def get_devices(request: Request) -> AgentDevices:
    """從 app.state 取得注入的裝置組合，供路由依賴。

    宣告為 async：此查找只讀 `app.state`、無 I/O，async 依賴會在事件迴圈上直接
    執行，避免被 FastAPI/AnyIO 派到 worker thread（在受限 ASGI 環境會卡住請求）。
    """
    devices: AgentDevices = request.app.state.devices
    return devices


DevicesDep = Annotated[AgentDevices, Depends(get_devices)]
