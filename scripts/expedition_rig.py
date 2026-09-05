"""The Rig — one booted cartridge, wired for a supervised leg.

This is the scratchpad harness that won badges 4 and 5 (``data/local_runs/roster-bench/
expedition.py``), promoted into ``scripts/`` because the doctrine says so: *fix the engine, do
not fork the scratchpad*. A leg that lives in ``data/`` teaches the repo nothing, and six of
them in a day is how 2026-08-30 went.

What the Rig owns, and nothing else:

* **Boot.** A baton ``.state`` loaded into ``PokemonAgent``'s PyBoy, plus the extracted truth
  and its tile pairs. The agent supplies the battle turn (catch hook, potions, forced switch,
  evolution guard) that ``road`` delegates to.
* **Recording.** With ``live_label=`` every button press is a turn, every turn a frame, and the
  ``runs/<id>/`` folder grows while we play — the viewer reads it live (no ``summary.json`` yet
  means "running"). ``EmuIO.press`` and ``GameController.press`` have different signatures, so
  the wrapper passes ``*a, **kw`` through; a wrapper that does not is the measured way to break
  ``road``'s ``press(dir, hold=8, release=8)``.
* **Telemetry.** Every leg emits to ``data/telemetry/game/<UTC-date>.jsonl`` under a stable
  ``run_id``. A run that does not emit is unminable, which is the whole reason the sink exists.
* **Reads.** Position, party, badges, dialogue — measured from RAM, never assumed.

A battle that will not end is a ``BattleWedge``, not a ``sys.exit``: the supervisor owns what
happens after a failure, and a harness that kills the process denies it that.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:  # pragma: no cover - the Rig is imported from repo root and scripts/ alike
    sys.path.insert(0, str(SCRIPT_DIR))

import quartermaster as qm  # noqa: E402
import road  # noqa: E402
import rom_truth as rt  # noqa: E402

ROM_DEFAULT = WORKSPACE / "rom" / "pokemon_red.gb"
BATON_DIR = WORKSPACE / "data" / "local_runs" / "roster-bench"
TELEMETRY_DIR = WORKSPACE / "data" / "telemetry" / "game"
SCREENS_DIR = TELEMETRY_DIR.parent / "screens"  # what a stuck moment actually looked like
RUNS_DIR = WORKSPACE / "runs"
VIEWER_WS = "ws://127.0.0.1:8201"

ADDR_BADGES = 0xD356  # game_profile.RED_BLUE.addr_badges
ADDR_FACING = 0xC109  # the player's facing — part of the state key on any tile-driven floor
ADDR_PARTY_COUNT, ADDR_PARTY_STRUCTS, PARTY_STRUCT_SIZE = 0xD163, 0xD16B, 44
# Measured, not recalled: with SURF=57 and CUT=15 looked up from this cartridge's move-name
# table, SURF sits in Gyarados' party struct at offset 11 and CUT in Charizard's at 8, and in no
# other struct of the six -- two independently known moves landing inside one four-byte window.
MOVE_SLOTS = range(8, 12)


def _menu_key(text: str) -> str:
    """Letters and digits only, accents folded, for matching a name against a rendered row.

    Measured on Route 16: the bag draws "POKé FLUTE" and the item table names it "POKe FLUTE";
    upper-casing the two gives POKÉFLUTE and POKEFLUTE, and the flute was reported "not in the
    bag" with the sleeping body still on the road. The decoder's stand-in letter and the screen's
    accented one must land on the same key.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", folded.upper())


ADDR_BAG_COUNT, ADDR_BAG_ITEMS = 0xD31D, 0xD31E  # quartermaster's, verified live in the mart probe
BAG_SLOTS = 20  # a full bag refuses pickups silently (measured in the Rocket Hideout)
ADDR_LIST_SCROLL = 0xCC36  # item-list scroll offset; 0xCC26 is the cursor WITHIN the 3-row window
BATTLE_TURN_CAP = 200  # a battle past this is wedged, not long
# A special (no-FIGHT) menu — e.g. the Safari Zone's BAIT/ROCK/BALL/RUN — is undriveable by the
# fight routine, which anchors every turn on that option. Once the routine has had this many
# turns without the menu ever showing a FIGHT, the wild is fleeable and we run. A gym leader
# always draws a FIGHT (so the guard below never fires on a trainer) and cannot be fled anyway.
SPECIAL_MENU_GRACE = 5


class BattleWedge(RuntimeError):
    """A battle that would not end. The state is banked; the supervisor decides what next."""


