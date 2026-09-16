"""手機號碼正規化與驗證（聯絡人專用）。

**只收台灣手機：09 開頭、共 10 碼**（裁示 2026-09-16）。門市自己的市話（`stores.phone`）
不走這裡——那是店家資訊，不參與「同店電話唯一」的比對。

為什麼要先正規化再存：`0912345678` 與 `0912-345-678` 是同一支號碼，但對
`uq_contacts_store_phone` 來說是兩個值，於是同一個人被建成兩筆——查得到兩個他，
點數與收購紀錄各分一半。連字號、空格、全形數字（從 Excel／LINE 複製很常見）
一律在邊界收斂成同一種寫法。
"""

import re
import unicodedata

from app.shared.exceptions import DomainError

_MOBILE_RE = re.compile(r"^09\d{8}$")
# 各種「看起來像分隔符」的字元：半形/全形連字號、en/em dash、空白類。
_SEPARATORS = str.maketrans("", "", "-－–—　 \t()")


class InvalidPhone(DomainError):
    """手機號碼格式不正確。"""


def normalize_phone(raw: str) -> str:
    """回傳正規化後的手機號碼；格式不符即 `InvalidPhone`。

    全形數字先以 NFKC 轉半形，再去掉分隔符，最後才比對格式——否則「０９１２…」這種
    從別處複製來的號碼會被誤判成非法。
    """
    collapsed = unicodedata.normalize("NFKC", raw or "").translate(_SEPARATORS)
    if not _MOBILE_RE.fullmatch(collapsed):
        raise InvalidPhone(f"手機號碼須為 09 開頭的 10 碼數字，收到「{raw}」")
    return collapsed
