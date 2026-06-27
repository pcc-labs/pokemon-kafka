"""Oak's Parcel quest: the scripted early-game gate that pure navigation can't pass.

In Pokémon Red the north exit of Viridian City (toward Route 2 → Viridian Forest → Pewter) is
blocked by the Old Man until you run Oak's errand: pick up OAK'S PARCEL at the Viridian Mart, carry
it back to Prof. Oak in Pallet, and receive the Pokédex. Only then does the Old Man step aside.

This module is a *pure* state machine. Given the current signals (map id, whether the parcel is in
the bag, whether the Pokédex has been received) it reports the quest phase and the navigation
override the agent should pursue this turn — a ``special_target`` dict in the exact shape the
``Navigator`` already consumes (``{name, target:(x,y), axis, at_target}``). The agent feeds the
target to the navigator and relies on its existing text-box mashing to clear the resulting
dialogue. No emulator access here, so the routing is fully unit-tested; tile coordinates live in
one table (`QUEST_TARGETS`) and are verified against map-transition telemetry on the first run.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Map ids (pokered Kanto). OAKS_LAB / VIRIDIAN_CITY / PALLET / ROUTE_1 confirmed in agent.py;
# VIRIDIAN_MART is the documented constant, verified via map-transition logging on the first run.
PALLET_TOWN = 0
VIRIDIAN_CITY = 1
ROUTE_1 = 12
ROUTE_2 = 13
OAKS_LAB = 40
VIRIDIAN_MART = 42

# Maps the quest actively steers through. On any other map it returns no override and lets the
# normal waypoint navigation (which already covers Route 2 / Forest / Pewter) take over.
QUEST_MAPS = frozenset({PALLET_TOWN, VIRIDIAN_CITY, ROUTE_1, OAKS_LAB, VIRIDIAN_MART})

# --- Phases ---
TO_MART = "TO_MART"  # no parcel yet → go to the Viridian Mart and pick it up
TO_OAK = "TO_OAK"  # parcel in bag → carry it back to Oak in Pallet
GO_NORTH = "GO_NORTH"  # Pokédex received → head north past the (now-cleared) Old Man
DONE = "DONE"  # quest satisfied and already north of Viridian → hand back to normal nav

# Tile targets that need fixed coordinates (the building interiors / doors). Confirmed against
# pokered map data + first-run telemetry; ``_north``/`_south` edge-pushes need no fixed tile.
MART_DOOR = (29, 19)  # Viridian City: tile just below the Mart entrance (verify on run)
MART_COUNTER = (3, 3)  # Viridian Mart: in front of the clerk's counter (verify on run)
OAKS_LAB_DOOR = (12, 11)  # Pallet Town: tile below Oak's Lab entrance (verify on run)
OAK_TILE = (5, 2)  # Oak's Lab: in front of Prof. Oak (verify on run)


@dataclass(frozen=True)
class QuestSignals:
    """Everything the quest needs to decide what to do this turn."""

    map_id: int
    x: int
    y: int
    has_parcel: bool
    has_pokedex: bool


def quest_phase(sig: QuestSignals) -> str:
    """Which leg of the errand we are on, purely from the signals."""
    if sig.has_pokedex:
        # Delivered. Keep steering (north / out of buildings) while still in the early loop;
        # done once we're past Viridian on a map the normal waypoints already cover.
        return GO_NORTH if sig.map_id in QUEST_MAPS else DONE
    if sig.has_parcel:
        return TO_OAK
    return TO_MART


def _north(sig: QuestSignals) -> dict:
    """Push straight up to the map's north edge (triggers the northward map transition)."""
    return {"name": "north", "target": (sig.x, 0), "axis": "y", "at_target": "up"}


def _south(sig: QuestSignals) -> dict:
    """Push straight down to the map's south edge (triggers the southward map transition)."""
    return {"name": "south", "target": (sig.x, 36), "axis": "y", "at_target": "down"}


def _to(coord: tuple[int, int], name: str, at_target: str = "up") -> dict:
    return {"name": name, "target": coord, "axis": "x", "at_target": at_target}


class ParcelQuest:
    """Holds the last-computed phase (for logging) and maps signals → a nav override."""

    def __init__(self) -> None:
        self.phase: str = TO_MART

    def next_target(self, sig: QuestSignals) -> dict | None:
        """The ``special_target`` the agent should pursue this turn, or ``None`` to defer to the
        normal navigator. Pure: depends only on ``sig``."""
        phase = quest_phase(sig)
        self.phase = phase

        if phase == TO_MART:
            if sig.map_id in (ROUTE_1, PALLET_TOWN):
                return _north(sig)  # head up toward Viridian
            if sig.map_id == VIRIDIAN_CITY:
                return _to(MART_DOOR, "Viridian Mart door")
            if sig.map_id == VIRIDIAN_MART:
                return _to(MART_COUNTER, "Mart counter (parcel)", at_target="a")
            return None

        if phase == TO_OAK:
            if sig.map_id == VIRIDIAN_MART:
                return _south(sig)  # leave the Mart first
            if sig.map_id in (VIRIDIAN_CITY, ROUTE_1):
                return _south(sig)  # head back down toward Pallet
            if sig.map_id == PALLET_TOWN:
                return _to(OAKS_LAB_DOOR, "Oak's Lab door")
            if sig.map_id == OAKS_LAB:
                return _to(OAK_TILE, "Prof. Oak (deliver)", at_target="a")
            return None

        if phase == GO_NORTH:
            # Pokédex in hand: exit any building we're in, then push north through
            # Pallet → Route 1 → Viridian → (the now-cleared Old Man).
            if sig.map_id in (OAKS_LAB, VIRIDIAN_MART):
                return _south(sig)  # walk out the door
            return _north(sig)

        return None  # DONE — normal waypoints handle Route 2 / Forest / Pewter

    def describe(self, sig: QuestSignals) -> str:
        """One-line status for telemetry/logging."""
        return (
            f"parcel_quest phase={quest_phase(sig)} map={sig.map_id} parcel={sig.has_parcel} pokedex={sig.has_pokedex}"
        )
