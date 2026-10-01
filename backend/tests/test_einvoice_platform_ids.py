"""送 Amego 的外部識別碼（OrderId／折讓單號）：建立當下隨機產生、持久化，不再由流水號推導。

流水號推導的識別碼在資料庫重建後會倒退重用，撞上平台上的舊紀錄（2026-10-01 正式機實際
發生：S1-4、S1-5 撞上 9/18 的測試發票）。隨機段讓「重建後同號」實際上不可能發生。
"""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.modules.einvoice.platform_ids import (
    new_platform_allowance_number,
    new_platform_order_id,
    rotated_platform_order_id,
)

_SAFE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_INT32_MAX = 2_147_483_647


def test_order_id_keeps_store_and_sale_for_humans_and_adds_random_suffix() -> None:
    order_id = new_platform_order_id(store_id=1, sale_id=5)

    assert re.fullmatch(rf"S1-5-[{_SAFE_CHARS}]{{8}}", order_id)


def test_order_id_never_repeats_for_the_same_sale() -> None:
    """同一個 sale_id（資料庫重建後倒退重用）也要拿到不同的編號。"""
    ids = {new_platform_order_id(store_id=1, sale_id=5) for _ in range(200)}

    assert len(ids) == 200


def test_order_id_fits_amego_40_char_limit_at_max_ids() -> None:
    assert len(new_platform_order_id(store_id=_INT32_MAX, sale_id=_INT32_MAX)) <= 40


def test_order_id_avoids_look_alike_characters() -> None:
    """店長可能要在光貿後台照著打：不用 0/O、1/I 這種會看錯的字。"""
    for _ in range(200):
        suffix = new_platform_order_id(store_id=1, sale_id=1).rsplit("-", 1)[1]
        assert not set(suffix) & set("01OI")


def test_allowance_number_fits_amego_16_char_limit_with_long_random_part() -> None:
    number = new_platform_allowance_number(store_id=1)

    assert len(number) == 16
    assert re.fullmatch(rf"L1-[{_SAFE_CHARS}]{{13}}", number)


def test_allowance_number_never_repeats() -> None:
    numbers = {new_platform_allowance_number(store_id=1) for _ in range(200)}

    assert len(numbers) == 200


def test_allowance_number_refuses_store_ids_that_leave_too_little_randomness() -> None:
    """16 字上限扣掉店號後亂數段太短就不安全，寧可拒絕也不默默縮短。"""
    with pytest.raises(ValueError):
        new_platform_allowance_number(store_id=10_000_000)


# ── 撞號後換的新編號：可重算（冪等），不可每次重抽 ──
#
# 換號後開立成功、資料庫又從換號前的備份還原：那一列回到舊編號、再撞一次。若再抽一組新亂數，
# 新編號在平台查無 → 送出 → 同一筆交易兩張發票（code-reviewer H1）。由還原前後都不變的值
# 推導，還原後算回同一個編號，對帳先行就會查到本筆、補記而不重送。

_CREATED = datetime(2026, 10, 1, 15, 37, 41, 914569, tzinfo=UTC)


def _rotated(*, previous_order_id: str = "S1-4", invoice_created_at: datetime = _CREATED) -> str:
    return rotated_platform_order_id(
        store_id=1,
        sale_id=4,
        invoice_id=4,
        previous_order_id=previous_order_id,
        invoice_created_at=invoice_created_at,
    )


def test_rotated_order_id_is_reproducible_after_restore() -> None:
    assert _rotated() == _rotated()


def test_rotated_order_id_ignores_how_the_timestamp_is_zoned() -> None:
    """還原後連線時區不同，讀回的 datetime 可能換了 tzinfo；同一瞬間要算出同一個編號。"""
    taipei = _CREATED.astimezone(timezone(timedelta(hours=8)))

    assert _rotated(invoice_created_at=taipei) == _rotated()


def test_rotated_order_id_differs_after_a_fresh_rebuild() -> None:
    """重建資料庫後同一個 sale_id 的發票建立時間不同 → 不同編號，舊撞號不會重演。"""
    later = _CREATED + timedelta(microseconds=1)

    assert _rotated(invoice_created_at=later) != _rotated()


def test_rotated_order_id_changes_with_each_rotation_and_keeps_the_format() -> None:
    first = _rotated()
    second = _rotated(previous_order_id=first)

    assert re.fullmatch(rf"S1-4-[{_SAFE_CHARS}]{{8}}", first)
    assert re.fullmatch(rf"S1-4-[{_SAFE_CHARS}]{{8}}", second)
    assert len({"S1-4", first, second}) == 3
