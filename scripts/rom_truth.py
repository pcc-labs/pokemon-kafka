"""ROM truth — world topology as lookup, not search.

Every wall class the benchmarks measured (docs/expedition-spec.md) traces to models re-deriving
facts by collision that sit deterministically in the ROM: warp tables ("gym sealed" — the door
mats warp to LAST_MAP), map connections ("Route 3 is a pocket" — its exit is the NORTH edge to
Route 4), and collision grids (the A* pilot's stale-grid wedges). This module parses those
structures straight out of ``rom/pokemon_red.gb`` (Gen 1, pret/pokered layout) and serves them
three ways:

    uv run python scripts/rom_truth.py extract                 # -> references/rom_truth.json
    uv run python scripts/rom_truth.py route 54 59             # map-level hop chain with tiles
    uv run python scripts/rom_truth.py seed-worldmap 2 14 15 --out seed.worldmap

``seed-worldmap`` writes a :class:`world_map.WorldMap` snapshot (grids + bounds) that
``relay.py --seed-worldmap`` already accepts — the pilot starts every listed map fully known,
with zero agent-code changes.

Validation discipline: the parser was checked against ground truth *measured live* by past runs —
Pewter City's seven warps (tests/test_agent_mtmoon.py CITY_WARPS), the gym's (4,13)/(5,13)
LAST_MAP mats, Route 3's empty warp table and 70x18 bounds, and 100 % cell agreement with the
learned ``badge1_gym_hp6.worldmap`` (465/465 cells on map 2). A wrong collision grid misroutes
silently, so ``extract`` records the ROM's sha256 and refuses a mismatched cached file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from world_map import WorldMap

ROM_DEFAULT = Path(__file__).resolve().parent.parent / "rom" / "pokemon_red.gb"
TRUTH_DEFAULT = Path(__file__).resolve().parent.parent / "references" / "rom_truth.json"

# Gen 1 Red (US) structure offsets, pret/pokered names. MapHeaderBanks/Pointers give each map's
# header; the header gives dims (in 2x2-tile blocks), the connections byte (N/S/W/E edge links),
# and the object data (warps, signs, sprites). The Tilesets table maps a header's tileset id to
# its blockset + the 0xFF-terminated list of walkable tile ids; a walk tile's passability is its
# 2x2 quad's bottom-left tile being in that list (the engine's own rule).
MAP_HEADER_POINTERS = 0x01AE  # bank 0: 2-byte pointer per map id
MAP_HEADER_BANKS = 0xC23D  # bank 3 (3:423D): 1 byte per map id
TILESETS = 0xC7BE  # bank 3 (3:47BE): 12-byte entries
NUM_MAPS = 248
MAX_TILESET = 23  # measured: 226 of this cartridge's maps use 0-23; the lone outlier claims 103
LAST_MAP = 0xFF  # warp destination "back where we came from" (door mats)
_CONN_BITS = (("north", 0x08), ("south", 0x04), ("west", 0x02), ("east", 0x01))
# pokered's TilePairCollisionsLand: triples of (tileset, tile a, tile b) ending in 0xFF. Moving
# BETWEEN these two tiles is refused even though each is individually walkable — the cave-wall
# lips in the CAVERN tileset (17) and the forest edges in FOREST (3). This is an EDGE property:
# no per-cell walkable/solid grid can express it, so ``grid`` alone over-reports connectivity.
TILE_PAIR_COLLISIONS_LAND = 0x0C7E


def _u16(rom: bytes, off: int) -> int:
    return rom[off] | (rom[off + 1] << 8)


def _faddr(bank: int, addr: int) -> int:
    """GB banked address -> file offset (0x4000-0x7FFF window maps into ``bank``)."""
    return addr if addr < 0x4000 else bank * 0x4000 + (addr - 0x4000)


def _walkable_tiles(rom: bytes, coll_ptr: int) -> set[int]:
    tiles: set[int] = set()
    i = coll_ptr
    while rom[i] != 0xFF:
        tiles.add(rom[i])
        i += 1
    return tiles


def tile_pairs(rom: bytes) -> set[tuple[int, int, int]]:
    """``{(tileset, tile_a, tile_b)}`` the engine refuses to walk between, both directions."""
    pairs: set[tuple[int, int, int]] = set()
    i = TILE_PAIR_COLLISIONS_LAND
    while rom[i] != 0xFF:
        ts, a, b = rom[i], rom[i + 1], rom[i + 2]
        pairs.add((ts, a, b))
        pairs.add((ts, b, a))
        i += 3
    return pairs


MEASURED_GATES = Path(__file__).resolve().parent.parent / "references" / "measured_gates.json"

_DIR_OF = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right"}


# A refused step whose sentence is a *door* talking, versus one where a body simply spoke. The
# distinction cost this project a session: Silph 5F's (9,16) was recorded as a gate carrying
# "I heard a kid was wandering around." — a wandering NPC's small talk, written down as a
# permanent wall and applied symmetrically ever after. It sat on the single tile between the 9F
# landing and the CARD KEY, so every route to the key was pruned before it was planned, on every
# run, invisibly. Four of 5F's fifteen "gates" were bodies talking.
#
# The bias is deliberate and it is the opposite of what this file used to say. Over-blocking was
# called the safe direction because under-blocking "costs a run"; measured, over-blocking costs
# *every* run and says nothing, while under-blocking costs one hop that the leg then measures and
# records. So a gate is honoured only when the sentence sounds like a lock. Anything else is a
# body, and bodies move.
DOOR_PHRASES = ("needs a", "locked", "won't open", "wont open", "no key", "can't open", "cant open")


def is_door_text(said: str | None) -> bool:
    """Does this refusal sentence sound like a door, rather than a body that happened to talk?"""
    return bool(said) and any(phrase in said.lower() for phrase in DOOR_PHRASES)


def door_gates(entries: dict) -> dict:
    """One map's measured gates minus the ones that are a body talking.

    A *silent* refusal is kept: nothing spoke, so nothing was standing there, and the step is a
    wall the collision grid failed to express. What is dropped is a refusal that came with small
    talk — "AAAAAAA got 1400 for winning!", "I am one of the 4 ROCKET BROTHERS!" — which is a
    sprite in the way, not a locked door. Across the measured file that is 106 of 130 entries;
    the pocket model every Silph route was planned on stood on 82% sprite chatter.
    """
    return {step: said for step, said in entries.items() if is_door_text(said) or not (said or "").strip()}


def gates_the_bag_opens(entries: dict, held: set[str]) -> dict:
    """One map's door gates minus the ones naming an item we are carrying.

    A locked door is only a wall while the key is missing. The door says what it wants — "Darn!
    It needs a CARD KEY!" — so the bag answers it directly, and the moment CARD KEY is picked up
    every door that names it stops being terrain. Without this the engine plans as if the key had
    never been found: the leg that took the key from 5F then reported `no-path` on 3F -> 7F, a
    refusal from our own model rather than from the world.
    """
    upper = {name.upper() for name in held}
    return {step: said for step, said in entries.items() if not any(name in (said or "").upper() for name in upper)}


def load_measured_gates(path: Path | None = None) -> dict:
    """Steps the *engine* refuses that the collision grid calls walkable.

    The grid has no way to express a script gate. Inside Silph it calls a card-key door plain
    floor, and so every region computed from it over-reports — 343 cells claimed on 3F against
    233 actually walkable. These are measured by ``Rig.survey_pocket``, which tries each step
    and records the ones the engine refuses, and merging them here is what makes the rest of the
    module honest: ``passable`` consults them, so ``path_on_map``, ``road.reachable``,
    ``blocking_body`` and every route planned on top all stop planning through locked doors.
    """
    path = path or MEASURED_GATES
    if not path.exists():
        return {}
    return json.loads(path.read_text())


BATTLE_PAGE_WORDS = ("for winning", "gained", "exp. points", "attack missed", "fainted", "sent out", "got on ")


def is_battle_sentence(text: str) -> bool:
    """A sentence the battle engine prints, not a door: the award and EXP pages, a missed attack.

    Measured 2026-09-04: twenty-one 'gates' across Saffron and five Silph floors were the award
    page "got 500 for winning!" -- a step refused while that page owned the screen was recorded
    as a shut door, and every planner since routed around a wall that was never there.
    """
    t = (text or "").lower()
    return any(w in t for w in BATTLE_PAGE_WORDS)


def merge_measured_gates(gates: dict, path: Path | None = None) -> dict:
    """Fold a survey's gates into the shared file. Knowledge accumulates or it is not knowledge --
    but a battle page is not knowledge about a door, and it is dropped here (see is_battle_sentence)."""
    path = path or MEASURED_GATES
    merged = load_measured_gates(path)
    for map_id, entries in gates.items():
        kept = {k: v for k, v in entries.items() if not is_battle_sentence(str(v))}
        if kept:
            merged.setdefault(str(map_id), {}).update(kept)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return merged


DEAD_WARPS = Path(__file__).resolve().parent.parent / "references" / "dead_warps.json"


def load_dead_warps(path: Path | None = None) -> dict:
    """Warp tiles the engine will not fire, by map — doors the extraction believes in and the
    world does not.

    ``measured_gates`` records refused *steps*; a dead door is a refused *warp*, and it had
    nowhere to live. Silph 1F's pad at (16,10) was measured dead early on, written into a
    learnings file, and then routed through by every planner since — including the pocket router,
    which proposed it as the first hop of three different chains.
    """
    path = path or DEAD_WARPS
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def merge_dead_warps(dead: dict, path: Path | None = None) -> dict:
    path = path or DEAD_WARPS
    merged = load_dead_warps(path)
    for map_id, tiles in dead.items():
        merged.setdefault(str(map_id), {}).update(tiles)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return merged


def passable(m: dict, pairs: set[tuple[int, int, int]], x0: int, y0: int, x1: int, y1: int) -> bool:
    """Can the player step from (x0,y0) to (x1,y1) on map ``m``? Both cells must be walkable
    AND the move must not be a tile-pair collision. Checking ``grid`` alone is not enough: on
    Mt. Moon B2F (map 61) the engine refuses (25,11)->(25,12) although both cells are open,
    because the pair is CAVERN 0x20/0x05 — measured live, see
    docs/learnings/mtmoon-collision-rule-audit.md."""
    h, w = m["height"], m["width"]
    if not (0 <= x0 < w and 0 <= y0 < h and 0 <= x1 < w and 0 <= y1 < h):
        return False
    if m["grid"][y0][x0] != "1" or m["grid"][y1][x1] != "1":
        return False
    gates = m.get("gates")
    if gates:
        forward = _DIR_OF.get((x1 - x0, y1 - y0), "?")
        back = _DIR_OF.get((x0 - x1, y0 - y1), "?")
        # Both ends. A gate is recorded from whichever side somebody stood on, but a shut door is
        # shut from both — measured on 234's (10,8), refused from (10,9) going up and from (10,7)
        # going down. Honouring only the recorded direction makes connectivity *asymmetric*,
        # which is impossible for a flood fill and produced exactly that: 234 cells reachable
        # from one side of a 233 gate and 109 from the other, so `pockets` merged two places the
        # world keeps apart and `route_pockets` planned straight through the join. Over-blocking
        # costs a route we might have had; under-blocking costs a run, and has, repeatedly.
        if gates.get(f"{x0},{y0},{forward}") is not None or gates.get(f"{x1},{y1},{back}") is not None:
            return False
    tiles = m.get("tiles")
    if not tiles:
        return True
    return (m["tileset"], int(tiles[y0][2 * x0 : 2 * x0 + 2], 16), int(tiles[y1][2 * x1 : 2 * x1 + 2], 16)) not in pairs


def parse_map(rom: bytes, map_id: int) -> dict | None:
    """One map's truth: dims (walk tiles), connections, warps as (x, y, dest_map, dest_warp),
    sprites (NPC/trainer positions), grass tile presence, and the walkable grid ('01' row
    strings, ``grid[y][x]``). ``None`` for an id whose header is degenerate (unused slots)."""
    bank = rom[MAP_HEADER_BANKS + map_id]
    off = _faddr(bank, _u16(rom, MAP_HEADER_POINTERS + 2 * map_id))
    tileset, h_blocks, w_blocks = rom[off], rom[off + 1], rom[off + 2]
    if not (0 < w_blocks <= 0x80 and 0 < h_blocks <= 0x80):
        return None
    if tileset > MAX_TILESET:
        # An unused header slot, and it is not harmless. Map 231 parses with tileset 103 — every
        # one of this cartridge's 226 real maps uses 0-23 — 28x64 dimensions, and 113 "warps" of
        # which 110 sit outside its own edges pointing at arbitrary maps. `route` links a
        # LAST_MAP interior to every map that warps *into* it, so those phantoms made 231 a
        # wormhole joined to most of the world: the Safari-side maps read as five hops from
        # Saffron, through a map nothing in the game can enter.
        return None
    data = _faddr(bank, _u16(rom, off + 3))
    conns: dict[str, int] = {}
    p = off + 10
    for name, bit in _CONN_BITS:
        if rom[off + 9] & bit:
            conns[name] = rom[p]
            p += 11
    obj = _faddr(bank, _u16(rom, p))
    warps = []
    q = obj + 2
    for _ in range(rom[obj + 1]):
        wx, wy = rom[q + 1], rom[q]
        # A warp outside its own map is not a warp. Unused header slots parse into garbage that
        # is otherwise indistinguishable from data: map 231 claims tileset 103 (every real map
        # uses 0-23) and 110 of its 113 warps sit past its own edges, pointing at arbitrary map
        # ids. Because `route`'s LAST_MAP rule links an interior to every map that warps *into*
        # it, those 110 phantoms turned 231 into a wormhole hub joined to almost the whole world
        # — and the router planned straight through it, reporting the Safari-side maps as five
        # hops from Saffron. The bounds check is the whole fix.
        if 0 <= wx < 2 * w_blocks and 0 <= wy < 2 * h_blocks:
            warps.append((wx, wy, rom[q + 3], rom[q + 2]))  # stored y,x,dwarp,dmap
        q += 4
    # Signs: count byte then (y, x, text id) each. Coordinates only — sign TEXT is read live
    # (walk up, face it, press A): what the game says on screen is the instruction stream.
    signs = []
    n_signs = rom[q]
    q += 1
    for _ in range(n_signs):
        signs.append((rom[q + 1], rom[q]))  # stored y, x
        q += 3
    sprites = []
    n_sprites = rom[q]
    q += 1
    for _ in range(n_sprites):
        pic, y, x, _mv, _rng, text = rom[q], rom[q + 1] - 4, rom[q + 2] - 4, rom[q + 3], rom[q + 4], rom[q + 5]
        kind = "npc"
        q += 6
        sprite = {"kind": kind, "x": x, "y": y, "pic": pic}
        if text & 0x40:  # trainer: +2 bytes (class/pokemon set, level/roster id)
            sprite["kind"] = "trainer"
            q += 2
        elif text & 0x80:  # item ball: +1 byte, the item id it holds
            # That byte is what turns an item hunt into a lookup: a floor's balls carry their
            # contents in the cartridge, so "which ball holds the CARD KEY" is extraction, not
            # a sweep. Cross-checked against the ids the bag reported after live pickups.
            sprite["kind"] = "item"
            sprite["item"] = rom[q]
            q += 1
        sprites.append(sprite)
    te = TILESETS + 12 * tileset
    tbank = rom[te]
    blocks = _faddr(tbank, _u16(rom, te + 1))
    walk = _walkable_tiles(rom, _u16(rom, te + 5))
    grass = rom[te + 10]
    w, h = 2 * w_blocks, 2 * h_blocks
    grid, grass_tiles, tiles = [], [], []
    for y in range(h):
        row, trow = [], []
        for x in range(w):
            block = rom[data + (y // 2) * w_blocks + (x // 2)]
            tile = rom[blocks + block * 16 + ((y % 2) * 2 + 1) * 4 + (x % 2) * 2]
            row.append("1" if tile in walk else "0")
            trow.append(f"{tile:02x}")  # kept so ``passable`` can apply tile-pair collisions
            if tile == grass and grass != 0xFF:
                grass_tiles.append([x, y])
        grid.append("".join(row))
        tiles.append("".join(trow))
    return {
        "width": w,
        "height": h,
        "tileset": tileset,
        "connections": conns,
        "warps": [list(wp) for wp in warps],
        "signs": [list(s) for s in signs],
        "sprites": sprites,
        "grass": grass_tiles,
        "grid": grid,
        "tiles": tiles,
    }


# pokered's LedgeTiles (engine/overworld/ledges.asm): 4-byte records of (sprite facing,
# standing tile, ledge tile, joypad input), 0xFF-terminated, and consulted by the engine only
# on the OVERWORLD tileset (HandleLedges bails unless wCurMapTileset == 0). Standing on a
# `standing` tile and pressing `input` into an adjacent `ledge` tile hops the player TWO cells
# in that direction — a one-way edge the walkable grid cannot express. Route 4's east half is
# the proof: a plain BFS over its grid reads the road to Cerulean as disconnected (two "solid"
# columns and a seal at x=80), yet the real map crosses there — over the ledges
# (benchmarks/2026-08-25-router-cerulean.md). The table is found by structure, not address:
# every record's facing byte must agree with its input byte, and no other run of ROM bytes
# parses that way four records deep.
_LEDGE_FACING_TO_INPUT = {0x00: 0x80, 0x04: 0x40, 0x08: 0x20, 0x0C: 0x10}  # down, up, left, right
_LEDGE_INPUT_TO_DIR = {0x80: "down", 0x40: "up", 0x20: "left", 0x10: "right"}
LEDGE_DELTAS = {"down": (0, 1), "up": (0, -1), "left": (-1, 0), "right": (1, 0)}


def ledge_hops(rom: bytes) -> list[tuple[str, int, int]]:
    """``(direction, standing tile, ledge tile)`` triples from the ROM's LedgeTiles table."""
    best: list[tuple[str, int, int]] = []
    i, n = 0, len(rom)
    while i < n - 4:
        j, records = i, []
        while j + 4 <= n and rom[j] != 0xFF:
            facing, standing, ledge, joy = rom[j : j + 4]
            if _LEDGE_FACING_TO_INPUT.get(facing) != joy:
                break
            records.append((_LEDGE_INPUT_TO_DIR[joy], standing, ledge))
            j += 4
        if len(records) >= 4 and j < n and rom[j] == 0xFF:
            if len(records) > len(best):
                best = records
            i = j + 1  # a suffix of the table also parses; skipping past keeps the full one
        else:
            i += 1
    if not best:
        raise ValueError("LedgeTiles table not found in ROM")
    return best


