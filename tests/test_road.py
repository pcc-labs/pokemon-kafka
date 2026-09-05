"""The road engine, driven through a scripted world.

Every mechanism ported from the badge-4 expedition is exercised against a FakeIO whose
d-pad presses move the player over truth-shaped grids, with warp behavior scripted per
test: entry warps fire on arrival, thresholds fire on the directional step through, and
misaligned edges hand over only at their true crossing cells."""

import pytest
import quartermaster as qm
import road


def _map(grid, connections=None, warps=None):
    return {
        "width": len(grid[0]),
        "height": len(grid),
        "grid": grid,
        "tileset": 1,  # ledges are an overworld-tileset mechanism; keep them out of these tests
        "connections": connections or {},
        "warps": warps or [],
        "sprites": [],
    }


class RoadIO:
    """Presses mutate the same registers the engine reads: d-pads move over the grid,
    arrival warps and threshold warps teleport, and a `frozen` flag models an input-eating
    screen (a guard, a lingering box)."""

    def __init__(self, truth, start, arrive=None, thresholds=None, frozen_at=None):
        self.truth = truth
        self.mem = {qm.ADDR_MAP: start[0], qm.ADDR_X: start[1], qm.ADDR_Y: start[2]}
        self.arrive = arrive or {}  # (map,x,y) -> (map,x,y): fires when stepped onto
        self.thresholds = thresholds or {}  # (map,x,y,dir) -> (map,x,y): fires stepping off
        self.frozen_at = frozen_at or set()  # (map,x,y): d-pads are eaten while standing here
        self.pressed = []

    def _tp(self, dest):
        self.mem[qm.ADDR_MAP], self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y] = dest

    def press(self, btn, hold=8, release=8):
        self.pressed.append(btn)
        if btn not in ("up", "down", "left", "right"):
            return
        mp, x, y = qm.read_pos(self)
        if (mp, x, y) in self.frozen_at:
            return
        th = self.thresholds.get((mp, x, y, btn))
        if th:
            self._tp(th)
            return
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[btn]
        nx, ny = x + dx, y + dy
        m = self.truth["maps"][str(mp)]
        if not (0 <= nx < m["width"] and 0 <= ny < m["height"]) or m["grid"][ny][nx] != "1":
            return
        self._tp(self.arrive.get((mp, nx, ny), (mp, nx, ny)))

    def wait(self, frames=30):
        pass

    def read(self, addr):
        return self.mem.get(addr, 0)


PAIRS: set = set()


# --------------------------------------------------------------------------- edge_cells / bodies


def test_edge_cells_all_four_sides():
    truth = {
        "maps": {
            "1": _map(["111", "111", "101"], connections={"north": 2, "south": 3, "west": 4, "east": 5}),
        }
    }
    assert road.edge_cells(truth, 1, 2) == ({(0, 0), (1, 0), (2, 0)}, "up")
    assert road.edge_cells(truth, 1, 3) == ({(0, 2), (2, 2)}, "down")
    assert road.edge_cells(truth, 1, 4) == ({(0, 0), (0, 1), (0, 2)}, "left")
    assert road.edge_cells(truth, 1, 5) == ({(2, 0), (2, 1), (2, 2)}, "right")


def test_live_bodies_reads_the_sprite_table():
    io = RoadIO({"maps": {}}, (0, 0, 0))
    io.mem[road.SPRITE_STATE_BASE + 0x10] = 1
    io.mem[road.SPRITE_DATA_BASE + 0x10 + 4] = 7 + 4  # y
    io.mem[road.SPRITE_DATA_BASE + 0x10 + 5] = 3 + 4  # x
    assert road.live_bodies(io) == {(3, 7)}


# --------------------------------------------------------------------------- walk


def _open_world(w=6, h=1, **kw):
    return {"maps": {"1": _map(["1" * w] * h, **kw)}}


def test_walk_arrives():
    io = RoadIO(_open_world(), (1, 0, 0))
    assert road.walk(io, io.truth, PAIRS, 1, {(4, 0)}) is True
    assert qm.read_pos(io) == (1, 4, 0)


def test_walk_reports_map_change():
    io = RoadIO(_open_world(), (1, 0, 0), arrive={(1, 2, 0): (9, 5, 5)})
    assert road.walk(io, io.truth, PAIRS, 1, {(4, 0)}) == "map-change"


def test_walk_no_path_and_body_blocked():
    truth = {"maps": {"1": _map(["101"])}}
    io = RoadIO(truth, (1, 0, 0))
    assert road.walk(io, truth, PAIRS, 1, {(2, 0)}) == "no-path"
    io2 = RoadIO(_open_world(w=3), (1, 0, 0))
    io2.mem[road.SPRITE_STATE_BASE + 0x10] = 1
    io2.mem[road.SPRITE_DATA_BASE + 0x10 + 4] = 0 + 4
    io2.mem[road.SPRITE_DATA_BASE + 0x10 + 5] = 1 + 4
    assert road.walk(io2, io2.truth, PAIRS, 1, {(2, 0)}) == "body-blocked"


def test_walk_delegates_battles_and_finishes():
    io = RoadIO(_open_world(), (1, 0, 0))
    io.mem[qm.ADDR_IN_BATTLE] = 1
    fought = []

    def battle(io_):
        fought.append(True)
        io_.mem[qm.ADDR_IN_BATTLE] = 0

    assert road.walk(io, io.truth, PAIRS, 1, {(3, 0)}, battle=battle) is True
    assert fought == [True]


def test_walk_without_a_handler_refuses_to_guess():
    io = RoadIO(_open_world(), (1, 0, 0))
    io.mem[qm.ADDR_IN_BATTLE] = 1
    with pytest.raises(qm.QuartermasterError, match="no battle handler"):
        road.walk(io, io.truth, PAIRS, 1, {(3, 0)})


def test_walk_stall_cycles_then_refused():
    """An input-eating screen: A/B cycles fire, and only persistent immobility refuses."""
    io = RoadIO(_open_world(), (1, 1, 0), frozen_at={(1, 1, 0)})
    assert road.walk(io, io.truth, PAIRS, 1, {(4, 0)}) == "refused"
    assert io.pressed.count("a") == 12  # 4 cycles x 3 A-presses
    assert "b" in io.pressed


def test_walk_stall_speech_leads_into_the_fight():
    """The trainer-speech shape: frozen until an A opens the battle, which the handler wins."""
    io = RoadIO(_open_world(), (1, 1, 0), frozen_at={(1, 1, 0)})
    orig = io.press

    def press(btn, hold=8, release=8):
        if btn == "a":
            io.mem[qm.ADDR_IN_BATTLE] = 2
        orig(btn, hold, release)

    io.press = press

    def battle(io_):
        io_.mem[qm.ADDR_IN_BATTLE] = 0
        io_.frozen_at = set()  # the fight over, the road is open

    assert road.walk(io, io.truth, PAIRS, 1, {(4, 0)}, battle=battle) is True


def test_walk_cap():
    io = RoadIO(_open_world(), (1, 0, 0), frozen_at={(1, 0, 0)})
    assert road.walk(io, io.truth, PAIRS, 1, {(4, 0)}, cap=3) == "cap"


# --------------------------------------------------------------------------- warps


def test_through_warp_fires_on_arrival():
    io = RoadIO(_open_world(), (1, 0, 0), arrive={(1, 3, 0): (7, 1, 1)})
    assert road.through_warp(io, io.truth, PAIRS, 1, 3, 0) is True
    assert qm.read_pos(io)[0] == 7


def test_through_warp_threshold_fires_on_the_step_through():
    """Route 11's gate door: standing on the tile does nothing; the deeper step fires."""
    truth = _open_world(w=4)
    io = RoadIO(truth, (1, 0, 0), thresholds={(1, 3, 0, "right"): (7, 0, 5)})
    assert road.through_warp(io, io.truth, PAIRS, 1, 3, 0) is True
    assert qm.read_pos(io)[0] == 7


def test_through_warp_ladder_fires_on_reentry():
    """The Rock Tunnel ladder: step off, and the step BACK onto the tile fires."""
    truth = _open_world(w=4)
    io = RoadIO(truth, (1, 2, 0))
    # walking onto (3,0) as a target does not fire; stepping right is walled; the undo of a
    # successful left step re-enters the tile, which now fires.
    fired = {"armed": False}
    orig_tp = io._tp

    def tp(dest):
        if dest == (1, 3, 0) and fired["armed"]:
            orig_tp((7, 9, 9))
        else:
            fired["armed"] = dest == (1, 2, 0)
            orig_tp(dest)

    io._tp = tp
    assert road.through_warp(io, io.truth, PAIRS, 1, 3, 0) is True
    assert qm.read_pos(io)[0] == 7


def test_through_warp_dead_and_walk_failure_passthrough():
    io = RoadIO({"maps": {"1": _map(["0001"])}}, (1, 3, 0))
    assert road.through_warp(io, io.truth, PAIRS, 1, 3, 0) == "warp-dead"
    io2 = RoadIO({"maps": {"1": _map(["101"])}}, (1, 0, 0))
    assert road.through_warp(io2, io2.truth, PAIRS, 1, 2, 0) == "no-path"


# --------------------------------------------------------------------------- interiors and gates


GATE = _map(["111"] * 3, warps=[[0, 1, 255, 0], [2, 1, 255, 1]])


