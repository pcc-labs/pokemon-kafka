# Replay arcs for the seats — recapturing the corpus from saves

*2026-09-05. Measured on the empirical-evidence corpus build `data/sft_v4` and this repo's sinks.*

## Why replay

- The species table was fixed on **2026-08-26**. Every battle row recorded before that day can
  name a Pokémon by hex id (`#6B`, `#A9`, `#04`); dropping those rows halves the Wheelman's
  corpus (battle-outcome 4,156 → 2,255; move-choice 4,344 → 1,896; battle-action 178 → 75).
- The `pokemon.game.v1` battle sink **stopped on 2026-08-27**. Every leg since — Silph, Seafoam,
  Victory Road, the League, the museum — was driven by `supervisor.py`, whose fights go through
  `Rig.battle()` → `agent.run_battle_turn()`. That path emits expedition events (`battle.fled`,
  `battle.wedge`) but never the collector's `battle` / `move_result` / `battle_outcome` rows; the
  recorded runs of 09-04 carry only `session` under `event_type`. The cleanest play we have
  produced zero Wheelman rows.
- The Forger's domains (gate-text 37, puzzle-consult 175, handoff 58) only exist since 08-31,
  when the expedition sink began. They are clean and thin.

We hold 711 banked states under `data/local_runs/roster-bench/`, one at nearly every wall the
crew ever hit. Replaying from them is cheaper than playing forward, and every replay lands in
the sink with a fresh `run_id`, the fixed species table, and provenance the converter now stamps
on each row (`meta.occurred_at`, `run_id`, `game`).

## Step 0 — wire the collector into the supervisor's fights (blocking)

Nothing below yields Wheelman rows until this lands.

1. `Rig.battle()` (`scripts/expedition_rig.py`) must feed `self.ag.collector` the same three
   events the standalone agent emits at `scripts/agent.py` ~2765 (`battle` per turn), ~2853
   (`move_result`) and ~3813 (`battle_outcome` / `battle_end` after the fight): pre-battle lead
   species/level/HP/move types, per-turn action, damage per move, result.
2. The collector only exists when the rig is booted with `--live-label` (`Rig._go_live`). Make
   that the default for supervisor runs, or attach a collector without the frame recorder.
3. **Acceptance**: boot `grind_start.state` with `--live-label`, walk into one wild fight, and
   `grep battle_outcome data/telemetry/game/<today>.jsonl` shows a row whose `user_species`
   and `enemy_species` are names, not `#xx`, under the run's `run_id`. Add the assertion to
   `tests/test_supervisor_leg.py` against the fake rig so it cannot regress silently.

## The run recipe (every arc below)

```bash
uv run python scripts/semantic_router.py route "<the leg>"          # cast the seat
uv run python scripts/supervisor.py run \
    --state data/local_runs/roster-bench/<baton>.state \
    --goal <maps> --budget 3600 --engage \
    --live-label "<seat> replay — <arc>" --bank <arc>
# verify before moving on: the sink has rows under this run_id
grep -c '"run_id": "<run_id>"' data/telemetry/game/$(date -u +%F).jsonl
```

Then, in empirical-evidence:

```bash
uv run python -m autotune.convert_telemetry --pk-data ../pokemon-kafka/data --pk-root ../pokemon-kafka
cat data/sft_v4/stats.json        # domains + vintage + dropped_unresolved_species
```

The converter aborts itself at half of MemTotal and writes `memlog.jsonl`; a replay day of
sink is a few hundred MB and loads in seconds.

## Wheelman arcs — battles with resolved species

Ordered by rows per hour. Each is a baton that already sits where fights are dense.

| arc | baton(s) | what it yields |
|---|---|---|
| W1 wild grind | `grind_start`, `live_grind_start`, `grind_hypno`, `grind_gyarados` | volume: hundreds of `battle_outcome` + `move_result` across the local encounter pool; level gaps in both directions |
| W2 gym leaders, one each | `misty_face`, `erika_door`, `koga_asked`, `badge6-178` (Sabrina), `gym7_blaine_no_badge`, `gym8_inside` | type-varied trainer fights; the `won`/`fled` label is the badge byte |
| W3 the League | `indigo_lobby`, `e4_room1_won` … `e4_room4_won`, `champion_won` | highest-level fights; each `*_won` baton replays the next room in isolation |
| W4 Silph + Hideout | `b6_silph-178`, `b6_giovanni`, `hideout_b1`, `x5_hideout` | trainer chains in corridors; bodies that fight on approach |
| W5 Route 22/23 + Victory Road | `route22_kit`, `route23_kit`, `victory_road_1f_kit` | wild + trainer mix at high level |
| W6 Safari | `safari_in`, `safari_moving` | the no-FIGHT menu: rows for `battle-action` where the right action is ball or run, never fight |

