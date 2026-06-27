"""Tests for the pure Oak's Parcel quest state machine."""

from parcel_quest import (
    DONE,
    GO_NORTH,
    OAKS_LAB,
    PALLET_TOWN,
    ROUTE_1,
    ROUTE_2,
    TO_MART,
    TO_OAK,
    VIRIDIAN_CITY,
    VIRIDIAN_MART,
    VIRIDIAN_NORTH,
    ParcelQuest,
    QuestSignals,
    quest_phase,
)


def sig(map_id, *, parcel=False, pokedex=False, x=5, y=5):
    return QuestSignals(map_id=map_id, x=x, y=y, has_parcel=parcel, has_pokedex=pokedex)


# --- phase derivation ---------------------------------------------------------


def test_phase_no_parcel_is_to_mart():
    assert quest_phase(sig(ROUTE_1)) == TO_MART


def test_phase_with_parcel_is_to_oak():
    assert quest_phase(sig(ROUTE_1, parcel=True)) == TO_OAK


def test_phase_with_pokedex_in_early_loop_is_go_north():
    for m in (PALLET_TOWN, ROUTE_1, VIRIDIAN_CITY):
        assert quest_phase(sig(m, pokedex=True)) == GO_NORTH


def test_phase_with_pokedex_past_viridian_is_done():
    assert quest_phase(sig(ROUTE_2, pokedex=True)) == DONE


def test_pokedex_overrides_a_lingering_parcel_flag():
    # If both somehow read true, delivery (pokedex) wins.
    assert quest_phase(sig(VIRIDIAN_CITY, parcel=True, pokedex=True)) == GO_NORTH


# --- target routing -----------------------------------------------------------


def test_to_mart_defers_outdoors_then_targets_mart():
    q = ParcelQuest()
    # Northbound outdoor maps defer to the baked waypoints (overriding walks into the houses).
    assert q.next_target(sig(ROUTE_1)) is None
    assert q.next_target(sig(PALLET_TOWN)) is None
    assert "Mart door" in q.next_target(sig(VIRIDIAN_CITY))["name"]
    clerk = q.next_target(sig(VIRIDIAN_MART))
    assert "clerk" in clerk["name"].lower()
    assert clerk["at_target"] == "a"  # press A at the clerk


def test_to_oak_routes_south_then_to_oak():
    q = ParcelQuest()
    assert q.next_target(sig(VIRIDIAN_CITY, parcel=True))["name"] == "south"
    assert q.next_target(sig(ROUTE_1, parcel=True))["name"] == "south"
    assert "door" in q.next_target(sig(PALLET_TOWN, parcel=True))["name"].lower()
    oak = q.next_target(sig(OAKS_LAB, parcel=True))
    assert "Oak" in oak["name"]
    assert oak["at_target"] == "a"  # press A to hand over the parcel


def test_go_north_defers_outdoors_and_targets_viridian_exit():
    q = ParcelQuest()
    # Pallet / Route 1 defer to the northbound waypoints once the gate is open.
    assert q.next_target(sig(PALLET_TOWN, pokedex=True)) is None
    assert q.next_target(sig(ROUTE_1, pokedex=True)) is None
    north = q.next_target(sig(VIRIDIAN_CITY, pokedex=True))
    assert north["target"] == VIRIDIAN_NORTH  # steer to the now-clear north exit


def test_go_north_exits_buildings_first():
    q = ParcelQuest()
    assert q.next_target(sig(OAKS_LAB, pokedex=True))["name"] == "south"
    assert q.next_target(sig(VIRIDIAN_MART, pokedex=True))["name"] == "south"


def test_to_oak_exits_mart_before_heading_south():
    q = ParcelQuest()
    assert q.next_target(sig(VIRIDIAN_MART, parcel=True))["name"] == "south"


def test_done_defers_to_normal_navigation():
    q = ParcelQuest()
    assert q.next_target(sig(ROUTE_2, pokedex=True)) is None


def test_unsteered_map_returns_none():
    # A map the quest does not manage (e.g. a random building) yields no override.
    q = ParcelQuest()
    assert q.next_target(sig(99)) is None


def test_south_target_tracks_current_x():
    q = ParcelQuest()
    t = q.next_target(sig(ROUTE_1, parcel=True, x=8))  # TO_OAK pushes south from current column
    assert t["name"] == "south"
    assert t["target"][0] == 8


def test_phase_attribute_updates_after_next_target():
    q = ParcelQuest()
    q.next_target(sig(ROUTE_1, parcel=True))
    assert q.phase == TO_OAK


def test_describe_is_a_oneliner():
    q = ParcelQuest()
    s = q.describe(sig(VIRIDIAN_CITY, parcel=True))
    assert "phase=" in s and "parcel=True" in s and "\n" not in s
