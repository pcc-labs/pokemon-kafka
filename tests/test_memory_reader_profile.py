"""MemoryReader reads through the game profile: same fields, Yellow addresses."""

from game_profile import RED_BLUE, YELLOW
from memory_reader import MemoryReader


def _stock_overworld(fake_memory, profile, map_id=38, x=3, y=6, party=0):
    fake_memory[profile.addr_map_id] = map_id
    fake_memory[profile.addr_player_x] = x
    fake_memory[profile.addr_player_y] = y
    fake_memory[profile.addr_badges] = 0
    fake_memory[profile.addr_party_count] = party
    # 3000 in BCD across three bytes: 00 30 00
    fake_memory[profile.addr_money_1] = 0x00
    fake_memory[profile.addr_money_2] = 0x30
    fake_memory[profile.addr_money_3] = 0x00


def test_overworld_reads_at_yellow_addresses(mock_pyboy, fake_memory):
    _stock_overworld(fake_memory, YELLOW, map_id=51, x=17, y=30, party=1)
    fake_memory[YELLOW.party_base + 1] = 0  # lead HP hi
    fake_memory[YELLOW.party_base + 2] = 25  # lead HP lo
    reader = MemoryReader(mock_pyboy, YELLOW)
    state = reader.read_overworld_state()
    assert (state.map_id, state.x, state.y) == (51, 17, 30)
    assert state.money == 3000
    assert state.party_hp == [25]


def test_red_default_profile_unchanged(mock_pyboy, fake_memory):
    _stock_overworld(fake_memory, RED_BLUE, map_id=2)
    reader = MemoryReader(mock_pyboy, RED_BLUE)
    assert reader.read_overworld_state().map_id == 2
    assert reader.ADDR_MAP_ID == 0xD35E  # legacy attribute names still work


def test_bag_reads_at_profile_addresses(mock_pyboy, fake_memory):
    fake_memory[YELLOW.addr_bag_count] = 1
    fake_memory[YELLOW.addr_bag_items] = 0x14  # Potion
    fake_memory[YELLOW.addr_bag_items + 1] = 3
    fake_memory[YELLOW.addr_bag_items + 2] = 0xFF
    reader = MemoryReader(mock_pyboy, YELLOW)
    assert reader.read_bag_items() == [(0x14, 3)]
    assert reader.find_healing_item() == (0, 0x14)


def test_battle_reads_at_yellow_addresses(mock_pyboy, fake_memory):
    fake_memory[YELLOW.addr_battle_type] = 1  # wild battle
    fake_memory[YELLOW.addr_enemy_hp_lo] = 12
    fake_memory[YELLOW.addr_enemy_level] = 5
    fake_memory[YELLOW.addr_enemy_species] = 0x70  # Weedle
    fake_memory[YELLOW.addr_player_hp_lo] = 19
    reader = MemoryReader(mock_pyboy, YELLOW)
    battle = reader.read_battle_state()
    assert battle.battle_type == 1
    assert battle.enemy_hp == 12
    assert battle.enemy_level == 5
    assert battle.enemy_species_name == "Weedle"
    assert battle.player_hp == 19


def test_detects_profile_when_not_given(mock_pyboy, fake_memory):
    mock_pyboy.cartridge_title = "POKEMON YELLOW"
    reader = MemoryReader(mock_pyboy)
    assert reader.profile is YELLOW
