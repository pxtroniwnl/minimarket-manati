from datetime import UTC, datetime

import pytest

from app.services.hours import is_open


@pytest.mark.parametrize(
    "open_h,close_h,utc_hour,expected",
    [
        (7, 22, 12, True),  # 7 a. m. Bogotá
        (7, 22, 11, False),  # 6 a. m.
        (7, 22, 3, False),  # 10 p. m.
        (7, 22, 2, True),  # 9 p. m.
        (18, 2, 4, True),  # 11 p. m., horario que cruza la medianoche
        (18, 2, 8, False),  # 3 a. m.
    ],
)
def test_is_open(settings, monkeypatch, open_h, close_h, utc_hour, expected):
    monkeypatch.setattr(settings, "open_hour", open_h)
    monkeypatch.setattr(settings, "close_hour", close_h)
    assert is_open(datetime(2026, 10, 24, utc_hour, 0, tzinfo=UTC)) is expected