def loaded_ledges(truth: dict) -> set[tuple[str, int, int]]:
    """The ledge-hop set as stored in an extracted truth file (empty for a pre-ledge file)."""
    return {(d, s, le) for d, s, le in truth.get("ledges", ())}


# Gen 1 type constants (pokered): the hand-typed copy of this map once swapped grass<->electric
# and psychic<->ice, mis-typing battle scoring for months — hence extraction over recall here too.
TYPE_NAMES = {
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
    0x16: "grass",
    0x17: "electric",
    0x18: "psychic",
    0x19: "ice",
    0x1A: "dragon",
}
_NAME_CHARS = {0xE1: "Pk", 0xE2: "Mn", 0xEF: "M", 0xF5: "F", 0xE8: "."}


HM_FIRST, HM_COUNT = 196, 5  # ids 196..200 render as HM01..HM05 on the game's own ITEM screen
TM_BASE, TM_COUNT = 200, 50  # id 200+n renders as TM<n>: 207->TM07, 234->TM34, six such readings

_ITEM_CHARS = {0x7F: " ", 0xBA: "e", 0xE1: "Pk", 0xE2: "Mn", 0xE3: "-", 0xE6: "?", 0xE7: "!", 0xE8: "."}
for _i in range(10):
    _ITEM_CHARS[0xF6 + _i] = chr(ord("0") + _i)


