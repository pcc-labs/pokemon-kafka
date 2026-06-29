"""Tests for the accumulated WorldMap occupancy grid + A* planner."""

from world_map import WorldMap


def _full(val=1):
    return [[val] * 10 for _ in range(9)]


# --- observe / walkable -------------------------------------------------------


def test_observe_stamps_window_at_player_coords():
    wm = WorldMap()
    grid = _full(1)
    grid[3][3] = 0  # wall NW of the player (one up, one left)
    wm.observe(5, px=10, py=10, grid=grid)
    # player at centre (row4,col4) -> (10,10); NW cell (row3,col3) -> (9,9)
    assert wm.walkable(5, 10, 10) == 1
    assert wm.walkable(5, 9, 9) == 0


def test_unknown_defaults_to_walkable():
    wm = WorldMap()
    assert wm.walkable(0, 1, 1) == 1  # never observed -> optimistic
    assert wm.walkable(0, 1, 1, default=0) == 0


def test_observe_keeps_per_map_separate():
    wm = WorldMap()
    wm.observe(0, 5, 5, _full(1))
    wm.observe(1, 5, 5, _full(0))
    assert wm.walkable(0, 5, 5) == 1
    assert wm.walkable(1, 5, 5) == 0


def test_observe_ignores_out_of_range_coords():
    wm = WorldMap()
    wm.observe(0, px=0, py=0, grid=_full(1))  # left/up of (0,0) would be negative
    assert wm.walkable(0, 0, 0) == 1
    assert all(x >= 0 and y >= 0 for (x, y) in wm.cells[0])  # no negative keys stored


# --- plan_step ----------------------------------------------------------------


def test_plan_step_none_at_target():
    wm = WorldMap()
    assert wm.plan_step(0, 5, 5, 5, 5) is None


def test_plan_step_straight_toward_target_on_empty_map():
    wm = WorldMap()  # everything unknown -> optimistic straight line
    assert wm.plan_step(0, 5, 5, 5, 0) == "up"
    assert wm.plan_step(0, 5, 5, 9, 5) == "right"
    assert wm.plan_step(0, 5, 5, 5, 9) == "down"
    assert wm.plan_step(0, 5, 5, 1, 5) == "left"


def test_plan_step_detours_around_a_known_wall():
    wm = WorldMap()
    m = wm.cells.setdefault(0, {})
    m[(5, 4)] = 0  # wall directly north of the player at (5,5)
    d = wm.plan_step(0, 5, 5, 5, 0)  # target is north, but straight up is blocked
    assert d in ("left", "right")  # must step around, never into the wall
    assert d != "up"


def test_plan_step_follows_a_long_fence_to_its_gap():
    wm = WorldMap()
    m = wm.cells.setdefault(0, {})
    # A horizontal fence at y=4 spanning x=0..9, with a single gap at x=8.
    for x in range(0, 10):
        m[(x, 4)] = 0
    m[(8, 4)] = 1
    # Player directly under the fence at (2,5): up is a wall, x<0 is off-map (dead end to the
    # left), so the only route north is rightward to the x=8 gap.
    d = wm.plan_step(0, 2, 5, 2, 0)
    assert d == "right"


def test_plan_step_boxed_in_takes_the_only_opening():
    wm = WorldMap()
    m = wm.cells.setdefault(0, {})
    m[(5, 4)] = 0  # up
    m[(4, 5)] = 0  # left
    m[(6, 5)] = 0  # right
    d = wm.plan_step(0, 5, 5, 5, 0)  # only "down" is open even though the goal is north
    assert d == "down"


def test_block_makes_a_tile_impassable_and_reroutes():
    wm = WorldMap()
    wm.block(0, 5, 4)  # the tile straight north of the player at (5,5)
    d = wm.plan_step(0, 5, 5, 5, 0)
    assert d in ("left", "right")  # routes around the blocked tile
    assert d != "up"


