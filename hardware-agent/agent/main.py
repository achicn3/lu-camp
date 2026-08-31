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

import os
from collections.abc import Mapping
from pathlib import Path

import anyio.to_thread
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.config import MissingDeviceConfigError
from agent.deps import (  # noqa: F401  (get_devices re-export)
    DevicesDep,
    HealthResponse,
    OkResponse,
    get_devices,
    ok_response,
)
from agent.devices import AgentDevices, default_fake_devices, real_epson_devices_from_env
from agent.drivers.brother_label import LabelContentTooWide
from agent.drivers.signature_png import SignatureImageError
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
    # CORS：POS 前端（瀏覽器）直接呼叫代理列印，須允許其來源。來源由 AGENT_CORS_ORIGINS
    # （逗號分隔）提供，預設前端開發位址 http://localhost:3000。無認證、僅列印/狀態端點。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            o.strip()
            for o in os.environ.get("AGENT_CORS_ORIGINS", "http://localhost:3000").split(",")
            if o.strip()
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
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

    @app.exception_handler(MissingDeviceConfigError)
    async def _missing_config_handler(
        _request: Request, exc: MissingDeviceConfigError
    ) -> JSONResponse:
        # 設定缺漏（如電子發票 AES 金鑰未設）→ 503 如實回報，不偽裝成功也不露 traceback。
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "error": type(exc).__name__},
        )

    @app.exception_handler(SignatureImageError)
    async def _signature_image_handler(
        _request: Request, exc: SignatureImageError
    ) -> JSONResponse:
        # 簽名影像不可用（非 8-bit RGBA PNG/超限/空白）：呼叫端資料問題 → 422，不印壞證據。
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(LabelContentTooWide)
    async def _label_too_wide_handler(_request: Request, exc: LabelContentTooWide) -> JSONResponse:
        # 標籤內容（條碼/識別碼/價格）超出長度上限 → 422 請求內容問題（非裝置故障），
        # 如實拒印；條碼不可截斷（截斷即印出錯的碼）。
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "error": type(exc).__name__},
        )

    @app.get("/health", response_model=HealthResponse, operation_id="agentHealth")
    async def health(devices: DevicesDep) -> HealthResponse:
        # 健康檢查沒有「這次送去哪台」可言，回整體狀況並點名沒接上的那幾台。
        names = devices.simulated_devices
        return HealthResponse(status="ok", simulated=bool(names), simulated_devices=list(names))

    @app.post("/print/label", response_model=OkResponse, operation_id="printLabel")
    async def label(req: LabelRequest, devices: DevicesDep) -> OkResponse:
        # 真機列印為同步阻塞 I/O，卸載到 worker thread，勿阻塞事件迴圈。
        await anyio.to_thread.run_sync(
            devices.label_printer.print_label, req.code, req.name, req.price
        )
        return ok_response(devices, "label")

    @app.post("/drawer/open", response_model=OkResponse, operation_id="openDrawer")
    async def drawer(devices: DevicesDep) -> OkResponse:
        # 真機踢櫃為同步阻塞 I/O（網路），卸載到 worker thread，勿阻塞事件迴圈。
        await anyio.to_thread.run_sync(devices.cash_drawer.open)
        return ok_response(devices, "drawer")

    # --- T15/T16 在此 include 各自的 router（避免彼此改同一 endpoint）---
    # 兩個 router 都從無循環的 agent.deps 取 DI（DevicesDep/OkResponse），
    # 故可在 create_app 末端延遲 include，不會與 module 層 app = create_app() 互咬。
    from agent.routers.devices import router as devices_router  # T16
    from agent.routers.print import router as print_router  # T15

    app.include_router(print_router)
    app.include_router(devices_router)

    return app


_MODE_REAL = "real"
_MODE_FAKE = "fake"


def devices_from_env(env: Mapping[str, str] | None = None) -> AgentDevices | None:
    """依 `AGENT_DEVICES` 選注入：`real` → 真機組合；`fake` → None（由 create_app 用 Fake）。

    **未設或無法辨識的值一律拒絕啟動**，不再默默退回假裝模式。

    為什麼要這麼嚴：假裝模式收到列印就回「成功」，紙卻不會出來。開機腳本／launchd
    忘了帶這個環境變數時，舊行為是安靜地變成假裝模式——畫面顯示「已送出」、客人拿不到
    收據，而店員完全看不出哪裡錯。拒絕啟動則會讓前端直接顯示「無法連線硬體代理」，
    當場就看得見。打錯字（real→rael）同樣要擋，那是最容易發生的情況。
    """
    resolved = os.environ if env is None else env
    mode = resolved.get("AGENT_DEVICES", "").strip().lower()
    if mode == _MODE_REAL:
        return real_epson_devices_from_env()
    if mode == _MODE_FAKE:
        return None
    raise MissingDeviceConfigError(
        "環境變數 AGENT_DEVICES 未設定或無法辨識"
        f"（收到 {resolved.get('AGENT_DEVICES', '')!r}）。"
        f"請明確設為 {_MODE_REAL}（接真機列印）或 {_MODE_FAKE}（不列印，開發與測試用）。"
        "不給預設值是刻意的：假裝模式會回報列印成功但不出紙。"
    )


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def build_app() -> FastAPI:
    """正式啟動的入口：`uvicorn agent.main:build_app --factory`。

    **刻意不在 import 時建立 app**：測試會 import 本模組，而 `.env` 不入庫；若在
    import 時就驗設定，乾淨 checkout 執行 `uv run pytest` 會在收集階段整個拋錯——
    品質關卡必須在新機器上直接跑得起來（Codex 審查 Standards 項）。

    先載入同目錄的 `.env`（若存在）：launchd／開機腳本很容易漏帶環境變數，而漏帶的
    後果是整台機器安靜地不列印，自己讀一份就少一個必須有人記得的步驟。**已存在的
    環境變數優先**——正式部署若在 plist 明確指定，不會被檔案蓋掉。
    """
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=False)
    return create_app(devices_from_env())