def test_traverse_interior_exits_the_far_side():
    truth = {"maps": {"8": GATE}}
    io = RoadIO(truth, (8, 0, 1), thresholds={(8, 2, 1, "right"): (1, 5, 0)})
    assert road.traverse_interior(io, truth, PAIRS, 8) is True
    assert qm.read_pos(io)[0] == 1


def test_traverse_interior_north_south_mats():
    """A vertical gate: entered by the south mats, exited by the north (mat rows classify)."""
    tall = _map(["111"] * 4, warps=[[1, 0, 255, 0], [1, 3, 255, 1]])
    truth = {"maps": {"8": tall}}
    io = RoadIO(truth, (8, 1, 3), thresholds={(8, 1, 0, "up"): (2, 4, 4)})
    assert road.traverse_interior(io, truth, PAIRS, 8) is True
    assert qm.read_pos(io) == (2, 4, 4)


def test_traverse_interior_map_change_and_unknown_and_stuck():
    truth = {"maps": {"8": GATE}}
    io = RoadIO(truth, (8, 0, 1), arrive={(8, 2, 1): (1, 5, 0)})
    assert road.traverse_interior(io, truth, PAIRS, 8) is True
    assert road.traverse_interior(RoadIO(truth, (8, 0, 1)), truth, PAIRS, 99) == "unknown-interior"
    io3 = RoadIO(truth, (8, 0, 1))  # far mat reachable but nothing ever fires
    assert road.traverse_interior(io3, truth, PAIRS, 8) == "interior-stuck"


def _gate_world():
    """A route severed mid-map: west half, wall, east half; a decoy house and a real gate."""
    route = _map(
        ["1111011", "1111011"],
        connections={"east": 3},
        warps=[[1, 0, 60, 0], [3, 0, 8, 0], [3, 1, 8, 1]],
    )
    house = _map(["11"], warps=[[0, 0, 255, 0]])  # one door: back where you came from
    return {"maps": {"2": route, "8": GATE, "60": house}}


def test_pass_gate_validates_candidates_and_crosses():
    truth = _gate_world()
    io = RoadIO(
        truth,
        (2, 0, 0),
        arrive={(2, 1, 0): (60, 1, 0), (2, 3, 0): (8, 0, 1), (2, 3, 1): (8, 0, 1)},
        thresholds={(60, 0, 0, "left"): (2, 0, 0), (8, 2, 1, "right"): (2, 5, 0)},
    )
    cells = {(6, 0), (6, 1)}
    assert road.pass_gate(io, truth, PAIRS, 2, cells) is True
    assert qm.read_pos(io) == (2, 5, 0)


def test_pass_gate_guard_refusal_and_exhaustion():
    truth = _gate_world()
    # the gate interior eats every input: a guard — pass_gate reports failure from inside
    io = RoadIO(truth, (2, 2, 0), arrive={(2, 3, 0): (8, 1, 1)}, frozen_at={(8, 1, 1)})
    io.truth["maps"]["2"]["warps"] = [[3, 0, 8, 0]]
    assert road.pass_gate(io, truth, PAIRS, 2, {(6, 0)}) is False
    # no candidate ever leaves the map at all
    truth2 = _gate_world()
    io2 = RoadIO(truth2, (2, 0, 0))
    assert road.pass_gate(io2, truth2, PAIRS, 2, {(6, 0)}) is False


# --------------------------------------------------------------------------- edges


def test_cross_edge_sweeps_for_the_aligned_cell():
    """Only the second edge cell actually hands over (the connection-offset lesson)."""
    truth = {"maps": {"1": _map(["111", "111"], connections={"east": 2})}}
    io = RoadIO(truth, (1, 0, 0), thresholds={(1, 2, 1, "right"): (2, 0, 1)})
    assert road.cross_edge(io, truth, PAIRS, 1, 2) is True
    assert qm.read_pos(io)[0] == 2


def test_cross_edge_walk_failure_and_stuck():
    truth = {"maps": {"1": _map(["101"], connections={"east": 2})}}
    io = RoadIO(truth, (1, 0, 0))
    assert road.cross_edge(io, truth, PAIRS, 1, 2) == "no-path"
    truth2 = {"maps": {"1": _map(["111"], connections={"east": 2})}}
    io2 = RoadIO(truth2, (1, 0, 0))
    assert road.cross_edge(io2, truth2, PAIRS, 1, 2) == "stuck-on-edge"


def test_cross_edge_hands_over_en_route_to_the_next_cell():
    """The map can change while WALKING toward another candidate cell."""
    truth = {"maps": {"1": _map(["111", "111"], connections={"east": 2})}}
    io = RoadIO(truth, (1, 2, 0), arrive={(1, 2, 1): (2, 0, 1)})
    assert road.cross_edge(io, truth, PAIRS, 1, 2) is True


# --------------------------------------------------------------------------- cut


def test_cut_facing_drives_the_menu_registers():
    io = RoadIO(_open_world(), (1, 0, 0))
    cur = {"v": 0}
    io.read_orig = io.read
    io.read = lambda addr: cur["v"] if addr == qm.ADDR_MENU_CUR else io.read_orig(addr)
    orig = io.press

    def press(btn, hold=8, release=8):
        if btn in ("down", "up"):
            cur["v"] += 1 if btn == "down" else -1
        orig(btn, hold, release)

    io.press = press
    road.cut_facing(io, "right")
    assert io.pressed[0] == "right"
    assert io.pressed.count("down") == 1  # cursor walked to the POKeMON row once
    assert io.pressed.count("a") == 3  # party -> lead -> CUT
    assert io.pressed[-1] == "b"


# --------------------------------------------------------------------------- drive_to


def _two_map_world():
    a = _map(["111"], connections={"east": 2})
    b = _map(["111"], connections={"west": 1}, warps=[[2, 0, 9, 0]])
    c = _map(["11"])
    return {"maps": {"1": a, "2": b, "9": c}}


def test_drive_to_edges_and_warps():
    truth = _two_map_world()
    logs = []
    io = RoadIO(truth, (1, 0, 0), thresholds={(1, 2, 0, "right"): (2, 0, 0)}, arrive={(2, 2, 0): (9, 0, 0)})
    assert road.drive_to(io, truth, PAIRS, 9, log=logs.append) is True
    assert any("--edge-->" in m or "edge" in m for m in logs)


def test_drive_to_no_route_and_hop_failure():
    truth = {"maps": {"1": _map(["111"]), "9": _map(["11"])}}
    io = RoadIO(truth, (1, 0, 0))
    assert road.drive_to(io, truth, PAIRS, 9) is False  # no route at all
    truth2 = {"maps": {"1": _map(["111"], connections={"east": 2}), "2": _map(["111"], connections={"west": 1})}}
    io2 = RoadIO(truth2, (1, 0, 0))  # edge never hands over, no gate to pass
    assert road.drive_to(io2, truth2, PAIRS, 2) is False


def test_drive_to_passes_a_gate_when_the_edge_is_severed():
    route = _map(
        ["110111", "110111"],
        connections={"east": 3},
        warps=[[1, 0, 8, 0]],
    )
    far = _map(["11"], connections={"west": 2})
    truth = {"maps": {"2": route, "8": GATE, "3": far}}
    io = RoadIO(
        truth,
        (2, 0, 0),
        arrive={(2, 1, 0): (8, 0, 1)},
        thresholds={(8, 2, 1, "right"): (2, 3, 0), (2, 5, 0, "right"): (3, 0, 0), (2, 5, 1, "right"): (3, 0, 0)},
    )
    assert road.drive_to(io, truth, PAIRS, 3) is True


def test_drive_to_traverses_a_swallowing_interior():
    """An edge crossing that lands INSIDE a gate: the interior is traversed onward."""
    a = _map(["111"], connections={"east": 3})
    far = _map(["11"], connections={"west": 1})
    truth = {"maps": {"1": a, "8": GATE, "3": far}}
    io = RoadIO(
        truth,
        (1, 0, 0),
        thresholds={(1, 2, 0, "right"): (8, 0, 1), (8, 2, 1, "right"): (3, 0, 0)},
    )
    assert road.drive_to(io, truth, PAIRS, 3) is True
    # and one that refuses from inside (a guard eating input)
    io2 = RoadIO(
        truth,
        (1, 0, 0),
        thresholds={(1, 2, 0, "right"): (8, 1, 1)},
        frozen_at={(8, 1, 1)},
    )
    assert road.drive_to(io2, truth, PAIRS, 3) is False


def test_drive_to_hop_cap_runs_out():
    truth = _two_map_world()
    io = RoadIO(truth, (1, 0, 0), thresholds={(1, 2, 0, "right"): (2, 0, 0)}, arrive={(2, 2, 0): (9, 0, 0)})
    assert road.drive_to(io, truth, PAIRS, 9, max_hops=0) is False


# ------------------------------------------------------------------ the wall vs the bump


def _corridor_truth():
    """A map shaped like Route 12: a wide south room, a two-column corridor north, and one
    choke cell at the top that a single body can plug."""
    #      x: 0123456
    rows = [
        "0011000",  # y=0  the goal edge
        "0011000",  # y=1
        "0001000",  # y=2  the choke: only x=3
        "0011000",  # y=3
        "1111111",  # y=4  the wide south room
        "1111111",  # y=5
    ]
    return {
        "maps": {
            "1": {
                "width": 7,
                "height": 6,
                "tileset": 0,
                "grid": rows,
                "warps": [],
                "sprites": [],
                "connections": {"north": 2},
            },
            "2": {
                "width": 7,
                "height": 6,
                "tileset": 0,
                "grid": rows,
                "warps": [],
                "sprites": [],
                "connections": {"south": 1},
            },
        }
    }


