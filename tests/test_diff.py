"""Tests for the diff engine using parsed snapshot data."""

from diff import diff_snapshots


def _snap(tempo=120.0, num=4, den=4, tracks=None):
    """Build a minimal snapshot dict for diff testing."""
    return {
        "meta": {"tempo": tempo, "time_sig_num": num, "time_sig_den": den},
        "tracks": tracks or [],
    }


def _track(tid, name, ttype="MIDI", devices=None):
    return {"track_id": tid, "track_name": name, "track_type": ttype, "devices": devices or []}


def test_identical_snapshots():
    tracks = [_track("1", "Kick", "MIDI")]
    a = _snap(tracks=tracks)
    b = _snap(tracks=tracks)
    result = diff_snapshots(a, b)
    assert result.changes == [], f"Expected no changes, got: {result.changes}"
    assert result.summary == "No changes"
    print("  Test 1 — identical snapshots: ✓")


def test_tempo_change():
    a = _snap(tempo=120.0)
    b = _snap(tempo=128.0)
    result = diff_snapshots(a, b)
    assert len(result.changes) == 1
    assert "120" in result.changes[0] and "128" in result.changes[0]
    assert result.summary == "Tempo changed: 120 → 128 BPM"
    print(f"  Test 2 — tempo change:        ✓  {result.summary!r}")


def test_track_added():
    a = _snap(tracks=[_track("1", "Kick")])
    b = _snap(tracks=[_track("1", "Kick"), _track("2", "Bass DI", "Audio")])
    result = diff_snapshots(a, b)
    assert any("Bass DI" in c and "added" in c for c in result.changes)
    assert result.summary == "Track 'Bass DI' added (Audio)"
    print(f"  Test 3 — track added:         ✓  {result.summary!r}")


def test_track_removed():
    a = _snap(tracks=[_track("1", "Kick"), _track("2", "Bass DI")])
    b = _snap(tracks=[_track("1", "Kick")])
    result = diff_snapshots(a, b)
    assert any("Bass DI" in c and "removed" in c for c in result.changes)
    assert result.summary == "Track 'Bass DI' removed"
    print(f"  Test 4 — track removed:       ✓  {result.summary!r}")


def test_track_renamed():
    a = _snap(tracks=[_track("1", "Kick")])
    b = _snap(tracks=[_track("1", "Kick Drum")])
    result = diff_snapshots(a, b)
    assert any("Kick" in c and "renamed" in c for c in result.changes)
    assert result.summary == "Track renamed: 'Kick' → 'Kick Drum'"
    print(f"  Test 5 — track renamed:       ✓  {result.summary!r}")


def test_device_added():
    a = _snap(tracks=[_track("1", "Vocals", devices=[])])
    b = _snap(tracks=[_track("1", "Vocals", devices=["Reverb"])])
    result = diff_snapshots(a, b)
    assert any("Reverb" in c and "added" in c for c in result.changes)
    assert result.summary == "Device 'Reverb' added to 'Vocals'"
    print(f"  Test 6 — device added:        ✓  {result.summary!r}")


def test_multiple_changes_summary_truncated():
    a = _snap(
        tempo=120.0,
        tracks=[
            _track("1", "Kick"),
            _track("2", "Bass"),
            _track("3", "Synth"),
        ],
    )
    b = _snap(
        tempo=128.0,
        tracks=[
            _track("1", "Kick"),
            _track("4", "New Track", "Audio"),  # added
            _track("5", "Another", "Audio"),    # added
        ],
        # Bass and Synth removed → 1 tempo + 2 added + 2 removed = 5 changes
    )
    result = diff_snapshots(a, b)
    assert len(result.changes) >= 3
    assert result.summary.startswith(f"{len(result.changes)} changes — ")
    assert result.summary.endswith("...")
    print(f"  Test 7 — multi-change summary: ✓  {result.summary!r}")
    print(f"           ({len(result.changes)} total changes)")


if __name__ == "__main__":
    print("Running diff tests...\n")
    test_identical_snapshots()
    test_tempo_change()
    test_track_added()
    test_track_removed()
    test_track_renamed()
    test_device_added()
    test_multiple_changes_summary_truncated()
    print("\n7/7 tests passed.")
