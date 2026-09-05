"""NPC catalog: what every body said when the crew talked to it, what it handed over, whether it
fought — measured from the expedition sink and joined to the cartridge's sprite table.

The Forger's ground truth. Nothing here is recalled: sentences come from
``supervisor.body_engaged`` / ``supervisor.blocker_engaged`` events (the screen, read live), the
body's kind and picture from ``references/rom_truth.json`` (the ROM), the fight from the
``battle.outcome`` / ``battle.fled`` event the talk triggered, the handout from the bag diff the
supervisor measured. The report says which maps the crew has never talked on, and which banked
save already stands there — the sweep list.

    uv run python scripts/npc_catalog.py build            # -> references/npc_catalog.json
    uv run python scripts/npc_catalog.py report           # coverage per map, batons to replay
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

TELEMETRY = Path("data/telemetry/game")
TRUTH = Path("references/rom_truth.json")
OUT = Path("references/npc_catalog.json")
BATON_INDEX = Path("data/local_runs/roster-bench/index.json")

ENGAGE = "supervisor.body_engaged"
BLOCKER = "supervisor.blocker_engaged"
LEG_START = "supervisor.leg_start"
FIGHTS = ("battle.outcome", "battle.fled")
COLLECTED = "supervisor.item_collected"  # the sweep opened a ball: map, at, items
REFUSED = "supervisor.item_refused"  # it could not: map, at, holds (what the cartridge lists)
WANTED = (ENGAGE, BLOCKER, LEG_START, COLLECTED, REFUSED) + FIGHTS
FIGHT_WINDOW_S = 5.0  # the talk starts the fight; its outcome lands just before the engage row
# Reads that are not the body's words: the START menu the engage loop opens on an item-ball tile
# (measured on maps 194, 219, 234) and the battle text pinned after a flee.
NOISE = frozenset({"OPTION EXIT", "Got away safely!"})


def clean_said(said: str) -> str:
    """One sentence from the decoder's growing window reads.

    ``"Yo! Champ in making! | Even I don't | Even I don't know VIRIDIAN LEADER's | VIRIDIAN
    LEADER's identity!"`` is four reads of one scrolling box: partial reads are dropped and the
    overlap between consecutive reads is merged once.
    """
    parts = [p.strip() for p in said.split("|") if p.strip()]
    keep: list[str] = []
    for p in parts:
        if any(o != p and o.startswith(p) for o in parts):
            continue  # a partial read of a fuller snapshot
        if keep and keep[-1] == p:
            continue
        keep.append(p)
    out = ""
    for p in keep:
        if not out:
            out = p
            continue
        k = next((n for n in range(min(len(out), len(p)), 2, -1) if out.endswith(p[:n])), 0)
        out = out + p[k:] if k else out + " " + p
    return out


def _ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def load_truth(path: Path = TRUTH) -> tuple[dict, dict]:
    """(sprite index keyed (map, x, y), item names keyed by id) from the extracted ROM truth."""
    truth = json.loads(path.read_text())
    sprites = {}
    for mid, m in truth.get("maps", {}).items():
        for s in m.get("sprites") or []:
            sprites[(int(mid), int(s["x"]), int(s["y"]))] = s
    return sprites, truth.get("items", {})


def _new_body(sprite: dict | None, items: dict) -> dict:
    body = {
        "kind": (sprite or {}).get("kind", "unknown"),
        "pic": (sprite or {}).get("pic"),
        "sentences": {},
        "gained": {},
        "fought": 0,
        "won": 0,
        "seen": 0,
        "runs": [],
    }
    if sprite and sprite.get("kind") == "item":
        body["item"] = items.get(str(sprite.get("item")), f"#{sprite.get('item')}")
    return body


def iter_events(telemetry: Path = TELEMETRY):
    """The sink's engage-family lines, in file order, JSON-parsed only when wanted."""
    for path in sorted(telemetry.glob("*.jsonl")):
        with open(path) as fh:
            for line in fh:
                if '"source": "expedition"' not in line:
                    continue
                if not any(f'"event": "{w}"' in line for w in WANTED):
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def build(telemetry: Path = TELEMETRY, truth_path: Path = TRUTH) -> dict:
    sprites, items = load_truth(truth_path)
    maps: dict[int, dict] = defaultdict(lambda: {"bodies": {}})
    cur_map: dict[str, int] = {}
    last_fight: dict[str, tuple[datetime | None, bool | None]] = {}
    for e in iter_events(telemetry):
        run = e.get("run_id", "")
        ev = e.get("event")
        if ev == LEG_START:
            pos = e.get("pos") or []
            if pos:
                cur_map[run] = int(pos[0])
            continue
        if ev in FIGHTS:
            last_fight[run] = (_ts(e.get("ts")), e.get("won") if ev == "battle.outcome" else False)
            continue
        if ev in (COLLECTED, REFUSED):
            mp = e.get("map")
            x, y = (e.get("at") or [None, None])[:2]
            if mp is None or x is None:
                continue
            bodies = maps[int(mp)]["bodies"]
            key = f"{x},{y}"
            body = bodies.get(key)
            if body is None:
                body = bodies[key] = _new_body(sprites.get((int(mp), int(x), int(y))), items)
            if ev == COLLECTED:
                for item_id, qty in e.get("items") or []:
                    name = items.get(str(item_id), f"#{item_id}")
                    body["gained"][name] = body["gained"].get(name, 0) + int(qty)
            else:
                body["refused"] = body.get("refused", 0) + 1
            if run and run not in body["runs"]:
                body["runs"].append(run)
            continue
        if ev == ENGAGE:
            mp = e.get("map")
            x, y = (e.get("at") or [None, None])[:2]
        else:  # blocker: no map on the row; the run's current map
            mp = cur_map.get(run)
            x, y = (e.get("body") or [None, None])[:2]
        if mp is None or x is None:
            continue
        cur_map[run] = int(mp)
        key = f"{x},{y}"
        bodies = maps[int(mp)]["bodies"]
        body = bodies.get(key)
        if body is None:
            body = bodies[key] = _new_body(sprites.get((int(mp), int(x), int(y))), items)
        body["seen"] += 1
        if run and run not in body["runs"]:
            body["runs"].append(run)
        sentence = clean_said(e.get("said") or "")
        if sentence in NOISE:
            body["noise"] = body.get("noise", 0) + 1
            sentence = ""
        if sentence:
            body["sentences"][sentence] = body["sentences"].get(sentence, 0) + 1
        for item_id, qty in e.get("gained") or []:
            name = items.get(str(item_id), f"#{item_id}")
            body["gained"][name] = body["gained"].get(name, 0) + int(qty)
        when, won = last_fight.get(run, (None, None))
        now = _ts(e.get("ts"))
        if when and now and 0 <= (now - when).total_seconds() <= FIGHT_WINDOW_S:
            body["fought"] += 1
            body["won"] += 1 if won else 0
            last_fight.pop(run, None)  # one fight, one body
        if ev == BLOCKER:
            body["blocker"] = True
    for (mid, x, y), s in sprites.items():
        entry = maps[mid]
        entry.setdefault("sprites", 0)
        entry["sprites"] += 1
        if s.get("kind") == "item":
            picked = bool(entry["bodies"].get(f"{x},{y}", {}).get("gained"))
            entry.setdefault("items", []).append(
                {"x": x, "y": y, "item": items.get(str(s.get("item")), f"#{s.get('item')}"), "picked": picked}
            )
    catalog = {"built": datetime.now().astimezone().isoformat(timespec="seconds"), "maps": {}}
    for mid in sorted(maps):
        entry = maps[mid]
        entry.setdefault("sprites", 0)
        entry["engaged"] = len(entry["bodies"])
        catalog["maps"][str(mid)] = entry
    return catalog