def test_observe_does_not_unblock_a_failed_tile():
    wm = WorldMap()
    wm.block(0, 5, 4)
    grid = _full(1)  # collision grid claims everything (incl. 5,4) is walkable
    wm.observe(0, 5, 5, grid)
    assert wm.walkable(0, 5, 4) == 0  # the hard block survives the optimistic observation
    assert wm.plan_step(0, 5, 5, 5, 0) != "up"


def test_cross_step_advances_toward_edge_when_open():
    wm = WorldMap()
    assert wm.cross_step(0, 5, 5, "north") == "up"
    assert wm.cross_step(0, 5, 5, "south") == "down"


def test_cross_step_sweeps_to_an_open_column_at_a_wall():
    wm = WorldMap()
    wm.block(0, 5, 4)  # north of the player's column is a (learned) wall
    wm.block(0, 4, 4)  # and the column to the left
    # north of x=6 (i.e. (6,4)) is unknown -> open, so sweep right toward it
    assert wm.cross_step(0, 5, 5, "north") == "right"


def test_accumulated_observations_inform_planning():
    wm = WorldMap()
    # Observe a window that reveals a wall directly north of the player.
    grid = _full(1)
    grid[3][4] = 0  # north of centre
    wm.observe(0, 5, 5, grid)
    d = wm.plan_step(0, 5, 5, 5, 0)
    assert d != "up"  # planner respects the remembered wall


def test_plan_step_fully_walled_falls_back_to_default():
    wm = WorldMap()
    for d in ((5, 4), (4, 5), (6, 5), (5, 6)):  # block all four neighbours
        wm.block(0, *d)
    assert wm.plan_step(0, 5, 5, 5, 0) == "up"  # greedy fallback finds nothing -> default


def test_dir_same_point_is_none():
    assert WorldMap._dir(5, 5, (5, 5)) is None


def test_cross_step_fully_boxed_nudges_forward():
    wm = WorldMap()
    for d in ((5, 4), (4, 5), (6, 5), (5, 6)):  # boxed in: no cell can advance toward the edge
        wm.block(0, *d)
    assert wm.cross_step(0, 5, 5, "north") == "up"  # nothing better known -> nudge forward


def test_greedy_picks_the_neighbour_closest_to_target():
    wm = WorldMap()
    assert wm._greedy(0, {}, 5, 5, 5, 0) == "up"  # open map: step toward the target (north)


# --- encounter-aware cost (grass avoidance) -----------------------------------


def test_mark_encounter_records_tile_per_map():
    wm = WorldMap()
    wm.mark_encounter(0, 3, 3)
    assert wm.is_encounter_tile(0, 3, 3)
    assert not wm.is_encounter_tile(1, 3, 3)  # per-map, like walkability
    assert not wm.is_encounter_tile(0, 9, 9)  # unmarked tile


def test_zero_encounter_cost_ignores_grass():
    wm = WorldMap()
    wm.mark_encounter(0, 5, 4)  # grass directly north of the player at (5,5)
    # default cost 0 -> behaves exactly like before: shortest path straight up.
    assert wm.plan_step(0, 5, 5, 5, 3) == "up"
    assert wm.plan_step(0, 5, 5, 5, 3, encounter_cost=0) == "up"


def test_encounter_cost_detours_around_known_grass():
    wm = WorldMap()
    wm.mark_encounter(0, 5, 4)  # grass on the straight path north to (5,3)
    # a 2-step straight path costs 2 + penalty; a 4-step detour costs 4. With a big
    # penalty the planner prefers the longer encounter-free route.
    d = wm.plan_step(0, 5, 5, 5, 3, encounter_cost=8)
    assert d != "up"
    assert d in ("left", "right")


def test_encounter_tile_is_penalised_not_impassable():
    wm = WorldMap()
    for d in ((4, 5), (6, 5), (5, 6)):  # box in left, right, down
        wm.block(0, *d)
    wm.mark_encounter(0, 5, 4)  # the only open neighbour is grass
    # grass costs more than open ground but is still passable (unlike a wall), so when it is
    # the only way out the planner still steps onto it rather than freezing.
    assert wm.plan_step(0, 5, 5, 5, 0, encounter_cost=8) == "up"


