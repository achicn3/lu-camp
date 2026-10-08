"""手寫字型子集（docs/44 §3.4；店主 2026-10-08 改用霞鶩文楷 TC，原為辰宇落雁體）。

整檔 15 MB 不能給客人下載，發佈時只抽菜單用到的字。子集一律改名 LukengHand、保留原著作權與授權。
"""

import io
from pathlib import Path

from fontTools.ttLib import TTFont

from app.modules.onlineorder.font import SUBSET_FAMILY, UI_TEXT, subset_font


def _open(data: bytes) -> TTFont:
    return TTFont(io.BytesIO(data))


def test_subset_is_small_woff2_with_requested_glyphs() -> None:
    result = subset_font("拿鐵 戚風蛋糕 $150")
    assert result.content[:4] == b"wOF2"
    assert len(result.content) < 200_000
    cmap = _open(result.content).getBestCmap()
    for ch in "拿鐵戚風蛋糕$150":
        assert ord(ch) in cmap, ch
    assert ord("龘") not in cmap  # 沒用到的字不帶


def test_ui_text_is_always_included() -> None:
    cmap = _open(subset_font("").content).getBestCmap()
    for ch in UI_TEXT:
        if not ch.isspace():
            assert ord(ch) in cmap, ch


def test_subset_is_renamed_but_keeps_copyright_and_license() -> None:
    names = _open(subset_font("咖啡").content)["name"]
    family = names.getDebugName(1)
    assert family == SUBSET_FAMILY
    assert "WenKai" not in (names.getDebugName(4) or "")
    assert "WenKai" not in (names.getDebugName(6) or "")
    assert "LXGW" in (names.getDebugName(0) or "")  # 霞鶩文楷的著作權聲明原樣保留
    assert "Copyright" in (names.getDebugName(0) or "")
    assert "Open Font License" in (names.getDebugName(13) or "")


def test_same_text_gives_same_hash() -> None:
    assert subset_font("拿鐵").sha256 == subset_font("拿鐵").sha256


def test_ui_text_covers_every_cjk_char_on_the_customer_page() -> None:
    """客人頁寫死的中文字都要在 UI_TEXT 裡，否則那幾個字會掉回系統字型（O3c 實機截圖抓到過）。

    掃 online-order 的客人頁原始碼；改了點餐頁文案、這支測試紅了，就把新字補進 UI_TEXT。
    """
    root = Path(__file__).resolve().parents[2] / "online-order"
    sources = [*sorted((root / "src" / "client").glob("*.ts")), root / "public" / "index.html"]
    text = "".join(p.read_text(encoding="utf-8") for p in sources)
    # 只看字串裡會顯示的字：去掉註解行
    shown = "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "<!--"))
    )
    cjk = {ch for ch in shown if "　" <= ch <= "鿿" or "＀" <= ch <= "￯"}
    missing = sorted(cjk - set(UI_TEXT))
    assert not missing, f"UI_TEXT 少了這些字：{''.join(missing)}"
