"""
Memory Reader — Extract game state from PyBoy emulator memory.

Addresses are for Pokemon Red/Blue (US release).
Swap out the address maps for other games.
"""

from dataclasses import dataclass, field
from typing import List

# Gen 1 type byte → name mapping (from pokered disassembly)
TYPE_ID_MAP: dict[int, str] = {
    0x00: "normal",
    0x01: "fighting",
    0x02: "flying",
    0x03: "poison",
    0x04: "ground",
    0x05: "rock",
    0x07: "bug",
    0x08: "ghost",
    0x14: "fire",
    0x15: "water",
    0x16: "electric",
    0x17: "grass",
    0x18: "ice",
    0x19: "psychic",
    0x1A: "dragon",
}

# Species byte → name for early-game Pokemon (Route 1 + Viridian Forest + starters/evos)
SPECIES_ID_MAP: dict[int, str] = {
    0x24: "Pidgey",
    0xA5: "Rattata",
    0x7B: "Caterpie",
    0x70: "Weedle",
    0x54: "Pikachu",
    0x6D: "Metapod",
    0x6E: "Kakuna",
    0xB0: "Charmander",
    0xB2: "Charmeleon",
    0xB1: "Squirtle",
    0xB3: "Wartortle",
    0x99: "Bulbasaur",
    0x09: "Ivysaur",
    0x7A: "Butterfree",
    0x97: "Beedrill",
    0x96: "Pidgeotto",
}

# Healing items the agent knows how to use in battle
HEALING_ITEM_IDS: dict[int, str] = {
    0x14: "Potion",
    0x19: "Super Potion",
    0x1A: "Hyper Potion",
    0x10: "Full Restore",
}

# Bag memory layout
ADDR_BAG_COUNT = 0xD31D
ADDR_BAG_ITEMS = 0xD31E  # pairs of [item_id, quantity], terminated by 0xFF
BAG_MAX_SLOTS = 20

# Quest items
ITEM_OAKS_PARCEL = 0x46  # given by the Viridian Mart clerk, delivered to Prof. Oak


@dataclass
class BattleState:
    """Current battle context."""

    battle_type: int = 0  # 0=none, 1=wild, 2=trainer
    enemy_hp: int = 0
    enemy_max_hp: int = 0
    enemy_level: int = 0
    enemy_species: int = 0
    enemy_type1: int = 0
    enemy_type2: int = 0
    player_hp: int = 0
    player_max_hp: int = 0
    player_level: int = 0
    player_species: int = 0
    moves: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    move_pp: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    party_count: int = 0
    party_hp: List[int] = field(default_factory=list)

    @property
    def enemy_type_name(self) -> str:
        """Resolve enemy_type1 byte to a type name string."""
        return TYPE_ID_MAP.get(self.enemy_type1, "normal")

    @property
    def enemy_species_name(self) -> str:
        """Resolve enemy_species byte to a species name string."""
        return SPECIES_ID_MAP.get(self.enemy_species, f"#{self.enemy_species:02X}")


@dataclass
class OverworldState:
    """Current overworld context."""

    map_id: int = 0
    x: int = 0
    y: int = 0
    badges: int = 0
    party_count: int = 0
    party_hp: List[int] = field(default_factory=list)
    money: int = 0
    text_box_active: bool = False


