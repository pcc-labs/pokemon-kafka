"""Decode Pokémon Red on-screen text from the background tilemap.

The game renders signs, NPC dialogue, and menus into the BG tilemap using the Gen-1 character set,
where each font tile's id *is* its character code (0x80='A', 0xA0='a', 0x7F=space, …). So reading
the tilemap and mapping tile ids through ``CHARMAP`` recovers the text the player sees — the same
technique the PokemonRedExperiments / pokegym projects use.

Pure logic, fully unit-tested: callers pass plain ``list[int]`` grids (e.g. from
``pyboy.tilemap_background``), so nothing here imports PyBoy.
"""

from __future__ import annotations

import re

# Gen-1 (Pokémon Red/Blue) tile-id → character. Printable subset; anything not here is dropped.
# 0x7F = BG blank, 0x17F = window blank (both render as space); 0x4E newline; 0x50 terminator.
CHARMAP: dict[int, str] = {0x7F: " ", 0x17F: " ", 0x4E: " ", 0x50: ""}
# Letters: 0x80–0x99 = A–Z, 0xA0–0xB9 = a–z
for _i in range(26):
    CHARMAP[0x80 + _i] = chr(ord("A") + _i)
    CHARMAP[0xA0 + _i] = chr(ord("a") + _i)
# Digits: 0xF6–0xFF = 0–9
for _i in range(10):
    CHARMAP[0xF6 + _i] = chr(ord("0") + _i)
# Common punctuation
CHARMAP.update(
    {
        0x9A: "(",
        0x9B: ")",
        0x9C: ":",
        0x9D: ";",
        0x9E: "[",
        0x9F: "]",
        0xE0: "'",
        0xE3: "-",
        0xE6: "?",
        0xE7: "!",
        0xE8: ".",
        0xF2: ".",  # decimal point glyph
        0xBA: "é",
        0xBB: "'d",
        0xBC: "'l",
        0xBD: "'s",
        0xBE: "'t",
        0xBF: "'v",
        0xF3: "/",
        0xF4: ",",
    }
)

_WS_RE = re.compile(r"\s+")


def decode_row(tiles: list[int]) -> str:
    """Decode one row of tile ids into a string (unknown ids → dropped)."""
    return "".join(CHARMAP.get(t, "") for t in tiles)


def decode_grid(rows: list[list[int]]) -> str:
    """Decode a grid of tile ids (rows top→bottom) into a single whitespace-collapsed string."""
    text = " ".join(decode_row(row) for row in rows)
    return _WS_RE.sub(" ", text).strip()
