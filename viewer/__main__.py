"""Launch the Pokédex Viewer server."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn

from viewer.live import LiveHub
from viewer.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Pokédex Viewer")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--alerts", default="pokedex/memory/alerts.jsonl")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    app = create_app(
        Path(args.runs_dir),
        alerts_path=Path(args.alerts),
        hub=LiveHub(),
    )
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