def item_names(rom: bytes, count: int = 250) -> dict[str, str]:
    """Item id -> name, from the ROM's own 0x50-terminated name list.

    Located by content signature (the list opens with MASTER BALL), never by address — the same
    rule the species and type tables follow. This is what lets a run say what it is *holding*:
    the bag is a list of numeric ids, and every previous session that reasoned about those ids
    did it from recall. The extraction independently reproduces the three that were measured
    live in the Rocket Hideout — 72 SILPH SCOPE, 73 POKE FLUTE, 74 LIFT KEY — which is the
    cross-check that says the decode is right.

    Ids past the plain-item list are TMs and HMs, which the ROM stores as a numbered range
    rather than as names; those come back as ``TM<n>``/``HM<n>``.
    """
    base = rom.find(bytes(0x80 + ord(c) - ord("A") if c != " " else 0x7F for c in "MASTER BALL"))
    if base < 0:
        return {}  # a synthetic/partial image has no item table; the real-ROM smoke test asserts ours does
    out: dict[str, str] = {}
    off = base
    for iid in range(1, count + 1):
        name = ""
        while off < len(rom) and rom[off] != 0x50:
            byte = rom[off]
            if 0x80 <= byte <= 0x99:
                name += chr(ord("A") + byte - 0x80)
            else:
                name += _ITEM_CHARS.get(byte, "")
            off += 1
        off += 1
        if not name.strip():
            break
        out[str(iid)] = name.strip()
    # Past the plain-item list the ROM stores TMs and HMs as a numbered range rather than as
    # names. The boundaries below were read off the game's own ITEM screen, one entry at a time,
    # for a bag holding ids 196/207/210/211/221/224/228/234 — which rendered as HM01, TM07, TM10,
    # TM11, TM21, TM24, TM28, TM34. Seven data points, so the offset is measured, not assumed.
    for iid in range(HM_FIRST, HM_FIRST + HM_COUNT):
        out[str(iid)] = f"HM{iid - HM_FIRST + 1:02d}"
    for iid in range(TM_BASE + 1, TM_BASE + TM_COUNT + 1):
        out[str(iid)] = f"TM{iid - TM_BASE:02d}"
    return out