def telemetry_path(now: datetime | None = None, root: Path | None = None) -> Path:
    """The sink line for today. One file per UTC date — the shape the benchmarks glob."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return (root or TELEMETRY_DIR) / f"{stamp}.jsonl"


def emit_event(run_id: str, event: str, fields: dict | None = None, *, root: Path | None = None) -> dict:
    """Append one expedition event to the game sink and return the record written."""
    import expedition_crew as crew

    record = crew.telemetry_record(run_id, event, fields)
    path = telemetry_path(root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def engaged_near(map_id: int, sprite: tuple[int, int], radius: int = 1, root: Path | None = None) -> list[str]:
    """Every distinct sentence recorded on ``map_id`` within ``radius`` tiles of ``sprite``.

    Written after nearly reporting a false "never engaged" today: a discovery event's (x, y) is
    where the PLAYER stood when it was said, not the sprite's own tile — you talk to a body from
    beside it, never from on top of it. Matching a sprite's coordinates exactly against the sink
    can never hit, and "no match" then reads as "never talked to" when the truth may be the
    opposite. This is the fix, not just the note: search a radius, not a point.
    """
    bx, by = sprite
    found: list[str] = []
    seen: set[str] = set()
    for path in sorted((root or TELEMETRY_DIR).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("map") != map_id:
                continue
            x, y = d.get("x"), d.get("y")
            if x is None or y is None or abs(x - bx) > radius or abs(y - by) > radius:
                continue
            text = (d.get("said") or d.get("text") or "").strip()
            if text and text not in seen:
                seen.add(text)
                found.append(text)
    return found


def forget_pick(rows, move_ids: dict, keep: set) -> tuple[int, str] | None:
    """Which row of the "Which move should be forgotten?" list to give up, and its name.

    Measured on surf_strength_hm03.state teaching HM03 to a four-move Gyarados (2026-09-04): the
    list is drawn OVER the ABLE/NOT ABLE roster on consecutive rows (8..11), one move per row --
    not every other row like the field menus -- and a row can carry a one-glyph sprite prefix
    from the roster underneath ("H SPLASH", "G BITE"). The victim is the first listed move that
    is a real move on this cartridge and not in ``keep`` (the HM moves). The index returned is
    relative to the first move row, which is what the raw cursor register counts.
    """
    moves = []
    for row, text in rows:
        name = re.sub(r"^[A-Za-z] (?=[A-Z])", "", text.strip()).strip().upper()
        if name in move_ids:
            moves.append((row, name))
    if not moves:
        return None
    first = moves[0][0]
    for row, name in moves:
        if name not in keep:
            return row - first, name
    return None


# Items whose only effect is to raise a stat when used: freeing a slot with one of these costs
# nothing. The names are the cartridge's own (rom_truth item table), matched exactly.
BOOSTERS = ("HP UP", "PROTEIN", "IRON", "CARBOS", "CALCIUM", "RARE CANDY")
SELL_ONLY = ("NUGGET",)


def room_plan(bag_named) -> list[tuple[str, str]]:
    """How to free one bag slot, cheapest loss first. Pure: (action, item name) pairs, in order.

    Measured 2026-09-04: at 20 stacks the Secret House NPC repeated his greeting eleven times and
    HM03 never landed; a tossed NUGGET fixed it on the next talk. Using a consumable is better
    than tossing anything, so stat boosters go first (only upside), then the sell-only stack,
    then the largest multi-item stack (the old measured rule: key items are single-copy), then a
    healing stack, then TMs. HMs and anything not on these lists are never touched.
    """
    names = [(n, q) for n, q in bag_named]
    plan: list[tuple[str, str]] = []
    for n, _q in names:
        if n in BOOSTERS:
            plan.append(("use", n))
    for n, _q in names:
        if n in SELL_ONLY:
            plan.append(("toss", n))
    stacks = sorted(
        ((q, n) for n, q in names if q > 1 and not n.startswith(("HM", "TM")) and not is_kit(n)), reverse=True
    )
    plan.extend(("toss", n) for _q, n in stacks)
    for n, _q in names:
        if n.startswith("TM"):
            plan.append(("toss", n))
    # The kit goes last, and only its medicine: measured 2026-09-04 at Cinnabar's mart, the old
    # "largest stack" rule tossed the 31 ULTRA BALLs bought for the League and then the HYPER POTIONs,
    # to make room for MAX REPELs. Balls are never tossed by this plan.
    for n, _q in names:
        if ("POTION" in n or "ETHER" in n or "ELIXER" in n) and ("toss", n) not in plan:
            plan.append(("toss", n))
    return plan


KIT_WORDS = ("BALL", "POTION", "REPEL", "HEAL", "REVIVE", "ETHER", "ELIXER", "RESTORE", "ESCAPE ROPE")


def is_kit(name: str) -> bool:
    """Is this bag entry part of the travelling kit (balls, medicine, repels, the rope)? Those are the
    stacks a leg bought on purpose; the room plan tosses TMs before them and balls never."""
    n = name.upper()
    return any(w in n for w in KIT_WORDS)


def storage_plan(bag_named, keep=()) -> list[str]:
    """What to leave in the Center's PC to free bag slots, cheapest loss first. Pure: item names, in order.

    The game has an item storage in every Center, so a full bag need not lose anything: measured
    2026-09-04 at Cinnabar, the lab said "Your pack is crammed full!" over the OLD AMBER and the old
    room plan's answer was to toss TM27 FISSURE. Storage keeps it. TMs go first (single copies, only
    ever spent once), then every other single-copy item that is not an HM; the kit and anything in
    ``keep`` (the item the leg is carrying somewhere) stay in the bag. Whether the game accepts a
    key item into storage is the PC's verdict, not this list's.
    """
    keep_up = {k.upper() for k in keep}
    names = [(n, q) for n, q in bag_named if n.upper() not in keep_up and not n.startswith("HM") and not is_kit(n)]
    plan = [n for n, _q in names if n.startswith("TM")]
    plan += [n for n, q in names if q == 1 and n not in plan]
    return plan


def fly_row_names(row_text: str, town: str) -> bool:
    """Does the town map's top row name ``town``? Measured 2026-09-04 on Route 16: the row decodes
    as ``ToPALLET TOWN`` / ``ToSAFFRON CITY`` (no space after ``To``); DOWN and UP cycle it."""
    t = row_text.strip().upper()
    if t.startswith("TO"):
        t = t[2:].strip()
    return t == town.strip().upper()


def booster_target(party) -> int | None:
    """Which member a stat booster should go to: the lowest-level standing one.

    Measured 2026-09-04: HP UP and CALCIUM 'used' on member 0 (a L100) consumed nothing -- the game
    declines a booster that can do nothing, and the count never dropped. The member with the most
    room to grow is the cheapest place to spend one; the verdict stays the stack count.
    """
    standing = [(lvl, i) for i, (_n, lvl, hp) in enumerate(party) if hp > 0]
    return min(standing)[1] if standing else None


class Rig:
    """A loaded cartridge plus the road engine, recording and emitting as it plays."""

    def __init__(
        self,
        state: str | Path,
        *,
        live_label: str | None = None,
        frame_interval: int = 1,
        viewer_ws: str = VIEWER_WS,
        rom: str | Path = ROM_DEFAULT,
        run_id: str | None = None,
        telemetry_root: Path | None = None,
        settle_on_boot: bool = True,
    ) -> None:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        from agent import PokemonAgent

        self.ag = PokemonAgent(str(rom))
        with open(state, "rb") as fh:
            self.ag.pyboy.load_state(fh)
        self.pb = self.ag.pyboy
        self.mem = self.pb.memory
        self.ctl = self.ag.controller
        self.mr = self.ag.memory
        self.io = qm.EmuIO(self.pb)
        self.truth = rt.load_truth()
        self.pairs = rt.loaded_pairs(self.truth)
        self.ag.catch_wanted = set()  # a leg is travel, not a hunt; the quartermaster arms catching
        self.turn = 0
        self._surfer: str | None = None  # the party member that answered to SURF, once one has
        self._moves: dict[str, int] | None = None  # this cartridge's move-name table, read on demand
        self._said: set[tuple] = set()  # (map, kind, text) already recorded; the sink is a record, not a tape
        self.recorder = None
        self.telemetry_root = telemetry_root
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.unlock_gates()
        if live_label:
            self._go_live(live_label, frame_interval, viewer_ws)
        if settle_on_boot and not self.settle():
            print("  WARNING: the baton would not settle — a textbox is still parking movement", flush=True)

    # ---- wiring ---------------------------------------------------------------------------

    def _go_live(
        self, label: str, frame_interval: int, viewer_ws: str
    ) -> None:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        from game_events import GameEventCollector
        from live_producer import LiveProducer
        from PIL import Image
        from recorder import RunRecorder

        run_id = RunRecorder.new_run_id(datetime.now(timezone.utc), uuid.uuid4().hex[:4])
        self.run_id = run_id
        producer = LiveProducer(f"{viewer_ws}/ws/produce/{run_id}", run_id)
        self.recorder = RunRecorder(
            run_id,
            RUNS_DIR,
            frame_grabber=lambda: Image.fromarray(self.pb.screen.ndarray),
            frame_interval=frame_interval,
            live=producer.send,
        )
        # The same date-partitioned sink the expedition events use (data/telemetry/game/<date>.jsonl):
        # the game-event-bridge container tails it into Kafka, and Flink reads that topic. Without a
        # publisher the fight rows stopped at runs/<run_id>/events.jsonl and never reached the stream.
        from publisher import make_publisher

        sink = (self.telemetry_root or TELEMETRY_DIR).parent / "game"
        self.ag.collector = GameEventCollector(
            publisher=make_publisher(telemetry_dir=str(sink)),
            recorder=self.recorder,
            game=self.ag.profile.name,
            run_id=run_id,
        )
        self.recorder.start({"label": label, "rom": str(ROM_DEFAULT)})

        def wrap(press_fn):
            def press(button, *a, **kw):  # EmuIO.press and GameController.press differ — pass through
                press_fn(button, *a, **kw)
                self.turn += 1
                self.ag.turn_count = self.turn  # events and frames share one clock
                self.recorder.tick(self.turn)

            return press

        self.ctl.press = wrap(self.ctl.press)
        self.io.press = wrap(self.io.press)
        print(f"LIVE RUN {run_id} -> http://127.0.0.1:8201/run/{run_id}", flush=True)

    def emit(self, event: str, **fields) -> dict:
        return emit_event(self.run_id, event, fields, root=self.telemetry_root)

    def finish(self, **summary) -> None:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        if self.recorder is not None:
            summary.setdefault("turns", self.turn)
            summary.setdefault("party", str(self.party()))
            summary.setdefault("pos", str(self.pos()))
            self.recorder.finish(summary)

    # ---- measured reads -------------------------------------------------------------------

    def pos(self) -> tuple[int, int, int]:
        return self.mem[0xD35E], self.mem[0xD362], self.mem[0xD361]

    def settled_pos(self, tries: int = 8) -> tuple[int, int, int]:
        """A position the world agrees with: stable across ticks, and inside the map's own bounds.

        A map transition writes the new map id before the coordinates catch up, so a raw read
        taken inside that window names a tile that cannot exist. Measured twice: a leg announced
        arrival at (234, 17, 11) on a map 16 tiles wide and then banked back on the floor below,
        and a baton banked at (7, 5, 28) booted as (157, 5, 27).
        """
        last = self.pos()
        for _ in range(tries):
            m = self.truth["maps"].get(str(last[0]))
            inside = m is None or (last[1] < m["width"] and last[2] < m["height"])
            self.io.wait(20)
            now = self.pos()
            if now == last and inside:
                return now
            last = now
        return last

    def badges(self) -> int:
        return self.mem[ADDR_BADGES]

    def bag(self) -> list[tuple[int, int]]:
        """The bag as (item id, quantity) pairs — the only honest proof a pickup happened."""
        count = self.mem[ADDR_BAG_COUNT]
        return [(self.mem[ADDR_BAG_ITEMS + 2 * i], self.mem[ADDR_BAG_ITEMS + 2 * i + 1]) for i in range(count)]

    def bag_full(self) -> bool:
        """The bag caps at 20 slots, and a full bag silently refuses pickups.

        Measured in the Rocket Hideout: tossing a whole stack frees a slot, a quantity-1 toss
        does not. A sweep that does not check this reports "collected nothing" and looks like a
        map problem.
        """
        return self.mem[ADDR_BAG_COUNT] >= BAG_SLOTS

    def item_full_name(self, name: str) -> str:
        """``HM03`` -> ``HM03 SURF``. A machine's number does not say what it teaches, and an
        operator asking "what is the item?" deserves the answer the number hides."""
        move = (self.truth.get("machines") or {}).get(name)
        return f"{name} {move}" if move else name

    def item_name(self, item_id: int) -> str:
        """What the cartridge calls this id. TMs/HMs live past the name list and keep their id."""
        return self.truth.get("items", {}).get(str(item_id), f"#{item_id}")

    def bag_named(self, full: bool = False) -> list[tuple[str, int]]:
        """The bag as names. ``full=True`` spells a machine out — "HM03 SURF" — because the
        number alone tells an operator nothing about what the item is."""
        names = [(self.item_name(item), qty) for item, qty in self.bag()]
        return [(self.item_full_name(n), q) for n, q in names] if full else names

    def toss_stack(
        self, item_id: int
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Free a slot by tossing a whole stack: START -> ITEM -> the slot -> TOSS -> all of it.

        Measured in the Rocket Hideout: tossing a *whole stack* frees the slot, a quantity-1 toss
        does not — so callers pick a stack, not an item. Every phase is confirmed from RAM, never
        from timing: the verdict is the bag's slot count dropping.
        """
        before = len(self.bag())
        found = next(((i, q) for i, (item, q) in enumerate(self.bag()) if item == item_id), None)
        if found is None:
            return False
        slot, qty = found
        for _ in range(6):  # never press START onto an already-open menu: close first, then open
            self.ctl.press("b")
            self.ctl.wait(25)
        self.ctl.press("start")
        self.ctl.wait(50)
        for _ in range(8):  # ITEM sits below POKeMON in the field menu; walk the cursor onto it
            if self.mem[qm.ADDR_MENU_CUR] == 2:
                break
            self.ctl.press("down" if self.mem[qm.ADDR_MENU_CUR] < 2 else "up")
            self.ctl.wait(20)
        self.ctl.press("a")
        self.ctl.wait(60)
        # The item list shows three rows at a time: 0xCC26 is the cursor *within that window* and
        # caps at 2, while 0xCC36 is the scroll offset. The slot we want is their sum. Comparing
        # the cursor alone to the slot index silently stops on slot 2 and tosses the wrong thing —
        # or, here, nothing at all.
        for _ in range(2 * (slot + len(self.bag()) + 4)):
            here = self.mem[ADDR_LIST_SCROLL] + self.mem[qm.ADDR_MENU_CUR]
            if here == slot:
                break
            self.ctl.press("down" if here < slot else "up")
            self.ctl.wait(20)
        if self.mem[ADDR_LIST_SCROLL] + self.mem[qm.ADDR_MENU_CUR] != slot:
            for _ in range(6):
                self.ctl.press("b")
                self.ctl.wait(25)
            return False
        self.ctl.press("a")
        self.ctl.wait(60)
        for _ in range(6):  # the item submenu: USE / TOSS — TOSS is the lower row
            if self.mem[qm.ADDR_MENU_CUR] == 1:
                break
            self.ctl.press("down")
            self.ctl.wait(20)
        self.ctl.press("a")
        self.ctl.wait(60)
        # The quantity picker starts at 1 and WRAPS. Holding up a fixed number of times is how
        # you ask for the whole stack and get one unit instead: twelve presses on a six-stack
        # lands back on 1, and a quantity-1 toss frees no slot — the very thing this method
        # exists to avoid. Press exactly what the stack holds.
        for _ in range(max(0, qty - 1)):
            self.ctl.press("up")
            self.ctl.wait(20)
        # The confirm phase is predicate-driven, not timed. `quartermaster` learned this on the
        # mart counter — "the shop dialog cadence swallowing fixed-timing scripts", a purchase
        # that looked confirmed two A-presses before the money moved — and this method ignored
        # it. The identical sequence tossed a stack at 60-frame waits and silently did nothing at
        # 45, which reads as "the game would not part with it" and is really "we stopped asking".
        # The bag is the predicate: press A until a slot frees or the strikes run out.
        for _ in range(8):
            if len(self.bag()) < before:
                break
            self.ctl.press("a")
            self.ctl.wait(60)
        for _ in range(6):
            self.ctl.press("b")
            self.ctl.wait(30)
        return len(self.bag()) < before

    def make_room(self, allow_toss: bool = False) -> bool:
        """Free one bag slot along ``room_plan``: use what only helps; toss only when told to.

        The caller decides when (``bag_full``); this frees exactly one slot. The verdict is the
        stack count dropping -- never the menu having been navigated. A use the game refuses
        ("It won't have any effect") leaves the count alone and the plan moves on; so does a toss
        the game refuses (a key item), which is the backstop against lore about what is safe.

        Tossing is off by default: the 2026-09-05 replays threw away NUGGETs and TMs on bag-full
        talks. The game's own answer to a full bag is the Center PC (``store_at_pc``); a leg that
        cannot reach one says so (``bag.full``) rather than selling the bag one item at a time.
        """
        before = len(self.bag())
        # room_plan sees the FULL names ("TM28 DIG"); the id lookup must use the same names, or every
        # TM/HM entry is skipped -- measured on the Safari leg: HP UP, CALCIUM, then nothing, twenty talks.
        named = self.bag_named(full=True)
        by_name = {name: item for (name, _q), (item, _q2) in zip(named, self.bag())}
        can_use = hasattr(self, "use_item") and hasattr(self, "ctl")
        for action, name in room_plan(named):
            if name not in by_name or (action == "use" and not can_use):
                continue
            if action == "toss" and not allow_toss:
                continue
            print(f"  bag full: {action} {name} to free a slot", flush=True)
            if action == "use":
                if self.use_item(name):
                    try:
                        target = booster_target(self.party())
                    except (KeyError, AttributeError, TypeError):  # a rig without a party table
                        target = None
                    if target is not None and hasattr(self, "cursor_to"):
                        self.ctl.wait(40)  # the party roster draws; put the highlight on the member
                        self.cursor_to(target)
                    for _ in range(6):  # pick the member, then the "went up" pages
                        self.ctl.press("a")
                        self.ctl.wait(40)
                for _ in range(6):  # and back out of a refusal
                    self.ctl.press("b")
                    self.ctl.wait(25)
            else:
                freed = self.toss_stack(by_name[name])  # measured in the Hideout: a whole stack frees the slot
                if freed:
                    before = len(self.bag()) + 1
            if len(self.bag()) < before:
                if hasattr(self, "emit"):
                    self.emit("bag.freed", action=action, item=name, slots=len(self.bag()))
                return True
        print("  bag full: nothing to use; store at a Center PC (store_at_pc) rather than toss", flush=True)
        if hasattr(self, "emit"):
            self.emit("bag.full", slots=len(self.bag()), hint="store_at_pc")
        print("  bag is full and nothing in it is expendable", flush=True)
        return False

    def item_balls(self, map_id: int) -> list[tuple[int, int]]:
        """Where this cartridge says the item balls are on a map — extracted, never recalled."""
        sprites = self.truth["maps"].get(str(map_id), {}).get("sprites", [])
        return [(s["x"], s["y"]) for s in sprites if s.get("kind") == "item"]

    def ball_contents(self, map_id: int) -> dict[tuple[int, int], str]:
        """``(x, y) -> item name`` for this map's balls, from the object data's item byte.

        A ball's contents are in the cartridge, so "where is the CARD KEY" is a lookup rather
        than a building-wide sweep — the hunt that cost two sessions. Cross-checked against the
        Rocket Hideout, whose two balls extract as SILPH SCOPE and LIFT KEY, both of which this
        run picked up live.
        """
        sprites = self.truth["maps"].get(str(map_id), {}).get("sprites", [])
        items = self.truth.get("items", {})
        return {
            (s["x"], s["y"]): items.get(str(s.get("item")), f"item {s.get('item')}")
            for s in sprites
            if s.get("kind") == "item"
        }

    def collect_item(
        self, bx: int, by: int
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Pick up one item ball: stand beside it, face it, press A. Bag growth is the verdict.

        Item-ball sprites can be invisible and walk-through-able — the Rocket Hideout's LIFT KEY
        was listed in the live sprite table the whole time while the engine let us walk over its
        tile. So the approach is a cell *beside* the ball, never the ball itself, and the pickup
        is confirmed from the bag rather than from anything on screen.
        """
        mp, x, y = self.pos()
        if self.bag_full() and not self.make_room():
            print(f"  bag is full ({BAG_SLOTS} slots) and no slot could be freed", flush=True)
            return False
        adjacent = {(bx + 1, by), (bx - 1, by), (bx, by + 1), (bx, by - 1)}
        if (x, y) not in adjacent:
            near = road.walkable(self.truth, self.pairs, mp, (x, y), self.bodies() - {(bx, by)}) & adjacent
            if not self.approach(near or adjacent):  # no walk reaches it: let a ride try
                return False
            mp, x, y = self.pos()
            if (x, y) not in adjacent:
                return False
        before = self.bag()
        self.ctl.press("right" if bx > x else "left" if bx < x else "down" if by > y else "up")
        self.ctl.wait(25)
        for _ in range(4):
            self.ctl.press("a")
            self.ctl.wait(45)
        for _ in range(3):
            self.ctl.press("b")
            self.ctl.wait(25)
        if self.bag() == before:
            return False
        self.unlock_gates()  # a key just picked up unlocks its doors for the rest of this leg
        return True

    def party(self) -> list[tuple[str, int, int]]:
        from memory_reader import SPECIES_ID_MAP

        base = ADDR_PARTY_STRUCTS
        return [
            (
                SPECIES_ID_MAP.get(self.mem[base + PARTY_STRUCT_SIZE * i], "?"),
                self.mem[base + PARTY_STRUCT_SIZE * i + 33],
                (self.mem[base + PARTY_STRUCT_SIZE * i + 1] << 8) | self.mem[base + PARTY_STRUCT_SIZE * i + 2],
            )
            for i in range(self.mem[ADDR_PARTY_COUNT])
        ]

    def dialogue(self) -> str:
        try:
            return self.mr.read_dialogue().strip()
        except Exception:  # a text buffer mid-redraw is not a leg failure
            return ""

    # ---- moving ---------------------------------------------------------------------------

    def warp_tiles(self, map_id: int) -> set[tuple[int, int]]:
        return {(w[0], w[1]) for w in self.truth["maps"].get(str(map_id), {}).get("warps", [])}

    def probe_step(self) -> bool:
        """One step and its undo — the only honest proof the world is accepting input.

        A textbox does not always leave text in the buffer (the buffer stays *stale* after boxes
        close, measured), so "is there dialogue" cannot answer "can we move". Actually moving can.

        A door is not a floor. The badge-6 leg booted a baton standing one tile below Fuchsia
        gym's mat, probed *up* onto it, and warped straight back into the gym it had just left —
        the same doctrine ``road.walk(avoid_warps=True)`` already follows, missing here. Warp
        neighbours are the last resort, and only because a state wedged in a doorway still has to
        be able to prove it accepts input.
        """
        mp, x, y = self.pos()
        warps = self.warp_tiles(mp)
        deltas = {"down": (0, 1), "up": (0, -1), "left": (-1, 0), "right": (1, 0)}
        order = [("down", "up"), ("up", "down"), ("left", "right"), ("right", "left")]
        floors = [(d, b) for d, b in order if (x + deltas[d][0], y + deltas[d][1]) not in warps]
        for direction, back in floors + [p for p in order if p not in floors]:
            before = self.pos()
            self.io.press(direction, hold=8, release=8)
            self.io.wait(30)
            after = self.pos()
            if after != before:
                if after[0] == before[0]:
                    self.io.press(back, hold=8, release=8)
                    self.io.wait(30)
                else:
                    # Every plain floor tile refused, so the last resort was a warp -- and it
                    # moved us. That is not a bug by itself (a real doorway alcove can be walled
                    # on three sides), but it is invisible unless said out loud: a baton banked
                    # right inside an entrance can silently step back out on its very first
                    # probe, and a caller checking only the boolean sees "input works" with no
                    # sign the map underneath just changed. Measured today: door_check.state,
                    # banked at Seafoam's own entrance (192, 4, 17), landed back on map 31 the
                    # next time anything called settle() on it.
                    print(f"  probe_step warped map {before[0]} -> {after[0]} to prove input", flush=True)
                return True
        return False

    def text_from(self, action) -> str:
        """Run ``action`` and return only text that appeared *because of it*.

        Every screen-derived signal here is sticky — the dialogue buffer, the window tilemap and
        the text-id register all keep their last contents until something overwrites them, and
        none is cleared when a box closes. Reading one raw is how 54 ordinary walls came to be
        labelled doors, all quoting a battle three minutes old. Routing every read through this
        makes "what did that do?" the only question anyone can ask of the screen.
        """
        baseline = self.dialogue()
        action()
        said = self.dialogue()
        return "" if said == baseline else said

    def flush_text(self, tries: int = 6) -> bool:
        """Close whatever box is on screen, so the next message is unambiguously the next message.

        .. warning::
           Every screen-derived signal on this cartridge is sticky. The dialogue buffer, the
           window tilemap and the text-id register at 0xD125 all keep their last contents until
           something overwrites them, and none of them is cleared when a box closes. A baton was
           diagnosed as "banked with the START menu open" on the strength of 0xD125 == 13 and a
           window layer still showing POKeDEX/ITEM/SAVE — and then a plain step moved the player
           one tile, proving no menu was open at all. Trust position, bag and badges; treat
           anything read off the screen as a hint that needs corroborating.

        Comparing the buffer before and after a step is not enough on its own: a snapshot taken
        while a box was up *contains* that box, so loading it restores the stale line and the
        comparison sees no change. That is how a survey of map 208 came back with zero doors on a
        floor whose very first westward step prints "Darn! It needs a CARD KEY!". Clear, then read.
        """
        for _ in range(tries):
            if not self.dialogue():
                return True
            self.ctl.press("b")
            self.ctl.wait(30)
        return not self.dialogue()

    def settle(self, max_rounds: int = 16) -> bool:
        """Flush a parked textbox so a baton can move.

        A state banked mid-dialogue cannot walk: every direction is swallowed while the box is
        up. Measured on ``BADGE5.state`` — banked on Koga's TM line ("Make space for this,
        child!"), it refused all four steps, and a leg booted from it fingerprinted a wall that
        was never in the world. The recovery is the measured one: A advances the pages, B closes
        whatever A opened, and a probe step is the proof.
        """
        for _ in range(max_rounds):
            if self.mem[qm.ADDR_IN_BATTLE]:
                self.battle()
                continue
            if self.probe_step():
                return True
            # B before A. A *commits*, and on the field menu committing opens a submenu — a
            # settle that leads with A can open the very thing it is trying to clear, which is
            # how a baton came to be banked with the START menu up and the cursor sitting on
            # ITEM, breaking every menu flow that booted from it. B closes; A is only for
            # advancing a box that B will not dismiss.
            self.ctl.press("b")
            self.ctl.wait(30)
            if self.probe_step():
                return True
            # Turn away from any body before pressing A. Facing an npc, A does not advance the
            # box — it *re-opens the conversation*, so the settle can never finish: measured on
            # the SECRET HOUSE baton, banked at (3,4) looking straight at the man who had just
            # handed over HM03, where sixteen rounds of A restarted his speech sixteen times.
            self._face_away_from_bodies()
            self.ctl.press("a")
            self.ctl.wait(40)
        return self.probe_step()

    def _face_away_from_bodies(self) -> None:  # pragma: no cover - drives the emulator
        """Turn toward a neighbour with nobody on it, so A advances text instead of starting it."""
        mp, x, y = self.pos()
        m = self.truth["maps"].get(str(mp)) or {}
        grid = m.get("grid")
        if not grid:
            return  # a map we do not model: turning blind is worse than not turning
        bodies = self.bodies()
        for direction, (dx, dy) in (("down", (0, 1)), ("left", (-1, 0)), ("right", (1, 0)), ("up", (0, -1))):
            nx, ny = x + dx, y + dy
            if not (0 <= ny < len(grid) and 0 <= nx < len(grid[ny])):
                continue
            if (nx, ny) in bodies or grid[ny][nx] != "1":
                continue
            self.ctl.press(direction)
            self.ctl.wait(20)
            return

    def battle(self, io=None) -> None:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """The agent's full battle turn until the fight ends; a stuck fight is a wedge."""
        self.ag._catch_enemy = None
        self.ag._catch_throws = 0
        # The same start-of-battle snapshot the standalone agent takes, so the fight ends with a
        # battle_outcome row (species, levels, HP, move types, result) in runs/<run_id>/events.jsonl.
        # Without it every supervisor-driven fight since 2026-08-27 left only per-turn rows.
        # Measured: the flag flips before the battle struct is loaded, so a snapshot taken at
        # once carries the previous battler (a L20 Gyarados logged as L100 with Charizard's HP).
        # The struct is complete once FIGHT is drawn, which is what _await_battle_menu syncs on.
        # And on a fight a trainer starts on approach (Lance, measured), the in-battle flag is up
        # before the battle-type byte, so the first sync fails; the attempt repeats each turn until
        # the struct is there, rather than dropping the whole fight's row.
        turns = 0
        no_fight = 0  # consecutive turns on a menu the routine cannot drive
        while self.mem[qm.ADDR_IN_BATTLE] and turns < BATTLE_TURN_CAP:
            if not self.ag._pre_battle_species and self.ag._await_battle_menu():
                self.ag.snapshot_battle_start(self.mr.read_battle_state())
            self.ag.run_battle_turn()
            turns += 1
            # The fight routine leaves a special (no-FIGHT) menu exactly where it found it: after a
            # turn it has driven, if no FIGHT is drawn anywhere on the menu it could not reach one,
            # and we have seen that repeatedly, the wild is fleeing away rather than spurning for the
            # cap. RUN sits bottom-right of every 2x2 battle menu, so a directed press there ends it.
            if not self._fightable():
                no_fight += 1
            else:
                no_fight = 0
            if no_fight >= SPECIAL_MENU_GRACE and self._flee_special_menu():
                self.emit("battle.fled", pos=list(self.pos()), turns=turns)
                self._battle_summary(won=False, turns=turns)
                return
            if turns in (60, 110, 160):
                self.ag._recover_battle_wedge()
        if self.mem[qm.ADDR_IN_BATTLE]:
            self.bank("wedge")
            self.emit("battle.wedge", pos=list(self.pos()), turns=turns)
            raise BattleWedge(f"battle did not end in {turns} turns; banked wedge.state")
        self._battle_summary(won=not self.mr.player_whited_out(), turns=turns)

    def _battle_summary(self, won: bool, turns: int) -> None:  # pragma: no cover - drives the emulator
        """Close the fight on the collector; a fight that was never snapshotted has no row."""
        if not self.ag._pre_battle_species:
            return
        disposition = self.ag.emit_battle_summary(won, turns)
        self.emit("battle.outcome", won=won, turns=turns, disposition=disposition)

    def _fightable(self, probes: int = 8) -> bool:  # pragma: no cover - drives the emulator
        """B through intro/dialog (B is a no-op on a menu, A on the evolution screen); report
        whether a standard menu — one with a FIGHT option — is up. False means a special menu the
        fight routine cannot select from (the Safari Zone's), so a wild is better fled than fought."""
        for _ in range(probes):
            if not self.mem[qm.ADDR_IN_BATTLE]:
                return True
            if self.mr.battle_menu_visible():
                return True
            self.ag._press_b_unless_evolving()
            self.ctl.wait(12)
        return self.mr.battle_menu_visible()

    def _flee_special_menu(self) -> bool:  # pragma: no cover - drives the emulator
        """Flee a no-FIGHT menu: normalise the cursor to top-left then step to bottom-right (RUN,
        on every 2x2 battle menu) and confirm; dismiss the 'you fled' text. Returns whether the
        battle actually ended."""
        for _ in range(4):
            if not self.mem[qm.ADDR_IN_BATTLE]:
                return True
            self.ctl.press("b")
            self.ctl.wait(16)
            self.ctl.press("up")
            self.ctl.wait(12)
            self.ctl.press("left")
            self.ctl.wait(12)
            self.ctl.press("down")
            self.ctl.wait(12)
            self.ctl.press("right")
            self.ctl.wait(12)
            self.ctl.press("a")
            self.ctl.wait(70)
            for _ in range(10):
                if not self.mem[qm.ADDR_IN_BATTLE]:
                    return True
                self.ctl.press("a")
                self.ctl.wait(35)
        return not self.mem[qm.ADDR_IN_BATTLE]

    def walk(self, map_id: int, targets, **kw):
        kw.setdefault("battle", self.battle)
        return road.walk(self.io, self.truth, self.pairs, map_id, targets, **kw)

    def drive(self, dst: int, **kw):
        kw.setdefault("battle", self.battle)
        kw.setdefault("log", lambda msg: print("  " + msg, flush=True))
        return road.drive_to(self.io, self.truth, self.pairs, dst, **kw)

    def warp(self, map_id: int, wx: int, wy: int, **kw):
        kw.setdefault("battle", self.battle)
        return road.through_warp(self.io, self.truth, self.pairs, map_id, wx, wy, **kw)

    def cross(self, cur: int, nxt: int, **kw):
        """One map connection. A land edge takes the A* path; a water edge has no floor to plan
        over, so that cross reports stuck-on-edge (or no-path) and is then run straight in its
        connection direction, arming SURF on the refusal (the water route is open water plus a
        walkable plaza; SURF carries the water, walking the plaza, and the arm glues the two)."""
        kw.setdefault("battle", self.battle)
        res = road.cross_edge(self.io, self.truth, self.pairs, cur, nxt, **kw)
        if res in ("stuck-on-edge", "no-path"):
            return self._surf_or_fail(cur, nxt, res, kw.get("battle"))
        return res

    def _surf_or_fail(self, cur: int, nxt: int, res: str, battle):
        """Surf the failed cross, but only if its near edge is genuinely water (modelled, that
        connection, and no land cell to stand on). Surfing a real land block would misroute, so a
        water verdict needs the map to say so; otherwise the land cross's failure stands."""
        try:
            cells, _d = road.edge_cells(self.truth, cur, nxt)
        except (KeyError, StopIteration, IndexError):
            return res  # the connection isn't modelled; "water" was a guess, keep the real failure
        if cells:  # land on the near edge: a genuine block, not water
            return res
        return road.surf_cross(self.io, self.truth, self.pairs, cur, nxt, arm_surf=self._arm_surf, battle=battle)

    # Measured: with the roster drawn on rows 0-11, the refusal "No SURFing on / GYARADOS here!"
    # rendered on rows 12 and 13, so the game's text box lives in the bottom of the 18-row window.
    TEXTBOX_ROWS = range(12, 18)

    def textbox(self) -> str:
        """What the game is currently saying, as one line. The screen is the instruction stream."""
        rows = [self.window_row(i).strip() for i in self.TEXTBOX_ROWS]
        return " ".join(t for t in rows if t)

    def knows_move(self, name: str, species: str | None = None) -> int | None:
        """Party index of the first *standing* member that knows ``name`` — from RAM, by move id.

        Pass ``species=`` to ask about one member specifically, without the menu: the badge-7
        crossing walked all five non-surfers through the POKeMON submenu before it reached
        Gyarados, and that fumbling is what hid the cursor-spliced "SURF" row. A RAM read is free,
        so the members that cannot be arming candidates are filtered before any menu is opened.
        This replaces a species literal in the engine (``if lead in ("Gyarados", ...)``), which is
        the same class of mistake as ``cut_facing``'s "CUT is row 0": true of one party on one
        leg, silently wrong on the next. Nothing here is recalled. The move ids come from this
        cartridge's own name list (`rom_truth.move_names`, the table that named HM03 SURF), and
        the struct offsets were measured rather than remembered: with SURF=57 and CUT=15 looked
        up first, SURF appears in Gyarados' struct at offset 11 and CUT in Charizard's at 8, and
        nowhere else in any of the six — two independently known facts landing inside the same
        four-byte window, which is what fixes the window at 8..11.

        Fainted members are skipped: Gen 1 omits them from the POKeMON menu, so a move they know
        cannot be selected and reporting them here would hand back an unusable index.
        """
        want = self._move_ids().get(name.strip().upper())
        if want is None:
            return None
        for i, (_n, _lvl, hp) in enumerate(self.party()):
            if hp <= 0:
                continue
            if species is not None and _n.strip().upper() != species.strip().upper():
                continue
            base = ADDR_PARTY_STRUCTS + PARTY_STRUCT_SIZE * i
            if want in (self.mem[base + off] for off in MOVE_SLOTS):
                return i
        return None

    def _move_ids(self) -> dict[str, int]:
        """``{MOVE NAME: id}`` for this cartridge, read once per rig."""
        if self._moves is None:
            self._moves = {v.strip().upper(): int(k) for k, v in rt.move_names(rt.ROM_DEFAULT.read_bytes()).items()}
        return self._moves

    def surf_facing(self, face: str | None = None) -> None:
        """Arm SURF on the lead (member 0) with a fixed key sequence — no window/row read.

        The flaky path this replaces, ``use_field_move`` -> ``menu_row_of``, seats the cursor off
        the sticky window-layer text and returned ``None`` on a clean baton. But the two facts that
        matter here are measured, not recalled: Gyarados (species 22) sits at party index 0, so it
        is row 0 of the POKeMON roster; and the lead's field submenu is SURF -> STATS -> SWITCH ->
        CANCEL, so surf is row 0 (``_select_move``'s "wrap with up" note). Surf is thus the TOP of
        both menus, so the whole arm is a fixed keystroke — START, POKeMON (row 1, the only
        navigation, read off the trustworthy ADDR_MENU_CUR), then three A's, with an up-wrap before
        each to seat row 0 defensively regardless of where the cursor opened. The step onto water
        immediately after (``surf_cross``) is the real predicate: a move to water means SURF armed;
        a refused step means it did not.

        ``face`` is the water's bearing for a standalone arm; on the crossing path the player is
        already facing water (the refused step set it), so pass nothing.
        """
        c = self.ctl
        for _ in range(5):  # close whatever is open before opening ours
            c.press("b")
            c.wait(25)
        if face:
            c.press(face)
            c.wait(25)
        c.press("start")
        c.wait(50)
        for _ in range(8):  # POKeDEX is row 0; POKeMON is row 1
            if self.mem[qm.ADDR_MENU_CUR] == 1:
                break
            c.press("down" if self.mem[qm.ADDR_MENU_CUR] < 1 else "up")
            c.wait(20)
        c.press("a")  # the roster opens
        c.wait(60)
        # Seat row 0 by cursor+scroll, never by a blind up: Gen 1 wraps, so up from row 0 lands
        # on the LAST member (measured on this baton it walked into Charizard and CUT, "There
        # isn't anything to CUT!"). menu_cursor_to walks down-from-above only and never wraps.
        self.menu_cursor_to(0)
        c.press("a")  # the lead's field submenu opens
        c.wait(60)
        self.menu_cursor_to(0)  # top of the field list = SURF; the same wrap guard
        c.press("a")  # SURF
        c.wait(60)

    def _face_water(self) -> None:
        """Turn to face a water model (0x11/0x14) so the SURF activation has a tile to land on.

        Measured on map 30: the identical arm from (4,9) fails while facing the solid western
        tile (0x3a) — the player stays put — and succeeds while facing the water tile to the
        south (0x14): the activation animates the player onto the tile they are facing. The
        model is only an offer of which direction to face; a model-land direction is not
        pressed (that would step into the island), and the game's own answer remains the
        authority either way.
        """
        mp, x, y = self.pos()
        truth = getattr(self, "truth", None)  # a Rig built by __new__ (fakes, probes) has none
        m = truth["maps"].get(str(mp)) if truth else None
        if not m or not m.get("tiles"):
            return
        for dx, dy, face in ((0, 1, "down"), (0, -1, "up"), (1, 0, "right"), (-1, 0, "left")):
            if road._water_model(m, x + dx, y + dy):
                self.ctl.press(face)
                self.ctl.wait(25)
                return

    def _arm_surf(self) -> bool:
        """Arm SURF on whoever actually knows it — the measured lead first, then by species.

        The old path assumed "Surf is the lead's job (member 0)" and then read the species off the
        sticky window (``menu_row_of``), which failed on a clean baton and left the crossing
        stuck-on-edge. Two measured facts make the lead path safe: Gyarados (species 22) is at
        party index 0, and its field submenu opens on SURF. So when the lead is that surfer we arm
        it with a fixed key sequence (``surf_facing``) — no window read — and let the following
        water step prove it. Otherwise we fall back to the species scan (a live member that knows
        SURF, remembered first).

        The faint-omission hazard that forced this design still stands: a fainted member is not
        drawn on the POKeMON menu, so a 0 HP surfer cannot be armed at all — the protect rule in
        ``BattleStrategy`` exists precisely to keep the lead's surfer alive across the crossings.

        **The return value is measured, not assumed, and this is the whole point.** It used to
        report success the moment the keystrokes had been sent. Measured on
        ``b8_BATON_island_gyarados_safe.state`` at map 30 (6,9): the keys go in, the game answers
        **"No SURFing on GYARADOS here!"**, the refusal text box swallows every subsequent input
        (``probe_step()`` is False in all four directions), and the old code returned True. Three
        legs then read "nothing moves anywhere" as a water/rock maze and as unreliable position
        tracking, and wrote both up as world facts. They were neither: they were this boolean
        lying.

        Using SURF moves the player onto the water as part of using it, so the honest predicate is
        the one ``surf_onto`` already documents — the position, never the menu. The text is
        cleared first, because a refusal left up is what made the world look frozen.
        """
        holder = self.knows_move("SURF")
        if holder is None:
            return False
        # Measured on map 30: the identical arm from (4,9) fails when the player faces the
        # solid west tile (0x3a) and lands at (4,10) — on the water — when they face it from the
        # south (0x14). The activation animates the player onto the tile they are facing, so the
        # facing must be a water cell in the model before the arm is attempted. Pressing a
        # direction onto water is a refusal (water is not walkable), so the move is free and
        # only the facing changes.
        self._face_water()
        before = self.pos()
        if holder == 0:  # the lead: the fixed key sequence, which reads no window text
            self.surf_facing()
        elif not (self._surfer and self.use_field_move("SURF", species=self._surfer)):
            for name, _lvl, hp in self.party():
                if hp <= 0 or name == self._surfer:
                    continue
                if not self.knows_move("SURF", name):  # a RAM read, so no member is walked through
                    continue
                if self.use_field_move("SURF", species=name):
                    break
        said = self.textbox()
        # Measured on Route 23 (2026-09-05): "AAAA got on GYARADOS!" types out over the party menu
        # and the position updates only when that page closes -- read at once, a mount that worked
        # looked refused (four legs, one screenshot each, the player already on the water in all).
        # So the verdict waits for the page: A advances it; the position is the proof either way.
        for _ in range(8):
            if self.pos() != before:
                break
            self.ctl.press("a")
            self.ctl.wait(45)
        refused = self.pos() == before  # settled before the clear loop; pressing B never moves us
        if refused:
            self.screenshot("surf_refused")  # the picture, not just the sentence -- see screenshot()
        for _ in range(6):  # a refusal left on screen freezes every later step
            self.ctl.press("b")
            self.ctl.wait(30)
        if refused:
            if said:
                self.say(said, "surf.refused")  # the sentence is the evidence, so it lands in the sink
            return False
        self._surfer = self.party()[holder][0]
        return True

    def approach(self, cells) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Get onto one of ``cells`` on this map. Walk first; on a facility floor, use the oracle.

        Silph's top floor refused every planned step: `walk` reported "refused" from (10,9) to a
        cell four tiles away that the grid says is plainly connected, because tileset 22's tiles
        decide where you end up. Planning a path there is the same category error that held
        Rocket Hideout B4 — so the fallback is the facing-keyed oracle, which is the engine's own
        answer for these floors and is already what gets legs *onto* them.
        """
        cells = set(cells)
        mp, x, y = self.pos()
        if (x, y) in cells:
            return True
        self.walk(mp, cells, cap=400)
        here = self.pos()
        if here[0] == mp and here[1:] in cells:
            return True
        # A region whose only door is a pad is invisible to both the walk and the oracle, because
        # both plan over tiles and a pad is a tile you cannot stand on by planning. Ride it.
        # Measured on Silph 5F: the card-key corridor is unreachable on foot from every cell on
        # the floor and one step from the pad at (27,3), and three legs died on that difference.
        # Only on a facility floor. A "warp" on a city map is a building's front door, not a
        # teleport pad: riding Saffron's Silph entrance walks into the lobby and back out, and a
        # leg trying to reach the gym past its guard did that eighty times before the hop cap
        # stopped it.
        facility = self.truth["maps"].get(str(mp), {}).get("tileset") == road.FACILITY_TILESET
        if facility and road.ride_pad(self.io, self.truth, self.pairs, mp, cells, battle=self.battle):
            return True
        here = self.settled_pos()
        if here[0] == mp and here[1:] in cells:
            return True
        if here[0] != mp:  # a ride left us on another floor; the caller's map is no longer ours
            return False
        if facility:
            self.oracle_goto(lambda p: p[0] == mp and (p[1], p[2]) in cells)
        here = self.pos()
        return here[0] == mp and here[1:] in cells

    def traverse(self, interior: int, **kw):
        """Leave a swallowed-hop interior by the mats on another side (a gate room, a house)."""
        kw.setdefault("battle", self.battle)
        return road.traverse_interior(self.io, self.truth, self.pairs, interior, **kw)

    def gate(self, cur: int, goal_cells, **kw):
        """Cross a route severed by its own gate building, validating each candidate door."""
        kw.setdefault("battle", self.battle)
        return road.pass_gate(self.io, self.truth, self.pairs, cur, goal_cells, **kw)

    def bodies(self) -> set[tuple[int, int]]:
        """Live sprites, clipped to this map. Unused sprite slots decode to off-map coordinates,
        and an off-map "blocker" is one a leg will walk across the floor to argue with."""
        m = self.truth["maps"].get(str(self.pos()[0]))
        return road.live_bodies(self.io, (m["width"], m["height"]) if m else None)

    def boulders(self) -> set[tuple[int, int]]:
        """Live cells of the sprites the cartridge draws as boulders (pic 63): a pushed boulder
        keeps its slot, so slot ``i`` is matched to the map's sprite ``i - 1``."""
        m = self.truth["maps"].get(str(self.pos()[0]))
        if not m:
            return set()
        sprites = m.get("sprites") or []
        live = road.live_sprites(self.io, (m["width"], m["height"]))
        return {
            xy
            for slot, xy in live.items()
            if slot - 1 < len(sprites) and sprites[slot - 1].get("pic") == road.BOULDER_PIC
        }

    def push_boulder(self, stand, face: str) -> bool:  # pragma: no cover - drives the emulator
        """Stand beside a boulder and shove it with STRENGTH; the sprite table is the verdict.

        Measured on Victory Road 1F (2026-09-05): a wild Onix opened on the walk to the stand and
        the push that followed was refused twice -- the win box was still up. So the walk is
        settled, the stand re-checked, and the shove tried twice.
        """
        stand = tuple(stand)
        for _ in range(2):
            if self.pos()[1:] != stand and not self.approach({stand}):
                return False
            self.settle()
            if self.pos()[1:] != stand:
                continue
            if self.strength_push(face):
                return True
            self.settle()
        return False

    def surf_to(self, targets) -> bool | str:  # pragma: no cover - drives the emulator
        """SURF across this map's water to the land that reaches ``targets`` (``road.surf_route``)."""
        return road.surf_route(
            self.io,
            self.truth,
            self.pairs,
            self.pos()[0],
            set(targets),
            mount=self.surf_onto,  # measured to answer by position, which the menu read cannot
            battle=self.battle,
            bodies=self.bodies(),
            log=lambda msg: print(msg, flush=True),
            dismiss=self._turn_pages,
        )

    def _turn_pages(self, rounds: int = 12) -> None:  # pragma: no cover - drives the emulator
        """A while the game is saying something: a guard's badge check, a "got on" line. Stops
        the moment the window is clear; the stale-window case just costs a few harmless A's."""
        for _ in range(rounds):
            if not self.textbox():
                return
            self.ctl.press("a")
            self.ctl.wait(40)

    def screenshot_path(self, tag: str) -> Path:
        """Where a tagged screen grab for this run lives. Pure — no PyBoy, so it is testable."""
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", tag).strip("_") or "screen"
        root = (self.telemetry_root or TELEMETRY_DIR).parent / "screens" / self.run_id
        return root / f"{safe}.png"

    def screenshot(self, tag: str) -> str:  # pragma: no cover - drives the emulator; verified live
        """Save what the screen actually shows, tagged, and record where it landed.

        A refusal's sentence is evidence; today proved the picture is too. Twice today a leg
        called this water "sealed" from the collision grid alone. On screen, the tile it refused
        on was a boulder sitting in open water — not a barrier, something to go around. Every
        genuine stuck moment now gets a picture by default, because "what the model claims" and
        "what the screen shows" have disagreed here more than once, and only one of them is true.
        """
        from PIL import Image

        mp, x, y = self.pos()
        path = self.screenshot_path(tag)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(self.pb.screen.ndarray).save(path)
        self.emit("screenshot", path=str(path), tag=tag, map=mp, x=x, y=y)
        return str(path)

    def say(self, text: str, kind: str = "dialogue") -> None:
        """Record something the game said, where it said it, into the run's event stream.

        The Rig reads dialogue constantly — a guru naming his rod, a boss conceding Silph, a door
        asking for a CARD KEY — and until now it only *printed* it. So the sink could not answer
        "when did the game tell us about X": a search across every captured event for SURF, HM or
        SOULBADGE returned nothing, while all of it had been on screen and in a log file.
        """
        if not (text or "").strip():
            return
        mp, x, y = self.pos()
        # Say each distinct sentence ONCE per map. Measured 2026-09-03: one leg looped on the
        # badge-explainer npc and wrote 1,455,047 discovery events carrying **37 distinct
        # sentences** -- the top five were ~207,000 repeats each -- for a 273 MB day file. A sink
        # that large is not a richer record, it is an unminable one: the crew-vs-solo benchmark
        # reads this file, and every query over it now pays for a quarter-gigabyte of one NPC
        # explaining badges. The dedup key is (map, kind, text), so the same line said on a
        # different map is still news.
        said = getattr(self, "_said", None)
        if said is None:  # a Rig built by __new__ (fakes, probes) still gets the guard
            said = self._said = set()
        seen_key = (mp, kind, text[:300])
        if seen_key in said:
            return
        said.add(seen_key)
        self.emit("discovery", map=mp, x=x, y=y, kind=kind, text=text[:300])

    def talk(self, face: str) -> str:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Face and read: the pages a body gives up. What the game says IS the instruction stream."""
        self.ctl.press(face)
        self.ctl.wait(25)
        self.ctl.press("a")
        pages: list[str] = []
        for _ in range(80):
            self.pb.tick()
            text = self.dialogue()
            if text and (not pages or text != pages[-1]):
                pages.append(text)
        for _ in range(3):
            self.ctl.press("a")
            self.ctl.wait(45)
            text = self.dialogue()
            if text and (not pages or text != pages[-1]):
                pages.append(text)
            if self.mem[qm.ADDR_IN_BATTLE]:
                self.battle()
                break
        for _ in range(4):
            self.ctl.press("b")
            self.ctl.wait(25)
        return " | ".join(pages[-4:])

    # ---- the lift ---------------------------------------------------------------------------

    CURSOR_TILE = 0xED  # the ">" glyph the menu cursor draws in the column it sits on

    def window_row(self, row: int, cursor: bool = False) -> str:
        """One decoded row of the window layer — where menus render (the background stays blank).

        The cursor glyph is blanked unless ``cursor=True``. It renders *into* the row it points
        at, so a row read raw can splice the highlighted entry onto its neighbour: the badge-7 run
        read "AAAAAAAASURF" and matched it as a field move called SURF that the menu did not
        actually offer. Callers that need to know where the highlight sits ask for it explicitly.
        """
        from text_decoder import decode_row

        tm = self.pb.tilemap_window
        tiles = [tm.tile_identifier(x, row) for x in range(20)]
        if not cursor:
            tiles = [0x7F if t == self.CURSOR_TILE else t for t in tiles]
        return decode_row(tiles).strip()

    def elevator_floors(self) -> list[str]:
        """The floor labels the panel is currently showing, top to bottom."""
        return [self.window_row(4 + 2 * i) for i in range(3)]

    def ride_elevator(
        self, floor: str
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Ride a lift car to a named floor, choosing from the panel's own list.

        The car is a small room on tileset 18 whose control panel is a **sign, not an NPC** —
        measured in the Rocket Hideout (panel at (1,1)) and again in Silph Co (panel at (3,0)),
        which is why talking to bodies never found it. The floor list scrolls exactly like the
        ITEM list: ``0xCC26`` is the cursor inside a three-row window and ``0xCC36`` the scroll
        offset. The label under the cursor is *read off the screen* rather than an index being
        assumed — which floor sits at which index is precisely the kind of fact this project has
        been burned by recalling.

        Returns True when the car has left for another map.
        """
        mp, _x, _y = self.pos()
        m = self.truth["maps"].get(str(mp), {})
        signs = m.get("signs") or []
        if not signs:
            print(f"  map {mp} has no sign to use as a lift panel", flush=True)
            return False
        sx, sy = signs[0]
        if not self.approach({(sx, sy + 1)}):
            return False
        self.ctl.press("up")
        self.ctl.wait(25)
        for _ in range(2):  # A opens the panel, A again brings up the floor list
            self.ctl.press("a")
            self.ctl.wait(70)
        target = floor.strip().upper()
        for _ in range(24):
            cursor = self.mem[qm.ADDR_MENU_CUR]
            if self.window_row(4 + 2 * cursor).upper() == target:
                break
            if target in [f.upper() for f in self.elevator_floors()]:
                self.ctl.press("down" if self.window_row(4).upper() != target else "up")
            else:
                self.ctl.press("down")
            self.ctl.wait(20)
        else:
            for _ in range(6):
                self.ctl.press("b")
                self.ctl.wait(25)
            print(f"  the lift panel never showed a floor called {floor!r}", flush=True)
            return False
        for _ in range(3):
            self.ctl.press("a")
            self.ctl.wait(60)
        for _ in range(4):
            self.ctl.press("b")
            self.ctl.wait(30)
        doors = {(w[0], w[1]) for w in m.get("warps", [])}
        if doors:
            self.approach(doors)
            for direction in ("down", "up", "left", "right"):
                self.io.press(direction, hold=8, release=8)
                self.io.wait(60)
                if self.pos()[0] != mp:
                    return True
        return self.pos()[0] != mp

    # ---- field moves ------------------------------------------------------------------------

    def field_moves(self) -> list[str]:
        """The field submenu's entries, decoded from the window layer, top to bottom.

        Read without the cursor glyph: with it, the highlighted entry splices onto the row above
        and a move the menu never offered appears to be there.
        """
        rows_ = self.menu_rows(first=0, last=20)
        canc = next((i for i, t in rows_ if t.strip().upper().startswith("CANCEL")), None)
        if canc is None:
            return []
        base = canc - 2 * self.mem[qm.ADDR_MENU_MAX]
        out = []
        for i in range(base, canc + 1, 2):
            t = self.window_row(i).strip().upper()
            tail = next((c for c in ("STATS", "SWITCH", "CANCEL") if c in t), None)
            if tail:
                out.append(tail)
                continue
            while t.startswith("AAAA") and len(t) > 4:  # the cursor glyph run splices the row
                t = t[4:]
            out.append(t.strip())
        return out

    def _hit_or_shift(self, wanted: str, first: int = 0, last: int = 18) -> tuple[int | None, str | None]:
        """The first row containing ``wanted`` (and its text) — the cursor row read clean.

        The cursor renders as an A-runs that splice its own row, names and all: the badge-7
        first- attempt read the lead's roster row as 'AAAAAAAAAA100' and Gyarados' own "SURF"
        entry as a run of As, and both of those are precisely the rows the cursor was pointing at
        when the menu opened. So a name nowhere to be seen is usually a cursor, not an absence:
        nudge the cursor — both directions, because it caps and a press past the end does nothing
        — and read again before declaring the row missing.
        """
        rows = self.menu_rows(first, last)
        hit = next(((i, t) for i, t in rows if wanted.upper() in t.upper()), None)
        if hit is not None:
            return hit
        c = self.ctl
        for direction, restore in (("down", "up"), ("up", "down")):
            c.press(direction)
            c.wait(20)
            rows = self.menu_rows(first, last)
            hit = next(((i, t) for i, t in rows if wanted.upper() in t.upper()), None)
            if hit is not None:
                c.press(restore)
                c.wait(20)
                return hit
        return None, None

    def menu_row_of(self, wanted: str, first: int = 0, last: int = 18) -> int | None:
        """The cursor index of the entry whose text contains ``wanted``, or None.

        Gen 1 **omits fainted members from the POKeMON menu**, so a party index is not a menu
        index: with two of six down, "member 0" selects whoever is drawn first, not the mon the
        caller meant. Match the name the menu prints instead — measured on the badge-7 leg, where
        the only surfer was the one that had fainted and the menu simply did not list it.
        """
        hit, _text = self._hit_or_shift(wanted, first, last)
        if hit is None:
            return None
        rows = self.menu_rows(first, last)
        text = {i: t for i, t in rows}
        if hit + 1 in text and re.fullmatch(r"\d+\s*/\s*0*\d+", text[hit + 1].strip()):
            # The roster interleaves an HP row under every entry — measured here: six members at
            # rows 0..11 with the levels on the odd rows — so the "nothing between entries"
            # walkback above stops one step short and reports every member as index 0. That is how
            # this leg armed Charizard's field list (row 10 spliced 'GYARADOS CUT') when Gyarados
            # was asked for. Roster entries are top-aligned at two rows per member, so the index
            # is the row halved; the HP row under the hit is the discriminator a pure entry list
            # never has.
            return hit // 2
        present = {i for i, _t in rows}
        start = hit
        while start - 2 in present and start - 1 not in present:
            start -= 2
        return (hit - start) // 2

    def use_field_move(
        self, name: str, face: str | None = None, member: int = 0, species: str | None = None
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Use a field move by *name*, choosing it off the menu the game draws.

        `road.cut_facing` hardcodes "CUT is row 0 of the lead's field submenu", which is true for
        Cut on this party and is exactly the kind of assumption that has cost this project runs.
        Which move sits on which row depends on the mon and what it has learned, so the row is
        read rather than assumed — the same fix the lift panel needed for its floor list.

        Returns whether the move was selected. Whether it *worked* is the caller's predicate:
        Cut is proved by stepping into the growth, Surf by ending up on water. Nothing here
        reports success from a menu having been navigated.
        """
        if face:
            self.ctl.press(face)
            self.ctl.wait(25)
        for _ in range(6):  # close anything already open before opening ours
            self.ctl.press("b")
            self.ctl.wait(25)
        if self.mem[qm.ADDR_IN_BATTLE]:
            print("  a battle is open; the field move waits for it", flush=True)
            return False
        self.ctl.press("start")
        self.ctl.wait(50)
        for _ in range(8):  # POKeMON is the row above ITEM
            if self.mem[qm.ADDR_MENU_CUR] == 1:
                break
            self.ctl.press("down" if self.mem[qm.ADDR_MENU_CUR] < 1 else "up")
            self.ctl.wait(20)
        self.ctl.press("a")
        self.ctl.wait(60)
        # By species when given: a fainted member is not drawn, so party index != menu index.
        target = member if species is None else self.menu_row_of(species)
        if target is None:
            # Measured 2026-09-04 on Route 20: this fired for a standing Gyarados because START
            # was pressed while a wild battle's text ("attack missed!") owned the screen -- the
            # party menu never opened and the roster read was a battle screen. Say which it was.
            why = "a battle owns the screen" if self.mem[qm.ADDR_IN_BATTLE] else "fainted members are not listed"
            print(f"  {species} is not on the POKeMON menu ({why})", flush=True)
            for _ in range(6):
                self.ctl.press("b")
                self.ctl.wait(25)
            return False
        for _ in range(8):  # the party list, then the member whose move we want
            if self.mem[qm.ADDR_MENU_CUR] == target:
                break
            self.ctl.press("down" if self.mem[qm.ADDR_MENU_CUR] < target else "up")
            self.ctl.wait(20)
        self.ctl.press("a")
        self.ctl.wait(60)
        target = name.strip().upper()
        # The field submenu overlays the roster (entries sit below it), so the entry base is read
        # from the screen rather than assumed: CANCEL is the last entry and renders clean, and an
        # entry is (row - base) // 2 off it. Measured on the lead: SURF r10, STATS r12,
        # SWITCH r14 (under the prompt), CANCEL r16, MAX 3 -> base 10.
        # Containment, not startswith: the cursor row decodes 'AAAAAAAA SURF' and the prompt row
        # decodes 'Choose a P SWITCH', so a startswith check misses both entries.
        rows = self.menu_rows(first=0, last=20)
        canc = next((i for i, t in rows if t.strip().upper().startswith("CANCEL")), None)
        if canc is None:
            print(f"  field submenu for member {member} drew no CANCEL row; not arming", flush=True)
            return False
        maxc = self.mem[qm.ADDR_MENU_MAX]
        base = canc - 2 * maxc
        want = None
        for i, t in rows:
            if (i - base) % 2 == 0 and 0 <= (i - base) // 2 <= maxc and target in t.strip().upper():
                want = (i - base) // 2
                break
        if want is None:  # the cursor row hides its entry the same way it hides a roster name
            hit, _t = self._hit_or_shift(name, 0, 20)
            if hit is not None and (hit - base) % 2 == 0 and 0 <= (hit - base) // 2 <= maxc:
                want = (hit - base) // 2
        if want is None:
            print(f"  no field move called {name!r} on party member {member}", flush=True)
            for _ in range(6):
                self.ctl.press("b")
                self.ctl.wait(30)
            return False
        for _ in range(10):
            if self.mem[qm.ADDR_MENU_CUR] == want:
                break
            self.ctl.press("down" if self.mem[qm.ADDR_MENU_CUR] < want else "up")
            self.ctl.wait(20)
        if self.mem[qm.ADDR_MENU_CUR] != want:
            print(f"  could not seat the cursor on {name!r} (row {base + 2 * want})", flush=True)
            return False
        self.ctl.press("a")
        self.ctl.wait(60)
        return True

    ADDR_BATTLE_COL = 0xCC25  # the battle menu's column: 9 = left (FIGHT/ITEM), 15 = right (PKMN/RUN)

    def battle_swap(self, index: int) -> bool:  # pragma: no cover - drives the emulator
        """Switch the active battler to party slot ``index``, mid-fight.

        This is what lets a level-5 recruit earn from a level-16 wild: it is sent out first, then
        swapped before it can be hit, and a Pokemon that was out — and did not faint — takes a
        share of the experience. Magikarp knows only SPLASH, so leaving it in is not an option:
        the first grind lap on Route 6 ended with it fainted and earning nothing while Dugtrio
        banked 125 EXP.

        The battle menu is two-dimensional and neither axis is the list cursor used everywhere
        else: 0xCC26 is the row (0 = FIGHT/PKMN, 1 = ITEM/RUN) and 0xCC25 the column (9 left,
        15 right), so PKMN is row 0 in the right column.
        """
        if not self.ag._await_battle_menu():
            return False
        for _ in range(4):
            if self.mem[qm.ADDR_MENU_CUR] == 0:
                break
            self.ctl.press("up")
            self.ctl.wait(20)
        for _ in range(4):
            if self.mem[self.ADDR_BATTLE_COL] >= 15:
                break
            self.ctl.press("right")
            self.ctl.wait(20)
        if self.mem[qm.ADDR_MENU_CUR] != 0 or self.mem[self.ADDR_BATTLE_COL] < 15:
            return False
        self.ctl.press("a")  # PKMN -> the party list
        self.ctl.wait(70)
        if not self.menu_cursor_to(index):
            return False
        self.ctl.press("a")
        self.ctl.wait(70)
        # SWITCH / STATS / CANCEL, drawn over the roster exactly as the field menu is.
        for candidate in range(3):
            if not self.menu_cursor_to(candidate, presses=5):
                continue
            self.ctl.press("a")
            self.ctl.wait(70)
            said = self.dialogue().upper()
            if "GO!" in said or "COME BACK" in said or "ENOUGH" in said:
                return True
            for _ in range(2):
                self.ctl.press("b")
                self.ctl.wait(30)
        return False

    def battle_flee(self) -> bool:  # pragma: no cover - drives the emulator
        """Run from the current battle. RUN is the bottom-right of the 2x2 battle menu.

        The escape hatch a fragile lead needs: when the swap fails, fighting on with SPLASH is
        how a level-5 Magikarp faints, and a fainted participant earns nothing. Fleeing costs the
        experience of one encounter and keeps the recruit.
        """
        if not self.ag._await_battle_menu():
            return False
        for _ in range(4):
            if self.mem[qm.ADDR_MENU_CUR] == 1:
                break
            self.ctl.press("down")
            self.ctl.wait(20)
        for _ in range(4):
            if self.mem[self.ADDR_BATTLE_COL] >= 15:
                break
            self.ctl.press("right")
            self.ctl.wait(20)
        self.ctl.press("a")
        self.ctl.wait(60)
        for _ in range(6):
            if not self.mem[qm.ADDR_IN_BATTLE]:
                return True
            self.ctl.press("a")
            self.ctl.wait(45)
        return not self.mem[qm.ADDR_IN_BATTLE]

    def lead_swap(self, index: int) -> bool:  # pragma: no cover - drives the emulator
        """Move the party member at ``index`` into slot 0, so it is sent out first.

        Only a Pokemon that is *sent out* earns a share of the fight, which is the whole mechanic
        behind grinding a weak recruit with a strong bench. The flow is read, not counted: the
        start menu's POKeMON entry must be matched on "MON" because "POK" also matches POKeDEX
        (measured — that mis-match opened the Pokedex), the roster shows nicknames so the member
        is chosen by index, and its submenu is STATS / SWITCH / CANCEL.
        """
        roster = [name for name, _lvl, _hp in self.party()]
        if not 0 <= index < len(roster) or index == 0:
            return index == 0

        def bail() -> bool:
            # A failed swap must not strand its own menus open. Measured on the karp grind:
            # a silent failure here left the START menu up, every following step was swallowed,
            # and the heal trip's first hop reported "refused" against a road that was clear.
            for _ in range(8):
                self.ctl.press("b")
                self.ctl.wait(25)
            return False

        for _ in range(6):
            self.ctl.press("b")
            self.ctl.wait(25)
        self.ctl.press("start")
        self.ctl.wait(70)
        if not self.menu_choose("MON"):
            return bail()
        self.ctl.wait(60)
        # The roster draws all six members, so it is the raw cursor that moves. `list_index`
        # adds the scroll register, and a baton banked after a bag walk still carries the bag's
        # offset there — measured on strength_taught.state, where the swap "could not reach"
        # slot 5 of a six-row list that had never scrolled.
        if not self.cursor_to(index):
            return bail()
        self.ctl.press("a")
        self.ctl.wait(70)
        rows = self.menu_rows(0, 20)
        canc = next((i for i, t in rows if t.strip().upper().startswith("CANCEL")), None)
        if canc is None:
            return bail()
        # The entry count is not fixed by position: a member with a field move draws
        # CUT/SURF(0) STATS(1) SWITCH(2) CANCEL(3), one without draws STATS(0) SWITCH(1)
        # CANCEL(2). Measured on this roster: Charizard's list put STATS where SWITCH was
        # assumed, and the seat picked the stats page — the swap silently never happened.
        # So the index is read from CANCEL (always last, always clean) like use_field_move,
        # then the row that carries "SWITCH" is mapped onto the entry space.
        maxc = self.mem[qm.ADDR_MENU_MAX]
        base = canc - 2 * maxc
        want = next(((i - base) // 2 for i, t in rows if "SWITCH" in t.upper() and (i - base) % 2 == 0), None)
        if want is None:
            return bail()
        if not self.cursor_to(want):  # the submenu draws whole, like the roster
            return bail()
        self.ctl.press("a")
        self.ctl.wait(70)
        if not self.cursor_to(0):
            return bail()
        self.ctl.press("a")
        self.ctl.wait(70)
        for _ in range(8):
            self.ctl.press("b")
            self.ctl.wait(25)
        now = [name for name, _lvl, _hp in self.party()]
        swapped = now and now[0] == roster[index]
        print(f"  lead is now {now[0] if now else '?'}" if swapped else f"  swap failed; lead is {now[0]}", flush=True)
        return bool(swapped)

    def use_item(self, name: str, face: str | None = None) -> bool:  # pragma: no cover - drives the emulator
        """Use a bag item by *name* from the ITEM menu. Returns whether it was selected.

        The engine could use field MOVES and had no way to use an ITEM, which is what a rod is —
        so with the OLD ROD in the bag there was still no way to fish. The item list scrolls like
        every other list here (cursor 0xCC26 inside its window, scroll 0xCC36), so the row is read
        rather than counted, and the entry is matched by the name the game prints.

        Whether it *worked* is the caller's predicate — a rod is proved by a bite, never by a menu
        having been navigated.
        """
        if face:
            self.ctl.press(face)
            self.ctl.wait(25)
        for _ in range(6):  # close anything already open before opening ours
            self.ctl.press("b")
            self.ctl.wait(25)
        self.ctl.press("start")
        self.ctl.wait(60)
        # By text, not by index: the start menu grows a PLAYER entry as the game goes on, so
        # "ITEM is the third row" is the kind of assumption that has cost this project runs. And
        # the block cannot be inferred from spacing alone — inside the Safari Zone the step
        # counter ("153/500") renders two rows above POKeDEX and the arithmetic counts it as an
        # entry, selecting the wrong line. POKeDEX is always the menu's first entry, so anchor on
        # it and count from there.
        rows = self.menu_rows()
        anchor = next((i for i, t in rows if "DEX" in t.upper()), None)
        item_row = next((i for i, t in rows if "ITEM" in t.upper()), None)
        if anchor is None or item_row is None:
            print("  the START menu did not open", flush=True)
            return False
        if not self.start_menu_cursor_to((item_row - anchor) // 2):
            print("  could not put the START menu's cursor on ITEM", flush=True)
            return False
        self.ctl.press("a")
        self.ctl.wait(60)
        self.ctl.wait(50)
        target = _menu_key(name)
        # The bag renders entries on rows 4/6/8/10 with their quantities interleaved, the cursor
        # caps at 2, and the list scrolls under it — so the highlighted row is 4 + 2*cursor and
        # the walk needs one step per item, not a fixed count.
        for _ in range(len(self.bag()) + 4):
            row = _menu_key(self.window_row(4 + 2 * self.mem[qm.ADDR_MENU_CUR]))
            if target and target in row:
                self.ctl.press("a")
                self.ctl.wait(55)
                return self.menu_choose("USE") or True  # some items skip the USE/TOSS submenu
            self.ctl.press("down")
            self.ctl.wait(18)
        print(f"  no bag item called {name!r}", flush=True)
        for _ in range(6):
            self.ctl.press("b")
            self.ctl.wait(30)
        return False

    def fly_to(self, town: str, presses: int = 12) -> bool:  # pragma: no cover - drives the emulator
        """FLY to ``town`` by the town map: DOWN until the top row names it, then A.

        Measured 2026-09-04: indoors the game answers "<FLYER> can't FLY here." and no map opens;
        outdoors the map's row 0 reads ``To<TOWN>`` and DOWN/UP cycle the destination while
        LEFT/RIGHT do nothing. The verdict is the map id changing -- the town is looked up in the
        cartridge's map names when known, otherwise any map change after the pick counts.
        """
        flyer_i = self.knows_move("FLY")
        if flyer_i is None:
            print("  nobody standing knows FLY", flush=True)
            return False
        before = self.pos()
        if not self.use_field_move("FLY", species=self.party()[flyer_i][0]):
            return False
        self.ctl.wait(60)
        said = self.textbox()
        if "CAN'T FLY" in said.upper():
            print(f"  {said}", flush=True)
            for _ in range(6):
                self.ctl.press("b")
                self.ctl.wait(25)
            return False
        for _ in range(presses):
            if fly_row_names(self.window_row(0), town):
                break
            self.ctl.press("down")
            self.ctl.wait(30)
        if not fly_row_names(self.window_row(0), town):
            print(f"  the town map never offered {town!r}; last row {self.window_row(0)!r}", flush=True)
            for _ in range(6):
                self.ctl.press("b")
                self.ctl.wait(25)
            return False
        self.ctl.press("a")
        for _ in range(12):  # the flight animation, then the landing
            self.ctl.wait(30)
            if self.pos()[0] != before[0]:
                break
        landed = self.pos()[0] != before[0]
        print(f"  FLY to {town}: {'landed at' if landed else 'still at'} {self.pos()}", flush=True)
        if landed:
            self.emit("flew", town=town, pos=list(self.pos()))
        return landed

    def teach(
        self, machine: str, species: str | None = None
    ) -> int | None:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Teach a TM/HM from the bag to a party member. Proved by the move id landing in RAM.

        The whole flow was measured on ``strength_won.state`` with HM04 (2026-09-04): USE ->
        "Booted up an HM!" -> "It contained STRENGTH!" -> "Teach STRENGTH to a POKeMON?" with
        YES highlighted -> a roster where every member is captioned ABLE / NOT ABLE (fainted
        members are drawn here, unlike the field-move roster) -> "GYARADOS learned STRENGTH!".
        Nothing is a remembered row: the pages are advanced until the roster's captions render,
        the member is chosen by party index against the captions, and success is the move id
        (from this cartridge's own move table) appearing in that member's struct.

        ``species`` names the member; otherwise the first ABLE member, standing ones first. A
        member holding four moves already gets the game's replace-a-move prompt; that branch is
        handled by text but was not exercised on this baton, so it is best-effort.
        """
        move = (self.truth.get("machines") or {}).get(machine.strip().upper())
        want = self._move_ids().get(move or "")
        if want is None:
            print(f"  the cartridge does not say what {machine} teaches", flush=True)
            return None
        roster = self.party()

        def knows(i: int) -> bool:
            base = ADDR_PARTY_STRUCTS + PARTY_STRUCT_SIZE * i
            return want in (self.mem[base + off] for off in MOVE_SLOTS)

        def bail(why: str) -> None:
            print(f"  teach {machine}: {why}", flush=True)
            for _ in range(8):
                self.ctl.press("b")
                self.ctl.wait(25)
            return None

        already = next((i for i in range(len(roster)) if knows(i)), None)
        if already is not None and (species is None or roster[already][0].upper() == species.upper()):
            print(f"  {roster[already][0]} already knows {move}", flush=True)
            return already
        if not self.use_item(machine):
            return bail("not in the bag")
        if not self.advance_text("TEACH", tries=6):
            return bail("the teach prompt never rendered")
        if not self.menu_shows("YES"):
            # Measured: "Teach STRENGTH" lands on screen while the typewriter is still mid-line,
            # and the YES / NO choice only draws once the question has finished printing.
            self.ctl.press("a")
            self.ctl.wait(80)
            if not self.menu_shows("YES"):
                return bail("the YES / NO prompt never rendered")
        self.ctl.press("a")  # YES is highlighted when the prompt opens (measured)
        self.ctl.wait(80)
        if not self.menu_shows("ABLE"):
            return bail("the ABLE / NOT ABLE roster never rendered")
        captions = {i: t for i, t in self.menu_rows(0, 12)}
        able = [i for i in range(len(roster)) if captions.get(2 * i + 1, "").strip().upper() == "ABLE"]
        if species is not None:
            pick = next((i for i in able if roster[i][0].strip().upper() == species.strip().upper()), None)
        else:
            pick = next((i for i in able if roster[i][2] > 0), able[0] if able else None)
        if pick is None:
            return bail(f"no ABLE member for {species or 'anyone'}; able={[roster[i][0] for i in able]}")
        # The roster draws all six members at once, so it is the raw cursor that moves here;
        # ``list_index`` adds the scroll register, which still holds the bag list's offset
        # (measured: the walk to HM04 at the end of the bag left it there and the cursor never
        # "reached" slot 0).
        if not self.cursor_to(pick):
            return bail("could not put the cursor on the member")
        self.ctl.press("a")
        self.ctl.wait(90)
        hms = {v for k, v in (self.truth.get("machines") or {}).items() if k.startswith("HM")}
        # Measured on a four-move Gyarados (2026-09-04): "GYARADOS is trying to learn SURF!" ->
        # "But, GYARADOS can't learn more than 4 moves!" -> "Delete an older move to make room
        # for SURF?" (YES highlighted) -> "Which move should be forgotten?" with the four moves on
        # consecutive rows. That is seven typewriter presses before the first prompt, so the old
        # budget of eight ran out exactly as the YES / NO drew. The wording it looked for
        # ("WHICH MOVE") never appears on this cartridge; "FORGOTTEN" does.
        for _ in range(24):
            if knows(pick):
                break
            text = self.textbox().upper()
            rows = [(i, t) for i, t in self.menu_rows(0, 18) if t.strip()]
            if "FORGOTTEN" in text:
                choice = forget_pick(rows, self._move_ids(), hms)
                if choice is None:
                    return bail("the replace prompt offered nothing this could forget")
                idx, victim = choice
                if not self.cursor_to(idx):
                    return bail(f"could not put the cursor on {victim}")
                print(f"  forgetting {victim} for {move}", flush=True)
                self.ctl.press("a")
                self.ctl.wait(120)
                continue
            self.ctl.press("a")  # typewriter, or YES on "make room?" (measured: YES is the default)
            self.ctl.wait(80)
        for _ in range(8):
            self.ctl.press("b")
            self.ctl.wait(25)
        if not knows(pick):
            print(f"  {roster[pick][0]} did not learn {move}", flush=True)
            return None
        print(f"  {roster[pick][0]} learned {move} ({machine})", flush=True)
        self.emit("taught", machine=machine, move=move, member=pick, species=roster[pick][0])
        return pick

    def fish(self, rod: str, face: str) -> bool:  # pragma: no cover - drives the emulator
        """Cast ``rod`` while facing ``face``. A bite is a battle; that is the only proof."""
        if not self.use_item(rod, face=face):
            return False
        # "Oh! It's a bite!" comes several frames before the battle flag flips, and reading the
        # flag too early reports a cast that hooked something as a miss — measured on Vermilion's
        # dock, twelve casts that all bit and all read as failures.
        for _ in range(12):
            if self.mem[qm.ADDR_IN_BATTLE]:
                return True
            self.ctl.press("a")
            self.ctl.wait(60)
        return bool(self.mem[qm.ADDR_IN_BATTLE])

    def surf_onto(self, face: str) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Ride onto water. The predicate is the position, never the menu.

        Measured on map 30 (2026-09-02): from (6,4), facing up refuses with "No SURFing on
        GYARADOS here!" while facing down surfs — and the two faced tiles carry the *same* id in
        the extracted grid. There is no tile test that predicts this, so the direction is chosen
        by asking the game and watching the position, which is what this returns.
        """
        before = self.pos()
        if face:
            self.ctl.press(face)
            self.ctl.wait(25)
        if not self._arm_surf():
            return False
        for _ in range(4):
            self.ctl.press("a")
            self.ctl.wait(50)
        self.io.press(face, hold=8, release=8)
        self.io.wait(45)
        return self.pos() != before

    def cut(self, face: str) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Cut the growth we face and prove it by stepping through it (``road.cut_until_open``)."""
        return road.cut_until_open(self.io, self.truth, self.pairs, face)

    def strength_push(
        self, face: str
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Enable Strength, then shove the boulder. Proved by the boulder's tile opening up.

        The move is used by whoever knows it, named by species: measured on Victory Road 1F
        (2026-09-05), party member 0 was a fainted Hypno and "no field move called 'STRENGTH' on
        party member 0" refused every push while Gyarados held it in slot 3.
        """
        idx = self.knows_move("STRENGTH")
        if idx is None:
            return False
        # By menu index, not by name: the POKeMON menu prints NICKNAMES (this party's Charizard is
        # "AAAAAAA", measured on strength_ready) and omits fainted members, so the menu index is
        # the party index less the fainted members drawn above it.
        # Measured both ways: the Route 23 party menu drew "HYPNO 100 FNT" in slot 0 (screenshot
        # 20260905-193131-1de1/surf_refused.png), while an older leg saw fainted members omitted.
        # So the party index is tried first, the index less the fainted members above second.
        party = self.party()
        fainted_above = sum(1 for _n, _l, hp in party[:idx] if hp <= 0)
        candidates = [idx] if not fainted_above else [idx, idx - fainted_above]
        if not any(self.use_field_move("STRENGTH", face=face, member=m) for m in candidates):
            return False
        for _ in range(4):
            self.ctl.press("a")
            self.ctl.wait(50)
        # Measured on Seafoam B3 (2026-09-04): an 8-frame press never moves a boulder and a 16-frame
        # hold does; the player's own cell may not change on the press that moves it, so the
        # verdict is the sprite table. A page left on screen swallows the press, so clear it first.
        for _ in range(8):
            if not self.textbox():
                break
            self.ctl.press("b")
            self.ctl.wait(24)
        before, sprites = self.pos(), sorted(tuple(b[:3]) for b in self.bodies())
        self.io.press(face, hold=16, release=16)
        self.io.wait(70)
        return self.pos() != before or sorted(tuple(b[:3]) for b in self.bodies()) != sprites

    # ---- surveying --------------------------------------------------------------------------

    def survey_pocket(
        self, max_cells: int = 400, log=print
    ) -> dict:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Walk the pocket for real and write down every wall that talks.

        The extracted collision grid cannot see a script gate. Inside Silph it calls a card-key
        door plain walkable floor, so *every* static region measured in that building over-reports
        — the 343 cells on 208 and the 128 on 235 both included ground behind locks. A route
        planned on those numbers is planned on a map of a different building.

        So this measures the pocket the way the only reliable statement about it can be made: a
        flood fill of **attempted steps**. Press, look at what happened, and when the step is
        refused, capture the sentence the game prints. The result is the pocket's true shape plus
        a door map keyed by (x, y, direction) — the locks turned into data.

        Each cell costs a save/load per direction, so this is deliberate, not cheap.
        """
        import io as _io
        from collections import deque

        deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
        mp, sx, sy = self.settled_pos()
        m = self.truth["maps"][str(mp)]
        ungated = {k: v for k, v in m.items() if k != "gates"}
        bodies = self.bodies()
        origin = _io.BytesIO()
        self.pb.save_state(origin)

        def snap():
            self.flush_text()  # a snapshot holding an open box poisons every probe made from it
            buf = _io.BytesIO()
            self.pb.save_state(buf)
            return buf

        def load(buf):
            buf.seek(0)
            self.pb.load_state(buf)

        cells = {(sx, sy)}
        doors: dict[str, str] = {}
        exits: dict[str, int] = {}
        battles: list[str] = []
        queue = deque([((sx, sy), snap())])
        probes = 0
        while queue and len(cells) < max_cells:
            cell, state = queue.popleft()
            for direction, (dx, dy) in deltas.items():
                target = (cell[0] + dx, cell[1] + dy)
                if target in cells:
                    continue
                load(state)
                self.io.press(direction, hold=8, release=8)
                self.io.wait(40)
                probes += 1
                if self.mem[qm.ADDR_IN_BATTLE]:
                    battles.append(f"{cell[0]},{cell[1]},{direction}")
                    continue  # a fight is not a wall; the next load undoes it
                now = self.pos()
                if now[0] != mp:
                    exits[f"{cell[0]},{cell[1]},{direction}"] = now[0]
                    continue
                if (now[1], now[2]) == cell:
                    # A press can turn in place before it walks, so give it a second one before
                    # calling the step refused.
                    self.io.press(direction, hold=8, release=8)
                    self.io.wait(40)
                    now = self.pos()
                if now[0] != mp:
                    exits[f"{cell[0]},{cell[1]},{direction}"] = now[0]
                    continue
                if (now[1], now[2]) == cell:
                    # A door is a *discrepancy*, not a message. The text buffer cannot carry this
                    # judgement: it is not cleared by closing a box, only overwritten when
                    # something new is drawn, so it reads the same before and after a refusal.
                    # The reliable signal is structural — the extracted grid says this step is
                    # walkable and passable, no body is standing there, and the engine still says
                    # no. That is exactly an unmodelled gate, and it is what the collision grid
                    # cannot see. Any text present is recorded as evidence, never as the test.
                    # Judge against the *grid*, not against what we already believe. `passable`
                    # is gate-aware now, so testing with it would skip every known gate and a
                    # false positive — a wanderer that moved, a trainer freeze — would become
                    # permanent, never re-probed. Surveys must be able to disagree with the file
                    # they feed.
                    if (
                        rt.passable(ungated, self.pairs, cell[0], cell[1], target[0], target[1])
                        and target not in bodies
                    ):
                        said = self.dialogue()
                        doors[f"{cell[0]},{cell[1]},{direction}"] = said or ""
                        log(f'  GATE at {cell} {direction} -> {target}   [buffer: "{(said or "")[:70]}"]')
                    continue
                landed = (now[1], now[2])
                if landed not in cells:
                    cells.add(landed)
                    queue.append((landed, snap()))
        load(origin)
        result = {
            "map": mp,
            "start": [sx, sy],
            "cells": sorted(cells),
            # ``doors`` maps a refused step to whatever was in the text buffer at the time. The
            # gate itself is the *key*, established structurally; the value is a hint and is
            # frequently stale — 207's four gates all "said" a trainer's line from minutes
            # earlier. Never read a value here as the door's own message.
            "doors": doors,
            "exits": exits,
            "battles": battles,
            "probes": probes,
            "truncated": len(cells) >= max_cells,
        }
        log(f"  surveyed map {mp}: {len(cells)} cells measured, {len(doors)} talking walls, {probes} probes")
        self.emit("supervisor.surveyed", map=mp, cells=len(cells), doors=len(doors), probes=probes)
        return result

    # ---- the oracle -------------------------------------------------------------------------

    def oracle_goto(
        self, goal_test, max_states: int = 500
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """BFS over press-and-settle transitions, using the game itself as the oracle.

        The mover for floors where planned walking is a category error: spin tiles, teleport
        pads, ice — anywhere the tile decides where you end up. Rocket Hideout B4 stood against
        an 880-state position-keyed oracle for weeks and fell in 721 states once **facing**
        entered the state key, because spin-tile movement reads 0xC109 and a position-only key
        prunes exactly the hold-arrivals the maze is made of.

        ``goal_test(pos) -> bool`` decides arrival. A failed search never strands the run at a
        random explored state — the origin is restored.
        """
        import io as _io
        from collections import deque

        def snap():
            buf = _io.BytesIO()
            self.pb.save_state(buf)
            return buf

        def load(buf):
            buf.seek(0)
            self.pb.load_state(buf)

        def key():
            return (*self.pos(), self.mem[ADDR_FACING])

        def press_settle(direction):
            self.pb.button(direction, delay=8)
            for _ in range(8):
                self.pb.tick()
            last, stable = self.pos(), 0
            for _ in range(40):
                for _ in range(10):
                    self.pb.tick()
                now = self.pos()
                if now == last:
                    stable += 1
                    if stable >= 3:
                        break
                else:
                    stable, last = 0, now
            return self.pos()

        if goal_test(self.pos()):
            return True
        origin = snap()
        seen = {key()}
        queue = deque([(key(), origin)])
        states = 0
        while queue and states < max_states:
            _state, snapshot = queue.popleft()
            for direction in ("down", "left", "right", "up"):
                load(snapshot)
                landed = press_settle(direction)
                if self.mem[qm.ADDR_IN_BATTLE]:
                    self.battle()
                    landed = self.pos()
                if goal_test(landed):
                    # Let the world finish. A warp fired by the settling step changes the map id
                    # before the coordinates catch up, and returning inside that window reports a
                    # position that cannot exist — the badge-6 leg announced arrival at
                    # (234, 17, 11) on a map only 16 tiles wide, then banked back on 209.
                    self.io.wait(90)
                    return True
                states += 1
                if key() not in seen:
                    seen.add(key())
                    queue.append((key(), snap()))
        load(origin)
        self.emit("oracle.exhausted", states=states, keys=len(seen), pos=list(self.pos()))
        return False

    def escape_pocket(
        self, max_states: int = 700
    ) -> bool:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        """Ride whatever this floor offers until we stand outside our own walkable region.

        Teleport pads are intra-map warps (``dst == this map``), and ``road.walk`` blocks every
        warp tile by design — the standing doctrine that a door is not a floor. That is right for
        walking and exactly wrong on a floor whose pads *are* the way across.

        Where this applies is measured, not assumed, and the measurement corrected a guess:
        Silph's floors have almost no intra-map pads (208 and 213 hold two each, the rest none) —
        they are cross-linked to *other floors* instead — so this returns False there, correctly.
        **Sabrina's gym, map 178, has thirty.** That is the floor this exists for.

        The goal is a **region**, not a cell: anywhere our own reachable set does not contain, on
        the same map. Aiming at a specific tile is what kept failing, because nothing knows which
        tile is on the far side of a pad until the game puts you there. Accepting any *other map*
        does not work either — the first run of this rode the floor's exit door out and called it
        an escape, which was true and useless.
        """
        mp, x, y = self.pos()
        region = road.reachable(self.truth, self.pairs, mp, (x, y), self.bodies())
        print(f"  escaping a {len(region)}-cell pocket on map {mp} by riding what the floor offers", flush=True)
        found = self.oracle_goto(lambda p: p[0] == mp and (p[1], p[2]) not in region, max_states=max_states)
        if found:
            self.emit("supervisor.pocket_escaped", map=mp, from_cells=len(region), to=list(self.pos()))
        return found

    # ---- banking --------------------------------------------------------------------------

    def unlock_gates(self) -> int:
        """Drop every measured door gate that names an item now in the bag. Returns how many.

        Called on boot and after each pickup, because the bag is what turns a locked door into a
        door. The CARD KEY was taken on 5F and the very next leg planned as though it had not
        been: `no-path` on 3F -> 7F, our own model refusing a route the world would have allowed.
        """
        held = {name for name, _qty in self.bag_named()}
        if not held:
            return 0
        opened = 0
        for m in self.truth.get("maps", {}).values():
            gates = m.get("gates")
            if not gates:
                continue
            kept = rt.gates_the_bag_opens(gates, held)
            opened += len(gates) - len(kept)
            m["gates"] = kept
        if opened:
            print(f"  the bag opens {opened} measured door gate(s)", flush=True)
        return opened

    # A Center's PC is neither a sign nor a sprite — the extraction has nothing for it, because it
    # is a tile you press A into. Measured by sweeping the interior's northern boundary on
    # Cerulean's map 64: from (13,4) facing up the screen says "AAAAAAA turned on the PC." and the
    # window renders BILL's PC / <player>'s PC / PROF.OAK's PC / LOG OFF. Every Center shares the
    # 14x8 tileset-6 interior, so the cell is a template like the nurse's counter.
    CENTER_PC = ((13, 4), "up")

    def center_pc(self, map_id: int) -> tuple[tuple[int, int], str] | None:
        """Where to stand and face to turn on the PC, if this map is a Pokemon Center."""
        return self.CENTER_PC if self.center_counter(map_id) else None

    def menu_rows(self, first: int = 0, last: int = 18) -> list[tuple[int, str]]:
        """The window layer's non-empty rows — menus render there, never to the background."""
        return [(i, t) for i in range(first, last) if (t := self.window_row(i)).strip()]

    def menu_shows(self, wanted: str, tries: int = 6) -> bool:
        """Wait — without pressing anything — until the window renders ``wanted``.

        This used to press A while it looked, and that is how Charizard and then Dugtrio ended up
        in a box: inside a list, A confirms the highlighted entry. Two traps make "is it safe to
        press?" unanswerable from the screen: the window keeps showing the *previous* menu while a
        box is up ("Accessed my PC."), and the text buffer stays stale after the box closes. So
        this only ever looks, and every press lives at a call site that knows what it is pressing.
        """
        for _ in range(tries):
            if any(wanted.upper() in text.upper() for _i, text in self.menu_rows()):
                return True
            self.ctl.wait(40)
        return any(wanted.upper() in text.upper() for _i, text in self.menu_rows())

    def advance_text(self, expect: str, tries: int = 8) -> bool:
        """Press A through a text box until the window changes, then judge the new menu.

        Safe *only* where the screen is known to be a box rather than a list: "Accessed BILL's
        PC." takes several presses to clear, and one press is not enough. It stops the moment the
        rows change, so it can never walk on into a list and confirm an entry there — which is
        exactly what an unbounded version did when it deposited Charizard and then Dugtrio.
        """
        avoid = [name.upper() for name, _lvl, _hp in self.party()]
        for _ in range(tries):
            rows = self.menu_rows()
            if any(expect.upper() in text.upper() for _i, text in rows):
                return True
            # The party list is the one screen where A commits something. Recognise it by its own
            # contents — the roster is right there in RAM — and stop before pressing into it.
            if any(any(who in text.upper() for who in avoid) for _i, text in rows):
                return False
            self.ctl.press("a")
            self.ctl.wait(50)
        return any(expect.upper() in text.upper() for _i, text in self.menu_rows())

    def list_index(self) -> int:
        """Which entry a scrolling list has highlighted: cursor within the window PLUS scroll.

        Measured on the deposit roster: ``0xCC26`` caps at 2 (a three-row window) while ``0xCC36``
        counts how far the list has scrolled, so reading only the cursor picks the wrong member
        for anything past the third slot.
        """
        return self.mem[qm.ADDR_MENU_CUR] + self.mem[ADDR_LIST_SCROLL]

    def cursor_to(self, index: int, presses: int = 12) -> bool:
        """Put a NON-scrolling menu's highlight on ``index`` by the raw cursor register.

        `menu_cursor_to` adds the scroll register, which is right for the bag and the PC box and
        wrong for every menu that draws all its entries at once: a baton banked after a bag walk
        still carries the bag's scroll offset (16, measured on strength_taught.state), and the
        party roster, its STATS/SWITCH/CANCEL submenu and the TM roster all "failed to reach" an
        entry that was three presses away.
        """
        for _ in range(presses):
            at = self.mem[qm.ADDR_MENU_CUR]
            if at == index:
                return True
            self.ctl.press("down" if at < index else "up")
            self.ctl.wait(20)
        return self.mem[qm.ADDR_MENU_CUR] == index

    def start_menu_cursor_to(self, index: int, presses: int = 16) -> bool:  # pragma: no cover - drives the emulator
        """The START menu does not scroll: its highlight is the cursor register alone. Judged with
        ``list_index`` (cursor + scroll), a scroll value left behind by an earlier list made ITEM
        unreachable -- measured on strength_ready (2026-09-05): every press cycled the cursor and
        the teach reported HM04 "not in the bag" with the HM in it."""
        for _ in range(presses):
            at = self.mem[qm.ADDR_MENU_CUR]
            if at == index:
                return True
            self.ctl.press("down" if at < index else "up")
            self.ctl.wait(20)
        return self.mem[qm.ADDR_MENU_CUR] == index

    def menu_cursor_to(self, index: int, presses: int = 16) -> bool:
        """Walk a scrolling list's highlight to ``index``, judged by cursor + scroll."""
        for _ in range(presses):
            at = self.list_index()
            if at == index:
                return True
            self.ctl.press("down" if at < index else "up")
            self.ctl.wait(20)
        return self.list_index() == index

    def menu_choose(self, wanted: str, *, presses: int = 12) -> bool:
        """Move the menu cursor onto the entry whose text contains ``wanted`` and press A.

        Selection is by *decoded text*, never by a remembered position. The PC's own menu is why:
        it lists WITHDRAW, DEPOSIT, RELEASE and CHANGE BOX, and choosing by index would one day
        release a party member because a menu shifted. Entries render every other row, so the
        cursor index is ``(row - first_row) // 2``, and the cursor register is the ground truth
        for where it currently sits.
        """
        rows = self.menu_rows()
        if not rows:
            return False
        hit, _text = self._hit_or_shift(wanted)
        if hit is None:
            return False
        # Menus OVERLAY: choosing DEPOSIT renders the party list on top of the box menu, and the
        # follow-up DEPOSIT/STATS/CANCEL renders on top of *that* — measured rows read
        # ('WI', 'DE'+nickname, 'RE GLOOM', ..., (12, 'DEPOSIT')). So the cursor index is measured
        # from the first row of the block the match sits in, not from the first row on screen; the
        # old arithmetic put the cursor five entries down a five-entry list.
        present = {i for i, _t in rows}
        block_start = hit
        # A block is entries two rows apart with nothing between them. The roster interleaves its
        # levels on the odd rows ((8,'CHGLOOM'),(9,'99')), and the DEPOSIT/STATS/CANCEL confirm
        # renders *below* all of that at row 12 — so walking back without checking the odd row
        # crossed into the roster and put the cursor five entries down a three-entry menu.
        while block_start - 2 in present and block_start - 1 not in present:
            block_start -= 2
        want = (hit - block_start) // 2
        for _ in range(presses):
            cur = self.mem[qm.ADDR_MENU_CUR]
            if cur == want:
                break
            self.ctl.press("down" if cur < want else "up")
            self.ctl.wait(20)
        if self.mem[qm.ADDR_MENU_CUR] != want:
            return False
        self.ctl.press("a")
        self.ctl.wait(45)
        return True

    def pc_store_item(self, name: str) -> bool:  # pragma: no cover - drives the emulator
        """Put one bag item into the PC's item storage. The bag shrinking is the proof.

        The bag caps at twenty slots and a full bag refuses purchases outright — Vermilion's mart
        failed at "confirm purchase" with no explanation, and the leg needed Poke Balls for a
        catch. `make_room` only helps when something is stacked; everything here is a single key
        item or TM. Storage is the player's OWN PC — the one whose menu is WITHDRAW ITEM /
        DEPOSIT ITEM / TOSS ITEM — which is the same screen that was mistaken for the Pokemon box.

        Measured 2026-09-04 at Cinnabar's Center: the deposit list SCROLLS (three names in the
        window, the rest below), so a text-matched cursor cannot reach the tenth entry -- the
        entry is picked by its bag index through ``menu_cursor_to`` (cursor + scroll), the same
        register pair the deposit roster needed. And every early return used to leave the PC menu
        open, where the next routine's A presses deposited whatever was highlighted (the MOON
        STONE, the S.S.TICKET and the SECRET KEY all went that way): now every path B's out.
        """
        spot = self.center_pc(self.pos()[0])
        if spot is None or not self.approach({spot[0]}):
            return False
        before = self.bag_named(full=True)
        names = [item for item, _qty in before]
        if name not in names:
            return False
        stored = False
        try:
            self.ctl.press(spot[1])
            self.ctl.wait(25)
            for _ in range(4):
                self.ctl.press("a")
                self.ctl.wait(55)
                if self.menu_rows():
                    break
            own = next(
                (
                    t
                    for _i, t in self.menu_rows()
                    if "PC" in t.upper() and "BILL" not in t.upper() and "OAK" not in t.upper()
                ),
                None,
            )
            if own is None or not self.menu_choose(own):
                return False
            if not self.advance_text("DEPOSIT") or not self.menu_choose("DEPOSIT ITEM"):
                return False
            self.ctl.wait(40)
            if not self.menu_shows("deposit") or not self.menu_cursor_to(names.index(name)):
                return False
            self.ctl.press("a")  # the entry -> "How many?" (defaults to one)
            self.ctl.wait(45)
            self.ctl.press("a")  # confirm -> "<item> was stored"
            self.ctl.wait(60)
            stored = len(self.bag()) < len(before)
            return stored
        finally:
            for _ in range(8):
                self.ctl.press("b")
                self.ctl.wait(25)
            self.ctl.wait(40)  # the bag count settles a few frames after the box closes
            print(f"  {'stored' if stored else 'could not store'} {name}; bag now {len(self.bag())}/20", flush=True)

    def store_at_pc(self, count: int, keep=()) -> int:  # pragma: no cover - drives the emulator
        """Bank ``count`` bag items in this Center's PC along ``storage_plan``. Returns how many left the bag.

        The game's own answer to a full bag; tossing is the fallback for a leg with no Center in
        reach. Each deposit is judged by the bag shrinking (``pc_store_item``); an item the PC
        refuses stays and the plan moves on.
        """
        if self.center_pc(self.pos()[0]) is None:
            print(f"  map {self.pos()[0]} is not a Center -- no PC to store in", flush=True)
            return 0
        stored = 0
        for name in storage_plan(self.bag_named(full=True), keep=keep):
            if stored >= count:
                break
            if self.pc_store_item(name):
                stored += 1
        return stored

    def pc_deposit(self, index: int) -> bool:  # pragma: no cover - drives the emulator
        """Deposit the party member at ``index`` into Bill's PC. The party shrinking is the proof.

        By index, not by name: the list renders *nicknames* — this run's lead shows as
        "AAAAAAAAAA", not CHARIZARD — so matching a species name there can never work. Every A
        press below is at a step whose screen is known; nothing presses A while searching.

        BILL's PC is the Pokemon box. The player's own PC is the item storage system (WITHDRAW
        ITEM / DEPOSIT ITEM / TOSS ITEM), and picking it because it carries the player's name is
        exactly the recalled assumption this repo forbids — measured wrong, one menu at a time.
        """
        spot = self.center_pc(self.pos()[0])
        if spot is None or not self.approach({spot[0]}):
            return False
        before = len(self.party())
        self.ctl.press(spot[1])
        self.ctl.wait(25)
        for _ in range(4):  # A through "turned on the PC." until the top menu renders
            self.ctl.press("a")
            self.ctl.wait(55)
            if self.menu_rows():
                break
        if not self.menu_choose("BILL"):
            return False
        if not self.advance_text("DEPOSIT"):  # "Accessed BILL's PC." -> the box submenu
            return False
        if not self.menu_choose("DEPOSIT"):
            return False
        roster = [name for name, _lvl, _hp in self.party()]
        if not 0 <= index < len(roster) or not self.menu_cursor_to(index):
            return False
        self.ctl.press("a")  # pick that party member -> the confirm menu
        self.ctl.wait(50)
        if not self.menu_shows("DEPOSIT"):
            return False
        # The confirm menu names its own order on screen: "DEPOSIT What? STATS CANCEL". Its rows
        # cannot be told apart from the roster's by shape — the roster's sixth level row is cut
        # off, so DEPOSIT at row 12 looks like a continuation of the entry at row 10, and the
        # block arithmetic picked STATS. So drive it by position and *verify*: STATS is harmless
        # and backs out with B, which makes a wrong pick recoverable rather than a guess.
        for candidate in range(3):
            if not self.menu_cursor_to(candidate, presses=6):
                continue
            self.ctl.press("a")
            self.ctl.wait(55)
            for _ in range(3):
                if len(self.party()) < before:
                    break
                self.ctl.press("a")
                self.ctl.wait(45)
            if len(self.party()) < before:
                break
            rows = self.menu_rows()
            if any("ATTACK" in t.upper() or "EXP POINTS" in t.upper() for _i, t in rows):
                for _ in range(3):  # a stats page: back out and try the next entry
                    self.ctl.press("b")
                    self.ctl.wait(30)
        for _ in range(8):
            self.ctl.press("b")
            self.ctl.wait(25)
        # The species that left must be the one we chose. A deposit is irreversible from the
        # leg's point of view, and this run has already watched a menu bug take Charizard off
        # the bench twice; "the party got smaller" is not proof that the right one went.
        left = [n for n in roster if roster.count(n) > [x for x, _l, _h in self.party()].count(n)]
        if left != [roster[index]]:
            self.emit("pc.deposit_mismatch", wanted=roster[index], left=left)
            print(f"  WARNING: meant to deposit {roster[index]}, the party lost {left}", flush=True)
            return False
        print(f"  deposited {roster[index]}; party is now {[n for n, _l, _h in self.party()]}", flush=True)
        return True

    def grass_lanes(self, map_id: int) -> list[tuple[int, int]]:
        """The two extreme grass cells on a map — a lane to pace for encounters.

        Where to roam comes from the ROM's own grass tiles, never from lore. Pacing between the
        extremes keeps crossing fresh tiles instead of rolling the same one, which is what the
        recruit grinds measured as the difference between fights and a step counter going up.
        """
        cells = [tuple(c) for c in self.truth["maps"].get(str(map_id), {}).get("grass", [])]
        if not cells:
            return []
        # Only grass we can actually stand on. The extremes of the whole map are not a lane:
        # Route 2's 84 grass cells are all outside the 144-cell region a leg arriving from
        # Diglett's Cave can reach, so a roam aimed at them walked nowhere and rolled no
        # encounters at all — twelve thousand laps of a level-5 Magikarp staying level 5.
        mp, x, y = self.pos()
        if mp == map_id:
            here = road.walkable(self.truth, self.pairs, map_id, (x, y), self.bodies())
            reachable_cells = [c for c in cells if c in here]
            if not reachable_cells:
                print(f"  no grass reachable on map {map_id} from {(x, y)}", flush=True)
                return []
            cells = reachable_cells
        return [min(cells, key=lambda c: (c[1], c[0])), max(cells, key=lambda c: (c[1], c[0]))]

    def roam_grass(self, map_id: int, until, laps: int = 40) -> bool:  # pragma: no cover - drives the emulator
        """Pace this map's grass until ``until()`` says stop. Battles are the point, not a failure."""
        lanes = self.grass_lanes(map_id)
        if not lanes:
            return False
        for lap in range(laps):
            for target in (lanes[lap % 2], lanes[(lap + 1) % 2]):
                if until():
                    return True
                self.walk(map_id, {target}, cap=200)
                if self.pos()[0] != map_id:  # a battle or a door moved us off the lane
                    return until()
        return until()

    def center_counter(self, map_id: int) -> tuple[tuple[int, int], str] | None:
        """Where to stand and which way to face to be healed, if this map is a Pokemon Center.

        A nurse is not an ordinary body: she stands *behind a counter*, so no cell is adjacent to
        her and `engage_bodies` — which only ever walks to a neighbouring tile — cannot meet her.
        Measured cost: a leg reached Saffron's Center, talked to all three idle NPCs (growth
        rates, Silph gossip, the Cable Club), and reported the heal refused with three fainted
        party members.

        The geometry is one template, verified live at Cerulean, Pewter and Vermilion
        (`quartermaster.CENTERS`): nurse sprite at (3,1), player at (3,3), facing up. Saffron's
        map 182 is the same 14x8 tileset-6 interior with the same nurse tile, which is how it was
        identified — by signature, not by recall.
        """
        m = self.truth["maps"].get(str(map_id))
        if not m or (m["width"], m["height"], m["tileset"]) != (14, 8, 6):
            return None
        if not any(s["kind"] == "npc" and (s["x"], s["y"]) == (3, 1) for s in m.get("sprites", [])):
            return None
        return (3, 3), "up"

    def heal_at_center(self) -> bool:
        """Stand at this map's nurse counter and A through the heal until the party reads full.

        ``center_counter`` knows where to stand — the nurse is *behind* the counter, adjacent to
        no cell, so talking to bodies can never meet her. The judge is quartermaster's party
        read, every member back at its own max: ``party()`` carries no max HP, and "nobody
        fainted" is already true before the top-off heal a grind leg comes in for.
        """
        counter = self.center_counter(self.pos()[0])
        if counter is None:
            print(f"  map {self.pos()[0]} is not a Center — nowhere to heal", flush=True)
            return False
        cell, face = counter

        def full() -> bool:
            party = qm.read_party(self.io)
            return bool(party) and all(p["hp"] == p["max_hp"] for p in party)

        if full():
            return True
        if not self.approach({cell}):
            print(f"  could not reach the nurse's counter at {cell}", flush=True)
            return False
        for _ in range(3):
            try:
                qm.heal(self.io, face)
            except qm.QuartermasterError:
                continue
            if full():
                return True
        return full()

    def step_off_targets(self, map_id: int, x: int, y: int) -> list[tuple[str, tuple[int, int]]]:
        """Directions off a warp tile that land on ordinary floor — doors excluded, in order."""
        m = self.truth["maps"].get(str(map_id))
        if not m:
            return []
        warps = self.warp_tiles(map_id)
        out = []
        for direction, (dx, dy) in (("up", (0, -1)), ("down", (0, 1)), ("left", (-1, 0)), ("right", (1, 0))):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < m["width"] and 0 <= ny < m["height"]):
                continue
            if m["grid"][ny][nx] != "1" or (nx, ny) in warps:
                continue
            if not rt.passable(m, self.pairs, x, y, nx, ny):
                continue
            out.append((direction, (nx, ny)))
        return out

    def _step_off_mat(self, mp: int, x: int, y: int) -> bool:  # pragma: no cover - drives the emulator
        """Try each floor-ward neighbour, undoing any step that leaves the map.

        A mat's neighbours can fire too — Silph 3F's (11,11) sits beside another door, and the
        step-off went straight back to 7F, so the *next* leg booted on the wrong side of the
        building and spent its budget trying to get back. A step that changes the map is not a
        step off the mat, so it is rolled back and the next direction tried.
        """
        import io as _io

        def snap():
            buf = _io.BytesIO()
            self.pb.save_state(buf)
            return buf

        before = snap()
        for direction, cell in self.step_off_targets(mp, x, y):
            self.ctl.press(direction)
            self.ctl.wait(30)
            if self.pos() == (mp, *cell):
                print(f"  stepped off the {mp} warp mat at ({x}, {y}) before banking", flush=True)
                return True
            before.seek(0)
            self.pb.load_state(before)  # that neighbour was a door too; undo and try another
        print(f"  WARNING: could not step off the warp mat at ({x}, {y}) on {mp}", flush=True)
        return False

    def bank(
        self, name: str, *, directory: Path | None = None
    ) -> Path:  # pragma: no cover - writes and reloads a real save state
        """Bank a baton the next leg can actually boot.

        Two states are worthless as batons and both were paid for: one banked mid-dialogue (every
        step swallowed) and one banked standing ON a warp mat, which boots back through the door
        it just came out of. Settle first, step off the door if we are on it, then save.
        """
        import io as _io

        arrival = self.pos()[0]
        entry = _io.BytesIO()
        self.pb.save_state(entry)  # the map we were asked to bank; anything else is not it
        mp, x, y = self.pos()
        if (x, y) in self.warp_tiles(mp):
            # Step off BEFORE settling. `settle`'s probe will use a door when every neighbour is
            # one, and Silph 3F's (11,11) mat is surrounded by them — so settling first fired the
            # warp and the baton recorded (212,5,3), a floor away from the leg that had just
            # arrived. Two chained legs booted on the wrong side of the building that way.
            self._step_off_mat(mp, x, y)
        self.settle()
        if self.pos()[0] != arrival:
            print(f"  banking on {arrival} but settling left us on {self.pos()[0]} — rolling back", flush=True)
            entry.seek(0)
            self.pb.load_state(entry)
        mp, x, y = self.pos()
        if (x, y) in self.warp_tiles(mp):
            # A real step, not a probe. `probe_step` presses and *undoes*, which leaves us on the
            # mat, and the reload check cannot see the problem because an in-process reload does
            # not settle. Booting that baton in a fresh process does, and the settle walks out
            # through the door: Saffron's Center banked at (182,3,7) came back up in the city,
            # and the leg spent its ladder trying to get back in. A Center has two mats side by
            # side, so the escape has to prefer a neighbour that is not itself a door.
            self._step_off_mat(mp, x, y)
        path = (directory or BATON_DIR) / f"{name}.state"
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = self.settled_pos()
        with path.open("wb") as fh:
            self.pb.save_state(fh)
        # Read it back. Three batons this session were unusable — one banked mid-dialogue, one
        # standing on a warp mat, one mid-transition reporting a tile its map does not have — and
        # each was discovered a leg later by a run that had already spent its budget booting it.
        # A baton nobody can boot is not a baton, and the check costs one load.
        with path.open("rb") as fh:
            self.pb.load_state(fh)
        got = self.settled_pos()
        if got != expected:
            print(f"  WARNING: {path.name} reloads as {got}, not {expected} — do not trust it", flush=True)
            self.emit("baton.unstable", name=name, expected=list(expected), got=list(got))
        else:
            print(f"  banked {path.name} at {expected}", flush=True)
        return path

    def shot(
        self, path: str | Path
    ) -> Path:  # pragma: no cover - drives the emulator; verified live, not in unit tests
        from PIL import Image

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(self.pb.screen.ndarray).save(out)
        return out
