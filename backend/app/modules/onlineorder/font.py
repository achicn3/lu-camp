"""客人點餐頁的手寫字型子集（docs/44 §3.4；店主 2026-10-08 改用霞鶩文楷 TC，原為辰宇落雁體）。

整檔約 15 MB，發佈菜單時只抽出菜單文字＋固定介面文字用到的字，做成 WOFF2（通常幾十到一百多 KB）。

OFL 授權：子集屬修改版，名稱一律改成 `LukengHand`；著作權聲明與授權（name ID 0、13、14）原樣保留。
見 fonts/README.md。
"""

import hashlib
import io
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

FONT_PATH = Path(__file__).parent / "fonts" / "LXGWWenKaiTC-Regular.ttf"
SUBSET_FAMILY = "LukengHand"

# 點餐頁固定會出現的字（按鈕、問候、提示）。改了點餐頁文案要一起補上，否則那幾個字會掉回系統字型。
UI_TEXT = (
    "早安午晚，今天下想喝來點什麼？桌外帶內用咖啡甜輕食選物售完今日限量剩份起"
    "去結帳購物車加入取消確定送出訂單收到了我們開始準備請至櫃台暫停線上明天見還沒東西先看看的"
    "露坑全部分類關閉必可不最多至少項即將開放這個已經失效洽菜單中連路網稍後再試或餐。、（）："
    "　「」一他付作你供依候儲免其別則前動務及合同否吧品喜回在址型好字存容尚式張影態應"
    "成才按挑接敗整數料新斷方是時更會有服未果查檢款歡正此每清狀現目碼移空突立筆組號衝"
    "製複要規計設註認調謝證識變資足載返退通過避重金除響頁驗"
    "北夜快推標無照發籤薦跨須坐慢喝首完口味擇架剛"
    "卡子手抽換支杯沖豆配面體·"
    "件商家檯領"
    "+-×$0123456789.,:;!?()/ ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz&"
)

# 含保留名稱的名稱欄位：家族、完整名稱、唯一識別、PostScript 名、偏好家族／字重。
_RESERVED_NAME_IDS = (1, 3, 4, 6, 16, 17)


@dataclass(frozen=True)
class FontSubset:
    content: bytes
    sha256: str


@cache
def _source_bytes() -> bytes:
    return FONT_PATH.read_bytes()


def _rename(font: TTFont) -> None:
    table = font["name"]
    table.names = [r for r in table.names if r.nameID not in _RESERVED_NAME_IDS]
    for name_id, value in ((1, SUBSET_FAMILY), (4, SUBSET_FAMILY), (6, SUBSET_FAMILY)):
        table.setName(value, name_id, 3, 1, 0x409)
    table.setName(SUBSET_FAMILY, 3, 3, 1, 0x409)


def subset_font(text: str) -> FontSubset:
    """抽出 text＋介面固定文字用到的字，回傳 WOFF2 與內容雜湊（同樣的字＝同樣的雜湊）。"""
    font = TTFont(io.BytesIO(_source_bytes()), recalcTimestamp=False)
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.layout_features = ["*"]
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text + UI_TEXT)
    subsetter.subset(font)
    _rename(font)
    font.flavor = "woff2"
    buf = io.BytesIO()
    font.save(buf)
    content = buf.getvalue()
    return FontSubset(content=content, sha256=hashlib.sha256(content).hexdigest())
