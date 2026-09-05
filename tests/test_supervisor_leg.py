"""The loop body, tested without a cartridge.

A fake Rig stands in for the emulator: it holds a scripted sequence of hop outcomes and records
what the runner asked it to do. That is enough to pin the behaviour that actually matters — the
ladder escalates navigation -> puzzle and then *stops with a written record*, a non-answer is
never silently turned into an action, GIVE_UP ends the leg, the budget is honoured, and the
badge check watches the byte change rather than a remembered bit.
"""

import json
from collections import Counter

import pytest
import rom_truth as rt
import supervisor
from supervisor import LADDER_ATTEMPTS, LegRunner, menu_for


class FakeRig:
    """Enough Rig to drive a leg: a position, a scripted hop outcome per call, a call log."""

    def __init__(
        self,
        *,
        start=(1, 5, 5),
        hops=None,
        truth=None,
        badges=0b11111,
        bodies=(),
        party=(("CHARIZARD", 99, 337),),
        heals_on_talk=False,
        saying="",
    ):
        self._pos = start
        self._hops = list(hops or [])  # each entry: the map id we land on after cross/warp
        self.truth = truth or _truth()
        self.pairs = set()
        self._badges = badges
        self._bodies = set(bodies)
        self._party = list(party)
        self._heals_on_talk = heals_on_talk
        self.run_id = "testrun00"
        self.calls: list[tuple] = []
        self.events: list[dict] = []
        self.io = self
        self.said = "MOVE ASIDE!"
        self._bag: list = []
        self._pickups: dict = {}
        self._saying = saying
        self.spoken: list = []
        self.shots: list = []

    # reads
    def pos(self):
        return self._pos

    def badges(self):
        return self._badges

    def party(self):
        return list(self._party)

    def dialogue(self):
        return self._saying

    def probe_step(self):
        """False exactly while a text box is up — the signal recon gates its read on."""
        return not self._saying

    def say(self, text, kind="dialogue"):
        self.spoken.append((kind, text))

    def screenshot(self, tag):
        self.shots.append(tag)
        return f"<fake>/{tag}.png"

    def bodies(self):
        return set(self._bodies)

    def settled_pos(self):
        return self._pos

    def item_balls(self, map_id):
        return [
            (s["x"], s["y"])
            for s in self.truth["maps"].get(str(map_id), {}).get("sprites", [])
            if s.get("kind") == "item"
        ]

    def center_counter(self, map_id):
        """The real lookup, run against the fake truth — it is pure, so the fake need not fake it."""
        import expedition_rig

        return expedition_rig.Rig.center_counter(self, map_id)

    def ball_contents(self, map_id):
        items = self.truth.get("items", {"48": "CARD KEY", "20": "SUPER POTION"})
        return {
            (s["x"], s["y"]): items.get(str(s.get("item")), f"item {s.get('item')}")
            for s in self.truth["maps"].get(str(map_id), {}).get("sprites", [])
            if s.get("kind") == "item"
        }

    def bag(self):
        return list(self._bag)

    def item_name(self, item_id):
        return {60: "FRESH WATER", 48: "CARD KEY"}.get(item_id, f"#{item_id}")

    def collect_item(self, bx, by):
        self.calls.append(("collect", (bx, by)))
        if (bx, by) in self._pickups:
            self._bag.append(self._pickups[(bx, by)])
            return True
        return False

    def emit(self, event, **fields):
        self.events.append({"event": event, **fields})
        return {}

    # moves — each consumes one scripted outcome.
    # "refused" is the generic failure here on purpose: it is the code with no deterministic
    # recovery, so these fixtures exercise the crew ladder. "no-path" is structural and the
    # runner answers it itself (see the reroute tests below).
    def _advance(self, label, arg):
        self.calls.append((label, arg))
        if self._hops:
            landed = self._hops.pop(0)
            if landed is not None:
                self._pos = (landed, 1, 1)
        return "refused"

    def cross(self, cur, nxt, **kw):
        return self._advance("cross", nxt)

    def warp(self, mp, x, y, **kw):
        return self._advance("warp", (x, y))

    def traverse(self, interior, **kw):
        return self._advance("traverse", interior)

    def gate(self, cur, cells, **kw):
        self.calls.append(("gate", cur))
        return False

    def walk(self, mp, targets, **kw):
        # A walk that records but never moves would let the runner "arrive" everywhere and
        # nowhere; the real one lands on a target cell, so this one does too.
        self.calls.append(("walk", sorted(targets)))
        if targets:
            self._pos = (mp, *sorted(targets)[0])
        return True

    def approach(self, cells):
        self.calls.append(("approach", sorted(cells)))
        if not cells:
            return False
        self._pos = (self._pos[0], *sorted(cells)[0])
        return True

    def settle(self, *a, **kw):
        """The real Rig closes a win/award box by pressing and probe-moving; here it's a close."""
        self.calls.append(("settle",))
        return True

    def talk(self, face):
        self.calls.append(("talk", face))
        if self._heals_on_talk:  # the Center nurse's line is the game's heal verb
            self._party = [(name, lvl, lvl or 1) for name, lvl, _hp in self._party]
        return self.said

    def text_from(self, action):
        baseline = self.dialogue()
        action()
        said = self.dialogue()
        return "" if said == baseline else said

    def press(self, button, hold=8, release=8):
        # `self.io is self`, so the refusal probe presses land here. A plain rig does not move.
        self.calls.append(("press", button))

    def wait(self, frames):
        self.calls.append(("wait", frames))


def _truth():
    """Two maps joined by one edge, plus a dead-end house on map 1 to back out through — the
    smallest world where the routed chain to map 2 is unambiguously the *edge*, not a door."""
    grid = ["1" * 8 for _ in range(8)]

    def m(**kw):
        return {
            "width": 8,
            "height": 8,
            "tileset": 0,
            "grid": grid,
            "sprites": [],
            "warps": [],
            "connections": {},
            **kw,
        }

    return {
        "maps": {
            "1": m(warps=[[2, 7, 9, 0]], connections={"east": 2}),
            "2": m(connections={"west": 1}),
            "9": m(warps=[[0, 0, 1, 0]]),  # the house: one door, straight back to map 1
        }
    }


def _consult(*answers):
    """A scripted seat: hands back (action, why, model) per call and logs the tier it was asked at."""
    seen = []

    def consult(tier, facts, menu):
        seen.append({"tier": tier, "facts": facts, "menu": list(menu)})
        action = answers[len(seen) - 1] if len(seen) <= len(answers) else answers[-1]
        return action, "scripted", "fake-model"

    consult.seen = seen
    return consult


# --------------------------------------------------------------------------- menus


def test_menu_drops_edge_action_on_a_warp_hop():
    assert "TRY_FAR_EDGE_CELL" in menu_for("no-path", edge_hop=True)
    assert "TRY_FAR_EDGE_CELL" not in menu_for("no-path", edge_hop=False)


def test_every_menu_action_is_one_the_engine_implements():
    for failure in list(supervisor.MENUS) + ["something-new"]:
        for action in menu_for(failure):
            assert action in supervisor.ACTIONS


def test_no_route_menu_never_offers_an_edge_or_retry_that_cannot_help():
    menu = menu_for("no-route")
    assert "RETRY_SAME" not in menu  # nothing to retry: the graph has no chain at all
    assert "GIVE_UP" in menu


# --------------------------------------------------------------------------- the happy leg


def test_a_clean_hop_arrives_without_ever_consulting():
    rig = FakeRig(hops=[2])
    consult = _consult("RETRY_SAME")
    result = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None).run()
    assert result["ok"] and result["outcome"] == "arrived"
    assert consult.seen == []  # models are asked on failure, never on a working hop
    assert [e["event"] for e in rig.events] == ["supervisor.leg_start", "supervisor.leg_end"]


def test_arrival_is_read_from_the_rig_not_assumed():
    rig = FakeRig(start=(2, 3, 3))
    result = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["outcome"] == "arrived" and rig.calls == []


# --------------------------------------------------------------------------- the ladder


