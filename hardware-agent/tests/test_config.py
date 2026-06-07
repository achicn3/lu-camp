"""裝置連線設定（agent.config）單元測試。

驗證：host 必填（未設/空白即報錯，不臆造 IP）、port/timeout 有預設且可由環境覆寫。
"""

from __future__ import annotations

import pytest

from agent.config import MissingDeviceConfigError, device_config_from_env


def test_reads_required_hosts_with_default_port_and_timeout() -> None:
    cfg = device_config_from_env(
        {"AGENT_BROTHER_HOST": "192.168.0.41", "AGENT_EPSON_HOST": "192.168.0.42"}
    )
    assert cfg.brother.host == "192.168.0.41"
    assert cfg.epson.host == "192.168.0.42"
    assert cfg.brother.port == 9100
    assert cfg.epson.port == 9100
    assert cfg.brother.timeout == 2.0
    assert cfg.epson.timeout == 2.0


def test_overrides_port_and_timeout() -> None:
    cfg = device_config_from_env(
        {
            "AGENT_BROTHER_HOST": "10.0.0.1",
            "AGENT_EPSON_HOST": "10.0.0.2",
            "AGENT_BROTHER_PORT": "515",
            "AGENT_EPSON_PORT": "9101",
            "AGENT_DEVICE_PROBE_TIMEOUT": "3.5",
        }
    )
    assert cfg.brother.port == 515
    assert cfg.epson.port == 9101
    assert cfg.brother.timeout == 3.5
    assert cfg.epson.timeout == 3.5


@pytest.mark.parametrize("missing", ["AGENT_BROTHER_HOST", "AGENT_EPSON_HOST"])
def test_missing_host_raises(missing: str) -> None:
    env = {"AGENT_BROTHER_HOST": "10.0.0.1", "AGENT_EPSON_HOST": "10.0.0.2"}
    del env[missing]
    with pytest.raises(MissingDeviceConfigError):
        device_config_from_env(env)


def test_blank_host_raises() -> None:
    with pytest.raises(MissingDeviceConfigError):
        device_config_from_env({"AGENT_BROTHER_HOST": "  ", "AGENT_EPSON_HOST": "10.0.0.2"})
