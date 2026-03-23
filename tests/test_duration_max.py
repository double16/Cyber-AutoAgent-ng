
import pytest

from modules.handlers.utils import duration_max


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("2m 30s", "1m 59s"), "2m 30s"),
        (("1h 2m 30s", "2m 30s"), "1h 2m 30s"),
        (("02:30", "1:59"), "02:30"),
        (("1:02:30", "59:59"), "1:02:30"),
        (("3.5h", "3h 40m"), "3h 40m"),
        (("9s", "10s"), "10s"),
        (("1 2 3", "1 2 4"), "1 2 4"),
        (("1m", "0h 59m"), "0h 59m"),
        (("0:0:5", "4"), "0:0:5"),
        (("1:00", "0:59"), "1:00"),
    ],
)
def test_duration_max_returns_largest_duration(values, expected):
    assert duration_max(*values) == expected


def test_duration_max_normalizes_shorter_values_with_left_padding():
    assert duration_max("2m 30s", "1h 0m 0s") == "1h 0m 0s"


def test_duration_max_splits_on_spaces_and_colons_together():
    assert duration_max("1h 02:03", "59:59") == "1h 02:03"


def test_duration_max_ignores_alpha_suffixes():
    assert duration_max("2hours 30mins", "2hours 29mins") == "2hours 30mins"


def test_duration_max_supports_decimal_values():
    assert duration_max("1.5h", "1h 40m") == "1h 40m"


def test_duration_max_ignores_tokens_without_numbers():
    assert duration_max("foo 2m bar 30s", "foo 2m bar 29s") == "foo 2m bar 30s"


def test_duration_max_ignores_empty_segments_from_extra_spaces_and_colons():
    assert duration_max("  1h   2m  ", "1:01") == "  1h   2m  "


def test_duration_max_returns_first_value_on_tie():
    assert duration_max("2m 30s", "2m 30s", "2:30") == "2m 30s"


def test_duration_max_ignores_none():
    assert duration_max("2m 30s", None, "2m 30s", "2:30") == "2m 30s"


def test_duration_max_returns_none_with_no_arguments():
    assert duration_max() is None


def test_duration_max_handles_strings_with_no_numbers():
    assert duration_max("abc", "1s") == "1s"


def test_duration_max_when_all_inputs_have_no_numbers_returns_first():
    assert duration_max("abc", "def") == "abc"