def test_reachable_is_the_body_aware_region():
    truth, pairs = _corridor_truth(), set()
    assert (3, 0) in road.reachable(truth, pairs, 1, (0, 5))
    assert (3, 0) not in road.reachable(truth, pairs, 1, (0, 5), blocked={(3, 2)})


def test_blocking_body_names_the_choke_not_the_body_underfoot():
    """Route 12 in miniature: the bystander at (1,4) is adjacent; the wall is the choke at (3,2)."""
    truth, pairs = _corridor_truth(), set()
    bodies = {(1, 4), (3, 2)}
    assert road.blocking_body(truth, pairs, 1, (0, 5), {(2, 0), (3, 0)}, bodies) == (3, 2)


def test_blocking_body_is_none_when_the_goal_is_already_reachable():
    truth, pairs = _corridor_truth(), set()
    assert road.blocking_body(truth, pairs, 1, (0, 5), {(2, 0), (3, 0)}, {(1, 4)}) is None


def test_two_bodies_in_one_corridor_are_terrain_not_a_gate():
    """No *single* removal reconnects it, so there is no one sprite to go argue with."""
    truth, pairs = _corridor_truth(), set()
    bodies = {(3, 2), (3, 3), (2, 3)}
    assert road.blocking_body(truth, pairs, 1, (0, 5), {(2, 0), (3, 0)}, bodies) is None


class CutIO:
    """A bush that opens after `cuts` applications of the field-Cut flow."""

    def __init__(self, cuts=1):
        self.cuts, self.applied, self.pos = cuts, 0, (1, 5, 5)
        self.presses = []

    def press(self, btn, hold=8, release=8):
        self.presses.append(btn)
        if btn == "a":
            self.applied += 1
        if btn == "up" and self.applied >= self.cuts:
            self.pos = (1, 5, 4)

    def wait(self, frames=30):
        pass

    def read(self, addr):
        return 1  # the field submenu cursor sits where cut_facing wants it


def test_cut_until_open_proves_the_cut_by_stepping(monkeypatch):
    """`cut_facing` fires the menu whether or not anything was cut; the step is the predicate."""
    io = CutIO(cuts=3)
    monkeypatch.setattr(road, "read_pos", lambda i: i.pos)
    assert road.cut_until_open(io, {}, set(), "up") is True
    assert "up" in io.presses


@pytest.fixture
def cut_read_pos(monkeypatch):
    """`cut_until_open`'s fake reads position off `io.pos`. Patch it through monkeypatch so the
    replacement is undone: assigning `road.read_pos` directly leaked into every later test in
    this file, and the first one to use another fake io died on `'RoadIO' has no attribute pos`."""
    monkeypatch.setattr(road, "read_pos", lambda i: i.pos)


def test_cut_until_open_gives_up_after_its_tries(cut_read_pos):
    io = CutIO(cuts=99)
    import rom_truth  # noqa: F401  (road.read_pos is imported at module scope)

    assert road.cut_until_open(io, {}, set(), "up", tries=2) is False


def test_cut_until_open_succeeds_on_the_step_after_the_cut(cut_read_pos):
    io = CutIO(cuts=1)
    assert road.cut_until_open(io, {}, set(), "up") is True


def test_cut_until_open_returns_at_once_when_the_way_is_already_clear(cut_read_pos):
    io = CutIO(cuts=0)
    assert road.cut_until_open(io, {}, set(), "up") is True
    assert "a" not in io.presses  # no menu was opened; the step just worked


def test_walkable_treats_pads_as_walls_and_reachable_does_not():
    """Silph 5F in miniature: the only path to the right-hand corridor crosses a warp pad.

    `reachable` (terrain) says the corridor is open; `walk` refuses to thread a door tile as
    floor, so the corridor is unreachable on foot and nine steps from the pad. Two sessions of
    "the model says reachable and the engine refuses" were this disagreement.
    """
    truth = {
        "maps": {
            "1": {
                "width": 5,
                "height": 1,
                "tileset": 22,
                "grid": ["11111"],
                "warps": [[2, 0, 7, 0]],
                "connections": {},
                "sprites": [],
            }
        }
    }
    pairs = set()
    assert (4, 0) in road.reachable(truth, pairs, 1, (0, 0))
    assert (4, 0) not in road.walkable(truth, pairs, 1, (0, 0))
    assert (4, 0) in road.walkable(truth, pairs, 1, (3, 0))  # already past the pad
    # The pad itself stays open when it is the target of the walk, matching `walk`'s own rule.
    assert (2, 0) in road.walkable(truth, pairs, 1, (0, 0), keep={(2, 0)})


def test_pads_reaching_names_the_ride_into_a_cut_off_corridor():
    truth = {
        "maps": {
            "1": {
                "width": 5,
                "height": 1,
                "tileset": 22,
                "grid": ["11111"],
                "warps": [[2, 0, 7, 0]],
                "connections": {},
                "sprites": [],
            }
        }
    }
    assert road.pads_reaching(truth, set(), 1, {(4, 0)}) == [((2, 0), 7)]
    assert road.pads_reaching(truth, set(), 1, {(0, 0)}) == [((2, 0), 7)]


def test_ride_pad_enters_a_region_whose_only_door_is_a_pad():
    """Silph 5F in miniature. Row 3 is `..P#` — the pad at x=2 is the only way to x=3, so `walk`
    (which treats pads as walls) can never deliver us there. Riding it lands on the far map,
    stepping off and back on returns us STANDING on the pad, and from there x=3 is one step."""
    truth = {
        "maps": {
            "1": _map(["1111"], warps=[[2, 0, 2, 0]]),
            "2": _map(["111"], warps=[[1, 0, 1, 0]]),
        }
    }
    # Each pad fires on arrival, sending us to the other map's warp tile.
    io = RoadIO(truth, (1, 0, 0), arrive={(1, 2, 0): (2, 1, 0), (2, 1, 0): (1, 2, 0)})
    assert road.walkable(truth, set(), 1, (0, 0)) == {(0, 0), (1, 0)}  # the walk cannot get there
    assert road.ride_pad(io, truth, set(), 1, {(3, 0)}) is True
    assert (io.mem[qm.ADDR_MAP], io.mem[qm.ADDR_X], io.mem[qm.ADDR_Y]) == (1, 3, 0)


def test_ride_pad_reports_failure_when_no_pad_stands_in_the_region():
    truth = {"maps": {"1": _map(["1101"], warps=[])}}
    io = RoadIO(truth, (1, 0, 0))
    assert road.ride_pad(io, truth, set(), 1, {(3, 0)}) is False


def test_live_bodies_clips_to_the_map_it_is_standing_on():
    """The sprite table has sixteen slots and the unused ones decode to coordinates that are not
    on any map. Silph 3F is 30x18 and a leg was told the body severing its hop stood at (18,22),
    four rows past the south wall — then walked over to engage it, opened the pause menu, and
    recorded "OPTION EXIT" as what the blocker said."""

    class SpriteIO:
        def __init__(self, cells):
            self.cells = cells

        def read(self, addr):
            for i, (x, y) in enumerate(self.cells, start=1):
                if addr == road.SPRITE_STATE_BASE + i * 0x10:
                    return 1
                if addr == road.SPRITE_DATA_BASE + i * 0x10 + 5:
                    return x + 4
                if addr == road.SPRITE_DATA_BASE + i * 0x10 + 4:
                    return y + 4
            return 0

    io = SpriteIO([(7, 9), (18, 22)])
    assert road.live_bodies(io) == {(7, 9), (18, 22)}  # unclipped: the junk slot is a "body"
    assert road.live_bodies(io, (30, 18)) == {(7, 9)}  # clipped to the floor we are standing on


def test_a_warp_tile_is_never_an_approach_cell():
    """`keep` exists for the target of a walk, not for the cell you stand on to reach one. Passing
    the whole adjacency as `keep` let a leg "walk next to the blocker" by stepping onto Saffron's
    Silph entrance — it warped indoors, walked back out, and did that until the hop cap fired."""
    truth = {
        "maps": {
            "1": {
                "width": 5,
                "height": 1,
                "tileset": 0,
                "grid": ["11111"],
                "warps": [[3, 0, 9, 0]],
                "connections": {},
                "sprites": [],
            }
        }
    }
    adjacent = {(3, 0), (1, 0)}  # (3,0) is the door beside the body at (4,0); (1,0) is floor
    assert road.walkable(truth, set(), 1, (0, 0)) & adjacent == {(1, 0)}


def test_ride_pad_handles_an_intra_map_pad_that_teleports_within_the_floor():
    """Sabrina's gym is a pad maze: 30 of its 32 warps point at itself, so riding one lands you
    elsewhere on the SAME map and there is no far side to come back from. A leg that only knew
    how to ride between maps met the guide at the door and called the floor engaged."""
    truth = {"maps": {"1": _map(["1" * 7], warps=[[2, 0, 1, 0], [5, 0, 1, 0]])}}
    io = RoadIO(truth, (1, 0, 0), arrive={(1, 2, 0): (1, 5, 0)})
    assert (6, 0) not in road.walkable(truth, set(), 1, (0, 0))  # unreachable on foot
    assert road.ride_pad(io, truth, set(), 1, {(6, 0)}) is True
    assert (io.mem[qm.ADDR_MAP], io.mem[qm.ADDR_X]) == (1, 6)


