from datetime import datetime

import pytest

from aja.utils.nl_time import parse_nl_time

BASE = datetime(2026, 8, 20, 10, 0, 0)  # Thursday, 10:00 local


def test_clock_12h_pm():
    assert parse_nl_time("3pm", now=BASE) == datetime(2026, 8, 20, 15, 0)


def test_clock_12h_am():
    assert parse_nl_time("9am", now=BASE) == datetime(2026, 8, 21, 9, 0)


def test_past_time_today_rolls_to_tomorrow():
    # 3am already passed today -> tomorrow 03:00
    assert parse_nl_time("3am", now=BASE) == datetime(2026, 8, 21, 3, 0)


def test_clock_24h_colon():
    assert parse_nl_time("15:00", now=BASE) == datetime(2026, 8, 20, 15, 0)
    assert parse_nl_time("19:45", now=BASE) == datetime(2026, 8, 20, 19, 45)


def test_tomorrow_bare_defaults_9am():
    assert parse_nl_time("tomorrow", now=BASE) == datetime(2026, 8, 21, 9, 0)


def test_tomorrow_with_time():
    assert parse_nl_time("tomorrow 9am", now=BASE) == datetime(2026, 8, 21, 9, 0)
    assert parse_nl_time("tomorrow at 14:30", now=BASE) == datetime(2026, 8, 21, 14, 30)


def test_today_with_future_time():
    assert parse_nl_time("today 5pm", now=BASE) == datetime(2026, 8, 20, 17, 0)


def test_today_past_time_rolls_to_tomorrow():
    assert parse_nl_time("today 8am", now=BASE) == datetime(2026, 8, 21, 8, 0)


def test_tonight_default_7pm():
    assert parse_nl_time("tonight", now=BASE) == datetime(2026, 8, 20, 19, 0)


def test_tonight_explicit_time():
    assert parse_nl_time("tonight 11pm", now=BASE) == datetime(2026, 8, 20, 23, 0)


def test_weekday_next_occurrence():
    # BASE is Thursday (weekday 3); next Friday is tomorrow
    assert parse_nl_time("friday", now=BASE) == datetime(2026, 8, 21, 9, 0)


def test_weekday_same_day_next_week():
    # Thursday said on Thursday -> next week's Thursday
    assert parse_nl_time("thursday", now=BASE) == datetime(2026, 8, 27, 9, 0)


def test_weekday_with_time_case_insensitive():
    assert parse_nl_time("Friday 18:00", now=BASE) == datetime(2026, 8, 21, 18, 0)


def test_relative_hours():
    result = parse_nl_time("in 2 hours", now=BASE)
    assert result == datetime(2026, 8, 20, 12, 0)


def test_relative_minutes_short_unit():
    assert parse_nl_time("in 30m", now=BASE) == datetime(2026, 8, 20, 10, 30)


def test_relative_seconds():
    assert parse_nl_time("in 90 seconds", now=BASE) == datetime(2026, 8, 20, 10, 1, 30)


def test_relative_days():
    assert parse_nl_time("in 2 days", now=BASE) == datetime(2026, 8, 22, 10, 0)


def test_combo_task_text_with_at_time():
    assert parse_nl_time("at 3pm", now=BASE) == datetime(2026, 8, 20, 15, 0)


def test_invalid_returns_none():
    assert parse_nl_time("buy milk", now=BASE) is None
    assert parse_nl_time("", now=BASE) is None
    assert parse_nl_time(None, now=BASE) is None
    assert parse_nl_time("someday maybe", now=BASE) is None


def test_invalid_clock_values_return_none():
    assert parse_nl_time("25:00", now=BASE) is None


@pytest.mark.parametrize(
    "text",
    ["TOMORROW 9AM", "In 2 HOURS", "FRIDAY"],
)
def test_case_insensitive(text):
    assert parse_nl_time(text, now=BASE) is not None
