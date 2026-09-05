"""Loop supervisor — the leg's loop body, and the cross-run loop that keeps relaunching it.

Two halves, both here on purpose.

**The loop body** (``run``) is the one the expedition skill promises: deterministic Python boots
a baton, looks the topology up in the extracted truth, and walks it hop by hop. When a hop
fails, the supervisor does not guess and does not hand the wheel to a model — it measures the
failure, builds a *bounded menu* of actions the road engine can actually execute, and asks the
seated crew member (`scripts/expedition_crew.py`: navigation, then puzzle, never Anthropic) to
pick exactly one. A wrong answer costs one attempt. When the ladder is exhausted the run writes
``docs/learnings/`` and emits ``supervisor.exhausted`` for the operator, and stops.

    uv run python scripts/supervisor.py run --state <baton> --goal <map> --budget 7200

**The cross-run loop** (``classify-exit``/``observe``/``replay``) is the older, outer half, owned
by ``scripts/expedition_run.sh``: it decides what happens when a whole operator process exits.
Mission text is measured exhausted — six early exits across three models (five Haiku, one
Sonnet) were each told in-mission not to, and did — so those levers live out here:

- **exit classification** (`classify-exit`): the expedition runner calls this when an operator
  process ends. Harness death -> resume from the newest baton (bounded); a baton -> next leg;
  budget left -> a continuation relaunch with the pending evidence in the prompt (bounded);
  budget exhausted without a baton -> the attempt is charged against the run's dominant wall
  fingerprint, and at ``escalate_after`` attempts on the same fingerprint the decision becomes
  ``escalate`` (the Opus fix-source tier).
- **wall fingerprints** (`observe`): map-pair springs (A->B->A transitions, the door-mat class
  that is 3-for-3 as the wall) and stalls (no new position across a poll window), parsed from
  lane logs' ``MAP CHANGE`` lines. Springs survive load (they are real transitions); the stall
  nudge is suppressed when the box is loaded, per the Brock-day rule — starvation looks like a
  wall and must never be reported as one.
- **nudges**: one per fingerprint per run (the ``MTMOON-MISS`` once-per-map pattern), emitted
  into the continuation prompt so a relaunched operator starts with "you have hit <wall> N
  times; change dimension (code vs genome vs route)" instead of rediscovering it.

State is a JSON file owned by scripts/expedition_run.sh, carried across resumes and legs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:  # pragma: no cover - imported from repo root and scripts/ alike
    sys.path.insert(0, str(SCRIPT_DIR))

MAP_CHANGE = re.compile(r"MAP CHANGE \| (\d+) -> (\d+)")

SPRING_MIN = 6  # A->B->A round trips before a map pair is a fingerprint, not a heal trip
DEFAULT_MAX_CONTINUATIONS = 3
DEFAULT_MAX_RESUMES = 5
DEFAULT_ESCALATE_AFTER = 3
CONTINUE_BELOW = 0.8  # exit with < this fraction of budget used -> continuation


def _flushing_print(msg: str) -> None:  # pragma: no cover - stdout wiring
    """A leg's log line, flushed. Redirected to a file, `print` buffers ~8 KB — so a run that
    was walking fine looked hung for six minutes with an empty log, and the only way to watch a
    leg was to wait for it to end."""
    print(msg, flush=True)


def spring_counts(text: str) -> Counter:
    """Count A<->B round trips from a lane log's MAP CHANGE lines. A round trip is a transition
    immediately undone (58->2 then 2->58): the door-mat spring signature, hundreds per second
    when live (Sonnet's probe13: 232+ on one pair)."""
    pairs = MAP_CHANGE.findall(text)
    springs: Counter = Counter()
    i = 0
    while i < len(pairs) - 1:
        (a, b), (c, d) = pairs[i], pairs[i + 1]
        if b == c and d == a:  # the second transition undoes the first: one bounce
            springs[f"{min(int(a), int(b))}<->{max(int(a), int(b))}"] += 1
            i += 2
        else:
            i += 1
    return springs


class Supervisor:
    def __init__(
        self,
        *,
        max_continuations: int = DEFAULT_MAX_CONTINUATIONS,
        max_resumes: int = DEFAULT_MAX_RESUMES,
        escalate_after: int = DEFAULT_ESCALATE_AFTER,
    ) -> None:
        self.max_continuations = max_continuations
        self.max_resumes = max_resumes
        self.escalate_after = escalate_after
        self.continuations = 0
        self.resumes = 0
        self.fingerprints: Counter = Counter()  # wall id -> leg-attempts charged against it
        self.springs: Counter = Counter()  # wall id -> observed round trips (evidence, not attempts)
        self.nudged: set[str] = set()
        self.last_positions: str | None = None  # progress signature from the previous poll

    # ---- observation ----------------------------------------------------------------------

    def observe(self, lane_logs: list[str], *, positions: str | None = None, load_ok: bool = True) -> list[str]:
        """Fold lane-log text into fingerprints; return new nudges (one per fingerprint, ever)."""
        nudges = []
        for text in lane_logs:
            for wall, n in spring_counts(text).items():
                if n >= SPRING_MIN:
                    self.springs[wall] += n
                    if wall not in self.nudged:
                        self.nudged.add(wall)
                        nudges.append(
                            f"WALL {wall}: a door/edge spring measured {n}x this leg. No genome knob has ever "
                            f"moved one (0 for ~2000 applied patches); it is code or route. Consult "
                            f"`uv run python scripts/rom_truth.py route <here> <goal>` before another attempt."
                        )
        if positions is not None:
            if positions == self.last_positions and load_ok:
                if "stall" not in self.nudged:
                    self.nudged.add("stall")
                    nudges.append(
                        "STALL: no new position since the last poll. State the furthest coordinate reached "
                        "and change dimension (code vs genome vs route) before repeating the approach."
                    )
            self.last_positions = positions
        return nudges

    # ---- exit classification --------------------------------------------------------------

    def classify_exit(self, *, budget_s: float, used_s: float, baton: bool, harness_death: bool) -> dict:
        """The expedition runner's one call per operator exit. Returns {action, reason, prompt?}."""
        if harness_death:
            if self.resumes >= self.max_resumes:
                return {"action": "stop_alert", "reason": f"harness death #{self.resumes + 1} exceeds resume budget"}
            self.resumes += 1
            return {"action": "resume", "reason": f"harness death; resume {self.resumes}/{self.max_resumes}"}
        if baton:
            return {"action": "next_leg", "reason": "baton written — the leg is cleared"}
        if used_s < CONTINUE_BELOW * budget_s and self.continuations < self.max_continuations:
            self.continuations += 1
            left = int((budget_s - used_s) / 60)
            walls = ", ".join(f"{w} (x{n})" for w, n in self.springs.most_common(3)) or "none fingerprinted"
            return {
                "action": "continue",
                "reason": f"early exit with ~{left}m left; continuation {self.continuations}/{self.max_continuations}",
                "prompt": (
                    f"The run is not over: ~{left} minutes of budget remain and no baton is written. "
                    f"Walls fingerprinted so far: {walls}. Continue from where you stopped; do not re-verify "
                    f"what is already committed."
                ),
            }
        # Budget spent (or continuations exhausted) without a baton: charge the attempt.
        wall = self.springs.most_common(1)[0][0] if self.springs else "no-fingerprint"
        self.fingerprints[wall] += 1
        if self.fingerprints[wall] >= self.escalate_after:
            return {
                "action": "escalate",
                "reason": f"wall {wall} has taken {self.fingerprints[wall]} attempts",
                "wall": wall,
            }
        return {
            "action": "retry_leg",
            "reason": f"attempt {self.fingerprints[wall]}/{self.escalate_after} on wall {wall}",
            "wall": wall,
        }

    # ---- persistence ----------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "max_continuations": self.max_continuations,
            "max_resumes": self.max_resumes,
            "escalate_after": self.escalate_after,
            "continuations": self.continuations,
            "resumes": self.resumes,
            "fingerprints": dict(self.fingerprints),
            "springs": dict(self.springs),
            "nudged": sorted(self.nudged),
            "last_positions": self.last_positions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Supervisor:
        sup = cls(
            max_continuations=d.get("max_continuations", DEFAULT_MAX_CONTINUATIONS),
            max_resumes=d.get("max_resumes", DEFAULT_MAX_RESUMES),
            escalate_after=d.get("escalate_after", DEFAULT_ESCALATE_AFTER),
        )
        sup.continuations = d.get("continuations", 0)
        sup.resumes = d.get("resumes", 0)
        sup.fingerprints = Counter(d.get("fingerprints", {}))
        sup.springs = Counter(d.get("springs", {}))
        sup.nudged = set(d.get("nudged", []))
        sup.last_positions = d.get("last_positions")
        return sup

    @classmethod
    def load(cls, path: Path) -> Supervisor:
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict()))


# ============================================================================================
# The loop body: drive a leg, consult the crew on a failed hop, execute one bounded action.
# ============================================================================================

LEARNINGS_DIR = WORKSPACE / "docs" / "learnings"

NAV_ATTEMPTS = 2  # attempts 1..2 are navigation-class; past that the wall is a puzzle
LADDER_ATTEMPTS = 4  # 2 navigation + 2 puzzle, then the ladder is written down and stops
BODY_WAIT_FRAMES = 240  # wanderers clear; a trainer in a corridor never will (PR #113)
# The two growth classes the field-Cut flow has been measured to open (road.py): 0x3D bushes
# (the Vermilion yard, Celadon's hedges, Route 16's (34,9)) and 0x50 trees (Erika's garden).
CUT_TILES = {0x3D, 0x50}
# The refusals a field move (STRENGTH on a boulder, SURF over water) can answer before a consult.
FIELD_MOVE_FAILURES = ("no-path", "body-blocked", "stuck-on-edge", "interior-interior-stuck")
DEFAULT_MAX_HOPS = 80
DEFAULT_ENGAGE_ROUNDS = 14

# Every action below is something the road engine can actually perform. A menu item the engine
# cannot execute is a way to log a decision and change nothing, which reads as progress and is
# not — so the menus are built from `road`'s verbs, not from what sounds reasonable.
ACTIONS = (
    "RETRY_SAME",  # the hop again: stalls and wanderers are often one attempt deep
    "TRY_FAR_EDGE_CELL",  # aim at the far end of the open edge, not the nearest cell
    "USE_GATE_WARP",  # the route is severed by its own gate building — go through it
    "BACK_OUT_AND_REENTER",  # leave by the nearest warp and come back; reloads bodies/scripts
    "WAIT_FOR_BODIES",  # bodies are not walls: wanderers move if you wait
    "TALK_TO_BLOCKER",  # what the blocker SAYS is the finding (guards, story gates)
    "ORACLE_SEARCH",  # let the game itself be the oracle: facing-keyed press-and-settle BFS
    "SWEEP_ITEMS",  # a locked door that names its key: the floor's item balls are extracted
    "GIVE_UP",  # end the leg now; the operator gets the written record
)


MENUS: dict[str, tuple[str, ...]] = {
    "no-route": ("BACK_OUT_AND_REENTER", "TALK_TO_BLOCKER", "USE_GATE_WARP", "GIVE_UP"),
    "no-path": ("USE_GATE_WARP", "TRY_FAR_EDGE_CELL", "WAIT_FOR_BODIES", "BACK_OUT_AND_REENTER", "GIVE_UP"),
    "body-blocked": ("WAIT_FOR_BODIES", "TALK_TO_BLOCKER", "TRY_FAR_EDGE_CELL", "RETRY_SAME", "GIVE_UP"),
    "refused": ("TALK_TO_BLOCKER", "BACK_OUT_AND_REENTER", "TRY_FAR_EDGE_CELL", "RETRY_SAME", "GIVE_UP"),
    "stuck-on-edge": ("TRY_FAR_EDGE_CELL", "USE_GATE_WARP", "RETRY_SAME", "BACK_OUT_AND_REENTER", "GIVE_UP"),
}
DEFAULT_MENU = ("RETRY_SAME", "TRY_FAR_EDGE_CELL", "USE_GATE_WARP", "BACK_OUT_AND_REENTER", "GIVE_UP")