def test_ride_pad_chains_hops_through_a_maze():
    """One ride is enough for Silph's floors; Sabrina's gym is thirty pads deep, and the pocket
    holding a trainer sits several rides from the door."""
    truth = {"maps": {"1": _map(["1" * 9], warps=[[2, 0, 1, 0], [4, 0, 1, 0], [6, 0, 1, 0]])}}
    io = RoadIO(truth, (1, 0, 0), arrive={(1, 2, 0): (1, 4, 0), (1, 4, 0): (1, 6, 0)})
    assert (8, 0) not in road.walkable(truth, set(), 1, (0, 0))
    assert road.ride_pad(io, truth, set(), 1, {(8, 0)}) is True
    assert io.mem[qm.ADDR_X] == 8


def test_ride_pad_stops_after_its_hop_budget():
    truth = {"maps": {"1": _map(["1" * 9], warps=[[2, 0, 1, 0]])}}
    io = RoadIO(truth, (1, 0, 0), arrive={(1, 2, 0): (1, 0, 0)})  # a pad that loops us home
    assert road.ride_pad(io, truth, set(), 1, {(8, 0)}, rides=2) is False


def test_pad_land_resolves_the_same_map_destination_not_the_others():
    # 255 (0xFF) is the ROM's "this map" destination and its index reads the map's own warp list.
    m = _map(["1" * 7], warps=[[2, 0, 255, 1], [3, 0, 1, 0], [5, 0, 2, 0]])
    road.truth = {"maps": {"1": m, "2": m}}
    assert road.pad_land(road.truth, 1, [2, 0, 255, 1]) == (3, 0)
    assert road.pad_land(road.truth, 1, [3, 0, 1, 0]) == (2, 0)
    assert road.pad_land(road.truth, 1, [5, 0, 2, 0]) is None  # a door to another map is not the graph
    assert road.pad_land(road.truth, 1, [2, 0, 255, 9]) is None  # an index past the list is a decode lie


def test_pad_route_orders_the_rides_the_table_order_hunt_gives_up_on():
    # (2,0) and (5,0) ride at each other; (7,0) rides home and opens the last pocket. The table
    # lists (7,0) LAST, so the nearest-use hunt stands on (5,0)'s pocket and never rides it — the
    # BFS says ride (2,0), then (7,0), and the walk takes the last two steps.
    road.truth = {"maps": {"1": _map(["1" * 9], warps=[[2, 0, 1, 1], [5, 0, 1, 0], [7, 0, 1, 2]])}}
    assert road.pad_route(road.truth, set(), 1, (0, 0), {(8, 0)}) == [(2, 0), (7, 0)]
    assert road.pad_route(road.truth, set(), 1, (0, 0), {(1, 0)}) == []  # a plain walk covers it
    assert road.pad_route(road.truth, set(), 1, (0, 0), {}) is None


def test_pad_route_says_ride_your_own_pad_when_it_is_the_only_exit():
    # Standing ON a pad does not fire it. When that pad's landing pocket holds the target, the
    # route is the pad itself — the caller re-fires it by stepping off and back on.
    road.truth = {"maps": {"1": _map(["1" * 7], warps=[[1, 0, 1, 1], [4, 0, 1, 0]])}}
    assert road.pad_route(road.truth, set(), 1, (1, 0), {(5, 0)}) == [(1, 0)]


def test_pad_route_sees_the_bodies_severing_the_pocket():
    road.truth = {"maps": {"1": _map(["1" * 9], warps=[[2, 0, 1, 1], [5, 0, 1, 0]])}}
    assert road.pad_route(road.truth, set(), 1, (0, 0), {(8, 0)}) == [(2, 0)]
    assert road.pad_route(road.truth, set(), 1, (0, 0), {(8, 0)}, bodies={(6, 0), (7, 0)}) is None


def test_ride_pad_rides_the_routed_sequence_and_walks_the_rest():
    truth = {"maps": {"1": _map(["1" * 9], warps=[[2, 0, 1, 1], [5, 0, 1, 0], [7, 0, 1, 2]])}}
    io = RoadIO(truth, (1, 0, 0), arrive={(1, 2, 0): (1, 5, 0), (1, 7, 0): (1, 7, 0)})
    assert road.ride_pad(io, truth, set(), 1, {(8, 0)}, rides=3) is True
    assert qm.read_pos(io) == (1, 8, 0)
    assert io.pressed.count("right") == 5  # the route: (1,0), (2,0), (6,0), (7,0), (8,0)


def test_ride_pad_refires_the_pad_its_feet_are_on():
    # The (9,8) pocket's only exit is its own pad, and we arrive standing on such a pad: the ride
    # is stepping off it and back on, which is what re-fires it.
    truth = {"maps": {"1": _map(["1" * 7], warps=[[1, 0, 1, 1], [4, 0, 1, 0]])}}
    io = RoadIO(truth, (1, 1, 0), arrive={(1, 1, 0): (1, 4, 0)})
    assert road.ride_pad(io, truth, set(), 1, {(5, 0)}, rides=2) is True
    assert qm.read_pos(io) == (1, 5, 0)


def test_ride_pad_reaches_every_standing_body_in_sabrinas_gym():
    # The measured shape: 32 warps, every same-map pad riding in 2-cycles (each landing is
    # another pad tile), five unengaged bodies, and a baton bench at (17,14). The old engine
    # rode its budget standing in two pockets on all five; the routed engine must reach each,
    # and stand on the facing cell, within six rides.
    import rom_truth as rt

    truth = rt.load_truth()
    pairs = rt.loaded_pairs(truth)
    m = truth["maps"]["178"]
    arrive = {
        (178, w[0], w[1]): (178, land[0], land[1])
        for w in m["warps"]
        if (land := road.pad_land(truth, 178, w)) is not None
    }
    bodies = {(3, 1), (3, 7), (3, 13), (9, 8), (10, 1), (10, 15)}
    for body in ((9, 8), (10, 1), (3, 7), (3, 13), (3, 1)):
        x, y = body
        ring = {(x, y + 1), (x, y - 1), (x + 1, y), (x - 1, y), (x, y - 2)}
        ring = {c for c in ring if 0 <= c[0] < m["width"] and 0 <= c[1] < m["height"]}
        io = RoadIO({"maps": {"178": m}}, (178, 17, 14), arrive=arrive)
        assert road.ride_pad(io, {"maps": {"178": m}}, pairs, 178, ring, rides=6) is True, f"no ride reaches {body}"
        assert qm.read_pos(io)[1:] in ring, f"did not stand on the {body} facing cell"
    # And leaving a dead pocket: standing on (11,11), the (9,8) pocket's only pad, the (10,1)
    # facing cells are two rides away — the first ride is re-firing the pad under us.
    ring = {(9, 1), (11, 1), (10, 0), (10, 2)}
    io = RoadIO({"maps": {"178": m}}, (178, 11, 11), arrive=arrive)
    assert road.pad_route({"maps": {"178": m}}, pairs, 178, (11, 11), ring, bodies) == [(11, 11), (5, 3)]
    assert road.ride_pad(io, {"maps": {"178": m}}, pairs, 178, ring, rides=4) is True
    assert qm.read_pos(io)[1:] in ring


def test_pad_route_can_target_a_warp_tile_itself():
    """A gym's exit mat is a warp tile, and `walkable` calls every warp a wall — so a route TO one
    is unreachable by construction unless the target stays open. Badge 6 was won at (9,9) behind
    Sabrina's pads and the next leg burned its whole budget re-trying the mat at (8,17)."""
    # Pad (2,0) lands on pad (5,0); (6,0) is the exit door to map 9. Walking east from (0,0)
    # is stopped by the pad at (2,0), exactly as `walk` blocks door tiles.
    truth = {"maps": {"1": _map(["1" * 7], warps=[[2, 0, 1, 1], [5, 0, 1, 0], [6, 0, 9, 0]])}}
    assert road.pad_route(truth, set(), 1, (0, 0), {(6, 0)}) == [(2, 0)]  # ride, then step to it
    assert road.pad_route(truth, set(), 1, (5, 0), {(6, 0)}) == []  # already beside the door
    assert road.pad_route(truth, set(), 1, (0, 0), {(1, 0)}) == []  # a plain walk reaches it


def test_a_walk_that_starts_on_a_door_can_still_plan():
    """Arriving through a door leaves us standing ON it. Blocking every warp tile then makes the
    start cell a wall and no plan exists — the bug behind 'could not step off the warp mat' on
    Silph 3F, the Center exit, and the Safari Zone's arrival pad."""
    truth = {"maps": {"1": _map(["1111"], warps=[[0, 0, 9, 0]])}}
    io = RoadIO(truth, (1, 0, 0))  # standing on the door at (0,0)
    assert road.walk(io, truth, set(), 1, {(3, 0)}) is True
    assert (io.mem[qm.ADDR_X], io.mem[qm.ADDR_Y]) == (3, 0)


def _silph_like():
    """A floor with two pockets: the left half walks, the right half is entered only by its pad."""
    return {
        "maps": {
            "5": _map(["1111011"], warps=[[2, 0, 7, 0], [6, 0, 7, 1]]),
            "7": _map(["111"], warps=[[0, 0, 5, 0], [2, 0, 5, 1]]),
        }
    }


