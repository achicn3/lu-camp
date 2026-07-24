"""不可變證據的 canonical JSON。

鍵與字串先做 Unicode NFC；dict 鍵固定排序；Decimal 轉無指數字串；float 一律拒絕，
避免同一商業內容因執行環境／序列化器差異產生不同 fingerprint。
"""

import json
import unicodedata
from decimal import Decimal

type CanonicalValue = None | bool | int | str | list["CanonicalValue"] | dict[str, "CanonicalValue"]


def canonicalize(value: object) -> CanonicalValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        raise ValueError("canonical JSON 不接受浮點數；金額必須用十進位字串")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list | tuple):
        return [canonicalize(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, CanonicalValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("canonical JSON 的物件鍵必須是字串")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise ValueError("Unicode 正規化後出現重複鍵")
            normalized[key] = canonicalize(raw_value)
        return normalized
    raise ValueError(f"canonical JSON 不支援型別 {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
