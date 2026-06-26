"""Reader for Paper sessions via the local paperd proxy + Claude Code JSONL transcripts.

Implements the same TapeReader / TapeEntry / TapeSession interface as tape_reader.py
so observer.py and observe_cli.py work without changes beyond the import.

Two-source hybrid:
  1. Paper API (http://127.0.0.1:<port>/v1/sessions)  — session discovery + metadata,
     filtered to sessions whose `cwd` matches the current working directory.
  2. Claude Code JSONL files (~/.claude/projects/<cwd-path>/<session_id>.jsonl)
     — transcript content (user/assistant turns, token usage, tool calls).

The paperd port is read from ANTHROPIC_BASE_URL (set by `paper init` / `paper start`).
"""

import json
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator


# Re-export dataclasses so callers can do: from paper_reader import TapeReader, TapeEntry, …
@dataclass
class ToolUse:
    id: str = ""
    name: str = ""
    input_summary: str = ""


@dataclass
class ToolResult:
    tool_use_id: str = ""
    content_summary: str = ""
    is_error: bool = False


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation: int = 0
    cache_read: int = 0


@dataclass
class TapeEntry:
    type: str = ""
    timestamp: str = ""
    session_id: str = ""
    text_content: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw: dict = field(default_factory=dict)


@dataclass
class TapeSession:
    session_id: str = ""
    entries: list[TapeEntry] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _paperd_base_url() -> str | None:
    """Return the paperd proxy root URL (scheme+host+port) from ANTHROPIC_BASE_URL."""
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not base:
        return None
    parsed = urllib.parse.urlparse(base)
    if parsed.hostname not in ("127.0.0.1", "::1", "localhost"):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _jsonl_dir() -> Path:
    """Return the Claude Code projects directory for this cwd.

    Claude Code slugifies the CWD by replacing every '/' with '-', keeping the
    leading '-' that results from the leading '/'.  e.g.:
        /Users/foo/project  ->  -Users-foo-project
    """
    cwd = Path.cwd()
    slug = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def _summarize_tool_input(name: str, tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]
    if name == "Read":
        return tool_input.get("file_path", "")
    elif name == "Write":
        return tool_input.get("file_path", "")
    elif name == "Edit":
        return tool_input.get("file_path", "")
    elif name == "Bash":
        return tool_input.get("command", "")[:200]
    elif name == "Grep":
        return f"pattern={tool_input.get('pattern', '')}"
    elif name == "Glob":
        return f"pattern={tool_input.get('pattern', '')}"
    elif name == "Agent":
        return tool_input.get("description", "")[:200]
    else:
        for key in ("prompt", "query", "description", "command", "file_path"):
            if key in tool_input:
                return f"{key}={str(tool_input[key])[:200]}"
        return str(tool_input)[:200]


# ---------------------------------------------------------------------------
# Paper API client
# ---------------------------------------------------------------------------

def _api_get(base_url: str, path: str) -> dict:
    url = f"{base_url}{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _list_paper_sessions(base_url: str, cwd: str) -> list[dict]:
    """Fetch all sessions from the Paper API whose cwd matches."""
    sessions = []
    cursor = None
    for _ in range(1000):
        qs = "?limit=100"
        if cursor:
            qs += f"&cursor={cursor}"
        data = _api_get(base_url, f"/v1/sessions{qs}")
        for item in data.get("items", []):
            if item.get("cwd") == cwd:
                sessions.append(item)
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return sessions


# ---------------------------------------------------------------------------
# JSONL transcript parsing
# ---------------------------------------------------------------------------