def test_rides_to_names_every_door_that_lands_where_a_target_is_walkable():
    """The cross-floor question a gated building actually poses: not 'which pad is beside the
    target' but 'which door, anywhere, lands somewhere that can reach it'."""
    truth = _silph_like()
    rides = road.rides_to(truth, set(), 5, {(6, 0)})
    assert rides, "no door found for a cell only its own pad reaches"
    assert all(set(r) == {"from_map", "door", "lands", "hops"} for r in rides)
    assert any(r["from_map"] == 7 and r["lands"] == (6, 0) for r in rides)
    assert rides == sorted(rides, key=lambda r: (r["hops"], r["from_map"], r["door"]))


def test_rides_to_is_empty_for_a_map_we_do_not_model():
    assert road.rides_to({"maps": {}}, set(), 404, {(0, 0)}) == []


def test_pads_reaching_skips_a_pad_that_is_itself_the_target():
    truth = {"maps": {"1": _map(["111"], warps=[[1, 0, 9, 0]])}}
    assert road.pads_reaching(truth, set(), 1, {(1, 0)}) == []


def test_pad_land_reads_the_landing_out_of_the_maps_own_warp_list():
    truth = {"maps": {"1": _map(["1111"], warps=[[0, 0, 1, 1], [3, 0, 1, 0]])}}
    m = truth["maps"]["1"]
    assert road.pad_land(truth, 1, m["warps"][0]) == (3, 0)  # same-map pad: index into its own list
    assert road.pad_land(truth, 1, [0, 0, 9, 0]) is None  # a door to another map is not a pad
    assert road.pad_land(truth, 404, m["warps"][0]) is None  # a map we do not model
    assert road.pad_land(truth, 1, [0, 0, 1, 99]) is None  # an index its warp list does not have


def test_pad_route_returns_none_for_a_map_we_do_not_model():
    assert road.pad_route({"maps": {}}, set(), 404, (0, 0), {(1, 1)}) is None
    assert road.pad_route({"maps": {}}, set(), 404, (0, 0), set()) is None


# --------------------------------------------------------------------------- surf_cross


class SurfIO:
    """A water crossing reduced to its mechanism: the outward step is refused while SURF is not
    armed, and lands on the far map once surf_cross arms it and re-steps. ``instant`` models the
    beach-to-beach walk that needs no water at all; ``solid`` a tile refused even surﬁng;
    ``battle`` a fight opening on the first step."""

    def __init__(self, cur, dest, solid=False, instant=False, battle=False, wallow=False):
        self.mem = {qm.ADDR_MAP: cur, qm.ADDR_X: 1, qm.ADDR_Y: 0}
        self.face = "left"  # the west connection is the "left" outward key
        self.dest = dest
        self.solid = solid
        self.instant = instant
        self.battle = battle
        self.wallow = wallow
        self.arms = 0
        self.battled = 0

    def read(self, addr):
        if addr == qm.ADDR_IN_BATTLE:
            return 1 if self.battle else 0
        return self.mem.get(addr, 0)

    def wait(self, frames=30):
        pass

    def press(self, btn, hold=8, release=8):
        if btn != self.face or self.solid:
            return  # a solid refuses even with SURF; other keys do nothing
        if self.wallow:
            self.mem[qm.ADDR_X] = self.mem[qm.ADDR_X] + 1  # keeps drifting on our own water
            return
        if self.arms == 0 and not self.instant:
            return  # walking into water is refused until SURF is armed
        self.mem[qm.ADDR_MAP], self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y] = self.dest

    def arm(self):
        self.arms += 1
        return True


def _surf_truth(cur):
    return {
        "maps": {
            str(cur): _map(["01"], connections={"west": cur + 1}),  # (0,0) water, (1,0) land
            str(cur + 1): _map(["11"], connections={"east": cur}),
        }
    }


def test_surf_cross_arms_on_refusal_then_crosses():
    io = SurfIO(1, (2, 0, 0))
    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm) is True
    assert io.mem[qm.ADDR_MAP] == 2 and io.arms >= 1


def test_surf_cross_crosses_immediately_when_no_water_is_in_the_way():
    io = SurfIO(1, (2, 0, 0), instant=True)
    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm) is True
    assert io.arms == 0  # never needed SURF: the first step was already across
    assert io.mem[qm.ADDR_MAP] == 2


def test_surf_cross_reports_no_surf_when_the_lead_cannot_surf():
    io = SurfIO(1, (2, 0, 0))
    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=lambda: False) == "surfmoved-failed"
    assert io.mem[qm.ADDR_MAP] == 1  # still on our side of the water


def test_surf_cross_reports_stuck_when_a_solid_blocks_the_run():
    io = SurfIO(1, (2, 0, 0), solid=True)
    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm) == "stuck-on-edge"
    assert io.mem[qm.ADDR_MAP] == 1


def test_surf_cross_fights_a_battle_that_opens_on_the_first_step():
    io = SurfIO(1, (2, 0, 0), battle=True)

    def fight(fio):
        fio.battle, fio.battled = False, 1

    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm, battle=fight) is True
    assert io.battled == 1
    assert io.mem[qm.ADDR_MAP] == 2


def test_surf_cross_raises_when_neither_crossed_nor_stuck():
    # a surf that keeps drifting and never flips a map and never hits a solid: the finite bound
    # trips before it can hang. Real bounded water cannot do this; the bound is the anti-hang guard.
    io = SurfIO(1, (2, 0, 0), wallow=True)
    road.SURF_MAX_STEPS = 3
    try:
        with pytest.raises(RuntimeError):
            road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm)
    finally:
        road.SURF_MAX_STEPS = 200


class EncounterSurfIO(SurfIO):
    """Water that draws a wild encounter on the step, the way a real crossing does.

    The encounter CANCELS the step, so the position does not change — the exact reading that
    made surf_cross call open water "stuck-on-edge". The battle clears when it is fought, and
    the next step then lands on the far map.
    """

    def __init__(self, cur, dest, encounters=1):
        super().__init__(cur, dest)
        self.left = encounters
        self.arms = 1  # already surfing: this leg is mid-water, not at the shore

    def press(self, btn, hold=8, release=8):
        if btn != self.face:
            return
        if self.left:  # the step is eaten by the encounter, position unchanged
            self.left -= 1
            self.battle = True
            return
        self.mem[qm.ADDR_MAP], self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y] = self.dest


def test_a_wild_encounter_on_the_water_is_fought_not_read_as_a_wall():
    """A cancelled step and a refused step are the same bytes; only ADDR_IN_BATTLE tells them
    apart, and reading the wrong one reported open water as a dead end."""
    io = EncounterSurfIO(1, (2, 0, 0))

    def fight(_io):
        _io.battle = False
        _io.battled += 1

    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm, battle=fight) is True
    assert io.battled == 1
    assert io.mem[qm.ADDR_MAP] == 2


def test_repeated_encounters_do_not_exhaust_the_crossing():
    io = EncounterSurfIO(1, (2, 0, 0), encounters=3)

    def fight(_io):
        _io.battle = False
        _io.battled += 1

    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm, battle=fight) is True
    assert io.battled == 3


class ArmedEncounterSurfIO(SurfIO):
    """Refuses on land until SURF is armed, then draws the encounter on the ARMED step.

    That step is the one surf_cross judged with "if it still will not move, the tile is solid" —
    so an encounter there condemned a perfectly good route as a dead end.
    """

    def __init__(self, cur, dest):
        super().__init__(cur, dest)
        self.drew = False

    def press(self, btn, hold=8, release=8):
        if btn != self.face:
            return
        if self.arms == 0:
            return  # walking into water, refused until armed
        if not self.drew:
            self.drew = True
            self.battle = True  # the armed step is eaten by the encounter
            return
        self.mem[qm.ADDR_MAP], self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y] = self.dest


def test_an_encounter_on_the_armed_step_is_not_mistaken_for_a_solid_tile():
    io = ArmedEncounterSurfIO(1, (2, 0, 0))

    def fight(_io):
        _io.battle = False
        _io.battled += 1

    assert road.surf_cross(io, _surf_truth(1), set(), 1, 2, arm_surf=io.arm, battle=fight) is True
    assert io.battled == 1 and io.arms == 1  # armed once, fought once, crossed


class ShoreIO:
    """The 30->31 boundary shape: a water column at the west edge (x0), the player on the water
    beside it (x1), and only the rows in `open` open to the far map. Sliding up/down along the
    column is the SURF that carries it; the crossing press on a sealed row (cliff, or a far-map
    cell that does not open) is refused."""

    def __init__(self, height, open_rows, start_row=10):
        self.height = height
        self.open = set(open_rows)
        self.mem = {qm.ADDR_MAP: 1, qm.ADDR_X: 1, qm.ADDR_Y: start_row}
        self.battle = False
        self.battled = 0
        self.left_encounters = 0
        self.arms = 0
        self.arm_ok = True

    def read(self, addr):
        if addr == qm.ADDR_IN_BATTLE:
            return 1 if self.battle else 0
        return self.mem.get(addr, 0)

    def wait(self, frames=30):
        pass

    def press(self, btn, hold=8, release=8):
        x, y = self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y]
        if btn in ("down", "up"):
            ny = y + 1 if btn == "down" else y - 1
            if not 0 <= ny < self.height:
                return
            if self.left_encounters:  # a wild draws on an accepted step and cancels it
                self.left_encounters -= 1
                self.battle = True
                return
            self.mem[qm.ADDR_Y] = ny
            return
        if btn != "left" or x != 1 or y not in self.open:
            return  # a sealed row refuses even mid-surf
        self.mem[qm.ADDR_MAP] = 2

    def arm(self):
        self.arms += 1
        return self.arm_ok