def menu_for(failure: str, *, edge_hop: bool = True, facility: bool = False, items: bool = False) -> list[str]:
    """The bounded menu for a measured failure, minus what this hop cannot do."""
    menu = list(MENUS.get(failure, DEFAULT_MENU))
    if not edge_hop:  # a warp hop has no edge cells to aim at
        menu = [m for m in menu if m != "TRY_FAR_EDGE_CELL"]
    if facility:  # on a tile-driven floor the oracle is a real option, and often the only one
        menu.insert(0, "ORACLE_SEARCH")
    if items:  # this floor still has item balls the cartridge listed and we have not opened
        menu.insert(0, "SWEEP_ITEMS")
    return menu


def hop_blocker(rig, hop: dict | None) -> tuple[int, int] | None:
    """The single body severing this hop, if one does — see ``road.blocking_body``.

    When the edge cells are unreachable even with every body lifted, the map is severed by its
    own terrain and the way across is its gate building. The question that matters then is not
    "which body blocks the edge" (none can) but "which body blocks the *gate door*" — measured
    on Route 12, where the north edge is only ever reached through the gate at (10,21), and one
    sprite at (10,62) was holding that door.
    """
    import road

    if hop is None:
        return None
    mp, x, y = rig.pos()
    try:
        if hop["via"] == "edge":
            targets, _direction = road.edge_cells(rig.truth, mp, hop["to"])
        else:
            targets = {(hop["x"], hop["y"])}
        if not (road.reachable(rig.truth, rig.pairs, mp, (x, y)) & set(targets)):
            doors = road.gate_doors(rig.truth, mp)
            if doors:
                targets = doors  # the terrain is severed: the gate door is the real objective
        return road.blocking_body(rig.truth, rig.pairs, mp, (x, y), targets, rig.bodies())
    except KeyError:  # the map itself is missing from the truth; a missing side is not an error
        return None


def prior_observations(map_id: int, path: str | Path = "pokedex/memory/observations.md", limit: int = 8) -> list[str]:
    """What the self-healing pipeline already recorded about THIS map, newest first.

    pokemon-kafka advances an upstream that already ships a Pokedex and an observations
    journal: ``observer.py`` and the Flink alerts-consumer both write structured signals here
    (``IN_PLACE_WEDGE map=3 pos=(16,18) stuck_turns=5947``, ``DOOR_STALL``, ``POSITION_DEADLOCK``)
    and ``discovery.py`` already reads the tail for its own prompts. The expedition path never
    did — so every leg started blind to thousands of recorded alerts, and prior-run knowledge
    reached a mission only if a human pasted it into the brief by hand.

    Scoped to the map on purpose: a raw tail is mostly other maps' noise, and a seat handed noise
    learns to skip the section. ``map=<id>`` is the pipeline's own convention in the alert text.
    """
    try:
        text = Path(path).read_text()
    except OSError:
        return []
    needle = f"map={map_id} "
    hits = [
        ln.strip()
        for ln in text.splitlines()
        if ln.startswith("- [") and (needle in ln or ln.rstrip().endswith(f"map={map_id}"))
    ]
    return hits[-limit:][::-1]


def describe(
    rig,
    goal: int,
    hop: dict | None,
    failure: str,
    notes: list[str] | None = None,
    heard: dict | None = None,
) -> str:
    """The measured facts handed to a seat. Everything here was read from RAM or the cartridge."""
    import road
    import rom_truth as rt

    mp, x, y = rig.pos()
    m = rig.truth["maps"].get(str(mp), {})
    lines = [
        f"GOAL: reach map {goal}. You are on map {mp} at ({x}, {y}).",
        f"MAP {mp}: {m.get('width', '?')}x{m.get('height', '?')}, tileset {m.get('tileset', '?')} "
        "(tile-id meanings are per-tileset and may not be reused across tilesets).",
        f"ROUTED CHAIN (extracted from this cartridge): {rt.describe_route(rt.route(rig.truth, mp, goal) or [])}"
        or "ROUTED CHAIN: none",
    ]
    if hop:
        lines.append(f"FAILED HOP: {mp} --{hop['via']}--> {hop['to']}; the engine returned {failure!r}.")
        if hop["via"] == "edge":
            try:
                cells, direction = road.edge_cells(rig.truth, mp, hop["to"])
            except KeyError:
                cells, direction = set(), ""
            if not direction:
                lines.append(f"OPEN EDGE CELLS toward {hop['to']}: the connection table has no side for this pair.")
            else:
                shown = sorted(cells)[:14]
                lines.append(
                    f"OPEN EDGE CELLS toward {hop['to']} (step {direction}): {shown}"
                    + (" ..." if len(cells) > 14 else "")
                )
        else:
            lines.append(f"WARP TILE: ({hop.get('x')}, {hop.get('y')}) on this map.")
    else:
        lines.append(f"NO ROUTE: the extracted connection graph has no chain from map {mp} to map {goal}.")
    bodies = sorted(rig.bodies())
    lines.append(f"LIVE BODIES (sprites on screen right now): {bodies[:12]}" + (" ..." if len(bodies) > 12 else ""))
    lines.append("Bodies are not walls — wanderers move if you wait, but trainers never move.")
    culprit = hop_blocker(rig, hop)
    if culprit:
        lines.append(
            f"THE BLOCKING BODY IS {culprit}: removing that one sprite reconnects this hop, and no "
            "other body does. Any body you are standing next to is a bystander unless it is this one."
        )
    lines.append(f"PARTY: {rig.party()}   BADGES byte: 0b{rig.badges():08b}")
    text = rig.dialogue()
    if text:
        lines.append(f"TEXT ON SCREEN: {text!r}")
    for spot, said in (heard or {}).items():
        lines.append(f"HEARD from the body at {spot}: {said!r}")
    for note in prior_observations(mp):
        # What the pipeline already knows about this map, from every previous run.
        lines.append(f"ALREADY OBSERVED HERE: {note}")
    for note in notes or []:
        lines.append(f"OBSERVED: {note}")
    return "\n".join(lines)


