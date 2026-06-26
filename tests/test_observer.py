"""Tests for observer.py."""

import json

import pytest

from paper_reader import TapeEntry, TapeSession, ToolResult, ToolUse, TokenUsage
from observer import (
    Observation,
    Observer,
    observe_session_inline,
    _first_user_message,
    _has_traceback,
    _extract_traceback_summary,
)


# ── Fake reader for unit tests ────────────────────────────────────────


class FakeTapeReader:
    """Minimal TapeReader stand-in that returns controlled sessions."""

    READER_ID = "fake-v1"

    def __init__(self, sessions: dict[str, TapeSession] | None = None):
        self._sessions = sessions or {}

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def read_session(self, session_id: str) -> TapeSession:
        return self._sessions.get(session_id, TapeSession(session_id=session_id))


def _make_observer(tmp_path, sessions=None):
    obs = Observer(db_path="", memory_dir=str(tmp_path / "memory"))
    obs.reader = FakeTapeReader(sessions or {})
    return obs


def _make_session(session_id="test-sess", entries=None):
    entries = entries or []
    s = TapeSession(
        session_id=session_id,
        entries=entries,
        start_time="2026-03-09T10:00:00Z",
        end_time="2026-03-09T10:30:00Z",
    )
    return s


# ── Observation dataclass ────────────────────────────────────────────


class TestObservation:
    def test_defaults(self):
        o = Observation()
        assert o.timestamp == ""
        assert o.referenced_time == ""
        assert o.priority == "informational"
        assert o.content == ""
        assert o.source_session == ""


# ── Helper functions ─────────────────────────────────────────────────


class TestFirstUserMessage:
    def test_finds_first_user(self):
        session = _make_session(entries=[
            TapeEntry(type="assistant", text_content="init"),
            TapeEntry(type="user", text_content="build a feature"),
            TapeEntry(type="user", text_content="second msg"),
        ])
        assert _first_user_message(session) == "build a feature"

    def test_no_user_messages(self):
        assert _first_user_message(_make_session()) == ""

    def test_user_with_empty_text(self):
        session = _make_session(entries=[
            TapeEntry(type="user", text_content=""),
            TapeEntry(type="user", text_content="actual message"),
        ])
        assert _first_user_message(session) == "actual message"

    def test_skips_system_reminder(self):
        session = _make_session(entries=[
            TapeEntry(type="user", text_content="<system-reminder>\nsome hook output\n</system-reminder>"),
            TapeEntry(type="user", text_content="fix the bug"),
        ])
        assert _first_user_message(session) == "fix the bug"

    def test_all_system_reminders(self):
        session = _make_session(entries=[
            TapeEntry(type="user", text_content="<system-reminder>hook</system-reminder>"),
        ])
        assert _first_user_message(session) == ""


class TestHasTraceback:
    def test_python_traceback(self):
        assert _has_traceback("Traceback (most recent call last):\n  File...")

    def test_error_at_line_start(self):
        assert _has_traceback("ValueError: bad value")

    def test_exception_at_line_start(self):
        assert _has_traceback("RuntimeException: oops")

    def test_error_midline_no_match(self):
        assert not _has_traceback("I see the error in the code")

    def test_error_in_sentence(self):
        assert not _has_traceback("Error handling is important")

    def test_no_traceback(self):
        assert not _has_traceback("everything is fine")

    def test_multiline_with_error_on_own_line(self):
        assert _has_traceback("Some context\nModuleNotFoundError: No module named 'foo'\nmore")


class TestExtractTracebackSummary:
    def test_extracts_last_error_line(self):
        assert _extract_traceback_summary("Some context\nValueError: bad input\nmore") == "ValueError: bad input"

    def test_exception_line(self):
        assert _extract_traceback_summary("RuntimeException: oops") == "RuntimeException: oops"

    def test_no_error_line_falls_back(self):
        assert _extract_traceback_summary("just some output") == "just some output"


# ── Observer.observe_session ─────────────────────────────────────────


