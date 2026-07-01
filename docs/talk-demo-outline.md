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
  what `--live` streams into).
- **Three terminals** pre-typed (don't type live): (1) naive run, (2) speedrun,
  (3) `bat pokedex/memory/observations.md`.
- **Clean state** so the title screen shows NEW GAME: delete `*.gb.ram` and
  `frames/`.
- **Rehearse the speedrun payoff** and record it. The speedrun reaching the
  starter is the one thing that *must* land — keep a recorded run in a browser
  tab as fallback.

### The beat sheet

Anchored to lines already in the talk. "Cue" = existing words; when you hit
them, trigger the demo.

**Beat 1 — Boot it (the world)**
- **Cue:** *"You start as a kid in your house… you have to go to Professor Oak's
  cabin to get your first Pokémon."*
- **Run:** naive agent with `--live`, clean state.
- **Audience sees:** the actual Game Boy booting in the browser, agent taking
  control.
- **Point:** no display server, no vision — it reads RAM. This is real, running
  now.

**Beat 2 — The failure (NOT talking to NPCs)**
- **Cue:** *"the challenge is I ran this for hours, and it would not get to
  anything."*
- **Audience sees:** the agent wandering, bumping walls, ignoring the mom NPC.
  Let it flail ~20–30 seconds — the failure *is* the content.
- **Point:** *"I forgot to tell the agent to talk to people."* Land the laugh
  line: *"it was politely hallucinating progress."*
- **Fallback:** this one is *supposed* to look bad, so it's low-risk. If it
  accidentally succeeds, cut early.

**Beat 3 — Talk to mom (the first context clue)**
- **Cue:** *"one of the first context clues… talk to your mom, and your mom will
  tell you exactly where to go."*
- **Audience sees:** agent facing mom, `A` to talk, dialogue clears, direction
  acquired. (In `agent.py` this is the `return "a"` — talk to clear dialogue;
  the early-game scripted targets then route Red's room → house → Pallet →
  Oak's lab.)
- **Point:** the rule you set — *no internet, self-learned inside the game.* The
  knowledge had to come from *observing*, not from a walkthrough.

**Beat 4 — The learning, made visible**
- **Cue:** *"I just dump all the context into a tapes database… this memory
  folder… observations in Markdown."*
- **Show live:** `pokedex/memory/observations.md` — the human-readable notes the
  agent wrote. Scroll it on screen.
- **Then the observer state:** land the best real detail — *"through a door, it
  takes seven seconds of cooldown… you can't spam back and forth."* Show that
  this is a *learned* fact, written down, fed back.
- **Point:** *"we never learned from the last five hours of sessions"* — this is
  the whole thesis in one artifact. Also gesture at `pokedex/log*.md` (hundreds
  of session logs) and `evolve_results.json` for scale.

**Beat 5 — The speedrun (the "after" — the payoff demo)**
- **Cue:** *"a self-healing loop to eventually get the three seconds to
  Pokémon."*
- **Run:** the speedrun (`route1-speedrun-demo` skill / clean run with
  `--live`).
- **Audience sees:** same opening as Beat 1, but now it *blasts* through — spams
  `A` through intro/naming, walks straight to Oak, picks the starter.
- **Point:** land the nuance you earned: *"you don't hit A to pick your Pokémon,
  you hit B — he asks yes/no, you hit B to confirm."* That single detail *came
  from the observations in Beat 4.* Explicitly connect them: "that B-instead-of-A?
  That's in the observations file I just showed you."
- **This closes the loop on stage:** failure (2) → observation (4) → solved (5).
  The audience *watched* the learning pay off.

### Why this ordering works

The talk builds to "all the Pokémon turned into Sweeper Agent" and then the
tapes/fine-tuning arc. Beats 1–5 give a **concrete, watched-it-happen**
foundation in the first 10 minutes, so when you later say "10 parallel agents,
77k sessions, fine-tune a 4B model," the audience is scaling up something they
*saw work small* — not taking it on faith. The observations file in Beat 4 is
also the setup for the "observational memory / check the tapes" section later.

### The one risk to manage

A from-scratch live run reaching the starter is the only fragile beat. Two ways
to de-risk, pick one:

- **(a) Live with a net (recommended):** rehearse it cold; if it reliably
  reaches the starter in rehearsal, run it live with the recorded run one tab
  away.
- **(b) Guaranteed:** play the recorded speedrun for Beat 5 and narrate over it —
  zero stage risk, slightly less magic.

---

## The rest of the hour (after the 20-min intro)

The four thesis claims escalate — each is a bigger loop wrapped around the last.
Structure the demo half as "the learning loop keeps getting bigger."

| Segment | Min | Mode |
|---|---|---|
| Intro (existing) | 20 | slides |
| Act 1 — It plays | 8 | **live** `--live` viewer |
| Act 2 — It streams | 7 | **live** Confluent |
| Act 3 — It learns across sessions | 8 | artifacts + 1 live |
| Act 4 — It trains weights | 10 | pre-baked + live inference |
| Act 5 — The wall + what's next | 7 | slides + `/goal` vision |
| Buffer / Q&A | ~ | — |

### Act 1 — "It plays" (the honest baseline)

Start with no ML at all. Python reading GameBoy RAM and pressing buttons. Set
the expectation that the intelligence comes *later*.

```bash
uv run python -m viewer            # localhost:8200, audience-facing
uv run python scripts/agent.py rom/pokemon_red.gb --strategy heuristic --live
```

**Land:** memory-reading beats vision — exact decisions, ~100× real-time
headless. This speed is *why* the later learning loops are feasible.

### Act 2 — "It streams" (data at scale — the Kafka thesis)

One agent is a toy. Stream every event and you have a dataset — the substrate
everything after is built on. Use the `route1-speedrun-demo` skill: 20-battle
Route 1 run streaming to Confluent Cloud. Split screen: agent left, Confluent
`pokemon.game.events` topic right.

**Land:** the event schema is the contract. Everything downstream reads these
same JSONL/Kafka events. **Pre-run once** so the topic already has messages.

### Act 3 — "It learns across sessions" (AlphaEvolve)

Now you have hundreds of runs. What can history tell you that one run can't?

1. **Historical Observer** — `scripts/historical_observer.py` runs DuckDB over
   the JSONL fitness logs and extracts cross-run patterns. Fast; produces a
   markdown insights file.
2. **AlphaEvolve loop** — `scripts/evolve.py`. The agent is steered by **12
   numbers** (the genome); evolve runs headless rollouts → fitness → LLM
   proposes better numbers → keep improvements.

**Land:** the observer's insights *feed* the evolve loop's proposer. Local-first
analytics (DuckDB on disk today, Kafka-backed tomorrow, same queries).

### Act 4 — "It trains weights" (autotune — the crescendo)

Switch to `../autotune`. The reveal: **the weights don't play Pokémon.** They're
a fine-tuned local model whose only job is to output the 12-number genome. Draw
the loop: **Try → Check → Reward → Nudge**.

Do **not** train live — 300 LoRA iters is unwatchable. Use the already-trained
adapter in `out/sft/adapters.safetensors`.

```bash
uv run python -m autotune.generate --prompt-beat route1
```

**Land, in order:**
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
| AlphaEvolve + Factorio part of the algorithm | **Real (AlphaEvolve), unverified (Factorio)** | `scripts/evolve.py` opens "AlphaEvolve-inspired". No Factorio reference in code — present it as analogy only. |
| `/goal` will incorporate all the learnings | **Aspirational** | No `/goal` skill or command exists in either repo. Roadmap, not demo. |
| We have weights in `../autotune` | **Real, on disk** | `autotune/out/sft/adapters.safetensors` + checkpoints (100/200/300 iters), base `smollm3-3b-mlx`, trained locally via MLX. |
