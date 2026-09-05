"""Boulder oracle: catalog every Strength push ever tried on a floor, and search from the catalog.

The Mt. Moon / Rocket Hideout shape. A boulder floor is a small search space (configuration =
where the boulders are), the cartridge is the only authority on what a push does, and a leg
that reasons about it from the tile map re-tries the same pushes every run. So every push is
measured once, written to ``references/boulder_catalog.json`` the moment it happens, and the
search resumes from that file: a killed run loses nothing and the next run tries only what is
untried. Configurations reached are banked as save states so later runs start from them.

Measured mechanics this relies on (journal, 2026-09-04): STRENGTH is activated once per boot;
a 16-frame hold moves a boulder; the sprite table is the verdict; a page on screen swallows a
press.

    uv run python scripts/boulder_oracle.py run --state <baton> --map 161 --max-pushes 40
    uv run python scripts/boulder_oracle.py show --map 161
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import road  # noqa: E402

CATALOG_PATH = Path("references/boulder_catalog.json")
ORACLE_STATES = Path("data/local_runs/roster-bench/oracle")
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
HOLD_FRAMES = 16  # measured: 8 never moves a boulder, 16 does


def config_key(boulders) -> str:
    """A configuration is where the boulders are; order-free and hashable."""
    return ";".join(f"{x},{y}" for x, y in sorted(boulders))


HOLE_TILE = 0x22  # measured 2026-09-04: the tile a boulder falls through (B1 (18,6); B3 (3,16))


def fallen(truth, map_id: int, boulders) -> set[tuple[int, int]]:
    """Boulders whose sprite slot sits on a hole tile: they fell, the slot just kept the coordinates.

    Measured: after (3,15) dropped into (3,16) the sprite table still listed (3,16), and every
    'push' on it was the player stepping onto the open hole and falling to B4 -- 56 wasted pushes
    in one run. Such a boulder is not a candidate; the open hole is a cell to avoid.
    """
    m = truth["maps"][str(map_id)]
    tiles = m.get("tiles")
    if not tiles:
        return set()
    return {(x, y) for x, y in boulders if int(tiles[y][2 * x : 2 * x + 2], 16) == HOLE_TILE}


def candidate_pushes(truth, pairs, map_id: int, player, boulders) -> list[tuple[tuple[int, int], str, tuple[int, int]]]:
    """Every (stand, direction, boulder) the player can walk to from here with the boulders solid.

    The far tile is not consulted: whether a boulder enters a hole, a floor or a wall is the
    cartridge's call, and asking it is the whole point of the catalog. A boulder on a hole tile
    has already fallen (see ``fallen``): the open hole is blocked for walking and never pushed.
    """
    boulders = set(boulders)
    gone = fallen(truth, map_id, boulders)
    region = road.reachable(truth, pairs, map_id, tuple(player), blocked=boulders)
    out = []
    for bx, by in sorted(boulders - gone):
        for name, (dx, dy) in DIRS.items():
            stand = (bx - dx, by - dy)
            if stand in region:
                out.append((stand, name, (bx, by)))
    return out


GAP_TILE = 0x24  # measured on Victory Road 1F (probe_vr_1f_plate, 2026-09-04): the boulder pushed onto (9,12)
# stays there and the tile carries the player -- the grid calls it solid, the boulder makes it floor.


def _filled(truth, map_id: int, cell) -> dict:
    """The truth with ``cell`` walkable: a boulder resting on a gap tile is floor from then on."""
    m = truth["maps"][str(map_id)]
    if m["grid"][cell[1]][cell[0]] == "1":
        return truth
    rows = [list(r) for r in m["grid"]]
    rows[cell[1]][cell[0]] = "1"
    filled = dict(m)
    filled["grid"] = ["".join(r) for r in rows]
    return {**truth, "maps": {**truth["maps"], str(map_id): filled}}


def _is_gap(m, cell) -> bool:
    tiles = m.get("tiles")
    return bool(tiles) and int(tiles[cell[1]][2 * cell[0] : 2 * cell[0] + 2], 16) == GAP_TILE


def push_plan(
    truth, pairs, map_id: int, start, targets, bodies, boulders, max_pushes: int = 40
) -> list[tuple[tuple[int, int], str, tuple[int, int]]] | None:
    """The pushes ``(stand, direction, boulder)`` of ONE boulder that reconnect ``start``'s region
    to ``targets``: ``[]`` when already connected, ``None`` when no boulder can (a two-boulder
    puzzle is the oracle's job, not this).

    Measured on Victory Road 1F (2026-09-05): the crew engaged the boulder at (2,10) as a body
    seven times ("This requires STRENGTH to move!") and never pushed. A push is simulated on the
    sprite set -- the boulder lands one cell on, the player stands where it was, the player's
    region is recomputed -- as a breadth-first search over the boulder's positions, so it may
    turn corners. A boulder pushed onto a gap tile (0x24) fills it and becomes floor (Victory
    Road 1F's rule, measured 2026-09-04), which is the whole point of most pushes there.
    """
    from collections import deque

    m = truth["maps"][str(map_id)]
    w, h = m["width"], m["height"]
    bodies = set(bodies)
    targets = set(targets)
    start = tuple(start)
    boulders = (set(boulders) & bodies) - fallen(truth, map_id, boulders)
    # A warp tile is not a place to stand or to park a boulder: measured on Victory Road 1F
    # (2026-09-05), the sixth push was planned from the exit at (8,17) and the player left the map.
    warps = {(wp[0], wp[1]) for wp in m.get("warps", [])}

    def connected(world, player, blocked):
        return bool(road.reachable(world, pairs, map_id, player, blocked=blocked) & targets)

    if connected(truth, start, bodies):
        return []
    best = None
    for b0 in sorted(boulders):
        others = bodies - {b0}
        # state: (boulder cell, player cell); the player is wherever the last push left them
        seen = {(b0, start)}
        queue = deque([((b0, start), [])])
        while queue:
            (b, player), moves = queue.popleft()
            if len(moves) >= max_pushes:
                continue
            region = road.reachable(truth, pairs, map_id, player, blocked=others | {b})
            for name, (dx, dy) in DIRS.items():
                stand = (b[0] - dx, b[1] - dy)
                land = (b[0] + dx, b[1] + dy)
                if stand not in region or stand in warps:
                    continue
                if not (0 <= land[0] < w and 0 <= land[1] < h) or land in others or land in warps:
                    continue
                gap = _is_gap(m, land)
                if m["grid"][land[1]][land[0]] != "1" and not gap:
                    continue
                nxt = (land, b)  # the boulder moved on; the player now stands where it was
                if nxt in seen:
                    continue
                seen.add(nxt)
                path = moves + [(stand, name, b)]
                if gap:  # it fills the gap: floor now, and no longer a body
                    if connected(_filled(truth, map_id, land), b, others):
                        rank = (len(path), abs(stand[0] - start[0]) + abs(stand[1] - start[1]))
                        if best is None or rank < best[0]:
                            best = (rank, path)
                    continue  # a boulder in a gap does not move again
                if connected(truth, b, others | {land}):
                    rank = (len(path), abs(stand[0] - start[0]) + abs(stand[1] - start[1]))
                    if best is None or rank < best[0]:
                        best = (rank, path)
                    break
                queue.append((nxt, path))
    return best[1] if best else None


def parse_surf_test(spec: str | None) -> tuple[tuple[int, int], str] | None:
    """``"15,7,down"`` -> ((15, 7), "down"): the shore cell to stand on and the way to face.

    The objective on a current floor is not 'a boulder fell' but 'the water carries me somewhere
    else now': after every configuration the driver surfs from this cell and records the landing.
    """
    if not spec:
        return None
    x, y, face = spec.split(",")
    if face.strip() not in DIRS:
        raise ValueError(f"unknown direction {face!r}")
    return (int(x), int(y)), face.strip()


def classify(before_map: int, after_map: int, before_boulders, after_boulders) -> str:
    """What a push did, from the map and the sprite table alone."""
    if after_map != before_map:
        return "player-fell"
    b, a = set(before_boulders), set(after_boulders)
    if len(a) < len(b):
        return "fell"
    if a != b:
        return "moved"
    return "refused"


class Catalog:
    """Every push ever tried, per map and configuration. Saved after each record."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or CATALOG_PATH)  # resolved at call time, so tests can point it elsewhere
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def records(self, map_id: int) -> list[dict]:
        return self.data.setdefault(str(map_id), {}).setdefault("pushes", [])

    def tried(self, map_id: int, key: str) -> set[tuple[tuple[int, int], str]]:
        return {(tuple(r["stand"]), r["dir"]) for r in self.records(map_id) if r["config"] == key}

    def untried(self, map_id: int, key: str, candidates) -> list:
        done = self.tried(map_id, key)
        return [c for c in candidates if (c[0], c[1]) not in done]

    def states(self, map_id: int) -> dict[str, str]:
        """Banked save states per configuration reached, so a later run resumes from them."""
        return self.data.setdefault(str(map_id), {}).setdefault("states", {})

    def currents(self, map_id: int) -> dict[str, dict]:
        """Where the water carried the surfer, per configuration (the surf-test results)."""
        return self.data.setdefault(str(map_id), {}).setdefault("current", {})

    def add(self, map_id: int, record: dict) -> None:
        self.records(map_id).append(record)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")

    def summary(self, map_id: int) -> str:
        recs = self.records(map_id)
        configs = {r["config"] for r in recs}
        by = {}
        for r in recs:
            by[r["outcome"]] = by.get(r["outcome"], 0) + 1
        lines = [f"map {map_id}: {len(recs)} pushes over {len(configs)} configurations; outcomes {by}"]
        landings = {}
        for key, c in self.currents(map_id).items():
            landings.setdefault(str(c.get("landing")), []).append(key)
        for land, keys in landings.items():
            lines.append(f"  surf test lands at {land} for {len(keys)} configuration(s)")
        for r in recs:
            if r["outcome"] in ("fell", "player-fell"):
                lines.append(
                    f"  {r['outcome']:11} config {r['config']} stand {tuple(r['stand'])} {r['dir']} -> {r.get('after')}"
                )
        return "\n".join(lines)


def run(
    state: str, map_id: int, max_pushes: int, log=print, surf_test=None
) -> int:  # pragma: no cover - drives the emulator
    import io as _io

    import quartermaster as qm
    import rom_truth as rt
    from expedition_rig import Rig

    rig = Rig(state, settle_on_boot=True)
    truth = rig.truth
    pairs = rt.loaded_pairs(truth)
    cat = Catalog()
    if rig.pos()[0] != map_id:
        log(f"baton is on map {rig.pos()[0]}, not {map_id}")
        return 2

    def drain(limit=12):
        for _ in range(limit):
            if rig.mem[qm.ADDR_IN_BATTLE]:
                rig.battle()
                continue
            if not rig.textbox():
                return
            rig.ctl.press("b")
            rig.ctl.wait(24)

    drain()
    who = rig.knows_move("STRENGTH")  # by RAM, not by menu position: member 0 is rarely the one
    if who is None or not rig.use_field_move("STRENGTH", species=rig.party()[who][0]):
        log("could not activate STRENGTH")
        return 3
    for _ in range(6):
        rig.ctl.press("a")
        rig.ctl.wait(40)
    drain()

    def snap() -> bytes:
        buf = _io.BytesIO()
        rig.pb.save_state(buf)
        return buf.getvalue()

    def load(blob: bytes) -> None:
        rig.pb.load_state(_io.BytesIO(blob))
        rig.ctl.wait(10)

    root_key = config_key(rig.bodies())
    frontier = [(root_key, snap())]
    for key, path in cat.states(map_id).items():  # configurations earlier runs reached
        if key != root_key and Path(path).exists():
            frontier.append((key, Path(path).read_bytes()))
    seen = {k for k, _ in frontier}
    pushes = 0
    while frontier:
        key, blob = frontier.pop(0)
        load(blob)
        if surf_test and key not in cat.currents(map_id):
            stand, face = surf_test
            drain()
            rig.walk(map_id, {stand}, battle=rig.battle)
            drain()
            if rig.pos()[1:] == tuple(stand):
                rig.io.press(face, hold=4, release=8)
                rig.ctl.wait(20)
                armed = rig._arm_surf()
                for _ in range(20):
                    rig.ctl.wait(8)
                    if rig.pos()[0] != map_id:
                        break
                drain()
                landing = list(rig.pos())
                cat.currents(map_id)[key] = {
                    "stand": list(stand),
                    "face": face,
                    "armed": armed,
                    "landing": landing,
                    "run_id": rig.run_id,
                }
                cat.save()
                log(f"  surf test from {stand} {face}: armed={armed} landing={landing}")
            else:
                log(f"  surf test: could not stand at {stand} (at {rig.pos()[1:]})")
            load(blob)
        if pushes >= max_pushes:
            continue  # still surf-test every known configuration; just no new pushes
        boulders = rig.bodies()
        cands = cat.untried(map_id, key, candidate_pushes(truth, pairs, map_id, rig.pos()[1:], boulders))
        log(f"config {key}: {len(cands)} untried pushes")
        for stand, name, boulder in cands:
            if pushes >= max_pushes:
                break
            load(blob)
            drain()
            w = rig.walk(map_id, {stand}, battle=rig.battle)
            drain()
            if rig.pos()[1:] != tuple(stand):
                cat.add(
                    map_id,
                    {
                        "config": key,
                        "stand": list(stand),
                        "dir": name,
                        "boulder": list(boulder),
                        "outcome": "unreachable",
                        "walk": str(w),
                        "run_id": rig.run_id,
                    },
                )
                continue
            # The sprite table is read BEFORE the facing press: measured, a 4-frame facing tap
            # already starts the shove, and the boulder finishes moving during the wait after it.
            before_map, before = rig.pos()[0], set(rig.bodies())
            rig.io.press(name, hold=4, release=8)
            rig.ctl.wait(20)
            drain()
            rig.io.press(name, hold=HOLD_FRAMES, release=16)
            rig.ctl.wait(70)
            after_map = rig.pos()[0]
            after = set(rig.bodies()) if after_map == map_id else set()
            outcome = classify(before_map, after_map, before, after)
            said = rig.textbox()
            rec = {
                "config": key,
                "stand": list(stand),
                "dir": name,
                "boulder": list(boulder),
                "outcome": outcome,
                "after": config_key(after) if after_map == map_id else f"map {after_map} at {rig.pos()[1:]}",
                "player": list(rig.pos()[1:]),
                "text": said,
                "run_id": rig.run_id,
                "ts": time.strftime("%H:%M:%S"),
            }
            pushes += 1
            log(f"  push {boulder} {name} from {stand}: {outcome} -> {rec['after']} {said!r}")
            if outcome in ("fell", "player-fell"):
                rec["screenshot"] = rig.screenshot(f"oracle_{outcome}_{boulder[0]}_{boulder[1]}_{name}")
            if outcome in ("moved", "fell"):
                new_key = config_key(after)
                if new_key not in seen:
                    seen.add(new_key)
                    blob2 = snap()
                    frontier.append((new_key, blob2))
                    ORACLE_STATES.mkdir(parents=True, exist_ok=True)
                    p = ORACLE_STATES / f"{map_id}_{new_key.replace(';', '_').replace(',', '-')}.state"
                    p.write_bytes(blob2)
                    cat.states(map_id)[new_key] = str(p)
                    rec["state"] = str(p)
            cat.add(map_id, rec)
            drain()
    log(cat.summary(map_id))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--state", required=True)
    r.add_argument("--map", type=int, required=True)
    r.add_argument("--max-pushes", type=int, default=40)
    r.add_argument(
        "--surf-test", default=None, help="shore cell and facing to surf from after every configuration, e.g. 15,7,down"
    )
    s = sub.add_parser("show")
    s.add_argument("--map", type=int, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "show":
        print(Catalog().summary(a.map))
        return 0
    return run(a.state, a.map, a.max_pushes, surf_test=parse_surf_test(a.surf_test))  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
