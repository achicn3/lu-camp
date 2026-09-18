"""openingcheck I/O schema。"""

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OpeningCheckItemRead(BaseModel):
    """一條自訂確認事項在「今天」的樣子。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    href: str | None
    done: bool = False


class OpeningCheckItemCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # 「前往處理」要去哪（選填）：**只收站內路徑**（例如 /cash）——這個值會直接餵給站內導覽。
    href: str | None = Field(default=None, max_length=200)

    @field_validator("href")
    @classmethod
    def _internal_path(cls, value: str | None) -> str | None:
        """只收站內路徑。

        `//evil.com` 也是以 `/` 開頭（protocol-relative URL），單純比對開頭會把店員導到站外，
        所以連續兩個斜線要一併擋掉。（Pydantic 的 pattern 走 Rust 正則、不支援前瞻，
        因此寫成驗證器而不是 pattern。）
        """
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned == "":
            return None
        # `//evil.com` 與 `/\evil.com` 都是以 `/` 開頭卻會導到站外（瀏覽器把 `\` 當 `/`）。
        if not cleaned.startswith("/") or cleaned[1:2] in {"/", "\\"}:
            raise ValueError("連結只能是站內路徑（例如 /cash）")
        return cleaned


class CashSessionState(StrEnum):
    """開帳狀態三態。

    只有「有沒有 OPEN 的班別」是不夠的：昨天忘記關帳的話，今天會被當成已經開好帳，
    今天的現金收入被算進昨天的班別，對帳永遠對不平（§7 不變量 4）。
    """

    OPEN_TODAY = "OPEN_TODAY"  # 今天開的班別
    STALE = "STALE"  # 還開著，但那是前一天的班別 → 要先結帳
    NONE = "NONE"  # 沒有開帳中的班別


class OpeningCheckTodayRead(BaseModel):
    """今天的檢查狀態。裝置狀態由前端直接問 hardware-agent，不在這裡。"""

    business_date: date
    cash_session_state: CashSessionState
    # 保留給既有呼叫端：等同 state == OPEN_TODAY。
    cash_session_open: bool
    items: list[OpeningCheckItemRead]
    skipped_keys: list[str]
    # 後端管得到的部分是否都完成（開帳＋自訂項目）。裝置那幾項由前端自己併進去判斷，
    # 所以前端顯示的「全部完成」可能比這個嚴格。
    completed: bool


class OpeningCheckItemDoneRequest(BaseModel):
    done: bool


_SKIP_KEY = re.compile(r"^(cash_session|device:[A-Z_]{1,30}:[\w.\-]{1,60})$")


class OpeningCheckSkipRequest(BaseModel):
    """略過／取消略過一個自動項目。裁示：不必填原因。

    key 只收認得的兩種：打錯字會靜默存進陣列、永遠不生效，店員還以為略過了。
    """

    key: str = Field(min_length=1, max_length=100)
    # 略過按錯要能取消——打勾可以取消，略過沒道理只能等明天。
    skipped: bool = True

    @field_validator("key")
    @classmethod
    def _known_key(cls, value: str) -> str:
        if not _SKIP_KEY.match(value):
            raise ValueError("不認得的檢查項目代號")
        return value
