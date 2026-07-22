"""sim_helpers 純函式單元測試（qa_e2e 專用；隨主測試套件執行）。"""

from __future__ import annotations

import base64
import random
import zlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from qa_e2e.sim_helpers import (
    build_schedule,
    daily_sales_target,
    make_national_id,
    make_phone,
    signature_png,
    simulation_day_start,
    suggested_price,
)

from app.core.national_id import is_valid_national_id


def test_national_id_generator_passes_backend_validator() -> None:
    rng = random.Random(42)
    ids = {make_national_id(rng) for _ in range(300)}
    assert all(is_valid_national_id(nid) for nid in ids)
    assert len(ids) > 250  # 幾乎不重複


def test_phone_unique_by_seq() -> None:
    rng = random.Random(1)
    phones = {make_phone(rng, i) for i in range(5000)}
    assert len(phones) == 5000
    assert all(p.startswith("09") and len(p) == 10 for p in phones)


def test_daily_sales_weekend_higher_on_average() -> None:
    rng = random.Random(7)
    weekday_avg = sum(daily_sales_target(50, 2, rng) for _ in range(200)) / 200
    weekend_avg = sum(daily_sales_target(50, 6, rng) for _ in range(200)) / 200
    assert weekend_avg > weekday_avg * 1.5


def test_suggested_price_matches_margin_formula() -> None:
    assert suggested_price(1000, 45) == Decimal(1818)  # 1000/0.55 = 1818.18 → 1818
    assert suggested_price(1800, 0) == Decimal(1800)
    with pytest.raises(ValueError):
        suggested_price(1000, 100)


def test_signature_png_structure_and_variety() -> None:
    rng = random.Random(9)
    a, b = signature_png(rng), signature_png(rng)
    assert a != b  # 每張筆跡不同
    raw = base64.b64decode(a)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    # IHDR：8-bit RGBA（color type 6）＝後端唯一接受形式
    ihdr = raw[16:29]
    assert ihdr[8] == 8 and ihdr[9] == 6
    # IDAT 可解壓且墨點充足（後端 require_visible_ink 門檻 100）
    idat_start = raw.index(b"IDAT") + 4
    idat_len = int.from_bytes(raw[idat_start - 8 : idat_start - 4], "big")
    pixels = zlib.decompress(raw[idat_start : idat_start + idat_len])
    ink = sum(1 for i in range(0, len(pixels), 4) if pixels[i : i + 3] not in (b"\xff\xff\xff",))
    assert ink > 200


def test_build_schedule_deterministic_and_sized() -> None:
    s1, s2 = build_schedule(200, seed=123), build_schedule(200, seed=123)
    assert s1 == s2
    assert len(s1) == 200
    assert sum(p.n_sales for p in s1) > 3500  # 期望 ≈4,300，寬鬆下限
    assert sum(1 for p in s1 if p.stocktake_day) == 8
    assert sum(p.n_buyout for p in s1) > 400


def test_simulation_timeline_deterministically_crosses_taipei_midnight() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    taipei = ZoneInfo("Asia/Taipei")
    starts = [simulation_day_start(now, 180, day) for day in range(180)]
    boundary_days = [
        day
        for day, start in enumerate(starts)
        if start.astimezone(taipei).strftime("%H:%M") == "23:58"
    ]

    assert boundary_days == [22, 67, 112, 157]
    for day in boundary_days:
        first_row = (starts[day] + timedelta(seconds=75)).astimezone(taipei)
        second_row = (starts[day] + timedelta(seconds=150)).astimezone(taipei)
        assert first_row.strftime("%H:%M:%S") == "23:59:15"
        assert second_row.strftime("%H:%M:%S") == "00:00:30"
        assert second_row.date() == first_row.date() + timedelta(days=1)
