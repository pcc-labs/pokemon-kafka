"""Tests for observe_cli.py."""

import json

import observer as obs_module
from observe_cli import detect_memory_dir, main
from paper_reader import TapeEntry, TapeSession


class FakeTapeReader:
    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    def list_sessions(self):
        return list(self._sessions.keys())

    def read_session(self, session_id):
        return self._sessions.get(session_id, TapeSession(session_id=session_id))


def _make_session(session_id="root1", text="fix the bug"):
    return TapeSession(
        session_id=session_id,
        entries=[TapeEntry(type="user", text_content=text, timestamp="2026-03-09T10:00:00Z")],
        start_time="2026-03-09T10:00:00Z",
        end_time="2026-03-09T10:01:00Z",
    )


class TestDetectPaths:
    def test_detect_memory_dir(self):
        path = detect_memory_dir()
        assert path.endswith("pokedex/memory")


class TestMainDryRun:
    def test_dry_run_prints_observations(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({"root1": _make_session("root1", "fix the bug")})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem), "--dry-run"])

        captured = capsys.readouterr()
        assert "fix the bug" in captured.out
        assert "observation(s) found" in captured.out
        assert not (mem / "observations.md").exists()

    def test_dry_run_empty_sessions(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem), "--dry-run"])

        captured = capsys.readouterr()
        assert "0 observation(s) found" in captured.out


class TestMainSession:
    def test_single_session(self, tmp_path, capsys, monkeypatch):
        # Fork behavior: --session without --dry-run writes observations to disk.
        fake = FakeTapeReader({"root1": _make_session("root1", "add tests")})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem), "--session", "root1"])

        captured = capsys.readouterr()
        assert "Wrote" in captured.out
        obs_file = mem / "observations.md"
        assert obs_file.exists()
        assert "add tests" in obs_file.read_text()

    def test_single_session_dry_run(self, tmp_path, capsys, monkeypatch):
        # --session with --dry-run prints without writing.
        fake = FakeTapeReader({"root1": _make_session("root1", "add tests")})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem), "--session", "root1", "--dry-run"])

        captured = capsys.readouterr()
        assert "add tests" in captured.out
        assert "observation(s) found" in captured.out
        assert not (mem / "observations.md").exists()

    def test_missing_session_returns_empty(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem), "--session", "nonexistent"])

        captured = capsys.readouterr()
        assert "0 observation(s) found" in captured.out


class TestMainRun:
    def test_full_run(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({"root1": _make_session("root1", "deploy the app")})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem)])

        captured = capsys.readouterr()
        assert "Wrote" in captured.out
        assert (mem / "observations.md").exists()
        assert (mem / "observer_state.json").exists()

    def test_full_run_no_sessions(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem)])

        captured = capsys.readouterr()
        assert "Wrote 0" in captured.out


class TestMainReset:
    def test_reset_clears_watermark(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({"root1": _make_session("root1", "hello world")})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "observer_state.json").write_text(json.dumps({"processed_sessions": ["root1"]}))

        main(["--memory-dir", str(mem), "--reset"])

        captured = capsys.readouterr()
        assert "Watermark cleared" in captured.out
        assert "Wrote" in captured.out

    def test_reset_no_existing_state(self, tmp_path, capsys, monkeypatch):
        fake = FakeTapeReader({})
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)
        mem = tmp_path / "memory"

        main(["--memory-dir", str(mem), "--reset"])

        captured = capsys.readouterr()
        assert "Watermark cleared" in captured.out