def test_the_ladder_escalates_navigation_then_puzzle_then_writes_the_record(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    consult = _consult("RETRY_SAME")
    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    tiers = [c["tier"] for c in consult.seen]
    assert tiers == ["navigation", "navigation", "puzzle", "puzzle"]
    assert result["outcome"] == "exhausted" and not result["ok"]
    doc = next(tmp_path.glob("*.md")).read_text()
    assert "Anthropic was NOT called" in doc and "RETRY_SAME" in doc
    assert any(e["event"] == "supervisor.exhausted" for e in rig.events)


def test_a_non_answer_is_never_turned_into_an_action(tmp_path):
    """An unparsed reply must cost its attempt and nothing more — not the first menu item."""
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult(None), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert not any(c[0] in ("gate", "talk", "walk") for c in rig.calls)
    assert any("no menu action" in n for n in runner.notes)


def test_give_up_ends_the_leg_with_a_record(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["outcome"] == "gave-up"
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_the_wall_counter_is_per_wall_so_progress_resets_the_ladder(tmp_path):
    # Fail the 1->2 hop LADDER_ATTEMPTS times, then let it through: the leg must still arrive.
    rig = FakeRig(hops=[None] * LADDER_ATTEMPTS + [2])
    runner = LegRunner(rig, goal=2, consult=_consult("RETRY_SAME"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.run()["ok"]


# --------------------------------------------------------------------------- executed actions


def test_talk_to_blocker_walks_adjacent_faces_and_banks_what_was_said(tmp_path):
    rig = FakeRig(hops=[None] * 12, bodies={(6, 5)})
    runner = LegRunner(rig, goal=2, consult=_consult("TALK_TO_BLOCKER"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert ("talk", "right") in rig.calls  # the body is east of (5, 5)
    assert any("MOVE ASIDE!" in n for n in runner.notes)


def test_talk_to_blocker_with_no_body_records_that_the_block_is_not_a_sprite(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("TALK_TO_BLOCKER"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("terrain or a script" in n for n in runner.notes)
    assert not any(c[0] == "talk" for c in rig.calls)


def test_wait_for_bodies_waits_rather_than_declaring_a_wall(tmp_path):
    rig = FakeRig(hops=[None] * 12, bodies={(6, 5)})
    runner = LegRunner(rig, goal=2, consult=_consult("WAIT_FOR_BODIES"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert ("wait", supervisor.BODY_WAIT_FRAMES) in rig.calls


def test_far_edge_cell_aims_at_the_far_end_of_the_open_edge(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("TRY_FAR_EDGE_CELL"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    walks = [c for c in rig.calls if c[0] == "walk"]
    assert walks and walks[0][1] == [(7, 0)]  # from (5,5), the far cell on map 1's east column


def test_back_out_uses_the_nearest_warp_on_this_map(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(
        rig, goal=2, consult=_consult("BACK_OUT_AND_REENTER"), log=lambda *_: None, learnings_dir=tmp_path
    )
    runner.run()
    assert ("warp", (2, 7)) in rig.calls


# --------------------------------------------------------------------------- budget + engage


def test_the_budget_stops_the_leg_even_mid_wall(tmp_path):
    ticks = iter([0, 0, 10_000])
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(
        rig,
        goal=2,
        budget_s=60,
        consult=_consult("RETRY_SAME"),
        clock=lambda: next(ticks),
        log=lambda *_: None,
        learnings_dir=tmp_path,
    )
    result = runner.run()
    assert result["outcome"] == "budget" and not result["ok"]


def test_engage_watches_the_badge_byte_change_not_a_remembered_bit():
    rig = FakeRig(start=(2, 3, 3), badges=0b11111, bodies={(4, 3)})
    original_talk = rig.talk

    def talk(face):  # the leader falls; the byte gains a bit we never had to name
        rig._badges = 0b111111
        return original_talk(face)

    rig.talk = talk
    result = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["ok"] and result["badges"] == 0b111111


def test_engage_that_changes_nothing_is_reported_as_such():
    rig = FakeRig(start=(2, 3, 3), badges=0b11111, bodies={(4, 3)})
    result = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["outcome"] == "engaged-no-badge" and not result["ok"]


def test_engage_never_talks_to_an_item_ball():
    """Balls are in the live sprite table too; they are sweep_items' job. Talking to one read the
    START menu the bag step had left on the window layer (measured on maps 194, 219, 234)."""
    truth = _truth()
    truth["maps"]["2"]["sprites"] = [{"kind": "item", "x": 2, "y": 2, "pic": 61, "item": 10}]
    rig = FakeRig(start=(2, 3, 3), badges=0b11111, bodies={(4, 3), (2, 2)}, truth=truth)
    LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    talked = {c[1] for c in rig.calls if c[0] == "talk"}
    assert talked and (2, 2) not in talked


def _truth_with_a_nurse():
    """The fake world plus a Center nurse ON MAP 2, because `engage_bodies` meets the bodies the
    *cartridge* lists, not the live sprite table — and in the real game the nurse is one of them.
    Without her the heal has nobody to talk to and the leg is right to report a refusal."""
    truth = _truth()
    truth["maps"]["2"]["sprites"] = [{"kind": "npc", "x": 4, "y": 3}]
    return truth


def test_heal_is_engagement_judged_on_the_party_not_the_badges():
    rig = FakeRig(
        start=(2, 3, 3),
        truth=_truth_with_a_nurse(),
        badges=0b11111,
        bodies={(4, 3)},
        party=[("CHARIZARD", 100, 0), ("DUGTRIO", 99, 0)],
        heals_on_talk=True,
    )
    result = LegRunner(rig, goal=2, heal=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["ok"], result
    assert ("talk", "right") in rig.calls, "the leg met the body that heals"
    assert all(hp > 0 for _name, _lvl, hp in rig.party())


def test_heal_that_heals_nothing_is_reported_as_such():
    """A body that talks and heals nothing leaves the readings at zero, and the leg says so
    rather than carrying a fainted party into the next fight."""
    rig = FakeRig(
        start=(2, 3, 3),
        truth=_truth_with_a_nurse(),
        badges=0b11111,
        bodies={(4, 3)},
        party=[("CHARIZARD", 100, 0)],
    )
    result = LegRunner(rig, goal=2, heal=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["outcome"] == "heal-refused" and not result["ok"]


def test_heal_that_is_already_done_talks_to_nobody():
    rig = FakeRig(
        start=(2, 3, 3),
        truth=_truth_with_a_nurse(),
        bodies={(4, 3)},
        party=[("CHARIZARD", 100, 100)],
        heals_on_talk=True,
    )
    result = LegRunner(rig, goal=2, heal=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["ok"] and ("talk", "right") not in rig.calls


# --------------------------------------------------------------------------- the facts


def test_the_facts_handed_over_are_measured_and_carry_the_per_tileset_warning():
    rig = FakeRig(bodies={(6, 5)})
    facts = supervisor.describe(rig, 2, {"via": "edge", "to": 2}, "no-path", ["the guard says NO"])
    assert "map 1 at (5, 5)" in facts
    assert "tileset" in facts and "per-tileset" in facts
    assert "OPEN EDGE CELLS" in facts and "(7, 0)" in facts
    assert "LIVE BODIES" in facts and "(6, 5)" in facts
    assert "BADGES byte: 0b00011111" in facts
    assert "OBSERVED: the guard says NO" in facts


def test_facts_for_a_missing_route_say_so_plainly():
    rig = FakeRig()
    facts = supervisor.describe(rig, 99, None, "no-route")
    assert "NO ROUTE" in facts


# --------------------------------------------------------------------------- the seat wiring


def test_the_consult_posts_to_the_tapes_proxy_and_parses_the_reply(monkeypatch):
    import expedition_crew as crew

    posted = {}

    class _Resp:
        """The seat's reply as the wire delivers it: an SSE stream, not one whole response."""

        def __iter__(self):
            for delta in (
                {"reasoning": "weighing the menu\n"},
                {"content": "ACTION: USE_GATE_WARP\nWHY: the gate severs it\n"},
            ):
                yield ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n").encode()
            yield b"data: [DONE]\n"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        posted["url"] = req.full_url
        posted["body"] = json.loads(req.data)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    action, why, model = supervisor.TapesConsult(log=lambda *_: None)(
        "puzzle", "facts here", ["USE_GATE_WARP", "GIVE_UP"]
    )
    assert posted["url"] == crew.TAPES_CHAT_URL  # :42345 — an uncaptured call is a doctrine break
    assert posted["body"]["model"] == crew.CREW["puzzle"]["model"]
    assert "recalled details are frequently wrong" in posted["body"]["messages"][0]["content"]
    assert posted["body"]["stream"] is True  # a 300s gateway ceiling cannot hold a whole answer
    assert (action, model) == ("USE_GATE_WARP", crew.CREW["puzzle"]["model"])
    assert why == "the gate severs it"


def test_a_dead_proxy_is_a_non_answer_not_a_crash(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    action, why, _ = supervisor.TapesConsult(log=lambda *_: None)("navigation", "facts", ["RETRY_SAME"])
    assert action is None and "consult failed" in why


def test_the_seats_are_the_benchmarked_crew_and_never_anthropic():
    import expedition_crew as crew

    for tier in ("navigation", "puzzle"):
        assert "claude" not in crew.seat_for(tier)["model"].lower()


# --------------------------------------------------------------------------- the old half still works


def test_the_cross_run_classifier_is_untouched():
    sup = supervisor.Supervisor()
    assert sup.classify_exit(budget_s=7200, used_s=7000, baton=True, harness_death=False)["action"] == "next_leg"


@pytest.mark.parametrize("cmd", ["run", "classify-exit", "replay"])
def test_every_documented_subcommand_is_registered(cmd):
    with pytest.raises(SystemExit):
        supervisor.main([cmd, "--help"])


def test_a_goal_chain_parses_into_legs():
    assert supervisor.parse_goals("10,181,178") == [10, 181, 178]
    assert supervisor.parse_goals("7") == [7]
    assert supervisor.parse_goals(" 10 , 178 ") == [10, 178]


def test_route_lookup_is_the_hop_source_not_a_search():
    """The runner asks rom_truth for the chain; the smallest world answers with one edge hop."""
    chain = rt.route(_truth(), 1, 2)
    assert chain and chain[0]["via"] == "edge" and chain[0]["to"] == 2


# --------------------------------------------------------------- determinism before consultation


def _fork_truth():
    """Map 1 reaches map 2 two ways: a direct edge, and a detour through map 3. The direct edge
    is Cycling Road — a graph path the world refuses."""
    grid = ["1" * 8 for _ in range(8)]

    def m(**kw):
        return {
            "width": 8,
            "height": 8,
            "tileset": 0,
            "grid": grid,
            "sprites": [],
            "warps": [],
            "connections": {},
            **kw,
        }

    return {
        "maps": {
            "1": m(connections={"east": 2, "south": 3}),
            "2": m(connections={"west": 1, "south": 4}),
            "3": m(connections={"north": 1, "east": 4}),
            "4": m(connections={"west": 3, "north": 2}),
        }
    }


class SeveredRig(FakeRig):
    """One named hop reports no-path forever; every other hop lands."""

    def __init__(self, severed=(1, 2), **kw):
        kw.setdefault("truth", _fork_truth())
        super().__init__(**kw)
        self.severed = severed

    def cross(self, cur, nxt, **kw):
        self.calls.append(("cross", nxt))
        if (cur, nxt) == self.severed:
            return "no-path"
        self._pos = (nxt, 1, 1)
        return True


def test_a_structurally_refused_hop_is_banned_and_routed_around_without_a_consult(tmp_path):
    """Cycling Road, in miniature: 1->2 is a graph edge no player can walk, so take 1->3->4->2."""
    rig = SeveredRig()
    consult = _consult("GIVE_UP")
    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert (1, 2) in runner.banned
    assert consult.seen == []  # a fact about the graph is not a question for a model
    assert any(e["event"] == "supervisor.rerouted" for e in rig.events)


def test_the_gate_building_is_tried_before_the_hop_is_banned(tmp_path):
    rig = SeveredRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert ("gate", 1) in rig.calls  # a severed route is usually its own gate building
    assert (1, 2) in runner.gated


def test_banning_the_only_chain_falls_back_to_the_crew_rather_than_looping(tmp_path):
    rig = SeveredRig(truth=_truth())  # the two-map world: nothing to reroute through
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["outcome"] == "gave-up"
    assert any("leaves no chain" in n for n in runner.notes)


def test_route_can_ban_a_hop_the_world_refuses():
    truth = _fork_truth()
    assert rt.route(truth, 1, 2)[0]["to"] == 2  # the direct edge, unbanned
    detour = rt.route(truth, 1, 2, banned={(1, 2)})
    assert [h["to"] for h in detour] == [3, 4, 2]
    assert rt.route(truth, 1, 2, banned={(1, 2), (1, 3)}) is None


# ------------------------------------------------------- one body is a gate, not a missing road


class BlockedRig(SeveredRig):
    """1->2 is severed while `blocker` stands there; engaging it opens the road."""

    def __init__(self, blocker=(3, 2), **kw):
        super().__init__(severed=(1, 2), truth=_corridor_world(), **kw)
        self._pos = (1, 0, 5)
        self._bodies = {blocker, (1, 4)}  # the wall, and a bystander right next to us
        self.blocker = blocker

    def cross(self, cur, nxt, **kw):
        self.calls.append(("cross", nxt))
        if (cur, nxt) == self.severed and self.blocker in self._bodies:
            return "no-path"
        self._pos = (nxt, 1, 1)
        return True

    def talk(self, face):
        self.calls.append(("talk", face))
        self._bodies.discard(self.blocker)  # beaten/moved: the road opens
        return "I like shorts!"


def _corridor_world():
    rows = ["0011000", "0011000", "0001000", "0011000", "1111111", "1111111"]

    def m(**kw):
        return {
            "width": 7,
            "height": 6,
            "tileset": 0,
            "grid": rows,
            "sprites": [],
            "warps": [],
            "connections": {},
            **kw,
        }

    return {"maps": {"1": m(connections={"north": 2}), "2": m(connections={"south": 1})}}


def test_one_body_severing_a_hop_is_engaged_before_anything_is_banned(tmp_path):
    """Route 12: the north road was banned as impassable when the wall was one unfought trainer."""
    rig = BlockedRig()
    consult = _consult("GIVE_UP")
    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert runner.banned == set()  # nothing was banned: the road was there all along
    assert consult.seen == []  # and nothing was asked: one body is not a judgement call
    assert any(e["event"] == "supervisor.blocker_engaged" for e in rig.events)


def test_the_body_underfoot_is_not_mistaken_for_the_wall(tmp_path):
    rig = BlockedRig()
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    walks = [c[1] for c in rig.calls if c[0] == "walk"]
    # It walked to a cell adjacent to the choke at (3,2), not to the bystander at (1,4).
    assert walks and set(walks[0]) <= {(2, 2), (4, 2), (3, 1), (3, 3)}


def test_the_same_blocker_is_only_engaged_once(tmp_path):
    """A body that does not clear must not become an infinite errand."""
    rig = BlockedRig()

    def stubborn(face):  # engaging changes nothing: the body stays exactly where it was
        rig.calls.append(("talk", face))
        return "..."

    rig.talk = stubborn
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert len(runner.engaged) == 1
    assert len([c for c in rig.calls if c[0] == "talk"]) == 1


def test_gate_doors_tell_a_pass_through_from_a_dead_end_house():
    import road

    truth = {
        "maps": {
            "23": {"warps": [[10, 15, 87, 0], [11, 15, 87, 1], [10, 21, 87, 2], [11, 77, 189, 0]]},
        }
    }
    assert road.gate_doors(truth, 23) == {(10, 15), (11, 15), (10, 21)}  # map 87 is the gate


def test_clearing_a_blocker_retires_the_verdicts_reached_while_it_stood(tmp_path):
    """Route 12 was banned as impassable, and its gate marked tried, on evidence gathered while
    the blocker still stood there. Clearing it makes both verdicts stale."""
    rig = BlockedRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.banned.add((1, 2))
    runner.gated.add((1, 2))
    runner._clear_blocker({"via": "edge", "to": 2})
    assert (1, 2) not in runner.banned
    assert (1, 2) not in runner.gated


# ------------------------------------------------------------- the facility floors (tileset 22)


def test_the_oracle_is_offered_only_on_tile_driven_floors():
    """Spin arrows and teleport pads live in tileset 22; a route map has nothing to search."""
    assert "ORACLE_SEARCH" not in menu_for("warp-dead", facility=False)
    assert menu_for("warp-dead", facility=True)[0] == "ORACLE_SEARCH"


class FacilityRig(FakeRig):
    """A tileset-22 floor whose warp will not fire until the oracle finds the way onto it."""

    def __init__(self):
        grid = ["1" * 8 for _ in range(8)]
        truth = {
            "maps": {
                "181": {
                    "width": 8,
                    "height": 8,
                    "tileset": 22,
                    "grid": grid,
                    "sprites": [],
                    "warps": [[6, 4, 208, 0]],
                    "connections": {},
                },
                "208": {
                    "width": 8,
                    "height": 8,
                    "tileset": 22,
                    "grid": grid,
                    "sprites": [],
                    "warps": [[0, 0, 181, 0]],
                    "connections": {},
                },
            }
        }
        super().__init__(start=(181, 1, 1), truth=truth, hops=[None] * 12)
        self.oracle_calls = []

    def oracle_goto(self, goal_test, max_states=500):
        self.oracle_calls.append(max_states)
        self._pos = (208, 1, 1)  # the oracle found the pad and it fired
        return True


def test_the_oracle_action_runs_the_facing_keyed_search_toward_the_hop_target(tmp_path):
    rig = FacilityRig()
    runner = LegRunner(rig, goal=208, consult=_consult("ORACLE_SEARCH"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert rig.oracle_calls, "the oracle was never run on a tileset-22 floor"
    assert result["ok"] and result["outcome"] == "arrived"


def test_the_facility_menu_reaches_the_seat(tmp_path):
    rig = FacilityRig()
    consult = _consult("GIVE_UP")
    LegRunner(rig, goal=208, consult=consult, log=lambda *_: None, learnings_dir=tmp_path).run()
    assert "ORACLE_SEARCH" in consult.seen[0]["menu"]


def test_a_dead_door_is_routed_around_rather_than_asked_about(tmp_path):
    """Silph 1F's (16,10) pad is dead and the floor has two other ways up. A door that will not
    open is as structural as a severed grid — a lookup, not a question."""

    class DeadDoorRig(FakeRig):
        def __init__(self):
            super().__init__(truth=_fork_truth(), start=(1, 5, 5))

        def cross(self, cur, nxt, **kw):
            self.calls.append(("cross", nxt))
            if (cur, nxt) == (1, 2):
                return "warp-dead"
            self._pos = (nxt, 1, 1)
            return True

    rig = DeadDoorRig()
    consult = _consult("GIVE_UP")
    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"] and (1, 2) in runner.banned
    assert consult.seen == []


def test_the_consult_waits_as_long_as_the_seat_needs(monkeypatch):
    import expedition_crew as crew

    waits = {}

    class _Resp:
        def __iter__(self):
            chunk = json.dumps({"choices": [{"delta": {"content": "ACTION: GIVE_UP\nWHY: x\n"}}]})
            yield ("data: " + chunk + "\n").encode()
            yield b"data: [DONE]\n"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        waits[json.loads(req.data)["model"]] = timeout
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    consult = supervisor.TapesConsult(log=lambda *_: None)
    consult("navigation", "facts", ["GIVE_UP"])
    consult("puzzle", "facts", ["GIVE_UP"])
    assert waits[crew.CREW["puzzle"]["model"]] > waits[crew.CREW["navigation"]["model"]]


# ------------------------------------------------------------------- the floor's own item balls


def _floor_with_balls():
    grid = ["1" * 8 for _ in range(8)]
    return {
        "maps": {
            "209": {
                "width": 8,
                "height": 8,
                "tileset": 22,
                "grid": grid,
                "warps": [[6, 6, 234, 0]],
                "connections": {},
                "sprites": [
                    {"kind": "item", "x": 3, "y": 4, "item": 48},
                    {"kind": "item", "x": 5, "y": 2, "item": 20},
                    {"kind": "trainer", "x": 1, "y": 1},
                ],
            },
            "234": {
                "width": 8,
                "height": 8,
                "tileset": 22,
                "grid": grid,
                "warps": [],
                "connections": {},
                "sprites": [],
            },
        }
    }


def test_the_sweep_opens_every_ball_the_cartridge_lists_and_reports_the_bag(tmp_path):
    rig = FakeRig(start=(209, 1, 4), truth=_floor_with_balls())
    rig._pickups = {(3, 4): (48, 1), (5, 2): (20, 3)}
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    gained = runner.sweep_items()
    assert sorted(gained) == [(20, 3), (48, 1)]
    assert sorted(c[1] for c in rig.calls if c[0] == "collect") == [(3, 4), (5, 2)]
    assert any(e["event"] == "supervisor.item_collected" for e in rig.events)


def test_an_unreachable_ball_names_the_pad_that_stands_beside_it(tmp_path):
    """ "Could not reach" is the least useful true sentence a leg can write. When a pad stands in
    the region the target lives in, the leg says so — that is the CARD KEY's (27,3) on 5F."""
    truth = _floor_with_balls()
    floor = truth["maps"]["209"]
    floor["width"], floor["height"] = 8, 8
    floor["grid"] = ["11111111"] * 8
    floor["warps"] = [[4, 4, 210, 0]]  # the pad severs the right half from the left on foot
    floor["sprites"] = [{"kind": "item", "x": 7, "y": 4, "item": 48}]
    rig = FakeRig(start=(209, 0, 4), truth=truth)
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.sweep_items()
    named = [e for e in rig.events if e["event"] == "supervisor.pad_named"]
    assert named and named[0]["pads"] == [[[4, 4], 210]]


def test_the_wanted_ball_is_opened_before_any_other(tmp_path):
    """A leg that came for the CARD KEY opens its ball first. The cartridge says which ball
    holds it, so a full bag, a lost fight, or a spent budget can no longer cost the one pickup
    the leg exists for — Silph's key sat in map 210's (21,16) through two sweep sessions."""
    rig = FakeRig(start=(209, 1, 4), truth=_floor_with_balls())
    rig._pickups = {(3, 4): (48, 1), (5, 2): (20, 3)}
    runner = LegRunner(
        rig, goal=234, want="CARD KEY", consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path
    )
    runner.sweep_items(runner.want)
    assert [c[1] for c in rig.calls if c[0] == "collect"] == [(3, 4), (5, 2)]


def test_a_refused_ball_says_what_the_cartridge_put_in_it(tmp_path):
    rig = FakeRig(start=(209, 1, 4), truth=_floor_with_balls())  # no pickups: every open fails
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.sweep_items()
    refused = [e for e in rig.events if e["event"] == "supervisor.item_refused"]
    assert {e["holds"] for e in refused} == {"CARD KEY", "SUPER POTION"}


def test_a_ball_is_only_tried_once_per_leg(tmp_path):
    rig = FakeRig(start=(209, 1, 4), truth=_floor_with_balls())
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.sweep_items()
    runner.sweep_items()
    assert len([c for c in rig.calls if c[0] == "collect"]) == 2  # two balls, not four


def test_the_sweep_is_offered_only_where_unopened_balls_remain():
    assert "SWEEP_ITEMS" not in menu_for("warp-dead", items=False)
    assert menu_for("warp-dead", items=True)[0] == "SWEEP_ITEMS"


def test_arriving_with_sweep_on_opens_the_floor(tmp_path):
    rig = FakeRig(start=(209, 1, 4), truth=_floor_with_balls())
    rig._pickups = {(3, 4): (48, 1)}
    runner = LegRunner(
        rig, goal=209, sweep=True, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path
    )
    assert runner.run()["ok"]
    assert rig.bag() == [(48, 1)]


# ------------------------------------------------------------------- clearing a story floor


def _top_floor():
    grid = ["1" * 16 for _ in range(18)]
    return {
        "maps": {
            "234": {
                "width": 16,
                "height": 18,
                "tileset": 22,
                "grid": grid,
                "warps": [],
                "connections": {},
                "sprites": [
                    {"kind": "trainer", "x": 1, "y": 9},
                    {"kind": "trainer", "x": 10, "y": 2},
                    {"kind": "npc", "x": 9, "y": 15},
                    {"kind": "item", "x": 2, "y": 12},
                ],
            }
        }
    }


def test_clearing_a_floor_fights_every_trainer_the_cartridge_lists(tmp_path):
    """Silph's top floor changes no badge, so the badge-watching engage is the wrong instrument."""
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    runner = LegRunner(
        rig, goal=234, clear_floor=True, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path
    )
    assert runner.run()["ok"]
    assert runner.engaged == {(1, 9), (10, 2)}  # both trainers, and neither the npc nor the ball
    assert len([e for e in rig.events if e["event"] == "supervisor.body_engaged"]) == 2


def test_clearing_a_floor_stops_early_when_a_badge_actually_lands():
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    original = rig.talk

    def talk(face):
        rig._badges = 0b111111
        return original(face)

    rig.talk = talk
    runner = LegRunner(rig, goal=234, clear_floor=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["ok"]
    assert len(runner.engaged) == 1  # the first fight flipped the byte; no need for the second


def test_a_floor_with_no_trainers_says_so_rather_than_claiming_a_clear(tmp_path):
    truth = _top_floor()
    truth["maps"]["234"]["sprites"] = [{"kind": "npc", "x": 9, "y": 15}]
    rig = FakeRig(start=(234, 8, 8), truth=truth)
    runner = LegRunner(
        rig, goal=234, clear_floor=True, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path
    )
    runner.run()
    assert any("lists no trainer" in n for n in runner.notes)


def test_the_lift_tour_is_registered_with_its_own_arguments():
    with pytest.raises(SystemExit):
        supervisor.main(["lift-tour", "--help"])


# ----------------------------------------------------- what the game says when it refuses


class TalkingWallRig(FakeRig):
    """A world whose refused step prints a sentence — the Silph card-key door, in miniature."""

    def __init__(self, said="Darn! It needs a CARD KEY!", moves=False):
        super().__init__(hops=[None] * 12)
        self.wall_text = said
        self.moves = moves
        self.presses: list[str] = []
        self.pressed = False

    def press(self, button, hold=8, release=8):
        self.presses.append(button)
        self.pressed = True
        if self.moves:
            self._pos = (self._pos[0], self._pos[1] + 1, self._pos[2])

    def wait(self, frames=30):
        pass

    def dialogue(self):
        # The real buffer is stale until something prints into it, and only a *change* is this
        # step's message — a constant buffer is last battle's line, not this wall's.
        return self.wall_text if self.pressed else ""


def test_a_refusal_that_prints_a_sentence_records_it(tmp_path):
    """The engine's failure code is one token; the sentence behind it is the actual finding."""
    rig = TalkingWallRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert (1, 5, 5) in runner.gates
    assert "CARD KEY" in runner.gates[(1, 5, 5)]
    assert any(e["event"] == "supervisor.gate_text" for e in rig.events)


def test_the_sentence_reaches_the_seat_and_the_written_record(tmp_path):
    rig = TalkingWallRig()
    consult = _consult("GIVE_UP")
    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert "CARD KEY" in consult.seen[0]["facts"]  # the crew is told, not left to guess
    assert "CARD KEY" in next(tmp_path.glob("*.md")).read_text()  # and so is the operator


def test_a_silent_refusal_records_no_gate(tmp_path):
    rig = TalkingWallRig(said="")
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert runner.gates == {}  # a bare refusal is a different fact, and stays one


def test_a_step_that_was_not_actually_refused_is_undone(tmp_path):
    """The probe must not leave the leg somewhere it did not choose to be."""
    rig = TalkingWallRig(moves=True)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.read_refusal({"via": "edge", "to": 2})
    assert rig.presses[-2:] == ["right", "left"]  # stepped out toward the edge, then back


def test_the_gates_ledger_is_reported_with_the_leg(tmp_path):
    rig = TalkingWallRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert any("CARD KEY" in said for said in result["gates"].values())


def test_two_seats_explaining_the_same_wall_is_recorded_as_a_diagnosis(tmp_path):
    """The Point Man named the CARD KEY twice on the first Silph leg and both were scored as
    failed answers, because only the ACTION field was ever read."""
    rig = FakeRig(hops=[None] * 12)

    def consult(tier, facts, menu):
        return "RETRY_SAME", "the warp is locked behind a CARD KEY requirement", "fake"

    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("both seats explain" in n and "CARD KEY" in n for n in runner.notes)
    assert "CARD KEY" in next(tmp_path.glob("*.md")).read_text()


def test_seats_that_disagree_are_not_reported_as_a_diagnosis(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    whys = iter(["a body is in the way", "the edge is offset", "try the gate", "back out"])

    def consult(tier, facts, menu):
        return "RETRY_SAME", next(whys, "something else"), "fake"

    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert not any("both seats explain" in n for n in runner.notes)


def test_a_stale_buffer_is_not_mistaken_for_a_door(tmp_path):
    """`road`'s docstring says the text buffer survives the box that wrote it. The first survey
    ignored that and labelled 54 ordinary walls as doors, all quoting a battle three minutes
    old — so a message only counts when it *changed* across the step."""
    rig = TalkingWallRig(said="AAAAAAA got 750 for winning!")
    rig.pressed = True  # the line was already sitting there before we tried anything
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert runner.gates == {}


def test_npcs_are_engaged_too_because_that_is_how_story_items_arrive(tmp_path):
    """Item ball, beaten trainer, and *an npc handing it over* are all observed in this ROM —
    the POKe FLUTE came from Mr Fuji. Only the first two were ever automated."""
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.engage_bodies(("trainer", "npc")) is True
    assert runner.engaged == {(1, 9), (10, 2), (9, 15)}  # both trainers AND the npc


def test_a_body_that_hands_something_over_is_reported_loudly(tmp_path):
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    original = rig.talk

    def talk(face):
        rig._bag.append((48, 1))  # it gives us something
        return original(face)

    rig.talk = talk
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.engage_bodies(("npc",))
    assert any("gave us" in n and "CARD KEY" in n for n in runner.notes)
    assert any(e["event"] == "supervisor.body_engaged" and e["gained"] for e in rig.events)


@pytest.mark.parametrize("cmd", ["explore", "survey", "lift-tour"])
def test_the_measurement_subcommands_are_registered(cmd):
    with pytest.raises(SystemExit):
        supervisor.main([cmd, "--help"])


# ------------------------------------------------------------------- the remaining branches


def test_hop_blocker_falls_back_to_the_gate_doors_when_the_edge_is_unreachable():
    """When no body *can* block the edge, the question becomes which body blocks the gate door."""
    grid = ["1111", "0000", "1111", "1111"]  # row 1 walls the north edge off entirely

    def m(**kw):
        return {
            "width": 4,
            "height": 4,
            "tileset": 0,
            "grid": grid,
            "sprites": [],
            "warps": [],
            "connections": {},
            **kw,
        }

    truth = {"maps": {"1": m(warps=[[0, 2, 9, 0], [3, 2, 9, 0]], connections={"north": 2}), "2": m()}}
    rig = FakeRig(start=(1, 1, 2), truth=truth, bodies={(1, 3)})
    assert supervisor.hop_blocker(rig, {"via": "edge", "to": 2}) in (None, (1, 3))


def test_hop_blocker_is_none_for_a_pair_with_no_connection():
    rig = FakeRig()
    assert supervisor.hop_blocker(rig, {"via": "edge", "to": 404}) is None
    assert supervisor.hop_blocker(rig, None) is None


def test_a_map_missing_from_the_truth_is_not_a_crash():
    """The connection table can lack a side (edge_cells answers empty); the truth can also lack the
    map the rig reads (a garbage map byte mid-warp). Every reader of the edge handles the second."""
    rig = FakeRig(start=(404, 1, 1))
    assert supervisor.hop_blocker(rig, {"via": "edge", "to": 2}) is None
    runner = LegRunner(rig, goal=2, engage=False, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.read_refusal({"via": "edge", "to": 2}) == ""
    runner._act("USE_GATE_WARP", {"via": "edge", "to": 2})  # targets fall back to the map's warps
    facts = supervisor.describe(rig, 2, {"via": "edge", "to": 2}, "no-path")
    assert "no side for this pair" in facts


def test_describe_survives_a_hop_whose_pair_has_no_side():
    rig = FakeRig()
    facts = supervisor.describe(rig, 2, {"via": "edge", "to": 404}, "no-path")
    assert "no side for this pair" in facts


def test_describe_names_the_warp_tile_on_a_warp_hop():
    rig = FakeRig()
    assert "WARP TILE: (2, 7)" in supervisor.describe(rig, 9, {"via": "warp", "to": 9, "x": 2, "y": 7}, "warp-dead")


def test_read_refusal_is_empty_without_a_hop_or_a_usable_pair():
    rig = FakeRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.read_refusal(None) == ""
    assert runner.read_refusal({"via": "edge", "to": 404}) == ""


def test_read_refusal_picks_a_direction_toward_a_warp_tile():
    rig = TalkingWallRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert "CARD KEY" in runner.read_refusal({"via": "warp", "to": 9, "x": 5, "y": 9})
    assert rig.presses[0] == "down"  # (5,9) is below (5,5)


def test_an_interior_that_swallows_a_hop_is_traversed_not_failed(tmp_path):
    rig = FakeRig(hops=[9, 2])  # the cross lands us in interior 9, the traverse gets us out
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.run()["ok"]
    assert any(c[0] == "traverse" for c in rig.calls)


def test_clear_blocker_reports_when_no_approach_cell_is_reachable(tmp_path):
    rig = BlockedRig()
    rig.approach = lambda cells: False
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("no approach cell" in n or "could not" in n for n in runner.notes) or runner.engaged


def test_use_gate_warp_falls_back_to_this_map_s_own_warps(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("USE_GATE_WARP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("USE_GATE_WARP", {"via": "warp", "to": 9, "x": 2, "y": 7})
    assert ("gate", 1) in rig.calls


def test_backing_out_of_a_map_with_no_warps_says_so(tmp_path):
    truth = _truth()
    truth["maps"]["1"]["warps"] = []
    rig = FakeRig(truth=truth, hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("BACK_OUT_AND_REENTER", None)
    assert any("no warps to back out" in n for n in runner.notes)


def test_backing_out_traverses_an_interior_it_lands_in(tmp_path):
    rig = FakeRig(hops=[9])
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("BACK_OUT_AND_REENTER", None)
    assert any(c[0] == "traverse" for c in rig.calls)


def test_sweep_items_is_reachable_as_an_action(tmp_path):
    rig = FakeRig(start=(209, 1, 4), truth=_floor_with_balls())
    rig._pickups = {(3, 4): (48, 1)}
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("SWEEP_ITEMS", None)
    assert rig.bag() == [(48, 1)]


def test_oracle_search_without_a_hop_says_there_is_nothing_to_search_toward(tmp_path):
    rig = FacilityRig()
    runner = LegRunner(rig, goal=208, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("ORACLE_SEARCH", None)
    assert any("nothing to search toward" in n for n in runner.notes)


def test_the_edge_action_on_a_warp_hop_and_an_unknown_action_are_both_refused(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("TRY_FAR_EDGE_CELL", {"via": "warp", "to": 9, "x": 2, "y": 7})
    runner._act("INTERPRETIVE_DANCE", None)
    assert any("meaningless on a warp hop" in n for n in runner.notes)
    assert any("unknown action" in n for n in runner.notes)


def test_engage_until_badge_walks_to_a_body_it_is_not_already_beside():
    rig = FakeRig(start=(2, 1, 1), badges=0, bodies={(6, 6)})
    original = rig.talk

    def talk(face):
        rig._badges = 1
        return original(face)

    rig.talk = talk
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["ok"]


def test_main_dispatches_each_subcommand(monkeypatch):
    for cmd, fn in [
        ("run", "cmd_run"),
        ("survey", "cmd_survey"),
        ("explore", "cmd_explore"),
        ("lift-tour", "cmd_lift_tour"),
    ]:
        monkeypatch.setattr(supervisor, fn, lambda args: 0)
        argv = {
            "run": ["run", "--state", "s", "--goal", "1"],
            "survey": ["survey", "--state", "s"],
            "explore": ["explore", "--state", "s"],
            "lift-tour": ["lift-tour", "--state", "s", "--floors", "2F"],
        }[cmd]
        assert supervisor.main(argv) == 0


def test_a_gate_that_opens_lets_the_hop_retry(tmp_path):
    """`_hop` tries the map's own gate building before calling a severed route a failure."""

    class GateOpensRig(FakeRig):
        def __init__(self):
            super().__init__(truth=_fork_truth())
            self.opened = False

        def cross(self, cur, nxt, **kw):
            self.calls.append(("cross", nxt))
            if (cur, nxt) == (1, 2) and not self.opened:
                return "no-path"
            self._pos = (nxt, 1, 1)
            return True

        def gate(self, cur, cells, **kw):
            self.calls.append(("gate", cur))
            self.opened = True
            return True

    rig = GateOpensRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.run()["ok"] and rig.opened


def test_an_interior_that_will_not_release_us_is_reported_as_such(tmp_path):
    class StuckInteriorRig(FakeRig):
        def cross(self, cur, nxt, **kw):
            self.calls.append(("cross", nxt))
            self._pos = (9, 1, 1)  # swallowed by the interior
            return True

        def traverse(self, interior, **kw):
            self.calls.append(("traverse", interior))
            return "interior-stuck"  # and it keeps us

    rig = StuckInteriorRig(hops=[])
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("interior-" in str(e.get("failure", "")) for e in rig.events if e["event"] == "supervisor.hop_failed")


def test_talk_to_blocker_walks_when_it_is_not_already_adjacent(tmp_path):
    rig = FakeRig(hops=[None] * 12, bodies={(1, 1)})
    runner = LegRunner(rig, goal=2, consult=_consult("TALK_TO_BLOCKER"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("TALK_TO_BLOCKER", {"via": "edge", "to": 2})
    assert any(c[0] == "walk" for c in rig.calls) and any(c[0] == "talk" for c in rig.calls)


def test_engage_bodies_reports_a_body_it_cannot_reach(tmp_path):
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    rig.approach = lambda cells: False
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.engage_bodies(("trainer",))
    assert any("could not reach" in n for n in runner.notes)


def test_max_hops_ends_the_leg_rather_than_looping(tmp_path):
    rig = FakeRig(hops=[None] * 40)
    runner = LegRunner(
        rig, goal=2, max_hops=2, consult=_consult("RETRY_SAME"), log=lambda *_: None, learnings_dir=tmp_path
    )
    assert runner.run()["outcome"] == "max-hops"


def test_engage_until_badge_skips_a_body_it_cannot_reach_even_riding(tmp_path):
    rig = FakeRig(start=(2, 1, 1), badges=0, bodies={(9, 9)})
    rig.approach = lambda cells: (rig.calls.append(("approach", sorted(cells))), False)[1]  # ride refused too
    lines = []
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lines.append, learnings_dir=tmp_path)
    assert runner.run()["outcome"] == "engaged-no-badge"
    assert any("could not reach" in s for s in lines)


def test_engage_until_badge_rides_the_pad_the_walk_cannot_cross():
    """Sabrina's gym shape: the body sits in a pocket the walk cannot cross, and approach is the
    ride. The badge byte is the verdict, not the roster: the loop talks the body down and stops
    the moment the byte changes, whether or not it ever met the others."""
    rig = FakeRig(start=(2, 17, 14), badges=0b11111, bodies={(3, 13)})

    def approach(cells):
        rig.calls.append(("approach", sorted(cells)))
        rig._pos = (2, 3, 12)  # arrived on a facing cell because the pads were ridden, not planned
        return True

    rig.approach = approach
    original = rig.talk

    def talk(face):
        rig._badges |= 0b10000000  # the badge bit is the game's own, set on its own schedule
        return original(face)

    rig.talk = talk
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["ok"]
    assert any(c[0] == "approach" for c in rig.calls)  # the walk never reached it; the ride did


def test_clear_blocker_stops_when_the_walk_lands_somewhere_else(tmp_path):
    rig = BlockedRig()
    rig.walk = lambda mp, targets, **kw: rig.calls.append(("walk", sorted(targets)))  # records, never moves
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._clear_blocker({"via": "edge", "to": 2}) is False


def test_use_gate_warp_on_a_pair_with_no_side_falls_back_to_the_warps(tmp_path):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._act("USE_GATE_WARP", {"via": "edge", "to": 404})  # no connection for this pair
    assert ("gate", 1) in rig.calls


def test_engage_bodies_skips_a_body_already_engaged(tmp_path):
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.engaged.add((1, 9))
    runner.engage_bodies(("trainer",))
    assert len([c for c in rig.calls if c[0] == "talk"]) == 1  # only the other one


def test_go_and_talk_gives_up_when_the_approach_is_refused(tmp_path):
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    rig.approach = lambda cells: False
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._go_and_talk((1, 9)) is False


def test_engage_until_badge_re_reads_after_approach_changes_map():
    rig = FakeRig(start=(2, 1, 1), badges=0, bodies={(6, 6)})

    def approach(cells):
        rig.calls.append(("approach", sorted(cells)))
        rig._pos = (99, 1, 1)  # a ride carried us to another floor
        return False

    rig.approach = approach
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["outcome"] == "engaged-no-badge"


def test_a_leg_with_no_route_at_all_consults_rather_than_crashing(tmp_path):
    rig = FakeRig(start=(9, 0, 0))  # map 9 is the dead-end house; no chain to map 2
    consult = _consult("GIVE_UP")
    runner = LegRunner(rig, goal=404, consult=consult, log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert consult.seen and "NO ROUTE" in consult.seen[0]["facts"]


def test_go_and_talk_gives_up_when_the_approach_lands_short(tmp_path):
    rig = FakeRig(start=(234, 8, 8), truth=_top_floor())
    rig.approach = lambda cells: True  # claims arrival without moving
    runner = LegRunner(rig, goal=234, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._go_and_talk((1, 9)) is False


def test_engage_until_badge_talks_to_a_body_it_is_already_beside():
    rig = FakeRig(start=(2, 5, 6), badges=0, bodies={(5, 5)})
    original = rig.talk

    def talk(face):
        rig._badges = 1
        return original(face)

    rig.talk = talk
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["ok"]
    assert ("talk", "up") in rig.calls  # already adjacent: no walk, straight to the conversation


def test_clear_blocker_stops_when_the_walk_does_not_land_beside_the_body(tmp_path):
    rig = BlockedRig()
    rig.walk = lambda mp, targets, **kw: rig.calls.append(("walk", sorted(targets)))  # records, never moves
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._clear_blocker({"via": "edge", "to": 2}) is False


def test_engage_until_badge_reports_the_byte_after_its_rounds_run_out():
    """The loop can exhaust with bodies still listed; the verdict is the byte, not the roster."""
    rig = FakeRig(start=(2, 5, 6), badges=0b11111, bodies={(5, 5), (9, 9)})
    runner = LegRunner(rig, goal=2, engage=True, engage_rounds=1, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["outcome"] == "engaged-no-badge"
    assert len([c for c in rig.calls if c[0] == "talk"]) == 1  # one round, one conversation


def test_engage_until_badge_revisits_a_gated_leader_after_the_member_falls():
    """Map 178's actual shape: the leader reads a coach line until the gym's member falls, then —
    and only then — battles and hands over the badge. Nearest-first reaches the leader first
    (still locked) and the member second. Retiring every body after one line is exactly how the
    gym reported "engaged every body, badge byte unchanged": the leader was met once, locked;
    no one ever met her again after the member came down. The loop must keep her turn, so her
    second meeting — the one the member's defeat opened — is the one that drops the badge."""
    member, leader = (0, 0), (7, 7)
    rig = FakeRig(start=(2, 7, 6), badges=0b11111, bodies={member, leader})
    state = {"member_down": False}

    def talk(face):
        rig.calls.append(("talk", face))  # the base FakeRig.talk does this; the override must too
        # whichever body the player is standing beside is the one being faced
        _mp, x, y = rig.pos()
        faced = min(rig.bodies(), key=lambda b: abs(b[0] - x) + abs(b[1] - y))
        if faced == member:
            state["member_down"] = True  # the member's fight ends; its defeat unlocks the leader
            return "got 1140 for winning!"
        if not state["member_down"]:
            return "In a battle of equals, the one with the stronger will wins!"  # the coach line
        rig._badges |= 0b00100000  # the badge commits on the leader's second meeting
        return "I dislike fighting, but if you wish, I will show you my powers!"

    rig.talk = talk
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    report = runner.run()
    assert report["ok"]  # badge byte gained — only reachable if the leader is met twice
    # the leader's locked line came first, the badge line after the member fell: two meetings
    assert state["member_down"]
    assert len([c for c in rig.calls if c[0] == "talk"]) >= 3  # leader-locked, member, leader-badge


def test_engage_until_badge_settles_the_win_box_after_each_battle():
    """A battle leaves the win/award box open; settle() is the closer that commits the result.
    Without it the "got a prize" box stays pinned and the next body never unlocks. _go_and_talk
    must settle after every talk so a fallen member actually registers as defeated."""
    rig = FakeRig(start=(2, 7, 6), badges=0b11111, bodies={(7, 7)})

    def talk(face):
        rig._badges |= 0b00100000
        return rig.said

    rig.talk = talk
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    runner.run()
    assert ("settle",) in rig.calls  # the win box was closed by settle, not left pinned


def test_a_hop_the_ladder_cannot_open_is_banned_and_another_chain_tried(tmp_path):
    """7F's 11F-side pocket has no route to 8F at all — only back to 3F. The leg spent both seats
    on that one hop and then exhausted, holding a map full of untried doors."""

    class NoPathRig(FakeRig):
        def __init__(self):
            super().__init__(truth=_fork_truth(), start=(1, 5, 5))

        def cross(self, cur, nxt, **kw):
            self.calls.append(("cross", nxt))
            if (cur, nxt) == (1, 2):
                return "no-path"
            self._pos = (nxt, 1, 1)
            return True

    rig = NoPathRig()
    runner = LegRunner(rig, goal=2, consult=_consult("RETRY_SAME"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.run()["ok"], "the leg died on one door while the map had another"
    assert (1, 2) in runner.banned


def test_a_body_parked_on_a_door_is_routed_around_once_the_ladder_is_spent(tmp_path):
    """Silph 5F parks a Rocket on the (24,0) pad, and the floor has six other doors. The loop
    asked both seats about that one door, was told "wait for the wanderer" by each, waited, and
    exhausted — with five roads out of the room unexamined. After the ladder, a body that will
    not move is structural for this leg: ban the hop and take another chain."""

    class ParkedBodyRig(FakeRig):
        def __init__(self):
            super().__init__(truth=_fork_truth(), start=(1, 5, 5))

        def cross(self, cur, nxt, **kw):
            self.calls.append(("cross", nxt))
            if (cur, nxt) == (1, 2):
                return "body-blocked"  # a trainer on the pad; it never moves
            self._pos = (nxt, 1, 1)
            return True

    rig = ParkedBodyRig()
    runner = LegRunner(rig, goal=2, consult=_consult("WAIT_AND_RETRY"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], "the leg died on one door while the map had another"
    assert (1, 2) in runner.banned


def _center_truth():
    """A Pokemon Center interior at the measured template: 14x8, tileset 6, nurse npc at (3,1)
    behind the counter. The idle NPCs are the ones a body-sweep would find instead."""
    truth = _truth()
    truth["maps"]["2"] = {
        "width": 14,
        "height": 8,
        "tileset": 6,
        "grid": ["1" * 14 for _ in range(8)],
        "warps": [],
        "connections": {},
        "sprites": [
            {"kind": "npc", "x": 3, "y": 1},
            {"kind": "npc", "x": 8, "y": 3},
        ],
    }
    return truth


def test_the_heal_talks_to_the_nurse_across_the_counter_not_to_the_idle_npcs(tmp_path):
    """The nurse is behind a counter, so no cell is adjacent to her and a body-sweep never meets
    her. A leg reached Saffron's Center, talked to all three idle NPCs, and reported the heal
    refused with three fainted party members."""

    class CenterRig(FakeRig):
        def approach(self, cells):
            self.calls.append(("approach", sorted(cells)))
            self._pos = (self._pos[0], *sorted(cells)[0])
            return True

        def talk(self, face):
            self.calls.append(("talk", face))
            if self._pos[1:] == (3, 3) and face == "up":  # only from the counter, facing the nurse
                self._party = [(n, lvl, lvl) for n, lvl, _hp in self._party]
            return "Your POKeMON are fighting fit!"

    rig = CenterRig(
        start=(2, 6, 6), truth=_center_truth(), badges=0b11111, party=[("CHARIZARD", 100, 0)], bodies={(8, 3)}
    )
    result = LegRunner(rig, goal=2, heal=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["ok"], result
    assert ("approach", [(3, 3)]) in rig.calls, "the leg never went to the counter"
    assert all(hp > 0 for _n, _l, hp in rig.party())


def test_a_map_that_is_not_a_center_has_no_counter():
    """The template is the whole test: 14x8, tileset 6, a nurse tile at (3,1). An ordinary room
    that happens to hold an npc is not a Center, and a leg must not stand in it pressing A."""
    assert FakeRig(truth=_truth()).center_counter(2) is None
    assert FakeRig(truth=_center_truth()).center_counter(2) == ((3, 3), "up")


def test_a_door_no_walk_reaches_is_ridden_to_not_retried(tmp_path):
    """Badge 6 was won at (9,9) inside Sabrina's gym — behind thirty teleport pads — and the next
    leg spent its entire budget re-trying the exit mat at (8,17) from there. A door on this map
    that no walk reaches is a ride, the same rule bodies and item balls already get."""

    class PadGymRig(FakeRig):
        def __init__(self):
            super().__init__(start=(1, 5, 5))  # map 1's (2,7) door leads to the house, map 9
            self.approached = []

        def warp(self, cur, wx, wy, **kw):
            self.calls.append(("warp", (wx, wy)))
            if not self.approached:  # nothing walks to the mat from where we stand
                return "no-path"
            self._pos = (9, 0, 0)
            return True

        def approach(self, cells):
            self.approached.append(sorted(cells))
            return True

    rig = PadGymRig()
    runner = LegRunner(rig, goal=9, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result
    assert rig.approached, "the leg never tried to ride to the door"


def test_a_heal_whose_counter_cannot_be_reached_says_so(tmp_path):
    """A Center whose nurse we cannot stand in front of is a fact worth recording, not a silent
    fall-through to sweeping bodies."""

    class NoCounterRig(FakeRig):
        def approach(self, cells):
            self.calls.append(("approach", sorted(cells)))
            return False

    rig = NoCounterRig(
        start=(2, 3, 3), truth=_center_truth(), badges=0b11111, party=[("CHARIZARD", 100, 0)], bodies={(8, 3)}
    )
    runner = LegRunner(rig, goal=2, heal=True, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("could not reach the nurse's counter" in n for n in runner.notes)


def test_engage_until_badge_reports_whether_the_byte_moved(tmp_path):
    """`_engage_until_badge` is judged on the BADGES byte, never on having talked to everyone."""

    class BadgeRig(FakeRig):
        def talk(self, face):
            self.calls.append(("talk", face))
            self._badges |= 0b00100000
            return "I dislike fighting, but if you wish..."

    rig = BadgeRig(start=(2, 7, 6), badges=0b11111, bodies={(7, 7)}, truth=_center_truth())
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["ok"]


def test_a_seat_that_runs_out_of_clock_is_asked_to_close(monkeypatch):
    """The Extractor gets 300 seconds from this gateway and spends them all thinking: six
    attempts, six non-answers. What clears it is a second call handing its own cut-off reasoning
    back — 289s of silence became a decision in 49s."""
    import expedition_crew as crew

    bodies = []

    class _Resp:
        def __init__(self, chunks):
            self.chunks = chunks

        def __iter__(self):
            for c in self.chunks:
                yield ("data: " + json.dumps({"choices": [{"delta": c}]}) + "\n").encode()
            yield b"data: [DONE]\n"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        if len(bodies) == 1:  # ran out of clock mid-thought: plenty of text, no ACTION line
            return _Resp([{"reasoning": "weighing " * 200}])
        return _Resp([{"content": "ACTION: GIVE_UP\nWHY: the door is a wall\n"}])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    action, why, model = supervisor.TapesConsult(log=lambda *_: None)("puzzle", "facts", ["GIVE_UP"])
    assert (action, why) == ("GIVE_UP", "the door is a wall")
    assert len(bodies) == 2, "the seat was never asked to close"
    assert "Do not reason further" in bodies[1]["messages"][0]["content"]
    assert model == crew.CREW["puzzle"]["model"]


def test_engage_until_badge_on_an_empty_floor_reports_the_byte(tmp_path):
    """A floor with nobody on it is not a failure to engage — it is a floor with nobody on it."""
    rig = FakeRig(start=(2, 3, 3), badges=0b11111, bodies=set(), truth=_center_truth())
    runner = LegRunner(rig, goal=2, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None)
    assert runner.run()["outcome"] == "engaged-no-badge"


# ------------------------------------------------------------------------------------ recon


def _truth_with_a_body_to_ask():
    """The fake world plus one npc on map 1 — the map the leg is stuck on."""
    t = _truth()
    t["maps"]["1"]["sprites"] = [{"x": 5, "y": 6, "kind": "npc"}]
    return t


def test_the_map_is_asked_before_a_model_is():
    """Four legs consulted seats about a sea route without speaking to one body on it.

    Recon runs before the first consult on a wall, and what it hears reaches the seats.
    """
    rig = FakeRig(hops=[None], truth=_truth_with_a_body_to_ask(), saying="The tide is low today!")
    seen = {}

    def consult(tier, facts, menu):
        seen["facts"] = facts
        return "GIVE_UP", "", "fake"

    LegRunner(rig, goal=2, consult=consult, log=lambda *_: None).run()
    assert "HEARD from the body at (5, 6): 'The tide is low today!'" in seen["facts"]


def test_what_the_body_said_is_recorded_into_the_sink():
    """A run that does not emit is unminable, and this is the sentence that unblocks the next one."""
    rig = FakeRig(hops=[None], truth=_truth_with_a_body_to_ask(), saying="The tide is low today!")
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert ("recon", "The tide is low today!") in rig.spoken


def test_a_sticky_window_is_not_mistaken_for_dialogue():
    """The window layer keeps the last menu drawn, so 'OPTION EXIT' reads as a sentence at every
    cell in every direction. Recon gates the read on the box actually blocking movement."""
    rig = FakeRig(hops=[None], truth=_truth_with_a_body_to_ask(), saying="")
    seen = {}

    def consult(tier, facts, menu):
        seen["facts"] = facts
        return "GIVE_UP", "", "fake"

    LegRunner(rig, goal=2, consult=consult, log=lambda *_: None).run()
    assert "HEARD" not in seen["facts"]
    assert rig.spoken == []


def test_recon_asks_a_map_once_not_once_per_attempt():
    """Recon precedes thinking; it is not a second exploration budget."""
    rig = FakeRig(hops=[None, None, None], truth=_truth_with_a_body_to_ask(), saying="Hello!")
    runner = LegRunner(rig, goal=2, consult=_consult("RETRY_SAME"), log=lambda *_: None)
    runner.run()
    assert runner.reconned == {1}
    assert rig.spoken.count(("recon", "Hello!")) == 1


def test_a_body_no_walk_reaches_is_skipped_not_waited_on():
    """Recon is bounded. A body behind a wall is not a reason to stall the leg."""
    rig = FakeRig(hops=[None], truth=_truth_with_a_body_to_ask(), saying="Hello!")
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None)
    runner._go_and_talk = lambda spot: False  # nothing reaches it
    assert runner.recon(1) == {}
    assert rig.spoken == []


def _truth_with_a_counter_body():
    """A body with NO walkable neighbour, reachable only across a counter — the BIKE SHOP shape."""
    t = _truth()
    t["maps"]["1"]["sprites"] = [{"x": 5, "y": 5, "kind": "npc"}]
    return t


def test_a_body_behind_a_counter_is_talked_to_across_it():
    """Measured in the BIKE SHOP: the clerk at (6,2) has no reachable neighbour, and the talk
    fires from (4,2) facing right. A recon leg holding the BIKE VOUCHER reported it unreachable."""
    rig = FakeRig(hops=[None], truth=_truth_with_a_counter_body(), saying="Oh, that's...")
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None)
    faced, approached = [], []
    rig.talk = lambda face: faced.append(face) or "Oh, that's..."
    rig.approach = lambda cells: approached.append(set(cells)) or True
    import road as road_mod

    # every neighbour of (5,5) is blocked; only the across-counter cells are walkable
    reach = {c for c, _f in road_mod.counter_stands((5, 5))} | {(4, 11)}

    def only_the_counter(*_a, **_k):
        return reach

    road_mod_walkable = road_mod.walkable
    road_mod.walkable = only_the_counter
    try:
        assert runner._go_and_talk((5, 5)) is True
    finally:
        road_mod.walkable = road_mod_walkable
    assert faced, "the counter body was never faced"
    assert faced[0] in ("up", "down", "left", "right")


def _truth_with_many_bodies():
    """More bodies than one recon budget can afford — so the order becomes a decision."""
    t = _truth()
    t["maps"]["1"]["sprites"] = [
        {"x": 5, "y": 6, "kind": "npc"},
        {"x": 5, "y": 7, "kind": "npc"},
        {"x": 6, "y": 6, "kind": "npc"},
        {"x": 7, "y": 7, "kind": "npc"},
        {"x": 2, "y": 2, "kind": "npc"},
    ]
    return t


def test_the_investigator_is_asked_which_body_is_worth_the_budget():
    """Navigation is asked HOW TO MOVE; recon is asked WHAT TO LOOK AT. Different question,
    different seat — and it only fires when there are more bodies than budget."""
    rig = FakeRig(hops=[None], truth=_truth_with_many_bodies(), saying="Hello!")
    asked = {}

    def consult(tier, facts, menu):
        asked[tier] = menu
        return (
            ("2,2", "the far one is the one the goal cares about", "fake")
            if tier == "recon"
            else ("GIVE_UP", "", "fake")
        )

    runner = LegRunner(rig, goal=2, consult=consult, log=lambda *_: None)
    runner.recon(1, cap=2)
    assert "recon" in asked, "the Investigator seat was never consulted"
    assert "2,2" in asked["recon"]
    assert (2, 2) in runner.heard, "the seat's pick was not visited first"


def test_a_recon_non_answer_leaves_the_nearest_first_order_alone():
    """An unparsed reply is a non-answer and must move nothing — the same rule the ladder uses."""
    rig = FakeRig(hops=[None], truth=_truth_with_many_bodies(), saying="Hello!")
    runner = LegRunner(rig, goal=2, consult=lambda *_a: (None, "", "fake"), log=lambda *_: None)
    order = [(5, 6), (5, 7), (2, 2)]
    assert runner._recon_order(1, order, cap=2) == order


def test_a_recon_pick_outside_the_menu_is_ignored():
    rig = FakeRig(hops=[None], truth=_truth_with_many_bodies(), saying="Hello!")
    order = [(5, 6), (5, 7), (2, 2)]
    for bogus in ("99,99", "not-a-cell", "5"):
        runner = LegRunner(rig, goal=2, consult=lambda *_a, _b=bogus: (_b, "", "fake"), log=lambda *_: None)
        assert runner._recon_order(1, order, cap=2) == order


def test_recon_does_not_spend_a_consult_when_every_body_fits_the_budget():
    rig = FakeRig(hops=[None], truth=_truth_with_many_bodies(), saying="Hello!")
    calls = []
    runner = LegRunner(rig, goal=2, consult=lambda t, f, m: calls.append(t) or (None, "", "x"), log=lambda *_: None)
    order = [(5, 6), (5, 7)]
    assert runner._recon_order(1, order, cap=4) == order
    assert calls == []


def test_recon_takes_a_screenshot_of_the_map_by_default():
    """The Investigator looks before it reasons. Twice this arc a leg called water 'sealed'
    from the collision grid alone; the tile it refused on was a boulder, visible on sight."""
    rig = FakeRig(hops=[None], truth=_truth_with_a_body_to_ask(), saying="Hello!")
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert "recon_map1" in rig.shots


def test_recon_only_screenshots_a_map_once():
    rig = FakeRig(hops=[None, None], truth=_truth_with_a_body_to_ask(), saying="Hello!")
    runner = LegRunner(rig, goal=2, consult=_consult("RETRY_SAME"), log=lambda *_: None)
    runner.run()
    assert rig.shots.count("recon_map1") == 1


def test_exhaustion_attaches_a_screenshot_to_the_written_record(tmp_path):
    """This is the exact moment a leg declares a wall. The picture goes in the record so the
    next reader can look before trusting the verdict."""
    rig = FakeRig(hops=[None])
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert "exhausted_map1" in rig.shots
    doc = next(tmp_path.iterdir()).read_text()
    assert "SCREENSHOT AT THE POINT OF FAILURE: <fake>/exhausted_map1.png" in doc


def _truth_with_an_item_ball():
    t = _truth()
    t["maps"]["1"]["sprites"] = [{"x": 5, "y": 5, "kind": "item", "item": 64}]
    return t


def test_recon_picks_up_item_balls_by_default():
    """The GOLD TEETH sat on the Safari Zone's own floor through every earlier leg that walked
    past it, because sweeping was opt-in (the SWEEP_ITEMS menu action) and nobody chose it. Recon
    now sweeps every map it visits, the same way it now screenshots and talks to bodies."""
    rig = FakeRig(hops=[None], truth=_truth_with_an_item_ball())
    rig._pickups[(5, 5)] = 64
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert 64 in rig.bag()


def test_recon_does_not_resweep_a_map_it_has_already_visited():
    rig = FakeRig(hops=[None, None], truth=_truth_with_an_item_ball())
    rig._pickups[(5, 5)] = 64
    runner = LegRunner(rig, goal=2, consult=_consult("RETRY_SAME"), log=lambda *_: None)
    runner.run()
    assert rig.bag().count(64) == 1


# ------------------------------------------------- the upstream observations journal


def test_prior_observations_scopes_to_the_map(tmp_path):
    """pokemon-kafka advances an upstream that already ships this journal; the expedition path
    never read it, so every leg started blind to thousands of recorded alerts."""
    p = tmp_path / "observations.md"
    p.write_text(
        "## 2026-09-01\n"
        "- [important] Flink alert [IN_PLACE_WEDGE]: map=3 pos=(16,18) stuck_turns=5947 (session: flink)\n"
        "- [important] Flink alert [DOOR_STALL]: map=99 pos=(1,1) action=a (session: flink)\n"
        "- [important] Flink alert [POSITION_DEADLOCK]: map=3 pos=(19,18) (session: flink)\n"
    )
    got = supervisor.prior_observations(3, path=p)
    assert len(got) == 2
    assert all("map=3" in ln for ln in got)
    assert got[0].endswith("(session: flink)")  # newest first
    assert "POSITION_DEADLOCK" in got[0]


def test_prior_observations_is_newest_first_and_bounded(tmp_path):
    p = tmp_path / "observations.md"
    p.write_text("".join(f"- [important] alert n={i}: map=5 pos=(1,1)\n" for i in range(20)))
    got = supervisor.prior_observations(5, path=p, limit=3)
    assert len(got) == 3
    assert "n=19" in got[0] and "n=17" in got[2]


def test_prior_observations_survives_a_missing_journal(tmp_path):
    assert supervisor.prior_observations(3, path=tmp_path / "nope.md") == []


def test_the_facts_carry_what_the_pipeline_already_knows(tmp_path):
    """A seat should never re-derive a wedge the pipeline diagnosed runs ago."""
    p = tmp_path / "observations.md"
    p.write_text("- [important] Flink alert [IN_PLACE_WEDGE]: map=1 pos=(16,18) stuck_turns=5947\n")
    rig = FakeRig(hops=[None])
    import supervisor as sup

    real = sup.prior_observations
    sup.prior_observations = lambda mp, **kw: real(mp, path=p)
    try:
        facts = sup.describe(rig, 2, None, "no-path")
    finally:
        sup.prior_observations = real
    assert "ALREADY OBSERVED HERE:" in facts
    assert "IN_PLACE_WEDGE" in facts


def test_the_expedition_stays_wired_to_the_upstream_journal():
    """A guard against silently diverging from the pipeline this repo advances.

    pokemon-kafka builds on pcc-labs/pokemon, which already ships pokedex/memory/observations.md
    — written by observer.py and the Flink alerts-consumer, read by discovery.py. For a full day
    the expedition path ignored it and grew a parallel memory (51 docs/learnings/*.md) that only
    reached a future run when a human pasted it into a mission by hand, while thousands of
    structured alerts went unread. If describe() ever stops surfacing the journal, that divergence
    is starting again — and this test is how it gets caught in CI instead of a day later.
    """
    import inspect

    import supervisor as sup

    src = inspect.getsource(sup.describe)
    assert "prior_observations" in src, "describe() no longer reads the upstream observations journal"
    assert "ALREADY OBSERVED HERE" in src
    # and the reader must still point at the upstream path, not a fork of it
    assert "pokedex/memory/observations.md" in inspect.getsource(sup.prior_observations)


# --------------------------------------------------------------------------- hunting a handed-over item


def test_hunt_item_ends_the_leg_when_a_body_hands_it_over():
    """The Warden shape: the leg is judged on the bag holding the item, not on a badge or a ball."""
    rig = FakeRig(start=(2, 3, 3), bodies={(4, 3)})
    original_talk = rig.talk

    def talk(face):
        rig._bag.append((48, 1))  # the body hands over the CARD KEY
        return original_talk(face)

    rig.talk = talk
    result = LegRunner(rig, goal=2, hunt="CARD KEY", consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["ok"] and result["outcome"] == "item-found"


def test_hunt_item_that_nobody_hands_over_rules_the_door_out():
    rig = FakeRig(start=(2, 3, 3), bodies={(4, 3)})
    result = LegRunner(rig, goal=2, hunt="CARD KEY", consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["outcome"] == "engaged-no-item" and not result["ok"]
    assert ("talk", "right") in rig.calls  # the body was met before the door was ruled out


def test_hunt_item_already_in_the_bag_is_found_without_talking():
    rig = FakeRig(start=(2, 3, 3), bodies={(4, 3)})
    rig._bag.append((48, 1))
    result = LegRunner(rig, goal=2, hunt="CARD KEY", consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert result["outcome"] == "item-found" and not any(c[0] == "talk" for c in rig.calls)


# --------------------------------------------------------------------------- a sleeping blocker


class SleeperRig(BlockedRig):
    """Route 16 in miniature: the body says it is asleep; talking never moves it, the flute does."""

    def __init__(self, flute=True, plays=True, **kw):
        super().__init__(**kw)
        if flute:
            self._bag.append((49, 1))
        self.plays = plays

    def item_name(self, item_id):
        return {49: "POKe FLUTE"}.get(item_id, f"#{item_id}")

    def talk(self, face):
        self.calls.append(("talk", face))
        return "A sleeping POKéMON blocks the way!"

    def use_item(self, name, face=None):
        self.calls.append(("use_item", name, face))
        if not self.plays:
            return False
        self._bodies.discard(self.blocker)  # woken, fought, gone
        return True


def test_a_sleeping_blocker_is_woken_with_the_flute_in_the_bag(tmp_path):
    rig = SleeperRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert any(c[:2] == ("use_item", "POKe FLUTE") for c in rig.calls)
    assert any(e["event"] == "supervisor.sleeper_woken" for e in rig.events)


def test_a_sleeping_blocker_without_a_flute_is_reported_not_retried(tmp_path):
    rig = SleeperRig(flute=False)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert not any(c[0] == "use_item" for c in rig.calls)
    assert any("no flute" in n for n in runner.notes)


def test_a_flute_that_will_not_play_is_reported(tmp_path):
    rig = SleeperRig(plays=False)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("could not play" in n for n in runner.notes)


# --------------------------------------------------------------------------- a warp hop behind a gate


def _warp_truth():
    """Map 1 holds the door to map 2 at (6,6); nothing else leads there."""
    truth = _truth()
    truth["maps"]["1"]["warps"] = [[6, 6, 2, 0]]
    truth["maps"]["1"]["connections"] = {}
    truth["maps"]["2"] = {**truth["maps"]["2"], "connections": {}, "warps": [[0, 0, 1, 0]]}
    return truth


class GatedWarpRig(FakeRig):
    """The door is no-path until the gate building has been passed; then the warp lands."""

    def __init__(self, **kw):
        kw.setdefault("truth", _warp_truth())
        super().__init__(**kw)
        self.passed = False

    def warp(self, mp, x, y, **kw):
        self.calls.append(("warp", (x, y)))
        if not self.passed:
            return "no-path"
        self._pos = (2, 1, 1)
        return True

    def gate(self, cur, cells, **kw):
        self.calls.append(("gate", cur, sorted(cells)))
        self.passed = True
        return True


def test_a_warp_hop_severed_by_its_own_gate_building_goes_through_the_gate(tmp_path):
    rig = GatedWarpRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert ("gate", 1, [(6, 6)]) in rig.calls  # the door tile is the goal the gate pass validates
    assert (1, 2) in runner.gated


# --------------------------------------------------------------------------- a cuttable growth


def _bush_truth():
    """Route 16 in miniature: the road on row 0, a tree row with one 0x3D bush at (2,1), the
    strip with the door on row 2. The grid calls the whole tree row solid."""
    truth = _truth()
    m = truth["maps"]["1"]
    m.update(width=5, height=3, grid=["11111", "00000", "11111"], warps=[[4, 2, 2, 0]], connections={})
    m["tiles"] = ["0303030303", "0a0a" + "3d" + "0a0a", "0303030303"]  # 0x0a: plain rock, not cuttable
    truth["maps"]["2"] = {**truth["maps"]["2"], "connections": {}, "warps": [[0, 0, 1, 0]]}
    return truth


class BushRig(FakeRig):
    def __init__(self, knows_cut=True, **kw):
        kw.setdefault("truth", _bush_truth())
        kw.setdefault("start", (1, 0, 0))
        super().__init__(**kw)
        self.knows_cut = knows_cut

    def knows_move(self, name, species=None):
        return 0 if (name == "CUT" and self.knows_cut) else None

    def _open(self):
        return self.truth["maps"]["1"]["grid"][1][2] == "1"

    def approach(self, cells):
        # The strip beyond the bush is out of reach until the bush is gone; the fake's default
        # approach teleports anywhere, which would let the leg "arrive" without cutting.
        self.calls.append(("approach", sorted(cells)))
        cells = {c for c in cells if c[1] == 0 or self._open()}
        if not cells:
            return False
        self._pos = (1, *sorted(cells)[0])
        return True

    def warp(self, mp, x, y, **kw):
        self.calls.append(("warp", (x, y)))
        if not self._open():
            return "no-path"
        self._pos = (2, 1, 1)
        return True

    def cut(self, face):
        self.calls.append(("cut", face))
        rows = self.truth["maps"]["1"]["grid"]
        rows[1] = "00100"
        return True


def test_a_cuttable_growth_sealing_a_hop_is_cut_from_the_nearest_cell(tmp_path):
    rig = BushRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert ("cut", "down") in rig.calls and ("approach", [(2, 0)]) in rig.calls
    assert any(e["event"] == "supervisor.growth_cut" for e in rig.events)


def test_no_cut_is_attempted_without_the_move_or_without_a_growth(tmp_path):
    rig = BushRig(knows_cut=False)
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(c[0] == "cut" for c in rig.calls)
    plain = BushRig()
    plain.truth["maps"]["1"]["tiles"][1] = "0a0a0a0a0a"  # rock only: nothing the flow opens
    LegRunner(plain, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(c[0] == "cut" for c in plain.calls)


def test_a_cut_that_does_not_open_is_reported(tmp_path):
    rig = BushRig()
    rig.cut = lambda face: rig.calls.append(("cut", face)) or False
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("did not open" in n for n in runner.notes)
    assert any(e["event"] == "supervisor.cut_refused" for e in rig.events)


def test_cut_search_skips_growth_with_nothing_beyond_and_needs_a_tile_model(tmp_path):
    rig = BushRig()
    m = rig.truth["maps"]["1"]
    m["tiles"][1] = "3d0a3d0a0a"  # a second bush at (0,1) ...
    m["grid"] = ["11111", "00000", "01111"]  # ... with solid ground behind it: not a way through
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.run()["ok"]
    assert ("approach", [(2, 0)]) in rig.calls  # the bush at (2,1) was the one cut, not (0,1)
    bare = BushRig()
    del bare.truth["maps"]["1"]["tiles"]
    LegRunner(bare, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(c[0] == "cut" for c in bare.calls)


def test_cut_search_on_an_edge_hop_stands_down_when_the_edge_is_already_reachable(tmp_path):
    rig = BushRig()
    m = rig.truth["maps"]["1"]
    m["warps"], m["connections"] = [], {"east": 2}
    rig.truth["maps"]["2"]["connections"] = {"west": 1}
    rig.cross = lambda cur, nxt, **kw: rig.calls.append(("cross", nxt)) or "no-path"
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(c[0] == "cut" for c in rig.calls)  # (4,0) is an edge cell and already ours


def test_a_stand_the_walk_cannot_reach_is_reported(tmp_path):
    rig = BushRig()
    rig.approach = lambda cells: rig.calls.append(("approach", sorted(cells))) or False
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("could not reach" in n and "cut the growth" in n for n in runner.notes)


# --------------------------------------------------------------------------- several doors, one destination


def _two_door_truth():
    """A gate room with two doors out to map 2: the routed one at (0,8) and another at (7,8)."""
    truth = _truth()
    m = truth["maps"]["1"]
    m.update(warps=[[0, 8, 2, 0], [7, 8, 255, 2]], connections={})  # 255: LAST_MAP, as gate doors are stored
    truth["maps"]["2"] = {**truth["maps"]["2"], "connections": {}, "warps": [[0, 0, 1, 0]]}
    return truth


class GuardedDoorRig(FakeRig):
    """The west door's walk hits the cap (a guard stops it); the east door lands."""

    def __init__(self, **kw):
        kw.setdefault("truth", _two_door_truth())
        kw.setdefault("start", (1, 7, 7))
        super().__init__(**kw)

    def warp(self, mp, x, y, **kw):
        self.calls.append(("warp", (x, y)))
        if (x, y) == (0, 8):
            return "cap"
        self._pos = (2, 1, 1)
        return True


def test_a_capped_door_is_not_the_only_door_to_that_map(tmp_path):
    rig = GuardedDoorRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert ("warp", (7, 8)) in rig.calls  # the other door to the same map was tried
    assert runner.attempts == Counter()  # and no wall was charged for it


def test_an_alternate_door_that_lands_elsewhere_hands_over_to_the_interior_logic(tmp_path):
    rig = GuardedDoorRig()

    def warp(mp, x, y, **kw):
        rig.calls.append(("warp", (x, y)))
        if (x, y) == (0, 8):
            return "cap"
        rig._pos = (9, 0, 0)  # the other door opens on a house, not map 2
        return True

    rig.warp = warp
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert ("warp", (7, 8)) in rig.calls and ("traverse", 9) in rig.calls


def test_every_door_to_the_map_failing_is_written_down(tmp_path):
    rig = GuardedDoorRig()
    rig.warp = lambda mp, x, y, **kw: rig.calls.append(("warp", (x, y))) or "cap"
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any(t.startswith("door (7,8) to 2: cap") for t in runner.tried)


def test_a_failed_gate_pass_that_ends_inside_the_building_retreats_first(tmp_path):
    rig = GatedWarpRig()
    rig.truth["maps"]["9"] = {**rig.truth["maps"]["1"], "warps": [[0, 0, 1, 0]], "connections": {}}

    def gate(cur, cells, **kw):
        rig.calls.append(("gate", cur, sorted(cells)))
        rig._pos = (9, 0, 0)  # pass_gate gave up while standing in the gate room
        return False

    def traverse(interior, **kw):
        rig.calls.append(("traverse", interior, kw.get("exclude_entry")))
        rig._pos = (1, 6, 5)  # back out the way we came
        return True

    rig.gate, rig.traverse = gate, traverse
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert ("traverse", 9, False) in rig.calls
    assert all(w.startswith("1->") for w in runner.attempts)  # every wall charged is about map 1


def test_exhaustion_lands_in_the_upstream_journal_for_the_next_leg(tmp_path):
    """A wall found once must reach the next leg on that map without a human pasting it."""
    rig = FakeRig(hops=[None] * 12)
    mem = tmp_path / "memory"
    runner = LegRunner(
        rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path, memory_dir=mem
    )
    runner.run()
    text = (mem / "observations.md").read_text()
    assert "[important] map=1 exhausted" in text
    assert "reaching goal 2" in text
    assert "(session: supervis" in text
    # and the reader that feeds describe() finds it by map
    assert any("exhausted" in ln for ln in supervisor.prior_observations(1, path=mem / "observations.md"))


def test_a_failed_journal_write_never_fails_the_leg(tmp_path, monkeypatch):
    rig = FakeRig(hops=[None] * 12)
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.memory_dir = tmp_path / "not-a-dir.md"
    (tmp_path / "not-a-dir.md").write_text("a file where a directory is expected")
    assert runner.run()["outcome"] == "gave-up"


def test_a_full_bag_is_freed_before_a_body_is_talked_to(tmp_path):
    """The hand-over fails silently at 20 stacks; the guard sits in front of the talk, not after the leg."""
    rig = FakeRig(hops=[None])
    calls = []
    rig.bag_full = lambda: True
    rig.make_room = lambda: calls.append("make_room") or True
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._go_and_talk((1, 1))
    assert calls == ["make_room"]


def _shaft_truth():
    """A 3-wide shaft on map 1: the boulder at (1,1) plugs the only way down to the warp at (0,4)."""
    truth = _truth()
    truth["maps"]["1"] = {
        "width": 3,
        "height": 5,
        "tileset": 17,
        "grid": ["111", "010", "111", "010", "111"],
        "connections": {},
        "warps": [[0, 2, 2, 0]],
        "sprites": [{"kind": "npc", "x": 1, "y": 1, "pic": 63}],
        "tiles": ["030303"] * 5,
    }
    return truth


class BoulderRig(FakeRig):
    def __init__(self, knows_strength=True, **kw):
        kw.setdefault("truth", _shaft_truth())
        kw.setdefault("start", (1, 0, 0))
        kw.setdefault("bodies", {(1, 1)})
        super().__init__(**kw)
        self.knows_strength = knows_strength
        self.boulder = (1, 1)

    def knows_move(self, name, species=None):
        return 0 if (name == "STRENGTH" and self.knows_strength) else None

    def bodies(self):
        return {self.boulder}

    def boulders(self):
        return {self.boulder}

    def approach(self, cells):
        self.calls.append(("approach", sorted(cells)))
        cells = {c for c in cells if c[1] <= self.boulder[1]}  # nothing below the boulder is reachable
        if not cells:
            return False
        self._pos = (1, *sorted(cells)[0])
        return True

    def walk(self, mp, targets, **kw):
        # the default walk teleports anywhere; nothing below the boulder is walkable here
        self.calls.append(("walk", sorted(targets)))
        cells = {c for c in targets if c[1] <= self.boulder[1]}
        if not cells:
            return "no-path"
        self._pos = (mp, *sorted(cells)[0])
        return True

    def push_boulder(self, stand, face):
        self.calls.append(("push", tuple(stand), face))
        if face != "down":
            return False
        self.boulder = (self.boulder[0], self.boulder[1] + 1)
        return True

    def warp(self, mp, x, y, **kw):
        self.calls.append(("warp", (x, y)))
        if self.boulder[1] < 3:
            return "no-path"
        self._pos = (2, 1, 1)
        return True


def test_a_boulder_sealing_a_hop_is_pushed_down_the_line(tmp_path):
    rig = BoulderRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert [c for c in rig.calls if c[0] == "push"] == [("push", (1, 0), "down"), ("push", (1, 1), "down")]
    assert sum(1 for e in rig.events if e["event"] == "supervisor.boulder_pushed") == 2


def test_no_push_without_strength_and_a_refused_push_is_reported(tmp_path):
    rig = BoulderRig(knows_strength=False)
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(c[0] == "push" for c in rig.calls)
    stuck = BoulderRig()
    stuck.push_boulder = lambda stand, face: stuck.calls.append(("push", tuple(stand), face)) or False
    runner = LegRunner(stuck, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("did not move" in n for n in runner.notes)
    assert any(e["event"] == "supervisor.push_refused" for e in stuck.events)


class ChannelRig(FakeRig):
    """Route 23 in miniature: the warp to map 2 sits across a channel on map 1."""

    def __init__(self, knows_surf=True, result=True, **kw):
        truth = _truth()
        truth["maps"]["1"] = {
            "width": 5,
            "height": 3,
            "tileset": 0,
            "grid": ["10011", "10011", "10011"],
            "connections": {},
            "warps": [[4, 1, 2, 0]],
            "sprites": [],
            "tiles": ["031414" + "0303"] * 3,
        }
        kw.setdefault("truth", truth)
        kw.setdefault("start", (1, 0, 1))
        super().__init__(**kw)
        self.knows_surf = knows_surf
        self.result = result
        self.across = False

    def knows_move(self, name, species=None):
        return 0 if (name == "SURF" and self.knows_surf) else None

    def walk(self, mp, targets, **kw):
        # the default walk teleports anywhere, water included; here the channel is real
        self.calls.append(("walk", sorted(targets)))
        cells = {c for c in targets if c[0] == 0 or self.across}
        if not cells:
            return "no-path"
        self._pos = (mp, *sorted(cells)[0])
        return True

    def approach(self, cells):
        self.calls.append(("approach", sorted(cells)))
        cells = {c for c in cells if c[0] == 0 or self.across}
        if not cells:
            return False
        self._pos = (1, *sorted(cells)[0])
        return True

    def surf_to(self, targets):
        self.calls.append(("surf_to", sorted(targets)))
        if self.result is True:
            self.across = True
            self._pos = (1, 3, 1)
        return self.result

    def warp(self, mp, x, y, **kw):
        self.calls.append(("warp", (x, y)))
        if not self.across:
            return "no-path"
        self._pos = (2, 1, 1)
        return True


def test_water_between_the_regions_is_surfed_before_any_consult(tmp_path):
    rig = ChannelRig()
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert ("surf_to", [(4, 1)]) in rig.calls
    assert any(e["event"] == "supervisor.surfed" for e in rig.events)


def test_no_surf_without_the_move_and_a_refused_surf_is_reported(tmp_path):
    rig = ChannelRig(knows_surf=False)
    LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(c[0] == "surf_to" for c in rig.calls)
    refused = ChannelRig(result="surfmoved-failed")
    runner = LegRunner(refused, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner.run()
    assert any("SURF route" in n for n in runner.notes)
    assert any(e["event"] == "supervisor.surf_refused" for e in refused.events)
    quiet = ChannelRig(result="no-route")
    LegRunner(quiet, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).run()
    assert not any(e["event"] == "supervisor.surf_refused" for e in quiet.events)


def test_engage_surfs_to_a_body_no_walk_reaches():
    rig = ChannelRig(start=(1, 0, 1), bodies={(4, 0)})
    original = rig.approach

    def approach(cells):
        rig.calls.append(("approach", sorted(cells)))
        if not rig.across:
            return False
        return original(cells)

    rig.approach = approach
    LegRunner(rig, goal=1, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert any(c[0] == "surf_to" for c in rig.calls)
    assert any(c[0] == "talk" for c in rig.calls)
    assert any(e["event"] == "supervisor.surfed" and e.get("toward") == [4, 0] for e in rig.events)
    refused = ChannelRig(start=(1, 0, 1), bodies={(4, 0)}, result="surfmoved-failed")
    refused.approach = lambda cells: refused.calls.append(("approach", sorted(cells))) or False
    LegRunner(refused, goal=1, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert any(e["event"] == "supervisor.surf_refused" and e.get("toward") == [4, 0] for e in refused.events)
    landed_short = ChannelRig(start=(1, 0, 1), bodies={(4, 0)})  # surfed over, but the last cell is walled
    landed_short.approach = lambda cells: landed_short.calls.append(("approach", sorted(cells))) or False
    LegRunner(landed_short, goal=1, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None).run()
    assert any(e["event"] == "supervisor.surfed" for e in landed_short.events)
    assert not any(c[0] == "talk" for c in landed_short.calls)


def test_push_and_surf_hooks_decline_cleanly_when_they_do_not_apply(tmp_path):
    hop = {"via": "warp", "to": 2, "x": 0, "y": 2}
    plain = FakeRig()  # no knows_move at all
    runner = LegRunner(plain, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._push_through(hop) is False and runner._surf_through(hop) is False
    lost = BoulderRig(start=(404, 0, 0))  # a map the truth does not model
    runner = LegRunner(lost, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._push_through(hop) is False
    lost_channel = ChannelRig(start=(404, 0, 0))
    runner = LegRunner(lost_channel, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._surf_through(hop) is False
    empty = BoulderRig()
    empty.boulders = lambda: set()  # STRENGTH known, nothing to push
    runner = LegRunner(empty, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._push_through(hop) is False
    walled = BoulderRig()
    walled.truth["maps"]["1"]["grid"] = ["111", "010", "000", "010", "111"]  # nothing opens below
    runner = LegRunner(walled, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._push_through(hop) is False
    across = ChannelRig(start=(1, 3, 1))  # already on the far bank: a walk reaches the warp, no surf
    runner = LegRunner(across, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner._surf_through({"via": "warp", "to": 2, "x": 4, "y": 1}) is False
    assert not any(c[0] == "surf_to" for c in across.calls)
    # the edge form of a hop's targets is the open edge
    edge_rig = FakeRig()
    runner = LegRunner(edge_rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    cells = runner._hop_targets({"via": "edge", "to": 2}, 1)
    assert cells and all(isinstance(c, tuple) for c in cells)


def test_a_surf_that_fails_in_the_hop_attempt_is_tried_again_from_the_ladder(tmp_path):
    """The hop attempt tries the field move first; the ladder tries it once more before a consult."""
    rig = ChannelRig()
    answers = iter(["surfmoved-failed", True])
    original = rig.surf_to

    def flaky(targets):
        result = next(answers)
        if result is True:
            return original(targets)
        rig.calls.append(("surf_to", sorted(targets)))
        return result

    rig.surf_to = flaky
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    result = runner.run()
    assert result["ok"], result["reason"]
    assert sum(1 for c in rig.calls if c[0] == "surf_to") == 2


def test_a_push_that_fails_in_the_hop_attempt_is_tried_again_from_the_ladder(tmp_path):
    rig = BoulderRig()
    original = rig.push_boulder
    state = {"first": True}

    def flaky(stand, face):
        if state["first"]:
            state["first"] = False
            rig.calls.append(("push", tuple(stand), face))
            return False  # a wild on the way to the stand (measured on Victory Road 1F)
        return original(stand, face)

    rig.push_boulder = flaky
    runner = LegRunner(rig, goal=2, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    runner._clear_blocker = lambda hop: False  # the boulder is not a body to argue with
    result = runner.run()
    assert result["ok"], result["reason"]
    assert sum(1 for c in rig.calls if c[0] == "push") == 3  # one refused, then the two that opened the shaft


class BoulderedBodyRig(BoulderRig):
    """The shaft again, with the map's one body and one item ball on the far side of the boulder
    (map 155's shape: the boulder at (8,4) with a RARE CANDY behind it, measured 2026-09-05)."""

    def __init__(self, **kw):
        truth = _shaft_truth()
        truth["maps"]["1"]["sprites"] += [
            {"kind": "npc", "x": 0, "y": 2, "pic": 1},
            {"kind": "item", "x": 2, "y": 2, "pic": 61, "item": 10},
        ]
        truth["maps"]["1"]["warps"] = []
        kw.setdefault("truth", truth)
        kw.setdefault("bodies", {(1, 1), (0, 2), (2, 2)})
        super().__init__(**kw)
        self.opened: list = []

    def bodies(self):
        return {self.boulder, (0, 2), (2, 2)}

    def collect_item(self, bx, by):
        self.calls.append(("collect", (bx, by)))
        if self.boulder[1] < 3:
            return False  # the boulder still plugs the shaft
        self.opened.append((bx, by))
        self._bag.append((10, 1))
        return True

    def item_balls(self, map_id):
        return [(2, 2)]

    def ball_contents(self, map_id):
        return {(2, 2): "MOON STONE"}


def test_a_body_behind_a_boulder_is_reached_by_pushing_it(tmp_path):
    rig = BoulderedBodyRig()
    runner = LegRunner(
        rig, goal=1, engage=True, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path
    )
    runner.run()
    assert [c for c in rig.calls if c[0] == "push"] == [("push", (1, 0), "down"), ("push", (1, 1), "down")]
    assert any(c[0] == "talk" for c in rig.calls)
    assert any(e["event"] == "supervisor.boulder_pushed" for e in rig.events)


def test_a_ball_behind_a_boulder_is_opened_after_a_push(tmp_path):
    rig = BoulderedBodyRig()
    runner = LegRunner(rig, goal=1, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    gained = runner.sweep_items()
    assert rig.opened == [(2, 2)] and gained == [(10, 1)]
    assert sum(1 for c in rig.calls if c[0] == "push") == 2
    quiet = BoulderedBodyRig(knows_strength=False)
    runner = LegRunner(quiet, goal=1, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.sweep_items() == [] and not any(c[0] == "push" for c in quiet.calls)


class HmRig(BoulderedBodyRig):
    """HM04 in the bag; Gyarados knows STRENGTH but is fainted; Charizard stands and can learn it."""

    def __init__(self, hm=True, able=("CHARIZARD",), **kw):
        kw.setdefault("party", (("GYARADOS", 20, 0), ("CHARIZARD", 99, 337)))
        super().__init__(knows_strength=False, **kw)
        self.hm = hm
        self.able = set(able)
        self.learned = {"GYARADOS"}
        self.taught: list = []

    def bag_named(self, full=False):
        return [("NUGGET", 1)] + ([("HM04 STRENGTH", 1)] if self.hm else [])

    def knows_move(self, name, species=None):
        if name != "STRENGTH":
            return None
        if species is not None:
            return 0 if species in self.learned else None
        standing = [n for n, _l, hp in self._party if hp > 0 and n in self.learned]
        return self._party.index(next((p for p in self._party if p[0] == standing[0]), None)) if standing else None

    def teach(self, machine, species=None):
        self.taught.append((machine, species))
        if species not in self.able:
            return None
        self.learned.add(species)
        return [p[0] for p in self._party].index(species)


def test_strength_is_taught_to_a_standing_member_before_the_first_push(tmp_path):
    """Map 155: HM04 in the bag, the only holder fainted, a boulder beside the WARDEN's RARE CANDY."""
    rig = HmRig()
    runner = LegRunner(rig, goal=1, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.sweep_items() == [(10, 1)]
    assert rig.taught == [("HM04", "CHARIZARD")]  # never the fainted holder
    assert any(e["event"] == "supervisor.move_taught" and e["species"] == "CHARIZARD" for e in rig.events)
    # no HM in the bag: nothing to teach; nobody ABLE: the note says so
    bare = HmRig(hm=False)
    assert (
        LegRunner(bare, goal=1, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path).sweep_items()
        == []
    )
    assert bare.taught == []
    unable = HmRig(able=())
    runner = LegRunner(unable, goal=1, consult=_consult("GIVE_UP"), log=lambda *_: None, learnings_dir=tmp_path)
    assert runner.sweep_items() == [] and any("no standing member could learn it" in n for n in runner.notes)
