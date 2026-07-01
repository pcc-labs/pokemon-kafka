# Demo runs

Curated, replayable agent runs for the 6-beat talk demo. Committed so a fresh
clone can replay them without an emulator, ROM, or API key.

## Replay

```bash
uv run python -m viewer --runs-dir demo-runs   # http://localhost:8200
```

Each beat has a stable folder (`beat1-…` … `beat6-…`) and a label starting with
its beat number, so the viewer's beat routes resolve: open `/3` to jump straight
to beat 3.

## Not committed

`demo-runs/states/` holds the PyBoy savestates used to record beats 3–6. They
are gitignored (binary, regenerable) — recording is a local/presenter step, the
frames are the shipped artifact.