class TapesConsult:
    """Ask the seated crew member through the tapes proxy; an unparsed reply is a non-answer.

    Every model call goes to :42345 (``expedition_crew.TAPES_CHAT_URL``). A call straight to
    ollama on :11434 is an uncaptured session, which the doctrine forbids — so the URL is not a
    parameter a caller can casually redirect, it is the crew module's constant.
    """

    def __init__(self, *, timeout: float | None = None, log=_flushing_print) -> None:
        self.timeout = timeout  # None = each seat is waited for as long as its budget needs
        self.log = log

    def __call__(self, tier: str, facts: str, menu: list[str]) -> tuple[str | None, str, str]:
        import urllib.error
        import urllib.request

        import expedition_crew as crew

        seat = crew.seat_for(tier)
        prompt = crew.build_prompt(facts, menu)
        # Streamed, because a seat that thinks for four minutes cannot be asked for one whole
        # response: the gateway 502s at a hard 300s ceiling, and a truncated non-stream reply is
        # 30 KB of chain-of-thought with no answer in it. Streaming reads the answer the moment
        # it is written; `timeout` becomes a per-chunk wait rather than a whole-answer deadline.
        body = json.dumps(crew.chat_body(seat["model"], prompt, crew.answer_tokens(tier), stream=True)).encode()
        wait = self.timeout if self.timeout is not None else crew.answer_timeout(tier)

        def ask(payload: bytes) -> tuple[str | None, str, str]:
            request = urllib.request.Request(
                crew.TAPES_CHAT_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(request, timeout=wait) as resp:
                return crew.decide_from_stream(resp, menu)

        try:
            action, why, text = ask(body)
            # A seat that ran out of clock mid-thought is not out of ideas — it is out of clock.
            # One closing call hands its own reasoning back and asks only for the line.
            if action is None and len(text) > 500:
                self.log(f"  {seat['title']} ran out of clock mid-thought ({len(text)}B) — asking it to close")
                closing = json.dumps(
                    crew.chat_body(
                        seat["model"], crew.closing_prompt(text, menu), crew.answer_tokens(tier), stream=True
                    )
                ).encode()
                action, why, _ = ask(closing)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            self.log(f"  consult FAILED ({seat['title']}, {seat['model']}): {exc}")
            return None, f"consult failed: {exc}", seat["model"]
        self.log(f"  {seat['title']} ({seat['model']}) -> {action or 'NO-ANSWER'}: {why[:120]}")
        return action, why, seat["model"]


class LegRunner:
    """One leg: boot to goal, supervised. Deterministic Python moves; the crew only chooses."""

    def __init__(
        self,
        rig,
        *,
        goal: int,
        budget_s: float = 7200,
        consult=None,
        clock=time.monotonic,
        log=_flushing_print,
        max_hops: int = DEFAULT_MAX_HOPS,
        engage: bool = False,
        heal: bool = False,
        sweep: bool = False,
        clear_floor: bool = False,
        engage_rounds: int = DEFAULT_ENGAGE_ROUNDS,
        learnings_dir: Path | None = None,
        memory_dir: Path | None = None,
        want: str | None = None,
        hunt: str | None = None,
    ) -> None:
        self.rig = rig
        self.goal = goal
        self.budget_s = budget_s
        self.consult = consult if consult is not None else TapesConsult(log=log)
        self.clock = clock
        self.log = log
        self.max_hops = max_hops
        self.engage = engage
        self.heal = heal
        self.sweep = sweep
        self.clear_floor = clear_floor
        self.engage_rounds = engage_rounds
        self.want = want  # item name this leg came for; its ball is opened before any other
        # An item a BODY hands over, not a ball: the leg is judged on the bag holding it. This
        # is the Warden shape -- GOLD TEETH in the bag, HM04 behind whichever of Fuchsia's eight
        # never-entered doors he is behind -- and it is the third observed way a story item
        # arrives (Mr Fuji's POKe FLUTE). Ten hand-tapped probe scripts were written for that
        # hunt in one night because the supervisor could only judge a leg on a badge or a ball.
        self.hunt = hunt
        self.learnings_dir = learnings_dir or LEARNINGS_DIR
        self.memory_dir = memory_dir or Path("pokedex/memory")  # the upstream journal, see prior_observations
        self.attempts: Counter = Counter()  # wall id -> attempts spent on it
        self.tried: list[str] = []  # every action executed, for the exhaustion record
        self.notes: list[str] = []  # measured observations that feed the next consult
        self.heard: dict[tuple[int, int], str] = {}  # what bodies on this map actually said
        self.reconned: set[int] = set()  # maps whose bodies have been asked
        self.consults: list[dict] = []
        self.banned: set[tuple[int, int]] = set()  # hops the world has refused; routing skips them
        self.gated: set[tuple[int, int]] = set()  # hops whose gate building we have already tried
        self.engaged: set[tuple[int, int]] = set()  # blocking bodies we have already gone to meet
        self.gates: dict[tuple[int, int, int], str] = {}  # (map, x, y) -> what the game said when it refused
        self.looted: set[tuple[int, tuple[int, int]]] = set()  # (map, ball) we have already tried

    # ---- one hop ---------------------------------------------------------------------------

    def _hop(self, hop: dict) -> str | None:
        """Attempt one routed hop. Returns None on progress, else the measured failure code."""
        import road

        cur = self.rig.pos()[0]
        result = self.rig.cross(cur, hop["to"]) if hop["via"] == "edge" else self.rig.warp(cur, hop["x"], hop["y"])
        if result in FIELD_MOVE_FAILURES and self.rig.pos()[0] == cur:
            # A field move the party holds may reconnect the target from right here. Tried
            # BEFORE the gate-building detour: on Route 23 (measured 2026-09-05) that detour
            # left the map, and the surf could only ever be planned from the shore it left.
            if self._push_through(hop) or self._surf_through(hop):
                result = (
                    self.rig.cross(cur, hop["to"]) if hop["via"] == "edge" else self.rig.warp(cur, hop["x"], hop["y"])
                )
        if result == "no-path" and (cur, hop["to"]) not in self.gated:
            # A severed route is usually its own gate building (Route 11's Diglett house taught
            # that the nearest door is not the gate). Determinism gets this before the crew does.
            # A WARP hop is severed the same way: Route 16's Fly house door (7,5) sits on a strip
            # that only the gate's upper corridor opens onto, and a leg on the lower road spent
            # its whole ladder on "no-path" with the gate three tiles away (measured 2026-09-04).
            self.gated.add((cur, hop["to"]))
            self.log(f"  no-path: trying {cur}'s gate building")
            if hop["via"] == "edge":
                cells, _direction = road.edge_cells(self.rig.truth, cur, hop["to"])
            else:
                cells = {(hop["x"], hop["y"])}
            if self.rig.gate(cur, cells):
                result = (
                    self.rig.cross(cur, hop["to"]) if hop["via"] == "edge" else self.rig.warp(cur, hop["x"], hop["y"])
                )
            elif self.rig.pos()[0] != cur:
                # A failed gate pass can end INSIDE a building whose far side would not open.
                # Route 16's gate did exactly that twenty times in a row: every verdict after it
                # (blocker, growth, reroute) was then computed on the gate's map, and the ban
                # landed on a pair that does not exist. Come back out the way we went in, so the
                # rest of this hop's reasoning is about ``cur``.
                inside = self.rig.pos()[0]
                self.log(f"  the gate pass ended inside {inside}; retreating to {cur}")
                self.rig.traverse(inside, exclude_entry=False)
        now = self.rig.pos()[0]
        if now == hop["to"]:
            return None
        if now == cur and hop["via"] != "edge" and str(result) in ("no-path", "refused", "cap", "warp-dead"):
            # The routed door is one of possibly several to the same map. Route 16's gate (186)
            # has four doors back out to map 27, and the router named the west one -- behind the
            # guard who stops every walk at (4,7) ("Excuse me! Wait up please") -- so three
            # attempts hit the walk cap on it while the east door stood open four tiles away.
            for ax, ay in self._other_doors_to(cur, hop):
                self.log(f"  the door at ({hop['x']},{hop['y']}) is {result}; trying ({ax},{ay}) to {hop['to']}")
                alt = self.rig.warp(cur, ax, ay)
                if self.rig.pos()[0] == hop["to"]:
                    return None
                if self.rig.pos()[0] != cur:
                    break  # somewhere else entirely: let the interior logic below read it
                self.tried.append(f"door ({ax},{ay}) to {hop['to']}: {alt}")
            now = self.rig.pos()[0]
        if now == cur and hop["via"] != "edge" and str(result) in ("no-path", "refused", "cap"):
            # The door is on this map but no walk reaches it. That is a ride, not a wall — the
            # rule already applied to bodies and item balls, and the hop is the third place that
            # needs it: badge 6 was won at (9,9) inside Sabrina's gym, and the leg then spent its
            # whole budget re-trying the exit mat at (8,17) from behind thirty teleport pads.
            if self.rig.approach({(hop["x"], hop["y"])}):
                result = self.rig.warp(cur, hop["x"], hop["y"])
                now = self.rig.pos()[0]
                if now == hop["to"]:
                    return None
        if now != cur:  # an interior swallowed the hop — a gate room, not a failure
            self.log(f"  interior {now} swallowed the hop")
            inner = self.rig.traverse(now)
            if self.rig.pos()[0] != now:
                return None
            return f"interior-{inner}"
        return str(result)

    def _other_doors_to(self, cur: int, hop: dict) -> list[tuple[int, int]]:
        """Every other warp on this map that leads where the routed one does, nearest first."""
        _mp, x, y = self.rig.pos()
        # 255 is the cartridge's LAST_MAP: an interior's doors say "back where you came from",
        # and the router already resolved the routed one to ``hop["to"]`` -- so do its siblings.
        doors = [
            (wx, wy)
            for wx, wy, dest, _wid in self.rig.truth["maps"].get(str(cur), {}).get("warps", [])
            if dest in (hop["to"], 255) and (wx, wy) != (hop["x"], hop["y"])
        ]
        return sorted(doors, key=lambda d: abs(d[0] - x) + abs(d[1] - y))

    def _clear_blocker(self, hop: dict) -> bool:
        """Go meet the one body that severs this hop. Deterministic: there is nothing to choose.

        When exactly one sprite explains the severance, the next move is not a judgement call —
        walk to it and engage. Route 12's was a trainer whose line of sight had never been
        crossed; the fight started on approach and paid 700. Others will be story gates, and then
        what they *say* is the finding. Either way it is measured, not decided.
        """
        import road

        culprit = hop_blocker(self.rig, hop)
        if culprit is None or culprit in self.engaged:
            return False
        self.engaged.add(culprit)
        mp, x, y = self.rig.pos()
        bx, by = culprit
        adjacent = {(bx + 1, by), (bx - 1, by), (bx, by + 1), (bx, by - 1)}
        near = road.reachable(self.rig.truth, self.rig.pairs, mp, (x, y), self.rig.bodies()) & adjacent
        if not near:  # pragma: no cover - defensive; unreachable while both reads use one body set
            # If lifting a body reconnects the target then a neighbour of it is reachable by
            # definition, so this cannot fire. It did once, when `blocking_body` was reading the
            # extraction's sprites and `reachable` the live table — two different sets. Kept as a
            # guard precisely because that divergence is easy to reintroduce.
            self.notes.append(f"the body at {culprit} severs this hop but no approach cell is reachable")
            return False
        self.log(f"  one body at {culprit} severs this hop — going to meet it")
        self.rig.walk(mp, near, cap=400)
        _, x, y = self.rig.pos()
        if (x, y) not in adjacent:
            return False
        import road as _road

        mp_now, hx, hy = self.rig.pos()
        before_region = len(_road.reachable(self.rig.truth, self.rig.pairs, mp_now, (hx, hy), self.rig.bodies()))
        face = "right" if bx > x else "left" if bx < x else "down" if by > y else "up"
        said = self.rig.talk(face)
        mp_now, hx, hy = self.rig.pos()
        after_region = len(_road.reachable(self.rig.truth, self.rig.pairs, mp_now, (hx, hy), self.rig.bodies()))
        if after_region <= before_region and self._wake_sleeper(culprit, said, face):
            mp_now, hx, hy = self.rig.pos()
            after_region = len(_road.reachable(self.rig.truth, self.rig.pairs, mp_now, (hx, hy), self.rig.bodies()))
        if after_region <= before_region:
            # Engaging is not clearing. A beaten Gen 1 trainer still stands on its tile, so a
            # blocker that is a trainer stays a wall however the fight goes — measured on Silph
            # 3F, where (20,7) was correctly named as the single body severing the npc at (24,8),
            # was fought, and left the reachable region at 98 cells exactly as before. Saying so
            # stops the next attempt spending its ladder on a body that has already been met.
            self.log(f"  {culprit} was engaged and still blocks ({after_region} cells) — it is a wall")
            self.notes.append(f"the body at {culprit} was engaged and did not clear; it is terrain")
        # The world just changed, so every verdict reached about this hop is stale. Route 12 was
        # banned as impassable on evidence gathered while the blocker still stood, and its gate
        # was marked "already tried" from an attempt made when the gate door was unreachable.
        self.gated.discard((mp, hop["to"]))
        self.banned.discard((mp, hop["to"]))
        self.tried.append(f"engaged the blocking body at {culprit}")
        self.notes.append(f"the body at {culprit} (which alone severs this hop) says: {said[:300]}")
        self.log(f"  it says: {said[:160]}")
        self.rig.emit("supervisor.blocker_engaged", body=[bx, by], said=said[:300])
        return True

    def _wake_sleeper(self, culprit: tuple[int, int], said: str, face: str) -> bool:
        """A blocker the game calls a sleeping POKeMON is woken with the flute from the bag.

        Route 16 (map 27), measured 2026-09-04: the body at (26,10) says "A sleeping POKeMON
        blocks the way!" and holds the 13 cells around the Fly house's door, while the POKe
        FLUTE had sat in the bag since Mr Fuji handed it over. The sentence names the gate and
        the bag names the key -- nothing here is recalled. The woken body attacks; the rig's own
        settle-then-battle closer resolves that, and the region is measured again afterwards.
        """
        if "SLEEPING" not in (said or "").upper():
            return False
        flute = next(
            (self.rig.item_name(i) for i, _q in self.rig.bag() if "FLUTE" in self.rig.item_name(i).upper()), None
        )
        if flute is None:
            self.notes.append(f"the body at {culprit} is asleep and the bag holds no flute to wake it")
            return False
        if not hasattr(self.rig, "use_item") or not self.rig.use_item(flute, face=face):
            self.notes.append(f"could not play the {flute} at the sleeping body {culprit}")
            return False
        if hasattr(self.rig, "settle"):
            self.rig.settle()  # the wake-up pages, then the fight the woken body starts
        self.log(f"  played the {flute} at {culprit}")
        self.tried.append(f"played the {flute} at the sleeping body {culprit}")
        self.rig.emit("supervisor.sleeper_woken", body=list(culprit), item=flute)
        return True

    def _hop_targets(self, hop: dict, mp: int) -> set[tuple[int, int]]:
        """The cells this hop needs to stand on: the open edge, or the warp tile."""
        import road

        if hop["via"] == "edge":
            cells, _direction = road.edge_cells(self.rig.truth, mp, hop["to"])
            return set(cells)
        return {(hop["x"], hop["y"])}

    def _push_through(self, hop: dict) -> bool:
        """Push the one boulder whose line reconnects this hop, when the party has STRENGTH.

        Victory Road 1F, measured 2026-09-05: the boulder at (2,10) was engaged as a body seven
        times ("This requires STRENGTH to move!") and the leg burned its budget without a push.
        A boulder is found like a growth is: on the sprite table, with the plan simulated on it
        (``boulder_oracle.push_plan``), and every push is proved by the table changing.
        """
        mp = self.rig.pos()[0]
        if not self._push_to(self._hop_targets(hop, mp), what="this hop"):
            return False
        self.notes[-1] += "; the hop's region changed"
        self.gated.discard((mp, hop["to"]))
        self.banned.discard((mp, hop["to"]))
        return True

    def _teach_from_bag(self, machine: str, move: str) -> bool:
        """Teach ``machine`` from the bag when nobody knows ``move`` yet. Map 155, measured
        2026-09-05: the WARDEN had handed over HM04 STRENGTH, the bag held it, nobody had learned
        it, and the boulder beside his RARE CANDY was talked to seven times. The move id landing
        in RAM is the proof (``Rig.teach``)."""
        bag = getattr(self.rig, "bag_named", None)
        teach = getattr(self.rig, "teach", None)
        if bag is None or teach is None:
            return False
        if not any(name.upper().startswith(machine) for name, _q in bag(full=True)):
            return False
        # A fainted member who knows the move is no use (map 155: Gyarados at 0 HP held STRENGTH
        # and "already knows" ended the teach). Teach a STANDING member who does not; the
        # game's ABLE captions decide who can, so a refusal moves to the next.
        for name, _lvl, hp in self.rig.party():
            if hp <= 0 or self.rig.knows_move(move, name) is not None:
                continue
            self.log(f"  nobody standing knows {move}; teaching {machine} to {name}")
            who = teach(machine, species=name)
            if who is not None and self.rig.knows_move(move) is not None:
                self.rig.emit("supervisor.move_taught", machine=machine, move=move, member=who, species=name)
                return True
        self.notes.append(f"{machine} is in the bag but no standing member could learn it")
        return False

    def _push_to(self, targets, what: str = "the target", exclude=()) -> bool:
        """Push ONE boulder so a walk reaches one of ``targets``; every push is proved by the
        sprite table. Serves a hop's cells, a body's neighbours and a ball's neighbours alike:
        map 155, measured 2026-09-05, had the boulder at (8,4) talked to seven times with a RARE
        CANDY ball behind it, because pushes served hops only. ``exclude`` are sprites that are
        the goal, not a wall (the body or ball itself)."""
        import boulder_oracle

        knows = getattr(self.rig, "knows_move", None)
        if knows is None or not hasattr(self.rig, "boulders"):
            return False
        if knows("STRENGTH") is None and not self._teach_from_bag("HM04", "STRENGTH"):
            return False
        mp, x, y = self.rig.pos()
        if str(mp) not in self.rig.truth.get("maps", {}):
            return False
        # The sprite table names most boulders by picture (63); the cartridge lists Victory Road
        # 1F's plateau boulder (7,5) as a trainer, and what it said when engaged is the proof.
        heard = {cell for cell, said in self.heard.items() if "requires STRENGTH" in (said or "")}
        boulders = (set(self.rig.boulders()) | heard) - set(exclude)
        if not boulders:
            return False
        bodies = set(self.rig.bodies())  # the goal sprite stays a wall: a boulder cannot land on a ball
        plan = boulder_oracle.push_plan(self.rig.truth, self.rig.pairs, mp, (x, y), set(targets), bodies, boulders)
        if not plan:
            return False
        for stand, face, boulder in plan:
            self.log(f"  a boulder at {boulder} seals {what} -- pushing it {face} from {stand}")
            if not self.rig.push_boulder(stand, face):
                self.notes.append(f"the boulder at {boulder} did not move when pushed {face} from {stand}")
                self.rig.emit("supervisor.push_refused", map=mp, boulder=list(boulder), stand=list(stand), face=face)
                return False
            self.rig.emit("supervisor.boulder_pushed", map=mp, boulder=list(boulder), stand=list(stand), face=face)
        self.tried.append(f"pushed the boulder at {plan[0][2]} {plan[0][1]} x{len(plan)}")
        self.notes.append(f"pushed the boulder at {plan[0][2]} {plan[0][1]} x{len(plan)} toward {what}")
        return True

    def _surf_through(self, hop: dict) -> bool:
        """SURF across this map's water to the land the hop needs, when the party has SURF.

        Route 23, measured 2026-09-05: the 34->108 warp sits across a channel; the planner sees
        land only and the League leg died on the shore with "no-path". The route is planned on
        the tile model (``road.water_route``) and proved by the landing position.
        """
        import road

        knows = getattr(self.rig, "knows_move", None)
        if knows is None or knows("SURF") is None or not hasattr(self.rig, "surf_to"):
            return False
        mp, x, y = self.rig.pos()
        if str(mp) not in self.rig.truth.get("maps", {}):
            return False
        targets = self._hop_targets(hop, mp)
        if road.reachable(self.rig.truth, self.rig.pairs, mp, (x, y), self.rig.bodies()) & targets:
            return False
        self.log(f"  water between here and {sorted(targets)[:3]} -- surfing across map {mp}")
        result = self.rig.surf_to(targets)
        self.log(f"  surf -> {result} at {self.rig.pos()}")
        if result is not True:
            if result != "no-route":
                self.notes.append(f"the SURF route toward {sorted(targets)[:4]} ended {result}")
                self.rig.emit("supervisor.surf_refused", map=mp, result=str(result))
            return False
        landed = self.rig.pos()
        self.tried.append(f"surfed to {landed[1:]} on map {mp}")
        self.notes.append(f"surfed across map {mp}'s water to {landed[1:]}; the hop's region changed")
        self.rig.emit("supervisor.surfed", map=mp, to=list(landed[1:]))
        self.gated.discard((mp, hop["to"]))
        self.banned.discard((mp, hop["to"]))
        return True

    def _cut_through(self, hop: dict) -> bool:
        """Cut the one growth that seals this hop, when the party can and the tile model shows it.

        Route 16, measured 2026-09-04: the Fly house's strip is the whole upper level of the
        map, entered from the lower road only by cutting the 0x3D bush at (34,9) in the tree
        row -- the gate's upper corridor is sealed from its lower one (survey: the guard at
        (4,7) and solid counter rows), and two crew ladders ended on "no-path" while Charizard
        held CUT. A growth is found like a blocking body is: a cuttable tile touching our
        region whose far side reaches the target. Nearest first, and the step is the proof.
        """
        import road

        knows = getattr(self.rig, "knows_move", None)
        if knows is None or knows("CUT") is None:
            return False
        mp, x, y = self.rig.pos()
        m = self.rig.truth["maps"].get(str(mp), {})
        tiles = m.get("tiles")
        if not tiles:
            return False
        if hop["via"] == "edge":
            targets, _direction = road.edge_cells(self.rig.truth, mp, hop["to"])
        else:
            targets = {(hop["x"], hop["y"])}
        targets = set(targets)
        bodies = self.rig.bodies()
        region = road.reachable(self.rig.truth, self.rig.pairs, mp, (x, y), bodies)
        if region & targets:
            return False
        w, h = m["width"], m["height"]

        def tile(cx: int, cy: int) -> int:
            return int(tiles[cy][2 * cx : 2 * cx + 2], 16)

        faces = {(0, 1): "down", (0, -1): "up", (1, 0): "right", (-1, 0): "left"}
        found: list[tuple[int, tuple[int, int], str, tuple[int, int]]] = []
        for rx, ry in region:
            for (dx, dy), face in faces.items():
                cx, cy = rx + dx, ry + dy
                if not (0 <= cx < w and 0 <= cy < h) or (cx, cy) in region or tile(cx, cy) not in CUT_TILES:
                    continue
                fx, fy = cx + dx, cy + dy  # the cell beyond the growth
                if not (0 <= fx < w and 0 <= fy < h) or m["grid"][fy][fx] != "1" or (fx, fy) in bodies:
                    continue
                if road.reachable(self.rig.truth, self.rig.pairs, mp, (fx, fy), bodies) & targets:
                    found.append((abs(rx - x) + abs(ry - y), (rx, ry), face, (cx, cy)))
        if not found:
            return False
        _dist, stand, face, growth = min(found)
        self.log(f"  a cuttable growth at {growth} seals this hop -- going to cut it from {stand}")
        if not self.rig.approach({stand}):
            self.notes.append(f"could not reach {stand} to cut the growth at {growth}")
            return False
        if not self.rig.cut(face):
            self.notes.append(f"CUT at {growth} did not open it")
            self.rig.emit("supervisor.cut_refused", map=mp, growth=list(growth))
            return False
        self.tried.append(f"cut the growth at {growth}")
        self.notes.append(f"cut the growth at {growth}; the hop's region changed")
        self.rig.emit("supervisor.growth_cut", map=mp, growth=list(growth), stand=list(stand))
        self.gated.discard((mp, hop["to"]))
        self.banned.discard((mp, hop["to"]))
        return True

    def read_refusal(self, hop: dict | None) -> str:
        """Step into the refusal and read what the game says about it.

        This is the cheapest measurement in the project and the one this session kept skipping.
        A failure code is a single token — `refused`, `no-path` — and it discards the sentence
        the game prints at that exact instant. On Silph the sentence was "Darn! It needs a CARD
        KEY!" and it took five legs and a hand-written probe to see it, because nothing in the
        loop ever pressed the direction and looked.

        The text buffer goes stale after boxes close, so reading it without causing the refusal
        again proves nothing. We press once toward the target: a refused step does not move us,
        and a step that *does* move is undone.
        """
        import road

        mp, x, y = self.rig.pos()
        if hop is None:
            return ""
        try:
            if hop["via"] == "edge":
                _cells, direction = road.edge_cells(self.rig.truth, mp, hop["to"])
            else:
                dx, dy = hop.get("x", x) - x, hop.get("y", y) - y
                if abs(dx) >= abs(dy):
                    direction = "right" if dx > 0 else "left" if dx < 0 else "down"
                else:
                    direction = "down" if dy > 0 else "up"
        except KeyError:  # the map itself is missing from the truth; a missing side is not an error
            return ""
        before = self.rig.pos()

        def step():
            self.rig.io.press(direction, hold=8, release=8)
            self.rig.io.wait(40)

        said = self.rig.text_from(step)  # only text this step produced; the buffer is sticky
        after = self.rig.pos()
        if after != before and after[0] == before[0]:  # it was not refused after all: undo
            opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}[direction]
            self.rig.io.press(opposite, hold=8, release=8)
            self.rig.io.wait(40)
        if said:
            self.gates[(mp, x, y)] = said
            self.log(f'  the game says: "{said[:160]}"')
            self.notes.append(f'stepping {direction} from ({x}, {y}) on map {mp} prints: "{said[:200]}"')
            self.rig.emit("supervisor.gate_text", map=mp, at=[x, y], direction=direction, said=said[:300])
        return said

    def _reroute_around(self, hop: dict) -> bool:
        """Ban a hop the world has structurally refused and ask the graph for another chain.

        The connection table is undirected; the world is not. Cycling Road (map 28) carries only
        down-ledges, so ``7 -> 29 -> 28 -> 27 -> 6`` is a valid graph path no player can walk
        north — and the badge-6 leg spent its whole crew ladder on it before the record was
        written. A structural refusal is a fact about the graph, not a question for a model.
        """
        import rom_truth as rt

        cur = self.rig.pos()[0]
        self.banned.add((cur, hop["to"]))
        alt = rt.route(self.rig.truth, cur, self.goal, banned=self.banned)
        if not alt:
            self.notes.append(f"banning {cur}->{hop['to']} leaves no chain to {self.goal} at all")
            return False
        self.log(f"  banned {cur}->{hop['to']}; rerouted: {rt.describe_route(alt)}")
        self.notes.append(f"the hop {cur}->{hop['to']} is structurally refused; rerouted around it")
        self.rig.emit("supervisor.rerouted", banned=[cur, hop["to"]], chain=rt.describe_route(alt))
        return True

    # ---- the bounded actions ----------------------------------------------------------------

    def _act(self, action: str, hop: dict | None) -> None:
        import road
        import rom_truth as rt

        cur, x, y = self.rig.pos()
        self.tried.append(f"{action} on map {cur} at ({x}, {y})")
        if action == "RETRY_SAME":
            return  # the loop re-attempts the hop; a stall is often one attempt deep
        if action == "WAIT_FOR_BODIES":
            self.rig.io.wait(BODY_WAIT_FRAMES)
            return
        if action == "TRY_FAR_EDGE_CELL" and hop and hop["via"] == "edge":
            cells, _direction = road.edge_cells(self.rig.truth, cur, hop["to"])
            if cells:
                far = max(cells, key=lambda c: abs(c[0] - x) + abs(c[1] - y))
                self.log(f"  aiming at far edge cell {far}")
                self.rig.walk(cur, {far}, cap=200)
                self.rig.cross(cur, hop["to"])
            return
        if action == "USE_GATE_WARP":
            targets = set()
            if hop and hop["via"] == "edge":
                try:
                    targets, _d = road.edge_cells(self.rig.truth, cur, hop["to"])
                except KeyError:  # the map itself is missing from the truth; a missing side is not an error
                    targets = set()
            if not targets:
                warps = self.rig.truth["maps"].get(str(cur), {}).get("warps", [])
                targets = {(w[0], w[1]) for w in warps}
            self.rig.gate(cur, targets)
            return
        if action == "BACK_OUT_AND_REENTER":
            warps = self.rig.truth["maps"].get(str(cur), {}).get("warps", [])
            if not warps:
                self.notes.append(f"map {cur} has no warps to back out through")
                return
            wx, wy, _dst, _wid = min(warps, key=lambda w: abs(w[0] - x) + abs(w[1] - y))
            self.rig.warp(cur, wx, wy)
            inside = self.rig.pos()[0]
            if inside != cur:
                self.rig.traverse(inside, exclude_entry=False)
            return
        if action == "TALK_TO_BLOCKER":
            bodies = self.rig.bodies()
            if not bodies:
                self.notes.append("no live body to talk to — the block is terrain or a script, not a sprite")
                return
            # The body that severs the hop, not the one we happen to be standing beside. On
            # Route 12 those were fifteen tiles apart and only one of them was the wall.
            culprit = hop_blocker(self.rig, hop)
            bx, by = culprit or min(bodies, key=lambda b: abs(b[0] - x) + abs(b[1] - y))
            adjacent = {(bx + 1, by), (bx - 1, by), (bx, by + 1), (bx, by - 1)}
            if (x, y) not in adjacent:
                # Only aim at approach cells our side of the block can actually reach — the
                # doctrine's body-aware region, applied to the approach as well as the crossing.
                near = road.reachable(self.rig.truth, self.rig.pairs, cur, (x, y), bodies) & adjacent
                self.rig.walk(cur, near or adjacent, cap=400)
                _, x, y = self.rig.pos()
            face = "right" if bx > x else "left" if bx < x else "down" if by > y else "up"
            said = self.rig.talk(face)
            self.log(f"  blocker at ({bx}, {by}) says: {said[:160]}")
            self.notes.append(f"the body at ({bx}, {by}) says: {said[:300]}")
            return
        if action == "SWEEP_ITEMS":
            self.sweep_items(self.want)
            return
        if action == "ORACLE_SEARCH":
            if hop is None:
                self.notes.append("nothing to search toward: there is no routed hop")
                return
            target = (
                {(hop["x"], hop["y"])}
                if hop["via"] != "edge"
                else set(road.edge_cells(self.rig.truth, cur, hop["to"])[0])
            )
            self.log(f"  oracle search on map {cur} toward {sorted(target)[:6]}")
            found = self.rig.oracle_goto(lambda p: p[0] != cur or (p[1], p[2]) in target)
            self.notes.append(
                f"the facing-keyed oracle {'reached' if found else 'could not reach'} the hop target on map {cur}"
            )
            return
        if action == "TRY_FAR_EDGE_CELL":  # warp hop: the menu should not have offered it
            self.notes.append("TRY_FAR_EDGE_CELL is meaningless on a warp hop")
            return
        # An action outside the menu is a parser bug, not a move: say so rather than acting.
        self.notes.append(f"unknown action {action!r} — nothing executed")
        _ = rt  # imported for symmetry with describe(); the actions above use `road`

    # ---- the gym engage ---------------------------------------------------------------------

    def sweep_items(self, want: str | None = None) -> list[tuple[int, int]]:
        """Collect every item ball the cartridge lists for this map. Returns what the bag gained.

        A locked door whose own NPC names the key it wants (Silph 209's (11,7): "requires a CARD
        KEY") is not a navigation problem — the key is an object somewhere, and both the balls
        *and what they hold* are extracted, not guessed. So a leg can name the item it came for:
        the wanted ball is opened first, before a full bag or a lost trainer fight can cost it.
        Bag growth is the only proof a pickup happened.
        """
        import road

        mp = self.rig.pos()[0]
        gained: list[tuple[int, int]] = []
        contents = self.rig.ball_contents(mp)
        balls = self.rig.item_balls(mp)
        if want:
            balls.sort(key=lambda b: contents.get(b, "") != want)
        for ball in balls:
            if (mp, ball) in self.looted:
                continue
            self.looted.add((mp, ball))
            before = self.rig.bag()
            holds = contents.get(ball, "?")
            bx, by = ball
            beside = {(bx + 1, by), (bx - 1, by), (bx, by + 1), (bx, by - 1)}
            px, py = self.rig.pos()[1:]
            if not (road.walkable(self.rig.truth, self.rig.pairs, mp, (px, py), self.rig.bodies() - {ball}) & beside):
                self._push_to(beside, what=f"the ball at {ball}", exclude={ball})  # the pickup is the verdict
            if not self.rig.collect_item(*ball):
                self.log(f"  could not open the ball at {ball} on map {mp} (cartridge says {holds})")
                self.name_the_ride(ball)
                self.rig.emit("supervisor.item_refused", map=mp, at=list(ball), holds=holds)
                continue
            new = [item for item in self.rig.bag() if item not in before]
            gained.extend(new)
            self.log(f"  picked up {new} at {ball} on map {mp}")
            self.rig.emit("supervisor.item_collected", map=mp, at=list(ball), items=new)
        if gained:
            self.notes.append(f"collected {gained} from map {mp}'s item balls")
        return gained

    def recon(self, mp: int, cap: int = 4) -> dict[tuple[int, int], str]:
        """Ask the map before asking a model. Talk to the bodies here and keep what they said.

        This exists because of a measured pattern, not a theory. Across four legs of the badge-7
        water arc the crew engaged **zero** bodies on maps 7, 30, 31, 8 and 166 — every one of the
        76 recorded conversations belongs to the badge-6 arc — while map 30's own stuck doc listed
        ten live bodies and used them only as obstacles to route around. The cartridge calls all
        ten `trainer`. The first time anyone spoke to one, it answered.

        A seat asked to choose an action from failure codes alone is reasoning about a world it
        has not observed. So recon runs once per map before the first consult on it, and whatever
        it hears goes into the facts under ``HEARD``.

        Bounded on purpose: ``cap`` bodies, nearest first, and only bodies a walk can reach. Recon
        is the cheap step that precedes thinking, not a second exploration budget.
        """
        if mp in self.reconned:
            return self.heard
        self.reconned.add(mp)
        if hasattr(self.rig, "screenshot"):
            # A picture of the map, taken by default, not only when someone remembers to ask.
            # Twice today a leg called this water sealed from the collision grid alone; on
            # screen the tile it refused on was a boulder in open water, not a barrier. The
            # Investigator's whole job is to look before reasoning, so it looks first.
            self.rig.screenshot(f"recon_map{mp}")
        # Pick up and catalog every item ball on the map, not only when a seat happens to choose
        # SWEEP_ITEMS off a menu. The GOLD TEETH sat on the Safari Zone's own floor (map 219,
        # (19,7)) through every earlier leg that walked past it, because sweeping was opt-in and
        # nobody opted in. sweep_items is idempotent (gated on self.looted), so calling it here
        # unconditionally costs nothing on a map already swept.
        self.sweep_items()
        sprites = [
            (s["x"], s["y"])
            for s in self.rig.truth["maps"].get(str(mp), {}).get("sprites", [])
            if s.get("kind") in ("trainer", "npc")
        ]
        if not sprites:
            return self.heard
        here = self.rig.pos()[1:]
        order = sorted(sprites, key=lambda c: abs(c[0] - here[0]) + abs(c[1] - here[1]))
        for spot in self._recon_order(mp, order, cap)[:cap]:
            if spot in self.heard or not self._go_and_talk(spot):
                continue
            said = self._what_it_said()
            if said:
                self.heard[spot] = said
                self.log(f"  heard at {spot}: {said[:120]}")
                if hasattr(self.rig, "say"):
                    self.rig.say(said, "recon")
        return self.heard

    def _recon_order(self, mp: int, order: list[tuple[int, int]], cap: int) -> list[tuple[int, int]]:
        """Which body is worth the budget when there are more than we can afford to ask.

        This is the decision The Investigator seat owns, and it is a different question from the
        one navigation answers. Navigation is asked *how to move* when a hop fails, off a menu of
        movements. Recon is asked *what to look at* — and nearest-first is a fine default but it is
        not a judgement. On the bike hunt the real question was which of six Cerulean buildings
        held the shop; that call was made by hand from ROM text and handed to the crew as a ranked
        list, which is exactly the work this seat exists to do.

        One consult per map, only when there are more candidates than budget, and the answer only
        promotes a body to the front — the nearest-first order is still the fallback, because an
        unparsed reply is a non-answer and must move nothing.
        """
        if len(order) <= cap or self.consult is None:
            return order
        menu = [f"{x},{y}" for x, y in order]
        facts = (
            f"You are on map {mp} at {self.rig.pos()[1:]}, doing RECON: choosing which body to "
            f"speak to first. Budget is {cap} of {len(order)} bodies. The cartridge lists these "
            f"at {menu}. Bodies are listed nearest-first already, so choose one only if something "
            "about the goal makes it worth more than proximity does."
            f"\nGOAL: reach map {self.goal}."
        )
        action, why, model = self.consult("recon", facts, menu)
        self.rig.emit("supervisor.recon_pick", map=mp, pick=action or "", model=model, why=why[:200])
        if not action:
            return order
        try:
            px, py = (int(v) for v in action.split(","))
        except ValueError:
            return order
        pick = (px, py)
        if pick not in order:
            return order
        self.log(f"  recon picks {pick} first: {why[:90]}")
        return [pick] + [c for c in order if c != pick]

    def _what_it_said(self) -> str:
        """The sentence on screen, but only when the game is actually saying one.

        The window layer is **sticky**: it keeps the last menu drawn, so a naive read returns
        'OPTION EXIT' at every cell in every direction and a leg records dialogue that never
        happened. The reliable signal is that a text box blocks movement — ``probe_step`` is False
        exactly while the game is talking, which is how the frozen-world bug was found.
        """
        if not hasattr(self.rig, "probe_step") or self.rig.probe_step():
            return ""
        return self.rig.dialogue() or ""

    def engage_trainers(self) -> bool:
        """Fight every trainer the cartridge lists for this map."""
        return self.engage_bodies(("trainer",))

    def engage_bodies(self, kinds: tuple[str, ...] = ("trainer", "npc")) -> bool:
        """Go and meet every sprite of these kinds that the cartridge lists for this map.

        Talking is not a lesser version of fighting. Three ways of acquiring a story item are
        *observed* in this ROM: an item ball, a beaten trainer dropping one (the Rocket Hideout's
        LIFT KEY), and **an npc simply handing it over** — which is how the POKe FLUTE arrived
        from Mr Fuji. Only the first two were ever automated, and `kind == "trainer"` is a filter
        that excludes both Giovanni, whose sprite is an npc, and every npc who might be holding
        the thing a run is looking for. Silph and Saffron between them hold ~41 never spoken to.
        """
        mp = self.rig.pos()[0]
        spots = [
            (s["x"], s["y"]) for s in self.rig.truth["maps"].get(str(mp), {}).get("sprites", []) if s["kind"] in kinds
        ]
        if not spots:
            self.notes.append(f"map {mp} lists no {'/'.join(kinds)} to engage")
            return False
        trainers = spots
        badges_before = self.rig.badges()
        for spot in trainers:
            if spot in self.engaged:
                continue
            self.engaged.add(spot)
            if not self._go_and_talk(spot):
                self.name_the_ride(spot)
                # Logged, not just noted: this line silently swallowed a whole debugging cycle
                # on Silph's top floor, where "could not reach" was really "a card-key door the
                # collision grid calls floor". A refusal the operator cannot see is a refusal
                # that gets re-diagnosed from scratch.
                self.log(f"  could not reach the trainer at {spot} on map {mp}")
                self.notes.append(f"could not reach the trainer at {spot} on map {mp}")
                continue
            if self.rig.badges() != badges_before:
                return True
        return True

    def party_up(self) -> bool:
        # rig.party() is ``[name, level, curHP]`` (curHP at struct +1, level at +33) — the
        # nurse's heal is proven by every fainted reading coming back above zero, not by a
        # max we would have to recall an address for.
        return bool(self.rig.party()) and all(hp > 0 for _name, _lvl, hp in self.rig.party())

    def heal_party(self) -> bool:
        """Go be healed — the only difference from --engage is the success condition.

        The measured world has no heal action: a Center body says it heals the party and the
        HP readings come back. So healing is ``engage_bodies`` judged on the party instead of
        the BADGES byte, and the report carries the readings before and after so a leg that
        arrives at the wrong door (Saffron has two that look alike) is told so plainly.
        """

        def report():
            return [f"{name} lv{lvl} hp{hp}" for name, lvl, hp in self.rig.party()]

        self.log(f"party before heal: {report()}")
        # The nurse first, when there is one. She stands behind a counter, so she is not adjacent
        # to any cell and `engage_bodies` cannot reach her — a leg once talked to all three idle
        # NPCs in Saffron's Center and reported the heal refused with three fainted members.
        counter = self.rig.center_counter(self.rig.pos()[0])
        if counter is not None:
            cell, face = counter
            if self.rig.approach({cell}):
                for _ in range(self.engage_rounds):
                    said = self.rig.talk(face)
                    self.log(f"  nurse says: {said[:90]}")
                    if self.party_up():
                        self.log(f"party after heal:  {report()}")
                        self.notes.append("the nurse healed the party")
                        return True
            else:
                self.notes.append(f"could not reach the nurse's counter at {cell}")
        for _ in range(self.engage_rounds):
            if self.party_up():
                self.log(f"party after heal:  {report()}")
                self.notes.append("the party came back standing")
                return True
            self.engage_bodies(("trainer", "npc"))
            self.log(f"party round:      {report()}")
        self.notes.append("talked every body on the map and the fainted readings stayed at zero")
        return self.party_up()

    def name_the_ride(self, spot: tuple[int, int]) -> None:
        """When a cell cannot be walked to, say whether a pad stands beside it.

        "Could not reach" is the least useful true sentence a leg can write, and Silph spent two
        sessions on it: the CARD KEY's corridor was nine steps from the pad at (27,3) and
        unreachable from every other cell on the floor. A pad named here is a route the next hop
        can take; an unnamed one is a wall the next session re-derives.
        """
        import road

        mp, _, _ = self.rig.pos()
        if self.rig.truth["maps"].get(str(mp)) is None:
            return  # a floor the truth table does not know; naming a pad there is recall, not measurement
        adjacent = {(spot[0] + 1, spot[1]), (spot[0] - 1, spot[1]), (spot[0], spot[1] + 1), (spot[0], spot[1] - 1)}
        pads = road.pads_reaching(self.rig.truth, self.rig.pairs, mp, adjacent, self.rig.bodies() - {spot})
        if not pads:
            return
        ride = ", ".join(f"{pad} (pairs with map {dest})" for pad, dest in pads)
        self.log(f"  {spot} is not walkable from here, but these pads stand beside it: {ride}")
        self.notes.append(f"{spot} on map {mp} is only reachable by riding a pad: {ride}")
        self.rig.emit("supervisor.pad_named", map=mp, target=list(spot), pads=[[list(p), d] for p, d in pads])

    def _go_and_talk(self, spot: tuple[int, int]) -> bool:
        """Walk to a cell beside ``spot`` (body-aware) and face it. Battles resolve on the way."""
        import road

        bx, by = spot
        mp, x, y = self.rig.pos()
        if str(mp) not in self.rig.truth.get("maps", {}):
            # A ride carried us onto a floor the extraction does not model. There is nothing to
            # plan here and nothing to engage; the caller re-reads position and moves on.
            return False
        adjacent = {(bx + 1, by), (bx - 1, by), (bx, by + 1), (bx, by - 1)}
        if (x, y) not in adjacent:
            # An empty `near` is not a verdict: it means no *walk* reaches this body, which is
            # exactly when a ride might. Sabrina sits behind thirty intra-map pads, and a leg that
            # returned here without ever calling `approach` met the guide at the door and reported
            # the gym cleared.
            reach = road.walkable(self.rig.truth, self.rig.pairs, mp, (x, y), self.rig.bodies() - {spot})
            near = reach & adjacent
            if not near and self._push_to(adjacent, what=f"the body at {spot}", exclude={spot}):
                mp, x, y = self.rig.pos()
                reach = road.walkable(self.rig.truth, self.rig.pairs, mp, (x, y), self.rig.bodies() - {spot})
                near = reach & adjacent
            if not near:
                # No neighbouring tile: this body may be behind a COUNTER, which is a shape the
                # engine only knew about for Pokemon Center nurses. Measured on the BIKE SHOP
                # clerk at (6,2): every adjacent cell is solid, and the talk fires from (4,2)
                # facing right. A recon leg holding the BIKE VOUCHER stood in that shop and
                # reported the clerk unreachable -- the clerk was fine, the approach was not.
                for cell, face in road.counter_stands(spot):
                    if cell in reach and self.rig.approach({cell}):
                        self.rig.talk(face)
                        return True
            if not self.rig.approach(near or adjacent):
                # No walk and no ride: the body may stand across water (Route 19's swimmers,
                # Seafoam's shore). SURF over, then approach again.
                surf = getattr(self.rig, "surf_to", None)
                if surf is None:
                    return False
                result = surf(adjacent)
                if result is not True:
                    if result != "no-route":
                        self.rig.emit("supervisor.surf_refused", map=mp, result=str(result), toward=list(spot))
                    return False
                self.rig.emit("supervisor.surfed", map=mp, to=list(self.rig.pos()[1:]), toward=list(spot))
                if not self.rig.approach(adjacent):
                    return False
            mp, x, y = self.rig.pos()
            if (x, y) not in adjacent:
                return False
        if hasattr(self.rig, "bag_full") and self.rig.bag_full() and hasattr(self.rig, "make_room"):
            # A body's hand-over silently fails at 20 stacks (the Secret House said its greeting
            # eleven times, HM03 never landed). Free a slot BEFORE the talk, not after the leg.
            self.log("  bag full before the talk: freeing a slot")
            self.rig.make_room()
        before = self.rig.bag()
        said = self.rig.talk("right" if bx > x else "left" if bx < x else "down" if by > y else "up")
        # settle() closes the win / award box that a battle leaves open. It is the step that
        # *commits* the result: it marks a fallen fighter as defeated (measured on map 178, that
        # defeat is what releases the next body to battle) and it banks the badge on a leader.
        # A win box poked with A stays pinned and the result never lands — the measured "got 1140
        # for winning!" loop — while settle's B-then-A-then-probe-move is the closer that lets the
        # cartridge finish what the fight started.
        if hasattr(self.rig, "settle"):
            self.rig.settle()
        gained = [item for item in self.rig.bag() if item not in before]
        self.log(f"  engaged {spot}: {said[:140]}")
        if gained:
            named = [(self.rig.item_name(i), q) for i, q in gained]
            self.log(f"  *** {spot} HANDED OVER {named} ***")
            self.notes.append(f"the body at {spot} gave us {named}")
        self.rig.emit("supervisor.body_engaged", map=mp, at=list(spot), said=said[:300], gained=gained)
        return True

    def _engage_until_badge(self) -> bool:
        """On the goal map, talk bodies down until the BADGES byte changes.

        The badge byte is watched for *change*, not for a remembered bit: which bit belongs to
        which leader is exactly the kind of recalled fact this project has been burned by. A
        changed byte is measurement; a named bit would be recall.

        Getting to a body goes through ``approach`` — the walk-or-ride path — not a raw walk.
        Sabrina's gym is the standing measurement: nine standing bodies, and every one past the
        nearest sits on a pocket that thirty intra-map pads hold from the baton's bench. The
        walk-only version of this loop marked each of them tried and skipped them, and four legs
        in a row spent their reports on "engaged every body, badge byte unchanged" — a verdict the
        walk can never see past a pad.
        """
        before = self.rig.badges()
        return self._engage_until(lambda: self.rig.badges() != before)

    def bag_holds(self, item: str) -> bool:
        """Whether the bag holds ``item`` -- by the cartridge's own name for what is in it."""
        want = item.upper()
        return any(self.rig.item_name(item_id).upper().startswith(want) for item_id, _qty in self.rig.bag())

    def _engage_until_item(self, item: str) -> bool:
        """On the goal map, talk to bodies until the bag holds ``item``.

        The same loop as the badge hunt with the bag as the judge -- the mission's own success
        condition for a handed-over HM ("confirm HM04 is actually in the bag; don't trust the
        dialogue"). A map whose bodies were all met without the item is not a failure of the
        leg, it is one door ruled out; the chain in ``cmd_run`` moves to the next.
        """
        if self.bag_holds(item):
            self.notes.append(f"the bag already holds {item}")
            return True
        return self._engage_until(lambda: self.bag_holds(item))

    def _engage_until(self, done) -> bool:
        """Meet every live body on this map, nearest-first then round-robin, until ``done()``."""
        spoken: set[tuple[int, int]] = set()
        order: list[tuple[int, int]] = []  # stable roster, for the retry phase
        cursor = 0
        for _ in range(self.engage_rounds):
            if done():
                return True
            mp, _x, _y = self.rig.pos()
            # Item balls are in the live sprite table too; they are sweep_items' job, and "talking"
            # to one reads whatever the bag step left on the window layer ("OPTION EXIT", measured
            # on maps 194, 219, 234) -- never the body's words.
            balls = set(self.rig.item_balls(mp)) if hasattr(self.rig, "item_balls") else set()
            all_bodies = [b for b in self.rig.bodies() if b not in balls]
            if not all_bodies:
                return done()
            # Keep a stable order for the retry phase: which bodies reappear is decided by the
            # roster, not by whatever happens to be standing nearest.
            order = [b for b in order if b in all_bodies] + [b for b in sorted(all_bodies) if b not in order]
            unspoken = [b for b in all_bodies if b not in spoken]
            if unspoken:
                # First pass over new bodies, nearest-first: cheap, and it meets the whole roster.
                bx, by = min(unspoken, key=lambda b: abs(b[0] - _x) + abs(b[1] - _y))
            else:
                # Every body is met once and the badge still has not moved. That is the gated-
                # leader shape, measured on map 178: the leader reads a coach line until the gym's
                # member falls, then — and only then — battles. Nearest-first would just keep
                # re-picking the member that is still standing beside us, so the loop walks the
                # roster in a fixed order (round-robin) instead, and the leader gets the turn the
                # member's defeat opens within one full cycle.
                cursor = (cursor + 1) % len(order)
                bx, by = order[cursor]
            spoken.add((bx, by))
            if not self._go_and_talk((bx, by)):
                # approach says no even after riding. Name the pads that could open it, so the
                # record reads as a route the next leg can take instead of a wall.
                self.name_the_ride((bx, by))
                self.log(f"  could not reach the body at {(bx, by)} on map {mp}")
        return done()

    # ---- the exhaustion record ---------------------------------------------------------------

    def write_exhaustion(self, failure: str, hop: dict | None) -> Path:
        import expedition_crew as crew

        mp, x, y = self.rig.pos()
        where = f"map {mp} ({x}, {y}) -> goal {self.goal}"
        facts = describe(self.rig, self.goal, hop, failure, self.notes, self.heard)
        shot = self.rig.screenshot(f"exhausted_map{mp}") if hasattr(self.rig, "screenshot") else None
        if shot:
            # This is the exact moment a leg declares a wall. Twice this project has been wrong
            # doing that from the collision grid alone -- a "sealed" verdict written from wherever
            # the leg happened to stop, generalised to the whole map. The picture goes in the
            # record itself so the next reader can look before trusting the verdict.
            facts += f"\n\nSCREENSHOT AT THE POINT OF FAILURE: {shot}"
        doc = crew.failure_doc(
            self.rig.run_id,
            f"reach map {self.goal}",
            where,
            facts,
            self.tried,
        )
        self.learnings_dir.mkdir(parents=True, exist_ok=True)
        path = self.learnings_dir / f"map{mp}-to-{self.goal}-stuck-{self.rig.run_id}.md"
        path.write_text(doc)
        self.rig.emit(
            "supervisor.exhausted", goal=self.goal, pos=[mp, x, y], failure=failure, doc=str(path), tried=self.tried
        )
        # The record above is prose for a human. The journal line is for the NEXT LEG: prior_observations
        # hands it to every seat that lands on this map, so a wall found once is never re-derived
        # blind. Until 2026-09-04 exhaustion reached the future only if a person pasted the doc into
        # a mission by hand; the walls that fell today were ones a prior leg had already measured.
        try:
            from memory_writer import append_observations

            tried = ", ".join(str(t) for t in self.tried[-6:]) or "nothing"
            row = {
                "referenced_time": time.strftime("%Y-%m-%d"),
                "priority": "important",
                "source_session": "supervisor",
                "content": (
                    f"map={mp} exhausted at ({x},{y}) reaching goal {self.goal}: {failure}; tried {tried}"
                    + (f"; screenshot {shot}" if shot else "")
                    + f"; record {path.name}"
                ),
            }
            append_observations(self.memory_dir, [row], dedupe=True)
        except OSError as e:  # the journal must never fail a leg
            self.log(f"  journal write failed: {e}")
        self.log(f"EXHAUSTED — record written to {path}")
        return path

    # ---- the leg -----------------------------------------------------------------------------

    def run(self) -> dict:
        import road
        import rom_truth as rt

        started = self.clock()
        self.rig.emit("supervisor.leg_start", goal=self.goal, pos=list(self.rig.pos()), budget_s=self.budget_s)
        for _ in range(self.max_hops):
            elapsed = self.clock() - started
            if elapsed >= self.budget_s:
                return self._finish("budget", f"budget of {self.budget_s:.0f}s spent")
            cur = self.rig.pos()[0]
            if cur == self.goal:
                # Confirm on a settled read: a torn one across a warp names a tile that cannot
                # exist, and "arrived" is the one verdict that must never be reported from it.
                cur = self.rig.settled_pos()[0]
            if cur == self.goal:
                if self.clear_floor:
                    self.engage_trainers()
                if self.sweep:
                    self.sweep_items(self.want)
                if self.heal and not self.heal_party():
                    return self._finish(
                        "heal-refused", f"arrived on map {self.goal} but the fainted readings stay at zero"
                    )
                if self.engage and not self._engage_until_badge():
                    return self._finish("engaged-no-badge", "arrived, engaged every body, badge byte unchanged")
                if self.hunt is not None:
                    if self._engage_until_item(self.hunt):
                        return self._finish("item-found", f"a body on map {self.goal} handed over {self.hunt}")
                    return self._finish(
                        "engaged-no-item", f"arrived on map {self.goal}, engaged every body, {self.hunt} not in the bag"
                    )
                return self._finish("arrived", f"reached map {self.goal}")
            chain = rt.route(self.rig.truth, cur, self.goal, banned=self.banned)
            hop = chain[0] if chain else None
            if hop is None:
                failure = "no-route"
            else:
                self.log(f"hop: {cur} --{hop['via']}--> {hop['to']}")
                failure = self._hop(hop)
                if failure is None:
                    continue  # progress: the wall counter for the next hop starts clean
            wall = f"{cur}->{hop['to'] if hop else self.goal}"
            self.attempts[wall] += 1
            attempt = self.attempts[wall]
            self.log(f"hop failed: {failure} (attempt {attempt}/{LADDER_ATTEMPTS} on {wall})")
            self.rig.emit("supervisor.hop_failed", wall=wall, failure=failure, attempt=attempt)
            # Look at it before reasoning about it. A refusal that prints a sentence is a
            # different fact from a silent one, and the sentence is free to obtain.
            self.read_refusal(hop)
            # Determinism first, in the order the measurements rule things out.
            # 1. One sprite explains the severance -> it is a gate to open, not a missing road.
            #    This must come before any ban: Route 12's north road was banned as impassable
            #    when the whole wall was a single unfought trainer at (10,62).
            # 2. Otherwise, a hop still severed after its gate building was tried is a fact about
            #    the graph — ban it and take another chain rather than spending a crew ladder on
            #    a road the world does not have.
            if hop is not None and failure in ("no-path", "body-blocked") and self._clear_blocker(hop):
                continue
            #    A growth the party can cut is the same shape as a body: one cell, and lifting it
            #    reconnects the target. It is measured off the tile model and proved by the step.
            if hop is not None and failure == "no-path" and self._cut_through(hop):
                continue
            #    A boulder in the line, or water between the regions, are the same shape again:
            #    a field move the party holds reconnects the target, and the map proves it.
            if hop is not None and failure in FIELD_MOVE_FAILURES and self._push_through(hop):
                continue
            if hop is not None and failure in FIELD_MOVE_FAILURES and self._surf_through(hop):
                continue
            # 3. A door that will not open is as structural as a severed grid. Silph 1F's
            #    (16,10) pad is dead, and the floor has two other ways up — (26,0) and (20,0).
            #    Routing around it is a lookup; the crew spent a whole ladder on it instead.
            structural = failure == "warp-dead" or (failure == "no-path" and (cur, hop["to"]) in self.gated)

            if hop is not None and structural:
                if self._reroute_around(hop):
                    continue
            if attempt > LADDER_ATTEMPTS:
                # Before giving up on the leg, give up on the *door*. Any hop the whole ladder
                # could not open is structural for this leg, and two rooms taught that in one
                # night: Silph 5F parks a Rocket on the (24,0) pad while five other doors out of
                # the room went unexamined, and 7F's 11F-side pocket has no route to 8F at all,
                # only back to 3F. Both times the loop spent both seats on one door and then died
                # holding a map full of untried ones. Banning costs a route we might have had
                # after a longer wait; `route` returning None still ends the leg honestly.
                if hop is not None and self._reroute_around(hop):
                    continue
                self.write_exhaustion(failure, hop)
                return self._finish("exhausted", f"the ladder ended on {wall} ({failure})")
            self.recon(cur)  # ask the map before asking a model; the seats get what it said
            tier = "navigation" if attempt <= NAV_ATTEMPTS else "puzzle"
            facility = self.rig.truth["maps"].get(str(cur), {}).get("tileset") == road.FACILITY_TILESET
            unlooted = [b for b in self.rig.item_balls(cur) if (cur, b) not in self.looted]
            menu = menu_for(
                failure, edge_hop=bool(hop and hop["via"] == "edge"), facility=facility, items=bool(unlooted)
            )
            facts = describe(self.rig, self.goal, hop, failure, self.notes, self.heard)
            action, why, model = self.consult(tier, facts, menu)
            self.consults.append({"wall": wall, "tier": tier, "model": model, "action": action, "why": why})
            self.rig.emit("supervisor.consult", wall=wall, tier=tier, model=model, action=action or "", why=why[:300])
            # A seat's WHY is evidence even when its ACTION does not clear the wall. Tonight the
            # Point Man named the CARD KEY twice on the first Silph leg and the loop scored both
            # as failed answers, because only the ACTION was ever read. When two seats keep
            # explaining the *same* wall, the leg has a diagnosis and should hand it over rather
            # than spend the rest of the ladder producing it again.
            if len({c["why"].strip().lower() for c in self.consults if c["wall"] == wall and c["why"]}) == 1:
                agreeing = [c for c in self.consults if c["wall"] == wall and c["why"]]
                if len(agreeing) >= 2:
                    self.notes.append(f"both seats explain {wall} the same way: {agreeing[-1]['why'][:200]}")
            if action is None:
                self.notes.append(f"the {tier} seat returned no menu action; retrying the hop unchanged")
                continue  # a non-answer costs the attempt it already cost, and nothing else
            if action == "GIVE_UP":
                self.write_exhaustion(failure, hop)
                return self._finish("gave-up", f"the {tier} seat chose GIVE_UP on {wall}")
            self._act(action, hop)
        return self._finish("max-hops", f"{self.max_hops} hops without arriving")

    def _finish(self, outcome: str, reason: str) -> dict:
        mp, x, y = self.rig.settled_pos()
        result = {
            "ok": outcome in ("arrived", "item-found"),
            "outcome": outcome,
            "reason": reason,
            "goal": self.goal,
            "pos": [mp, x, y],
            "badges": self.rig.badges(),
            "consults": self.consults,
            "gates": {f"{m}:{x},{y}": said for (m, x, y), said in self.gates.items()},
            "run_id": self.rig.run_id,
        }
        self.rig.emit("supervisor.leg_end", **{k: v for k, v in result.items() if k not in ("consults", "gates")})
        for where, said in self.gates.items():
            self.log(f'  GATE {where}: "{said[:120]}"')
        self.log(f"LEG {outcome}: {reason} — at {(mp, x, y)}, badges 0b{self.rig.badges():08b}")
        return result


