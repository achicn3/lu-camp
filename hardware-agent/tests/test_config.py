"""裝置連線設定（agent.config）單元測試。

驗證：host 必填（未設/空白即報錯，不臆造 IP）、port/timeout 有預設且可由環境覆寫。
"""

from __future__ import annotations

import pytest

from agent.config import (
    MissingDeviceConfigError,
    brother_endpoint_from_env,
    device_config_from_env,
    label_font_path_from_env,
)


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


class TestBrotherEndpointFromEnv:
    def test_unset_returns_none(self) -> None:
        """Brother 選配：未設 host 回 None（不列管、標籤機維持 Fake），不報錯。"""
        assert brother_endpoint_from_env({}) is None
        assert brother_endpoint_from_env({"AGENT_BROTHER_HOST": "   "}) is None

    def test_set_returns_endpoint_with_defaults(self) -> None:
        endpoint = brother_endpoint_from_env({"AGENT_BROTHER_HOST": "192.0.2.45"})
        assert endpoint is not None
        assert endpoint.host == "192.0.2.45"
        assert endpoint.port == 9100
        assert endpoint.timeout == 2.0

    def test_overrides_port_and_timeout(self) -> None:
        endpoint = brother_endpoint_from_env(
            {
                "AGENT_BROTHER_HOST": "192.0.2.45",
                "AGENT_BROTHER_PORT": "515",
                "AGENT_DEVICE_PROBE_TIMEOUT": "3.5",
            }
        )
        assert endpoint is not None
        assert endpoint.port == 515
        assert endpoint.timeout == 3.5


class TestLabelFontPathFromEnv:
    def test_default_is_bundled_noto_and_exists(self) -> None:
        from pathlib import Path

        path = label_font_path_from_env({})
        assert Path(path).is_file()
        assert path.endswith("NotoSansTC.ttf")  # repo 內建（OFL 授權）

    def test_env_override(self, tmp_path: object) -> None:
        from pathlib import Path

        font = Path(str(tmp_path)) / "custom.ttf"
        font.write_bytes(b"stub")
        assert label_font_path_from_env({"AGENT_LABEL_FONT": str(font)}) == str(font)

    def test_missing_override_raises(self) -> None:
        with pytest.raises(MissingDeviceConfigError):
            label_font_path_from_env({"AGENT_LABEL_FONT": "/no/such/font.ttf"})
