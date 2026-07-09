"""GameProfile: per-game RAM maps with Yellow derived by the verified -1 shift.

Ground truth: pret/pokered vs pret/pokeyellow symbol files — every WRAM address in
0xCF00-0xD7FF shifts down one byte in Yellow; the sprite state block (0xC1xx) and
tile buffers (0xC3xx-0xC4xx) do not move.
"""

from unittest.mock import MagicMock

from game_profile import RED_BLUE, YELLOW, detect_profile, profile_for_title

# Every profile field that lives in the shifted WRAM window.
SHIFTED_FIELDS = (
    "addr_battle_type",
    "addr_enemy_hp_hi",
    "addr_enemy_hp_lo",
    "addr_enemy_max_hp_hi",
    "addr_enemy_max_hp_lo",
    "addr_enemy_level",
    "addr_enemy_species",
    "addr_enemy_type1",
    "addr_enemy_type2",
    "addr_player_hp_hi",
    "addr_player_hp_lo",
    "addr_player_max_hp_hi",
    "addr_player_max_hp_lo",
    "addr_player_level",
    "addr_player_species",
    "addr_move_1",
    "addr_move_2",
    "addr_move_3",
    "addr_move_4",
    "addr_pp_1",
    "addr_pp_2",
    "addr_pp_3",
    "addr_pp_4",
    "addr_party_count",
    "addr_party_species_list",
    "party_base",
    "addr_map_id",
    "addr_player_x",
    "addr_player_y",
    "addr_badges",
    "addr_money_1",
    "addr_money_2",
    "addr_money_3",
    "addr_wd730",
    "addr_wd74b",
    "addr_lab_script",
    "addr_diag_script",
    "addr_warp_flag",
    "addr_bag_count",
    "addr_bag_items",
)


def test_red_blue_matches_legacy_constants():
    assert RED_BLUE.name == "red_blue"
    assert RED_BLUE.addr_battle_type == 0xD057
    assert RED_BLUE.addr_party_count == 0xD163
    assert RED_BLUE.addr_map_id == 0xD35E
    assert RED_BLUE.addr_badges == 0xD356
    assert RED_BLUE.addr_money_1 == 0xD347
    assert RED_BLUE.addr_wd730 == 0xD730
    assert RED_BLUE.addr_wd74b == 0xD74B
    assert RED_BLUE.addr_bag_count == 0xD31D
    assert RED_BLUE.party_base == 0xD16B
    assert RED_BLUE.addr_player_facing == 0xC109
    assert RED_BLUE.addr_pp_1 == 0xD02C  # NOTE: one below wBattleMonPP (d02d); preserved verbatim
    assert RED_BLUE.addr_text_progress == 0xC4F2
    assert RED_BLUE.addr_warp_flag == 0xD736
    assert RED_BLUE.routes_file == "routes.json"
    assert RED_BLUE.lab_ball_x == 6  # Charmander ball column


def test_yellow_is_red_minus_one_in_wram_block():
    for f in SHIFTED_FIELDS:
        assert getattr(YELLOW, f) == getattr(RED_BLUE, f) - 1, f


def test_yellow_low_ram_blocks_not_shifted():
    assert YELLOW.addr_player_facing == RED_BLUE.addr_player_facing == 0xC109
    assert YELLOW.addr_text_progress == RED_BLUE.addr_text_progress == 0xC4F2


def test_yellow_story_hooks():
    assert YELLOW.name == "yellow"
    assert YELLOW.label == "Yellow"
    assert YELLOW.routes_file == "routes.yellow.json"
    assert YELLOW.lab_ball_x == 7  # the single Eevee ball (pokeyellow OaksLab objects)


def test_profile_for_title():
    assert profile_for_title("POKEMON RED") is RED_BLUE
    assert profile_for_title("POKEMON BLUE") is RED_BLUE
    assert profile_for_title("POKEMON YELLOW") is YELLOW
    assert profile_for_title("") is RED_BLUE  # unknown → historical default
    assert profile_for_title("TETRIS") is RED_BLUE


def test_detect_profile_reads_cartridge_title():
    pyboy = MagicMock()
    pyboy.cartridge_title = "POKEMON YELLOW"
    assert detect_profile(pyboy) is YELLOW


def test_oak_trigger_row_is_per_game():
    # pokered PalletTownDefaultScript: cp 1; pokeyellow: cp 0 (north boundary row)
    assert RED_BLUE.oak_trigger_y == 1
    assert YELLOW.oak_trigger_y == 0