def parse_goals(text: str) -> list[int]:
    """``--goal 10,181,178`` — one booted cartridge, a chain of legs, banked between each."""
    return [int(part) for part in str(text).replace(" ", "").split(",") if part]


def cmd_explore(args) -> int:  # pragma: no cover - drives the emulator; verified live, not in unit tests
    """Walk the frontier: survey every pocket reachable from here, merging what each one teaches.

    A static pocket model is only as good as its gate coverage, and gates only exist where
    somebody has walked. Silph's 233 looked like one 234-cell pocket because its eight measured
    gates all came from the one pocket the lift reaches — so a route planned through it died on
    the first hop into ground nobody had surveyed. The fix is not a better guess, it is coverage:
    enter a pocket, survey it, fold its gates into `references/measured_gates.json`, and push
    every exit it actually has onto the frontier.

    This is a *discovery* pass. Backtracking is done by reloading a snapshot, so items picked up
    and fights won on an abandoned branch do not persist — the output is the map, and a normal
    leg walks it afterwards with the corrected model.
    """
    import io as _io

    import rom_truth as rt
    from expedition_rig import Rig

    rig = Rig(args.state, live_label=args.live_label)
    area = {int(m) for part in (args.area or "").split(",") if part.strip() for m in [part.strip()]}
    started = time.monotonic()
    origin = _io.BytesIO()
    rig.pb.save_state(origin)
    frontier = [(origin, "start")]
    visited: set[tuple] = set()
    found: list[dict] = []
    while frontier and time.monotonic() - started < args.budget and len(visited) < args.max_pockets:
        snap, label = frontier.pop()
        snap.seek(0)
        rig.pb.load_state(snap)
        where = rig.settled_pos()
        print(f"\n=== pocket via {label}: map {where[0]} at {where[1:]} ===", flush=True)
        survey = rig.survey_pocket(max_cells=args.max_cells)
        signature = (survey["map"], min(survey["cells"]) if survey["cells"] else where[1:])
        if signature in visited:
            print("  already surveyed this pocket", flush=True)
            continue
        visited.add(signature)
        if survey["doors"]:
            rt.merge_measured_gates({survey["map"]: survey["doors"]})
        cells = {tuple(c) for c in survey["cells"]}
        sprites = rig.truth["maps"].get(str(survey["map"]), {}).get("sprites", [])
        here = {
            "map": survey["map"],
            "anchor": list(signature[1]),
            "cells": len(cells),
            "gates": len(survey["doors"]),
            "balls": [[s["x"], s["y"]] for s in sprites if s["kind"] == "item" and (s["x"], s["y"]) in cells],
            "npcs": [[s["x"], s["y"]] for s in sprites if s["kind"] == "npc" and (s["x"], s["y"]) in cells],
            "trainers": [[s["x"], s["y"]] for s in sprites if s["kind"] == "trainer" and (s["x"], s["y"]) in cells],
            "exits": survey["exits"],
        }
        found.append(here)
        print(f"  {here['cells']} cells, {here['gates']} gates, balls={here['balls']} npcs={here['npcs']}", flush=True)
        rig.emit("supervisor.pocket_explored", **{k: v for k, v in here.items() if k != "exits"})
        for step, dest in survey["exits"].items():
            if area and dest not in area:
                continue  # the frontier is unbounded otherwise: it walked out of Silph into
                # Saffron and then Route 7, which is true exploration and the wrong budget
            x, y, direction = step.split(",")
            branch = _io.BytesIO()
            snap.seek(0)
            rig.pb.load_state(snap)
            if rig.approach({(int(x), int(y))}):
                rig.io.press(direction, hold=8, release=8)
                rig.io.wait(60)
                if rig.settled_pos()[0] == dest:
                    rig.pb.save_state(branch)
                    frontier.append((branch, f"{survey['map']} {step} -> {dest}"))
    origin.seek(0)
    rig.pb.load_state(origin)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(found, indent=2) + "\n")
        print(f"\nwrote {out}", flush=True)
    print(json.dumps({"pockets": len(found), "frontier_left": len(frontier)}))
    rig.finish(outcome="explore")
    return 0


