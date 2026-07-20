"""Run healer.py check on a recorded run — backs the viewer's HEAL button.

A run folder already holds everything a heal needs: summary.json is the run's
fitness (healer ignores the extra params/run_id keys) and names the ROM in
params.rom. Races take minutes of emulation, so jobs run on a daemon thread
and the UI polls for the verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

_HEALER_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "healer.py"


class HealJobs:
    """One healer subprocess per run_id, with an injectable runner for tests."""

    def __init__(self, runs_dir, runner=subprocess.run, healer_script=_HEALER_SCRIPT, background=True):
        self.runs_dir = Path(runs_dir)
        self.runner = runner
        self.healer_script = Path(healer_script)
        self.background = background
        self.jobs: dict[str, dict] = {}

    def status(self, run_id: str) -> dict:
        return self.jobs.get(run_id, {"state": "idle", "verdict": None})

    def start(self, run_id: str, force: bool = False) -> dict:
        if self.jobs.get(run_id, {}).get("state") == "running":
            return self.jobs[run_id]

        summary_path = self.runs_dir / run_id / "summary.json"
        if not summary_path.is_file():
            self.jobs[run_id] = {"state": "error", "verdict": "run has no summary.json yet (still live?)"}
            return self.jobs[run_id]

        rom = (json.loads(summary_path.read_text()).get("params") or {}).get("rom")
        if not rom or not Path(rom).exists():
            self.jobs[run_id] = {"state": "error", "verdict": f"rom not found: {rom}"}
            return self.jobs[run_id]

        cmd = [
            sys.executable,
            str(self.healer_script),
            "check",
            "--fitness",
            str(summary_path),
            "--rom",
            str(rom),
        ]
        if force:
            cmd += ["--cooldown-hours", "0"]

        self.jobs[run_id] = {"state": "running", "verdict": None}
        if self.background:
            threading.Thread(target=self._work, args=(run_id, cmd), daemon=True).start()
        else:
            self._work(run_id, cmd)
        return self.jobs[run_id]

    def _work(self, run_id: str, cmd: list[str]) -> None:
        try:
            proc = self.runner(cmd, capture_output=True, text=True)
            lines = [ln for ln in (proc.stdout or "").splitlines() if "[healer]" in ln]
            verdict = lines[-1].split("[healer]", 1)[1].strip() if lines else "no healer output"
            self.jobs[run_id] = {"state": "done", "verdict": verdict}
        except Exception as exc:
            self.jobs[run_id] = {"state": "error", "verdict": str(exc)}
