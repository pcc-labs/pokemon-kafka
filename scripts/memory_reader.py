"""
Memory Reader — Extract game state from PyBoy emulator memory.

Per-game addresses live in ``game_profile.py`` (Red/Blue and Yellow supported);
the profile is auto-detected from the cartridge header unless passed explicitly.
"""

from dataclasses import dataclass, field
from typing import List

from game_profile import RED_BLUE, GameProfile, detect_profile
from text_decoder import decode_grid

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

# Bag memory layout (Red/Blue defaults; MemoryReader reads via its profile)
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

    Addresses come from the game profile (Red/Blue or Yellow); the legacy
    ``ADDR_*`` names are kept as instance attributes so callers are unchanged.
    Pokemon Red/Blue memory map:
    https://datacrystal.romhacking.net/wiki/Pok%C3%A9mon_Red/Blue:RAM_map
    """

    # Game-invariant layout constants
    PARTY_STRUCT_SIZE = 44
    PARTY_HP_OFFSET = 1  # Offset to current HP within party struct

    # Class-level defaults (Red/Blue) for callers/tests that reference the constants
    # without an instance; __init__ shadows every one of these from the actual profile.
    ADDR_BATTLE_TYPE = RED_BLUE.addr_battle_type
    ADDR_ENEMY_HP_HI = RED_BLUE.addr_enemy_hp_hi
    ADDR_ENEMY_HP_LO = RED_BLUE.addr_enemy_hp_lo
    ADDR_ENEMY_MAX_HP_HI = RED_BLUE.addr_enemy_max_hp_hi
    ADDR_ENEMY_MAX_HP_LO = RED_BLUE.addr_enemy_max_hp_lo
    ADDR_ENEMY_LEVEL = RED_BLUE.addr_enemy_level
    ADDR_ENEMY_SPECIES = RED_BLUE.addr_enemy_species
    ADDR_ENEMY_TYPE1 = RED_BLUE.addr_enemy_type1
    ADDR_ENEMY_TYPE2 = RED_BLUE.addr_enemy_type2
    ADDR_PLAYER_HP_HI = RED_BLUE.addr_player_hp_hi
    ADDR_PLAYER_HP_LO = RED_BLUE.addr_player_hp_lo
    ADDR_PLAYER_MAX_HP_HI = RED_BLUE.addr_player_max_hp_hi
    ADDR_PLAYER_MAX_HP_LO = RED_BLUE.addr_player_max_hp_lo
    ADDR_PLAYER_LEVEL = RED_BLUE.addr_player_level
    ADDR_PLAYER_SPECIES = RED_BLUE.addr_player_species
    ADDR_MOVE_1 = RED_BLUE.addr_move_1
    ADDR_MOVE_2 = RED_BLUE.addr_move_2
    ADDR_MOVE_3 = RED_BLUE.addr_move_3
    ADDR_MOVE_4 = RED_BLUE.addr_move_4
    ADDR_PP_1 = RED_BLUE.addr_pp_1
    ADDR_PP_2 = RED_BLUE.addr_pp_2
    ADDR_PP_3 = RED_BLUE.addr_pp_3
    ADDR_PP_4 = RED_BLUE.addr_pp_4
    ADDR_PARTY_COUNT = RED_BLUE.addr_party_count
    ADDR_PARTY_SPECIES_LIST = RED_BLUE.addr_party_species_list
    PARTY_BASE = RED_BLUE.party_base
    ADDR_MAP_ID = RED_BLUE.addr_map_id
    ADDR_PLAYER_X = RED_BLUE.addr_player_x
    ADDR_PLAYER_Y = RED_BLUE.addr_player_y
    ADDR_BADGES = RED_BLUE.addr_badges
    ADDR_MONEY_1 = RED_BLUE.addr_money_1
    ADDR_MONEY_2 = RED_BLUE.addr_money_2
    ADDR_MONEY_3 = RED_BLUE.addr_money_3
    ADDR_WD730 = RED_BLUE.addr_wd730
    ADDR_WD74B = RED_BLUE.addr_wd74b
    ADDR_PLAYER_FACING = RED_BLUE.addr_player_facing

    # Player facing direction (wSpritePlayerStateData1FacingDirection).
    # Encoded as multiples of 4: 0=down, 4=up, 8=left, 12=right.
    FACING_NAMES = {0: "down", 4: "up", 8: "left", 12: "right"}

    # Early-game event flags (pokered wd74b). bit 5 (0x20) = obtained the Pokedex, set when
    # Oak takes the parcel. Confirmed against telemetry in the discovery spike.
    BIT_GOT_POKEDEX = 0x20

    def __init__(self, pyboy, profile: GameProfile | None = None):
        self.pyboy = pyboy
        self.profile = profile or detect_profile(pyboy)
        p = self.profile
        # Legacy attribute names, now per-instance from the profile.
        self.ADDR_BATTLE_TYPE = p.addr_battle_type
        self.ADDR_ENEMY_HP_HI = p.addr_enemy_hp_hi
        self.ADDR_ENEMY_HP_LO = p.addr_enemy_hp_lo
        self.ADDR_ENEMY_MAX_HP_HI = p.addr_enemy_max_hp_hi
        self.ADDR_ENEMY_MAX_HP_LO = p.addr_enemy_max_hp_lo
        self.ADDR_ENEMY_LEVEL = p.addr_enemy_level
        self.ADDR_ENEMY_SPECIES = p.addr_enemy_species
        self.ADDR_ENEMY_TYPE1 = p.addr_enemy_type1
        self.ADDR_ENEMY_TYPE2 = p.addr_enemy_type2
        self.ADDR_PLAYER_HP_HI = p.addr_player_hp_hi
        self.ADDR_PLAYER_HP_LO = p.addr_player_hp_lo
        self.ADDR_PLAYER_MAX_HP_HI = p.addr_player_max_hp_hi
        self.ADDR_PLAYER_MAX_HP_LO = p.addr_player_max_hp_lo
        self.ADDR_PLAYER_LEVEL = p.addr_player_level
        self.ADDR_PLAYER_SPECIES = p.addr_player_species
        self.ADDR_MOVE_1, self.ADDR_MOVE_2 = p.addr_move_1, p.addr_move_2
        self.ADDR_MOVE_3, self.ADDR_MOVE_4 = p.addr_move_3, p.addr_move_4
        self.ADDR_PP_1, self.ADDR_PP_2 = p.addr_pp_1, p.addr_pp_2
        self.ADDR_PP_3, self.ADDR_PP_4 = p.addr_pp_3, p.addr_pp_4
        self.ADDR_PARTY_COUNT = p.addr_party_count
        self.ADDR_PARTY_SPECIES_LIST = p.addr_party_species_list
        self.PARTY_BASE = p.party_base
        self.ADDR_MAP_ID = p.addr_map_id
        self.ADDR_PLAYER_X = p.addr_player_x
        self.ADDR_PLAYER_Y = p.addr_player_y
        self.ADDR_BADGES = p.addr_badges
        self.ADDR_MONEY_1, self.ADDR_MONEY_2, self.ADDR_MONEY_3 = (
            p.addr_money_1,
            p.addr_money_2,
            p.addr_money_3,
        )
        self.ADDR_WD730 = p.addr_wd730
        self.ADDR_WD74B = p.addr_wd74b
        self.ADDR_PLAYER_FACING = p.addr_player_facing

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

    # On-screen text box region of the 20x18 BG tilemap: the dialogue/sign box occupies the
    # bottom rows, inside the 1-tile border. Reading just this region keeps menus/HUD noise out.
    _TEXT_X0, _TEXT_X1 = 1, 19  # cols [1, 19)
    _TEXT_Y0, _TEXT_Y1 = 12, 18  # rows [12, 18)

    def read_dialogue(self) -> str:
        """Decode the on-screen text box (signs / NPC dialogue / battle messages) from the tilemap.

        Pokémon Red draws the text box on the *window* layer (battle/menu messages) but some
        overworld text lands on the background, so we read the bottom message-box region of both and
        prefer the window. Returns ``""`` if unavailable or empty. Pull this only when a text box is
        active (``OverworldState.text_box_active``) — it walks a tilemap region, not free per frame.
        """

        def _read(layer) -> str:
            try:
                rows = [
                    [int(layer[x, y]) for x in range(self._TEXT_X0, self._TEXT_X1)]
                    for y in range(self._TEXT_Y0, self._TEXT_Y1)
                ]
            except Exception:
                return ""
            return decode_grid(rows)

        try:
            window = _read(self.pyboy.tilemap_window)
            return window or _read(self.pyboy.tilemap_background)
        except Exception:
            return ""

    def read_enemy_hp(self) -> int:
        """Current enemy HP (lightweight — for observing a move's damage without re-reading the
        whole battle state, which lets callers compute damage from a before/after delta)."""
        return self._read_16(self.ADDR_ENEMY_HP_HI, self.ADDR_ENEMY_HP_LO)

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
        count = self._read(self.profile.addr_bag_count)
        items: list[tuple[int, int]] = []
        for i in range(min(count, BAG_MAX_SLOTS)):
            addr = self.profile.addr_bag_items + i * 2
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
