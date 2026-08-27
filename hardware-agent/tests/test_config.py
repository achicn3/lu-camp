"""裝置連線設定（agent.config）單元測試。

驗證：host 必填（未設/空白即報錯，不臆造 IP）、port/timeout 有預設且可由環境覆寫。
"""

from __future__ import annotations

import pytest

from agent.config import (
    MissingDeviceConfigError,
    brother_endpoint_from_env,
    device_config_from_env,
    drawer_endpoint_from_env,
    epson_endpoint_from_env,
    invoice_endpoint_from_env,
    kitchen_endpoint_from_env,
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


class TestKitchenEndpointFromEnv:
    """出餐機（第二台 EPSON）選配（docs/35）。"""

    def test_unset_returns_none(self) -> None:
        """未接第二台回 None——出餐單即印到收據機，不得因此報錯或壞掉。"""
        assert kitchen_endpoint_from_env({}) is None
        assert kitchen_endpoint_from_env({"AGENT_KITCHEN_HOST": "   "}) is None

    def test_set_returns_endpoint_with_defaults(self) -> None:
        endpoint = kitchen_endpoint_from_env({"AGENT_KITCHEN_HOST": "192.0.2.60"})
        assert endpoint is not None
        assert (endpoint.host, endpoint.port, endpoint.timeout) == ("192.0.2.60", 9100, 2.0)

    def test_overrides_port_and_timeout(self) -> None:
        endpoint = kitchen_endpoint_from_env(
            {
                "AGENT_KITCHEN_HOST": "192.0.2.60",
                "AGENT_KITCHEN_PORT": "9101",
                "AGENT_DEVICE_PROBE_TIMEOUT": "3.5",
            }
        )
        assert endpoint is not None
        assert (endpoint.port, endpoint.timeout) == (9101, 3.5)

    def test_does_not_borrow_the_receipt_printer_host(self) -> None:
        """**不得**以 EPSON 的 host 當預設：那會讓「沒接第二台」被誤判成已接，
        狀態頁多出一台其實不存在的出餐機。"""
        assert kitchen_endpoint_from_env({"AGENT_EPSON_HOST": "192.168.0.42"}) is None


class TestPrinterEncoding:
    """每台印表機的中文編碼由設定決定（實機 2026-08-27：兩台 TM-T82III 字型 ROM 不同）。

    `.42`（S/N X7BJ020997、fw 15.05）回報 `TAIWAN BIG-5`，`.44`（S/N X7B4090696、
    fw 20.09）回報 `CHINA GB18030`——把 Big5 位元組送到後者，中文全是亂碼。
    編碼因此必須跟著「哪一台」走，不能是全域常數。
    """

    def test_defaults_to_big5(self) -> None:
        """未設即 Big5：既有單機店家（只有一台繁體機）升級後行為不變。"""
        assert epson_endpoint_from_env({"AGENT_EPSON_HOST": "10.0.0.2"}).encoding == "big5"

    def test_env_override_per_printer(self) -> None:
        endpoint = epson_endpoint_from_env(
            {"AGENT_EPSON_HOST": "10.0.0.2", "AGENT_EPSON_ENCODING": "gbk"}
        )
        assert endpoint.encoding == "gbk"

    def test_unknown_encoding_raises(self) -> None:
        """打錯編碼名**不得**默默退回 Big5——那會在實機印出一整捲亂碼才被發現。"""
        with pytest.raises(MissingDeviceConfigError):
            epson_endpoint_from_env(
                {"AGENT_EPSON_HOST": "10.0.0.2", "AGENT_EPSON_ENCODING": "big-five"}
            )

    def test_encoding_is_normalised(self) -> None:
        """大小寫/前後空白不該讓設定失效（`GBK ` 與 `gbk` 同義）。"""
        endpoint = epson_endpoint_from_env(
            {"AGENT_EPSON_HOST": "10.0.0.2", "AGENT_EPSON_ENCODING": " GBK "}
        )
        assert endpoint.encoding == "gbk"

    def test_each_printer_reads_its_own_encoding(self) -> None:
        """發票機與收據機互不借用編碼：兩台字型 ROM 不同才是本次改動的起因。"""
        env = {
            "AGENT_EPSON_HOST": "192.0.2.44",
            "AGENT_EPSON_ENCODING": "gbk",
            "AGENT_INVOICE_HOST": "192.0.2.42",
        }
        invoice = invoice_endpoint_from_env(env)
        assert invoice is not None
        assert epson_endpoint_from_env(env).encoding == "gbk"
        assert invoice.encoding == "big5"  # 未設 → 預設 Big5，不繼承收據機的 gbk

    def test_kitchen_reads_its_own_encoding(self) -> None:
        endpoint = kitchen_endpoint_from_env(
            {"AGENT_KITCHEN_HOST": "192.0.2.60", "AGENT_KITCHEN_ENCODING": "gbk"}
        )
        assert endpoint is not None
        assert endpoint.encoding == "gbk"


class TestInvoiceEndpointFromEnv:
    """發票專屬機（第二台 EPSON）選配：未設 → 證明聯印回收據機（單機店家行為不變）。"""

    def test_unset_returns_none(self) -> None:
        assert invoice_endpoint_from_env({}) is None
        assert invoice_endpoint_from_env({"AGENT_INVOICE_HOST": "   "}) is None

    def test_set_returns_endpoint_with_defaults(self) -> None:
        endpoint = invoice_endpoint_from_env({"AGENT_INVOICE_HOST": "192.0.2.42"})
        assert endpoint is not None
        assert (endpoint.host, endpoint.port, endpoint.timeout) == ("192.0.2.42", 9100, 2.0)

    def test_overrides_port_and_timeout(self) -> None:
        endpoint = invoice_endpoint_from_env(
            {
                "AGENT_INVOICE_HOST": "192.0.2.42",
                "AGENT_INVOICE_PORT": "9101",
                "AGENT_DEVICE_PROBE_TIMEOUT": "3.5",
            }
        )
        assert endpoint is not None
        assert (endpoint.port, endpoint.timeout) == (9101, 3.5)

    def test_does_not_borrow_the_receipt_printer_host(self) -> None:
        """**不得**以 EPSON 的 host 當預設：那會讓「沒接發票機」被誤判成已接。"""
        assert invoice_endpoint_from_env({"AGENT_EPSON_HOST": "192.0.2.44"}) is None


class TestDrawerEndpointFromEnv:
    """錢櫃走哪一台的 drawer port（實機：線插在發票機那台，非收據機）。

    回傳**具體端點**而非 `None`：錢櫃一定接在某一台上，解析只在此處做一次，
    呼叫端（裝置組裝、狀態探測）不得各自 or 一次而漂移。
    """

    def test_defaults_to_the_receipt_printer(self) -> None:
        """未設 → 沿用收據機（現行單機行為，升級不得改變）。"""
        endpoint = drawer_endpoint_from_env({"AGENT_EPSON_HOST": "192.0.2.44"})
        assert (endpoint.host, endpoint.port) == ("192.0.2.44", 9100)

    def test_explicit_host_wins(self) -> None:
        endpoint = drawer_endpoint_from_env(
            {"AGENT_EPSON_HOST": "192.0.2.44", "AGENT_DRAWER_HOST": "192.0.2.42"}
        )
        assert endpoint.host == "192.0.2.42"

    def test_overrides_port(self) -> None:
        endpoint = drawer_endpoint_from_env(
            {
                "AGENT_EPSON_HOST": "192.0.2.44",
                "AGENT_DRAWER_HOST": "192.0.2.42",
                "AGENT_DRAWER_PORT": "9101",
            }
        )
        assert (endpoint.host, endpoint.port) == ("192.0.2.42", 9101)

    def test_missing_receipt_host_still_raises_when_unset(self) -> None:
        """沒有 EPSON host 又沒指定錢櫃 host → 無處可退，照樣報錯（不臆造 IP）。"""
        with pytest.raises(MissingDeviceConfigError):
            drawer_endpoint_from_env({})