def _shore_truth(height):
    return {
        "maps": {
            "1": _map(["01"] * height, connections={"west": 2}),
            "2": _map(["10"] * height, connections={"east": 1}),
        }
    }


def test_shunt_finds_the_open_band_far_down_the_shore():
    # the 30->31 measurement: approach row 10 refuses, the edge opens at rows 40..52 -
    # thirty rows of SURF between the refusal and the crossing
    io = ShoreIO(height=54, open_rows=range(40, 53))
    assert road.surf_cross(io, _shore_truth(54), set(), 1, 2, arm_surf=io.arm) is True
    assert io.mem[qm.ADDR_MAP] == 2 and io.mem[qm.ADDR_Y] in range(40, 53)


def test_shunt_finds_the_open_band_up_the_shore():
    io = ShoreIO(height=12, open_rows={4}, start_row=10)
    assert road.surf_cross(io, _shore_truth(12), set(), 1, 2, arm_surf=io.arm) is True
    assert io.mem[qm.ADDR_MAP] == 2 and io.mem[qm.ADDR_Y] == 4


def test_shunt_keeps_the_no_surf_verdict_when_the_shore_cannot_slide():
    # arm refuses AND the slide refuses (not surfing at all): the caller's original verdict
    # stands, exactly as before the shunt existed
    inner = ShoreIO(height=54, open_rows=range(40, 53))

    class WalkerShore:
        """A walker's shore: every slide press is refused (no SURF to carry the water)."""

        def __init__(self, inner):
            self.inner = inner
            for key in ("height", "read", "wait", "arm"):
                setattr(self, key, getattr(inner, key))

        def press(self, btn, hold=8, release=8):
            if btn in ("down", "up"):
                return  # walking into the shore: refused
            self.inner.press(btn, hold=hold, release=release)

    walker = WalkerShore(inner)
    assert road.surf_cross(walker, _shore_truth(54), set(), 1, 2, arm_surf=lambda: False) == "surfmoved-failed"


def test_shunt_keeps_the_stuck_verdict_when_the_shore_is_sealed_both_ways():
    io = ShoreIO(height=12, open_rows=())  # no row opens the crossing
    assert road.surf_cross(io, _shore_truth(12), set(), 1, 2, arm_surf=io.arm) == "stuck-on-edge"
    assert io.mem[qm.ADDR_MAP] == 1  # never left the near map


def test_wilds_on_shunt_steps_are_fought_not_read_as_a_sealed_shore():
    # a cancelled step and a refused step are the same bytes; the sealed-shore break is the
    # same error the encounter tests fixed for the crossing step, moved one slide over
    io = ShoreIO(height=12, open_rows={6}, start_row=4)
    io.left_encounters = 2

    def fight(_io):
        _io.battle = False
        _io.battled += 1

    assert road.surf_cross(io, _shore_truth(12), set(), 1, 2, arm_surf=io.arm, battle=fight) is True
    assert io.battled == 2 and io.mem[qm.ADDR_MAP] == 2 and io.mem[qm.ADDR_Y] == 6


# --------------------------------------------------------------------- the water model / route


