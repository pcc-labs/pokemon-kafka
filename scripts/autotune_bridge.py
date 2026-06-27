"""autotune_bridge — consume the autotune training loop's learnings inside pokemon-kafka.

Two integration points back from autotune (../autotune):

  - load_genome_from_notes: read the genome block autotune writes into notes.md and use it as
    the agent's EVOLVE_PARAMS baseline (L2). The agent reads this at startup; the EVOLVE_PARAMS
    env var still overrides it.
  - build_generate_cmd / make_local_llm_fn: drive autotune's locally-trained MLX model as the
    parameter-mutation proposer in evolve.py (L3), replacing the Anthropic call.

autotune writes the genome block as:

    <!-- autotune:genome
    {"stuck_threshold": 8, ...}
    -->
"""

import json
import os
import re
import subprocess
from pathlib import Path

from evolve import DEFAULT_PARAMS, clamp_params

_GENOME_BLOCK_RE = re.compile(r"<!-- autotune:genome\s*(\{.*?\})\s*-->", re.DOTALL)

_DEFAULT_MODEL = "EricFillion/smollm3-3b-mlx"
_GENERATE_TIMEOUT_S = 300


def load_genome_from_notes(notes_path) -> dict:
    """Parse the latest autotune genome block from notes.md.

    Returns a clamped genome restricted to known parameters, or ``{}`` when the file is
    missing, has no block, or the block is invalid.
    """
    path = Path(notes_path)
    if not path.exists():
        return {}
    matches = _GENOME_BLOCK_RE.findall(path.read_text())
    if not matches:
        return {}
    try:
        genome = json.loads(matches[-1])  # last block wins; regex guarantees a JSON object
    except json.JSONDecodeError:
        return {}
    known = {k: v for k, v in genome.items() if k in DEFAULT_PARAMS}
    return clamp_params(known)


def _autotune_dir() -> Path:
    """Locate the sibling autotune repo (override with AUTOTUNE_DIR)."""
    override = os.environ.get("AUTOTUNE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "autotune"


def build_generate_cmd(model: str, adapter: str | None, prompt: str) -> list[str]:
    """Build the ``mlx_lm generate`` command (pure, for testing)."""
    cmd = ["uv", "run", "python", "-m", "mlx_lm", "generate", "--model", model]
    if adapter and Path(adapter).exists():
        cmd += ["--adapter-path", str(adapter)]
    cmd += ["--prompt", prompt, "--max-tokens", "256"]
    return cmd


def make_local_llm_fn(model: str | None = None, adapter: str | None = None):
    """Return an ``llm_fn(prompt) -> str | None`` backed by autotune's local MLX model.

    Drop-in replacement for evolve.py's Anthropic ``llm_fn`` — no API key required.
    """
    model = model or os.environ.get("AUTOTUNE_BASE_MODEL", _DEFAULT_MODEL)
    adapter = adapter or os.environ.get("AUTOTUNE_ADAPTER_PATH", str(_autotune_dir() / "out" / "sft"))
    autotune_dir = _autotune_dir()

    def llm_fn(prompt: str):
        cmd = build_generate_cmd(model, adapter, prompt)
        try:
            proc = subprocess.run(
                cmd, cwd=str(autotune_dir), capture_output=True, text=True, timeout=_GENERATE_TIMEOUT_S
            )
            return proc.stdout
        except (subprocess.SubprocessError, OSError):
            return None

    return llm_fn
