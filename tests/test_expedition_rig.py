"""The Rig's sink, tested without booting a cartridge.

A run that does not emit is unminable, so the sink's shape is doctrine: one file per UTC date
under ``data/telemetry/game/``, one JSON object per line, every line carrying the run_id that
correlates it to ``runs/<run_id>/``.
"""

import json
from datetime import datetime, timezone

import expedition_rig as rig
import quartermaster as qm


def test_the_sink_is_one_file_per_utc_date(tmp_path):
    when = datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)
    assert rig.telemetry_path(when, root=tmp_path).name == "2026-08-31.jsonl"


def test_events_append_as_jsonl_and_carry_the_run_id(tmp_path):
    rig.emit_event("run-abc", "supervisor.leg_start", {"goal": 157}, root=tmp_path)
    rig.emit_event("run-abc", "supervisor.leg_end", {"outcome": "arrived"}, root=tmp_path)
    lines = [json.loads(x) for x in rig.telemetry_path(root=tmp_path).read_text().splitlines()]
    assert [x["event"] for x in lines] == ["supervisor.leg_start", "supervisor.leg_end"]
    assert {x["run_id"] for x in lines} == {"run-abc"}
    assert lines[0]["source"] == "expedition" and lines[0]["goal"] == 157
    assert lines[1]["ts"]  # every line is stamped; the sink is time-ordered by append


def test_the_sink_directory_is_created_on_first_write(tmp_path):
    root = tmp_path / "not" / "yet"
    rig.emit_event("run-xyz", "supervisor.hop_failed", {"failure": "no-path"}, root=root)
    assert rig.telemetry_path(root=root).exists()


class FakeIO:
    """A world that swallows every step until `parked` rounds of A/B have been spent.

    Stepping onto a warp tile moves us to another map, exactly as the cartridge does — which is
    how the badge-6 leg warped itself back into the gym it had just left.
    """

    def __init__(self, rig, *, parked=0, walls=(), warp_to=None):
        self.rig = rig
        self.parked = parked
        self.walls = set(walls)
        self.warp_to = warp_to  # {(x, y): destination map id}
        self.presses: list[str] = []

    def press(self, button, hold=8, release=8):
        self.presses.append(button)
        if button in ("a", "b"):
            self.parked = max(0, self.parked - (1 if button == "b" else 0))
            return
        if self.parked or button in self.walls:
            return
        dx, dy = {"down": (0, 1), "up": (0, -1), "left": (-1, 0), "right": (1, 0)}[button]
        nx, ny = self.rig.mem[0xD362] + dx, self.rig.mem[0xD361] + dy
        self.rig.mem[0xD362], self.rig.mem[0xD361] = nx, ny
        if self.warp_to and (nx, ny) in self.warp_to:
            self.rig.mem[0xD35E] = self.warp_to[(nx, ny)]

    def wait(self, frames=30):
        pass


def _stub_rig(*, parked=0, walls=(), warps=(), warp_to=None, at=(4, 11)):
    """A Rig with only the pieces settle() touches — no cartridge, no PyBoy."""
    r = rig.Rig.__new__(rig.Rig)
    r.mem = {0xD35E: 157, 0xD362: at[0], 0xD361: at[1], rig.ADDR_BADGES: 0b11111, 0xD057: 0}
    r.truth = {"maps": {"157": {"warps": [[wx, wy, 7, 0] for wx, wy in warps]}}}
    r.io = FakeIO(r, parked=parked, walls=walls, warp_to=warp_to)
    r.ctl = r.io
    return r


def test_probe_step_moves_and_undoes_itself():
    r = _stub_rig()
    assert r.probe_step() is True
    assert r.pos() == (157, 4, 11)  # the probe left the world exactly where it found it
    assert r.io.presses[:2] == ["down", "up"]


def test_probe_step_reports_false_when_every_direction_is_swallowed():
    r = _stub_rig(parked=99)
    assert r.probe_step() is False


def test_settle_flushes_a_parked_textbox_and_proves_it_with_a_step():
    """BADGE5.state was banked on Koga's TM line and refused all four steps until flushed."""
    r = _stub_rig(parked=2)
    assert r.settle() is True
    assert "b" in r.io.presses  # A advances the pages, B closes what A opened
    assert r.pos() == (157, 4, 11)


def test_settle_gives_up_honestly_rather_than_claiming_a_flush():
    r = _stub_rig(parked=99)
    assert r.settle(max_rounds=3) is False


def test_the_probe_refuses_to_thread_a_door():
    """Measured: a baton banked one tile below Fuchsia gym's mat probed *up* and warped inside."""
    r = _stub_rig(at=(5, 28), warps=[(5, 27)], warp_to={(5, 27): 999})
    assert r.probe_step() is True
    assert r.pos()[0] == 157  # the probe proved input without going through the door
    assert "up" not in r.io.presses[:1]


def test_the_probe_uses_a_door_only_when_there_is_nothing_else():
    """A state wedged in a doorway still has to be able to prove it accepts input."""
    r = _stub_rig(at=(5, 28), warps=[(5, 27), (5, 29), (4, 28), (6, 28)], warp_to={(5, 29): 999})
    assert r.probe_step() is True
    assert r.pos()[0] == 999  # it went through, because every neighbour was a door


def test_a_last_resort_warp_says_so_out_loud(capsys):
    """Measured on door_check.state, banked right at Seafoam's own entrance: every plain
    floor tile refused (rock on three sides), the only open neighbour was the warp back
    outside, and probe_step took it silently. A caller checking only the boolean saw
    "input works" with no sign the map underneath had changed. This is that sign."""
    r = _stub_rig(at=(5, 28), warps=[(5, 27), (5, 29), (4, 28), (6, 28)], warp_to={(5, 29): 999})
    r.probe_step()
    assert "map 157 -> 999" in capsys.readouterr().out


def test_the_rig_points_at_this_repos_rom_and_baton_shelf():
    assert rig.ROM_DEFAULT.name == "pokemon_red.gb"
    assert rig.BATON_DIR.parts[-2:] == ("local_runs", "roster-bench")
    assert rig.TELEMETRY_DIR.parts[-2:] == ("telemetry", "game")


def test_settled_pos_rejects_a_torn_read_across_a_warp():
    """(234, 17, 11) on a map 16 tiles wide is the transition window, not a place."""
    r = _stub_rig(at=(17, 11))
    r.mem[0xD35E] = 234
    r.truth = {"maps": {"234": {"width": 16, "height": 18, "warps": []}}}
    calls = {"n": 0}

    def press(button, hold=8, release=8):  # the transition completes as the world ticks on
        calls["n"] += 1

    def wait(frames=30):
        r.mem[0xD362], r.mem[0xD361] = 13, 7
        r.mem[0xD35E] = 209

    r.io.press, r.io.wait = press, wait
    r.truth["maps"]["209"] = {"width": 26, "height": 18, "warps": []}
    assert r.settled_pos() == (209, 13, 7)


def test_settled_pos_returns_a_stable_in_bounds_read_unchanged():
    r = _stub_rig(at=(4, 11))
    r.truth = {"maps": {"157": {"width": 10, "height": 18, "warps": []}}}
    r.io.wait = lambda frames=30: None
    assert r.settled_pos() == (157, 4, 11)


def _bag_rig(bag, items=None):
    r = rig.Rig.__new__(rig.Rig)
    r.mem = {rig.ADDR_BAG_COUNT: len(bag)}
    for i, (item, qty) in enumerate(bag):
        r.mem[rig.ADDR_BAG_ITEMS + 2 * i] = item
        r.mem[rig.ADDR_BAG_ITEMS + 2 * i + 1] = qty
    r.truth = {"items": items or {"60": "FRESH WATER", "74": "LIFT KEY", "40": "RARE CANDY"}}
    r.run_id = "t"
    r.telemetry_root = None
    r.settle = lambda *a, **kw: True  # the real one presses buttons; nothing to press here
    return r


def test_the_bag_is_read_as_named_items_not_raw_ids():
    r = _bag_rig([(74, 1), (60, 6)])
    assert r.bag_named() == [("LIFT KEY", 1), ("FRESH WATER", 6)]
    assert r.item_name(999) == "#999"  # TMs live past the name list and keep their id


def test_the_bag_is_full_only_at_the_measured_slot_cap():
    assert _bag_rig([(4, 1)] * (rig.BAG_SLOTS - 1)).bag_full() is False
    assert _bag_rig([(4, 1)] * rig.BAG_SLOTS).bag_full() is True


def test_make_room_tosses_the_largest_stack(monkeypatch, tmp_path):
    """Quantity is the measured signal: key items are single copies, consumables come in stacks."""
    r = _bag_rig([(74, 1), (60, 6), (40, 2)])
    r.telemetry_root = tmp_path
    tossed = {}

    def toss(item):
        tossed["item"] = item
        return True

    r.toss_stack = toss
    assert r.make_room() is False  # never by default: the 2026-09-05 replays sold NUGGETs and TMs this way
    assert tossed == {}
    assert r.make_room(allow_toss=True) is True
    assert tossed["item"] == 60  # FRESH WATER x6, not the LIFT KEY and not RARE CANDY x2


def test_make_room_says_so_when_only_the_pc_can_help(tmp_path):
    r = _bag_rig([(74, 1), (60, 6)], items={"74": "LIFT KEY", "60": "FRESH WATER"})
    r.telemetry_root = tmp_path
    events = []
    r.emit = lambda event, **fields: events.append((event, fields))
    assert r.make_room() is False
    assert events == [("bag.full", {"slots": 2, "hint": "store_at_pc"})]


def test_make_room_refuses_when_every_slot_is_a_single_item(tmp_path):
    r = _bag_rig([(74, 1), (72, 1), (73, 1)])
    r.telemetry_root = tmp_path
    assert r.make_room() is False  # nothing here is expendable; say so rather than tossing a key