def move_names(rom: bytes, count: int = 165) -> dict[str, str]:
    """Move id -> name, from the ROM's own 0x50-terminated list (found by POUND at id 1)."""
    base = rom.find(bytes(0x80 + ord(c) - ord("A") for c in "POUND"))
    if base < 0:
        return {}
    out, off = {}, base
    for mid in range(1, count + 1):
        name, i = "", off
        while i < len(rom) and rom[i] != 0x50:
            byte = rom[i]
            name += chr(ord("A") + byte - 0x80) if 0x80 <= byte <= 0x99 else _ITEM_CHARS.get(byte, "")
            i += 1
        if not name.strip():
            break
        out[str(mid)] = name.strip()
        off = i + 1
    return out


def move_table(rom: bytes, count: int = 165) -> dict[str, dict]:
    """Move id -> {name, type, power, accuracy, pp}, from the ROM's own 6-byte-per-move table.

    Located by content signature: the table opens with POUND (id 1: effect 0, power 40, NORMAL,
    accuracy 255/255, 35 PP) followed by KARATE CHOP (id 2: power 50, 25 PP). Names come from
    :func:`move_names`; the type byte is decoded with the same TYPE_NAMES the species table uses;
    accuracy is the ROM's /255 byte rendered as a percentage. The measured reason this exists: the
    agent carried an 18-entry hand-typed move table whose entry 0x3F said "Flamethrower" -- on this
    cartridge 0x3F is HYPER BEAM, and the fight against Giovanni's Rhydon chose it over Surf.
    """
    base = rom.find(bytes([1, 0, 40, 0, 255, 35, 2, 0, 50, 0, 255, 25]))
    if base < 0:
        return {}
    names = move_names(rom, count)
    out: dict[str, dict] = {}
    for mid in range(1, count + 1):
        e = base + 6 * (mid - 1)
        if e + 6 > len(rom) or rom[e] != mid:
            break
        out[str(mid)] = {
            "name": names.get(str(mid), f"#{mid:02X}"),
            "type": TYPE_NAMES.get(rom[e + 3], "?"),
            "power": rom[e + 2],
            "accuracy": round(rom[e + 4] * 100 / 255),
            "pp": rom[e + 5],
        }
    return out


def machine_moves(rom: bytes) -> dict[str, str]:
    """``"HM03" -> "SURF"`` — what each TM and HM actually teaches.

    An item's name does not say what it is. The cartridge stores machines as a numbered range with
    no text, so ``item_names`` generates "HM03" and nothing anywhere says *Surf* — which is how a
    whole session can be spent hunting an item nobody can name. The mapping is in the ROM: fifty
    TM move ids followed by the five HM ids.

    Located by content signature, never by address: the five HM entries are exactly the field
    moves CUT, FLY, SURF, STRENGTH and FLASH, so the run of their ids (15, 19, 57, 70, 148) with
    fifty valid move ids before it identifies the table, and no other run of ROM bytes does.
    """
    moves = move_names(rom)
    if not moves:
        return {}
    field = [
        next((int(i) for i, n in moves.items() if n == want), 0) for want in ("CUT", "FLY", "SURF", "STRENGTH", "FLASH")
    ]
    if not all(field):
        return {}
    marker = bytes(field)
    off = 0
    while True:
        hit = rom.find(marker, off)
        if hit < 0:
            return {}
        table = rom[hit - 50 : hit]
        if len(table) == 50 and all(1 <= b <= len(moves) for b in table):
            break
        off = hit + 1
    out = {f"TM{n:02d}": moves.get(str(table[n - 1]), "?") for n in range(1, 51)}
    out.update({f"HM{n:02d}": moves[str(field[n - 1])] for n in range(1, 6)})
    return out


