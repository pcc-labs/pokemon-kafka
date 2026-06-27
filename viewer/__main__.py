"""Launch the Pokédex Viewer server."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import uvicorn

from viewer.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Pokédex Viewer")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--observations", default="pokedex/memory/observations.md")
    parser.add_argument("--alerts", default="pokedex/memory/alerts.jsonl")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    obs = Path(args.observations) if Path(args.observations).exists() else None
    alerts = Path(args.alerts) if Path(args.alerts).exists() else None
    app = create_app(Path(args.runs_dir), observations_path=obs, alerts_path=alerts)
    if not args.no_open:
        webbrowser.open(f"http://localhost:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
