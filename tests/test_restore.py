"""Restore: backup, atomic overwrite, and round-trip fidelity."""

import gzip

import pytest

import db
import restore as restore_service
import snapshots
from conftest import row_count, with_tempo, write_als
from parser import parse_als


@pytest.fixture
def backup_dir(tmp_path):
    return tmp_path / "backups"


@pytest.fixture
def project(tmp_path, fixture_xml):
    """A real .als on disk, outside any watched tree."""
    path = tmp_path / "project" / "Water.als"
    path.parent.mkdir(parents=True)
    write_als(path, fixture_xml)
    return path


def _snapshot_of(path, db_path):
    xml = gzip.decompress(path.read_bytes())
    return snapshots.record_snapshot(str(path), xml, parse_als(str(path)), db_path=db_path)


def test_restore_round_trip(db_path, project, fixture_xml, backup_dir):
    original_id = _snapshot_of(project, db_path)
    original = db.get_snapshot(original_id, path=db_path)

    # Move the project forward: a tempo change the parser can see.
    write_als(project, with_tempo(fixture_xml, original["meta"]["tempo"] + 30))
    edited_id = _snapshot_of(project, db_path)
    assert edited_id is not None
    assert parse_als(str(project)).tempo == original["meta"]["tempo"] + 30

    result = restore_service.restore_snapshot(
        original_id, db_path=db_path, backup_dir=backup_dir
    )

    # The file on disk must parse back to the snapshotted state.
    restored = parse_als(str(project))
    assert restored.tempo == original["meta"]["tempo"]
    assert restored.time_sig_num == original["meta"]["time_sig_num"]
    assert restored.time_sig_den == original["meta"]["time_sig_den"]
    assert len(restored.tracks) == original["meta"]["track_count"]
    assert [t.track_name for t in restored.tracks] == [
        t["track_name"] for t in original["tracks"]
    ]
    assert [sorted(t.devices) for t in restored.tracks] == [
        sorted(t["devices"]) for t in original["tracks"]
    ]

    # Still a valid gzipped .als, byte-identical to the stored blob.
    assert project.read_bytes() == db.get_snapshot_gz(original_id, path=db_path)

    assert result.warning
    assert result.project_path == str(project)


def test_restore_backs_up_the_current_file_first(db_path, project, fixture_xml, backup_dir):
    original_id = _snapshot_of(project, db_path)
    edited_xml = with_tempo(fixture_xml, 999.0)
    write_als(project, edited_xml)
    _snapshot_of(project, db_path)

    result = restore_service.restore_snapshot(
        original_id, db_path=db_path, backup_dir=backup_dir
    )

    assert result.backup_path is not None
    backup = backup_dir / result.backup_path.split("/")[-1]
    assert backup.exists()
    assert parse_als(str(backup)).tempo == 999.0, "backup must hold the pre-restore file"


def test_restore_records_the_revert_and_reuses_the_blob(
    db_path, project, fixture_xml, backup_dir
):
    original_id = _snapshot_of(project, db_path)
    write_als(project, with_tempo(fixture_xml, 150.0))
    _snapshot_of(project, db_path)

    result = restore_service.restore_snapshot(
        original_id, db_path=db_path, backup_dir=backup_dir
    )

    assert result.new_snapshot_id is not None
    revert = db.get_snapshot(result.new_snapshot_id, path=db_path)
    assert revert["name_auto"] == f"Restored from snapshot #{original_id}"
    assert revert["blob_hash"] == db.get_snapshot(original_id, path=db_path)["blob_hash"]
    assert row_count(db_path, "snapshots") == 3
    assert row_count(db_path, "blobs") == 2, "the reverted blob must be reused"


def test_restoring_the_latest_state_is_a_no_op_snapshot(
    db_path, project, backup_dir
):
    only_id = _snapshot_of(project, db_path)

    result = restore_service.restore_snapshot(
        only_id, db_path=db_path, backup_dir=backup_dir
    )

    assert result.new_snapshot_id is None, "content is unchanged — no new snapshot"
    assert row_count(db_path, "snapshots") == 1


def test_restore_leaves_no_temp_files(db_path, project, fixture_xml, backup_dir):
    original_id = _snapshot_of(project, db_path)
    write_als(project, with_tempo(fixture_xml, 140.0))
    _snapshot_of(project, db_path)

    restore_service.restore_snapshot(original_id, db_path=db_path, backup_dir=backup_dir)

    assert [p.name for p in project.parent.iterdir()] == ["Water.als"]


def test_restore_unknown_snapshot(db_path, backup_dir):
    with pytest.raises(restore_service.SnapshotNotFound):
        restore_service.restore_snapshot(4242, db_path=db_path, backup_dir=backup_dir)


def test_restore_when_project_folder_is_gone(db_path, project, backup_dir):
    snapshot_id = _snapshot_of(project, db_path)
    project.unlink()
    project.parent.rmdir()

    with pytest.raises(restore_service.ProjectPathUnavailable):
        restore_service.restore_snapshot(snapshot_id, db_path=db_path, backup_dir=backup_dir)
