"""Tests for memory_reader.py — targeting 100% line coverage."""

import pytest
from memory_reader import (
    ADDR_BAG_COUNT,
    ADDR_BAG_ITEMS,
    BAG_MAX_SLOTS,
    HEALING_ITEM_IDS,
    ITEM_OAKS_PARCEL,
    SPECIES_ID_MAP,
    TYPE_ID_MAP,
    BattleState,
    CollisionMap,
    MemoryReader,
    OverworldState,
)

# ---------------------------------------------------------------------------
# Dataclass default-value tests
# ---------------------------------------------------------------------------


class TestBattleStateDefaults:
    def test_defaults(self):
        bs = BattleState()
        assert bs.battle_type == 0
        assert bs.enemy_hp == 0
        assert bs.enemy_max_hp == 0
        assert bs.enemy_level == 0
        assert bs.enemy_species == 0
        assert bs.enemy_type1 == 0
        assert bs.enemy_type2 == 0
        assert bs.player_hp == 0
        assert bs.player_max_hp == 0
        assert bs.player_level == 0
        assert bs.player_species == 0
        assert bs.moves == [0, 0, 0, 0]
        assert bs.move_pp == [0, 0, 0, 0]
        assert bs.party_count == 0
        assert bs.party_hp == []

    def test_enemy_type_name_known(self):
        bs = BattleState(enemy_type1=0x14)
        assert bs.enemy_type_name == "fire"

    def test_enemy_type_name_unknown_falls_back(self):
        bs = BattleState(enemy_type1=0xFF)
        assert bs.enemy_type_name == "normal"

    def test_enemy_type_name_default(self):
        bs = BattleState()
        assert bs.enemy_type_name == "normal"  # 0x00 = normal

    def test_enemy_species_name_known(self):
        bs = BattleState(enemy_species=0x24)
        assert bs.enemy_species_name == "Pidgey"

    def test_enemy_species_name_unknown(self):
        bs = BattleState(enemy_species=0xFF)
        assert bs.enemy_species_name == "#FF"

    def test_enemy_species_name_default(self):
        bs = BattleState()
        assert bs.enemy_species_name == "#00"


class TestTypeIdMap:
    def test_known_types(self):
        assert TYPE_ID_MAP[0x00] == "normal"
        assert TYPE_ID_MAP[0x14] == "fire"
        assert TYPE_ID_MAP[0x15] == "water"
        assert TYPE_ID_MAP[0x17] == "grass"
        assert TYPE_ID_MAP[0x02] == "flying"
        assert TYPE_ID_MAP[0x03] == "poison"

    def test_all_entries_are_strings(self):
        for key, val in TYPE_ID_MAP.items():
            assert isinstance(key, int)
            assert isinstance(val, str)


class TestSpeciesIdMap:
    def test_known_species(self):
        assert SPECIES_ID_MAP[0x24] == "Pidgey"
        assert SPECIES_ID_MAP[0xA5] == "Rattata"
        assert SPECIES_ID_MAP[0x7B] == "Caterpie"
        assert SPECIES_ID_MAP[0x54] == "Pikachu"

    def test_starter_evolutions(self):
        assert SPECIES_ID_MAP[0xB0] == "Charmander"
        assert SPECIES_ID_MAP[0xB2] == "Charmeleon"
        assert SPECIES_ID_MAP[0xB1] == "Squirtle"
        assert SPECIES_ID_MAP[0xB3] == "Wartortle"
        assert SPECIES_ID_MAP[0x99] == "Bulbasaur"
        assert SPECIES_ID_MAP[0x09] == "Ivysaur"
        assert SPECIES_ID_MAP[0x7A] == "Butterfree"
        assert SPECIES_ID_MAP[0x97] == "Beedrill"
        assert SPECIES_ID_MAP[0x96] == "Pidgeotto"

    def test_all_entries_are_strings(self):
        for key, val in SPECIES_ID_MAP.items():
            assert isinstance(key, int)
            assert isinstance(val, str)


class TestHealingItemIds:
    def test_known_items(self):
        assert HEALING_ITEM_IDS[0x14] == "Potion"
        assert HEALING_ITEM_IDS[0x19] == "Super Potion"
        assert HEALING_ITEM_IDS[0x1A] == "Hyper Potion"
        assert HEALING_ITEM_IDS[0x10] == "Full Restore"

    def test_bag_constants(self):
        assert ADDR_BAG_COUNT == 0xD31D
        assert ADDR_BAG_ITEMS == 0xD31E
        assert BAG_MAX_SLOTS == 20


