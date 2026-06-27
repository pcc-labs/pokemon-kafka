"""Tests for the autotune_bridge (L2 notes genome + L3 local-model proposer)."""

from unittest.mock import MagicMock, patch

import autotune_bridge
from autotune_bridge import (
    build_generate_cmd,
    load_genome_from_notes,
    make_local_llm_fn,
)

GENOME_BLOCK = '# Notes\n<!-- autotune:genome\n{"stuck_threshold": 5, "door_cooldown": 12}\n-->\n'


def test_load_genome_missing_file(tmp_path):
    assert load_genome_from_notes(tmp_path / "nope.md") == {}


def test_load_genome_no_block(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("# Notes\nnothing here\n")
    assert load_genome_from_notes(p) == {}


def test_load_genome_invalid_json(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("<!-- autotune:genome\n{not json}\n-->\n")
    assert load_genome_from_notes(p) == {}


def test_load_genome_valid_and_clamped(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text(GENOME_BLOCK)
    genome = load_genome_from_notes(p)
    assert genome["stuck_threshold"] == 5
    assert genome["door_cooldown"] == 12


def test_load_genome_filters_unknown_keys(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text('<!-- autotune:genome\n{"stuck_threshold": 5, "bogus": 1}\n-->\n')
    genome = load_genome_from_notes(p)
    assert "bogus" not in genome
    assert genome["stuck_threshold"] == 5


def test_load_genome_last_block_wins(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text(
        '<!-- autotune:genome\n{"stuck_threshold": 3}\n-->\n<!-- autotune:genome\n{"stuck_threshold": 7}\n-->\n'
    )
    assert load_genome_from_notes(p)["stuck_threshold"] == 7


def test_autotune_dir_env_override(monkeypatch):
    monkeypatch.setenv("AUTOTUNE_DIR", "/custom/autotune")
    assert str(autotune_bridge._autotune_dir()) == "/custom/autotune"


def test_autotune_dir_default(monkeypatch):
    monkeypatch.delenv("AUTOTUNE_DIR", raising=False)
    # Defaults to a sibling "autotune" directory.
    assert autotune_bridge._autotune_dir().name == "autotune"


def test_build_generate_cmd_with_adapter(tmp_path):
    cmd = build_generate_cmd("model-x", str(tmp_path), "hello")
    assert "--adapter-path" in cmd
    assert "--model" in cmd and "model-x" in cmd
    assert cmd[-2:] == ["--max-tokens", "256"]


def test_build_generate_cmd_without_adapter(tmp_path):
    assert "--adapter-path" not in build_generate_cmd("m", None, "hi")
    # Nonexistent adapter path is also skipped.
    assert "--adapter-path" not in build_generate_cmd("m", str(tmp_path / "absent"), "hi")


def test_make_local_llm_fn_success(monkeypatch):
    monkeypatch.setenv("AUTOTUNE_BASE_MODEL", "m")
    monkeypatch.setenv("AUTOTUNE_ADAPTER_PATH", "/no/adapter")
    fn = make_local_llm_fn()
    proc = MagicMock()
    proc.stdout = '{"stuck_threshold": 4}'
    with patch("autotune_bridge.subprocess.run", return_value=proc) as run:
        out = fn("propose a genome")
    assert out == '{"stuck_threshold": 4}'
    run.assert_called_once()


def test_make_local_llm_fn_handles_failure():
    fn = make_local_llm_fn(model="m", adapter=None)
    with patch("autotune_bridge.subprocess.run", side_effect=OSError("boom")):
        assert fn("prompt") is None
