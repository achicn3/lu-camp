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


def test_v3_states_that_consignment_commission_is_計算於未稅價() -> None:
    """寄售分潤改以未稅價計算（ADR-021），合約必須講明白——不然是店家單方說了算。

    v1／v2 只寫「寄售抽成比例」，客人合理會以為是含稅標價的比例；
    1050 元的寄售品，以為拿 525、實際拿 500，正是會吵起來的地方。
    """
    _title, body = AGREEMENT_TEXTS[3]
    assert "未稅" in body
    assert "1,050" in body and "500" in body  # 要有看得懂的實例
    assert "營業稅" in body


def test_v3_keeps_everything_else_identical_to_v2() -> None:
    """只動第二條（交易確認）：其餘條款一字不改，改版才看得出改了什麼。"""
    _t2, v2 = AGREEMENT_TEXTS[2]
    _t3, v3 = AGREEMENT_TEXTS[3]
    clauses2 = v2.split("\n\n")
    clauses3 = v3.split("\n\n")
    assert len(clauses2) == len(clauses3)
    for i, (a, b) in enumerate(zip(clauses2, clauses3, strict=True)):
        if a.startswith("二、"):
            assert a != b
        else:
            assert a == b, f"第 {i} 段不該變"