class MemoryReader:
    """
    Read game state from PyBoy memory.

    Pokemon Red/Blue memory map:
    https://datacrystal.romhacking.net/wiki/Pok%C3%A9mon_Red/Blue:RAM_map
    """

    # --- Address constants (Pokemon Red/Blue US) ---

    # Battle
    ADDR_BATTLE_TYPE = 0xD057
    ADDR_ENEMY_HP_HI = 0xCFE6
    ADDR_ENEMY_HP_LO = 0xCFE7
    ADDR_ENEMY_MAX_HP_HI = 0xCFF4
    ADDR_ENEMY_MAX_HP_LO = 0xCFF5
    ADDR_ENEMY_LEVEL = 0xCFF3
    ADDR_ENEMY_SPECIES = 0xCFE5
    ADDR_ENEMY_TYPE1 = 0xCFEA
    ADDR_ENEMY_TYPE2 = 0xCFEB

    # Player party (lead pokemon)
    ADDR_PLAYER_HP_HI = 0xD015
    ADDR_PLAYER_HP_LO = 0xD016
    ADDR_PLAYER_MAX_HP_HI = 0xD023
    ADDR_PLAYER_MAX_HP_LO = 0xD024
    ADDR_PLAYER_LEVEL = 0xD022
    ADDR_PLAYER_SPECIES = 0xD014

    # Moves (lead pokemon)
    ADDR_MOVE_1 = 0xD01C
    ADDR_MOVE_2 = 0xD01D
    ADDR_MOVE_3 = 0xD01E
    ADDR_MOVE_4 = 0xD01F

    # Move PP (lead pokemon)
    ADDR_PP_1 = 0xD02C
    ADDR_PP_2 = 0xD02D
    ADDR_PP_3 = 0xD02E
    ADDR_PP_4 = 0xD02F

    # Party
    ADDR_PARTY_COUNT = 0xD163
    ADDR_PARTY_SPECIES_LIST = 0xD164  # 6 bytes, one species ID per party member

    # Party pokemon HP addresses (6 pokemon, 44 bytes apart)
    PARTY_BASE = 0xD16B
    PARTY_STRUCT_SIZE = 44
    PARTY_HP_OFFSET = 1  # Offset to current HP within party struct

    # Overworld
    ADDR_MAP_ID = 0xD35E
    ADDR_PLAYER_X = 0xD362
    ADDR_PLAYER_Y = 0xD361
    ADDR_BADGES = 0xD356

    # Money (BCD encoded, 3 bytes)
    ADDR_MONEY_1 = 0xD347
    ADDR_MONEY_2 = 0xD348
    ADDR_MONEY_3 = 0xD349

    # Game state flags (pokered wd730)
    # bit 1: d-pad input disabled (text boxes, menus)
    # bit 5: simulated joypad active (scripted movement, e.g. Oak walking)
    # bit 6: text/script display active (set by DisplayTextID)
    ADDR_WD730 = 0xD730

    # Player facing direction (pokered wSpritePlayerStateData1FacingDirection).
    # Encoded as multiples of 4: 0=down, 4=up, 8=left, 12=right.
    ADDR_PLAYER_FACING = 0xC109
    FACING_NAMES = {0: "down", 4: "up", 8: "left", 12: "right"}

    # Early-game event flags (pokered wd74b). bit 5 (0x20) = obtained the Pokedex, set when
    # Oak takes the parcel. Confirmed against telemetry in the discovery spike.
    ADDR_WD74B = 0xD74B
    BIT_GOT_POKEDEX = 0x20

    def __init__(self, pyboy):
        self.pyboy = pyboy

    def _read(self, addr: int) -> int:
        """Read a single byte from memory."""
        return self.pyboy.memory[addr]

    def _read_16(self, addr_hi: int, addr_lo: int) -> int:
        """Read a 16-bit big-endian value from two addresses."""
        return (self._read(addr_hi) << 8) | self._read(addr_lo)

    def _read_bcd(self, *addrs) -> int:
        """Read BCD-encoded value across multiple bytes."""
        result = 0
        for addr in addrs:
            byte = self._read(addr)
            high = (byte >> 4) & 0x0F
            low = byte & 0x0F
            result = result * 100 + high * 10 + low
        return result

    def read_battle_state(self) -> BattleState:
        """Read full battle context from memory."""
        battle_type = self._read(self.ADDR_BATTLE_TYPE)

        state = BattleState(battle_type=battle_type)

        if battle_type == 0:
            return state

        # Enemy
        state.enemy_hp = self._read_16(self.ADDR_ENEMY_HP_HI, self.ADDR_ENEMY_HP_LO)
        state.enemy_max_hp = self._read_16(self.ADDR_ENEMY_MAX_HP_HI, self.ADDR_ENEMY_MAX_HP_LO)
        state.enemy_level = self._read(self.ADDR_ENEMY_LEVEL)
        state.enemy_species = self._read(self.ADDR_ENEMY_SPECIES)
        state.enemy_type1 = self._read(self.ADDR_ENEMY_TYPE1)
        state.enemy_type2 = self._read(self.ADDR_ENEMY_TYPE2)

        # Player lead
        state.player_hp = self._read_16(self.ADDR_PLAYER_HP_HI, self.ADDR_PLAYER_HP_LO)
        state.player_max_hp = self._read_16(self.ADDR_PLAYER_MAX_HP_HI, self.ADDR_PLAYER_MAX_HP_LO)
        state.player_level = self._read(self.ADDR_PLAYER_LEVEL)
        state.player_species = self._read(self.ADDR_PLAYER_SPECIES)

        # Moves
        state.moves = [
            self._read(self.ADDR_MOVE_1),
            self._read(self.ADDR_MOVE_2),
            self._read(self.ADDR_MOVE_3),
            self._read(self.ADDR_MOVE_4),
        ]

        # PP
        state.move_pp = [
            self._read(self.ADDR_PP_1),
            self._read(self.ADDR_PP_2),
            self._read(self.ADDR_PP_3),
            self._read(self.ADDR_PP_4),
        ]

        # Party
        state.party_count = self._read(self.ADDR_PARTY_COUNT)
        state.party_hp = self._read_party_hp(state.party_count)

        return state

    def read_overworld_state(self) -> OverworldState:
        """Read overworld navigation context from memory."""
        party_count = self._read(self.ADDR_PARTY_COUNT)

        return OverworldState(
            map_id=self._read(self.ADDR_MAP_ID),
            x=self._read(self.ADDR_PLAYER_X),
            y=self._read(self.ADDR_PLAYER_Y),
            badges=self._read(self.ADDR_BADGES),
            party_count=party_count,
            party_hp=self._read_party_hp(party_count),
            money=self._read_bcd(self.ADDR_MONEY_1, self.ADDR_MONEY_2, self.ADDR_MONEY_3),
            text_box_active=self._is_text_or_script_active(),
        )

    def _is_text_or_script_active(self) -> bool:
        """Detect text box / menu / scripted movement via wd730 flags."""
        d730 = self._read(self.ADDR_WD730)
        # bit 1 (0x02): d-pad disabled (text/menu active)
        # bit 5 (0x20): simulated joypad (scripted NPC movement)
        # bit 6 (0x40): text/script display in progress
        return bool(d730 & 0x62)

    def _read_party_hp(self, count: int) -> list[int]:
        """Read HP for each party member."""
        hp_list = []
        for i in range(min(count, 6)):
            base = self.PARTY_BASE + (i * self.PARTY_STRUCT_SIZE)
            hp = self._read_16(base + self.PARTY_HP_OFFSET, base + self.PARTY_HP_OFFSET + 1)
            hp_list.append(hp)
        return hp_list

    def read_bag_items(self) -> list[tuple[int, int]]:
        """Read item_id/quantity pairs from the bag."""
        count = self._read(ADDR_BAG_COUNT)
        items: list[tuple[int, int]] = []
        for i in range(min(count, BAG_MAX_SLOTS)):
            addr = ADDR_BAG_ITEMS + i * 2
            item_id = self._read(addr)
            if item_id == 0xFF:
                break
            quantity = self._read(addr + 1)
            items.append((item_id, quantity))
        return items

    def find_healing_item(self) -> tuple[int, int] | None:
        """Find first healing item with qty > 0. Returns (bag_index, item_id) or None."""
        items = self.read_bag_items()
        for idx, (item_id, qty) in enumerate(items):
            if item_id in HEALING_ITEM_IDS and qty > 0:
                return (idx, item_id)
        return None

    def has_item(self, item_id: int) -> bool:
        """True if the bag holds at least one of ``item_id``."""
        return any(iid == item_id and qty > 0 for iid, qty in self.read_bag_items())

    def has_parcel(self) -> bool:
        """True while Oak's Parcel is in the bag (between the Mart and delivering it to Oak)."""
        return self.has_item(ITEM_OAKS_PARCEL)

    def has_pokedex(self) -> bool:
        """True once Oak takes the parcel and hands over the Pokedex (wd74b bit 5)."""
        return bool(self._read(self.ADDR_WD74B) & self.BIT_GOT_POKEDEX)

    def read_player_facing(self) -> int:
        """Raw facing byte (0=down, 4=up, 8=left, 12=right)."""
        return self._read(self.ADDR_PLAYER_FACING)

    def read_player_facing_name(self) -> str:
        """Facing direction as a name, or ``?<raw>`` if the byte is unrecognised."""
        raw = self.read_player_facing()
        return self.FACING_NAMES.get(raw, f"?{raw}")

    def read_party_species(self) -> list[int]:
        """Read species ID for each party member."""
        count = self._read(self.ADDR_PARTY_COUNT)
        return [self._read(self.ADDR_PARTY_SPECIES_LIST + i) for i in range(min(count, 6))]

    def read_party(self) -> list[dict]:
        """Per-slot ``{species, level, hp, max_hp}`` for the current party.

        Offsets within each 44-byte party struct: current HP @ +1, level @ +33,
        max HP @ +34 (all confirmed against the addresses this reader already uses).
        """
        count = self._read(self.ADDR_PARTY_COUNT)
        out: list[dict] = []
        for i in range(min(count, 6)):
            base = self.PARTY_BASE + (i * self.PARTY_STRUCT_SIZE)
            species_id = self._read(self.ADDR_PARTY_SPECIES_LIST + i)
            out.append(
                {
                    "species": SPECIES_ID_MAP.get(species_id, f"#{species_id:02X}"),
                    "level": self._read(base + 33),
                    "hp": self._read_16(base + self.PARTY_HP_OFFSET, base + self.PARTY_HP_OFFSET + 1),
                    "max_hp": self._read_16(base + 34, base + 35),
                }
            )
        return out

    def is_in_battle(self) -> bool:
        """Quick check: are we in a battle?"""
        return self._read(self.ADDR_BATTLE_TYPE) != 0

    def player_whited_out(self) -> bool:
        """Check if all party pokemon have fainted."""
        count = self._read(self.ADDR_PARTY_COUNT)
        for hp in self._read_party_hp(count):
            if hp > 0:
                return False
        return True


