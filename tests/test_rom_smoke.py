"""Boot each real ROM headless, script through the intro, and verify the profile
reads true values: bedroom map 38, empty party, ₽3000 (same expectations across
Red, Blue, and Yellow). This is the empirical gate that the Yellow -1 address
shift is correct on real emulator state — unit tests only prove the tables'
internal consistency.

Auto-skips when no ROMs are present (rom/ ships only locally). Deselect with
``-m "not rom"`` when iterating on fast tests.
"""

from pathlib import Path

import pytest

ROM_DIR = Path(__file__).resolve().parent.parent / "rom"
ROMS = sorted(ROM_DIR.glob("*.gb")) if ROM_DIR.exists() else []

pytestmark = [
    pytest.mark.rom,
    pytest.mark.skipif(not ROMS, reason="no ROMs present under rom/"),
]

EXPECTED_PROFILE = {
    "POKEMON RED": "red_blue",
    "POKEMON BLUE": "red_blue",
    "POKEMON YELLOW": "yellow",
}


def _run_intro(controller):
    """Mirror agent._advance_intro: title -> (NEW GAME) -> mash A through Oak/naming."""
    controller.wait(1500)
    controller.press("start")
    controller.wait(60)
    # With a save present the menu is CONTINUE/NEW GAME (DOWN selects NEW GAME);
    # without one, NEW GAME is already selected and DOWN+A lands harmlessly.
    controller.press("down")
    controller.wait(30)
    controller.press("a")
    controller.wait(60)
    for _ in range(600):
        controller.press("a")
        controller.wait(30)
    for _ in range(10):
        controller.press("b")
        controller.wait(15)


@pytest.mark.parametrize("rom", ROMS, ids=lambda r: r.name.split(" (")[0].replace(" ", "_"))
def test_intro_reaches_bedroom(rom):
    from agent import GameController
    from game_profile import detect_profile
    from memory_reader import MemoryReader
    from pyboy import PyBoy

    pyboy = PyBoy(str(rom), window="null")
    try:
        pyboy.set_emulation_speed(0)
        profile = detect_profile(pyboy)
        assert profile.name == EXPECTED_PROFILE[pyboy.cartridge_title.strip()]
        reader = MemoryReader(pyboy, profile)
        _run_intro(GameController(pyboy))
        state = reader.read_overworld_state()
        assert state.map_id == 38, f"{rom.name}: expected bedroom (38), got map {state.map_id}"
        assert state.party_count == 0, f"{rom.name}: party should be empty, got {state.party_count}"
        assert state.money == 3000, f"{rom.name}: money should read 3000, got {state.money}"
    finally:
        pyboy.stop()