def species_table(rom: bytes) -> dict[str, dict]:
    """Internal species id -> {name, dex, types, catch_rate}, from the ROM's own three tables:
    the name table (10 bytes/entry, internal order — found by RHYDON at id 1), the internal->dex
    order table, and the dex-order base stats (28 bytes/entry: types at +6/+7, catch rate at +8).
    All located by content signature, never by address. MISSINGNO slots are skipped; a dex entry
    past the stats table (Mew, stored separately in Red) keeps name/dex with empty types."""

    def enc(s: str) -> bytes:
        return bytes(0x80 + ord(c) - ord("A") for c in s)

    names_base = rom.find(enc("RHYDON"))
    dex_base = rom.find(bytes([112, 115, 32, 35, 21, 100, 34, 80, 2]))
    stats_base = rom.find(bytes([1, 45, 49, 49, 45, 65, 0x16, 0x03, 45, 64]))
    if min(names_base, dex_base, stats_base) < 0:
        raise ValueError("species tables not found in ROM")
    out: dict[str, dict] = {}
    for iid in range(1, 191):
        raw = rom[names_base + 10 * (iid - 1) : names_base + 10 * iid]
        name = ""
        for b in raw:
            if 0x80 <= b <= 0x99:
                name += chr(ord("A") + b - 0x80)
            elif b in _NAME_CHARS:
                name += _NAME_CHARS[b]
            elif b == 0x50:
                break
        if not name or "MISSINGNO" in name:
            continue
        name = {"NIDORANM": "NidoranM", "NIDORANF": "NidoranF"}.get(name, name.title())
        dex = rom[dex_base + iid - 1]
        entry = {"name": name, "dex": dex, "types": [], "catch_rate": None}
        if 1 <= dex <= 150:  # Mew (151) lives outside the table in Red
            e = stats_base + (dex - 1) * 28
            t1, t2 = TYPE_NAMES.get(rom[e + 6], "?"), TYPE_NAMES.get(rom[e + 7], "?")
            entry["types"] = [t1] if t2 == t1 else [t1, t2]
            entry["catch_rate"] = rom[e + 8]
        out[str(iid)] = entry
    return out


