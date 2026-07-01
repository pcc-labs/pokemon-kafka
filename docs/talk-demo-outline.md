# Hour-Long Demo: Training AI on Your Own Code

Outline + narrative for interweaving a live Pokémon-agent demo into the opening
of the talk. Not a script — beats, demo cues, and the point to land at each
stage.

## The spine (one sentence for the whole hour)

"An agent that plays Pokémon, streams everything it does, mines its own history,
and trains a local model from it — and the most honest thing I learned is
*where that loop stops working.*"

That last clause is the differentiator. Most demos hide the wall. This one *is*
the thesis.

## How the demo is driven

**Everything on stage is driven by prompts into Claude Code — not `uv`
commands.** Claude Code is the general-purpose harness; you prompt it, it invokes
the skills / runs the agent under the hood, and the agent streams into the live
viewer. This *is* the thesis in miniature: the harness is generic, the prompts do
the work, and the value is in the recorded sessions.

- The `uv` commands below are what Claude Code executes under the hood — shown so
  you know what's happening, not what you type.
- What you type is a **prompt** (or a skill invocation like
  `/route1-speedrun-demo`). Those are the "Prompt" lines in each beat.
- Infra you start once, yourself, before the audience is watching: the viewer
  (`uv run python -m viewer` → localhost:8200). Everything after is prompts.

---

## Interweaving the demo into the intro

The demo isn't a separate section — it *is* the Pokémon story, shown live
instead of through screenshots. Every sentence already in the talk maps to
something on screen. The spine is a **before → learning → after** contrast, all
in the first ~10 minutes:

- **Before:** the naive agent wanders and won't talk to NPCs ("politely
  hallucinating progress").
- **The bridge:** the observations it wrote (`observations.md` + observer state)
  — the learning made visible.
- **After:** the 3-seconds-to-Pokémon speedrun — the same problem, solved.

### Setup before you walk on

- **Viewer up, projected:** `uv run python -m viewer` → localhost:8200 (this is
  what the agent streams into via `--live`). Start it yourself; it's infra.
- **Claude Code open and projected** — this is your driving surface. The audience
  should see you *prompt* the harness, not run scripts.
- **Clean state** so the title screen shows NEW GAME: delete `*.gb.ram` and
  `frames/`.
- **Rehearse the speedrun payoff** and record it. The speedrun reaching the
  starter is the one thing that *must* land — keep a recorded run in a browser
  tab as fallback.

### The notes are the star (say this out loud)

The through-line for all six beats: **every fix the agent found is written down
in its own notes, and those notes are what turn failure into a speedrun.** Two
artifacts, shown repeatedly — not once:

