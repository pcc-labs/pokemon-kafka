"""The boulder oracle's pure parts: candidates from a configuration, outcome classes, the catalog."""

import json

import boulder_oracle as bo


def _truth():
    # 5x3 cavern floor: all walkable, one boulder in the middle. Tileset 17 has no ledges.
    rows = ["11111", "11111", "11111"]
    return {"maps": {"7": {"width": 5, "height": 3, "tileset": 17, "grid": rows, "warps": [], "sprites": []}}}


def test_config_key_is_order_free():
    assert bo.config_key([(3, 1), (1, 1)]) == bo.config_key({(1, 1), (3, 1)}) == "1,1;3,1"


def test_candidates_are_the_stands_the_player_can_reach_with_boulders_solid():
    cands = bo.candidate_pushes(_truth(), set(), 7, (0, 0), {(2, 1)})
    stands = {(s, d) for s, d, _b in cands}
    assert stands == {((2, 2), "up"), ((2, 0), "down"), ((3, 1), "left"), ((1, 1), "right")}
    assert all(b == (2, 1) for _s, _d, b in cands)


def test_a_boulder_in_a_corridor_hides_the_stands_behind_it():
    t = _truth()
    t["maps"]["7"]["grid"] = ["00000", "11111", "00000"]  # one-row corridor
    cands = bo.candidate_pushes(t, set(), 7, (0, 1), {(2, 1)})
    assert [(s, d) for s, d, _b in cands] == [((1, 1), "right")]  # (3,1) is on the far side


def test_classify_reads_the_map_and_the_sprite_table_only():
    assert bo.classify(161, 162, {(1, 1)}, set()) == "player-fell"
    assert bo.classify(161, 161, {(1, 1), (2, 2)}, {(2, 2)}) == "fell"
    assert bo.classify(161, 161, {(1, 1)}, {(1, 2)}) == "moved"
    assert bo.classify(161, 161, {(1, 1)}, {(1, 1)}) == "refused"


def test_catalog_persists_every_record_and_resumes_untried_pushes(tmp_path):
    p = tmp_path / "cat.json"
    cat = bo.Catalog(p)
    key = bo.config_key({(2, 1)})
    cat.add(7, {"config": key, "stand": [2, 2], "dir": "up", "boulder": [2, 1], "outcome": "moved", "after": "2,0"})
    cands = bo.candidate_pushes(_truth(), set(), 7, (0, 0), {(2, 1)})
    left = cat.untried(7, key, cands)
    assert ((2, 2), "up") not in {(s, d) for s, d, _b in left} and len(left) == len(cands) - 1
    # a fresh Catalog on the same file knows the same push
    again = bo.Catalog(p)
    assert again.tried(7, key) == {((2, 2), "up")}
    assert json.loads(p.read_text())["7"]["pushes"][0]["outcome"] == "moved"


def test_catalog_states_and_summary(tmp_path):
    cat = bo.Catalog(tmp_path / "cat.json")
    cat.states(161)["1,1"] = "x.state"
    cat.add(161, {"config": "1,1", "stand": [1, 2], "dir": "up", "boulder": [1, 1], "outcome": "fell", "after": ""})
    cat.add(
        161, {"config": "1,1", "stand": [0, 1], "dir": "right", "boulder": [1, 1], "outcome": "refused", "after": "1,1"}
    )
    s = cat.summary(161)
    assert "2 pushes over 1 configurations" in s and "fell" in s and "refused" in s
    assert bo.Catalog(tmp_path / "cat.json").states(161) == {"1,1": "x.state"}


def test_show_prints_the_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bo, "CATALOG_PATH", tmp_path / "cat.json")
    assert bo.main(["show", "--map", "161"]) == 0
    assert "map 161: 0 pushes" in capsys.readouterr().out


def test_surf_test_spec_is_parsed_and_validated():
    import pytest

    assert bo.parse_surf_test("15,7,down") == ((15, 7), "down")
    assert bo.parse_surf_test(None) is None
    with pytest.raises(ValueError):
        bo.parse_surf_test("1,2,sideways")


def test_catalog_summarises_where_the_water_carried_the_surfer(tmp_path):
    cat = bo.Catalog(tmp_path / "cat.json")
    cat.currents(161)["a"] = {"landing": [162, 20, 15]}
    cat.currents(161)["b"] = {"landing": [162, 20, 15]}
    cat.currents(161)["c"] = {"landing": [161, 25, 14]}
    cat.save()
    s = bo.Catalog(tmp_path / "cat.json").summary(161)
    assert "lands at [162, 20, 15] for 2 configuration(s)" in s and "lands at [161, 25, 14] for 1" in s