class TestOverworldStateDefaults:
    def test_defaults(self):
        ow = OverworldState()
        assert ow.map_id == 0
        assert ow.x == 0
        assert ow.y == 0
        assert ow.badges == 0
        assert ow.party_count == 0
        assert ow.party_hp == []
        assert ow.money == 0
        assert ow.text_box_active is False


# ---------------------------------------------------------------------------
# Low-level read helpers
# ---------------------------------------------------------------------------


class TestRead:
    def test_read_returns_byte(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[0x1234] = 0xAB
        assert reader._read(0x1234) == 0xAB

    def test_read_unset_address_returns_zero(self, mock_pyboy):
        reader = MemoryReader(mock_pyboy)
        assert reader._read(0x9999) == 0


class TestRead16:
    def test_read_16_combines_hi_lo(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[0x0010] = 0x01  # high byte
        fake_memory[0x0011] = 0x2C  # low byte
        assert reader._read_16(0x0010, 0x0011) == 0x012C  # 300

    def test_read_16_max_value(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[0x0010] = 0xFF
        fake_memory[0x0011] = 0xFF
        assert reader._read_16(0x0010, 0x0011) == 0xFFFF


class TestReadBCD:
    def test_single_byte_bcd(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        # 0x49 -> high=4, low=9 -> 49
        fake_memory[0x0001] = 0x49
        assert reader._read_bcd(0x0001) == 49

    def test_multi_byte_bcd(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        # Encoding of 123456:
        # byte1=0x12 -> 12, byte2=0x34 -> 34, byte3=0x56 -> 56
        # result = ((12)*100 + 34)*100 + 56 = 123456
        fake_memory[0x0001] = 0x12
        fake_memory[0x0002] = 0x34
        fake_memory[0x0003] = 0x56
        assert reader._read_bcd(0x0001, 0x0002, 0x0003) == 123456

    def test_bcd_zero(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[0x0001] = 0x00
        fake_memory[0x0002] = 0x00
        fake_memory[0x0003] = 0x00
        assert reader._read_bcd(0x0001, 0x0002, 0x0003) == 0


# ---------------------------------------------------------------------------
# read_battle_state
# ---------------------------------------------------------------------------


class TestReadBattleState:
    def test_no_battle_returns_early(self, mock_pyboy, fake_memory):
        """battle_type == 0 should return default BattleState immediately."""
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_BATTLE_TYPE] = 0
        state = reader.read_battle_state()
        assert state.battle_type == 0
        assert state.enemy_hp == 0
        assert state.moves == [0, 0, 0, 0]
        assert state.party_hp == []

    def test_wild_battle_full_read(self, mock_pyboy, fake_memory):
        """battle_type > 0 should populate every field."""
        reader = MemoryReader(mock_pyboy)
        mem = fake_memory

        mem[MemoryReader.ADDR_BATTLE_TYPE] = 1  # wild battle

        # Enemy: HP=120, MaxHP=150, Level=7, Species=4
        mem[MemoryReader.ADDR_ENEMY_HP_HI] = 0x00
        mem[MemoryReader.ADDR_ENEMY_HP_LO] = 120
        mem[MemoryReader.ADDR_ENEMY_MAX_HP_HI] = 0x00
        mem[MemoryReader.ADDR_ENEMY_MAX_HP_LO] = 150
        mem[MemoryReader.ADDR_ENEMY_LEVEL] = 7
        mem[MemoryReader.ADDR_ENEMY_SPECIES] = 4
        mem[MemoryReader.ADDR_ENEMY_TYPE1] = 0x00  # normal
        mem[MemoryReader.ADDR_ENEMY_TYPE2] = 0x02  # flying

        # Player: HP=200, MaxHP=250, Level=10, Species=0xB0
        mem[MemoryReader.ADDR_PLAYER_HP_HI] = 0x00
        mem[MemoryReader.ADDR_PLAYER_HP_LO] = 200
        mem[MemoryReader.ADDR_PLAYER_MAX_HP_HI] = 0x00
        mem[MemoryReader.ADDR_PLAYER_MAX_HP_LO] = 250
        mem[MemoryReader.ADDR_PLAYER_LEVEL] = 10
        mem[MemoryReader.ADDR_PLAYER_SPECIES] = 0xB0

        # Moves
        mem[MemoryReader.ADDR_MOVE_1] = 33  # Tackle
        mem[MemoryReader.ADDR_MOVE_2] = 45  # Growl
        mem[MemoryReader.ADDR_MOVE_3] = 52  # Ember
        mem[MemoryReader.ADDR_MOVE_4] = 0

        # PP
        mem[MemoryReader.ADDR_PP_1] = 35
        mem[MemoryReader.ADDR_PP_2] = 40
        mem[MemoryReader.ADDR_PP_3] = 25
        mem[MemoryReader.ADDR_PP_4] = 0

        # Party: 2 pokemon
        mem[MemoryReader.ADDR_PARTY_COUNT] = 2
        base0 = MemoryReader.PARTY_BASE
        mem[base0 + MemoryReader.PARTY_HP_OFFSET] = 0x00
        mem[base0 + MemoryReader.PARTY_HP_OFFSET + 1] = 200
        base1 = MemoryReader.PARTY_BASE + MemoryReader.PARTY_STRUCT_SIZE
        mem[base1 + MemoryReader.PARTY_HP_OFFSET] = 0x00
        mem[base1 + MemoryReader.PARTY_HP_OFFSET + 1] = 50

        state = reader.read_battle_state()

        assert state.battle_type == 1
        assert state.enemy_hp == 120
        assert state.enemy_max_hp == 150
        assert state.enemy_level == 7
        assert state.enemy_species == 4
        assert state.enemy_type1 == 0x00
        assert state.enemy_type2 == 0x02
        assert state.enemy_type_name == "normal"
        assert state.player_hp == 200
        assert state.player_max_hp == 250
        assert state.player_level == 10
        assert state.player_species == 0xB0
        assert state.moves == [33, 45, 52, 0]
        assert state.move_pp == [35, 40, 25, 0]
        assert state.party_count == 2
        assert state.party_hp == [200, 50]

    def test_trainer_battle(self, mock_pyboy, fake_memory):
        """battle_type == 2 (trainer) also triggers the full read path."""
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_BATTLE_TYPE] = 2
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 0
        state = reader.read_battle_state()
        assert state.battle_type == 2
        assert state.party_hp == []


# ---------------------------------------------------------------------------
# read_overworld_state
# ---------------------------------------------------------------------------


class TestReadOverworldState:
    def test_full_overworld_read(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        mem = fake_memory

        mem[MemoryReader.ADDR_MAP_ID] = 12
        mem[MemoryReader.ADDR_PLAYER_X] = 5
        mem[MemoryReader.ADDR_PLAYER_Y] = 10
        mem[MemoryReader.ADDR_BADGES] = 0x03  # 2 badges
        mem[MemoryReader.ADDR_PARTY_COUNT] = 1

        # Party HP for 1 pokemon: 45
        base0 = MemoryReader.PARTY_BASE
        mem[base0 + MemoryReader.PARTY_HP_OFFSET] = 0x00
        mem[base0 + MemoryReader.PARTY_HP_OFFSET + 1] = 45

        # Money: $1234 => BCD 0x00, 0x12, 0x34
        mem[MemoryReader.ADDR_MONEY_1] = 0x00
        mem[MemoryReader.ADDR_MONEY_2] = 0x12
        mem[MemoryReader.ADDR_MONEY_3] = 0x34

        # No text box active
        mem[MemoryReader.ADDR_WD730] = 0x00

        state = reader.read_overworld_state()

        assert state.map_id == 12
        assert state.x == 5
        assert state.y == 10
        assert state.badges == 0x03
        assert state.party_count == 1
        assert state.party_hp == [45]
        assert state.money == 1234
        assert state.text_box_active is False

    def test_overworld_with_text_box_active(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_WD730] = 0x62
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 0
        fake_memory[MemoryReader.ADDR_MONEY_1] = 0x00
        fake_memory[MemoryReader.ADDR_MONEY_2] = 0x00
        fake_memory[MemoryReader.ADDR_MONEY_3] = 0x00

        state = reader.read_overworld_state()
        assert state.text_box_active is True


# ---------------------------------------------------------------------------
# _is_text_or_script_active
# ---------------------------------------------------------------------------


class TestIsTextOrScriptActive:
    @pytest.mark.parametrize(
        "d730_val, expected",
        [
            (0x00, False),  # no bits set
            (0x02, True),  # bit 1 set (0x02 & 0x62 = 0x02)
            (0x20, True),  # bit 5 set (0x20 & 0x62 = 0x20)
            (0x40, True),  # bit 6 set (0x40 & 0x62 = 0x40)
            (0x62, True),  # all relevant bits set
            (0x01, False),  # bit 0 only — not in mask
            (0x80, False),  # bit 7 only — not in mask
            (0x9D, False),  # 0x9D = 10011101 — 0x9D & 0x62 = 0x00
        ],
    )
    def test_text_or_script_flag(self, mock_pyboy, fake_memory, d730_val, expected):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_WD730] = d730_val
        assert reader._is_text_or_script_active() is expected


# ---------------------------------------------------------------------------
# _read_party_hp
# ---------------------------------------------------------------------------


class TestReadPartyHP:
    def test_zero_party_members(self, mock_pyboy):
        reader = MemoryReader(mock_pyboy)
        assert reader._read_party_hp(0) == []

    def test_one_party_member(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        base = MemoryReader.PARTY_BASE
        fake_memory[base + MemoryReader.PARTY_HP_OFFSET] = 0x00
        fake_memory[base + MemoryReader.PARTY_HP_OFFSET + 1] = 100
        assert reader._read_party_hp(1) == [100]

    def test_two_party_members(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        base0 = MemoryReader.PARTY_BASE
        fake_memory[base0 + MemoryReader.PARTY_HP_OFFSET] = 0x00
        fake_memory[base0 + MemoryReader.PARTY_HP_OFFSET + 1] = 80

        base1 = MemoryReader.PARTY_BASE + MemoryReader.PARTY_STRUCT_SIZE
        fake_memory[base1 + MemoryReader.PARTY_HP_OFFSET] = 0x01
        fake_memory[base1 + MemoryReader.PARTY_HP_OFFSET + 1] = 0x00
        # 0x0100 = 256
        assert reader._read_party_hp(2) == [80, 256]

    def test_capped_at_six(self, mock_pyboy, fake_memory):
        """Passing count=7 should still only read 6 entries (min(7, 6))."""
        reader = MemoryReader(mock_pyboy)
        for i in range(7):
            base = MemoryReader.PARTY_BASE + (i * MemoryReader.PARTY_STRUCT_SIZE)
            fake_memory[base + MemoryReader.PARTY_HP_OFFSET] = 0x00
            fake_memory[base + MemoryReader.PARTY_HP_OFFSET + 1] = 10 + i

        result = reader._read_party_hp(7)
        assert len(result) == 6
        assert result == [10, 11, 12, 13, 14, 15]

    def test_exactly_six(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        for i in range(6):
            base = MemoryReader.PARTY_BASE + (i * MemoryReader.PARTY_STRUCT_SIZE)
            fake_memory[base + MemoryReader.PARTY_HP_OFFSET] = 0x00
            fake_memory[base + MemoryReader.PARTY_HP_OFFSET + 1] = 20 + i

        result = reader._read_party_hp(6)
        assert len(result) == 6
        assert result == [20, 21, 22, 23, 24, 25]


# ---------------------------------------------------------------------------
# is_in_battle
# ---------------------------------------------------------------------------


class TestIsInBattle:
    def test_not_in_battle(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_BATTLE_TYPE] = 0
        assert reader.is_in_battle() is False

    def test_in_wild_battle(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_BATTLE_TYPE] = 1
        assert reader.is_in_battle() is True

    def test_in_trainer_battle(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_BATTLE_TYPE] = 2
        assert reader.is_in_battle() is True


# ---------------------------------------------------------------------------
# player_whited_out
# ---------------------------------------------------------------------------


class TestPlayerWhitedOut:
    def test_all_fainted(self, mock_pyboy, fake_memory):
        """All party pokemon at 0 HP -> whited out."""
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 2
        # Both at 0 HP (default memory is 0)
        assert reader.player_whited_out() is True

    def test_some_alive(self, mock_pyboy, fake_memory):
        """At least one pokemon alive -> not whited out."""
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 2
        # First pokemon fainted (0 HP — default)
        # Second pokemon alive
        base1 = MemoryReader.PARTY_BASE + MemoryReader.PARTY_STRUCT_SIZE
        fake_memory[base1 + MemoryReader.PARTY_HP_OFFSET] = 0x00
        fake_memory[base1 + MemoryReader.PARTY_HP_OFFSET + 1] = 25
        assert reader.player_whited_out() is False

    def test_first_alive(self, mock_pyboy, fake_memory):
        """First pokemon alive triggers early return False in the loop."""
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 1
        base0 = MemoryReader.PARTY_BASE
        fake_memory[base0 + MemoryReader.PARTY_HP_OFFSET] = 0x00
        fake_memory[base0 + MemoryReader.PARTY_HP_OFFSET + 1] = 1
        assert reader.player_whited_out() is False

    def test_empty_party(self, mock_pyboy, fake_memory):
        """No party members -> loop body never runs -> returns True."""
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 0
        assert reader.player_whited_out() is True


# ---------------------------------------------------------------------------
# read_bag_items
# ---------------------------------------------------------------------------


class TestReadBagItems:
    def test_empty_bag(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 0
        assert reader.read_bag_items() == []

    def test_one_item(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 1
        fake_memory[ADDR_BAG_ITEMS] = 0x14  # Potion
        fake_memory[ADDR_BAG_ITEMS + 1] = 3  # qty
        assert reader.read_bag_items() == [(0x14, 3)]

    def test_two_items(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 2
        fake_memory[ADDR_BAG_ITEMS] = 0x14  # Potion
        fake_memory[ADDR_BAG_ITEMS + 1] = 2
        fake_memory[ADDR_BAG_ITEMS + 2] = 0x19  # Super Potion
        fake_memory[ADDR_BAG_ITEMS + 3] = 1
        assert reader.read_bag_items() == [(0x14, 2), (0x19, 1)]

    def test_ff_terminator_stops_early(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 3
        fake_memory[ADDR_BAG_ITEMS] = 0x14
        fake_memory[ADDR_BAG_ITEMS + 1] = 1
        fake_memory[ADDR_BAG_ITEMS + 2] = 0xFF  # terminator
        assert reader.read_bag_items() == [(0x14, 1)]

    def test_capped_at_max_slots(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 25  # more than BAG_MAX_SLOTS
        for i in range(BAG_MAX_SLOTS):
            fake_memory[ADDR_BAG_ITEMS + i * 2] = 0x04
            fake_memory[ADDR_BAG_ITEMS + i * 2 + 1] = 1
        result = reader.read_bag_items()
        assert len(result) == BAG_MAX_SLOTS


# ---------------------------------------------------------------------------
# find_healing_item
# ---------------------------------------------------------------------------


class TestFindHealingItem:
    def test_no_items(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 0
        assert reader.find_healing_item() is None

    def test_no_healing_items(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 1
        fake_memory[ADDR_BAG_ITEMS] = 0x04  # non-healing item
        fake_memory[ADDR_BAG_ITEMS + 1] = 5
        assert reader.find_healing_item() is None

    def test_finds_first_healing(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 2
        fake_memory[ADDR_BAG_ITEMS] = 0x04  # non-healing
        fake_memory[ADDR_BAG_ITEMS + 1] = 5
        fake_memory[ADDR_BAG_ITEMS + 2] = 0x14  # Potion
        fake_memory[ADDR_BAG_ITEMS + 3] = 3
        assert reader.find_healing_item() == (1, 0x14)

    def test_skips_zero_qty_healing(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 2
        fake_memory[ADDR_BAG_ITEMS] = 0x14  # Potion with 0 qty
        fake_memory[ADDR_BAG_ITEMS + 1] = 0
        fake_memory[ADDR_BAG_ITEMS + 2] = 0x19  # Super Potion with qty
        fake_memory[ADDR_BAG_ITEMS + 3] = 2
        assert reader.find_healing_item() == (1, 0x19)

    def test_returns_full_restore(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 1
        fake_memory[ADDR_BAG_ITEMS] = 0x10  # Full Restore
        fake_memory[ADDR_BAG_ITEMS + 1] = 1
        assert reader.find_healing_item() == (0, 0x10)


# ---------------------------------------------------------------------------
# read_party_species
# ---------------------------------------------------------------------------


class TestReadPartySpecies:
    def test_empty_party(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 0
        assert reader.read_party_species() == []

    def test_one_member(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 1
        fake_memory[MemoryReader.ADDR_PARTY_SPECIES_LIST] = 0xB0  # Charmander
        assert reader.read_party_species() == [0xB0]

    def test_three_members(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 3
        fake_memory[MemoryReader.ADDR_PARTY_SPECIES_LIST] = 0xB0
        fake_memory[MemoryReader.ADDR_PARTY_SPECIES_LIST + 1] = 0x24
        fake_memory[MemoryReader.ADDR_PARTY_SPECIES_LIST + 2] = 0xA5
        assert reader.read_party_species() == [0xB0, 0x24, 0xA5]

    def test_capped_at_six(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PARTY_COUNT] = 8
        for i in range(8):
            fake_memory[MemoryReader.ADDR_PARTY_SPECIES_LIST + i] = 0x24
        result = reader.read_party_species()
        assert len(result) == 6


# ---------------------------------------------------------------------------
# CollisionMap
# ---------------------------------------------------------------------------


class TestCollisionMap:
    def test_init_defaults(self):
        cm = CollisionMap()
        assert cm.grid == [[0] * 10 for _ in range(9)]
        assert cm.player_pos == (4, 4)
        assert cm.sprites == []

    def test_update_reads_collision_data(self, mock_pyboy):
        """update() should read game_area_collision and downsample 18x20 to 9x10."""
        cm = CollisionMap()
        raw = [[1] * 20 for _ in range(18)]
        mock_pyboy.game_wrapper.return_value.game_area_collision.return_value = raw
        cm.update(mock_pyboy)
        for row in cm.grid:
            for cell in row:
                assert cell == 1

    def test_update_walls_downsample(self, mock_pyboy):
        """A 2x2 block with any 0 should produce a 0 in the downsampled grid."""
        cm = CollisionMap()
        raw = [[1] * 20 for _ in range(18)]
        raw[0][0] = 0
        mock_pyboy.game_wrapper.return_value.game_area_collision.return_value = raw
        cm.update(mock_pyboy)
        assert cm.grid[0][0] == 0

    def test_update_all_walls(self, mock_pyboy):
        """All zeros -> all walls."""
        cm = CollisionMap()
        raw = [[0] * 20 for _ in range(18)]
        mock_pyboy.game_wrapper.return_value.game_area_collision.return_value = raw
        cm.update(mock_pyboy)
        for row in cm.grid:
            for cell in row:
                assert cell == 0

    def test_to_ascii(self):
        cm = CollisionMap()
        cm.grid = [[1] * 10 for _ in range(9)]
        cm.grid[0][0] = 0
        result = cm.to_ascii()
        assert isinstance(result, str)
        lines = result.strip().split("\n")
        assert len(lines) == 9
        assert "@" in lines[4]

    def test_to_ascii_with_sprites(self):
        cm = CollisionMap()
        cm.grid = [[1] * 10 for _ in range(9)]
        cm.sprites = [(0, 0)]
        result = cm.to_ascii()
        lines = result.strip().split("\n")
        assert "S" in lines[0]

    def test_player_pos_always_center(self):
        cm = CollisionMap()
        assert cm.player_pos == (4, 4)


# ---------------------------------------------------------------------------
# Quest helpers: parcel / pokedex / facing
# ---------------------------------------------------------------------------


class TestQuestHelpers:
    def test_has_item_true_and_false(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 1
        fake_memory[ADDR_BAG_ITEMS] = ITEM_OAKS_PARCEL
        fake_memory[ADDR_BAG_ITEMS + 1] = 1
        assert reader.has_item(ITEM_OAKS_PARCEL) is True
        assert reader.has_item(0x14) is False

    def test_has_item_zero_quantity_is_false(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 1
        fake_memory[ADDR_BAG_ITEMS] = ITEM_OAKS_PARCEL
        fake_memory[ADDR_BAG_ITEMS + 1] = 0
        assert reader.has_item(ITEM_OAKS_PARCEL) is False

    def test_has_parcel(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[ADDR_BAG_COUNT] = 0
        assert reader.has_parcel() is False
        fake_memory[ADDR_BAG_COUNT] = 1
        fake_memory[ADDR_BAG_ITEMS] = ITEM_OAKS_PARCEL
        fake_memory[ADDR_BAG_ITEMS + 1] = 1
        assert reader.has_parcel() is True

    def test_has_pokedex_bit(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_WD74B] = 0x00
        assert reader.has_pokedex() is False
        fake_memory[MemoryReader.ADDR_WD74B] = MemoryReader.BIT_GOT_POKEDEX
        assert reader.has_pokedex() is True
        # other bits set but not the pokedex bit -> still False
        fake_memory[MemoryReader.ADDR_WD74B] = 0xFF & ~MemoryReader.BIT_GOT_POKEDEX
        assert reader.has_pokedex() is False

    def test_read_player_facing_raw_and_name(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        for raw, name in MemoryReader.FACING_NAMES.items():
            fake_memory[MemoryReader.ADDR_PLAYER_FACING] = raw
            assert reader.read_player_facing() == raw
            assert reader.read_player_facing_name() == name

    def test_read_player_facing_name_unknown(self, mock_pyboy, fake_memory):
        reader = MemoryReader(mock_pyboy)
        fake_memory[MemoryReader.ADDR_PLAYER_FACING] = 7
        assert reader.read_player_facing_name() == "?7"