def _parse_jsonl_entry(obj: dict) -> TapeEntry | None:
    """Convert a JSONL line object to a TapeEntry, or None if not user/assistant."""
    if not isinstance(obj, dict):
        return None
    etype = obj.get("type")
    if etype not in ("user", "assistant"):
        return None

    msg = obj.get("message", {})
    role = msg.get("role", etype)
    timestamp = obj.get("timestamp", "")
    content = msg.get("content", [])

    entry = TapeEntry(
        type=role,
        timestamp=timestamp,
        session_id="",  # filled by caller
        raw={"type": etype, "uuid": obj.get("uuid", "")},
    )

    if isinstance(content, str):
        entry.text_content = content
        return entry

    if role == "assistant":
        usage = msg.get("usage", {})
        entry.token_usage = TokenUsage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation=usage.get("cache_creation_input_tokens", 0),
            cache_read=usage.get("cache_read_input_tokens", 0),
        )
        texts = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_input = block.get("input", {})
                name = block.get("name", "")
                entry.tool_uses.append(ToolUse(
                    id=block.get("id", ""),
                    name=name,
                    input_summary=_summarize_tool_input(name, tool_input),
                ))
        entry.text_content = "\n".join(texts)

    elif role == "user":
        texts = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    parts = [
                        p.get("text", "")
                        for p in result_content
                        if isinstance(p, dict)
                    ]
                    result_content = "\n".join(parts)
                entry.tool_results.append(ToolResult(
                    tool_use_id=block.get("tool_use_id", ""),
                    content_summary=str(result_content)[:500],
                    is_error=bool(block.get("is_error", False)),
                ))
        entry.text_content = "\n".join(texts)

    return entry


def _read_jsonl(path: Path, session_id: str) -> list[TapeEntry]:
    entries = []
    if not path.exists():
        return entries
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = _parse_jsonl_entry(obj)
            if entry is not None:
                entry.session_id = session_id
                entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Public reader class
# ---------------------------------------------------------------------------

class TapeReader:
    """Reads Paper sessions via the paperd proxy API + local JSONL transcripts.

    Accepts an optional ``db_path`` argument so it can be constructed the same
    way as the original TapeReader (observe_cli.py passes the db path positionally).
    The argument is ignored; port is read from ANTHROPIC_BASE_URL instead.
    """

    # Stable identifier for the session-ID namespace this reader produces.
    # Stored in observer_state.json so the observer can detect when the reader
    # changed (e.g. the old SQLite tape_reader used SHA hashes; this one uses
    # Paper harness UUIDs) and reset its watermark instead of reprocessing.
    READER_ID = "paper-jsonl-v1"

    def __init__(self, db_path: str = ""):
        self._cwd = str(Path.cwd())
        self._jsonl_dir = _jsonl_dir()
        self._base_url = _paperd_base_url()

    def list_sessions(self) -> list[str]:
        """Return harness_session_ids for sessions whose cwd matches, ordered by start time.

        Tries the Paper API first; falls back to scanning the local JSONL directory
        when paperd is unavailable or ANTHROPIC_BASE_URL is not set.
        """
        if self._base_url:
            try:
                sessions = _list_paper_sessions(self._base_url, self._cwd)
                sessions.sort(key=lambda s: s.get("started_at", ""))
                return [s["harness_session_id"] for s in sessions if s.get("harness_session_id")]
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                pass
        # Filesystem fallback: scan ~/.claude/projects/{slug}/*.jsonl sorted by mtime
        if self._jsonl_dir.exists():
            files = sorted(self._jsonl_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            return [p.stem for p in files]
        return []

    def read_session(self, session_id: str) -> TapeSession:
        """Read a session's transcript from its JSONL file."""
        jsonl_path = self._jsonl_dir / f"{session_id}.jsonl"
        entries = _read_jsonl(jsonl_path, session_id)
        session = TapeSession(session_id=session_id, entries=entries)
        if entries:
            session.start_time = entries[0].timestamp
            session.end_time = entries[-1].timestamp
        return session

    def iter_entries(self, session_id: str) -> Generator[TapeEntry, None, None]:
        """Lazy generator over entries in a session."""
        jsonl_path = self._jsonl_dir / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            return
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry = _parse_jsonl_entry(obj)
                if entry is not None:
                    entry.session_id = session_id
                    yield entry
