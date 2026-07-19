# scripts/historical_observer.py
"""Historical Observer — cross-session pattern extraction via DuckDB.

Reads fitness events from local JSONL files and extracts patterns that
span multiple agent runs: score trends, stuck count progression,
parameter effectiveness. Writes insights to a markdown file that the
evolution loop feeds to the LLM for smarter parameter proposals.

This is the local-first analytics layer. The same DuckDB queries work
against JSONL files on disk today; if the query target ever switches to
a Kafka-backed data store, the insight extraction logic stays the same.

Usage:
    python scripts/historical_observer.py [TELEMETRY_DIR] [--output PATH]
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import duckdb
except ImportError:  # pragma: no cover
    print("Install duckdb: pip install duckdb", file=sys.stderr)
    sys.exit(1)


def observe(telemetry_dir: str, db_path: str | None = None) -> list[dict]:
    """Extract cross-session insights from JSONL fitness events.

    Returns list of {"priority": str, "content": str} dicts.

    If *db_path* points to a persistent DuckDB warehouse (e.g. one created
    by ``dlt_pipeline.py``), queries run against the ``telemetry.events``
    table instead of scanning JSONL files on disk.
    """
    if db_path and Path(db_path).exists():
        conn = duckdb.connect()
        conn.execute(f"ATTACH '{db_path}' AS warehouse (READ_ONLY)")
        conn.execute(
            """
            CREATE VIEW wh_events AS SELECT
                *,
                {
                    'turns': fitness__turns,
                    'battles_won': fitness__battles_won,
                    'maps_visited': fitness__maps_visited,
                    'final_map_id': fitness__final_map_id,
                    'badges': fitness__badges,
                    'party_size': fitness__party_size,
                    'stuck_count': fitness__stuck_count,
                    'backtrack_restores': fitness__backtrack_restores
                } AS fitness
            FROM warehouse.raw.events
            """
        )
        table_expr = "wh_events"
    else:
        data_dir = Path(telemetry_dir)
        if not data_dir.exists() or not list(data_dir.glob("*.jsonl")):
            return []
        pattern = str(data_dir / "*.jsonl")
        table_expr = f"read_json_auto('{pattern}')"
        conn = duckdb.connect()

    try:
        return _extract_insights(conn, table_expr)
    finally:
        conn.close()


def _extract_insights(conn, table_expr: str) -> list[dict]:
    """Run DuckDB queries to extract cross-session insights."""
    # Load only fitness events
    try:
        count = conn.execute(f"SELECT count(*) FROM {table_expr} WHERE type = 'fitness'").fetchone()[0]
    except Exception:
        return []

    if count == 0:
        return []

    insights: list[dict] = []

    # 1. Fitness score trend (using the evolve.py scoring formula)
    rows = conn.execute(
        f"""
        SELECT
            occurred_at,
            root_hash,
            COALESCE(fitness.final_map_id, 0) * 1000
                + COALESCE(fitness.badges, 0) * 5000
                + COALESCE(fitness.party_size, 0) * 500
                + COALESCE(fitness.battles_won, 0) * 100
                - COALESCE(fitness.stuck_count, 0) * 5
                - COALESCE(fitness.turns, 0) * 0.1
                - COALESCE(fitness.backtrack_restores, 0) * 2 AS score,
            COALESCE(fitness.stuck_count, 0),
            COALESCE(fitness.battles_won, 0),
            COALESCE(fitness.final_map_id, 0)
        FROM {table_expr}
        WHERE type = 'fitness'
        ORDER BY occurred_at
        """
    ).fetchall()

    if len(rows) >= 2:
        scores = [r[2] for r in rows]
        first_score = scores[0]
        last_score = scores[-1]
        delta = last_score - first_score

        if delta > 0:
            insights.append(
                {
                    "priority": "important",
                    "content": (
                        f"Fitness trend: improving over {len(rows)} runs "
                        f"(score {first_score:.0f} -> {last_score:.0f}, "
                        f"delta +{delta:.0f})"
                    ),
                }
            )
        elif delta < 0:
            insights.append(
                {
                    "priority": "important",
                    "content": (
                        f"Fitness trend: declining over {len(rows)} runs "
                        f"(score {first_score:.0f} -> {last_score:.0f}, "
                        f"delta {delta:.0f})"
                    ),
                }
            )
        else:
            insights.append(
                {
                    "priority": "informational",
                    "content": f"Fitness trend: flat over {len(rows)} runs (score {last_score:.0f})",
                }
            )

    # 2. Stuck count trend
    if len(rows) >= 2:
        stuck_counts = [r[3] for r in rows]
        first_stuck = stuck_counts[0]
        last_stuck = stuck_counts[-1]
        avg_stuck = sum(stuck_counts) / len(stuck_counts)

        if last_stuck < first_stuck:
            insights.append(
                {
                    "priority": "important",
                    "content": (
                        f"Stuck count trend: decreasing ({first_stuck} -> {last_stuck}, "
                        f"avg {avg_stuck:.1f} across {len(rows)} runs)"
                    ),
                }
            )
        elif last_stuck > first_stuck:
            insights.append(
                {
                    "priority": "important",
                    "content": (
                        f"Stuck count trend: increasing ({first_stuck} -> {last_stuck}, "
                        f"avg {avg_stuck:.1f}) -- navigation may be regressing"
                    ),
                }
            )

    # 3. Best run summary
    if rows:
        best = max(rows, key=lambda r: r[2])
        insights.append(
            {
                "priority": "informational",
                "content": (
                    f"Best run: {best[1][:12]} with score {best[2]:.0f} "
                    f"(map {best[5]}, {best[4]} battles won, {best[3]} stuck events)"
                ),
            }
        )

    # 4. Parameter effectiveness (if params are stored)
    try:
        param_rows = conn.execute(
            f"""
            SELECT
                params.stuck_threshold,
                AVG(fitness.stuck_count) as avg_stuck,
                COUNT(*) as runs
            FROM {table_expr}
            WHERE type = 'fitness' AND params.stuck_threshold IS NOT NULL
            GROUP BY params.stuck_threshold
            HAVING COUNT(*) >= 1
            ORDER BY avg_stuck
            """
        ).fetchall()

        if len(param_rows) >= 2:
            best_param = param_rows[0]
            insights.append(
                {
                    "priority": "possible",
                    "content": (
                        f"Parameter insight: stuck_threshold={best_param[0]} "
                        f"had lowest avg stuck count ({best_param[1]:.1f} over "
                        f"{best_param[2]} runs)"
                    ),
                }
            )
    except Exception:
        pass  # params may not have stuck_threshold in all events

    return insights


def write_insights(insights: list[dict], output_path: str) -> None:
    """Write insights to a markdown file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Historical Insights", ""]
    lines.append(f"_Cross-session analysis of {len(insights)} patterns._")
    lines.append("")

    for insight in insights:
        lines.append(f"- [{insight['priority']}] {insight['content']}")

    path.write_text("\n".join(lines) + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Historical Observer -- cross-session insights")
    parser.add_argument(
        "telemetry_dir",
        nargs="?",
        default="data/telemetry",
        help="Directory containing JSONL files (default: data/telemetry)",
    )
    parser.add_argument(
        "--output",
        default=".tapes/memory/historical_insights.md",
        help="Output markdown path (default: .tapes/memory/historical_insights.md)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to persistent DuckDB warehouse (e.g. data/telemetry.duckdb)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print insights without writing to file",
    )
    args = parser.parse_args()

    insights = observe(args.telemetry_dir, db_path=args.db)
    if not insights:
        print("[historical] No fitness events found.")
        return

    for i in insights:
        print(f"  [{i['priority']}] {i['content']}")

    if not args.dry_run:
        write_insights(insights, args.output)
        print(f"[historical] Wrote {len(insights)} insights to {args.output}")


if __name__ == "__main__":
    main()
