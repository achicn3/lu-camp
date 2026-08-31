"""假裝模式必須在回應裡承認自己是假的（地雷的第三層）。

第二層（未設模式即拒絕啟動）擋得住「忘了設」，但擋不住「有人刻意設成 fake 又忘了改
回來」。那種情況下列印一樣會回成功、紙一樣不會出來。所以回應要帶註記，讓前端能對
店員直說「這次沒有真的列印」。
"""

from dataclasses import replace

import httpx

from agent.devices import default_fake_devices, real_epson_devices_from_env
from agent.main import create_app


async def _post(app: object, path: str, json: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=json)


async def test_fake_mode_marks_the_response_as_simulated() -> None:
    resp = await _post(create_app(default_fake_devices()), "/print/label",
                       {"code": "SN-1", "name": "帳篷", "price": 100})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["simulated"] is True  # ← 沒有這個註記，前端就無從對店員說實話


async def test_response_mirrors_the_device_set_instead_of_hardcoding_the_mark() -> None:
    """非假裝的裝置組合不得被標記，否則正常列印也會被說成「沒有真的印」。

    這裡刻意用假驅動但 `simulated=False` 的組合：真機在測試環境不可達，列印會以 503
    結束，回應根本沒有 `simulated` 欄位——那種測試無論實作怎麼寫都會過（斷言為了錯的
    理由成立）。要驗的是「回應照實反映裝置組合」，就得讓列印真的成功。
    """
    devices = replace(default_fake_devices(), simulated=False)

    resp = await _post(create_app(devices), "/print/label",
                       {"code": "SN-1", "name": "帳篷", "price": 100})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "simulated": False}


def test_real_device_set_is_not_simulated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """真機工廠必須產出未標記的組合——否則整間店的列印都會被加上假裝註記。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "203.0.113.44")
    monkeypatch.setenv("AGENT_DEVICE_PROBE_TIMEOUT", "0.01")

    assert real_epson_devices_from_env().simulated is False
