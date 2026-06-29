"""Tests for the Gen-1 tilemap → text decoder."""

from text_decoder import CHARMAP, decode_grid, decode_row

# Reverse map for encoding test strings into tile ids.
_ENC = {v: k for k, v in CHARMAP.items() if len(v) == 1}


def _encode(s: str) -> list[int]:
    """Encode an ASCII string into Gen-1 tile ids (space -> 0x7F)."""
    return [_ENC[ch] for ch in s]


def test_decode_row_letters_digits_space():
    assert decode_row(_encode("PEWTER CITY")) == "PEWTER CITY"
    assert decode_row(_encode("Route 2")) == "Route 2"


def test_unknown_tiles_dropped():
    # 0x00 / 0x01 are non-text background tiles -> dropped, not garbage.
    row = [0x00, *_encode("HI"), 0x01]
    assert decode_row(row) == "HI"


def test_decode_grid_joins_and_collapses_whitespace():
    rows = [_encode("PEWTER"), _encode("CITY")]
    assert decode_grid(rows) == "PEWTER CITY"


def test_decode_grid_strips_blank_rows():
    rows = [[0x50] * 18, _encode("OAK"), [0x7F] * 18]
    assert decode_grid(rows) == "OAK"


def test_empty_grid():
    assert decode_grid([]) == ""
    assert decode_grid([[0x00, 0x50, 0x7F]]) == ""


def test_punctuation_and_terminator():
    # 0x50 is the string terminator -> empty; 0xE7 is '!'
    assert decode_row([*_encode("GO"), 0xE7, 0x50]) == "GO!"