def cmd_survey(args) -> int:  # pragma: no cover - drives the emulator; verified live, not in unit tests
    """Measure a pocket's true shape and write down every wall that talks.

    The output is the thing Silph has been missing all along: which steps the game refuses and
    what it says when it does. A static region cannot answer that, because the collision grid
    calls a card-key door plain floor.
    """
    from expedition_rig import Rig

    rig = Rig(args.state, live_label=args.live_label)
    print(f"surveying from {rig.settled_pos()}", flush=True)
    survey = rig.survey_pocket(max_cells=args.max_cells)
    if survey["doors"] and not args.no_merge:
        import rom_truth as rt

        rt.merge_measured_gates({survey["map"]: survey["doors"]})
        print(f"merged {len(survey['doors'])} gates into {rt.MEASURED_GATES.name}", flush=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(survey, indent=2) + "\n")
        print(f"wrote {out}", flush=True)
    talking = sorted(set(survey["doors"].values()))
    for said in talking:
        where = [k for k, v in survey["doors"].items() if v == said]
        print(f'  "{said[:90]}" at {where}', flush=True)
    print(json.dumps({k: v for k, v in survey.items() if k != "cells"}))
    rig.finish(outcome="survey")
    return 0


def cmd_lift_tour(args) -> int:  # pragma: no cover - drives the emulator; verified live, not in unit tests
    """Ride a lift car to each named floor, clearing and looting what the lift can reach.

    A lift is not a hop in the connection graph, and that is exactly why it matters here: it
    deposits you *inside* pockets the walking routes cannot enter. Measured in Silph — riding to
    5F lands on map 210 at (20,1), while every walked approach to 210 arrives at (8,15) on the
    other side of a card-key door. So the tour returns to the car between floors by reloading it,
    rather than trying to walk back through doors that are shut.
    """
    import io as _io

    from expedition_rig import BattleWedge, Rig

    rig = Rig(args.state, live_label=args.live_label)
    floors = [f.strip() for f in args.floors.split(",") if f.strip()]
    print(f"start {rig.settled_pos()} in the car; touring {floors}", flush=True)
    car_map = rig.settled_pos()[0]
    car = _io.BytesIO()
    rig.pb.save_state(car)
    results = []
    for floor in floors:
        car.seek(0)
        rig.pb.load_state(car)
        print(f"\n=== {floor} ===", flush=True)
        if not rig.ride_elevator(floor):
            print(f"  the lift would not take us to {floor}", flush=True)
            results.append({"floor": floor, "ok": False})
            continue
        where = rig.settled_pos()
        runner = LegRunner(rig, goal=where[0], consult=lambda *a: (None, "", "none"), log=print)
        try:
            cleared = runner.engage_trainers()
            gained = runner.sweep_items()
        except BattleWedge as exc:
            print(f"  battle wedge on {floor}: {exc}", flush=True)
            results.append({"floor": floor, "ok": False})
            continue
        print(f"  {floor} -> map {where[0]} at {where[1:]}; cleared={cleared} gained={gained}", flush=True)
        rig.emit("supervisor.lift_floor", floor=floor, map=where[0], gained=gained)
        results.append({"floor": floor, "ok": True, "map": where[0], "gained": gained})
        if args.bank:
            rig.bank(f"{args.bank}-{floor}")
        # Carry this floor's fights and pickups forward by re-snapshotting the car — but only
        # once we are actually back inside it. Snapshotting here while still standing on the
        # floor is how the first tour rode 2F and then reported "the lift would not take us to
        # 3F" nine times: it was reloading a state that had never been in the car.
        doors = {(w[0], w[1]) for w in rig.truth["maps"].get(str(where[0]), {}).get("warps", []) if w[2] == car_map}
        returned = False
        for door in sorted(doors):
            if rig.warp(where[0], *door) is True or rig.settled_pos()[0] == car_map:
                returned = rig.settled_pos()[0] == car_map
                if returned:
                    break
        if returned:
            car.seek(0)
            rig.pb.save_state(car)
        else:
            print(f"  could not get back into the car from {floor}; the next floor starts fresh", flush=True)
    print(json.dumps({"floors": results, "bag": rig.bag_named(), "run_id": rig.run_id}))
    rig.finish(outcome="lift-tour")
    return 0