class LiftRig:
    """A lift car whose panel prints a scrolling floor list, like Silph's and the Hideout's."""

    def __init__(self, floors, target_row=0):
        self.floors = floors
        self.cursor = 0
        self.presses = []
        self.left = False

    def window_row(self, row):
        i = (row - 4) // 2
        return self.floors[i] if 0 <= i < len(self.floors) else ""


def test_the_floor_labels_are_read_off_the_panel_not_indexed():
    """Which floor sits at which index is exactly the sort of fact this project has been burned
    by recalling, so the label under the cursor is decoded from the window layer."""
    r = rig.Rig.__new__(rig.Rig)
    r.window_row = lambda row: {4: "1F", 6: "2F", 8: "3F"}.get(row, "")
    assert r.elevator_floors() == ["1F", "2F", "3F"]


def test_a_car_without_a_sign_is_reported_not_guessed(capsys):
    r = rig.Rig.__new__(rig.Rig)
    r.mem = {0xD35E: 236, 0xD362: 1, 0xD361: 2}
    r.truth = {"maps": {"236": {"warps": [], "signs": []}}}
    assert r.ride_elevator("5F") is False
    assert "no sign to use as a lift panel" in capsys.readouterr().out


def test_make_room_falls_back_to_a_tm_when_nothing_is_stacked(tmp_path):
    """Every slot a single item is not the same as nothing being expendable: TMs are named by
    the cartridge, we carry eight, and the game refuses to toss anything it considers a key."""
    r = _bag_rig([(74, 1), (72, 1), (207, 1)], items={"74": "LIFT KEY", "72": "SILPH SCOPE", "207": "TM07"})
    r.telemetry_root = tmp_path
    tried = []

    def toss(item):
        tried.append(item)
        return True

    r.toss_stack = toss
    assert r.make_room(allow_toss=True) is True
    assert tried == [207]  # the TM, never the LIFT KEY or the SILPH SCOPE


def test_make_room_moves_on_when_the_game_refuses_a_toss(tmp_path):
    r = _bag_rig([(207, 1), (210, 1)], items={"207": "TM07", "210": "TM10"})
    r.telemetry_root = tmp_path
    tried = []

    def toss(item):
        tried.append(item)
        return item == 210  # the first one will not go

    r.toss_stack = toss
    assert r.make_room(allow_toss=True) is True
    assert tried == [207, 210]


def test_make_room_still_refuses_a_bag_of_only_key_items(tmp_path):
    r = _bag_rig([(74, 1), (72, 1)], items={"74": "LIFT KEY", "72": "SILPH SCOPE"})
    r.telemetry_root = tmp_path
    assert r.make_room() is False


def test_text_from_returns_only_what_the_action_produced():
    r = rig.Rig.__new__(rig.Rig)
    state = {"text": "AAAAAAA got 750 for winning!"}
    r.dialogue = lambda: state["text"]
    assert r.text_from(lambda: None) == ""  # a sticky buffer is not this action's message
    assert r.text_from(lambda: state.update(text="Darn! It needs a CARD KEY!")) == "Darn! It needs a CARD KEY!"


class FieldMenuRig:
    """A field submenu in its measured screen layout: entry rows two apart (GYARADOS draws them
    at 10-16, MAX 3), STATS/SWITCH/CANCEL the fixed tail, the cursor row prefixed with the 'AAAA'
    glyph run, and a prompt that can splice onto an entry ('Choose a P SWITCH'). The base is a
    parameter of the screen, not of the menu - the code under test anchors off CANCEL, so this
    sits at row 4 to keep every row inside the 20-row window."""

    BASE = 4
    TAIL = ("STATS", "SWITCH", "CANCEL")

    def __init__(self, moves, cursor=0, prompt_row=None, prompt_text="Choose a P SWITCH"):
        self.entries = [m.strip().upper() for m in moves] + list(self.TAIL)
        self.cursor = cursor
        self.chosen = None
        self.prompt_row = prompt_row
        self.prompt_text = prompt_text

    def window_row(self, row):
        if self.prompt_row is not None and row == self.prompt_row:
            return self.prompt_text
        i = (row - self.BASE) // 2
        if row % 2 or not (0 <= i < len(self.entries)):
            return ""
        text = self.entries[i]
        return ("AAAA " + text) if i == self.cursor else text


class _Mem(dict):
    """CUR/MAX read live off the menu, like the RAM the rig reads in production."""

    def __init__(self, menu):
        super().__init__()
        self._menu = menu

    def __getitem__(self, key):
        if key == 0xCC26:
            return self._menu.cursor
        if key == 0xCC28:
            return len(self._menu.entries) - 1
        if key == rig.qm.ADDR_IN_BATTLE:
            return 0  # these menus open on a quiet field screen; use_field_move refuses to open START mid-battle
        return super().__getitem__(key)


class _Ctl:
    def __init__(self, menu):
        self._menu = menu
        self.presses = []

    def press(self, b):
        self.presses.append(b)
        m = self._menu
        if b == "down":
            m.cursor = min(m.cursor + 1, len(m.entries) - 1)
        elif b == "up":
            m.cursor = max(m.cursor - 1, 0)
        elif b == "a":
            m.chosen = m.entries[m.cursor]

    def wait(self, n=0):
        pass


def _menu_rig(menu):
    r = rig.Rig.__new__(rig.Rig)
    r.window_row = menu.window_row
    r.mem = _Mem(menu)
    r.ctl = _Ctl(menu)
    return r


def test_a_field_move_is_found_in_its_row_not_by_a_remembered_row_number():
    """Which move sits on which row depends on the mon, so the row is read, not assumed."""
    r = _menu_rig(FieldMenuRig(["FLY", "SURF", "STRENGTH", "CUT"]))
    assert r.field_moves() == ["FLY", "SURF", "STRENGTH", "CUT", "STATS", "SWITCH", "CANCEL"]


def test_the_surfer_surf_is_selected_even_on_the_garbled_cursor_row():
    """Measured on GYAARADOS: the cursor row decodes 'AAAA SURF', so startswith would miss it."""
    menu = FieldMenuRig(["SURF"], prompt_row=8)  # the prompt splicing onto SWITCH, as measured
    r = _menu_rig(menu)
    assert r.use_field_move("SURF") is True
    assert menu.chosen == "SURF"


def test_a_field_move_below_the_cursor_is_won_not_the_first_row():
    menu = FieldMenuRig(["STRENGTH", "SURF"])
    r = _menu_rig(menu)
    assert r.use_field_move("SURF") is True
    assert menu.chosen == "SURF"
    assert "down" in r.ctl.presses  # the cursor was walked onto SURF, not guessed at


def test_a_move_the_party_does_not_know_is_reported_not_guessed(capsys):
    r = _menu_rig(FieldMenuRig(["CUT", "FLASH"]))
    assert r.use_field_move("SURF") is False
    assert "no field move called 'SURF'" in capsys.readouterr().out


# --------------------------------------------------------------- the plain reads and delegations


def _reader_rig(mem=None, truth=None):
    r = rig.Rig.__new__(rig.Rig)
    r.mem = mem if mem is not None else {}
    r.truth = truth if truth is not None else {"maps": {}}
    r.pairs = set()
    r.io = object()
    return r


def test_badges_is_read_straight_from_ram():
    assert _reader_rig({rig.ADDR_BADGES: 0b11111}).badges() == 31


def test_party_is_decoded_from_the_struct_table():
    mem = {rig.ADDR_PARTY_COUNT: 1}
    base = rig.ADDR_PARTY_STRUCTS
    mem[base] = 1  # whatever species id 1 is in this ROM's internal order
    mem[base + 33] = 99
    mem[base + 1], mem[base + 2] = 1, 0x2C  # 300 hp, big-endian across two bytes
    species, level, hp = _reader_rig(mem).party()[0]
    assert (level, hp) == (99, 300) and isinstance(species, str)


def test_dialogue_survives_a_buffer_read_that_throws():
    r = _reader_rig()

    class Reader:
        def read_dialogue(self):
            raise RuntimeError("mid-redraw")

    r.mr = Reader()
    assert r.dialogue() == ""  # a text buffer mid-redraw is not a leg failure


def test_item_balls_come_from_the_extraction():
    truth = {"maps": {"9": {"sprites": [{"kind": "item", "x": 1, "y": 2}, {"kind": "npc", "x": 3, "y": 4}]}}}
    assert _reader_rig(truth=truth).item_balls(9) == [(1, 2)]
    assert _reader_rig(truth=truth).item_balls(404) == []


def test_warp_tiles_come_from_the_extraction():
    truth = {"maps": {"9": {"warps": [[1, 2, 3, 0], [4, 5, 6, 0]]}}}
    assert _reader_rig(truth=truth).warp_tiles(9) == {(1, 2), (4, 5)}


def test_settled_pos_gives_up_after_its_tries_rather_than_spinning():
    r = _stub_rig(at=(4, 11))
    r.truth = {"maps": {"157": {"width": 10, "height": 18, "warps": []}}}
    moves = iter(range(100))
    r.io.wait = lambda frames=30: r.mem.__setitem__(0xD362, next(moves))  # never settles
    assert r.settled_pos(tries=3)[0] == 157


def test_flush_text_reports_whether_the_buffer_actually_emptied():
    r = _stub_rig()
    r.dialogue = lambda: ""
    assert r.flush_text() is True
    r.dialogue = lambda: "still here"
    assert r.flush_text(tries=2) is False


def test_settle_resolves_a_battle_before_probing():
    r = _stub_rig(parked=1)
    r.mem[0xD057] = 1
    fought = []

    def battle():
        fought.append(True)
        r.mem[0xD057] = 0

    r.battle = battle
    assert r.settle() is True
    assert fought == [True]


def test_the_road_delegations_pass_the_battle_handler_through(monkeypatch):
    """Every mover hands the agent's battle turn to `road`; a delegation that forgets it raises."""
    import road as road_mod

    r = _reader_rig()
    r.battle = lambda io=None: None
    seen = {}

    def spy(*a, **kw):
        seen[kw.get("battle")] = True
        return "ok"

    for name, call in [
        ("walk", lambda: r.walk(1, {(0, 0)})),
        ("drive_to", lambda: r.drive(2)),
        ("through_warp", lambda: r.warp(1, 0, 0)),
        ("cross_edge", lambda: r.cross(1, 2)),
        ("traverse_interior", lambda: r.traverse(1)),
        ("pass_gate", lambda: r.gate(1, set())),
    ]:
        monkeypatch.setattr(road_mod, name, spy)
        assert call() == "ok"
    assert list(seen) == [r.battle]  # one handler, threaded through every one of them


