"""Observational memory: distills Paper sessions into prioritized observations.

Uses heuristic pattern matching (no LLM calls) to extract noteworthy events
from recorded Paper conversation data and write them to memory files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from memory_writer import append_observations
from paper_reader import TapeReader, TapeSession


@dataclass
class Observation:
    """A single observation extracted from a tape session."""

    timestamp: str = ""
    referenced_time: str = ""
    priority: str = "informational"
    content: str = ""
    source_session: str = ""


# Keywords that signal importance level
_IMPORTANT_KEYWORDS = re.compile(
    r"\b(fix|bug|error|fail|crash|broken|revert|hotfix|security|vulnerability)\b",
    re.IGNORECASE,
)
_POSSIBLE_KEYWORDS = re.compile(
    r"\b(test|refactor|rename|cleanup|reorganize|migrate|deprecate|update)\b",
    re.IGNORECASE,
)


class Observer:
    """Extracts observations from Paper sessions using heuristics."""

    def __init__(self, db_path: str, memory_dir: str):
        self.db_path = Path(db_path)
        self.memory_dir = Path(memory_dir)
        self.reader = TapeReader(db_path)
        self.state_path = self.memory_dir / "observer_state.json"
        self.observations_path = self.memory_dir / "observations.md"

    def run(self) -> list[Observation]:
        """Process unprocessed sessions, write observations. Returns all new observations."""
        sessions = self.get_unprocessed_sessions()
        all_observations: list[Observation] = []

        for session_id in sessions:
            session = self.reader.read_session(session_id)
            observations = self.observe_session(session)
            all_observations.extend(observations)

        if all_observations:
            self.write_observations(all_observations)

        # Update watermark — only keep sessions that still exist in the DB
        # to prevent unbounded growth of the state file.
        current_sessions = set(self.reader.list_sessions())
        state = self.load_state()
        previously_processed = set(state.get("processed_sessions", []))
        state["processed_sessions"] = sorted((previously_processed | current_sessions) & current_sessions)
        self.save_state(state)

        return all_observations

    def get_unprocessed_sessions(self) -> list[str]:
        """Return session IDs that haven't been processed yet."""
        state = self.load_state()
        processed = set(state.get("processed_sessions", []))
        all_sessions = self.reader.list_sessions()
        return [s for s in all_sessions if s not in processed]

    def observe_session(self, session: TapeSession) -> list[Observation]:
        """Extract observations from a parsed session via heuristics."""
        observations: list[Observation] = []
        now = datetime.now(timezone.utc).isoformat() + "Z"

        # 1. Context: first user message (session goal)
        first_user = _first_user_message(session)
        if first_user:
            observations.append(
                Observation(
                    timestamp=now,
                    referenced_time=session.start_time,
                    priority="informational",
                    content=f"Session goal: {first_user[:300]}",
                    source_session=session.session_id,
                )
            )

        # 2. Error patterns: tool results with is_error or exception tracebacks
        for entry in session.entries:
            for result in entry.tool_results:
                if result.is_error:
                    observations.append(
                        Observation(
                            timestamp=now,
                            referenced_time=entry.timestamp,
                            priority="important",
                            content=f"Tool error: {result.content_summary[:300]}",
                            source_session=session.session_id,
                        )
                    )

            # Check assistant text for traceback patterns
            if entry.type == "assistant" and entry.text_content:
                if _has_traceback(entry.text_content):
                    snippet = _extract_traceback_summary(entry.text_content)
                    observations.append(
                        Observation(
                            timestamp=now,
                            referenced_time=entry.timestamp,
                            priority="important",
                            content=f"Exception discussed: {snippet}",
                            source_session=session.session_id,
                        )
                    )

        # 3. Discovery patterns: new files created
        for entry in session.entries:
            for tool in entry.tool_uses:
                if tool.name == "Write" and tool.input_summary:
                    observations.append(
                        Observation(
                            timestamp=now,
                            referenced_time=entry.timestamp,
                            priority="possible",
                            content=f"File created: {tool.input_summary}",
                            source_session=session.session_id,
                        )
                    )

        # 4. Context: token usage summary
        total_input = 0
        total_output = 0
        total_cache_read = 0
        for entry in session.entries:
            total_input += entry.token_usage.input_tokens
            total_output += entry.token_usage.output_tokens
            total_cache_read += entry.token_usage.cache_read

        if total_input > 0:
            observations.append(
                Observation(
                    timestamp=now,
                    referenced_time=session.end_time,
                    priority="informational",
                    content=(f"Token usage: {total_input} input, {total_output} output, {total_cache_read} cache read"),
                    source_session=session.session_id,
                )
            )

        # Classify priorities based on content keywords
        for obs in observations:
            obs.priority = self.classify_priority(obs.content, obs.priority)

        return observations

    def classify_priority(self, content: str, default: str = "informational") -> str:
        """Classify observation priority using keyword matching."""
        if _IMPORTANT_KEYWORDS.search(content):
            return "important"
        if _POSSIBLE_KEYWORDS.search(content):
            return "possible"
        return default

    def write_observations(self, observations: list[Observation]) -> None:
        """Append observations to observations.md grouped by date.

        Delegates to memory_writer so the Flink alerts-consumer writes to the
        same file in the same format (see scripts/memory_writer.py).
        """
        append_observations(self.memory_dir, observations)

    def load_state(self) -> dict:
        """Load observer state from JSON file.

        If the stored ``reader`` identity differs from the current reader's
        READER_ID, the watermark was written against a different session-ID
        namespace (e.g. the old SQLite tape_reader's SHA hashes vs. Paper's
        harness UUIDs). Reprocessing under the new IDs would duplicate every
        observation, so we drop the stale watermark instead.
        """
        if not self.state_path.exists():
            return {}
        state = json.loads(self.state_path.read_text())
        expected = getattr(self.reader, "READER_ID", None)
        if expected is not None and state.get("reader") != expected:
            print(f"[observer] reader changed ({state.get('reader')!r} -> {expected!r}); resetting watermark")
            return {"reader": expected}
        return state

    def save_state(self, state: dict) -> None:
        """Save observer state to JSON file, stamping the current reader identity."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        expected = getattr(self.reader, "READER_ID", None)
        if expected is not None:
            state = {**state, "reader": expected}
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")


def observe_session_inline(session_id: str | None = None) -> list[dict]:
    """Return observations as dicts for programmatic use (no file I/O).

    Reads the most recent Paper session (or ``session_id`` when given) and runs
    the heuristic extraction. Returns list of {"priority": str, "content": str}.
    """
    reader = TapeReader()
    sessions = reader.list_sessions()
    if not sessions:
        return []

    target = session_id if session_id else sessions[-1]
    session = reader.read_session(target)

    # Reuse Observer's heuristic extraction without needing memory_dir
    obs = Observer.__new__(Observer)
    obs.reader = reader
    observations = obs.observe_session(session)

    return [{"priority": o.priority, "content": o.content} for o in observations]


def _first_user_message(session: TapeSession) -> str:
    """Extract the first user message text from a session.

    Skips system framework noise (e.g. <system-reminder> tags) that the
    harness stores as user-role entries.
    """
    for entry in session.entries:
        if entry.type == "user" and entry.text_content:
            stripped = entry.text_content.strip()
            if stripped.startswith("<system-reminder>"):
                continue
            return entry.text_content
    return ""


def _has_traceback(text: str) -> bool:
    """Check if text contains Python traceback patterns."""
    if "Traceback (most recent call last)" in text:
        return True
    # Match "SomeError:" or "SomeException:" at line start where the prefix
    # starts with an uppercase letter, matching Python exception naming
    # conventions (e.g. ValueError, RuntimeError).  Avoids false positives
    # like "myCustomError:" or bare "Error handling is important".
    return bool(re.search(r"^[A-Z]\w*(Error|Exception):", text, re.MULTILINE))


def _extract_traceback_summary(text: str) -> str:
    """Extract a short summary from traceback text."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line and ("Error:" in line or "Exception:" in line):
            return line[:200]
    return text[:200]
