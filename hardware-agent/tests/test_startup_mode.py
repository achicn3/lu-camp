"""啟動模式必須明講（搬機器最大的地雷）。

原本的邏輯是「`AGENT_DEVICES` 不等於 real 就用假裝模式」——**沒設定也算**。
於是 launchd／開機腳本忘了帶環境變數時，代理會安靜地退回假裝列印：畫面顯示
「已送出」、紙永遠不出來，店員完全看不出哪裡錯。

改為：沒設就拒絕啟動。安靜的錯變成大聲的錯——服務起不來，前端會直接顯示
「無法連線硬體代理」，店員當場看得到。
"""

import pytest

from agent.config import MissingDeviceConfigError
from agent.main import devices_from_env


def test_missing_mode_refuses_to_start() -> None:
    """未設 AGENT_DEVICES → 明確報錯，而不是默默用假裝模式。"""
    with pytest.raises(MissingDeviceConfigError) as err:
        devices_from_env({})

    # 訊息要講得出「要設什麼」，否則店家看到錯誤也不知道怎麼辦
    assert "AGENT_DEVICES" in str(err.value)


def test_blank_mode_refuses_to_start() -> None:
    """只有空白等同沒設（.env 裡寫了 `AGENT_DEVICES=` 也要擋）。"""
    with pytest.raises(MissingDeviceConfigError):
        devices_from_env({"AGENT_DEVICES": "   "})


def test_unknown_mode_refuses_to_start() -> None:
    """打錯字（real→rael）不得默默退回假裝模式——那正是最容易發生的事。"""
    with pytest.raises(MissingDeviceConfigError):
        devices_from_env({"AGENT_DEVICES": "rael"})


def test_explicit_fake_is_allowed() -> None:
    """明講要假裝模式是合法的（自動化測試與無實機開發）——回 None 由 create_app 用 Fake。"""
    assert devices_from_env({"AGENT_DEVICES": "fake"}) is None


def test_case_and_whitespace_tolerated() -> None:
    """`Fake ` 這種大小寫/空白差異不該讓服務起不來。"""
    assert devices_from_env({"AGENT_DEVICES": " Fake "}) is None
