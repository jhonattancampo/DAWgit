"""Content guard, blob dedupe, and storage size."""

import os

import pytest

import db
import snapshots
from conftest import row_count, with_cosmetic_change, with_tempo
from parser import parse_als

PROJECT = "/ableton/Water Project/Water.als"


def _record(project, xml, parsed, db_path, name=None):
    return snapshots.record_snapshot(project, xml, parsed, name_override=name, db_path=db_path)


@pytest.fixture
def parsed(fixture_path):
    return parse_als(str(fixture_path))


def test_first_snapshot_is_recorded(db_path, fixture_xml, parsed):
    snapshot_id = _record(PROJECT, fixture_xml, parsed, db_path)

    assert snapshot_id is not None
    stored = db.get_snapshot(snapshot_id, path=db_path)
    assert stored["name_auto"] == snapshots.NAME_INITIAL
    assert stored["name_user"] is None


def test_identical_content_is_skipped(db_path, fixture_xml, parsed):
    first = _record(PROJECT, fixture_xml, parsed, db_path)
    second = _record(PROJECT, fixture_xml, parsed, db_path)
    third = _record(PROJECT, fixture_xml, parsed, db_path)

    assert first is not None
    assert second is None, "identical XML must not create a second snapshot"
    assert third is None
    assert row_count(db_path, "snapshots") == 1
    assert row_count(db_path, "blobs") == 1


def test_blob_is_deduped_across_projects(db_path, fixture_xml, parsed):
    a = _record("/ableton/A/song.als", fixture_xml, parsed, db_path)
    b = _record("/ableton/B/song.als", fixture_xml, parsed, db_path)

    assert a is not None and b is not None
    assert row_count(db_path, "snapshots") == 2
    assert row_count(db_path, "blobs") == 1, "same XML must be stored once"
    assert (
        db.get_snapshot(a, path=db_path)["blob_hash"]
        == db.get_snapshot(b, path=db_path)["blob_hash"]
    )


def test_non_structural_edit_still_creates_a_snapshot(db_path, fixture_xml, parsed):
    """A MIDI/automation edit is invisible to the parser but must be versioned."""
    _record(PROJECT, fixture_xml, parsed, db_path)
    second = _record(PROJECT, with_cosmetic_change(fixture_xml), parsed, db_path)

    assert second is not None, "different bytes must produce a snapshot"
    stored = db.get_snapshot(second, path=db_path)
    assert stored["name_auto"] == snapshots.NAME_NO_STRUCTURAL_CHANGES
    assert row_count(db_path, "snapshots") == 2
    assert row_count(db_path, "blobs") == 2


def test_structural_change_is_named_from_the_diff(db_path, fixture_xml, tmp_path, parsed):
    _record(PROJECT, fixture_xml, parsed, db_path)

    faster_xml = with_tempo(fixture_xml, parsed.tempo + 20)
    als = tmp_path / "faster.als"
    import gzip

    als.write_bytes(gzip.compress(faster_xml, 6))
    faster_parsed = parse_als(str(als))

    second = _record(PROJECT, faster_xml, faster_parsed, db_path)
    name = db.get_snapshot(second, path=db_path)["name_auto"]
    assert "Tempo changed" in name, name


def test_returning_to_a_previous_state_is_recorded(db_path, fixture_xml, parsed):
    """The guard compares against the latest snapshot only, so a revert counts."""
    edited = with_cosmetic_change(fixture_xml)

    _record(PROJECT, fixture_xml, parsed, db_path)
    _record(PROJECT, edited, parsed, db_path)
    back = _record(PROJECT, fixture_xml, parsed, db_path)

    assert back is not None, "reverting must create a snapshot"
    assert row_count(db_path, "snapshots") == 3
    assert row_count(db_path, "blobs") == 2, "the original blob must be reused"


def test_ten_snapshots_stay_under_five_megabytes(db_path, fixture_xml, parsed):
    for i in range(10):
        assert _record(PROJECT, with_cosmetic_change(fixture_xml, f"edit-{i}"), parsed, db_path)

    assert row_count(db_path, "snapshots") == 10
    assert row_count(db_path, "blobs") == 10

    size = os.path.getsize(db_path)
    assert size < 5 * 1024 * 1024, f"DB is {size / 1024 / 1024:.1f} MB"