def cmd_hunt(args) -> int:  # pragma: no cover - drives the emulator; verified live, not in unit tests
    """Travel to a map and come back with a species in the party.

    Badges 7 and 8 need Surf, and this cartridge's cheapest surfer is a chain, not a find:
    Nidoran-M (map 33, levels 2-4) evolves at 16 into Nidorino and with the MOON STONE already in
    the bag into Nidoking, which learns HM03. A hunt is therefore a leg like any other — route
    there, then pace the ROM's own grass with the catch armed — except that its success condition
    is the party growing rather than a position.
    """
    import quartermaster as qm
    from expedition_rig import BattleWedge, Rig
    from memory_reader import SPECIES_ID_MAP

    wanted = qm.parse_catch(args.species)
    rig = Rig(args.state, live_label=args.live_label)
    names = {SPECIES_ID_MAP.get(i, str(i)) for i in wanted}
    print(f"start {rig.pos()} party {[n for n, _l, _h in rig.party()]} hunting {sorted(names)}", flush=True)
    if len(rig.party()) >= 6:
        print("  the party is full — a catch would go to a box; deposit someone first", flush=True)
        return 2
    started = time.monotonic()
    if rig.pos()[0] != args.map:
        leg = LegRunner(rig, goal=args.map, budget_s=args.budget / 2, consult=None)
        result = leg.run()
        if not result.get("ok"):
            print(json.dumps(result), flush=True)
            return 1
    rig.ag.catch_wanted = set(wanted)  # the agent's battle turn throws instead of attacking
    before = len(rig.party())
    rig.emit("supervisor.hunt_start", map=args.map, species=sorted(names), party=before)
    try:
        got = rig.roam_grass(
            args.map,
            lambda: len(rig.party()) > before or time.monotonic() - started > args.budget,
        )
    except BattleWedge as exc:
        print(f"  battle wedged: {exc}", flush=True)
        got = False
    caught = len(rig.party()) > before
    print(f"party now {[n for n, _l, _h in rig.party()]}", flush=True)
    rig.emit("supervisor.hunt_end", caught=caught, party=[n for n, _l, _h in rig.party()])
    if caught and args.bank:
        rig.bank(args.bank)
    rig.finish(outcome="caught" if caught else "no-catch")
    print(json.dumps({"caught": caught, "roamed": got, "run_id": rig.run_id}), flush=True)
    return 0 if caught else 1


