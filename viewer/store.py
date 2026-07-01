"""Read-only index over the runs/ directory."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class RunSummary:
    run_id: str
    status: str
    turns: int
    battles_won: int
    maps_visited: int
    badges: int
    frame_count: int
    thumbnail: str | None
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RunStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    def _run_dirs(self) -> list[Path]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(
            (p for p in self.runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )

    def get_summary(self, run_id: str) -> dict:
        path = self.runs_dir / run_id / "summary.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}

    def frame_names(self, run_id: str) -> list[str]:
        frames = self.runs_dir / run_id / "frames"
        if not frames.is_dir():
            return []
        return sorted(p.name for p in frames.glob("*.png"))

    def load_events(self, run_id: str) -> list[dict]:
        path = self.runs_dir / run_id / "events.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def get_meta(self, run_id: str) -> dict:
        path = self.runs_dir / run_id / "meta.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}

    def _summary_for(self, run_id: str) -> RunSummary:
        summary = self.get_summary(run_id)
        frames = self.frame_names(run_id)
        status = "done" if (self.runs_dir / run_id / "summary.json").exists() else "live"
        label = self.get_meta(run_id).get("label") or summary.get("params", {}).get("label", "")
        return RunSummary(
            run_id=run_id,
            status=status,
            turns=int(summary.get("turns", 0)),
            battles_won=int(summary.get("battles_won", 0)),
            maps_visited=int(summary.get("maps_visited", 0)),
            badges=int(summary.get("badges", 0)),
            frame_count=len(frames),
            thumbnail=frames[-1] if frames else None,
            label=str(label or ""),
        )

    def list_runs(self) -> list[RunSummary]:
        return [self._summary_for(p.name) for p in self._run_dirs()]
