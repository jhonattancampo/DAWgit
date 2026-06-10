"""Tests for the ALS parser against real fixture files."""

import gzip
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db
from parser import parse_als

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "Water-11-nov-drums.als")


def test_save_and_retrieve():
    parsed = parse_als(FIXTURE)
    with open(FIXTURE, "rb") as f:
        xml_raw = gzip.decompress(f.read()).decode("utf-8")

    import tempfile, pathlib
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        db.init_db(tmp_path)
        snapshot_id = db.save_snapshot(FIXTURE, xml_raw, parsed, path=tmp_path)

        result = db.get_snapshot(snapshot_id, path=tmp_path)

        assert result is not None, "get_snapshot returned None"
        assert result["meta"]["tempo"] == parsed.tempo, (
            f"tempo mismatch: {result['meta']['tempo']} != {parsed.tempo}"
        )
        assert result["meta"]["track_count"] == len(parsed.tracks), (
            f"track_count mismatch: {result['meta']['track_count']} != {len(parsed.tracks)}"
        )
        assert result["meta"]["time_sig_num"] == parsed.time_sig_num
        assert result["meta"]["time_sig_den"] == parsed.time_sig_den

        print(f"  snapshot_id:  {snapshot_id}")
        print(f"  tempo:        {result['meta']['tempo']} BPM  ✓")
        print(f"  time sig:     {result['meta']['time_sig_num']}/{result['meta']['time_sig_den']}  ✓")
        print(f"  track_count:  {result['meta']['track_count']}  ✓")
        print(f"  tracks in DB: {len(result['tracks'])}  ✓")
        print(f"  xml_raw len:  {len(result['xml_raw'])} chars  ✓")

        snapshots = db.get_snapshots(FIXTURE, path=tmp_path)
        assert len(snapshots) == 1
        print(f"  get_snapshots: {len(snapshots)} row  ✓")

        db.update_snapshot_name(snapshot_id, "Initial snapshot", path=tmp_path)
        updated = db.get_snapshot(snapshot_id, path=tmp_path)
        assert updated["name_auto"] == "Initial snapshot"
        print(f"  name_auto:    {updated['name_auto']!r}  ✓")

        # Devices: 'Bass' track (id=32) has OriginalSimpler + Tuner
        bass_track = next(
            (t for t in result["tracks"] if t["track_name"] == "Bass"), None
        )
        assert bass_track is not None, "Bass track not found in results"
        assert set(bass_track["devices"]) == {"OriginalSimpler", "Tuner"}, (
            f"Unexpected devices on Bass: {bass_track['devices']}"
        )
        print(f"  Bass devices: {sorted(bass_track['devices'])}  ✓")

        # Return track 'A-Reverb' should have Reverb device
        reverb_track = next(
            (t for t in result["tracks"] if t["track_name"] == "A-Reverb"), None
        )
        assert reverb_track is not None, "A-Reverb track not found"
        assert "Reverb" in reverb_track["devices"], (
            f"Reverb device missing: {reverb_track['devices']}"
        )
        print(f"  A-Reverb devices: {reverb_track['devices']}  ✓")

        print("\nAll assertions passed.")
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    test_save_and_retrieve()
