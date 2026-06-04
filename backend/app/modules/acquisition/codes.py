"""識別碼產生：item_code / lot_code 建檔當下產生、固定不變（與 POS 掃碼同一套碼）。

只用 1D Code 128 可編碼的 ASCII 大寫字母與數字；以 uuid4 取亂數段，全域唯一性
另由 serialized_items.item_code / bulk_lots.lot_code 的唯一索引在 DB 層保證。
前綴帶 store_id 以利人工辨識，不寫死單店假設。
"""

from uuid import uuid4

_RANDOM_LEN = 10


def _random_segment() -> str:
    return uuid4().hex[:_RANDOM_LEN].upper()


def new_item_code(store_id: int) -> str:
    """序號單品識別碼，如 ``S1-3F9A2B7C4D``。"""
    return f"S{store_id}-{_random_segment()}"


def new_lot_code(store_id: int) -> str:
    """散裝批識別碼，如 ``L1-3F9A2B7C4D``。"""
    return f"L{store_id}-{_random_segment()}"
