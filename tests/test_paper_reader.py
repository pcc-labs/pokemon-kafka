"""Tests for paper_reader.py."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from paper_reader import (
    TapeReader,
    _api_get,
    _list_paper_sessions,
    _paperd_base_url,
    _parse_jsonl_entry,
    _read_jsonl,
    _summarize_tool_input,
)


class TestSummarizeToolInput:
    def test_read(self):
        assert _summarize_tool_input("Read", {"file_path": "/foo.py"}) == "/foo.py"

    def test_write(self):
        assert _summarize_tool_input("Write", {"file_path": "/bar.py"}) == "/bar.py"

    def test_edit(self):
        assert _summarize_tool_input("Edit", {"file_path": "/baz.py"}) == "/baz.py"

    def test_bash(self):
        assert _summarize_tool_input("Bash", {"command": "ls -la"}) == "ls -la"

    def test_bash_truncated(self):
        result = _summarize_tool_input("Bash", {"command": "x" * 300})
        assert len(result) == 200

    def test_grep(self):
        assert _summarize_tool_input("Grep", {"pattern": "TODO"}) == "pattern=TODO"

    def test_glob(self):
        assert _summarize_tool_input("Glob", {"pattern": "**/*.py"}) == "pattern=**/*.py"

    def test_agent(self):
        assert _summarize_tool_input("Agent", {"description": "find bugs"}) == "find bugs"

    def test_fallback_known_key(self):
        assert "query=test" in _summarize_tool_input("Unknown", {"query": "test"})

    def test_fallback_no_known_key(self):
        result = _summarize_tool_input("Unknown", {"foo": "bar"})
        assert "foo" in result

    def test_non_dict_input(self):
        result = _summarize_tool_input("Read", "just a string")
        assert result == "just a string"


class TestParseJsonlEntry:
    def _user(self, content):
        return {
            "type": "user",
            "timestamp": "2026-03-09T10:00:00Z",
            "message": {"role": "user", "content": content},
        }

    def _assistant(self, content, usage=None):
        return {
            "type": "assistant",
            "timestamp": "2026-03-09T10:01:00Z",
            "message": {
                "role": "assistant",
                "content": content,
                "usage": usage or {},
            },
        }

    def test_non_dict_returns_none(self):
        assert _parse_jsonl_entry(None) is None
        assert _parse_jsonl_entry([]) is None
        assert _parse_jsonl_entry("string") is None

    def test_unknown_type_returns_none(self):
        assert _parse_jsonl_entry({"type": "system"}) is None

    def test_user_text_string(self):
        entry = _parse_jsonl_entry(self._user("hello world"))
        assert entry is not None
        assert entry.type == "user"
        assert entry.text_content == "hello world"

    def test_user_text_block(self):
        entry = _parse_jsonl_entry(self._user([{"type": "text", "text": "hello"}]))
        assert entry.text_content == "hello"

    def test_user_tool_result(self):
        content = [{"type": "tool_result", "tool_use_id": "tu-1", "content": "output", "is_error": False}]
        entry = _parse_jsonl_entry(self._user(content))
        assert len(entry.tool_results) == 1
        assert entry.tool_results[0].tool_use_id == "tu-1"
        assert not entry.tool_results[0].is_error

    def test_user_tool_result_error(self):
        content = [{"type": "tool_result", "tool_use_id": "tu-2", "content": "err", "is_error": True}]
        entry = _parse_jsonl_entry(self._user(content))
        assert entry.tool_results[0].is_error

    def test_assistant_text(self):
        entry = _parse_jsonl_entry(self._assistant([{"type": "text", "text": "ok"}]))
        assert entry is not None
        assert entry.type == "assistant"
        assert entry.text_content == "ok"

    def test_assistant_tool_use(self):
        content = [{"type": "tool_use", "id": "tu-1", "name": "Read", "input": {"file_path": "/x.py"}}]
        entry = _parse_jsonl_entry(self._assistant(content))
        assert len(entry.tool_uses) == 1
        assert entry.tool_uses[0].name == "Read"
        assert entry.tool_uses[0].input_summary == "/x.py"

    def test_assistant_token_usage(self):
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 10,
        }
        entry = _parse_jsonl_entry(self._assistant([], usage))
        assert entry.token_usage.input_tokens == 100
        assert entry.token_usage.output_tokens == 50
        assert entry.token_usage.cache_read == 80
        assert entry.token_usage.cache_creation == 10

    def test_assistant_skips_non_dict_blocks(self):
        # A bare string block in an assistant message is ignored.
        content = ["not a dict", {"type": "text", "text": "ok"}]
        entry = _parse_jsonl_entry(self._assistant(content))
        assert entry.text_content == "ok"

    def test_user_skips_non_dict_blocks(self):
        content = ["not a dict", {"type": "text", "text": "hi"}]
        entry = _parse_jsonl_entry(self._user(content))
        assert entry.text_content == "hi"

    def test_user_tool_result_list_content(self):
        # tool_result content can be a list of {type:text} parts that get joined;
        # non-dict parts are skipped.
        content = [
            {
                "type": "tool_result",
                "tool_use_id": "tu-3",
                "content": [
                    {"type": "text", "text": "line1"},
                    {"type": "text", "text": "line2"},
                    "ignored-non-dict",
                ],
            }
        ]
        entry = _parse_jsonl_entry(self._user(content))
        assert len(entry.tool_results) == 1
        assert entry.tool_results[0].content_summary == "line1\nline2"


# ── _paperd_base_url() ────────────────────────────────────────────────


class TestPaperdBaseUrl:
    def test_no_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        assert _paperd_base_url() is None

    def test_localhost_ip(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787/foo")
        assert _paperd_base_url() == "http://127.0.0.1:8787"

    def test_localhost_hostname(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:9000")
        assert _paperd_base_url() == "http://localhost:9000"

    def test_non_local_host_returns_none(self, monkeypatch):
        # A remote base URL (e.g. the real Anthropic API) is not a paperd proxy.
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        assert _paperd_base_url() is None


# ── _api_get() ─────────────────────────────────────────────────────────


class TestApiGet:
    def test_returns_parsed_json(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"items": [1, 2]}).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_resp

        with patch("paper_reader.urllib.request.urlopen", return_value=mock_cm) as m:
            result = _api_get("http://127.0.0.1:8787", "/v1/sessions")

        assert result == {"items": [1, 2]}
        # URL is built from base + path
        assert m.call_args[0][0] == "http://127.0.0.1:8787/v1/sessions"


# ── _list_paper_sessions() ─────────────────────────────────────────────


class TestListPaperSessions:
    def test_filters_by_cwd_and_paginates(self):
        pages = [
            {
                "items": [
                    {"cwd": "/work", "harness_session_id": "a", "started_at": "1"},
                    {"cwd": "/other", "harness_session_id": "x", "started_at": "1"},
                ],
                "next_cursor": "c1",
            },
            {
                "items": [
                    {"cwd": "/work", "harness_session_id": "b", "started_at": "2"},
                ],
                "next_cursor": None,
            },
        ]
        with patch("paper_reader._api_get", side_effect=pages):
            sessions = _list_paper_sessions("http://base", "/work")

        assert [s["harness_session_id"] for s in sessions] == ["a", "b"]

    def test_single_page_no_cursor(self):
        pages = [{"items": [{"cwd": "/work", "harness_session_id": "a"}]}]
        with patch("paper_reader._api_get", side_effect=pages):
            sessions = _list_paper_sessions("http://base", "/work")
        assert len(sessions) == 1


# ── _read_jsonl() ──────────────────────────────────────────────────────


class TestReadJsonl:
    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_jsonl(tmp_path / "nope.jsonl", "s1") == []

    def test_parses_skips_blank_and_invalid(self, tmp_path):
        path = tmp_path / "s1.jsonl"
        path.write_text(
            "\n"  # blank line skipped
            + json.dumps({"type": "user", "timestamp": "t1", "message": {"role": "user", "content": "hi"}})
            + "\n"
            + "{not valid json\n"  # decode error skipped
            + json.dumps({"type": "system"})
            + "\n"  # non user/assistant -> None
            + json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "t2",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                }
            )
            + "\n"
        )
        entries = _read_jsonl(path, "s1")
        assert len(entries) == 2
        assert all(e.session_id == "s1" for e in entries)
        assert entries[0].text_content == "hi"
        assert entries[1].text_content == "ok"


# ── TapeReader: Paper API path ─────────────────────────────────────────


class TestTapeReaderPaperApi:
    def test_list_sessions_via_api_sorted_by_started_at(self):
        reader = TapeReader()
        reader._base_url = "http://127.0.0.1:8787"
        reader._cwd = "/work"

        api_sessions = [
            {"harness_session_id": "later", "started_at": "2026-01-02"},
            {"harness_session_id": "earlier", "started_at": "2026-01-01"},
            {"started_at": "2026-01-03"},  # no harness_session_id -> dropped
        ]
        with patch("paper_reader._list_paper_sessions", return_value=api_sessions):
            sessions = reader.list_sessions()

        assert sessions == ["earlier", "later"]

    def test_list_sessions_api_error_falls_back_to_jsonl(self, tmp_path):
        jsonl_dir = tmp_path / "projects" / "-work"
        jsonl_dir.mkdir(parents=True)
        (jsonl_dir / "session-fs.jsonl").write_text("")

        reader = TapeReader()
        reader._base_url = "http://127.0.0.1:8787"
        reader._cwd = "/work"
        reader._jsonl_dir = jsonl_dir

        with patch("paper_reader._list_paper_sessions", side_effect=urllib.error.URLError("paperd down")):
            sessions = reader.list_sessions()

        assert sessions == ["session-fs"]


# ── TapeReader.read_session() ──────────────────────────────────────────


class TestReadSession:
    def test_reads_entries_with_times(self, tmp_path):
        jsonl_dir = tmp_path / "proj"
        jsonl_dir.mkdir()
        (jsonl_dir / "s1.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "t1", "message": {"role": "user", "content": "hi"}})
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "t2",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                }
            )
            + "\n"
        )
        reader = TapeReader()
        reader._jsonl_dir = jsonl_dir

        session = reader.read_session("s1")
        assert session.session_id == "s1"
        assert len(session.entries) == 2
        assert session.start_time == "t1"
        assert session.end_time == "t2"

    def test_missing_session_has_no_times(self, tmp_path):
        reader = TapeReader()
        reader._jsonl_dir = tmp_path
        session = reader.read_session("missing")
        assert session.entries == []
        assert session.start_time == ""
        assert session.end_time == ""


# ── TapeReader.iter_entries() ──────────────────────────────────────────


class TestIterEntries:
    def test_yields_entries_skipping_blank_and_invalid(self, tmp_path):
        jsonl_dir = tmp_path / "proj"
        jsonl_dir.mkdir()
        (jsonl_dir / "s1.jsonl").write_text(
            "\n"
            + json.dumps({"type": "user", "timestamp": "t1", "message": {"role": "user", "content": "hi"}})
            + "\n"
            + "{bad json\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "t2",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                }
            )
            + "\n"
        )
        reader = TapeReader()
        reader._jsonl_dir = jsonl_dir

        entries = list(reader.iter_entries("s1"))
        assert [e.text_content for e in entries] == ["hi", "ok"]
        assert all(e.session_id == "s1" for e in entries)

    def test_missing_file_yields_nothing(self, tmp_path):
        reader = TapeReader()
        reader._jsonl_dir = tmp_path
        assert list(reader.iter_entries("missing")) == []


class TestTapeReaderFilesystemFallback:
    def test_falls_back_to_jsonl_dir_when_no_paperd(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

        jsonl_dir = tmp_path / "projects" / "-test"
        jsonl_dir.mkdir(parents=True)
        (jsonl_dir / "session-aaa.jsonl").write_text("")
        (jsonl_dir / "session-bbb.jsonl").write_text("")

        reader = TapeReader()
        reader._jsonl_dir = jsonl_dir
        reader._base_url = None

        sessions = reader.list_sessions()
        assert set(sessions) == {"session-aaa", "session-bbb"}

    def test_returns_empty_when_no_paperd_and_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        reader = TapeReader()
        reader._jsonl_dir = tmp_path / "nonexistent"
        reader._base_url = None
        assert reader.list_sessions() == []

    def test_jsonl_files_ordered_by_mtime(self, tmp_path, monkeypatch):
        import time

        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

        jsonl_dir = tmp_path / "projects" / "-test"
        jsonl_dir.mkdir(parents=True)
        a = jsonl_dir / "session-a.jsonl"
        b = jsonl_dir / "session-b.jsonl"
        a.write_text("")
        time.sleep(0.01)
        b.write_text("")

        reader = TapeReader()
        reader._jsonl_dir = jsonl_dir
        reader._base_url = None

        sessions = reader.list_sessions()
        assert sessions == ["session-a", "session-b"]
