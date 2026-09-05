"""Index the banked batons: boot each .state once and record where it stands and what it carries.

    uv run python scripts/probe_baton_index.py [roster-bench dir] [out json]

Writes {name: {"map": m, "x": x, "y": y, "party": [[species, level, hp], ...], "bag": n}} so a
catalog sweep can pick, for any map, a save that already stands on it -- without booting 700 states
again. Boots settled: five saves banked on warp pads reported the destination map when read
unsettled (measured 2026-09-05: 148 vs 147, 74 vs 17, 85 vs 22, 192 vs 31, 220 vs 156).

Each boot runs in its own process with a hard timeout: a settle can open a wild fight the party
cannot end (measured: a L19 Goldeen looped a fainted party's switch menu for half an hour, and the
battle loop swallows an in-process alarm), so the parent kills the child and records the error.
"""

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

BOOT_TIMEOUT_S = 60


def _boot(path: str, out: mp.Queue) -> None:  # pragma: no cover - drives the emulator
    sys.path.insert(0, "scripts")
    from expedition_rig import Rig

    rig = Rig(path, settle_on_boot=True)
    m, x, y = rig.pos()
    out.put({"map": m, "x": x, "y": y, "party": [list(p) for p in rig.party()], "bag": len(rig.bag())})


def index_one(path: Path, timeout_s: float = BOOT_TIMEOUT_S) -> dict:  # pragma: no cover - spawns the emulator
    q: mp.Queue = mp.Queue()
    proc = mp.Process(target=_boot, args=(str(path), q), daemon=True)
    proc.start()
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        return {"error": f"boot+settle > {timeout_s:.0f} s (a fight the settle could not end?)"}
    if q.empty():
        return {"error": f"boot failed (exit {proc.exitcode})"}
    entry = q.get()
    entry["mtime"] = int(path.stat().st_mtime)
    return entry


def main() -> int:  # pragma: no cover - drives the emulator
    bench = Path(sys.argv[1] if len(sys.argv) > 1 else "data/local_runs/roster-bench")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else bench / "index.json")
    index: dict = json.loads(out.read_text()) if out.exists() else {}
    states = sorted(bench.glob("*.state"))
    t0 = time.time()
    done = 0
    for path in states:
        if path.stem in index:
            continue
        index[path.stem] = index_one(path)
        done += 1
        if done % 10 == 0:
            out.write_text(json.dumps(index, indent=1, sort_keys=True))
            got = index[path.stem].get("map", index[path.stem].get("error"))
            print(f"{len(index)}/{len(states)} {path.stem} -> {got} ({time.time() - t0:.0f}s)", flush=True)
    out.write_text(json.dumps(index, indent=1, sort_keys=True))
    print("wrote", out, len(index), "batons", flush=True)
    return 0


if __name__ == "__main__":
    mp.set_start_method("spawn")
    sys.exit(main())
