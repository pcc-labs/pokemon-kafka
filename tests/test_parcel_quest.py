"""Tests for the pure Oak's Parcel quest state machine."""

from parcel_quest import (
    DONE,
    GO_NORTH,
    OAKS_LAB,
    PALLET_TOWN,
    PEWTER_CITY,
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


def test_phase_keeps_going_north_through_route2_and_forest():
    # The corridor up to Pewter is all GO_NORTH; only Pewter itself is DONE.
    assert quest_phase(sig(ROUTE_2, pokedex=True)) == GO_NORTH
    assert quest_phase(sig(51, pokedex=True)) == GO_NORTH  # Viridian Forest
    assert quest_phase(sig(PEWTER_CITY, pokedex=True)) == DONE


def test_pokedex_overrides_a_lingering_parcel_flag():
    # If both somehow read true, delivery (pokedex) wins.
    assert quest_phase(sig(VIRIDIAN_CITY, parcel=True, pokedex=True)) == GO_NORTH


# --- target routing -----------------------------------------------------------


def test_to_mart_pilots_north_then_targets_mart():
    q = ParcelQuest()
    # Outdoor legs hand off to the collision pilot; the Mart itself is a precise target.
    assert q.next_target(sig(ROUTE_1))["pilot"] == "north"
    assert q.next_target(sig(PALLET_TOWN))["pilot"] == "north"
    assert "Mart door" in q.next_target(sig(VIRIDIAN_CITY))["name"]
    clerk = q.next_target(sig(VIRIDIAN_MART))
    assert "clerk" in clerk["name"].lower()
    assert clerk["at_target"] == "left"  # face the clerk over the counter


def test_to_oak_pilots_south_then_to_oak():
    q = ParcelQuest()
    assert q.next_target(sig(VIRIDIAN_CITY, parcel=True))["pilot"] == "south"
    assert q.next_target(sig(ROUTE_1, parcel=True))["pilot"] == "south"
    assert "door" in q.next_target(sig(PALLET_TOWN, parcel=True))["name"].lower()
    oak = q.next_target(sig(OAKS_LAB, parcel=True))
    assert "Oak" in oak["name"]
    assert oak["at_target"] == "up"  # face Oak to hand over the parcel


def test_go_north_pilots_outdoors_and_targets_viridian_exit():
    q = ParcelQuest()
    # Pallet / Route 1 pilot north once the gate is open.
    assert q.next_target(sig(PALLET_TOWN, pokedex=True))["pilot"] == "north"
    assert q.next_target(sig(ROUTE_1, pokedex=True))["pilot"] == "north"
    north = q.next_target(sig(VIRIDIAN_CITY, pokedex=True))
    assert north["pilot_to"] == VIRIDIAN_NORTH  # seek the now-clear north exit


def test_go_north_exits_buildings_via_door_warp():
    q = ParcelQuest()
    assert "exit" in q.next_target(sig(OAKS_LAB, pokedex=True))["name"].lower()
    assert "exit" in q.next_target(sig(VIRIDIAN_MART, pokedex=True))["name"].lower()


def test_to_oak_exits_mart_via_door_warp():
    q = ParcelQuest()
    t = q.next_target(sig(VIRIDIAN_MART, parcel=True))
    assert "exit" in t["name"].lower() and "pilot_to" in t


def test_route2_and_forest_pilot_north_then_pewter_defers():
    q = ParcelQuest()
    assert q.next_target(sig(ROUTE_2, pokedex=True))["pilot"] == "north"
    assert q.next_target(sig(51, pokedex=True))["pilot"] == "north"  # Viridian Forest
    assert q.next_target(sig(PEWTER_CITY, pokedex=True)) is None  # DONE at Pewter


def test_unsteered_map_returns_none():
    # A map the quest does not manage (e.g. a random building) yields no override.
    q = ParcelQuest()
    assert q.next_target(sig(99)) is None


def test_pilot_directives_have_no_coordinate_target():
    q = ParcelQuest()
    t = q.next_target(sig(ROUTE_1, parcel=True))  # TO_OAK pilots south
    assert t["pilot"] == "south"
    assert "target" not in t  # the agent's pilot navigates by collision, not a fixed tile


def test_phase_attribute_updates_after_next_target():
    q = ParcelQuest()
    q.next_target(sig(ROUTE_1, parcel=True))
    assert q.phase == TO_OAK


def test_describe_is_a_oneliner():
    q = ParcelQuest()
    s = q.describe(sig(VIRIDIAN_CITY, parcel=True))
    assert "phase=" in s and "parcel=True" in s and "\n" not in s