def report(catalog: dict, baton_index: Path = BATON_INDEX, limit: int = 40) -> str:
    """Per map: bodies the cartridge lists vs bodies talked to, item balls picked, and which
    banked saves stand on the map. Sorted by what is still unheard."""
    batons: dict[int, list[str]] = defaultdict(list)
    if baton_index.exists():
        for name, v in json.loads(baton_index.read_text()).items():
            if "map" in v:
                batons[int(v["map"])].append(name)
    rows = []
    for mid, entry in catalog["maps"].items():
        items = entry.get("items", [])
        rows.append(
            (
                entry.get("sprites", 0) - entry.get("engaged", 0),
                int(mid),
                entry.get("sprites", 0),
                entry.get("engaged", 0),
                sum(1 for i in items if i["picked"]),
                len(items),
                ", ".join(sorted(batons.get(int(mid), []))[:3]) or "-",
            )
        )
    rows.sort(key=lambda r: (-r[0], r[1]))
    lines = [
        f"{'map':>4} {'sprites':>7} {'talked':>6} {'unheard':>7} {'items':>9}  batons standing here",
        "-" * 78,
    ]
    for missing, mid, sprites, engaged, picked, n_items, names in rows[:limit]:
        lines.append(f"{mid:>4} {sprites:>7} {engaged:>6} {missing:>7} {picked:>4}/{n_items:<4}  {names}")
    total_sprites = sum(e.get("sprites", 0) for e in catalog["maps"].values())
    total_engaged = sum(e.get("engaged", 0) for e in catalog["maps"].values())
    total_items = sum(len(e.get("items", [])) for e in catalog["maps"].values())
    picked = sum(1 for e in catalog["maps"].values() for i in e.get("items", []) if i["picked"])
    lines.append("-" * 78)
    lines.append(
        f"{len(catalog['maps'])} maps; {total_engaged} of {total_sprites} bodies talked to; "
        f"{picked} of {total_items} item balls picked; "
        f"{sum(1 for e in catalog['maps'].values() if e.get('engaged'))} maps with any talk"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("command", choices=["build", "report"])
    p.add_argument("--telemetry", type=Path, default=TELEMETRY)
    p.add_argument("--truth", type=Path, default=TRUTH)
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--baton-index", type=Path, default=BATON_INDEX)
    p.add_argument("--limit", type=int, default=40)
    args = p.parse_args(argv)
    if args.command == "build":
        catalog = build(args.telemetry, args.truth)
        args.out.write_text(json.dumps(catalog, indent=1, sort_keys=True) + "\n")
        print(f"wrote {args.out}: {len(catalog['maps'])} maps")
        return 0
    catalog = json.loads(args.out.read_text())
    print(report(catalog, args.baton_index, args.limit))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
