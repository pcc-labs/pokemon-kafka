# Demo runs

Curated, replayable agent runs for the 9-beat talk demo. Committed so a fresh
clone can replay them without an emulator, ROM, or API key.

## Replay

```bash
uv run python -m viewer --runs-dir demo-runs   # http://localhost:8200
```

Each beat has a stable folder (`beat1-…` … `beat9-…`) and a label starting with
its beat number, so the viewer's beat routes resolve: open `/3` to jump straight
to beat 3.

Beats 7–9 are the "harder frontier" set — see the `forest-navigation-demo`,
`bug-catcher-demo`, and `discovery-signs-demo` skills:

- `beat7-forest-nav` — maps Viridian Forest under 9x10 visibility (navigation is hard)
- `beat8-bug-hunt` — type-effective bug battles on Route 2 into the forest
- `beat9-discovery` — decodes signs / dialogue / Pokedex flavor into `discovery` events

## Not committed

`demo-runs/states/` holds the PyBoy savestates used to record beats 3–8. They
are gitignored (binary, regenerable) — recording is a local/presenter step, the
frames are the shipped artifact.