Every wild encounter in these legs also cross-checks `scripts/encounters.py`; a species that
still prints as `#xx` after Step 0 is a decode bug and stops the arc.

## Forger arcs — what the game says back, and what cleared it

The Forger's row is *(map, cell, facing, the sentence on screen) → (gate class, the verb that
clears it)*. The label is measured from the same run: the next events after the refusal. Run
these with `--engage` so `LegRunner.recon` talks to every body on the map first; the sentences
land as `supervisor.gate_text`, `refusal`, `discovery` and `supervisor.body_engaged`.

| arc | baton(s) | gate class in `handoff_resolutions` / `GATES` |
|---|---|---|
| F1 Saffron gate guard | `saffron_gate`, `saffron_gate2`, `saffron_city` | `route_gate_guard` — "Wait up please"; cleared from the upper corridor after CUT |
| F2 Silph card key + 11F guard | `b6_silph-234`, `badge6-208`, `b6_giovanni-235` | `card_key_door`, `script_guard` ("Get out of the way") |
| F3 sleeping blockers | `route12_south`, `route16_upper`, `strength_route16-*` | `sleeping_blocker` — the POKé FLUTE from the bag |
| F4 STRENGTH boulders + plates | `seafoam_1f_str`, `seafoam_b1_str`, `strength_celadon`, `vr_1f_plate`, `vr_3f_plate_pushed` | `strength_boulder`; Victory Road's plates open doors |
| F5 SURF refusals | `fuchsia_hm03-*`, `seafoam_b3_surfing`, `route21_north`, `route21_stuck` | `surf_launch_refused`, `surf_no_landing`, `current_too_fast` |
| F6 CUT gates | `celadon_from_route7`, `erika_door` | `nothing_to_cut`; Erika's door behind the bush |
| F7 trainer challenges | `b6_silph-178`, `route12_south`, `kg2_route13`, `kg2_route14` | `trainer_challenge` — the talk that opens a fight |
| F8 story talk chains | `bill_*` (12 batons), `talk01-noroute`, `pewter_museum*`, `lab_front` → `lab_amber_given` | multi-step dialogue that hands over an item; `bag.freed` before the talk |
| F9 quiz walls | `gym7_blaine_no_badge`, `gym8_gate` | Cinnabar's quiz doors: the sentence *is* the puzzle |
| F10 stale text | `hideout_lost`, `seafoam_loop_stuck_*` | `stale_window_text` — a farewell pinned on the window is not a gate |

F1–F5 are the seed: each has a curated resolution already, so the row's label is human-checked.
F8 is the arc that grows the Forger past refusals into persuasion: who to talk to, in what
order, with what in the bag.

## Extractor arcs — puzzle-consult

| arc | baton(s) |
|---|---|
| Rocket Hideout spinners (facing in the state key) | `hideout_b1`, `hideout_items`, `x5_hideout` |
| Sabrina's pads | `badge6-10`, `badge6-181`, `badge6f2` |
| Seafoam boulders and currents | `seafoam_b3_main`, `seafoam_descent-31`, `seafoam_west_door`, `seafoam_west_descend` |
| Victory Road plates and holes | `vr_1f_open_pushed`, `vr_2f_landing_stuck`, `vr_3f_dropped` |
| Mansion switches | `mansion_1f`, `mansion_216_top`, `mansion_doors_open` |

Every consult already logs the menu, the choice and what the engine returned; the arc only
needs to run with consults on (`--no-consult` off) so the Extractor is actually seated.

## Point Man / Investigator

Every arc above is also a navigation leg. No dedicated batons: the nav rows come from
`supervisor.hop_failed` / `rerouted` / `leg_end` on whatever is replayed. Recon rows
(`supervisor.recon_pick`, `body_engaged`) are the Investigator's and come free with `--engage`.

## Order

1. Step 0, with its test.
2. W1 for an afternoon: the cheapest thousand clean battle rows.
3. F1–F5, one baton each, budget 1800 s: the Forger's seed doubles.
4. W3 the League from `indigo_lobby`: the hardest fights, one run.
5. Rebuild the corpus; the card's seat table is the scoreboard.

## Definition of done per arc

Same as the expedition skill: state banked, tape exists, sink has rows under the `run_id`, and
the converter's `stats.json` moved in the seat's domain. An arc that banks a state but adds no
rows is a wiring bug, not a data point.
