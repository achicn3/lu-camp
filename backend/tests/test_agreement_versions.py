"""切結書版本內文的不變條件（docs/23 §5）。

**版本列不可變**：舊簽名永遠綁定簽署當下那一版。改版一律新增條目、不改舊條目——
改舊條目不但沒有效果（首次讀取就已落庫），還會讓程式碼與資料庫的內容不一致，
日後爭議時拿不出「客人當初簽的是哪一份」。
"""

import re

from app.modules.signing.agreements import (
    AGREEMENT_BODY_V1,
    AGREEMENT_TEXTS,
    AGREEMENT_TITLE_V1,
    CURRENT_AGREEMENT_VERSION,
)


def _strip_newlines(text: str) -> str:
    return text.replace("\n", "")


def test_v1_text_is_frozen() -> None:
    """v1 內文與標題不可再更動——已有簽署綁在上面。

    這條測試就是那道鎖：改了 v1 會在這裡失敗，逼你改成新增 v2。
    """
    title, body = AGREEMENT_TEXTS[1]
    assert title == AGREEMENT_TITLE_V1
    assert body == AGREEMENT_BODY_V1
    assert body.startswith("出賣人（以下稱「本人」）茲將本文件所列物品讓售／寄售予本店")
    assert "非贓物切結" in body
    assert "個人資料保護法第 8 條" in body
    assert len(body) == 677  # 落庫長度；變了就是改到 v1 了


def test_current_version_is_the_highest() -> None:
    assert CURRENT_AGREEMENT_VERSION == max(AGREEMENT_TEXTS)


def test_versions_are_contiguous_from_one() -> None:
    """版本號連續遞增，不跳號——跳號會讓「客人簽的是第幾版」難以對帳。"""
    assert sorted(AGREEMENT_TEXTS) == list(range(1, max(AGREEMENT_TEXTS) + 1))


def test_v2_only_reflows_v1_without_changing_a_single_character() -> None:
    """v2 是**純排版**改版：拿掉段落內的硬換行，一個字都不改。

    v1 的內文按固定寬度硬斷行，在手持裝置上換行會落在句子中間
    （「…詐欺所得／或其他來路不明之物」），讀起來會頓。
    """
    _, v2 = AGREEMENT_TEXTS[2]
    assert _strip_newlines(v2) == _strip_newlines(AGREEMENT_BODY_V1)
    assert AGREEMENT_TEXTS[2][0] == AGREEMENT_TITLE_V1


def test_v2_keeps_each_clause_heading_on_its_own_line() -> None:
    """條號自成一行、段落各自成段——否則整份會連成一大塊，更難讀。"""
    _, v2 = AGREEMENT_TEXTS[2]
    blocks = v2.strip("\n").split("\n\n")
    headed = [b for b in blocks if re.match(r"^[一二三四五六]、", b)]
    assert len(headed) == 6
    for block in headed:
        lines = block.split("\n")
        assert len(lines) == 2, f"條款內文應併成一段：{lines[0]}"


def test_v2_has_no_line_break_inside_a_paragraph() -> None:
    """回歸：真正要修的就是這件事。"""
    _, v2 = AGREEMENT_TEXTS[2]
    for block in v2.strip("\n").split("\n\n"):
        lines = block.split("\n")
        body_lines = lines[1:] if re.match(r"^[一二三四五六]、", block) else lines
        assert len(body_lines) <= 1, f"段落內仍有換行：{block[:30]}"
