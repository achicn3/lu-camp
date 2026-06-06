"""硬體裝置失敗例外（Wave 2.0 骨架）。

驅動（Fake 或真機）以這些例外表達真實失敗，路由層 `agent.main` 將其轉為對應
HTTP 狀態。**禁止吞例外假裝成功**（CLAUDE.md §9）。Fake 必須能依設定丟出這些
例外，讓錯誤處理路徑在沒有實體機器時也被測到。
"""

from __future__ import annotations


class DeviceError(Exception):
    """所有硬體裝置錯誤的基底。"""


class DeviceOffline(DeviceError):
    """裝置離線／連線被拒（探測不到、Wi-Fi 斷、USB 未接）。"""


class DeviceTimeout(DeviceError):
    """裝置連線／回應逾時。"""


class PaperOut(DeviceError):
    """缺紙，無法列印。"""


class CoverOpen(DeviceError):
    """上蓋開啟，無法列印。"""


class DrawerNotConnected(DeviceError):
    """錢櫃未接到 drawer port，無法踢開。"""
