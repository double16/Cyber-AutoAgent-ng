
import pytest

from modules.utils.text_reducer import (
    collapse_first_repeated_sequence,
)


@pytest.mark.parametrize(
    "s,expected",
    [
        # Perfect tail repetition → collapse
        (
                "This is a duplicate. This is a duplicate. This is a duplicate. This is a duplicate.",
                "This is a duplicate.",
        ),
        # Prefix before the repeated block, but tail is pure repetitions → collapse
        (
                "Intro — This is a duplicate. This is a duplicate. This is a duplicate.",
                "Intro — This is a duplicate.",
        ),
        # Unrepeated tail words → DO NOT collapse
        (
                "This is a duplicate. This is a duplicate. But then extra words.",
                "This is a duplicate. This is a duplicate. But then extra words.",
        ),
        # Unrepeated tail (even one word) → DO NOT collapse
        (
                "hello world hello world tail",
                "hello world hello world tail",
        ),
        # Punctuation-only variation between repeats is OK; words line up → collapse
        (
                "hello, world! hello, world? hello, world.",
                "hello, world!",
        ),
        # Immediate repeat exists, but later breaks before end → DO NOT collapse
        (
                "alpha beta alpha beta gamma",
                "alpha beta alpha beta gamma",
        ),
        # Multiple repeats to end → collapse
        ("A A A A", "A"),
        ("foo bar foo bar foo bar", "foo bar"),
        # No repetition → unchanged
        ("No repetition here.", "No repetition here."),
        # Whitespace should not affect result
        ("   A B   A B   ", "   A B"),
        # Edge cases
        ("word", "word"),
        ("", ""),
        # Unicode words
        ("Привет мир Привет мир Привет мир", "Привет мир"),
    ],
)
def test_collapse_first_repeated_sequence(s, expected):
    assert collapse_first_repeated_sequence(s) == expected