def test_bodies_delegates_to_the_live_sprite_table(monkeypatch):
    import road as road_mod

    # The rig passes the current map's bounds so off-map sprite slots cannot become blockers.
    seen = {}

    def fake(io, bounds=None):
        seen["bounds"] = bounds
        return {(1, 2)}

    monkeypatch.setattr(road_mod, "live_bodies", fake)
    r = _reader_rig({0xD35E: 208, 0xD362: 26, 0xD361: 1}, {"maps": {"208": {"width": 30, "height": 18}}})
    assert r.bodies() == {(1, 2)}
    assert seen["bounds"] == (30, 18)  # the floor we are standing on, so off-map slots are dropped


def test_window_row_decodes_the_layer_menus_render_to():
    r = _reader_rig()

    class Tile:
        def tile_identifier(self, x, y):
            return (0x80 + x) if y == 4 and x < 3 else 0x7F

    class PB:
        tilemap_window = Tile()

    r.pb = PB()
    assert r.window_row(4) == "ABC"
    assert r.elevator_floors()[1] == ""


def test_settle_succeeds_once_a_b_press_frees_the_world():
    """The probe fails, B closes what was open, and the second probe proves it."""
    r = _stub_rig(parked=1)
    assert r.settle() is True
    assert "b" in r.io.presses


def test_settle_returns_immediately_when_the_world_already_moves():
    r = _stub_rig(parked=0)
    assert r.settle() is True
    assert "b" not in r.io.presses  # nothing was blocking, so nothing was pressed at it


def test_step_off_targets_prefers_floor_and_never_another_door():
    """A Pokemon Center has two exit mats side by side. Banking on one and "stepping off" onto
    the other leaves the baton in the doorway, and booting it settles straight out of the
    building — which is exactly what Saffron's (182,3,7) baton did, costing a leg its ladder
    trying to get back in."""
    r = _reader_rig(
        {0xD35E: 182, 0xD362: 3, 0xD361: 7},
        {"maps": {"182": {"width": 6, "height": 8, "grid": ["111111"] * 8, "warps": [[3, 7, 10, 0], [4, 7, 10, 0]]}}},
    )
    moves = r.step_off_targets(182, 3, 7)
    assert ("up", (3, 6)) in moves  # into the building
    assert all(cell != (4, 7) for _d, cell in moves)  # never the mat next door
    assert moves[0][0] == "up"  # and the interior is tried first


class _MenuRig:
    """Enough Rig to exercise menu selection: a window layer and a cursor register."""

    def __init__(self, rows, cursor=0):
        self._rows = rows
        self.mem = {rig.qm.ADDR_MENU_CUR: cursor, rig.ADDR_LIST_SCROLL: 0}
        self.presses = []

        class Ctl:
            def __init__(self, outer):
                self.outer = outer

            def press(self, button, *a, **kw):
                self.outer.presses.append(button)
                cur = self.outer.mem[rig.qm.ADDR_MENU_CUR]
                if button == "down":
                    self.outer.mem[rig.qm.ADDR_MENU_CUR] = cur + 1
                elif button == "up":
                    self.outer.mem[rig.qm.ADDR_MENU_CUR] = max(0, cur - 1)

            def wait(self, frames=30):
                pass

        self.ctl = Ctl(self)

    def window_row(self, row):
        return self._rows.get(row, "")

    def menu_rows(self, first=0, last=14):
        return rig.Rig.menu_rows(self, first, last)

    def dialogue(self):
        return ""

    def list_index(self):
        return rig.Rig.list_index(self)

    def _hit_or_shift(self, wanted, first=0, last=18):
        """The real helper, bound to the fake: menu_row_of delegates to it now, and the cursor
        nudge it performs is part of the behaviour under test."""
        return rig.Rig._hit_or_shift(self, wanted, first, last)


def test_menu_choose_selects_by_text_not_by_position():
    """The PC menu lists WITHDRAW, DEPOSIT, RELEASE and CHANGE BOX. Choosing by index would one
    day release a party member because a menu shifted, so entries are matched by decoded text and
    the cursor register is the ground truth for where the cursor sits."""
    menu = _MenuRig({2: "WITHDRAW", 4: "DEPOSIT", 6: "RELEASE", 8: "CHANGE BOX", 10: "SEE YA!"})
    assert rig.Rig.menu_choose(menu, "DEPOSIT") is True
    assert menu.mem[rig.qm.ADDR_MENU_CUR] == 1  # entries render every other row
    assert menu.presses[-1] == "a"
    assert "RELEASE" not in menu.presses


def test_menu_choose_reports_a_miss_rather_than_pressing_a():
    menu = _MenuRig({2: "WITHDRAW", 4: "DEPOSIT"})
    assert rig.Rig.menu_choose(menu, "SURF") is False
    assert "a" not in menu.presses


def test_menu_choose_walks_the_cursor_back_up():
    menu = _MenuRig({2: "WITHDRAW", 4: "DEPOSIT", 6: "RELEASE"}, cursor=2)
    assert rig.Rig.menu_choose(menu, "WITHDRAW") is True
    assert menu.mem[rig.qm.ADDR_MENU_CUR] == 0
    assert menu.presses.count("up") == 2


def test_the_pc_is_a_template_cell_like_the_nurses_counter():
    center = _reader_rig(
        {0xD35E: 64, 0xD362: 3, 0xD361: 7},
        {"maps": {"64": {"width": 14, "height": 8, "tileset": 6, "sprites": [{"kind": "npc", "x": 3, "y": 1}]}}},
    )
    assert center.center_pc(64) == ((13, 4), "up")
    plain = _reader_rig({}, {"maps": {"9": {"width": 10, "height": 9, "tileset": 0, "sprites": []}}})
    assert plain.center_pc(9) is None


def test_menu_shows_never_presses_anything_while_it_looks():
    """A advances a text box, but inside a list it CONFIRMS the highlighted entry — that is how
    Charizard and then Dugtrio ended up in a box. Looking must never press."""
    menu = _MenuRig({2: "CHARIZARD", 4: "DUGTRIO", 6: "HYPNO"})
    assert rig.Rig.menu_shows(menu, "DEPOSIT", tries=2) is False
    assert menu.presses == []
    assert rig.Rig.menu_shows(menu, "DUGTRIO", tries=2) is True
    assert menu.presses == []


def test_menu_cursor_to_counts_the_scroll_not_just_the_cursor():
    """The deposit roster shows three rows: 0xCC26 caps at 2 while 0xCC36 counts how far the list
    has scrolled, so the highlight is cursor + scroll. Reading only the cursor deposits the wrong
    member for anything past the third slot — measured on Cerulean's PC."""
    menu = _MenuRig({2: "AAAAAAAAAA", 4: "DUGTRIO", 6: "GLOOM"}, cursor=0)
    menu.mem[rig.ADDR_LIST_SCROLL] = 0

    def press(button, *a, **kw):  # the window caps at 2 and then the list scrolls under it
        menu.presses.append(button)
        if button == "down":
            if menu.mem[rig.qm.ADDR_MENU_CUR] < 2:
                menu.mem[rig.qm.ADDR_MENU_CUR] += 1
            else:
                menu.mem[rig.ADDR_LIST_SCROLL] += 1

    menu.ctl.press = press
    assert rig.Rig.menu_cursor_to(menu, 4) is True
    assert rig.Rig.list_index(menu) == 4
    assert menu.mem[rig.qm.ADDR_MENU_CUR] == 2 and menu.mem[rig.ADDR_LIST_SCROLL] == 2
    assert "a" not in menu.presses  # walked, never confirmed


def test_menu_choose_indexes_within_the_block_when_menus_overlay():
    """Choosing DEPOSIT renders the party list on top of the box menu, and the follow-up
    DEPOSIT/STATS/CANCEL renders on top of that. Measured rows from Cerulean's PC — the cursor
    index must be counted from the block the match is in, not from the first row on screen."""
    overlaid = _MenuRig(
        {2: "WI", 4: "DEDUGTRIO", 5: "99", 6: "RE GLOOM", 7: "99", 8: "CH PRIMEAPE", 12: "DEPOSIT", 14: "CANCEL"}
    )
    assert rig.Rig.menu_choose(overlaid, "DEPOSIT") is True
    assert overlaid.mem[rig.qm.ADDR_MENU_CUR] == 0  # first entry of ITS OWN block, not the fifth


def test_grass_lanes_are_the_rom_s_own_extremes():
    """Where to roam comes from the extracted grass tiles, not from lore — and pacing the
    extremes keeps crossing fresh tiles instead of rolling the same one. (Reachability is
    filtered only when the rig is standing on that map; see the next test.)"""
    # Standing on a different map, so the reachability filter does not apply.
    r = _reader_rig(
        {0xD35E: 99, 0xD362: 0, 0xD361: 0},
        {"maps": {"33": {"grass": [[5, 9], [2, 3], [7, 3], [2, 9]]}, "1": {"grass": []}}},
    )
    assert r.grass_lanes(33) == [(2, 3), (5, 9)]
    assert r.grass_lanes(1) == []  # a map with no grass has no lane to pace
    assert r.grass_lanes(999) == []  # and neither has one we do not model