def test_prefers_fewer_grass_tiles_among_equal_length_paths():
    wm = WorldMap()
    # Two 2-step paths from (5,5) to (6,4): via (5,4) or via (6,5). Make the (5,4)
    # route cross grass; the planner should take the other equal-length route.
    wm.mark_encounter(0, 5, 4)
    d = wm.plan_step(0, 5, 5, 6, 4, encounter_cost=8)
    assert d == "right"  # step to (6,5), avoiding the grass at (5,4)


# --- persistence (carry observations across runs) -----------------------------


def test_worldmap_roundtrips_through_dict():
    wm = WorldMap()
    grid = _full(1)
    grid[3][3] = 0  # a wall NW of the player
    wm.observe(5, 10, 10, grid)
    wm.block(5, 3, 4)
    wm.mark_encounter(7, 2, 2)

    restored = WorldMap.from_dict(wm.to_dict())

    assert restored.walkable(5, 10, 10) == 1
    assert restored.walkable(5, 9, 9) == 0  # observed wall survives
    assert restored.walkable(5, 3, 4) == 0  # hard block survives
    assert restored.is_encounter_tile(7, 2, 2)
    # A planner that learned a wall plans identically after a round-trip.
    assert restored.plan_step(5, 10, 10, 10, 5) == wm.plan_step(5, 10, 10, 10, 5)


def test_worldmap_save_load_file(tmp_path):
    wm = WorldMap()
    wm.observe(2, 8, 8, _full(1))
    wm.block(2, 8, 7)
    wm.mark_encounter(2, 9, 9)
    path = tmp_path / "world.json"
    wm.save(path)

    loaded = WorldMap.load(path)
    assert loaded.walkable(2, 8, 7) == 0
    assert loaded.is_encounter_tile(2, 9, 9)
    assert loaded.walkable(2, 8, 8) == 1


def test_worldmap_load_missing_file_is_empty():
    wm = WorldMap.load("/no/such/world-map-file.json")
    assert wm.cells == {} and wm.blocked == {} and wm.encounters == {}


# --- known_reachable / explore_step (forest exit-wedge fix) -------------------


def test_known_reachable_strict_about_unknown():
    wm = WorldMap()
    # A known-walkable corridor (0,0)->(2,0); (3,0) is unknown (never observed).
    for x in range(3):
        wm.cells.setdefault(1, {})[(x, 0)] = 1
    assert wm.known_reachable(1, 0, 0, 2, 0) is True
    # (4,0) is only reachable through the unknown (3,0): NOT known-reachable.
    assert wm.known_reachable(1, 0, 0, 4, 0) is False


def test_known_reachable_blocked_by_wall():
    wm = WorldMap()
    m = wm.cells.setdefault(1, {})
    m[(0, 0)] = 1
    m[(1, 0)] = 0  # wall
    m[(2, 0)] = 1
    assert wm.known_reachable(1, 0, 0, 2, 0) is False


def test_explore_step_heads_to_nearest_unknown():
    wm = WorldMap()
    m = wm.cells.setdefault(1, {})
    # An enclosed known corridor (0,0)->(3,0): walls above and below, so the only unknown frontier
    # is off the right end at (4,0) -> the agent must walk "right" to reach unexplored ground.
    for x in range(0, 4):
        m[(x, 0)] = 1
        m[(x, -1)] = 0
        m[(x, 1)] = 0
    m[(-1, 0)] = 0
    assert wm.explore_step(1, 0, 0) == "right"


def test_explore_step_none_when_fully_mapped():
    wm = WorldMap()
    m = wm.cells.setdefault(1, {})
    # A fully-enclosed 1x1 known cell: walls on all sides, nothing unknown reachable.
    m[(5, 5)] = 1
    for nb in [(4, 5), (6, 5), (5, 4), (5, 6)]:
        m[nb] = 0
    assert wm.explore_step(1, 5, 5) is None