- `pokedex/memory/observations.md` — the human-readable journal the agent writes
  at the end of sessions ("talk to NPCs", "hit B to pick a starter", "flee to
  keep moving"). This is observational memory.
- `pokedex/log*.md` — the per-session logs (`log1`–`log6` are committed as
  examples; hundreds more are local run output). The raw record each observation
  is distilled from.

The demo's real argument isn't "the agent plays Pokémon" — it's *"the agent kept
notes, and the notes compounded."* Keep coming back to these files. Every beat
below ends by pointing at the note that made it possible.

### The beat sheet (6 beats)

Each beat introduces **one** piece of learned knowledge, and each one lives in
the notes. Anchored to lines already in the talk. "Cue" = existing words; when
you hit them, prompt Claude Code.

**Beat 1 — Professor Oak's cabin, no help (the failure state)**
- **Cue:** *"You start as a kid in your house… you have to go to Professor Oak's
  cabin to get your first Pokémon."*
- **Prompt:** "Boot Pokémon Red from a new game in the live viewer. No hints —
  just try to reach Professor Oak's lab."
- **Under the hood:** `scripts/agent.py … --live`, discovery/NPC help *off*.
- **Audience sees:** the agent wandering, bumping walls, ignoring NPCs. Let it
  flail ~20–30s — the failure *is* the content.
- **The note:** open a failing `pokedex/log*.md` — the wall got *recorded*. "It
  didn't just fail, it wrote down that it failed."
- **Point:** *"I forgot to tell the agent to talk to people."* Land the laugh:
  *"it was politely hallucinating progress."* Low-risk beat — it's meant to look
  bad.

**Beat 2 — Discovery engine: talk to NPCs**
- **Cue:** *"one of the first context clues… talk to your mom, and your mom will
  tell you exactly where to go."*
- **Prompt:** "Turn on discovery — talk to NPCs and read what they say — then head
  to Oak's lab."
- **Under the hood:** discovery capture decodes on-screen NPC dialogue & signs
  into context (`return "a"` clears dialogue; scripted targets then route Red's
  room → house → Pallet → Oak's lab).
- **Audience sees:** agent faces mom, reads the clue, routes correctly.
- **The note:** the observation "NPCs give directions — always talk to them" in
  `observations.md`. *This* is the moment the failure became a written rule.
- **Point:** the rule you set — *no internet, self-learned inside the game.* The
  knowledge came from *observing and writing it down*, not a walkthrough.

**Beat 3 — Choose your Pokémon (door cooldown + B, not A)**
- **Cue:** *"you don't hit A when you pick your Pokémon, you hit B."*
- **Prompt:** "Get to the Pokéball table and pick a starter."
- **The two gotchas, both from the notes:**
  - **Door cooldown** — the observer learned you can't spam through doors; there's
    a cooldown (the `door_cooldown` knob exists *because* the notes flagged
    door-spam). Show the observer state.
  - **B, not A** — picking a starter is a yes/no confirm, so `A` loops you; `B`
    confirms. This exact detail is in `observations.md`.
- **Audience sees:** agent reaches the table, handles the door, confirms with `B`,
  walks out with a Pokémon.
- **Point:** two tiny, non-obvious rules that no walkthrough gave it — it found
  them by playing and *saved them*. Payoff of note-taking, made literal.

**Beat 4 — First battle (battle knowledge)**
- **Cue:** *"when you're below a certain HP, you should probably eat a berry, or
  switch Pokémon… a lot of nuance I had to have the agent learn."*
- **Prompt / state:** load `first_battle.state`, then "win this battle."
- **Under the hood:** `--load-state first_battle.state`; battle heuristics score
  moves (`unknown_move_score`, `status_move_score` genome knobs), type chart in
  `references/type_chart.json`.
- **Audience sees:** the agent reading the battle, picking an effective move.
- **The note:** battle-mechanics observations + the per-battle win/loss rows
  behind the win-probability model. "It's not guessing which move — it learned
  matchups and wrote them down."
- **Point:** battle competence is another learned layer, not baked in.

**Beat 5 — Complete Route 1 by fleeing**
- **Cue:** *"get to the point of battling… a lot of nuance about the game."*
- **Prompt / state:** load `route1.state`, then "cross Route 1 — don't waste turns,
  flee wild battles."
- **Under the hood:** `--load-state route1.state`; flee logic + `hp_run_threshold`
  keep it moving.
- **Audience sees:** the agent *declining* fights to traverse the route fast.
- **The note:** the observation "flee to preserve HP and make progress." Fleeing
  is a *strategy the agent recorded*, not cowardice.
- **Point:** sometimes the right move is to *not* fight. Sets up the reversal.

**Beat 6 — Level up and never flee (the reversal)**
- **Cue:** *"a self-healing loop… I had the agent learn when to fight."*
- **Prompt / state:** load `route1.state`, then "grind Route 1 — fight everything,
  level up." (This is the `/route1-speedrun-demo` grind loop.)
- **Audience sees:** *same starting state as Beat 5*, opposite behavior — it now
  *seeks* battles and levels up.
- **The note:** the observation about *when* to fight vs. flee — context decides.
  Beats 5 and 6 start from the identical `route1.state`; the only difference is
  which learned rule applies.
- **Point (the closer):** the "right" behavior is context-dependent, and the
  agent only knows the difference *because it wrote both rules down.* Failure (1)
  → discovery (2) → saved rules (3–6) → the notes are the product.

### Why this ordering works

Six beats, one argument: **the notes compound.** Each beat adds a rule to
`observations.md`, and by Beat 6 the same starting state produces the *opposite*
correct behavior depending on which saved rule fires. When you later say "10
parallel agents, 77k sessions, fine-tune a 4B model," the audience is scaling up
something they *watched* work small — and they already believe the notes are the
asset, because they saw six of them earn their keep.

### The one risk to manage

Beats 1–3 run from a fresh NEW GAME, so reaching the lab live is the only fragile
stretch. Beats 4–6 load a pinned savestate (`first_battle.state`, `route1.state`)
so they're deterministic. Two ways to de-risk the from-scratch beats:

- **(a) Live with a net (recommended):** rehearse cold; run live with a recorded
  run one tab away.
- **(b) Guaranteed:** capture an "at Oak's lab" savestate and load it for Beat 3
  so even the starter pick is deterministic (see `docs/worktree.md`).

---

## The rest of the hour (after the 20-min intro)

The four thesis claims escalate — each is a bigger loop wrapped around the last.
Structure the demo half as "the learning loop keeps getting bigger." Everything
is still driven by prompts into Claude Code.

| Segment | Min | Mode |
|---|---|---|
| Intro (existing) | 20 | slides + interwoven demo (beats above) |
| Act 1 — It plays | 8 | **live** (prompt → `--live` viewer) |
| Act 2 — It streams | 7 | **live** Confluent |
| Act 3 — It learns across sessions | 8 | 10 bots in 36s + artifacts |
| Act 4 — It trains weights | 10 | pre-baked + live inference |
| Act 5 — The wall + what's next | 7 | slides + `/goal` vision |
| Buffer / Q&A | ~ | — |

### Act 1 — "It plays" (the honest baseline)

Covered by the interwoven beats. The point that carries forward: no ML yet —
Python reading GameBoy RAM and pressing buttons, driven by a prompt. The
intelligence comes *later*. Memory-reading beats vision (exact decisions, ~100×
real-time headless), and that speed is *why* the later learning loops are
feasible.

### Act 2 — "It streams" (data at scale — the Kafka thesis)

One agent is a toy. Stream every event and you have a dataset — the substrate
everything after is built on.

- **Prompt / skill:** `/route1-speedrun-demo` again, but this time point at the
  Confluent side. Split screen: agent left, Confluent `pokemon.game.events`
  topic right.
- **Land:** the event schema is the contract. Everything downstream reads these
  same JSONL/Kafka events. **Pre-run once** so the topic already has messages.

### Act 3 — "It learns across sessions" (AlphaEvolve — "10 bots in 36 seconds")

This is where the blog post lands. Read the AlphaEvolve idea aloud: *"treat the
source as a genome, use an LLM to propose mutations, evaluate against a fitness
metric, keep the best."*

- **Prompt:** "Race 10 parameter variants headless and show me which door
  cooldown wins." (Under the hood: `run_10_agents.py` / `evolve.py` launch 10
  subprocesses with different `EVOLVE_PARAMS`.)
- **The money result:** the guess was `door_cooldown=8`; racing 10 variants found
  `door_cooldown=4` wins (9 stuck events vs 11 baseline vs 16 for a long
  cooldown). Land the line: **"That's not a guess. That's a measurement."**
- **The honest lesson from the post:** *"Running agents is easy. Defining what
  'good' means is not."* The fitness function is the hard part. Manual watching
  discovers *new capabilities*; evolution only *tunes* existing heuristics — and
  the door-cooldown knob only existed because Beat 4's observation noticed the
  door-spam problem first.
- **Then Historical Observer** (`scripts/historical_observer.py`): DuckDB over the
  JSONL fitness logs extracts cross-run patterns and feeds the evolve proposer.
  Local-first analytics (DuckDB on disk today, Kafka-backed tomorrow, same
  queries).

### Act 4 — "It trains weights" (autotune — the crescendo)

Switch to `../autotune`. The reveal: **the weights don't play Pokémon.** They're
a fine-tuned local model whose only job is to output the 12-number genome. Draw
the loop: **Try → Check → Reward → Nudge**.

Do **not** train live — 300 LoRA iters is unwatchable. Use the already-trained
adapter in `out/sft/adapters.safetensors`.

- **Prompt:** "Ask the trained local model to propose a genome for the Route 1
  beat." (Under the hood: `autotune.generate --prompt-beat route1`.)
- **Land, in order:**
  - LoRA = freeze the 3B base, train tiny rank-16 adapters (few MB, trains on a
    Mac via MLX). Show `adapter_config.json`: rank 16, 300 iters, smollm3-3b.
  - Three ways learnings flow back: L1 best genome (`out/best_genome.json`), L2
    `notes.md` block the live agent reads at startup, L3 local model as proposer.

### Act 5 — "The wall" (the strongest slide) + what's next

The part most people would hide. Read `autotune/docs/experiment-findings.md`
aloud: the harness works, but on early-game tasks **the reward saturated** —
every rollout scored the same 5.0, so "do more of what passed" had nothing to
select on. Validation loss fell (5.3→3.0) but the model only learned the
*output format*, not a better *strategy*.

Say the line verbatim: **"The walls are experimental-design walls, not code
bugs."** Then the three compounding causes: Route 1 already solved by baked-in
heuristics; save states replay deterministically → zero variance; 12-param
genome has low leverage.

**Pivot to `/goal`:** be honest it doesn't exist yet. Frame it as the roadmap —
the piece that would incorporate all these learnings into a single goal-directed
driver, aimed at tasks where there's actually a *gap* to close (Brock, forest
nav) so the loop has signal.

---

## Thesis validation (fact-check for the slides)

| Claim | Status | Evidence |
|---|---|---|
| Agent plays Pokémon and learns from past sessions | **Real** | `scripts/historical_observer.py` (DuckDB over JSONL); L1/L2/L3 seams persist learnings into live play. Caveat: learns via a 12-param genome, not weights-from-scratch. |
| AlphaEvolve + Factorio part of the algorithm | **Real (AlphaEvolve), unverified (Factorio)** | `scripts/evolve.py` opens "AlphaEvolve-inspired"; blog frames source-as-genome. No Factorio reference in code — present it as analogy only. |
| `/goal` will incorporate all the learnings | **Aspirational** | No `/goal` skill or command exists in either repo. Roadmap, not demo. |
| We have weights in `../autotune` | **Real, on disk** | `autotune/out/sft/adapters.safetensors` + checkpoints (100/200/300 iters), base `smollm3-3b-mlx`, trained locally via MLX. |

## Source material

- Blog: "What I Learned Running 10 Pokemon Bots in 36 Seconds" (2026-03-10) —
  AlphaEvolve framing, the `door_cooldown=4` "measurement not a guess" result,
  and "the fitness function is the hard part."