def wild_encounters(rom: bytes, species_ids: set[int]) -> dict[str, dict]:
    """Per-map wild pools from the ROM's own encounter tables — the answer to "what spawns
    where" that neither sampled telemetry nor recalled game lore can be trusted for (versions
    differ; recall hallucinates — the species-id and type-map bugs both came from recall).

    Format on cartridge: a per-map pointer table into one bank; each block is a grass rate byte
    (0 = none, else 10 (level, species) pairs follow) then the same for water. The table is
    located by STRUCTURE, never by address: the only run of 248 in-window pointers whose blocks
    all parse with plausible rates, levels, and known species ids. Returns
    ``{map_id: {"grass_rate": r, "grass": [[level, species], ...], "water_rate": r, "water": [...]}}``
    for maps with any encounters."""

    def parse_block(a: int):
        out = {}
        for kind in ("grass", "water"):
            if a >= len(rom):
                return None
            rate = rom[a]
            a += 1
            out[kind + "_rate"] = rate
            pairs = []
            if rate:
                if rate > 100 or a + 20 > len(rom):
                    return None
                for k in range(10):
                    lv, sp = rom[a + 2 * k], rom[a + 2 * k + 1]
                    if not (1 <= lv <= 80) or sp not in species_ids:
                        return None
                    pairs.append([lv, sp])
                a += 20
            out[kind] = pairs
        return out

    def try_base(base: int, bank: int):
        lo = bank * 0x4000
        blocks = {}
        for mid in range(NUM_MAPS):
            # base is bounded to whole banks, so off + 1 is always in range
            off = base + 2 * mid
            p = rom[off] | (rom[off + 1] << 8)
            if not (0x4000 <= p < 0x8000):
                return None
            parsed = parse_block(lo + (p - 0x4000))
            if parsed is None:
                return None
            if parsed["grass_rate"] or parsed["water_rate"]:
                blocks[str(mid)] = parsed
        return blocks if len(blocks) >= 20 else None  # a real overworld has dozens of wild maps

    for bank in range(len(rom) // 0x4000):
        for base in range(bank * 0x4000, (bank + 1) * 0x4000 - 2 * NUM_MAPS):
            found = try_base(base, bank)
            if found:
                return found
    raise ValueError("wild encounter tables not found in ROM")


def evolutions_table(rom: bytes, species_count: int = 190) -> dict[str, dict]:
    """Per-internal-id evolutions and level-up learnsets, located by content signature.

    pokered's EvosMovesPointerTable: one same-bank 2-byte pointer per internal species id;
    each target holds evolution entries — (1, level, species) for level, (2, item, 1, species)
    for stone, (3, level, species) for trade — then a (level, move) learnset, both
    0-terminated. Found by scanning every bank for the run of ``species_count`` in-bank
    pointers whose targets all parse to that shape, never by remembered address. Validated
    live the day it was written: Charmeleon level-36 -> Charizard (the evolution the B button
    had been cancelling), and Drowzee's learnset put POISON GAS at the exact level the live
    learn prompt fired (29)."""

    def parse_block(o: int) -> tuple[list, list] | None:
        evos: list = []
        while o < len(rom) and rom[o] != 0:
            m = rom[o]
            if m == 1 and o + 2 < len(rom):
                evos.append(["level", rom[o + 1], rom[o + 2]])
                o += 3
            elif m == 2 and o + 3 < len(rom):
                evos.append(["item", rom[o + 1], rom[o + 3]])
                o += 4
            elif m == 3 and o + 2 < len(rom):
                evos.append(["trade", rom[o + 1], rom[o + 2]])
                o += 3
            else:
                return None
            if len(evos) > 3:
                return None
        o += 1
        learn: list = []
        while o + 1 < len(rom) and rom[o] != 0:
            lvl, mv = rom[o], rom[o + 1]
            if not (1 <= lvl <= 100 and 1 <= mv <= 200):
                return None
            learn.append([lvl, mv])
            o += 2
            if len(learn) > 25:
                return None
        return evos, learn

    for bank in range(len(rom) // 0x4000):
        base = bank * 0x4000
        for off in range(base, base + 0x4000 - 2 * species_count):
            ok = True
            for i in range(8):
                p = rom[off + 2 * i] | (rom[off + 2 * i + 1] << 8)
                if not (0x4000 <= p < 0x8000):
                    ok = False
                    break
            if not ok:
                continue
            entries: dict[str, dict] = {}
            for i in range(species_count):
                p = rom[off + 2 * i] | (rom[off + 2 * i + 1] << 8)
                parsed = None if not (0x4000 <= p < 0x8000) else parse_block(base + p - 0x4000)
                if parsed is None:
                    break
                entries[str(i + 1)] = {"evolutions": parsed[0], "learnset": parsed[1]}
            if len(entries) == species_count:
                # The ascending scan reaches the true table start first: windows shifted into
                # the pointer run lose their tail to block data and score short.
                return entries
    raise ValueError("evolutions table not found in ROM")


def type_chart(rom: bytes) -> dict[str, dict[str, float]]:
    """The game's own TypeEffects table, located by content signature.

    Triples (attacker, defender, multiplier*10) terminated by 0xFF; multipliers are only
    0x00/0x05/0x14/0x20 and type ids stay small. The hand-typed chart this replaces had
    grass<->electric and psychic<->ice swapped for months — extraction, never recall.
    Pairs absent from the table are neutral (1.0)."""
    best = None
    off = 0
    while off < len(rom) - 4:
        n = 0
        j = off
        zeros = 0
        while j + 3 < len(rom) and n < 200:
            a, d, e = rom[j], rom[j + 1], rom[j + 2]
            if a > 0x1A or d > 0x1A or e not in (0x00, 0x05, 0x0A, 0x14):
                break
            if a == 0 and d == 0 and e == 0:
                zeros += 1
                if zeros > 3:
                    break
            n += 1
            j += 3
        if 30 <= n < 200 and zeros <= 3 and j < len(rom) and rom[j] == 0xFF:
            if best is None or n > best[1]:
                best = (off, n)
        off = max(off + 1, j - 2) if n > 20 else off + 1
    if best is None:
        raise ValueError("type-effectiveness table not found in ROM")
    off, n = best
    chart: dict[str, dict[str, float]] = {}
    for i in range(n):
        a, d, e = rom[off + 3 * i], rom[off + 3 * i + 1], rom[off + 3 * i + 2]
        chart.setdefault(TYPE_NAMES.get(a, f"#{a:02X}"), {})[TYPE_NAMES.get(d, f"#{d:02X}")] = e / 10
    return chart


def parse_rom(path: Path = ROM_DEFAULT, map_ids: list[int] | None = None) -> dict:
    rom = path.read_bytes()
    maps = {}
    for mid in map_ids if map_ids is not None else range(NUM_MAPS):
        m = parse_map(rom, mid)
        if m is not None:
            maps[str(mid)] = m
    species = species_table(rom)
    return {
        "rom_sha256": hashlib.sha256(rom).hexdigest(),
        "tile_pairs": [list(t) for t in sorted(tile_pairs(rom))],
        "ledges": [list(t) for t in ledge_hops(rom)],
        "species": species,
        "wilds": wild_encounters(rom, {int(k) for k in species}),
        "evolutions": evolutions_table(rom),
        "type_chart": type_chart(rom),
        "items": item_names(rom),
        "machines": machine_moves(rom),
        "moves": move_table(rom),
        "maps": maps,
    }


def attach_measured_gates(truth: dict, path: Path | None = None) -> dict:
    """Hang each map's measured gates on its own dict, where ``passable`` will find them.

    Only the *doors* are hung. The survey records every refusal it meets, which is right — the
    sentence is evidence and the file keeps it — but a refusal from a body that was standing
    there is not a fact about the map, and honouring it walls off ground that is open the moment
    the sprite wanders on. See ``is_door_text``.
    """
    for map_id, entries in load_measured_gates(path).items():
        if map_id in truth.get("maps", {}):
            truth["maps"][map_id]["gates"] = door_gates(entries)
    for map_id, tiles in load_dead_warps().items():
        if map_id in truth.get("maps", {}):
            truth["maps"][map_id]["dead_warps"] = tiles
    return truth


def load_truth(path: Path = TRUTH_DEFAULT, rom_path: Path | None = None) -> dict:
    """Load an extracted truth file; if the ROM is present, refuse a sha mismatch (a grid from a
    different image misroutes silently — the spec's number-one risk)."""
    truth = json.loads(path.read_text())
    if rom_path is not None and rom_path.exists():
        sha = hashlib.sha256(rom_path.read_bytes()).hexdigest()
        if sha != truth.get("rom_sha256"):
            raise ValueError(f"rom_truth.json was extracted from a different ROM (sha {truth.get('rom_sha256')[:12]}…)")
    return attach_measured_gates(truth)


def pockets(truth: dict, map_id: int) -> list[set[tuple[int, int]]]:
    """A map's walkable regions once the measured gates are honoured.

    The engine's unit of place is the map, and inside a gated building that is one level too
    coarse: with 36 card-key doors closed, "map 209" names five disconnected places, and every
    primitive keyed on a map id — routing, banning, batons, goals — is quietly talking about the
    wrong thing. A pocket is the honest unit.
    """
    from collections import deque

    m = truth["maps"].get(str(map_id))
    if not m:
        return []
    pairs = loaded_pairs(truth)
    w, h = m["width"], m["height"]
    open_cells = {(x, y) for y in range(h) for x in range(w) if m["grid"][y][x] == "1"}
    out: list[set[tuple[int, int]]] = []
    while open_cells:
        start = min(open_cells)
        seen, queue = {start}, deque([start])
        while queue:
            x, y = queue.popleft()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in open_cells and (nx, ny) not in seen and passable(m, pairs, x, y, nx, ny):
                    seen.add((nx, ny))
                    queue.append((nx, ny))
        out.append(seen)
        open_cells -= seen
    return out


def pocket_of(truth: dict, map_id: int, cell) -> int | None:
    """Which pocket index a cell belongs to — the place-id the map id was standing in for."""
    for i, pocket in enumerate(pockets(truth, map_id)):
        if tuple(cell) in pocket:
            return i
    return None


def pocket_exits(truth: dict, map_id: int, index: int) -> list[dict]:
    """Every warp reachable *from this pocket*, resolved to where it actually lands.

    A warp's destination is a map in the extraction and a *pocket* in the world. Resolving that
    is what turns 51 loose landings into a graph you can walk.
    """
    pocket = pockets(truth, map_id)
    if index >= len(pocket):
        return []
    cells = pocket[index]
    out = []
    dead = truth["maps"][str(map_id)].get("dead_warps", {})
    for wx, wy, dst, dwarp in truth["maps"][str(map_id)]["warps"]:
        if (wx, wy) not in cells or dst == LAST_MAP or str(dst) not in truth["maps"]:
            continue
        if f"{wx},{wy}" in dead:
            continue  # measured: this door does not open, whatever the warp table says
        dwarps = truth["maps"][str(dst)]["warps"]
        land = (dwarps[dwarp][0], dwarps[dwarp][1]) if dwarp < len(dwarps) else None
        out.append(
            {
                "from": [wx, wy],
                "to_map": dst,
                "land": list(land) if land else None,
                "to_pocket": pocket_of(truth, dst, land) if land else None,
            }
        )
    return out


def route_pockets(truth: dict, src: tuple[int, int], dst: tuple[int, int]) -> list[dict] | None:
    """A hop chain between *pockets*, which is the unit a gated building is actually made of.

    ``route`` plans over map ids. Inside Silph that is the wrong granularity and it is why a
    whole session failed to find Giovanni: with the card-key doors closed the eleven floors are
    twenty-eight pockets, and the eleventh floor's boss sits in one that map-level routing has no
    way to distinguish from the pocket the lift drops you in.
    """
    from collections import deque

    if src == dst:
        return []
    seen, parents, queue = {src}, {}, deque([src])
    while queue:
        node = queue.popleft()
        for exit_ in pocket_exits(truth, node[0], node[1]):
            if exit_["to_pocket"] is None:
                continue
            nxt = (exit_["to_map"], exit_["to_pocket"])
            if nxt in seen:
                continue
            seen.add(nxt)
            parents[nxt] = (node, exit_)
            if nxt == dst:
                chain = []
                cur = dst
                while cur != src:
                    prev, hop = parents[cur]
                    chain.append({"from_map": prev[0], "from_pocket": prev[1], **hop})
                    cur = prev
                return list(reversed(chain))
            queue.append(nxt)
    return None


def route(truth: dict, src: int, dst: int, banned: set[tuple[int, int]] | None = None) -> list[dict] | None:
    """BFS over the map graph: warps (LAST_MAP mats are the return leg of the warp that entered,
    so only forward, non-LAST_MAP warps make edges) plus edge connections. Returns the hop list
    — each hop names the mechanism and the tile to use — or ``None`` if unreachable.

    ``banned`` drops ``(from, to)`` hops from the graph. The connection table is undirected but
    the world is not: Cycling Road (map 28, 20x144) carries only *down* ledges — the extracted
    ledge set has no upward hop anywhere in the cartridge — so the chain
    ``7 -> 29 -> 28 -> 27 -> 6`` is a perfectly good graph path that a player cannot walk north.
    A caller that measures a hop as structurally impossible bans it and asks again, rather than
    re-attempting a chain the world has already refused.
    """
    maps = truth["maps"]
    banned = banned or set()
    if str(src) not in maps or str(dst) not in maps:
        return None
    frontier, seen, parents = [src], {src}, {}
    while frontier:
        nxt = []
        for m in frontier:
            hops = []
            for x, y, dmap, dwarp in maps[str(m)]["warps"]:
                if dmap != LAST_MAP and str(dmap) in maps and (m, dmap) not in banned:
                    hops.append((dmap, {"from": m, "to": dmap, "via": "warp", "x": x, "y": y, "dest_warp": dwarp}))
            for edge, dmap in maps[str(m)]["connections"].items():
                if str(dmap) in maps and (m, dmap) not in banned:
                    hops.append((dmap, {"from": m, "to": dmap, "via": "edge", "edge": edge}))
            # A LAST_MAP mat is usable as "back out the way in": link to every map holding a warp
            # into this one (single-door interiors: the Center, the gym, gate rooms).
            if any(w[2] == LAST_MAP for w in maps[str(m)]["warps"]):
                for other, om in maps.items():
                    for x, y, dmap, dwarp in om["warps"]:
                        if dmap == m and (m, int(other)) not in banned:
                            mat = maps[str(m)]["warps"][0]
                            hops.append(
                                (int(other), {"from": m, "to": int(other), "via": "mat", "x": mat[0], "y": mat[1]})
                            )
            for dmap, hop in hops:
                if dmap not in seen:
                    seen.add(dmap)
                    parents[dmap] = hop
                    nxt.append(dmap)
            if m == dst:
                nxt = []
                break
        if dst in seen:
            break
        frontier = nxt
    if dst not in seen and src != dst:
        return None
    chain: list[dict] = []
    cur = dst
    while cur != src:
        hop = parents[cur]
        chain.append(hop)
        cur = hop["from"]
    return list(reversed(chain))


def describe_route(chain: list[dict]) -> str:
    parts = []
    for hop in chain:
        if hop["via"] == "edge":
            parts.append(f"{hop['from']} --{hop['edge']} edge--> {hop['to']}")
        elif hop["via"] == "mat":
            parts.append(f"{hop['from']} --door mat ({hop['x']},{hop['y']})--> {hop['to']}")
        else:
            parts.append(f"{hop['from']} --warp ({hop['x']},{hop['y']})--> {hop['to']}")
    return "\n".join(parts)


def loaded_pairs(truth: dict) -> set[tuple[int, int, int]]:
    """The tile-pair collision set as stored in an extracted truth file (``tile_pairs`` parses the
    ROM; this reads the already-parsed list, which is what every consumer of the JSON has)."""
    return {tuple(t) for t in truth.get("tile_pairs", ())}


def exit_targets(truth: dict, hop: dict) -> set[tuple[int, int]]:
    """The tiles on ``hop['from']`` that carry you to ``hop['to']``.

    A warp/mat hop is a single mat tile. An *edge* hop has no tile in any table — the engine hands
    the player to the neighbour when they step off that side — so the target is every walkable cell
    on that edge. Route 3's north edge (x 57..63 of 70) is the only way to Route 4, and it is why a
    blind east march can never leave the map: its east edge is solid for all 18 rows.
    """
    m = truth["maps"][str(hop["from"])]
    if hop["via"] in ("warp", "mat"):
        return {(hop["x"], hop["y"])}
    grid, w, h = m["grid"], m["width"], m["height"]
    edge = hop.get("edge")
    if edge == "north":
        return {(x, 0) for x in range(w) if grid[0][x] == "1"}
    if edge == "south":
        return {(x, h - 1) for x in range(w) if grid[h - 1][x] == "1"}
    if edge == "west":
        return {(0, y) for y in range(h) if grid[y][0] == "1"}
    if edge == "east":
        return {(w - 1, y) for y in range(h) if grid[y][w - 1] == "1"}
    return set()


def sprite_tiles(truth: dict, map_id: int) -> set[tuple[int, int]]:
    """Tiles held by NPCs/trainers on ``map_id``.

    The collision grid says walkable — the ROM's object data puts a body there. A *defeated* Gen 1
    trainer keeps standing on its tile, so a route planned through one is blocked forever, not
    merely until the battle ends. Route 3 has nine, and the 08-21 lanes' first truth path ran
    straight through the one at (19,5)."""
    m = truth["maps"].get(str(map_id))
    if m is None:
        return set()
    return {(s["x"], s["y"]) for s in m.get("sprites", ())}


def path_on_map(
    truth: dict,
    pairs: set[tuple[int, int, int]],
    map_id: int,
    start: tuple[int, int],
    targets: set[tuple[int, int]],
    blocked: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]] | None:
    """Shortest tile path from ``start`` to the nearest of ``targets``, or ``None``.

    BFS over :func:`passable`, so it honours tile-pair collisions as well as the walkable grid —
    plus the ROM's one-way LEDGE hops on the overworld tileset (a two-cell jump over a tile the
    grid calls solid; without them Route 4's east road reads as disconnected).
    ``blocked`` adds tiles the grid calls walkable but a caller knows are not (sprites, and cells a
    lane has learned are solid). ``start`` is never treated as blocked — a lane standing on a tile
    that later reads blocked must still be able to route out of it.
    Returns the full path including ``start``; ``[start]`` when already on a target.
    """
    m = truth["maps"].get(str(map_id))
    if m is None or not targets:
        return None
    if start in targets:
        return [start]
    blocked = (blocked or set()) - {start}
    targets = targets - blocked
    if not targets:
        return None
    tiles = m.get("tiles")
    ledges = loaded_ledges(truth) if m.get("tileset") == 0 and tiles else set()
    # A door is a warp tile the grid calls solid (the Elite Four rooms' (4,0)/(5,0), measured):
    # as the TARGET it is enterable from a walkable neighbour, the way the engine enters it.
    door_targets = {(wp[0], wp[1]) for wp in m.get("warps", [])} & targets

    def _tile(tx: int, ty: int) -> int:
        return int(tiles[ty][2 * tx : 2 * tx + 2], 16)

    prev: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = [start]
    while queue:
        nxt = []
        for x, y in queue:
            steps = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
            hops = []
            if ledges:
                # A hop lands two cells out; the mid tile is the ledge itself (solid in the
                # grid, jumped over by the engine), so only the LANDING cell must be open.
                t0 = _tile(x, y)
                for d, (dx, dy) in LEDGE_DELTAS.items():
                    mx, my, lx, ly = x + dx, y + dy, x + 2 * dx, y + 2 * dy
                    if not (0 <= lx < m["width"] and 0 <= ly < m["height"]):
                        continue
                    if (d, t0, _tile(mx, my)) in ledges and m["grid"][ly][lx] == "1" and (mx, my) not in blocked:
                        hops.append((lx, ly))
            for step in steps + hops:
                if step in prev or step in blocked:
                    continue
                if step not in hops and not passable(m, pairs, x, y, *step):
                    if not (step in door_targets and m["grid"][y][x] == "1"):
                        continue
                prev[step] = (x, y)
                if step in targets:
                    path = [step]
                    while path[-1] is not None:
                        path.append(prev[path[-1]])
                    return list(reversed(path[:-1]))
                nxt.append(step)
        queue = nxt
    return None


def seed_worldmap(truth: dict, map_ids: list[int]) -> WorldMap:
    """A WorldMap with the listed maps fully known (grid + bounds), ready for
    ``relay.py --seed-worldmap``. Sprites are stamped as hard-blocked tiles — the collision grid
    says walkable, but an NPC stands there; the existing expiry machinery re-tests them."""
    wm = WorldMap()
    for mid in map_ids:
        m = truth["maps"][str(mid)]
        cells = wm.cells.setdefault(mid, {})
        for y, row in enumerate(m["grid"]):
            for x, ch in enumerate(row):
                cells[(x, y)] = 1 if ch == "1" else 0
        wm.bounds[mid] = (m["width"], m["height"])
        for s in m["sprites"]:
            if 0 <= s["x"] < m["width"] and 0 <= s["y"] < m["height"]:
                wm.block(mid, s["x"], s["y"])
        for gx, gy in m["grass"]:
            wm.mark_encounter(mid, gx, gy)
    return wm


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="parse the ROM into references/rom_truth.json")
    ex.add_argument("--rom", type=Path, default=ROM_DEFAULT)
    ex.add_argument("--out", type=Path, default=TRUTH_DEFAULT)
    rt = sub.add_parser("route", help="map-level hop chain from A to B")
    rt.add_argument("src", type=int)
    rt.add_argument("dst", type=int)
    rt.add_argument("--truth", type=Path, default=TRUTH_DEFAULT)
    sw = sub.add_parser("seed-worldmap", help="write a WorldMap snapshot for relay.py --seed-worldmap")
    sw.add_argument("maps", type=int, nargs="+")
    sw.add_argument("--out", type=Path, required=True)
    sw.add_argument("--truth", type=Path, default=TRUTH_DEFAULT)
    args = ap.parse_args(argv)
    if args.cmd == "extract":
        truth = parse_rom(args.rom)
        # The battle strategy reads references/type_chart.json — keep it in lockstep with
        # the extraction so no hand-typed chart can drift back in.
        (args.out.parent / "type_chart.json").write_text(json.dumps(truth["type_chart"], indent=2))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(truth))
        print(f"{len(truth['maps'])} maps -> {args.out} (rom sha {truth['rom_sha256'][:12]}…)")
        return 0
    truth = load_truth(args.truth)
    if args.cmd == "route":
        chain = route(truth, args.src, args.dst)
        if chain is None:
            print(f"no route {args.src} -> {args.dst}")
            return 1
        print(describe_route(chain))
        return 0
    wm = seed_worldmap(truth, args.maps)
    wm.save(args.out)
    print(f"seeded maps {args.maps} -> {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