def cmd_run(args) -> int:  # pragma: no cover - drives the emulator; verified live, not in unit tests
    """Boot a baton and drive the supervised leg chain. This is the loop body the skill promises.

    One boot, N goals: the campaign shape. Each goal gets its share of the remaining budget, the
    state is banked after every cleared goal (so a failure never costs the legs that worked), and
    the chain stops at the first leg that does not arrive — the record for that wall is already
    written by then.
    """
    from expedition_rig import BattleWedge, Rig

    goals = parse_goals(args.goal)
    rig = Rig(args.state, live_label=args.live_label)
    print(f"start {rig.pos()} badges 0b{rig.badges():08b} {rig.party()}", flush=True)
    consult = (lambda tier, facts, menu: (None, "consults disabled", "none")) if args.no_consult else None
    started, results = time.monotonic(), []
    for index, goal in enumerate(goals, start=1):
        left = args.budget - (time.monotonic() - started)
        if left <= 0:
            print(f"budget spent before leg {index}/{len(goals)} (goal {goal})", flush=True)
            break
        share = left if index == len(goals) else left / (len(goals) - index + 1)
        print(f"\n=== leg {index}/{len(goals)}: goal map {goal}, {share / 60:.0f}m ===", flush=True)
        runner = LegRunner(
            rig,
            goal=goal,
            budget_s=share,
            # Engaging is what turns arrival into a badge, so it belongs to the last goal only:
            # a mid-chain city is a waypoint, and talking to every body in it is not the leg.
            # Heal is the same shape with a different success condition: the party readings
            # coming back, judged only on the goal that is a Center.
            engage=args.engage and index == len(goals),
            # Healing belongs at the Center, which is a *mid-chain* goal — copying --engage's
            # "last goal only" rule meant a chain of 182,235 healed at Giovanni's floor, i.e.
            # never, and carried three fainted party members into the boss.
            heal=args.heal,
            sweep=args.sweep_items,
            want=args.want,
            # Unlike --engage, which watches for a badge and so belongs to the final gym, a
            # floor clear is worth doing on every goal in the chain: the thing a story floor
            # yields is usually carried by one of its trainers.
            clear_floor=args.clear_floor,
            # A hunt is judged on the bag at EVERY goal: the chain is the list of doors the
            # item might be behind, and the first one that yields it ends the chain.
            hunt=args.hunt_item,
            consult=consult,
        )
        try:
            result = runner.run()
        except BattleWedge as exc:
            result = {"ok": False, "outcome": "battle-wedge", "reason": str(exc), "pos": list(rig.pos())}
        results.append({k: v for k, v in result.items() if k != "consults"})
        if args.bank:
            rig.bank(args.bank if len(goals) == 1 else f"{args.bank}-{goal}")
        if result.get("outcome") == "item-found":
            print(f"{args.hunt_item} is in the bag: {rig.bag_named()}", flush=True)
            break
        if result.get("outcome") == "engaged-no-item":
            continue  # one door ruled out; the next goal is the next door
        if not result.get("ok"):
            break
    if args.hunt_item:
        found = any(r.get("outcome") == "item-found" for r in results)
        if args.bank and found:
            rig.bank(args.bank)
        verdict = f"{args.hunt_item} won" if found else f"{args.hunt_item} not found"
        rig.finish(outcome=f"hunt: {verdict}", goals=str(goals))
        report = {"legs": results, "found": found, "bag": rig.bag_named(), "pos": list(rig.pos()), "run_id": rig.run_id}
        print(json.dumps(report))
        return 0 if found else 1
    if args.bank and results and results[-1].get("ok"):
        rig.bank(args.bank)
    rig.finish(outcome=results[-1]["outcome"] if results else "no-legs", goals=str(goals))
    print(json.dumps({"legs": results, "badges": rig.badges(), "pos": list(rig.pos()), "run_id": rig.run_id}))
    return 0 if results and all(r["ok"] for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="drive one supervised leg from a baton to a goal map")
    rn.add_argument("--state", required=True, help="baton .state to boot from")
    rn.add_argument("--goal", required=True, help="goal map id, or a comma-separated chain (10,181,178)")
    rn.add_argument("--budget", type=float, default=7200.0, help="seconds for the whole chain")
    rn.add_argument("--bank", default=None, help="bank the end state under this name")
    rn.add_argument("--live-label", default=None, help="stream to the viewer under this label")
    rn.add_argument("--engage", action="store_true", help="on arrival, engage bodies until the BADGES byte changes")
    rn.add_argument(
        "--heal",
        action="store_true",
        help="on the last goal, engage the map's bodies until the fainted readings come back",
    )
    rn.add_argument(
        "--sweep-items", action="store_true", help="on arrival, open every item ball the cartridge lists for the map"
    )
    rn.add_argument(
        "--clear-floor", action="store_true", help="on the last goal, fight every trainer the cartridge lists there"
    )
    rn.add_argument("--want", help="item name this leg came for; its ball is opened first")
    rn.add_argument(
        "--hunt-item",
        default=None,
        help="on EVERY goal, engage bodies until the bag holds this item; the chain is the doors it might be behind",
    )
    ht = sub.add_parser("hunt", help="travel to a map and come back with a species in the party")
    ht.add_argument("--state", required=True)
    ht.add_argument("--species", required=True, help="e.g. Nidoran-M, or a raw internal id")
    ht.add_argument("--map", type=int, required=True)
    ht.add_argument("--budget", type=float, default=1800)
    ht.add_argument("--bank")
    ht.add_argument("--live-label")
    rn.add_argument("--no-consult", action="store_true", help="deterministic only — never call a model")
    ce = sub.add_parser("classify-exit", help="decide resume/continue/next/retry/escalate after an operator exit")
    ce.add_argument("--state", type=Path, required=True)
    ce.add_argument("--budget", type=float, required=True)
    ce.add_argument("--used", type=float, required=True)
    ce.add_argument("--baton", type=int, default=0)
    ce.add_argument("--harness-death", type=int, default=0)
    ce.add_argument("--load-ok", type=int, default=1)
    ce.add_argument("--lane-log", type=Path, action="append", default=[])
    ex = sub.add_parser("explore", help="survey every pocket reachable from here, merging what each teaches")
    ex.add_argument("--state", required=True)
    ex.add_argument("--budget", type=float, default=3600.0)
    ex.add_argument("--max-pockets", type=int, default=30)
    ex.add_argument("--max-cells", type=int, default=400)
    ex.add_argument("--out", default=None)
    ex.add_argument("--area", default=None, help="comma-separated map ids the frontier may enter")
    ex.add_argument("--live-label", default=None)
    sv = sub.add_parser("survey", help="measure a pocket by attempted steps and record every wall that talks")
    sv.add_argument("--state", required=True)
    sv.add_argument("--max-cells", type=int, default=400)
    sv.add_argument("--out", default=None, help="write the survey as JSON here")
    sv.add_argument("--live-label", default=None)
    sv.add_argument("--no-merge", action="store_true", help="do not fold the gates into references/")
    lt = sub.add_parser("lift-tour", help="ride a lift car to each floor, clearing and looting each")
    lt.add_argument("--state", required=True, help="a baton standing inside the lift car")
    lt.add_argument("--floors", required=True, help="comma-separated labels exactly as the panel prints them")
    lt.add_argument("--bank", default=None)
    lt.add_argument("--live-label", default=None)
    rp = sub.add_parser("replay", help="what the supervisor would have said, from a run's lane logs")
    rp.add_argument("logs", type=Path, nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "hunt":
        return cmd_hunt(args)  # pragma: no cover - CLI dispatch, like every other subcommand
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "lift-tour":
        return cmd_lift_tour(args)
    if args.cmd == "survey":
        return cmd_survey(args)
    if args.cmd == "explore":
        return cmd_explore(args)
    if args.cmd == "replay":
        springs: Counter = Counter()
        for p in args.logs:
            springs.update(spring_counts(p.read_text(errors="replace")))
        for wall, n in springs.most_common():
            if n >= SPRING_MIN:
                print(f"WALL {wall}: {n} round trips")
        return 0
    sup = Supervisor.load(args.state)
    texts = [p.read_text(errors="replace") for p in args.lane_log if p.exists()]
    nudges = sup.observe(texts, load_ok=bool(args.load_ok))
    decision = sup.classify_exit(
        budget_s=args.budget, used_s=args.used, baton=bool(args.baton), harness_death=bool(args.harness_death)
    )
    if nudges and decision.get("prompt"):
        decision["prompt"] += "\n" + "\n".join(nudges)
    decision["nudges"] = nudges
    sup.save(args.state)
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
