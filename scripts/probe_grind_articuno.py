"""Level a party member (argv[4], default Articuno): lead with it, pace the Cinnabar mansion's 1F (map 165,
wilds L28-42, rate 10) between two cells, let the agent fight every wild, heal at the Cinnabar Center (171) when
the lead is low, bank every few fights, stop at the target level or the fight budget. Levels come from the party
read; nothing is recalled."""

import os
import subprocess
import sys
import time
from collections import deque

sys.path.insert(0, "scripts")
import quartermaster as qm  # noqa: E402
from expedition_rig import ADDR_PARTY_STRUCTS, MOVE_SLOTS, PARTY_STRUCT_SIZE, Rig  # noqa: E402

STATE = sys.argv[1] if len(sys.argv) > 1 else "data/local_runs/roster-bench/legend_flies.state"
TARGET = int(sys.argv[2]) if len(sys.argv) > 2 else 100
BUDGET_S = int(sys.argv[3]) if len(sys.argv) > 3 else 5 * 3600
SPECIES = sys.argv[4] if len(sys.argv) > 4 else "Articuno"
LANE = [(5, 25), (2, 12)]  # mansion 1F: from the entrance hall up the west corridor and back
print(subprocess.run(["date"], capture_output=True, text=True).stdout.strip(), flush=True)
# LIVE_LABEL attaches the recorder: every fight then lands as battle / move_result / battle_outcome
# rows in runs/<run_id>/events.jsonl, which the SFT converter reads. Without it the rows are lost.
rig = Rig(STATE, settle_on_boot=True, live_label=os.environ.get("LIVE_LABEL"))
t0 = time.time()


def drain(n=16):
    for _ in range(n):
        if rig.mem[qm.ADDR_IN_BATTLE]:
            rig.battle()
            continue
        if not rig.textbox():
            return
        rig.ctl.press("b")
        rig.ctl.wait(24)


def member(name):
    for i, (n, lvl, hp) in enumerate(rig.party()):
        if n == name:
            return i, lvl, hp
    return None


def moves(i):
    names = {v: k for k, v in rig._move_ids().items()}
    base = ADDR_PARTY_STRUCTS + PARTY_STRUCT_SIZE * i
    return [names.get(rig.mem[base + o], hex(rig.mem[base + o])) for o in MOVE_SLOTS]


def to_center_and_heal():
    """From wherever we are: mansion -> Cinnabar -> Center 171 -> nurse -> back out."""
    drain()
    mp = rig.pos()[0]
    if mp in (165, 214, 215, 216):
        rig.walk(165, {(5, 26)}, battle=rig.battle)
        for _ in range(3):
            if rig.pos()[0] == 8:
                break
            rig.io.press("down", hold=16, release=16)
            rig.ctl.wait(90)
            drain()
    if rig.pos()[0] == 8:
        rig.walk(8, {(11, 12)}, battle=rig.battle)
        for _ in range(3):
            if rig.pos()[0] == 171:
                break
            rig.io.press("up", hold=16, release=16)
            rig.ctl.wait(90)
            drain()
    if rig.pos()[0] == 171:
        ok = rig.heal_at_center()
        drain()
        leave_center()
        return ok
    return False


def leave_center():
    """Out of 171 by the door mats (3,7)/(4,7): approach from ABOVE. Measured 2026-09-04: a heal leaves the player
    at (2,7) under the NPC at (2,6), and a walk aimed straight at (3,6) from there never reaches the mat."""
    for _ in range(3):
        if rig.pos()[0] != 171:
            return True
        drain()
        rig.walk(171, {(3, 5)}, battle=rig.battle)
        rig.walk(171, {(3, 6)}, battle=rig.battle)
        if rig.pos()[1:] != (3, 6):
            for key in ("up", "right", "down"):
                rig.io.press(key, hold=12, release=8)
                rig.ctl.wait(30)
        for _ in range(3):
            if rig.pos()[0] != 171:
                return True
            rig.io.press("down", hold=16, release=16)
            rig.ctl.wait(90)
            drain()
    return rig.pos()[0] != 171


def to_mansion():
    drain()
    if rig.pos()[0] == 171:
        leave_center()
    if rig.pos()[0] == 8:
        rig.walk(8, {(6, 4)}, battle=rig.battle)
        for _ in range(3):
            if rig.pos()[0] == 165:
                break
            rig.io.press("up", hold=16, release=16)
            rig.ctl.wait(90)
            drain()
    return rig.pos()[0] == 165


i0 = member(SPECIES)
print("start", rig.pos(), "party", [(n, lv, h) for n, lv, h in rig.party()], flush=True)
if i0 is None:
    sys.exit(f"no {SPECIES} in the party")
print(f"{SPECIES} moves:", moves(i0[0]), flush=True)
if i0[0] != 0:
    print("lead_swap ->", rig.lead_swap(i0[0]), [n for n, _l, _h in rig.party()], flush=True)
    drain()
fights, last_level = 0, member(SPECIES)[1]
recent = deque(maxlen=20)
while time.time() - t0 < BUDGET_S:
    idx, lvl, hp = member(SPECIES)
    if lvl >= TARGET:
        print(f"*** {SPECIES} reached L{lvl} ***", flush=True)
        rig.bank(f"{SPECIES.lower()}_L{lvl}")
        break
    if lvl != last_level:
        print(f"  level {last_level} -> {lvl} after {fights} fights ({(time.time() - t0) / 60:.1f} min)", flush=True)
        last_level = lvl
        rig.bank(f"grind_{SPECIES.lower()}")
    maxhp = qm.read_party(rig.io)[idx]["max_hp"] or 1
    fainted = [n for n, _l, h in rig.party() if h <= 0]
    if hp < maxhp * 0.35 or fainted:
        print(f"  heal: {SPECIES} {hp}/{maxhp}, fainted {fainted}", flush=True)
        to_center_and_heal()
        if member(SPECIES)[0] != 0:
            rig.lead_swap(member(SPECIES)[0])
        to_mansion()
        continue
    if rig.pos()[0] != 165:
        if not to_mansion():
            print("  lost outside the mansion at", rig.pos(), flush=True)
            to_center_and_heal()
            to_mansion()
            continue
    before = fights
    for target in LANE:
        b0 = rig.mem[qm.ADDR_IN_BATTLE]
        rig.walk(165, {target}, battle=rig.battle, cap=120)
        drain()
    # count fights by the agent's battle log is indirect; count encounters by level/hp change or the battle flag seen
    fights += 1  # one lap ~ one or two wilds at rate 10; the level print is the real progress meter
    if fights % 25 == 0:
        i, lvl, hp = member(SPECIES)
        print(
            f"  lap {fights}: {SPECIES} L{lvl} {hp}hp pos {rig.pos()} ({(time.time() - t0) / 60:.1f} min)", flush=True
        )
        rig.bank(f"grind_{SPECIES.lower()}")
print("end", rig.pos(), [(n, lv, h) for n, lv, h in rig.party()], flush=True)
rig.bank(f"grind_{SPECIES.lower()}")
print(subprocess.run(["date"], capture_output=True, text=True).stdout.strip(), flush=True)
rig.finish(fights=fights)
print("done", rig.run_id, rig.pos(), rig.party(), flush=True)
