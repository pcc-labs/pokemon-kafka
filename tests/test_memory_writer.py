"""Tests for memory_writer.py — shared observations.md writer."""

from dataclasses import dataclass

from memory_writer import _format_line, _get, append_observations


def _row(date="2026-06-26", priority="important", content="hi", source="flink"):
    return {
        "referenced_time": date,
        "priority": priority,
        "content": content,
        "source_session": source,
    }


class TestHelpers:
    def test_get_from_dict(self):
        assert _get({"a": 1}, "a") == 1
        assert _get({}, "a", "def") == "def"

    def test_get_from_object(self):
        @dataclass
        class Row:
            priority: str = "important"

        assert _get(Row(), "priority") == "important"
        assert _get(Row(), "missing", "def") == "def"

    def test_format_line_truncates_source(self):
        line = _format_line("important", "msg", "flink-very-long-source")
        assert line == "- [important] msg (session: flink-ve)"


class TestAppendObservations:
    def test_writes_new_file_with_header(self, tmp_path):
        n = append_observations(tmp_path, [_row(content="add tests")])
        assert n == 1
        content = (tmp_path / "observations.md").read_text()
        assert "## 2026-06-26" in content
        assert "- [important] add tests (session: flink)" in content

    def test_missing_referenced_time_groups_unknown(self, tmp_path):
        append_observations(tmp_path, [_row(date="")])
        assert "## unknown" in (tmp_path / "observations.md").read_text()

    def test_existing_header_not_duplicated(self, tmp_path):
        path = tmp_path / "observations.md"
        path.write_text("## 2026-06-26\n- old\n")
        append_observations(tmp_path, [_row(content="new")])
        content = path.read_text()
        assert content.count("## 2026-06-26") == 1
        assert "new" in content

    def test_accepts_objects(self, tmp_path):
        @dataclass
        class Obs:
            referenced_time: str = "2026-06-26"
            priority: str = "possible"
            content: str = "from object"
            source_session: str = "abcdef1234"

        append_observations(tmp_path, [Obs()])
        content = (tmp_path / "observations.md").read_text()
        assert "- [possible] from object (session: abcdef12)" in content

    def test_returns_count_across_dates(self, tmp_path):
        n = append_observations(
            tmp_path,
            [_row(date="2026-06-25"), _row(date="2026-06-26"), _row(date="2026-06-26")],
        )
        assert n == 3
        content = (tmp_path / "observations.md").read_text()
        assert "## 2026-06-25" in content
        assert "## 2026-06-26" in content


class TestDedupe:
    def test_skips_line_already_in_file(self, tmp_path):
        append_observations(tmp_path, [_row(content="dup")], dedupe=True)
        # Second write of the same content is skipped → file unchanged, 0 written
        n = append_observations(tmp_path, [_row(content="dup")], dedupe=True)
        assert n == 0
        assert (tmp_path / "observations.md").read_text().count("dup") == 1

    def test_skips_duplicate_within_same_call(self, tmp_path):
        n = append_observations(
            tmp_path,
            [_row(content="same"), _row(content="same")],
            dedupe=True,
        )
        assert n == 1
        assert (tmp_path / "observations.md").read_text().count("- [important] same") == 1

    def test_all_deduped_writes_nothing(self, tmp_path):
        append_observations(tmp_path, [_row(content="x")], dedupe=True)
        before = (tmp_path / "observations.md").read_text()
        n = append_observations(tmp_path, [_row(content="x")], dedupe=True)
        assert n == 0
        assert (tmp_path / "observations.md").read_text() == before

    def test_no_write_when_nothing_to_add_leaves_no_file(self, tmp_path):
        # dedupe with an empty row set writes nothing and creates no file
        n = append_observations(tmp_path, [], dedupe=True)
        assert n == 0
        assert not (tmp_path / "observations.md").exists()