class CollisionMap:
    """9x10 walkability grid from PyBoy's collision data."""

    def __init__(self):
        self.grid: list[list[int]] = [[0] * 10 for _ in range(9)]
        self.player_pos: tuple[int, int] = (4, 4)
        self.sprites: list[tuple[int, int]] = []

    def update(self, pyboy) -> None:
        """Read collision data and downsample 18x20 to 9x10."""
        # ``game_wrapper`` is a property in modern PyBoy and a method in older versions;
        # calling the property raises TypeError (silently swallowed by the agent), which left the
        # grid all-walls and disabled A* pathfinding. Support both shapes.
        gw = pyboy.game_wrapper
        if callable(gw):
            gw = gw()
        raw = gw.game_area_collision()
        self.sprites = []
        for r in range(9):
            for c in range(10):
                cells = [
                    raw[r * 2][c * 2],
                    raw[r * 2][c * 2 + 1],
                    raw[r * 2 + 1][c * 2],
                    raw[r * 2 + 1][c * 2 + 1],
                ]
                self.grid[r][c] = 1 if all(v != 0 for v in cells) else 0

    def to_ascii(self) -> str:
        """Printable map: @ = player, # = wall, . = walkable, S = sprite."""
        sprite_set = set(self.sprites)
        lines = []
        for r in range(9):
            row = []
            for c in range(10):
                if (r, c) == self.player_pos:
                    row.append("@")
                elif (r, c) in sprite_set:
                    row.append("S")
                elif self.grid[r][c] == 0:
                    row.append("#")
                else:
                    row.append(".")
                row.append(" ")
            lines.append("".join(row).rstrip())
        return "\n".join(lines)
