"""Tests for the ALS parser against real fixture files."""

import gzip
import warnings
from pathlib import Path

import db
import snapshots
from conftest import with_tempo_automation
from parser import parse_als


def test_save_and_retrieve(db_path, fixture_path, fixture_xml):
    parsed = parse_als(str(fixture_path))

    snapshot_id = snapshots.record_snapshot(
        str(fixture_path), fixture_xml, parsed, db_path=db_path
    )
    assert snapshot_id is not None

    result = db.get_snapshot(snapshot_id, path=db_path)

    assert result is not None, "get_snapshot returned None"
    assert result["meta"]["tempo"] == parsed.tempo, (
        f"tempo mismatch: {result['meta']['tempo']} != {parsed.tempo}"
    )
    assert result["meta"]["track_count"] == len(parsed.tracks), (
        f"track_count mismatch: {result['meta']['track_count']} != {len(parsed.tracks)}"
    )
    assert result["meta"]["time_sig_num"] == parsed.time_sig_num
    assert result["meta"]["time_sig_den"] == parsed.time_sig_den

    snapshot_list = db.get_snapshots(str(fixture_path), path=db_path)
    assert len(snapshot_list) == 1

    assert result["name_auto"] == snapshots.NAME_INITIAL
    assert result["name_user"] is None

    # The blob is fetched separately now; it must round-trip exactly.
    assert db.get_snapshot_xml(snapshot_id, path=db_path).encode("utf-8") == fixture_xml

    # Devices: 'Bass' track has OriginalSimpler + Tuner
    bass_track = next((t for t in result["tracks"] if t["track_name"] == "Bass"), None)
    assert bass_track is not None, "Bass track not found in results"
    assert set(bass_track["devices"]) == {"OriginalSimpler", "Tuner"}, (
        f"Unexpected devices on Bass: {bass_track['devices']}"
    )

    # Return track 'A-Reverb' should have Reverb device
    reverb_track = next((t for t in result["tracks"] if t["track_name"] == "A-Reverb"), None)
    assert reverb_track is not None, "A-Reverb track not found"
    assert "Reverb" in reverb_track["devices"], (
        f"Reverb device missing: {reverb_track['devices']}"
    )


def test_tempo_baseline_point_is_not_automation(fixture_path):
    """Ableton writes a lone envelope point matching Manual on every set,
    even when tempo was never touched. That baseline must not read as
    automated — otherwise every ordinary set would trip the new copy."""
    parsed = parse_als(str(fixture_path))
    assert parsed.tempo_automated is False
    assert parsed.tempo_min is None
    assert parsed.tempo_max is None
    assert parsed.tempo_points == 0


def test_tempo_automation_is_extracted(fixture_xml, tmp_path):
    automated_xml = with_tempo_automation(fixture_xml, [80, 80, 80, 120, 90, 90, 114])
    als = tmp_path / "automated.als"
    als.write_bytes(gzip.compress(automated_xml, 6))

    parsed = parse_als(str(als))

    assert parsed.tempo_automated is True
    assert parsed.tempo_min == 80
    assert parsed.tempo_max == 120
    assert parsed.tempo_points == 7


def test_master_track_lookup_prefers_master_then_main(fixture_xml):
    """Live 11 ships <MasterTrack>; the Live 12 <MainTrack> fallback must not
    fire just because the element has no children."""
    import xml.etree.ElementTree as ET

    from parser import _find_master_track

    liveset = ET.fromstring(fixture_xml).find("LiveSet")
    assert _find_master_track(liveset) is liveset.find("MasterTrack")

    empty = ET.fromstring("<LiveSet><MasterTrack /></LiveSet>")
    assert _find_master_track(empty) is not None, "empty MasterTrack must still be found"

    live12 = ET.fromstring("<LiveSet><MainTrack><X /></MainTrack></LiveSet>")
    assert _find_master_track(live12) is live12.find("MainTrack")


def test_parsing_emits_no_deprecation_warnings(fixture_path):
    """Guards the ElementTree truthiness trap: `find(a) or find(b)` warns.

    Scoped to warnings attributed to parser.py so an unrelated deprecation in
    Python or a dependency cannot fail this test.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parse_als(str(fixture_path))

    ours = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and Path(w.filename).name == "parser.py"
    ]
    assert not ours, [str(w.message) for w in ours]
