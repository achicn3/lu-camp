"""sim_180d 的純函式輔助（docs/27 Phase 1）。

全部無 I/O、可單元測試：日程/客流模型、合法身分證產生器、唯一電話、
擬真簽名 PNG（每張不同筆跡）、含稅定價。
"""

from __future__ import annotations

import base64
import math
import random
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from app.core.money import round_ntd
from app.core.time import STORE_TIME_ZONE

# 身分證字母 → 兩位數字（戶役政編碼）
_NID_LETTER: dict[str, int] = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17,
    "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23,
    "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28, "V": 29, "W": 32, "X": 30,
    "Y": 31, "Z": 33,
}
_NID_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)


def make_national_id(rng: random.Random) -> str:
    """產生檢核碼合法的身分證字號（隨機縣市/性別/流水）。"""
    letter = rng.choice(sorted(_NID_LETTER))
    code = _NID_LETTER[letter]
    digits = [rng.choice((1, 2))] + [rng.randint(0, 9) for _ in range(7)]
    total = (code // 10) * 1 + (code % 10) * 9
    total += sum(d * w for d, w in zip(digits, _NID_WEIGHTS, strict=True))
    check = (10 - total % 10) % 10
    return letter + "".join(str(d) for d in digits) + str(check)


def make_phone(rng: random.Random, seq: int) -> str:
    """同店唯一手機（contacts UNIQUE(store_id, phone)）：以 seq 保證唯一。"""
    return f"09{seq % 100_000_000:08d}"


def daily_sales_target(day_index: int, weekday: int, rng: random.Random) -> int:
    """某日銷售筆數：週間低/週末高＋季節正弦波動。day_index 0=最舊。

    期望值約 21.5/日 → 200 天 ≈ 4,300 筆。
    """
    base = rng.randint(35, 65) if weekday >= 5 else rng.randint(12, 28)
    season = 1.0 + 0.25 * math.sin(day_index / 200.0 * 2 * math.pi)
    return max(1, int(base * season))


def simulation_day_start(now: datetime, total_days: int, day_index: int) -> datetime:
    """回傳模擬營業日基準瞬間；固定樣本跨過台灣午夜以覆蓋日界。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("模擬基準時間必須包含時區")
    if total_days <= 0 or not 0 <= day_index < total_days:
        raise ValueError("模擬日索引超出範圍")
    target_date = now.astimezone(STORE_TIME_ZONE).date() - timedelta(days=total_days - day_index)
    # _shift_day 的新列依序加 75 秒；23:58 基準會讓前兩列落在 23:59:15 / 00:00:30。
    wall_time = time(23, 58) if day_index % 45 == 22 else time(10)
    return datetime.combine(target_date, wall_time, tzinfo=STORE_TIME_ZONE).astimezone(UTC)


def suggested_price(cost: int, margin_pct: int) -> Decimal:
    """收購定價計算機（CLAUDE.md §7-9）：round_ntd(cost ÷ (1 − margin/100))。"""
    if not 0 <= margin_pct <= 99:
        raise ValueError("margin_pct 限 0–99")
    return Decimal(round_ntd(Decimal(cost) / (Decimal(1) - Decimal(margin_pct) / Decimal(100))))


def signature_png(rng: random.Random, width: int = 240, height: int = 100) -> str:
    """擬真簽名 PNG（8-bit RGBA，type 6＝後端唯一接受的 canvas 輸出）。

    每張筆跡不同：雙頻正弦主筆劃＋一撇，滿足後端「非空白墨跡」門檻
    （≥100 墨點、涵蓋跨度）。回傳 base64。
    """
    phase = rng.uniform(0, math.pi)
    amp1, amp2 = rng.uniform(8, 16), rng.uniform(3, 7)
    freq1, freq2 = rng.uniform(1.5, 2.5), rng.uniform(4.0, 6.0)
    thick = rng.randint(2, 3)
    grid = [[False] * width for _ in range(height)]
    cy = height // 2
    for x in range(10, width - 10):
        t = (x - 10) / (width - 20)
        y = cy + int(
            amp1 * math.sin(freq1 * math.pi * t + phase) + amp2 * math.sin(freq2 * math.pi * t)
        )
        for dy in range(-thick, thick + 1):
            yy = y + dy
            if 0 <= yy < height:
                grid[yy][x] = True
    # 一撇（右上到左下），像手寫勾尾
    x0, y0 = rng.randint(width // 2, width - 20), rng.randint(10, cy)
    for i in range(rng.randint(18, 30)):
        xx, yy = x0 - i, y0 + i
        if 0 <= xx < width and 0 <= yy < height:
            grid[yy][xx] = True
            if yy + 1 < height:
                grid[yy + 1][xx] = True

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter byte: None
        for x in range(width):
            raw += b"\x1b\x1b\x1b\xff" if grid[y][x] else b"\xff\xff\xff\xff"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


@dataclass(frozen=True)
class DayPlan:
    """單一模擬日的事件配額。"""

    day_index: int  # 0 = 最舊；DAYS-1 = 昨天
    weekday: int
    n_sales: int
    n_buyout: int
    n_consign_intake: int
    make_bulk_lot: bool
    po_action: bool
    settle_payout_day: bool
    stocktake_day: bool


def build_schedule(days: int, seed: int) -> list[DayPlan]:
    """整段模擬期的日程（決定性：同 seed 同結果）。"""
    rng = random.Random(seed)
    plans: list[DayPlan] = []
    for i in range(days):
        weekday = i % 7  # 模擬曆：0–4 週間、5–6 週末（與真實星期無關，僅供客流模型）
        plans.append(
            DayPlan(
                day_index=i,
                weekday=weekday,
                n_sales=daily_sales_target(i, weekday, rng),
                n_buyout=max(0, int(rng.gauss(3.6, 1.8))),
                n_consign_intake=max(0, int(rng.gauss(1.7, 1.2))),
                make_bulk_lot=(i % 5 == 2),
                po_action=True,
                settle_payout_day=(i % 7 == 6),
                stocktake_day=(i % 25 == 24),
            )
        )
    return plans
