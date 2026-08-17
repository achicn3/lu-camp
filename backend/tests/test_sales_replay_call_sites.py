"""重播路徑的來源碼守衛：指紋欄位一旦漏傳，就是「錢收了卻回 409」。

`_cart_fingerprint` 每加一個欄位，**每一個**重算指紋的呼叫點都必須跟著帶。漏一處的症狀
不是壞掉的測試，而是正式環境的偶發 409——贏家已扣款成單，輸家卻被告知內容不同。
這個坑在本檔涵蓋的檔案裡踩過三次（折扣、內用/外帶各一次於 router 的兩條分支）。

同 `test_enum_check_constraint_sync.py` 的作法：用來源碼比對守住「兩處必須一致」，
因為真正觸發那條分支需要並發競態，一般整合測試打不到。
"""

import re
from pathlib import Path

_ROUTER = Path(__file__).parents[1] / "app" / "modules" / "sales" / "router.py"
_SERVICE = Path(__file__).parents[1] / "app" / "modules" / "sales" / "service.py"

# `_cart_fingerprint` 納入指紋、且由呼叫端逐一傳入的具名參數。
_FINGERPRINT_KWARGS = (
    "lines",
    "buyer_contact_id",
    "tenders",
    "invoice_info",
    "adjustments",
    "service_mode",
    "table_no",
)


def _replay_calls(source: str) -> list[str]:
    """取出所有 find_*_replay(...) 的呼叫文字（含參數）。"""
    calls: list[str] = []
    for match in re.finditer(r"find_(?:idempotent|signature)_replay\(", source):
        # 跳過定義本身（`async def find_..._replay(`），只看呼叫點。
        if source[: match.start()].rstrip().endswith("def"):
            continue
        depth, i = 0, match.end() - 1
        while i < len(source):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    calls.append(source[match.start() : i + 1])
                    break
            i += 1
    return calls


def test_every_replay_call_site_passes_all_fingerprint_fields() -> None:
    """router 與 service 的每一個重播呼叫都必須帶齊指紋欄位。"""
    calls = _replay_calls(_ROUTER.read_text()) + _replay_calls(_SERVICE.read_text())
    # router 三處 idempotent + 一處 signature；service 各一處。
    assert len(calls) >= 5, f"重播呼叫點數量異常（{len(calls)}），守衛可能失效"
    for call in calls:
        missing = [kw for kw in _FINGERPRINT_KWARGS if f"{kw}=" not in call]
        assert not missing, (
            f"重播呼叫漏傳指紋欄位 {missing}——會讓已成交的單被誤判成"
            f"「同鍵不同內容」而回 409：\n{call}"
        )


def test_fingerprint_signature_matches_the_guarded_field_list() -> None:
    """`_cart_fingerprint` 新增參數卻沒更新本檔清單 → 守衛會失效，故一併鎖住。"""
    source = _SERVICE.read_text()
    signature = source.split("def _cart_fingerprint(", 1)[1].split(") -> str:", 1)[0]
    params = {line.split(":")[0].strip() for line in signature.splitlines() if ":" in line}
    assert params == set(_FINGERPRINT_KWARGS), (
        "_cart_fingerprint 的參數與守衛清單不一致；新增指紋欄位時，"
        "請同步更新 _FINGERPRINT_KWARGS 並補齊所有呼叫點"
    )