def test_grass_lanes_only_offers_grass_we_can_stand_on(monkeypatch):
    """Route 2's 84 grass cells all sit outside the 144-cell region a leg arriving from Diglett's
    Cave can reach. Aimed at them, the roam walked nowhere and rolled no encounters at all —
    twelve thousand laps with a level-5 Magikarp still level 5."""
    import road as road_mod

    truth = {"maps": {"13": {"grass": [[0, 2], [9, 51]], "width": 20, "height": 72}}}
    r = _reader_rig({0xD35E: 13, 0xD362: 12, 0xD361: 10}, truth)
    r.bodies = lambda: set()
    monkeypatch.setattr(road_mod, "walkable", lambda *a, **k: {(12, 10), (12, 11)})
    assert r.grass_lanes(13) == []  # none of the map's grass is in our region
    monkeypatch.setattr(road_mod, "walkable", lambda *a, **k: {(12, 10), (0, 2), (9, 51)})
    assert r.grass_lanes(13) == [(0, 2), (9, 51)]


# --------------------------------------------------------------------------- healing at a Center

_CENTER_MAP = {"width": 14, "height": 8, "tileset": 6, "sprites": [{"kind": "npc", "x": 3, "y": 1}]}


def test_heal_at_center_refuses_a_map_that_is_not_a_center(capsys):
    """The grind leg that crashed here (run 20260901-164132-3962) had driven to map 89 and then
    called a method that did not exist; the honest failure on a wrong map is False, said aloud."""
    r = _reader_rig({0xD35E: 157, 0xD362: 3, 0xD361: 3}, {"maps": {"157": {"width": 10, "height": 9, "tileset": 0}}})
    assert r.heal_at_center() is False
    assert "not a Center" in capsys.readouterr().out


def test_heal_at_center_is_a_no_op_when_the_party_already_reads_full(monkeypatch):
    r = _reader_rig({0xD35E: 182, 0xD362: 5, 0xD361: 5}, {"maps": {"182": _CENTER_MAP}})
    monkeypatch.setattr(rig.qm, "read_party", lambda io: [{"hp": 63, "max_hp": 63}])
    r.approach = lambda cells: (_ for _ in ()).throw(AssertionError("walked for nothing"))
    assert r.heal_at_center() is True


def test_heal_at_center_talks_the_template_cell_until_the_party_reads_full(monkeypatch):
    r = _reader_rig({0xD35E: 182, 0xD362: 5, 0xD361: 5}, {"maps": {"182": _CENTER_MAP}})
    world = {"healed": False, "stood": None}
    monkeypatch.setattr(rig.qm, "read_party", lambda io: [{"hp": 63 if world["healed"] else 12, "max_hp": 63}])

    def approach(cells):
        world["stood"] = cells
        return True

    def nurse(io, face):
        assert face == "up"  # the counter template: player at (3,3) facing the nurse at (3,1)
        world["healed"] = True

    r.approach = approach
    monkeypatch.setattr(rig.qm, "heal", nurse)
    assert r.heal_at_center() is True
    assert world["stood"] == {(3, 3)}


def test_heal_at_center_reports_an_unreachable_counter(monkeypatch, capsys):
    r = _reader_rig({0xD35E: 182, 0xD362: 5, 0xD361: 5}, {"maps": {"182": _CENTER_MAP}})
    monkeypatch.setattr(rig.qm, "read_party", lambda io: [{"hp": 12, "max_hp": 63}])
    r.approach = lambda cells: False
    assert r.heal_at_center() is False
    assert "could not reach" in capsys.readouterr().out


def test_heal_at_center_gives_up_honestly_when_the_nurse_never_heals(monkeypatch):
    r = _reader_rig({0xD35E: 182, 0xD362: 5, 0xD361: 5}, {"maps": {"182": _CENTER_MAP}})
    monkeypatch.setattr(rig.qm, "read_party", lambda io: [{"hp": 12, "max_hp": 63}])
    r.approach = lambda cells: True
    calls = {"n": 0}

    def refuses(io, face):
        calls["n"] += 1
        raise rig.qm.QuartermasterError("nurse heal")

    monkeypatch.setattr(rig.qm, "heal", refuses)
    assert r.heal_at_center() is False
    assert calls["n"] == 3  # it retried, then told the truth instead of spinning


class _Recorder:
    """A controller that only remembers what was pressed — menus are stubbed per test."""

    def __init__(self):
        self.presses: list[str] = []

    def press(self, button, *a, **kw):
        self.presses.append(button)

    def wait(self, frames=30):
        pass


def test_a_failed_lead_swap_closes_the_menus_it_opened():
    """Measured on the karp grind: a silent failure here left the START menu up, every step after
    was swallowed, and the heal trip's first hop reported "refused" against a clear road."""
    r = rig.Rig.__new__(rig.Rig)
    r.party = lambda: [("MAGIKARP", 16, 5), ("HYPNO", 99, 341)]
    r.ctl = _Recorder()
    r.menu_choose = lambda wanted: False  # the start menu never showed POKeMON
    assert r.lead_swap(1) is False
    opened = r.ctl.presses.index("start")
    assert r.ctl.presses[opened + 1 :].count("b") >= 8  # what it opened, it closed