class TestObserveSession:
    def test_extracts_session_goal(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[TapeEntry(type="user", text_content="fix the login bug")])
        results = obs.observe_session(session)
        goals = [o for o in results if "Session goal" in o.content]
        assert len(goals) == 1
        assert "fix the login bug" in goals[0].content

    def test_extracts_tool_errors(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[
            TapeEntry(
                type="user",
                timestamp="2026-03-09T10:05:00Z",
                tool_results=[ToolResult(tool_use_id="tu-1", content_summary="command not found", is_error=True)],
            )
        ])
        results = obs.observe_session(session)
        errors = [o for o in results if "Tool error" in o.content]
        assert len(errors) == 1
        assert errors[0].priority == "important"

    def test_extracts_tracebacks(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[
            TapeEntry(
                type="assistant",
                timestamp="2026-03-09T10:05:00Z",
                text_content="I see an error:\nValueError: bad input\nLet me fix it.",
            )
        ])
        results = obs.observe_session(session)
        assert any("Exception discussed" in o.content for o in results)

    def test_extracts_file_creations(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[
            TapeEntry(
                type="assistant",
                timestamp="2026-03-09T10:05:00Z",
                tool_uses=[ToolUse(id="tu-1", name="Write", input_summary="/new_file.py")],
            )
        ])
        results = obs.observe_session(session)
        files = [o for o in results if "File created" in o.content]
        assert len(files) == 1
        assert "/new_file.py" in files[0].content

    def test_extracts_token_usage(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[
            TapeEntry(type="assistant", token_usage=TokenUsage(input_tokens=1000, output_tokens=200, cache_read=800))
        ])
        results = obs.observe_session(session)
        usage = [o for o in results if "Token usage" in o.content]
        assert len(usage) == 1
        assert "800 cache read" in usage[0].content

    def test_no_token_usage_when_zero(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[TapeEntry(type="assistant")])
        results = obs.observe_session(session)
        assert not any("Token usage" in o.content for o in results)

    def test_empty_session(self, tmp_path):
        obs = _make_observer(tmp_path)
        assert obs.observe_session(_make_session()) == []

    def test_write_tool_with_empty_summary_skipped(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[
            TapeEntry(type="assistant", tool_uses=[ToolUse(id="tu-1", name="Write", input_summary="")])
        ])
        results = obs.observe_session(session)
        assert not any("File created" in o.content for o in results)

    def test_non_write_tools_not_tracked(self, tmp_path):
        obs = _make_observer(tmp_path)
        session = _make_session(entries=[
            TapeEntry(type="assistant", tool_uses=[ToolUse(id="tu-1", name="Read", input_summary="/some.py")])
        ])
        results = obs.observe_session(session)
        assert not any("File created" in o.content for o in results)


# ── Observer.classify_priority ────────────────────────────────────────


class TestClassifyPriority:
    def setup_method(self):
        self.obs = Observer(db_path="", memory_dir="/tmp/unused")

    def test_important_keywords(self):
        assert self.obs.classify_priority("Fixed a bug in login") == "important"
        assert self.obs.classify_priority("Error: connection failed") == "important"
        assert self.obs.classify_priority("crash on startup") == "important"
        assert self.obs.classify_priority("security vulnerability found") == "important"

    def test_possible_keywords(self):
        assert self.obs.classify_priority("test coverage added") == "possible"
        assert self.obs.classify_priority("refactor the module") == "possible"
        assert self.obs.classify_priority("update dependencies") == "possible"

    def test_informational_default(self):
        assert self.obs.classify_priority("Session started") == "informational"

    def test_custom_default(self):
        assert self.obs.classify_priority("nothing special", "possible") == "possible"

    def test_important_beats_possible(self):
        assert self.obs.classify_priority("fix the test") == "important"


# ── Observer.write_observations ───────────────────────────────────────


