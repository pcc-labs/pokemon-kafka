"""Per-run recorder: writes self-contained runs/<id>/ folders for the viewer."""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:  # Pillow is a project dep; typing only.
    from PIL.Image import Image
except Exception:  # pragma: no cover - import guard
    Image = object  # type: ignore


class RunRecorder:
    def __init__(
        self,
        run_id: str,
        runs_dir: Path,
        frame_grabber: Optional[Callable[[], "Image"]] = None,
        frame_interval: int = 10,
        live: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.run_id = run_id
        self.run_dir = Path(runs_dir) / run_id
        self.frames_dir = self.run_dir / "frames"
        self.frame_grabber = frame_grabber
        self.frame_interval = max(1, int(frame_interval))
        self.live = live
        self._params: dict = {}
        self._events_fh = None

    @staticmethod
    def new_run_id(now: datetime, suffix: str) -> str:
        return f"{now.strftime('%Y%m%d-%H%M%S')}-{suffix}"

    def start(self, params: dict) -> None:
        self._params = dict(params)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        # Write the label up front so the viewer can show it while the run is still live
        # (summary.json only exists once the run finishes).
        label = str(params.get("label", "")).strip()
        if label:
            (self.run_dir / "meta.json").write_text(json.dumps({"label": label}))
        self._events_fh = open(self.run_dir / "events.jsonl", "a", encoding="utf-8")

    def on_event(self, event: dict) -> None:
        if self._events_fh is not None:
            self._events_fh.write(json.dumps(event) + "\n")
            self._events_fh.flush()
        if self.live is not None:
            self.live({"type": "event", **event})
        turn = int(event.get("turn", 0))
        if self.frame_grabber is not None and turn % self.frame_interval == 0:
            self.capture_frame(turn)

    def capture_frame(self, turn: int) -> None:
        if self.frame_grabber is None:
            return
        img = self.frame_grabber()
        img.save(self.frames_dir / f"{turn:06d}.png")
        if self.live is not None:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self.live({"type": "frame", "turn": turn, "png_b64": base64.b64encode(buf.getvalue()).decode()})

    def finish(self, summary: dict) -> None:
        payload = dict(summary)
        payload["params"] = self._params
        payload["run_id"] = self.run_id
        (self.run_dir / "summary.json").write_text(json.dumps(payload, indent=2))
        if self.live is not None:
            self.live({"type": "done"})
        if self._events_fh is not None:
            self._events_fh.close()
            self._events_fh = None
