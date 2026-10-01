"""送 Amego 的外部識別碼（開立 OrderId、折讓單號）：建立當下產生一次、持久化。

之後送出、對帳、補印一律讀存下來的值。

為什麼不再由流水號推導（原 `S{store}-{sale}`、`L{store}-{allowance}`）：資料庫重建或自備份
還原後流水號會倒退，識別碼跟著重用，撞上平台上重建前的舊紀錄——對帳守衛會正確地擋下，
但那筆開立從此永遠卡住（2026-10-01 正式機 S1-4、S1-5 撞上 9/18 的測試發票）。帶隨機段後
「重建後同號」實際上不可能發生。

為什麼一定要持久化、不能每次送出時才產生：OrderId 同時是**防重複開立的冪等鍵**——送出後
斷線、結果不明時，重送沿用同一個編號，平台會拒絕重複、對帳查得回原發票。每次重產就失去
這道保險，斷線重送會開出第二張發票。

亂數段用 `secrets`、去掉 0/O、1/I 這類看錯字：店長可能要在光貿後台照著打字查詢。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

_SAFE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_ORDER_ID_RANDOM_LEN = 8
_ORDER_ID_MAX_LEN = 40  # Amego OrderId 上限
_ALLOWANCE_NUMBER_LEN = 16  # Amego 折讓單號上限；用滿以取最長亂數段
_ALLOWANCE_MIN_RANDOM_LEN = 8


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def _random_segment(length: int) -> str:
    return "".join(secrets.choice(_SAFE_ALPHABET) for _ in range(length))


def new_platform_order_id(*, store_id: int, sale_id: int) -> str:
    """開立用 OrderId，如 ``S1-5-K7M2QX9A``：店號、銷售編號給人看，亂數段保證不重號。"""
    order_id = f"S{store_id}-{sale_id}-{_random_segment(_ORDER_ID_RANDOM_LEN)}"
    if len(order_id) > _ORDER_ID_MAX_LEN:  # int32 id 最長 31 字，防禦性檢查
        raise ValueError("OrderId 超過光貿 40 字限制")
    return order_id


def rotated_platform_order_id(
    *,
    store_id: int,
    sale_id: int,
    invoice_id: int,
    previous_order_id: str,
    invoice_created_at: datetime,
) -> str:
    """撞號後換的新 OrderId：**由還原前後都不變的值推導**，不重抽亂數。

    換號並開立成功後，若資料庫又從換號前的備份還原，那一列會回到舊編號、再撞一次；此時若
    重抽亂數，新編號在平台查無 → 送出 → 同一筆交易兩張發票。改由（前一個編號、發票 id、
    發票建立時間到微秒）雜湊而得：還原後算回**同一個**編號，對帳先行查到本筆、補記不重送；
    全新重建的資料庫裡同 sale_id 的發票建立時間不同，算出不同編號，舊撞號不會重演。
    時間取 epoch 微秒整數，不受讀回時 tzinfo 不同影響。
    """
    created_us = (invoice_created_at - _EPOCH) // _MICROSECOND
    digest = hashlib.sha256(
        f"{store_id}|{invoice_id}|{previous_order_id}|{created_us}".encode()
    ).digest()
    value = int.from_bytes(digest, "big")
    chars: list[str] = []
    for _ in range(_ORDER_ID_RANDOM_LEN):
        value, index = divmod(value, len(_SAFE_ALPHABET))
        chars.append(_SAFE_ALPHABET[index])
    order_id = f"S{store_id}-{sale_id}-{''.join(chars)}"
    if len(order_id) > _ORDER_ID_MAX_LEN:
        raise ValueError("OrderId 超過光貿 40 字限制")
    return order_id


def new_platform_allowance_number(*, store_id: int) -> str:
    """折讓單號，如 ``L1-K7M2QX9AB3CDE``：16 字上限扣掉店號前綴，其餘全填亂數。

    放不下銷售／折讓編號，但本地以 `invoice_allowances.platform_number` 對應，不靠字面辨識。
    """
    prefix = f"L{store_id}-"
    random_len = _ALLOWANCE_NUMBER_LEN - len(prefix)
    if random_len < _ALLOWANCE_MIN_RANDOM_LEN:
        raise ValueError("店號過長，折讓單號的亂數段不足以避免重號")
    return prefix + _random_segment(random_len)
