"""HTTP surface: health, rename, delete, restore."""

import gzip

import pytest
from fastapi.testclient import TestClient

import db
import restore as restore_service
import snapshots
from conftest import row_count, with_tempo, with_tempo_automation, write_als
from parser import parse_als


@pytest.fixture
def client(db_path, monkeypatch, tmp_path):
    monkeypatch.delenv("ABLETON_PROJECTS_PATH", raising=False)
    monkeypatch.setattr(restore_service, "BACKUP_DIR", tmp_path / "backups")
    import main

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def project(tmp_path, fixture_xml):
    path = tmp_path / "project" / "Water.als"
    path.parent.mkdir(parents=True)
    write_als(path, fixture_xml)
    return path


def _snapshot_of(path, db_path):
    xml = gzip.decompress(path.read_bytes())
    return snapshots.record_snapshot(str(path), xml, parse_als(str(path)), db_path=db_path)


def test_health_reports_db_and_watcher(client):
    body = client.get("/health").json()

    assert body == {
        "status": "ok",
        "db_ok": True,
        "watcher_active": False,
        "watch_path": None,
    }


def test_get_snapshot_reports_tempo_automation(client, db_path, project, fixture_xml):
    """Regression: snapshot_meta had the automation fields, but the response
    model and _snapshot_to_detail never mapped them — the client always saw
    a bare `tempo`. Assert on the actual HTTP response, not the DB row."""
    write_als(project, with_tempo_automation(fixture_xml, [80, 80, 80, 120, 90, 90, 114]))
    snapshot_id = _snapshot_of(project, db_path)

    body = client.get(f"/snapshots/{snapshot_id}").json()

    assert body["tempo_automated"] is True
    assert body["tempo_min"] == 80
    assert body["tempo_max"] == 120
    assert body["tempo_points"] == 7


def test_get_snapshot_without_automation_reports_null_range(client, db_path, project):
    snapshot_id = _snapshot_of(project, db_path)

    body = client.get(f"/snapshots/{snapshot_id}").json()

    assert body["tempo_automated"] is False
    assert body["tempo_min"] is None
    assert body["tempo_max"] is None
    assert body["tempo_points"] == 0


def test_patch_sets_name_user_and_preserves_name_auto(client, db_path, project):
    snapshot_id = _snapshot_of(project, db_path)
    generated = db.get_snapshot(snapshot_id, path=db_path)["name_auto"]

    response = client.patch(f"/snapshots/{snapshot_id}", json={"name": "Before the drop"})

    assert response.status_code == 200
    body = response.json()
    assert body["name_user"] == "Before the drop"
    assert body["name_auto"] == generated, "the generated name must survive a rename"

    listed = client.get("/snapshots", params={"project": str(project)}).json()
    assert listed[0]["name_user"] == "Before the drop"
    assert listed[0]["name_auto"] == generated


def test_patch_can_clear_the_user_name(client, db_path, project):
    snapshot_id = _snapshot_of(project, db_path)
    client.patch(f"/snapshots/{snapshot_id}", json={"name": "temp"})

    body = client.patch(f"/snapshots/{snapshot_id}", json={"name": None}).json()

    assert body["name_user"] is None


def test_patch_unknown_snapshot(client):
    assert client.patch("/snapshots/999", json={"name": "x"}).status_code == 404


def test_delete_cascades_to_meta_tracks_and_devices(client, db_path, project):
    snapshot_id = _snapshot_of(project, db_path)
    assert row_count(db_path, "snapshot_tracks") > 0
    assert row_count(db_path, "snapshot_devices") > 0

    assert client.delete(f"/snapshots/{snapshot_id}").status_code == 204

    assert row_count(db_path, "snapshots") == 0
    assert row_count(db_path, "snapshot_meta") == 0
    assert row_count(db_path, "snapshot_tracks") == 0
    assert row_count(db_path, "snapshot_devices") == 0
    assert row_count(db_path, "blobs") == 0, "an unreferenced blob must be collected"
    assert client.get(f"/snapshots/{snapshot_id}").status_code == 404


def test_delete_keeps_a_blob_that_another_snapshot_still_uses(client, db_path, project, fixture_xml):
    first = _snapshot_of(project, db_path)
    snapshots.record_snapshot(
        "/ableton/other/Water.als", fixture_xml, parse_als(str(project)), db_path=db_path
    )
    assert row_count(db_path, "blobs") == 1

    assert client.delete(f"/snapshots/{first}").status_code == 204

    assert row_count(db_path, "snapshots") == 1
    assert row_count(db_path, "blobs") == 1, "blob is still referenced"


def test_delete_unknown_snapshot(client):
    assert client.delete("/snapshots/999").status_code == 404


def test_restore_endpoint(client, db_path, project, fixture_xml):
    original_id = _snapshot_of(project, db_path)
    write_als(project, with_tempo(fixture_xml, 160.0))
    _snapshot_of(project, db_path)

    response = client.post(f"/snapshots/{original_id}/restore")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == original_id
    assert body["backup_path"] is not None
    assert body["new_snapshot_id"] is not None
    assert "close it WITHOUT saving" in body["warning"]
    assert parse_als(str(project)).tempo != 160.0


def test_restore_unknown_snapshot(client):
    assert client.post("/snapshots/999/restore").status_code == 404
