"""收購號碼牌每天換一個字母（店主 2026-10-16）：跨日整理裝備時，看號碼就分得出是哪天收的。"""

from datetime import date, timedelta

from app.modules.intake.service import TICKET_LETTERS, ticket_label


def test_letter_changes_every_day() -> None:
    day = date(2026, 10, 16)
    assert ticket_label(day, 12) == "A012"
    assert ticket_label(day + timedelta(days=1), 12) == "B012"
    assert ticket_label(day - timedelta(days=1), 3) == f"{TICKET_LETTERS[-1]}003"


def test_skips_letters_that_look_like_digits() -> None:
    assert "I" not in TICKET_LETTERS
    assert "O" not in TICKET_LETTERS
    assert len(set(TICKET_LETTERS)) == len(TICKET_LETTERS) == 24


def test_cycle_repeats_after_all_letters() -> None:
    day = date(2026, 10, 16)
    labels = {ticket_label(day + timedelta(days=n), 1) for n in range(len(TICKET_LETTERS))}
    assert len(labels) == len(TICKET_LETTERS)
    assert ticket_label(day + timedelta(days=len(TICKET_LETTERS)), 1) == "A001"