class TestWriteObservations:
    def test_writes_markdown_file(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.write_observations([
            Observation(referenced_time="2026-03-09T10:00:00Z", priority="important",
                        content="Found a bug", source_session="abcdef12-3456"),
            Observation(referenced_time="2026-03-09T11:00:00Z", priority="informational",
                        content="Session started", source_session="abcdef12-3456"),
        ])
        content = obs.observations_path.read_text()
        assert "## 2026-03-09" in content
        assert "[important]" in content
        assert "Found a bug" in content
        assert "(session: abcdef12)" in content

    def test_appends_to_existing(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.memory_dir.mkdir(parents=True, exist_ok=True)
        obs.observations_path.write_text("# Existing\n\n## 2026-03-08\n- old\n")
        obs.write_observations([
            Observation(referenced_time="2026-03-09T10:00:00Z", priority="possible",
                        content="New thing", source_session="sess1234-5678"),
        ])
        content = obs.observations_path.read_text()
        assert "# Existing" in content
        assert "## 2026-03-09" in content
        assert "New thing" in content

    def test_no_duplicate_date_headers(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.memory_dir.mkdir(parents=True, exist_ok=True)
        obs.observations_path.write_text("## 2026-03-09\n- existing\n")
        obs.write_observations([
            Observation(referenced_time="2026-03-09T12:00:00Z", content="More stuff",
                        source_session="sess1234-5678"),
        ])
        assert obs.observations_path.read_text().count("## 2026-03-09") == 1

    def test_unknown_date(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.write_observations([
            Observation(referenced_time="", content="No date", source_session="sess1234-5678"),
        ])
        assert "## unknown" in obs.observations_path.read_text()

    def test_multiple_dates_sorted(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.write_observations([
            Observation(referenced_time="2026-03-10T10:00:00Z", content="later", source_session="s1"),
            Observation(referenced_time="2026-03-08T10:00:00Z", content="earlier", source_session="s1"),
        ])
        content = obs.observations_path.read_text()
        assert content.index("2026-03-08") < content.index("2026-03-10")

    def test_creates_memory_dir(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.memory_dir = tmp_path / "deep" / "nested" / "memory"
        obs.observations_path = obs.memory_dir / "observations.md"
        obs.write_observations([
            Observation(referenced_time="2026-01-01T00:00:00Z", content="test", source_session="s1"),
        ])
        assert obs.observations_path.exists()


# ── Observer.load_state / save_state ─────────────────────────────────


class TestLoadState:
    def test_missing_file_returns_empty(self, tmp_path):
        obs = _make_observer(tmp_path)
        assert obs.load_state() == {}

    def test_reads_existing_state(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.memory_dir.mkdir(parents=True, exist_ok=True)
        obs.state_path.write_text(
            json.dumps({"reader": "fake-v1", "processed_sessions": ["a", "b"]})
        )
        assert obs.load_state()["processed_sessions"] == ["a", "b"]

    def test_resets_on_reader_mismatch(self, tmp_path, capsys):
        obs = _make_observer(tmp_path)
        obs.memory_dir.mkdir(parents=True, exist_ok=True)
        # State written by a different reader (e.g. old SQLite tape_reader)
        obs.state_path.write_text(
            json.dumps({"reader": "sqlite-sha", "processed_sessions": ["sha1", "sha2"]})
        )
        state = obs.load_state()
        assert state.get("processed_sessions", []) == []
        assert state["reader"] == "fake-v1"
        assert "resetting watermark" in capsys.readouterr().out

    def test_resets_when_reader_key_absent(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.memory_dir.mkdir(parents=True, exist_ok=True)
        # Legacy state with no reader stamp at all
        obs.state_path.write_text(json.dumps({"processed_sessions": ["old1"]}))
        state = obs.load_state()
        assert state.get("processed_sessions", []) == []
        assert state["reader"] == "fake-v1"


class TestSaveState:
    def test_writes_json(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.save_state({"processed_sessions": ["x"]})
        data = json.loads(obs.state_path.read_text())
        assert data["processed_sessions"] == ["x"]

    def test_stamps_reader_id(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.save_state({"processed_sessions": ["x"]})
        data = json.loads(obs.state_path.read_text())
        assert data["reader"] == "fake-v1"

    def test_creates_dir(self, tmp_path):
        obs = _make_observer(tmp_path)
        obs.memory_dir = tmp_path / "new" / "dir"
        obs.state_path = obs.memory_dir / "observer_state.json"
        obs.save_state({"key": "val"})
        assert obs.state_path.exists()


# ── Observer.run ──────────────────────────────────────────────────────


class TestRun:
    def test_end_to_end(self, tmp_path):
        session = _make_session("s1", entries=[
            TapeEntry(type="user", text_content="fix the crash", timestamp="2026-03-09T10:00:00Z"),
            TapeEntry(type="assistant", timestamp="2026-03-09T10:01:00Z",
                      token_usage=TokenUsage(input_tokens=500, output_tokens=100, cache_read=400)),
        ])
        obs = _make_observer(tmp_path, {"s1": session})
        results = obs.run()
        assert len(results) > 0
        assert obs.observations_path.exists()
        assert obs.state_path.exists()

        # Running again produces no new observations
        assert obs.run() == []

    def test_run_with_no_sessions(self, tmp_path):
        obs = _make_observer(tmp_path)
        assert obs.run() == []

    def test_run_updates_watermark(self, tmp_path):
        session = _make_session("s1", entries=[
            TapeEntry(type="user", text_content="hello", timestamp="2026-03-09T10:00:00Z"),
        ])
        obs = _make_observer(tmp_path, {"s1": session})
        obs.run()
        assert "s1" in obs.load_state()["processed_sessions"]

    def test_run_no_observations_no_write(self, tmp_path):
        session = _make_session("s1", entries=[TapeEntry(type="assistant")])
        obs = _make_observer(tmp_path, {"s1": session})
        assert obs.run() == []
        assert not obs.observations_path.exists()


# ── observe_session_inline ────────────────────────────────────────────


class TestObserveSessionInline:
    def test_returns_dicts(self, tmp_path, monkeypatch):
        session = _make_session("s1", entries=[
            TapeEntry(type="user", text_content="fix the crash", timestamp="2026-03-09T10:00:00Z"),
            TapeEntry(type="assistant", timestamp="2026-03-09T10:01:00Z",
                      text_content="ValueError: bad",
                      token_usage=TokenUsage(input_tokens=500, output_tokens=100, cache_read=400)),
        ])
        fake = FakeTapeReader({"s1": session})
        import observer as obs_module
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)

        results = observe_session_inline("", "s1")
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("priority" in r and "content" in r for r in results)

    def test_latest_session_when_no_id(self, tmp_path, monkeypatch):
        s1 = _make_session("s1", entries=[TapeEntry(type="user", text_content="hello")])
        s2 = _make_session("s2", entries=[TapeEntry(type="user", text_content="world")])
        fake = FakeTapeReader({"s1": s1, "s2": s2})
        import observer as obs_module
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)

        results = observe_session_inline("")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_empty_returns_empty(self, tmp_path, monkeypatch):
        fake = FakeTapeReader({})
        import observer as obs_module
        monkeypatch.setattr(obs_module, "TapeReader", lambda db_path="": fake)

        assert observe_session_inline("") == []
