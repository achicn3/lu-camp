"""電子發票證明聯之年期別標示（附件一格式一第 3 項）。

條碼/QR **內容**一律以 Amego 平台回傳為權威（docs/24；Codex 第十八輪）——本機不再
產生一維條碼字串與 QR 加密驗證資訊（舊 T13 路線的 AES 產碼已移除），僅保留版面
所需的民國年期別文字。
"""

from __future__ import annotations

from datetime import date


def _roc_year(d: date) -> int:
    return d.year - 1911


def _period_month(d: date) -> int:
    """期別之雙數月份：1-2 月期 → 2、3-4 月期 → 4 …。"""
    return d.month + (d.month % 2)


def roc_period_label(d: date) -> str:
    """證明聯抬頭之年期別，例：「102年05-06月」。"""
    pm = _period_month(d)
    return f"{_roc_year(d)}年{pm - 1:02d}-{pm:02d}月"
