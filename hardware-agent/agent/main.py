"""硬體代理 localhost HTTP 服務（Wave 2.0 骨架：介面化 + DI + include_router）。

`create_app` 注入 `AgentDevices`（預設全 Fake），路由只透過 `request.app.state`
取得介面、不依賴具體實作；換 Fake↔真機只換注入。裝置失敗例外（`agent.errors`）
由統一 handler 轉成對應 HTTP 狀態，**不吞例外假裝成功**。

端點分工：
- 本檔：`/health`、`/print/label`、`/drawer/open`（已走介面）。
- **T15**：新增 `agent/routers/print.py`（receipt/detail/einvoice），在下方 include。
- **T16**：新增 `agent/routers/devices.py`（`/devices/status`），在下方 include。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.deps import DevicesDep, OkResponse, get_devices  # noqa: F401  (get_devices re-export)
from agent.devices import AgentDevices, default_fake_devices
from agent.errors import (
    CoverOpen,
    DeviceError,
    DeviceOffline,
    DeviceTimeout,
    DrawerNotConnected,
    PaperOut,
)

# 裝置失敗 → HTTP 狀態（離線/逾時為服務暫不可用；缺紙/上蓋/錢櫃未接為當前無法完成）
_DEVICE_ERROR_STATUS: dict[type[DeviceError], int] = {
    DeviceOffline: 503,
    DeviceTimeout: 504,
    PaperOut: 409,
    CoverOpen: 409,
    DrawerNotConnected: 409,
}


class LabelRequest(BaseModel):
    code: str
    name: str
    price: int


def create_app(devices: AgentDevices | None = None) -> FastAPI:
    """建立硬體代理應用程式；可注入裝置組合（預設全 Fake）。"""
    app = FastAPI(title="lu-camp hardware-agent", version="0.1.0")
    resolved = devices if devices is not None else default_fake_devices()
    if not isinstance(resolved, AgentDevices):
        # 早失敗、明確指路：Phase 0 的 create_app(printer: SupportsWrite) 介面已由
        # Wave 2.0 取代為注入 AgentDevices。誤傳舊型別在此即報，不拖到請求時才 AttributeError。
        raise TypeError(
            "create_app(devices=...) 需要 AgentDevices；Phase 0 的 "
            "create_app(printer=...) 介面已由 Wave 2.0 取代，請改注入 "
            "AgentDevices（見 agent.devices.default_fake_devices）。"
        )
    app.state.devices = resolved

    @app.exception_handler(DeviceError)
    async def _device_error_handler(_request: Request, exc: DeviceError) -> JSONResponse:
        status = _DEVICE_ERROR_STATUS.get(type(exc), 502)
        return JSONResponse(
            status_code=status,
            content={"detail": str(exc), "error": type(exc).__name__},
        )

    @app.get("/health", response_model=OkResponse, operation_id="agentHealth")
    async def health() -> OkResponse:
        return OkResponse(status="ok")

    @app.post("/print/label", response_model=OkResponse, operation_id="printLabel")
    async def label(req: LabelRequest, devices: DevicesDep) -> OkResponse:
        devices.label_printer.print_label(req.code, req.name, req.price)
        return OkResponse(status="ok")

    @app.post("/drawer/open", response_model=OkResponse, operation_id="openDrawer")
    async def drawer(devices: DevicesDep) -> OkResponse:
        devices.cash_drawer.open()
        return OkResponse(status="ok")

    # --- T15/T16 在此 include 各自的 router（避免彼此改同一 endpoint）---
    # 兩個 router 都從無循環的 agent.deps 取 DI（DevicesDep/OkResponse），
    # 故可在 create_app 末端延遲 include，不會與 module 層 app = create_app() 互咬。
    from agent.routers.devices import router as devices_router  # T16
    from agent.routers.print import router as print_router  # T15

    app.include_router(print_router)
    app.include_router(devices_router)

    return app


app = create_app()