def test_say_puts_what_the_game_said_into_the_sink(tmp_path):
    """The Rig read a guru naming his rod, a boss conceding Silph and every card-key door, and
    only ever printed them — a search across every captured event for SURF, HM or SOULBADGE
    returned nothing while all of it had been on screen."""
    r = rig.Rig.__new__(rig.Rig)
    r.mem = {0xD35E: 163, 0xD362: 2, 0xD361: 5}
    r.run_id = "t"
    r.telemetry_root = tmp_path
    r.say("I'm the FISHING GURU! I simply love fishing!")
    r.say("   ")  # nothing said is nothing to record
    lines = [json.loads(x) for x in rig.telemetry_path(root=tmp_path).read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "discovery"
    assert lines[0]["map"] == 163 and (lines[0]["x"], lines[0]["y"]) == (2, 5)
    assert "FISHING GURU" in lines[0]["text"] and lines[0]["kind"] == "dialogue"


def test_ball_contents_names_what_each_ball_holds():
    """The lookup that ended the CARD KEY hunt: a ball's contents are in the cartridge."""
    r = _reader_rig(
        {},
        {
            "items": {"48": "CARD KEY", "20": "SUPER POTION"},
            "maps": {
                "210": {
                    "sprites": [
                        {"kind": "item", "x": 21, "y": 16, "item": 48},
                        {"kind": "item", "x": 2, "y": 13, "item": 20},
                        {"kind": "trainer", "x": 8, "y": 3},
                    ]
                }
            },
        },
    )
    assert r.ball_contents(210) == {(21, 16): "CARD KEY", (2, 13): "SUPER POTION"}
    assert r.ball_contents(999) == {}


def test_unlock_gates_drops_the_doors_the_bag_can_open():
    """A locked door is only a wall while the key is missing — and the leg that took the CARD KEY
    then planned its next hop as though it had not."""
    truth = {
        "items": {"48": "CARD KEY"},
        "maps": {
            "208": {"gates": {"11,11,left": "Darn! It needs a CARD KEY!", "3,3,up": "The door is locked..."}},
            "210": {"gates": {}},
            "1": {},
        },
    }
    r = _reader_rig({}, truth)
    r.bag_named = lambda: [("CARD KEY", 1), ("POTION", 3)]
    assert r.unlock_gates() == 1
    assert truth["maps"]["208"]["gates"] == {"3,3,up": "The door is locked..."}
    r.bag_named = lambda: []
    assert r.unlock_gates() == 0  # an empty bag opens nothing


def test_advance_text_stops_at_the_roster_rather_than_pressing_into_it():
    """A stops a text box and CONFIRMS inside a list. Recognise the roster by its own contents."""

    class Roster(_MenuRig):
        def party(self):
            return [("GLOOM", 99, 313), ("DUGTRIO", 100, 259)]

    menu = Roster({2: "DE GLOOM", 4: "RE DUGTRIO"})
    assert rig.Rig.advance_text(menu, "DEPOSIT") is False
    assert menu.presses == []


def test_advance_text_presses_through_a_box_until_the_menu_it_wants():
    class Box(_MenuRig):
        def __init__(self):
            super().__init__({2: "BILL's PC"})
            self.seen = 0

        def party(self):
            return [("GLOOM", 99, 313)]

        def menu_rows(self, first=0, last=18):
            self.seen += 1
            return [(2, "DEPOSIT")] if self.seen > 1 else [(2, "BILL's PC")]

    box = Box()
    assert rig.Rig.advance_text(box, "DEPOSIT") is True
    assert box.presses.count("a") >= 1


def test_advance_text_gives_up_after_its_budget():
    """A box that never changes is not a menu we can reach; say so rather than press forever."""

    class Stuck(_MenuRig):
        def party(self):
            return [("GLOOM", 99, 313)]

    stuck = Stuck({2: "BILL's PC"})
    assert rig.Rig.advance_text(stuck, "DEPOSIT", tries=3) is False
    assert stuck.presses.count("a") == 3


def test_menu_cursor_to_reports_failure_when_the_cursor_will_not_move():
    class Frozen(_MenuRig):
        def __init__(self):
            super().__init__({2: "A", 4: "B"})
            self.ctl.press = lambda *a, **k: self.presses.append(a[0] if a else "?")

    frozen = Frozen()
    assert rig.Rig.menu_cursor_to(frozen, 3, presses=4) is False


def test_menu_choose_and_center_lookups_refuse_what_they_cannot_find():
    menu = _MenuRig({})
    assert rig.Rig.menu_choose(menu, "DEPOSIT") is False  # nothing on screen
    plain = _reader_rig({}, {"maps": {"9": {"width": 10, "height": 9, "tileset": 0, "sprites": []}}})
    assert plain.center_counter(9) is None
    assert plain.step_off_targets(404, 0, 0) == []  # a map we do not model
    assert plain.grass_lanes(9) == []  # no grass listed


def test_menu_choose_refuses_when_the_cursor_will_not_reach_the_entry():
    """Reporting a miss beats pressing A somewhere we did not aim — the PC menu holds RELEASE."""

    class Stuck(_MenuRig):
        def __init__(self):
            super().__init__({2: "WITHDRAW", 4: "DEPOSIT"})
            self.ctl.press = lambda *a, **k: self.presses.append(a[0] if a else "?")  # cursor frozen

    stuck = Stuck()
    assert rig.Rig.menu_choose(stuck, "DEPOSIT") is False
    assert "a" not in stuck.presses


def test_center_counter_needs_the_nurse_tile_not_just_the_shell():
    """A room the same size and tileset as a Center is not a Center without the nurse at (3,1)."""
    shell = _reader_rig(
        {}, {"maps": {"64": {"width": 14, "height": 8, "tileset": 6, "sprites": [{"kind": "npc", "x": 7, "y": 4}]}}}
    )
    assert shell.center_counter(64) is None


def test_step_off_targets_skips_cells_a_tile_pair_refuses(monkeypatch):
    """Both cells walkable is not enough — the engine refuses some moves between them."""
    import rom_truth as rt_mod

    truth = {"maps": {"1": {"width": 3, "height": 3, "grid": ["111"] * 3, "warps": [[1, 1, 9, 0]]}}}
    r = _reader_rig({0xD35E: 1, 0xD362: 1, 0xD361: 1}, truth)
    r.pairs = set()
    monkeypatch.setattr(rt_mod, "passable", lambda *a, **k: False)
    assert r.step_off_targets(1, 1, 1) == []


def test_the_bag_spells_a_machine_out():
    """ "HM03" tells an operator nothing. The cartridge knows it teaches SURF, so the bag can say
    so — this is the answer to "tell me what the item is" that the number was hiding."""
    r = _bag_rig([(198, 1), (60, 2)], items={"198": "HM03", "60": "FRESH WATER"})
    r.truth["machines"] = {"HM03": "SURF", "TM26": "EARTHQUAKE"}
    assert r.bag_named() == [("HM03", 1), ("FRESH WATER", 2)]
    assert r.bag_named(full=True) == [("HM03 SURF", 1), ("FRESH WATER", 2)]
    assert r.item_full_name("TM26") == "TM26 EARTHQUAKE"
    assert r.item_full_name("POKe FLUTE") == "POKe FLUTE"  # not a machine: unchanged


def test_cross_routes_a_failed_cross_to_surf_only_when_the_edge_is_water(monkeypatch):
    import road as road_mod

    def make():
        r = _reader_rig()
        r.battle = lambda io=None: None
        return r

    # a water edge: the cross fails and the edge has no modelled floor -> surf across it
    monkeypatch.setattr(road_mod, "cross_edge", lambda *a, **k: "stuck-on-edge")
    monkeypatch.setattr(road_mod, "edge_cells", lambda *a: (set(), "left"))
    monkeypatch.setattr(road_mod, "surf_cross", lambda *a, **k: "surfed")
    assert make().cross(1, 2) == "surfed"

    # a land edge that still fails is a real block, not water -> surf must not swallow it
    monkeypatch.setattr(road_mod, "edge_cells", lambda *a: ({(0, 0)}, "left"))
    assert make().cross(1, 2) == "stuck-on-edge"

    # the connection isn't modelled -> the water verdict was a guess, keep the land failure
    def no_map(*a):
        raise KeyError("1")

    monkeypatch.setattr(road_mod, "edge_cells", no_map)
    assert make().cross(1, 2) == "stuck-on-edge"

    # a no-path water edge also routes to surf
    monkeypatch.setattr(road_mod, "cross_edge", lambda *a, **k: "no-path")
    monkeypatch.setattr(road_mod, "edge_cells", lambda *a: (set(), "left"))
    monkeypatch.setattr(road_mod, "surf_cross", lambda *a, **k: "surfed")
    assert make().cross(1, 2) == "surfed"


def test_menu_row_of_finds_the_entry_the_menu_actually_draws():
    """Gen 1 omits fainted members from the POKeMON menu, so a party index is not a menu index —
    on the badge-7 leg the only surfer was the one that had fainted, and the menu did not list it
    at all while `party()` still decoded six rows."""
    menu = _MenuRig({2: "PRIMEAPE  99", 4: "PIDGEOT   99", 6: "HYPNO     99", 8: "CHARIZARD 100"})
    assert rig.Rig.menu_row_of(menu, "HYPNO") == 2
    assert rig.Rig.menu_row_of(menu, "PRIMEAPE") == 0
    assert rig.Rig.menu_row_of(menu, "GYARADOS") is None  # fainted: simply not drawn


def test_field_moves_reports_nothing_when_the_submenu_is_not_on_screen():
    """The entry base is anchored on CANCEL, so a screen without one is not a field submenu.

    Measured on map 30: `use_field_move` is called blind after a `start` press, and if the menu
    did not open (mid-animation, or a dialog still up) every row decodes to roster text. Reading
    an entry list out of that invents moves the party does not have — the same class of error as
    the 'AAAAAAAASURF' splice, so the honest answer is an empty list.
    """
    menu = FieldMenuRig(["SURF"])
    menu.window_row = lambda row: ""  # nothing drawn: no CANCEL, so no base to count entries from
    r = _menu_rig(menu)
    r.window_row = menu.window_row
    assert r.field_moves() == []


# ------------------------------------------------------------------- arming SURF off the lead


class _SurfRig:
    """A party, the one member whose menu offers SURF, and a world that MOVES when it works.

    Using SURF carries the player onto the water, so a fake that never moves would let a lying
    boolean pass — which is precisely the bug these tests exist to pin.
    """

    def __init__(self, party, knows):
        self._party = party
        self.knows = knows
        self.asked = []
        self.at = (30, 6, 9)
        self.said = ""
        self.shots = []

    def party(self):
        return self._party

    def use_field_move(self, name, face=None, member=0, species=None):
        self.asked.append(species)
        if name == "SURF" and species == self.knows:
            self.at = (30, self.at[1], self.at[2] + 1)  # the game rides us onto the water
            return True
        self.said = "No SURFing here!"
        return False


class _NullCtl:
    def press(self, button, hold=8, release=8):
        pass

    def wait(self, frames=0):
        pass


def _surf_rig(party, knows, *, lead_arms=True):
    """A rig whose party RAM really carries the move ids, laid out at the measured offsets."""
    r = rig.Rig.__new__(rig.Rig)
    fake = _SurfRig(party, knows)
    r.party = fake.party
    r.use_field_move = fake.use_field_move
    r._surfer = None
    r._moves = {"SURF": 57, "CUT": 15}
    r.mem = {}
    for i, (name, _lvl, _hp) in enumerate(party):
        base = rig.ADDR_PARTY_STRUCTS + rig.PARTY_STRUCT_SIZE * i
        for off in rig.MOVE_SLOTS:
            r.mem[base + off] = 0
        if name == knows:
            r.mem[base + rig.MOVE_SLOTS[-1]] = 57  # SURF, in the last slot as Gyarados carries it

    def surf_facing(face=None):
        fake.asked.append("<keystroke>")
        if lead_arms:
            fake.at = (30, fake.at[1], fake.at[2] + 1)
        else:
            fake.said = "No SURFing on GYARADOS here!"

    r.surf_facing = surf_facing
    r.pos = lambda: fake.at
    r.textbox = lambda: fake.said
    r.ctl = _NullCtl()
    r.say = lambda text, kind="dialogue": fake.__setattr__("logged", (kind, text))
    r.screenshot = lambda tag: fake.shots.append(tag) or f"<fake>/{tag}.png"
    return r, fake


def test_surf_is_armed_on_whoever_knows_it_not_on_the_lead():
    """The lead is the battler; the surfer rides behind it. Assuming member 0 ended a leg."""
    party = [("Dugtrio", 100, 259), ("Gyarados", 20, 73)]
    r, fake = _surf_rig(party, "Gyarados")
    assert r._arm_surf() is True
    # knows_move(name, species=) filters by a free RAM read, so members that provably cannot
    # surf are never walked through the POKeMON menu -- that fumbling is what hid the
    # cursor-spliced SURF row on the badge-7 crossing.
    assert fake.asked == ["Gyarados"]
    assert r._surfer == "Gyarados"


def test_the_surfer_is_remembered_so_later_crossings_ask_it_first():
    party = [("Dugtrio", 100, 259), ("Gyarados", 20, 73)]
    r, fake = _surf_rig(party, "Gyarados")
    r._arm_surf()
    fake.asked.clear()
    assert r._arm_surf() is True
    assert fake.asked == ["Gyarados"]  # one call, not a re-scan of the whole party


def test_a_fainted_member_is_never_asked_because_the_menu_does_not_draw_it():
    """Gen 1 omits fainted members from the POKeMON menu, so asking for one selects a neighbour."""
    party = [("Dugtrio", 100, 259), ("Gyarados", 20, 0)]
    r, fake = _surf_rig(party, "Gyarados")
    assert r._arm_surf() is False
    assert "Gyarados" not in fake.asked


def test_a_party_with_no_surfer_reports_it_rather_than_arming_something_else():
    party = [("Dugtrio", 100, 259), ("Hypno", 99, 341)]
    r, _fake = _surf_rig(party, "Gyarados")
    assert r._arm_surf() is False
    assert r._surfer is None


def test_the_surfer_is_found_by_move_id_not_by_a_species_name():
    """The engine briefly carried `if lead in ("Gyarados", ...)` — true of one party, wrong on
    the next. The move id comes from the cartridge and the offsets were measured."""
    party = [("Dugtrio", 100, 259), ("Gyarados", 20, 73)]
    r, _fake = _surf_rig(party, "Gyarados")
    assert r.knows_move("SURF") == 1
    assert r.knows_move("CUT") is None  # nobody in this party has it
    assert r.knows_move("NOT-A-MOVE") is None


def test_a_fainted_surfer_is_not_reported_as_the_holder():
    party = [("Dugtrio", 100, 259), ("Gyarados", 20, 0)]
    r, _fake = _surf_rig(party, "Gyarados")
    assert r.knows_move("SURF") is None


def test_a_lead_that_knows_surf_is_armed_by_keystroke_not_by_a_window_read():
    """The window read returned None on a clean baton, so the lead path presses a fixed
    sequence; the water step after it is the real predicate."""
    party = [("Gyarados", 20, 73), ("Dugtrio", 100, 259)]
    r, fake = _surf_rig(party, "Gyarados")
    assert r._arm_surf() is True
    assert fake.asked == ["<keystroke>"]
    assert r._surfer == "Gyarados"


class _KeyCtl:
    def __init__(self):
        self.presses = []

    def press(self, button, hold=8, release=8):
        self.presses.append(button)

    def wait(self, frames=0):
        pass


def test_surf_facing_drives_a_fixed_sequence_and_reads_no_window_text():
    """The window read returned None on a clean baton, so the lead's arm is keystrokes + the
    cursor register only (no window read): START, POKeMON (seated off ADDR_MENU_CUR), roster
    seated to row 0 by cursor+scroll, field list the same, then A's."""
    r = rig.Rig.__new__(rig.Rig)
    r.ctl = _KeyCtl()
    r.mem = {qm.ADDR_MENU_CUR: 1, rig.ADDR_LIST_SCROLL: 0}

    def press(button, hold=8, release=8):
        r.ctl.presses.append(button)
        if button == "up" and "start" in r.ctl.presses:
            r.mem[qm.ADDR_MENU_CUR] = (r.mem[qm.ADDR_MENU_CUR] - 1) % 6

    r.ctl.press = press
    r.surf_facing()
    assert r.ctl.presses == ["b"] * 5 + ["start", "a", "up", "a", "a"]


def test_surf_facing_at_row_zero_never_wraps_onto_the_last_member():
    """Gen 1 menus wrap: a blind up from row 0 stepped onto the LAST member (measured on the
    badge-7 baton: the arm walked into Charizard, whose only field move is CUT, and left
    "There isn't nothing to CUT!" on the edge). With both menus opening on row 0, not a
    single up may appear between the roster A and the SELECT A - and SURF still lands.
    """
    r = rig.Rig.__new__(rig.Rig)
    r.ctl = _KeyCtl()
    r.mem = {qm.ADDR_MENU_CUR: 0, rig.ADDR_LIST_SCROLL: 0}  # START opens on POKeDEX (row 0)

    def press(button, hold=8, release=8):
        r.ctl.presses.append(button)
        if button in ("up", "down") and "start" in r.ctl.presses:
            r.mem[qm.ADDR_MENU_CUR] = (r.mem[qm.ADDR_MENU_CUR] - (1 if button == "up" else -1)) % 6
        if button == "a":
            r.mem[qm.ADDR_MENU_CUR] = 0  # every menu opens on its first entry (measured)

    r.ctl.press = press
    r.surf_facing()
    p = r.ctl.presses
    assert p == ["b"] * 5 + ["start", "down", "a", "a", "a"]  # seat row 1, roster 0, field 0, SURF
    assert "up" not in p, "an up at row 0 would wrap onto Charizard and fire CUT"


def test_surf_facing_seats_the_start_menu_on_pokemon_and_can_turn_first():
    r = rig.Rig.__new__(rig.Rig)
    r.ctl = _KeyCtl()
    r.mem = {qm.ADDR_MENU_CUR: 3, rig.ADDR_LIST_SCROLL: 0}  # opened lower down; it has to walk up to row 1

    def press(button, hold=8, release=8):
        r.ctl.presses.append(button)
        if button in ("up", "down") and r.ctl.presses.count("start"):
            r.mem[qm.ADDR_MENU_CUR] = (r.mem[qm.ADDR_MENU_CUR] - (1 if button == "up" else -1)) % 6

    r.ctl.press = press
    r.surf_facing(face="down")
    assert "down" in r.ctl.presses[:7]  # the turn happened before the menu opened
    assert r.ctl.presses.count("start") == 1
    # and never the blind-wrap failure: seating cost at most one up per menu, not the whole budget
    assert r.ctl.presses.count("up") <= r.ctl.presses.count("a") + 2


def test_the_move_table_is_read_from_the_cartridge_once_and_then_cached(monkeypatch, tmp_path):
    r = rig.Rig.__new__(rig.Rig)
    r._moves = None
    calls = {"n": 0}

    def fake_move_names(rom):
        calls["n"] += 1
        return {"57": "SURF", "15": "CUT"}

    rom = tmp_path / "fake.gb"
    rom.write_bytes(b"")
    monkeypatch.setattr(rig.rt, "move_names", fake_move_names)
    monkeypatch.setattr(rig.rt, "ROM_DEFAULT", rom)
    assert r._move_ids()["SURF"] == 57
    assert r._move_ids()["CUT"] == 15
    assert calls["n"] == 1  # the cartridge is read once per rig, not once per crossing


def test_a_fainted_member_ahead_of_the_surfer_is_stepped_over():
    """knows_move already skipped it; the arming loop has to skip it too, or it asks the menu
    for a member the menu does not draw."""
    party = [("Dugtrio", 100, 0), ("Gyarados", 20, 73)]
    r, fake = _surf_rig(party, "Gyarados")
    assert r._arm_surf() is True
    assert fake.asked == ["Gyarados"]  # the fainted lead was never asked


def test_arming_reports_failure_when_the_menu_will_not_take_the_selection():
    """RAM says someone knows SURF and the menu still refuses — that is a false, not a crash."""
    party = [("Dugtrio", 100, 259), ("Gyarados", 20, 73)]
    r, _fake = _surf_rig(party, "Gyarados")
    r.use_field_move = lambda *a, **k: False
    assert r._arm_surf() is False
    assert r._surfer is None


def test_a_refused_arm_reports_failure_instead_of_lying_about_it():
    """The bug that cost three legs, pinned.

    Measured on b8_BATON_island_gyarados_safe.state at map 30 (6,9): the keystrokes go in, the
    game answers "No SURFing on GYARADOS here!", the refusal text box swallows every later input
    (probe_step is False in all four directions) — and the old code returned True. The legs then
    read "nothing moves anywhere" as a water/rock maze and as unreliable position tracking, and
    wrote both up as world facts.
    """
    party = [("Gyarados", 20, 73), ("Dugtrio", 100, 259)]
    r, fake = _surf_rig(party, "Gyarados", lead_arms=False)
    assert r._arm_surf() is False  # the position did not change, so nothing was armed
    assert r._surfer is None


def test_the_refusal_sentence_is_recorded_into_the_sink():
    """A run that does not emit is unminable, and this sentence is the whole diagnosis."""
    party = [("Gyarados", 20, 73), ("Dugtrio", 100, 259)]
    r, fake = _surf_rig(party, "Gyarados", lead_arms=False)
    r._arm_surf()
    assert fake.logged == ("surf.refused", "No SURFing on GYARADOS here!")


def test_a_refusal_is_cleared_so_the_next_step_is_not_swallowed():
    """Leaving the box up is what made the world look frozen in every direction."""
    party = [("Gyarados", 20, 73), ("Dugtrio", 100, 259)]
    r, _fake = _surf_rig(party, "Gyarados", lead_arms=False)
    pressed = []
    r.ctl.press = lambda button, hold=8, release=8: pressed.append(button)
    r._arm_surf()
    assert pressed.count("b") >= 1


def test_the_textbox_is_read_off_the_bottom_of_the_window_and_joined():
    """Measured: with the roster on rows 0-11, "No SURFing on / GYARADOS here!" rendered on 12
    and 13, so the box is the bottom of the 18-row window and its rows are one sentence."""
    r = rig.Rig.__new__(rig.Rig)
    drawn = {12: "No SURFing on", 13: "GYARADOS here!"}
    r.window_row = lambda row, cursor=False: drawn.get(row, "")
    assert r.textbox() == "No SURFing on GYARADOS here!"


def test_an_empty_textbox_reads_as_nothing_said():
    r = rig.Rig.__new__(rig.Rig)
    r.window_row = lambda row, cursor=False: "   "
    assert r.textbox() == ""


# ------------------------------------------------------------------ the sink is a record


def _say_rig(tmp_path):
    r = rig.Rig.__new__(rig.Rig)
    r.run_id = "sayrun"
    r.telemetry_root = tmp_path
    r._said = set()
    r.pos = lambda: (30, 6, 9)
    return r


def _lines(tmp_path):
    p = rig.telemetry_path(root=tmp_path)
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_the_same_sentence_on_one_map_is_recorded_once(tmp_path):
    """Measured: a leg looped on the badge-explainer npc and wrote 1,455,047 discovery events
    carrying 37 distinct sentences -- 273 MB for one day. A sink that large is unminable."""
    r = _say_rig(tmp_path)
    for _ in range(500):
        r.say("Which of the 8 BADGEs should I describe?", "discovery")
    assert len(_lines(tmp_path)) == 1


def test_distinct_sentences_are_all_kept(tmp_path):
    r = _say_rig(tmp_path)
    r.say("Hi there! May I help you?")
    r.say("POKe BALL? That will be 200. OK?")
    assert len(_lines(tmp_path)) == 2


def test_the_same_line_on_a_different_map_is_still_news(tmp_path):
    """One mart clerk per city says the same thing; which city said it is the finding."""
    r = _say_rig(tmp_path)
    r.say("Hi there! May I help you?")
    r.pos = lambda: (67, 2, 5)
    r.say("Hi there! May I help you?")
    assert len(_lines(tmp_path)) == 2


def test_the_same_text_under_a_different_kind_is_kept(tmp_path):
    """A refusal and a dialogue line that read alike are different observations."""
    r = _say_rig(tmp_path)
    r.say("No SURFing here!", "discovery")
    r.say("No SURFing here!", "surf.refused")
    assert len(_lines(tmp_path)) == 2


def test_blank_text_is_never_recorded(tmp_path):
    r = _say_rig(tmp_path)
    r.say("   ")
    r.say("")
    assert _lines(tmp_path) == []


# ------------------------------------------------- facing water, and the cursor that hides a row


class _FaceRig:
    """A map whose tile grid is real, plus a controller that records what was pressed."""

    def __init__(self, rows, at=(1, 1)):
        self.presses = []
        self._at = at
        self.truth = {
            "maps": {
                "30": {
                    "width": len(rows[0]) // 2,
                    "height": len(rows),
                    "tileset": 0,
                    "tiles": list(rows),
                }
            }
        }

        class Ctl:
            def __init__(self, outer):
                self.outer = outer

            def press(self, button, *a, **kw):
                self.outer.presses.append(button)

            def wait(self, frames=30):
                pass

        self.ctl = Ctl(self)

    def pos(self):
        return (30, *self._at)


def test_the_arm_turns_to_face_water_before_pressing_start():
    """Measured on map 30: the same arm from (4,9) fails facing the solid 0x3a to the west and
    succeeds facing the 0x14 to the south — the activation animates you onto the tile you face."""
    r = _FaceRig(["000000", "003a14", "000000"], at=(1, 1))  # water (0x14) sits to the right
    rig.Rig._face_water(r)
    assert r.presses == ["right"]


def test_facing_water_prefers_the_first_water_neighbour_and_stops():
    r = _FaceRig(["001400", "001400", "001400"], at=(1, 1))  # water above and below
    rig.Rig._face_water(r)
    assert r.presses == ["down"]  # one press, not four


def test_no_water_neighbour_means_no_turn():
    r = _FaceRig(["3a3a3a", "3a3a3a", "3a3a3a"], at=(1, 1))
    rig.Rig._face_water(r)
    assert r.presses == []


def test_a_map_without_tiles_is_left_alone():
    r = _FaceRig(["001400"], at=(0, 0))
    r.truth["maps"]["30"]["tiles"] = None
    rig.Rig._face_water(r)
    assert r.presses == []


def test_a_row_hidden_by_the_cursor_is_found_by_nudging_it():
    """The cursor splices its own row into a run of As, so a name that is 'missing' is usually
    the row the cursor sits on. Nudge, re-read, and put the cursor back."""
    menu = _MenuRig({2: "AAAAAAAAAA", 4: "DUGTRIO"}, cursor=0)
    original = menu._rows.copy()

    def rows_after_nudge(first=0, last=18):
        # once the cursor has moved, the spliced row renders its real text
        if menu.presses:
            menu._rows = {2: "GYARADOS", 4: "DUGTRIO"}
        return rig.Rig.menu_rows(menu, first, last)

    menu.menu_rows = rows_after_nudge
    hit, text = menu._hit_or_shift("GYARADOS")
    assert hit == 2 and "GYARADOS" in text
    assert "down" in menu.presses and "up" in menu.presses  # nudged, then restored
    assert original[2] == "AAAAAAAAAA"


def test_a_name_that_is_genuinely_absent_reports_nothing():
    menu = _MenuRig({2: "DUGTRIO", 4: "HYPNO"}, cursor=0)
    assert menu._hit_or_shift("GYARADOS") == (None, None)


def test_a_roster_with_hp_rows_indexes_by_the_halved_row():
    """The POKeMON roster interleaves an HP row under every entry, so the 'nothing between
    entries' walkback stops a step short and calls every member index 0 — which is how a leg
    armed Charizard's field list when Gyarados was asked for. The HP row is the discriminator."""
    menu = _MenuRig(
        {
            0: "GYARADOS  20",
            1: " 73/ 73",
            2: "DUGTRIO  100",
            3: "259/259",
            4: "HYPNO     99",
            5: "341/341",
        }
    )
    assert rig.Rig.menu_row_of(menu, "HYPNO") == 2
    assert rig.Rig.menu_row_of(menu, "GYARADOS") == 0


def test_a_refusal_takes_a_screenshot_not_only_a_sentence():
    """Twice this project called water 'sealed' from the collision grid. On screen the tile it
    refused on was a boulder in open water. A refusal is a picture by default now, not only
    a sentence someone remembers to capture."""
    party = [("Gyarados", 20, 73), ("Dugtrio", 100, 259)]
    r, fake = _surf_rig(party, "Gyarados", lead_arms=False)
    r._arm_surf()
    assert fake.shots == ["surf_refused"]


def test_a_successful_arm_does_not_bother_taking_a_picture():
    party = [("Gyarados", 20, 73), ("Dugtrio", 100, 259)]
    r, fake = _surf_rig(party, "Gyarados", lead_arms=True)
    r._arm_surf()
    assert fake.shots == []


def test_screenshot_path_is_pure_and_namespaced_per_run():
    r = rig.Rig.__new__(rig.Rig)
    r.run_id = "runX"
    r.telemetry_root = None
    p = r.screenshot_path("surf refused!!")
    assert p.name == "surf_refused.png"
    assert p.parent.name == "runX"
    assert p.parent.parent.name == "screens"


def test_screenshot_path_respects_a_custom_telemetry_root(tmp_path):
    r = rig.Rig.__new__(rig.Rig)
    r.run_id = "runY"
    r.telemetry_root = tmp_path / "game"
    p = r.screenshot_path("stuck")
    assert p == tmp_path / "screens" / "runY" / "stuck.png"


def test_an_empty_tag_still_produces_a_usable_filename():
    r = rig.Rig.__new__(rig.Rig)
    r.run_id = "runZ"
    r.telemetry_root = None
    assert r.screenshot_path("!!!").name == "screen.png"


# ------------------------------------------------------------------ proximity-based engagement


def _write_sink(tmp_path, name, rows):
    p = tmp_path / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_engaged_near_matches_the_players_standing_tile_not_the_sprites(tmp_path):
    """The bug pinned: a discovery event's (x, y) is where the PLAYER stood to talk, one tile
    off the sprite it engaged. An exact-coordinate match against the sprite's own tile can
    never hit, which is how a real conversation reads as 'never talked to'."""
    _write_sink(
        tmp_path,
        "2026-09-03",
        [
            {"map": 91, "x": 0, "y": 6, "said": "There are evil people who will use POKeMON for..."},
        ],
    )
    # the sprite itself sits at (0,5); the player stood at (0,6) to talk to it
    assert rig.engaged_near(91, (0, 5), root=tmp_path) == ["There are evil people who will use POKeMON for..."]
    assert rig.engaged_near(91, (0, 5), radius=0, root=tmp_path) == []  # exact match: the old, broken way


def test_engaged_near_reads_every_dated_file_and_dedups(tmp_path):
    _write_sink(tmp_path, "2026-09-01", [{"map": 5, "x": 4, "y": 14, "text": "Hi there!"}])
    _write_sink(
        tmp_path,
        "2026-09-03",
        [
            {"map": 5, "x": 4, "y": 14, "text": "Hi there!"},
            {"map": 5, "x": 5, "y": 14, "said": "May I help you?"},
        ],
    )
    assert rig.engaged_near(5, (4, 14), root=tmp_path) == ["Hi there!", "May I help you?"]


def test_engaged_near_ignores_other_maps_and_blank_lines(tmp_path):
    p = tmp_path / "2026-09-03.jsonl"
    p.write_text('{"map": 30, "x": 1, "y": 1, "said": "wrong map"}\n\n{"map": 5, "x": 9, "y": 9}\n')
    assert rig.engaged_near(5, (9, 9), root=tmp_path) == []


def test_engaged_near_skips_unparseable_lines(tmp_path):
    p = tmp_path / "2026-09-03.jsonl"
    p.write_text("not json at all\n" + json.dumps({"map": 5, "x": 9, "y": 9, "text": "ok"}) + "\n")
    assert rig.engaged_near(5, (9, 9), root=tmp_path) == ["ok"]


def test_engaged_near_reports_nothing_for_a_map_with_no_sink(tmp_path):
    assert rig.engaged_near(999, (0, 0), root=tmp_path) == []


def test_menu_key_folds_the_screen_accent_onto_the_decoder_stand_in():
    from expedition_rig import _menu_key

    assert _menu_key("POKé FLUTE") == _menu_key("POKe FLUTE") == "POKEFLUTE"
    assert _menu_key("HM04     YES") == "HM04YES"


def test_cursor_to_ignores_the_stale_scroll_a_banked_bag_walk_left_behind():
    """The party roster draws every entry at once, so the scroll register must not count.

    Measured on strength_taught.state: a baton banked after walking the bag still carries that
    walk's scroll offset (16). menu_cursor_to adds it, so the roster, its STATS/SWITCH/CANCEL
    submenu and the TM roster all reported "failed to reach" an entry three presses away — which
    is how STRENGTH was nearly taught to the wrong member.
    """
    menu = _MenuRig({}, cursor=0)
    menu.mem[rig.ADDR_LIST_SCROLL] = 16  # the bag's leftovers; a non-scrolling menu must ignore it
    assert rig.Rig.cursor_to(menu, 3) is True
    assert menu.mem[rig.qm.ADDR_MENU_CUR] == 3
    assert menu.presses == ["down"] * 3


def test_cursor_to_walks_back_up_and_stops_on_arrival():
    menu = _MenuRig({}, cursor=4)
    assert rig.Rig.cursor_to(menu, 1) is True
    assert menu.presses == ["up"] * 3


def test_cursor_to_is_a_no_op_when_already_there():
    menu = _MenuRig({}, cursor=2)
    assert rig.Rig.cursor_to(menu, 2) is True
    assert menu.presses == []


def test_cursor_to_gives_up_rather_than_pressing_forever():
    """A menu that will not move must report failure, not spin."""
    menu = _MenuRig({}, cursor=0)

    class Stuck:
        def press(self, *a, **kw):
            menu.presses.append("down")

        def wait(self, frames=30):
            pass

    menu.ctl = Stuck()
    assert rig.Rig.cursor_to(menu, 5, presses=4) is False
    assert len(menu.presses) == 4


# ------------------------------------------------- the "Which move should be forgotten?" list


MOVES = {"SPLASH": 150, "TACKLE": 33, "BITE": 44, "STRENGTH": 70, "SURF": 57}


def test_forget_pick_reads_the_measured_list_and_skips_hm_moves():
    """Measured 2026-09-04 teaching HM03 to a four-move Gyarados: the moves draw on consecutive
    rows over the roster, some with a one-glyph sprite prefix from the row underneath."""
    rows = [(0, "AAAAAAAAAA100"), (1, "NOT ABLE"), (8, "H SPLASH"), (9, "TACKLE"), (10, "G BITE"), (11, "STRENGTH")]
    assert rig.forget_pick(rows, MOVES, {"STRENGTH", "SURF"}) == (0, "SPLASH")


def test_forget_pick_index_is_relative_to_the_first_move_row():
    rows = [(8, "STRENGTH"), (9, "TACKLE"), (10, "BITE")]
    assert rig.forget_pick(rows, MOVES, {"STRENGTH"}) == (1, "TACKLE")


def test_forget_pick_refuses_when_every_move_is_one_to_keep():
    assert rig.forget_pick([(8, "STRENGTH"), (9, "SURF")], MOVES, {"STRENGTH", "SURF"}) is None


def test_forget_pick_refuses_when_no_move_is_on_screen():
    assert rig.forget_pick([(0, "AAAAAAAAAA100"), (1, "NOT ABLE")], MOVES, set()) is None


# ------------------------------------------------- freeing a bag slot


def test_room_plan_uses_what_only_helps_before_tossing_anything():
    bag = [
        ("NUGGET", 1),
        ("HM01", 1),
        ("TM28", 1),
        ("HP UP", 1),
        ("HYPER POTION", 1),
        ("CALCIUM", 1),
        ("POKe FLUTE", 1),
    ]
    plan = rig.room_plan(bag)
    assert plan[:2] == [("use", "HP UP"), ("use", "CALCIUM")]
    assert plan[2] == ("toss", "NUGGET")
    # medicine is kit: it goes AFTER the TMs (measured at Cinnabar's mart, where the old order tossed the potions)
    assert ("toss", "HYPER POTION") in plan and plan.index(("toss", "TM28")) < plan.index(("toss", "HYPER POTION"))
    assert not any(n in ("HM01", "POKe FLUTE") for _a, n in plan)  # HMs and unlisted items are never touched


def test_room_plan_tosses_the_largest_multi_stack_before_singles():
    plan = rig.room_plan([("FRESH WATER", 7), ("SODA POP", 3), ("TM10", 1)])
    assert plan[0] == ("toss", "FRESH WATER")
    assert plan[-1] == ("toss", "TM10")
    # balls are kit and never in the plan, however large the stack (measured: 31 ULTRA BALLs tossed for a repel)
    assert rig.room_plan([("POKe BALL", 7), ("ULTRA BALL", 3), ("TM10", 1)]) == [("toss", "TM10")]


def test_room_plan_is_empty_when_nothing_is_expendable():
    assert rig.room_plan([("HM04", 1), ("CARD KEY", 1)]) == []


def test_make_room_uses_a_stat_booster_before_tossing_anything(tmp_path):
    """Using what only helps costs nothing; the verdict is the stack count, not the menu."""
    r = _bag_rig([(74, 1), (60, 6), (40, 2)], items={"60": "FRESH WATER", "74": "LIFT KEY", "40": "HP UP"})
    r.telemetry_root = tmp_path
    used, tossed = [], []

    def use_item(name):
        used.append(name)
        r.mem[rig.ADDR_BAG_COUNT] -= 1  # the game consumed the stack
        return True

    class Ctl:
        def press(self, *a, **kw):
            pass

        def wait(self, *a, **kw):
            pass

    r.use_item, r.ctl, r.toss_stack = use_item, Ctl(), lambda item: tossed.append(item) or True
    r.emit = lambda *a, **kw: None
    assert r.make_room() is True
    assert used == ["HP UP"] and tossed == []


def test_make_room_moves_past_a_use_the_game_refused(tmp_path):
    r = _bag_rig([(74, 1), (60, 6), (40, 1)], items={"60": "FRESH WATER", "74": "LIFT KEY", "40": "HP UP"})
    r.telemetry_root = tmp_path
    tossed = []

    class Ctl:
        def press(self, *a, **kw):
            pass

        def wait(self, *a, **kw):
            pass

    r.use_item, r.ctl = (lambda name: True), Ctl()  # selected, but the count never moved
    r.toss_stack = lambda item: tossed.append(item) or True
    r.emit = lambda *a, **kw: None
    assert r.make_room(allow_toss=True) is True
    assert tossed == [60]  # the largest stack, once using got nowhere


def test_the_town_map_row_is_matched_without_the_to_prefix():
    """Measured: the row decodes as 'ToPALLET TOWN' with no space after 'To'."""
    assert rig.fly_row_names("ToPALLET TOWN", "Pallet Town")
    assert rig.fly_row_names("ToFUCHSIA CITY", "FUCHSIA CITY")
    assert not rig.fly_row_names("ToSAFFRON CITY", "FUCHSIA CITY")
    assert not rig.fly_row_names("", "PALLET TOWN")


def test_make_room_reaches_the_tm_fallback_under_its_full_name(tmp_path):
    """room_plan sees 'TM28 DIG'; the id lookup must use the same name or TMs are never tossed."""
    r = _bag_rig([(74, 1), (40, 1), (228, 1)], items={"74": "LIFT KEY", "40": "HP UP", "228": "TM28"})
    r.truth["machines"] = {"TM28": "DIG"}
    r.telemetry_root = tmp_path
    tossed = []
    r.toss_stack = lambda item: tossed.append(item) or True
    assert (
        r.make_room(allow_toss=True) is True
    )  # no use_item on this rig: the use entries are skipped, the TM is tossed
    assert tossed == [228]


def test_a_booster_goes_to_the_lowest_level_standing_member():
    assert rig.booster_target([("Charizard", 100, 341), ("Gyarados", 20, 73), ("Hypno", 99, 341)]) == 1
    assert rig.booster_target([("Charizard", 100, 341), ("Gyarados", 20, 0)]) == 0  # fainted members are skipped
    assert rig.booster_target([]) is None


def test_make_room_hands_the_booster_to_the_lowest_level_member(tmp_path):
    r = _bag_rig([(74, 1), (40, 1)], items={"74": "LIFT KEY", "40": "HP UP"})
    r.telemetry_root = tmp_path
    seated, used = [], []

    class Ctl:
        def press(self, *a, **kw):
            pass

        def wait(self, *a, **kw):
            pass

    def use_item(name):
        used.append(name)
        r.mem[rig.ADDR_BAG_COUNT] -= 1
        return True

    r.use_item, r.ctl, r.emit = use_item, Ctl(), (lambda *a, **kw: None)
    r.party = lambda: [("Charizard", 100, 341), ("Gyarados", 20, 73)]
    r.cursor_to = lambda i: seated.append(i) or True
    assert r.make_room() is True
    assert used == ["HP UP"] and seated == [1]


def test_room_plan_spares_the_kit_and_tosses_tms_before_medicine():
    """Measured at Cinnabar's mart: the largest-stack rule tossed 31 ULTRA BALLs and the HYPER POTIONs to fit
    MAX REPELs. Balls are never in the plan; TMs go before any medicine; an ordinary stack still goes first."""
    from expedition_rig import is_kit, room_plan

    bag = [("ULTRA BALL", 31), ("HYPER POTION", 5), ("MAX REPEL", 8), ("TM07 HORN DRILL", 1), ("HM03 SURF", 1)]
    plan = room_plan(bag)
    assert ("toss", "ULTRA BALL") not in plan and ("toss", "MAX REPEL") not in plan
    assert ("toss", "HM03 SURF") not in plan
    assert plan.index(("toss", "TM07 HORN DRILL")) < plan.index(("toss", "HYPER POTION"))
    # a sellable stack that is not kit still goes before the TMs
    plan2 = room_plan([("FRESH WATER", 6), ("TM07 HORN DRILL", 1), ("ULTRA BALL", 3)])
    assert plan2[0] == ("toss", "FRESH WATER") and ("toss", "ULTRA BALL") not in plan2
    assert is_kit("GREAT BALL") and is_kit("FULL RESTORE") and not is_kit("NUGGET")


def test_storage_plan_banks_tms_then_single_items_and_keeps_the_kit_hms_and_the_carried_item():
    """Measured at Cinnabar 2026-09-04: the lab refused the OLD AMBER over a full bag and room_plan's answer was
    to toss a TM. The Center's PC is the game's own answer: TMs first, then single-copy items; never HMs, the kit,
    or the item the leg is carrying."""
    from expedition_rig import storage_plan

    bag = [
        ("S.S.TICKET", 1),
        ("HM01 CUT", 1),
        ("SECRET KEY", 1),
        ("TM27 FISSURE", 1),
        ("MAX REPEL", 4),
        ("ULTRA BALL", 20),
        ("OLD AMBER", 1),
        ("NUGGET", 3),
    ]
    plan = storage_plan(bag, keep=("OLD AMBER",))
    assert plan[0] == "TM27 FISSURE"
    assert plan[1:] == ["S.S.TICKET", "SECRET KEY"]
    assert "HM01 CUT" not in plan and "OLD AMBER" not in plan and "ULTRA BALL" not in plan
    assert storage_plan([("HM03 SURF", 1), ("HYPER POTION", 5)]) == []


def test_boulders_are_the_live_cells_of_pic_63_sprites_by_slot():
    r = rig.Rig.__new__(rig.Rig)
    r.truth = {
        "maps": {
            "108": {
                "width": 20,
                "height": 20,
                "sprites": [{"kind": "npc", "x": 2, "y": 10, "pic": 63}, {"kind": "trainer", "x": 5, "y": 5, "pic": 6}],
            }
        }
    }
    r.pos = lambda: (108, 1, 1)

    class IO:
        def read(self, addr):
            if addr < rig.road.SPRITE_DATA_BASE:
                return 1 if (addr - rig.road.SPRITE_STATE_BASE) // 0x10 in (1, 2) else 0
            slot, off = (addr - rig.road.SPRITE_DATA_BASE) // 0x10, addr & 0xF
            return {1: {4: 12 + 4, 5: 3 + 4}, 2: {4: 5 + 4, 5: 5 + 4}}[slot][off]  # the boulder moved to (3,12)

    r.io = IO()
    assert r.boulders() == {(3, 12)}
    r.pos = lambda: (999, 0, 0)
    assert r.boulders() == set()
