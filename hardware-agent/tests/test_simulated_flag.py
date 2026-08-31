"""假裝模式必須在回應裡承認自己是假的（地雷的第三層）。

第二層（未設模式即拒絕啟動）擋得住「忘了設」，但擋不住兩件事：
1. 有人刻意設成 fake 又忘了改回來；
2. **real 模式下個別裝置沒設 host、悄悄退回 Fake**——例如標籤機。整組共用一個
   「是不是假裝」的旗標會謊報這種情形（Codex 審查高風險項）。

所以「假裝」不是整組的屬性，而是**逐台**的事實，且由實際注入的物件推得，不由工廠
自己宣告——工廠日後多接一台而忘了改旗標，就又回到謊報。
"""

from dataclasses import replace

import httpx
import pytest

from agent.config import PrinterEndpoint
from agent.devices import default_fake_devices, real_epson_devices_from_env
from agent.drivers.escpos_network import NetworkEscposWriter
from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.main import create_app


@pytest.fixture
def real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """真機環境變數，但**刻意不設標籤機**——這是專案支援的配置（標籤機選配）。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "203.0.113.44")
    monkeypatch.setenv("AGENT_INVOICE_HOST", "203.0.113.42")
    monkeypatch.setenv("AGENT_DRAWER_HOST", "203.0.113.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)
    monkeypatch.setenv("AGENT_DEVICE_PROBE_TIMEOUT", "0.01")


async def _post(app: object, path: str, json: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=json)


async def _get(app: object, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def test_fake_devices_report_every_device_as_simulated() -> None:
    assert set(default_fake_devices().simulated_devices) == {"標籤機", "收據機", "錢櫃"}


def test_real_devices_report_only_the_unconfigured_one(real_env: None) -> None:
    """沒設 host 的標籤機必須被點名；有設的收據機/錢櫃不得被點名。

    整組回一個 False（改動前的作法）會讓「按了列印標籤、卻什麼都沒出來」完全無跡可循。
    """
    assert real_epson_devices_from_env().simulated_devices == ("標籤機",)


async def test_label_print_admits_it_did_nothing_when_only_the_label_printer_is_fake(
    real_env: None,
) -> None:
    app = create_app(real_epson_devices_from_env())

    resp = await _post(app, "/print/label", {"code": "SN-1", "name": "帳篷", "price": 100})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "simulated": True}


async def test_health_names_which_devices_are_simulated(real_env: None) -> None:
    """畫面要對店員說「哪一台」沒接上；只說「測試模式」他不知道還能不能結帳。"""
    resp = await _get(create_app(real_epson_devices_from_env()), "/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "simulated": True, "simulated_devices": ["標籤機"]}


async def test_receipt_bound_endpoint_is_not_marked_when_its_printer_is_real(
    real_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """標籤機是假的，不代表出餐單也是假的——逐台認定不可退化成整組認定。

    出餐單走的是收據機（未設出餐機時），該台已設 host＝真機，故不得標記。
    真機在測試環境不可達，列印會失敗；這裡直接驗端點綁定的裝置本身。
    """
    devices = real_epson_devices_from_env()

    assert devices.simulated_for("kitchen") is False
    assert devices.simulated_for("label") is True


def test_every_endpoint_binding_is_known() -> None:
    """端點對裝置的對照表不得漏項：漏掉的端點會沿用整組判斷而悄悄謊報。"""
    devices = default_fake_devices()
    for binding in ("label", "receipt", "kitchen", "einvoice", "drawer"):
        assert devices.simulated_for(binding) is True


def test_simulated_is_derived_from_the_objects_not_from_a_declared_flag() -> None:
    """把真的收據機換進一組 Fake 裡，該台就不該再被點名。

    這證明判定看的是**實際注入的物件**，而不是工廠自己宣告的旗標——後者只要有人日後
    多接一台而忘了同步，就會再次謊報（正是本次 Codex 審查抓到的高風險項）。
    """
    fakes = default_fake_devices()
    mixed = replace(
        fakes,
        receipt_printer=EscposReceiptPrinter(
            NetworkEscposWriter(PrinterEndpoint(host="203.0.113.44")), encoding="big5"
        ),
    )

    assert "收據機" in fakes.simulated_devices
    assert "收據機" not in mixed.simulated_devices
    assert "標籤機" in mixed.simulated_devices
