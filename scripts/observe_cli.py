"""CLI wrapper for the observational memory observer.

Usage:
    python3 scripts/observe_cli.py [--memory-dir DIR] [--dry-run] [--session ID] [--reset]
"""

import argparse
import os
from pathlib import Path

from observer import Observer
from paper_reader import TapeReader


def detect_memory_dir() -> str:
    return str(Path(os.getcwd()) / "pokedex" / "memory")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Distill Paper sessions into observational memory"
    )
    parser.add_argument(
        "--memory-dir",
        help="Directory for observations output (default: pokedex/memory/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print observations without writing to disk",
    )
    parser.add_argument(
        "--session",
        help="Process a single session ID only",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear watermark and reprocess all sessions",
    )

    args = parser.parse_args(argv)
    memory_dir = args.memory_dir or detect_memory_dir()

    # db_path is unused by paper_reader but Observer still accepts it
    observer = Observer(db_path="", memory_dir=memory_dir)

    if args.reset:
        if observer.state_path.exists():
            observer.state_path.unlink()
        print("Watermark cleared.")

    if args.session:
        session = observer.reader.read_session(args.session)
        observations = observer.observe_session(session)
    else:
        if args.dry_run:
            sessions = observer.get_unprocessed_sessions()
            observations = []
            for sid in sessions:
                session = observer.reader.read_session(sid)
                observations.extend(observer.observe_session(session))
        else:
            observations = observer.run()

    if args.dry_run or args.session:
        for obs in observations:
            print(
                f"[{obs.priority}] {obs.content} "
                f"(session: {obs.source_session[:8]})"
            )
        print(f"\n{len(observations)} observation(s) found.")
    else:
        print(f"Wrote {len(observations)} observation(s) to {observer.observations_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
