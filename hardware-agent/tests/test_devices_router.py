"""T16 裝置狀態端點測試（A 級）。

TDD step 1：先寫會失敗的測試，確認 404/ImportError 才算真正失敗。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

from agent.devices import AgentDevices, default_fake_devices
from agent.fakes import FakeStatusProvider
from agent.interfaces import DeviceKind, DeviceStatus
from agent.main import create_app

_AGENT_ROOT = Path(__file__).resolve().parent.parent


def test_devices_router_importable_in_isolation() -> None:
    """回歸測試：agent.routers.devices 必須能在全新直譯器中獨立匯入，
    防止 router↔main 循環匯入再度發生（DI 應放在無循環的 agent.deps）。"""
    result = subprocess.run(
        [sys.executable, "-c", "import agent.routers.devices as d; assert d.router"],
        cwd=_AGENT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def test_get_devices_status_returns_200_with_default_fake() -> None:
    """GET /devices/status 以預設 Fake 應回傳 200，body 包含 devices 清單。"""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/devices/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "devices" in body
    assert isinstance(body["devices"], list)
    assert len(body["devices"]) >= 1


async def test_get_devices_status_reflects_online_true() -> None:
    """端點回傳的 devices 中應有 online=True 的裝置（Fake 預設全在線）。"""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/devices/status")
    assert resp.status_code == 200
    devices = resp.json()["devices"]
    assert any(d["online"] is True for d in devices)


async def test_get_devices_status_reflects_offline_device() -> None:
    """以 FakeStatusProvider 放一台 offline 裝置，端點應如實反映 online=False。"""
    offline_device = DeviceStatus(
        id="label-offline",
        kind=DeviceKind.LABEL_PRINTER,
        model="Brother QL-810W",
        online=False,
        last_seen=None,
        unsupported=["paper_out", "cover_open", "error"],
        driver="fake",
        validated_on_hardware=False,
    )
    provider = FakeStatusProvider(statuses=[offline_device])
    devices_obj = default_fake_devices()
    app = create_app(
        AgentDevices(
            label_printer=devices_obj.label_printer,
            receipt_printer=devices_obj.receipt_printer,
            cash_drawer=devices_obj.cash_drawer,
            status_provider=provider,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/devices/status")
    assert resp.status_code == 200
    devices = resp.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["online"] is False
    assert devices[0]["id"] == "label-offline"


async def test_get_devices_status_brother_unsupported_contains_b_grade_keys() -> None:
    """Brother QL-810W 的 unsupported 清單必須包含 B 級鍵（Wi-Fi 下不支援）。"""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/devices/status")
    devices = resp.json()["devices"]
    brother = next((d for d in devices if d["model"] == "Brother QL-810W"), None)
    assert brother is not None, "Brother QL-810W 裝置不存在於回應中"
    unsupported = brother["unsupported"]
    # Wi-Fi 下 B 級三項皆應標為 unsupported（docs/15 §2）
    assert "paper_out" in unsupported
    assert "cover_open" in unsupported
    assert "error" in unsupported


def test_devices_router_operation_id() -> None:
    """確認 /devices/status 端點 operation_id=getDevicesStatus。"""
    app = create_app()
    openapi = app.openapi()
    paths = openapi.get("paths", {})
    assert "/devices/status" in paths, "/devices/status 路徑未出現在 OpenAPI schema"
    get_op = paths["/devices/status"].get("get", {})
    assert get_op.get("operationId") == "getDevicesStatus"