def _water_map(rows, connections=None):
    """A map whose tile grid is given row by row as hex pairs."""
    return {
        "width": len(rows[0]) // 2,
        "height": len(rows),
        "tileset": 0,
        "tiles": list(rows),
        "grid": ["1" * (len(rows[0]) // 2) for _ in rows],
        "sprites": [],
        "warps": [],
        "connections": connections or {},
    }


def test_the_water_model_reads_the_grid_when_there_is_one():
    """Tiles present: the model answers from the cartridge. Absent (fake truth): it proposes
    everything and lets the game's refusals be the authority."""
    m = _water_map(["1400", "1411"])
    assert road._water_model(m, 0, 0) is True  # 0x14, water
    assert road._water_model(m, 1, 0) is False  # 0x00, not water
    assert road._water_model(m, 1, 1) is True  # 0x11, the shallows class
    assert road._water_model({"tiles": None}, 9, 9) is True


def test_water_reach_refuses_to_start_outside_the_map():
    m = _water_map(["1414", "1414"])
    assert road._water_reach(m, 9, 9, set()) == {}
    assert road._water_reach(m, 0, 0, {(0, 0)}) == {}


def test_water_reach_bfs_stands_on_the_start_unconditionally():
    """The shore left us here, so the start cell stands even if the model dislikes it."""
    m = _water_map(["0014", "1414"])
    prev = road._water_reach(m, 0, 0, set())  # (0,0) is 0x00, not water, but it is where we are
    assert (0, 0) in prev and prev[(0, 0)] is None
    assert (0, 1) in prev  # reached downward through water


def test_a_crossing_with_no_side_column_is_not_attempted():
    """north/south connections have no edge column, so this router has nothing to aim at."""
    truth = {"maps": {"1": _water_map(["1414"])}}
    assert road._water_cross(None, truth, 1, 2, "up", None) is None


class WaterIO:
    """Sea that opens only on a chosen row: stepping the connection direction anywhere else is
    refused, and one wild encounter interrupts the first press."""

    def __init__(self, cur, dest, open_row, start=(0, 0), wilds=0):
        self.mem = {qm.ADDR_MAP: cur, qm.ADDR_X: start[0], qm.ADDR_Y: start[1]}
        self.cur, self.dest, self.open_row = cur, dest, open_row
        self.wilds = wilds
        self.battle = False
        self.battled = 0

    def read(self, addr):
        if addr == qm.ADDR_IN_BATTLE:
            return 1 if self.battle else 0
        return self.mem.get(addr, 0)

    def wait(self, frames=30):
        pass

    def press(self, btn, hold=8, release=8):
        if self.wilds:
            self.wilds -= 1
            self.battle = True
            return  # the step is cancelled by the encounter
        dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[btn]
        x, y = self.mem[qm.ADDR_X] + dx, self.mem[qm.ADDR_Y] + dy
        if x < 0:  # stepping off the west edge: only the open row crosses
            if y == self.open_row:
                self.mem[qm.ADDR_MAP] = self.dest
            return
        if 0 <= x < 2 and 0 <= y < 4:
            self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y] = x, y


def test_the_route_walks_the_shore_until_a_row_opens():
    """The opening band can sit far from the approach row (measured: 30 rows on 30->31), so a
    dead row is remembered and the next one tried rather than the crossing being abandoned."""
    truth = {"maps": {"1": _water_map(["1414"] * 4)}}
    io = WaterIO(1, 2, open_row=3)
    assert road._water_cross(io, truth, 1, 2, "left", _default_battle_noop) is True
    assert io.mem[qm.ADDR_MAP] == 2


def _default_battle_noop(io):
    io.battle = False
    io.battled += 1


def test_a_wild_on_the_way_is_fought_and_the_step_retried():
    truth = {"maps": {"1": _water_map(["1414"] * 4)}}
    io = WaterIO(1, 2, open_row=0, wilds=1)
    assert road._water_cross(io, truth, 1, 2, "left", _default_battle_noop) is True
    assert io.battled == 1


def test_landing_on_a_third_map_is_reported_as_a_detour():
    truth = {"maps": {"1": _water_map(["1414"] * 4)}}
    io = WaterIO(1, 99, open_row=0)  # the edge leads somewhere that is not the goal
    assert road._water_cross(io, truth, 1, 2, "left", _default_battle_noop) == "detoured"


def test_a_sea_that_never_opens_reports_nothing_rather_than_spinning():
    truth = {"maps": {"1": _water_map(["1414"] * 4)}}
    io = WaterIO(1, 2, open_row=99)  # no row crosses
    assert road._water_cross(io, truth, 1, 2, "left", _default_battle_noop) is None


# ------------------------------------------------------------------------ counters


def test_a_counter_body_is_talked_to_from_two_tiles_away():
    """Measured on two different counters: the Center nurse at (3,1) is talked to from (3,3)
    facing up, and the BIKE SHOP clerk at (6,2) from (4,2) facing right."""
    stands = dict(road.counter_stands((6, 2)))
    assert stands[(4, 2)] == "right"
    assert stands[(8, 2)] == "left"
    nurse = dict(road.counter_stands((3, 1)))
    assert nurse[(3, 3)] == "up"  # exactly what center_counter hard-codes


def test_every_counter_stand_faces_back_at_the_body():
    """A stand cell that faces away is not a way to talk to anything."""
    body = (6, 2)
    step = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    for (cx, cy), face in road.counter_stands(body):
        dx, dy = step[face]
        # walking COUNTER_SPAN steps in the facing direction arrives at the body
        assert (cx + dx * road.COUNTER_SPAN, cy + dy * road.COUNTER_SPAN) == body


# --------------------------------------------------------------------------- shore_stand


def _tiled(grid, tiles, **kw):
    """A map whose tile model is spelled out: 'w' water (0x14), anything else land (0x03)."""
    m = _map(grid, **kw)
    m["tiles"] = ["".join("14" if c == "w" else "03" for c in row) for row in tiles]
    return m


def _strip_truth():
    # Route 19's shape in miniature: a land strip on row 0 that the walker can pace west along
    # (its west end is a fence, x=0 solid), and open water on rows 1..2 that reaches the west
    # edge. Standing at (3,0) there is no water beside us; (1,0)..(3,0) all touch it below.
    grid = ["0111", "0111", "1111", "1111"]
    tiles = ["....", "....", "wwww", "wwww"]
    return {"maps": {"1": _tiled(grid, tiles, connections={"west": 2}), "2": _map(["1"] * 4, connections={"east": 1})}}


def test_shore_stand_names_the_nearest_land_cell_that_touches_edge_reaching_water():
    truth = _strip_truth()
    assert road.shore_stand(truth, PAIRS, 1, 2, (3, 0)) == ((3, 1), "down")
    assert road.shore_stand(truth, PAIRS, 1, 2, (3, 1)) is None  # already beside the water


def test_shore_stand_is_none_without_a_tile_model_or_without_edge_water():
    assert road.shore_stand(_surf_truth(1), PAIRS, 1, 2, (1, 0)) is None
    landlocked = {"maps": {"1": _tiled(["111", "111"], ["...", ".w."], connections={"west": 2}), "2": _map(["1"] * 2)}}
    assert road.shore_stand(landlocked, PAIRS, 1, 2, (2, 0)) is None


class StripIO(RoadIO):
    """Route 19 in miniature: the d-pad walks the land strip, water refuses until SURF is armed
    (which lands the player on the water cell they face), and only x=0 on a water row crosses."""

    def __init__(self, truth):
        super().__init__(truth, (1, 3, 0))
        self.arms = 0
        self.arm_at = None
        self.armed = False

    def press(self, btn, hold=8, release=8):
        self.pressed.append(btn)
        mp, x, y = qm.read_pos(self)
        if btn not in ("up", "down", "left", "right"):
            return
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[btn]
        nx, ny = x + dx, y + dy
        self.face = btn
        if btn == "left" and x == 0 and y > 1:
            self._tp((2, 0, y))
            return
        m = self.truth["maps"]["1"]
        if not (0 <= nx < m["width"] and 0 <= ny < m["height"]) or m["grid"][ny][nx] != "1":
            return
        if ny > 1 and not self.armed:
            return  # water refuses a walker
        self._tp((1, nx, ny))

    def arm(self):
        self.arms += 1
        self.arm_at = qm.read_pos(self)[1:]
        self.arm_face = self.face
        if self.face != "down":
            return False  # "There's no place to get off!" -- not facing water
        self.armed = True
        self._tp((1, self.mem[qm.ADDR_X], 2))  # SURF animates onto the tile faced
        return True


def test_surf_cross_walks_to_the_shore_and_arms_facing_the_water():
    io = StripIO(_strip_truth())
    assert road.surf_cross(io, io.truth, PAIRS, 1, 2, arm_surf=io.arm) is True
    assert io.arms == 1 and io.arm_at == (3, 1) and io.arm_face == "down"
    assert qm.read_pos(io)[0] == 2


# --------------------------------------------------------------------------- reachable hops ledges


def _ledge_truth(tileset=0):
    # Route 19's beach in miniature: row 0 is land (tile 0x39), row 1 is a ledge (0x37, solid
    # in the grid), row 2 is land again. The only way down is the hop the ROM's LedgeTiles allow.
    m = _map(["111", "000", "111"])
    m["tileset"] = tileset
    m["tiles"] = ["393939", "373737", "393939"]
    return {"maps": {"1": m}, "ledges": [["down", 0x39, 0x37]]}


def test_reachable_hops_a_ledge_down_but_never_up():
    below = road.reachable(_ledge_truth(), PAIRS, 1, (1, 0))
    assert (1, 2) in below and (1, 1) not in below  # landed past the ledge; never stood on it
    assert road.reachable(_ledge_truth(), PAIRS, 1, (1, 2)) == {(0, 2), (1, 2), (2, 2)}  # no way back up


def test_reachable_hops_only_on_the_overworld_tileset():
    assert road.reachable(_ledge_truth(tileset=3), PAIRS, 1, (1, 0)) == {(0, 0), (1, 0), (2, 0)}


def test_reachable_does_not_hop_over_a_blocked_ledge_or_onto_a_blocked_landing():
    ledge_row = {(0, 1), (1, 1), (2, 1)}
    landing_row = {(0, 2), (1, 2), (2, 2)}
    assert (1, 2) not in road.reachable(_ledge_truth(), PAIRS, 1, (1, 0), blocked=ledge_row)
    assert (1, 2) not in road.reachable(_ledge_truth(), PAIRS, 1, (1, 0), blocked=landing_row)


def test_surf_cross_walks_to_the_shore_before_arming_and_reports_when_it_cannot_arm():
    """Arming where the straight line stopped answers "There's no place to get off!".

    Route 19's beach is the measured case: standing on the land strip with no water beside us,
    the outward key carries us *along* the land, so SURF is armed facing dry ground. shore_stand
    names the nearest cell that actually touches edge-reaching water; only there is arming
    meaningful. When the party still cannot surf from that cell, the leg says so rather than
    pacing the strip until its step budget runs out.
    """
    truth = _strip_truth()
    io = SurfIO(1, (2, 0, 0))
    io.mem[qm.ADDR_X], io.mem[qm.ADDR_Y] = 3, 0  # on the strip, no water adjacent
    assert road.shore_stand(truth, PAIRS, 1, 2, (3, 0)) is not None  # the branch under test
    assert road.surf_cross(io, truth, PAIRS, 1, 2, arm_surf=lambda: False) == "surfmoved-failed"
    assert io.mem[qm.ADDR_MAP] == 1  # never left our side


def test_the_water_model_calls_anything_off_the_map_land():
    """Route 22 -> 23 crashed on int('') when the facing cell lay past the row's end."""
    m = {"width": 2, "height": 2, "tiles": ["1400", "0011"]}
    assert road._water_model(m, 2, 0) is False
    assert road._water_model(m, -1, 0) is False
    assert road._water_model(m, 0, 2) is False
    assert road._water_model(m, 0, -1) is False
    assert road._water_model(m, 0, 0) is True


def test_edge_cells_without_a_connection_is_empty_not_an_error():
    truth = {"maps": {"1": _map(["111", "111"], connections={"north": 2}), "2": _map(["111"])}}
    assert road.edge_cells(truth, 1, 2) == ({(0, 0), (1, 0), (2, 0)}, "up")
    assert road.edge_cells(truth, 1, 9) == (set(), "")  # 17->10 on a reroute raised StopIteration here


def test_a_door_is_reachable_and_a_steppable_target_but_never_a_corridor():
    """The Elite Four rooms' door tiles (4,0)/(5,0) are warps the collision grid calls solid."""
    import rom_truth as rt

    m = _map(["000", "111", "111"], warps=[[1, 0, 9, 0]])
    truth = {"maps": {"1": m}}
    region = road.reachable(truth, set(), 1, (1, 2))
    assert (1, 0) in region and (0, 0) not in region and (2, 0) not in region
    assert rt.path_on_map(truth, set(), 1, (1, 2), {(1, 0)}) == [(1, 2), (1, 1), (1, 0)]
    assert rt.path_on_map(truth, set(), 1, (1, 2), {(0, 0)}) is None  # not a warp: still solid
    # the door is a terminal cell: nothing routes *through* it to the far side
    m2 = _map(["101", "000", "111"], warps=[[1, 1, 9, 0]])
    assert rt.path_on_map({"maps": {"1": m2}}, set(), 1, (1, 2), {(1, 0)}) is None


def test_shore_stand_and_surf_cross_bail_when_the_pair_has_no_side():
    assert road.shore_stand(_strip_truth(), PAIRS, 1, 404, (3, 0)) is None  # a tiled map, no side to 404
    io = SurfIO(1, (2, 0, 0))
    assert road.surf_cross(io, _surf_truth(1), set(), 1, 404, arm_surf=io.arm) == "no-route"
    assert io.mem[qm.ADDR_MAP] == 1


class ChannelIO:
    """One map with water in it: presses move over land; a press onto water is refused until SURF
    is armed and then moves; ``solid`` cells refuse even surfing (the model was wrong there)."""

    def __init__(self, truth, start, solid=(), battle_on=None):
        self.truth = truth
        self.mem = {qm.ADDR_MAP: start[0], qm.ADDR_X: start[1], qm.ADDR_Y: start[2]}
        self.solid = set(solid)
        self.battle_on = set(battle_on or ())  # cells whose first entry opens a wild fight
        self.arms = 0
        self.fought = 0
        self._battle = False

    def read(self, addr):
        if addr == qm.ADDR_IN_BATTLE:
            return 1 if self._battle else 0
        return self.mem.get(addr, 0)

    def wait(self, frames=30):
        pass

    def arm(self):
        self.arms += 1
        return True

    def fight(self, io=None):
        self.fought += 1
        self._battle = False

    def press(self, btn, hold=8, release=8):
        d = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.get(btn)
        if d is None:
            return
        m = self.truth["maps"][str(self.mem[qm.ADDR_MAP])]
        x, y = self.mem[qm.ADDR_X] + d[0], self.mem[qm.ADDR_Y] + d[1]
        if not (0 <= x < m["width"] and 0 <= y < m["height"]) or (x, y) in self.solid:
            return
        if road._water_model(m, x, y) and self.arms == 0:
            return  # water refuses a walker
        if not road._water_model(m, x, y) and m["grid"][y][x] != "1":
            return
        if (x, y) in self.battle_on:
            self.battle_on.discard((x, y))
            self._battle = True  # the step is cancelled by the encounter
            return
        self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y] = x, y


