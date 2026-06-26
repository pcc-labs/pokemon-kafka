"""Append observations to the observer's memory file (``observations.md``).

Shared by ``observer.py`` and the Flink ``alerts-consumer`` so that anomaly
alerts land in the *same* ``pokedex/memory/observations.md`` the agent loads at
session start. Pure stdlib — the alerts-consumer imports this inside a minimal
Docker container with no project dependencies.

Output format (one source of truth for both writers)::

    ## 2026-06-26
    - [important] Flink alert [BATTLE_LOOP]: enemy_hp=12 ... (session: flink)
"""

from __future__ import annotations

from pathlib import Path


def _get(row, key: str, default=None):
    """Read a field from either a mapping or an object with attributes."""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _format_line(priority: str, content: str, source: str) -> str:
    return f"- [{priority}] {content} (session: {str(source)[:8]})"


def append_observations(memory_dir, rows, *, dedupe: bool = False) -> int:
    """Append observation ``rows`` to ``<memory_dir>/observations.md``.

    Each row is a mapping or object exposing ``referenced_time``, ``priority``,
    ``content`` and ``source_session``. Rows are grouped under a ``## <date>``
    header derived from ``referenced_time[:10]`` (``unknown`` when absent), and a
    header is only emitted when it is not already in the file.

    When ``dedupe`` is true, lines already present in the file (or already added
    in this call) are skipped, and a date contributing no new lines is omitted
    entirely — this keeps the high-frequency alerts stream from spamming the
    file. With ``dedupe`` false the behaviour matches the observer exactly.

    Returns the number of observation lines written.
    """
    memory_dir = Path(memory_dir)
    path = memory_dir / "observations.md"
    existing = path.read_text() if path.exists() else ""

    by_date: dict[str, list[str]] = {}
    for row in rows:
        ref = _get(row, "referenced_time", "") or ""
        date = ref[:10] if ref else "unknown"
        line = _format_line(
            _get(row, "priority", "informational"),
            _get(row, "content", ""),
            _get(row, "source_session", ""),
        )
        by_date.setdefault(date, []).append(line)

    lines: list[str] = []
    written = 0
    seen: set[str] = set()
    for date in sorted(by_date.keys()):
        date_lines: list[str] = []
        for line in by_date[date]:
            if dedupe and (line in existing or line in seen):
                continue
            date_lines.append(line)
            seen.add(line)
        if dedupe and not date_lines:
            continue

        header = f"## {date}"
        lines.append(f"\n{header}\n" if header not in existing else "")
        lines.extend(date_lines)
        written += len(date_lines)

    if not lines:
        return 0

    memory_dir.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")
    return written