def test_a_boulder_on_a_hole_tile_has_fallen_and_is_never_pushed():
    t = _truth()
    m = t["maps"]["7"]
    m["tiles"] = ["0505050505", "0522050505", "0505050505"]  # (1,1) is a hole
    assert bo.fallen(t, 7, {(1, 1), (3, 1)}) == {(1, 1)}
    cands = bo.candidate_pushes(t, set(), 7, (0, 0), {(1, 1), (3, 1)})
    assert all(b == (3, 1) for _s, _d, b in cands) and cands
    assert bo.fallen({"maps": {"7": {"tiles": None}}}, 7, {(1, 1)}) == set()


def _t_shape():
    # A 3-wide shaft: the boulder at (1,1) plugs the only way down; two pushes down clear (1,1)
    # and (1,2), and the boulder rests at (1,3) where the shaft narrows again.
    rows = ["111", "010", "111", "010", "111"]
    return {"maps": {"7": {"width": 3, "height": 5, "tileset": 17, "grid": rows, "warps": [], "sprites": []}}}


def test_push_plan_pushes_one_boulder_down_the_line_until_the_target_connects():
    plan = bo.push_plan(_t_shape(), set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, {(1, 1)})
    assert plan == [((1, 0), "down", (1, 1)), ((1, 1), "down", (1, 2))]


def test_push_plan_is_empty_when_already_connected_and_none_when_no_line_opens():
    assert bo.push_plan(_t_shape(), set(), 7, (0, 0), {(2, 0)}, {(1, 1)}, {(1, 1)}) == []
    walled = _t_shape()
    walled["maps"]["7"]["grid"] = ["111", "010", "000"]
    walled["maps"]["7"]["height"] = 3
    assert bo.push_plan(walled, set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, {(1, 1)}) is None
    # a body that is not a boulder is never pushed; a boulder that is not live is not a wall
    assert bo.push_plan(_t_shape(), set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, set()) is None
    assert bo.push_plan(_t_shape(), set(), 7, (0, 0), {(0, 2)}, set(), {(1, 1)}) == []


def test_push_plan_fills_a_gap_tile_and_the_boulder_becomes_floor():
    """Victory Road 1F's rule: the boulder pushed onto the 0x24 tile stays and carries the player."""
    grid = ["11111", "11111", "11111", "00000", "11111"]  # row 3 is a chasm; (2,3) is the gap tile
    tiles = ["0303030303", "0303030303", "0303030303", "0303240303", "0303030303"]
    truth = {
        "maps": {
            "7": {"width": 5, "height": 5, "tileset": 17, "grid": grid, "warps": [], "sprites": [], "tiles": tiles}
        }
    }
    plan = bo.push_plan(truth, set(), 7, (0, 0), {(2, 4)}, {(2, 1)}, {(2, 1)})
    assert plan is not None
    stand, direction, last = plan[-1]
    dx, dy = bo.DIRS[direction]
    assert (last[0] + dx, last[1] + dy) == (2, 3)  # the last push drops it into the gap
    assert [b for _s, _d, b in plan] == [(2, 1), (2, 2)]
    plain = {"maps": {"7": {**truth["maps"]["7"], "tiles": ["0303030303"] * 5}}}
    assert bo.push_plan(plain, set(), 7, (0, 0), {(2, 4)}, {(2, 1)}, {(2, 1)}) is None


def test_filled_is_a_no_op_on_floor_and_the_push_cap_holds():
    plain = _t_shape()
    assert bo._filled(plain, 7, (0, 0)) is plain  # already floor
    assert bo._is_gap(plain["maps"]["7"], (0, 0)) is False  # no tile model
    # the shaft needs two pushes; capped at one, there is no plan
    assert bo.push_plan(_t_shape(), set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, {(1, 1)}, max_pushes=1) is None


def test_push_plan_never_stands_on_or_pushes_onto_a_warp():
    """Victory Road 1F, measured: the sixth push was planned from the exit warp and the player left the map."""
    t = _t_shape()
    t["maps"]["7"]["warps"] = [[1, 0, 9, 0]]  # the only stand for the first push is the exit
    assert bo.push_plan(t, set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, {(1, 1)}) is None
    t2 = _t_shape()
    t2["maps"]["7"]["warps"] = [[1, 3, 9, 0]]  # the boulder would have to rest on a warp
    assert bo.push_plan(t2, set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, {(1, 1)}) is None
    t3 = _t_shape()
    t3["maps"]["7"]["warps"] = [[2, 4, 9, 0]]  # a warp off the line changes nothing
    assert bo.push_plan(t3, set(), 7, (0, 0), {(0, 2)}, {(1, 1)}, {(1, 1)}) == [
        ((1, 0), "down", (1, 1)),
        ((1, 1), "down", (1, 2)),
    ]
