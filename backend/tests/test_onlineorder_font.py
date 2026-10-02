"""手寫字型子集（docs/44 §3.4；店主 2026-10-02 選定辰宇落雁體）。

整檔 9.5 MB 不能給客人下載，發佈時只抽菜單用到的字。OFL 保留字型名稱：子集一律改名 LukengHand。
"""

import io

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
    assert "辰宇落雁" not in (names.getDebugName(4) or "")
    assert "Chenyuluoyan" not in (names.getDebugName(6) or "")
    assert "Copyright" in (names.getDebugName(0) or "")
    assert "Open Font License" in (names.getDebugName(13) or "")


def test_same_text_gives_same_hash() -> None:
    assert subset_font("拿鐵").sha256 == subset_font("拿鐵").sha256