def _channel_truth():
    # Route 23 in miniature: land on the left, a two-wide channel, land on the right where the
    # warp (4,1) sits. Water is solid in the extracted grid, exactly as on map 34.
    grid = ["10011", "10011", "10011"]
    tiles = [".ww..", ".ww..", ".ww.."]
    return {"maps": {"34": _tiled(grid, tiles, warps=[[4, 1, 108, 0]])}}


def test_water_route_crosses_the_channel_to_the_land_that_reaches_the_target():
    truth = _channel_truth()
    plan = road.water_route(truth, set(), 34, (0, 2), {(4, 1)})
    assert plan is not None
    stand, face_in, path, landing, face_out = plan
    assert stand[0] == 0 and face_in == "right"
    assert path[0] == (1, stand[1]) and all(road._water_model(truth["maps"]["34"], *c) for c in path)
    assert landing[0] == 3 and face_out == "right"
    assert road.water_route(truth, set(), 34, (3, 0), {(4, 1)}) is None  # a walk already reaches it
    assert road.water_route({"maps": {"34": _map(["111"])}}, set(), 34, (0, 0), {(2, 0)}) is None  # no tile model


def test_surf_route_walks_faces_arms_and_lands():
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 0, 1), battle_on={(2, 1)})
    r = road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm, battle=io.fight)
    assert r is True
    assert io.arms == 1 and io.fought == 1  # the wild on (2,1) was fought and the step re-pressed
    assert (io.mem[qm.ADDR_X], io.mem[qm.ADDR_Y]) == (3, 1)


def test_surf_route_reports_what_stopped_it():
    truth = _channel_truth()
    far = ChannelIO(truth, (34, 3, 0))
    assert road.surf_route(far, truth, set(), 34, {(4, 1)}, arm_surf=lambda: True) == "no-route"
    io = ChannelIO(truth, (34, 0, 2))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=lambda: False) == "surfmoved-failed"
    io = ChannelIO(truth, (34, 0, 2), solid={(2, 2), (2, 1), (2, 0)})  # the far column refuses surfing
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) == "surfmoved-failed"


def test_live_sprites_keeps_slots_and_clips_to_the_map():
    class IO:
        def read(self, addr):
            slot = (
                (addr - road.SPRITE_STATE_BASE) // 0x10
                if addr < road.SPRITE_DATA_BASE
                else (addr - road.SPRITE_DATA_BASE) // 0x10
            )
            if addr < road.SPRITE_DATA_BASE:
                return 1 if slot in (1, 2) else 0
            off = addr & 0xF
            return {1: {4: 5 + 4, 5: 3 + 4}, 2: {4: 40 + 4, 5: 1 + 4}}[slot][off]

    assert road.live_sprites(IO()) == {1: (3, 5), 2: (1, 40)}
    assert road.live_sprites(IO(), (10, 10)) == {1: (3, 5)}


def test_surf_route_edge_cases_walk_failure_vertical_keys_and_a_detour(monkeypatch):
    assert road._key_between((0, 0), (0, 1)) == "down" and road._key_between((0, 1), (0, 0)) == "up"
    truth = _channel_truth()
    monkeypatch.setattr(road, "walk", lambda *a, **kw: "no-path")
    io = ChannelIO(truth, (34, 0, 1))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) == "no-path"
    monkeypatch.undo()

    class FlipIO(ChannelIO):
        def press(self, btn, hold=8, release=8):
            super().press(btn, hold, release)
            if (self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y]) == (2, 1):
                self.mem[qm.ADDR_MAP] = 99  # a current carried us onto another map mid-water

    io = FlipIO(truth, (34, 0, 1))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) == "detoured"


def test_surf_route_skips_the_cell_the_surf_confirmation_already_carried_us_onto():
    truth = _channel_truth()

    class AutoStepIO(ChannelIO):
        def arm(self):
            self.arms += 1
            self.mem[qm.ADDR_X] += 1  # "use SURF?" -> yes -> the player is already on the water
            return True

    io = AutoStepIO(truth, (34, 0, 1))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) is True
    assert (io.mem[qm.ADDR_X], io.mem[qm.ADDR_Y]) == (3, 1)


def test_surf_route_re_presses_a_step_a_lingering_box_swallowed():
    truth = _channel_truth()

    class BoxIO(ChannelIO):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.eaten = 0

        def press(self, btn, hold=8, release=8):
            if btn in ("up", "down", "left", "right") and self.arms and self.eaten < 2:
                self.eaten += 1  # the "got on" box is still up: the press advances text, not the player
                return
            super().press(btn, hold, release)

    io = BoxIO(truth, (34, 0, 1))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) is True
    assert io.eaten == 2 and (io.mem[qm.ADDR_X], io.mem[qm.ADDR_Y]) == (3, 1)


def test_surf_route_settles_what_arming_left_on_screen():
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 0, 1))
    closed = []
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm, settle=lambda: closed.append(1)) is True
    assert closed == [1]


def test_surf_route_replans_around_a_cell_the_game_refuses():
    """The model calls it water; a rock sits there (measured on map 34). One refusal, one replan."""
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 0, 1), solid={(2, 1)})  # the straight line across is blocked mid-channel
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) is True
    assert io.mem[qm.ADDR_X] == 3  # landed on the far bank by another row
    all_solid = ChannelIO(truth, (34, 0, 1), solid={(2, 0), (2, 1), (2, 2)})
    assert road.surf_route(all_solid, truth, set(), 34, {(4, 1)}, arm_surf=all_solid.arm) == "surfmoved-failed"


def test_surf_route_steps_in_itself_when_the_confirmation_did_not():
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 0, 1))  # arm() here never moves the player: the route must step in
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) is True
    stuck = ChannelIO(truth, (34, 0, 1), solid={(1, 1)})  # the very first water cell refuses
    assert road.surf_route(stuck, truth, set(), 34, {(4, 1)}, arm_surf=stuck.arm) == "surfmoved-failed"


def test_surf_route_mounts_through_the_rig_when_given_a_mount():
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 0, 1))
    mounted = []

    def mount(face):
        mounted.append(face)
        io.arms += 1
        io.mem[qm.ADDR_X] += 1  # surf_onto answers by position: we are on the water now
        return True

    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, mount=mount) is True
    assert mounted == ["right"]
    assert (
        road.surf_route(ChannelIO(truth, (34, 0, 1)), truth, set(), 34, {(4, 1)}, mount=lambda f: False)
        == "surfmoved-failed"
    )
    assert (
        road.surf_route(ChannelIO(truth, (34, 0, 1)), truth, set(), 34, {(4, 1)}) == "surfmoved-failed"
    )  # nothing to arm with


def test_surf_route_replans_past_a_landing_the_game_refuses_and_gives_up_when_the_water_runs_out():
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 0, 1), solid={(3, 1)})  # "There's no place to get off!" on the straight landing
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) is True
    assert io.mem[qm.ADDR_X] == 3 and io.mem[qm.ADDR_Y] != 1
    io = ChannelIO(truth, (34, 0, 1), solid={(3, 0), (3, 1), (3, 2)})
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm, replans=2) == "surfmoved-failed"

    class DriftIO(ChannelIO):
        def press(self, btn, hold=8, release=8):
            super().press(btn, hold, release)
            if self.arms and self.mem[qm.ADDR_X] == 1:
                self.mem[qm.ADDR_MAP] = 99  # a current took us off the map the moment we were afloat

    io = DriftIO(truth, (34, 0, 1))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm) == "detoured"


def test_surf_route_continues_when_already_afloat_and_says_what_it_does():
    truth = _channel_truth()
    io = ChannelIO(truth, (34, 1, 1))  # a previous route left us on the water
    io.arms = 1
    said = []
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=lambda: False, log=said.append) is True
    assert io.mem[qm.ADDR_X] == 3 and any("afloat" in s for s in said)
    io = ChannelIO(truth, (34, 1, 1))
    io.arms = 1
    no_bank = road.surf_route(io, truth, set(), 34, {(0, 9)}, arm_surf=lambda: False)
    assert no_bank == "surfmoved-failed"  # no bank reaches that cell
    assert road.surf_route(io, truth, set(), 34, set(), arm_surf=lambda: False) == "no-route"  # nowhere to go


def test_surf_route_turns_a_guards_page_before_calling_a_step_refused():
    truth = _channel_truth()

    class GuardIO(ChannelIO):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.page_up = False
            self.pages = 0

        def press(self, btn, hold=8, release=8):
            if self.page_up:
                return  # every press is eaten while the guard talks
            if btn == "right" and self.arms and not self.pages and (self.mem[qm.ADDR_X], self.mem[qm.ADDR_Y]) == (1, 1):
                self.page_up = True  # stepping toward (2,1) trips the badge check, once
                return
            super().press(btn, hold, release)

        def dismiss(self):
            self.pages += 1
            self.page_up = False

    io = GuardIO(truth, (34, 0, 1))
    assert road.surf_route(io, truth, set(), 34, {(4, 1)}, arm_surf=io.arm, dismiss=io.dismiss) is True
    assert io.pages == 1 and io.mem[qm.ADDR_X] == 3


def test_surf_route_needs_a_tile_model_before_it_believes_it_is_afloat():
    """Indoors (maps 45, 171, 236, measured): no tiles, so the water model says everything is water."""
    truth = {"maps": {"45": _map(["111", "111"])}}
    io = ChannelIO(truth, (45, 0, 0))
    assert road.surf_route(io, truth, set(), 45, {(2, 1)}, arm_surf=io.arm) == "no-route"
    assert io.arms == 0
