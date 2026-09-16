"""Tests for the diff engine using parsed snapshot data."""

from diff import diff_snapshots


def _snap(
    tempo=120.0,
    num=4,
    den=4,
    tracks=None,
    tempo_automated=False,
    tempo_min=None,
    tempo_max=None,
    tempo_points=0,
):
    """Build a minimal snapshot dict for diff testing."""
    return {
        "meta": {
            "tempo": tempo,
            "time_sig_num": num,
            "time_sig_den": den,
            "tempo_automated": tempo_automated,
            "tempo_min": tempo_min,
            "tempo_max": tempo_max,
            "tempo_points": tempo_points,
        },
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


def test_automated_base_change_only():
    """Manual (base) moved, the envelope stays identical — the base value
    isn't what plays, so the message must say so, not just show the number."""
    a = _snap(tempo=120.0, tempo_automated=True, tempo_min=80.0, tempo_max=120.0, tempo_points=7)
    b = _snap(tempo=126.0, tempo_automated=True, tempo_min=80.0, tempo_max=120.0, tempo_points=7)
    result = diff_snapshots(a, b)
    assert result.changes == ["Tempo base: 120 → 126 BPM (manda la automation: 80–120 BPM)"]
    print(f"  Test 2b — automated base change only: ✓  {result.changes[0]!r}")


def test_automated_envelope_change_only():
    """Base (Manual) unchanged, but the automation curve moved — today
    that's invisible; the diff must surface it."""
    a = _snap(tempo=120.0, tempo_automated=True, tempo_min=80.0, tempo_max=120.0, tempo_points=7)
    b = _snap(tempo=120.0, tempo_automated=True, tempo_min=80.0, tempo_max=140.0, tempo_points=9)
    result = diff_snapshots(a, b)
    assert result.changes == [
        "Automation de tempo cambió: ahora corre 80–140 BPM (9 pts) — antes 80–120 BPM (7 pts)"
    ]
    print(f"  Test 2c — automated envelope change only: ✓  {result.changes[0]!r}")


def test_automated_base_and_envelope_change_both_reported():
    """Base and envelope moving together are two independent facts, not one
    merged sentence — the reader needs both, separately."""
    a = _snap(tempo=120.0, tempo_automated=True, tempo_min=80.0, tempo_max=120.0, tempo_points=7)
    b = _snap(tempo=126.0, tempo_automated=True, tempo_min=80.0, tempo_max=140.0, tempo_points=9)
    result = diff_snapshots(a, b)
    assert result.changes == [
        "Tempo base: 120 → 126 BPM (manda la automation: 80–140 BPM)",
        "Automation de tempo cambió: ahora corre 80–140 BPM (9 pts) — antes 80–120 BPM (7 pts)",
    ]
    print(f"  Test 2d — automated base + envelope change: ✓  {result.changes}")


def test_tempo_automation_added_without_manual_change():
    a = _snap(tempo=120.0)
    b = _snap(tempo=120.0, tempo_automated=True, tempo_min=80.0, tempo_max=120.0, tempo_points=5)
    result = diff_snapshots(a, b)
    assert result.changes == [
        "Automation de tempo activada: ahora corre 80–120 BPM (5 pts) — antes fijo en 120 BPM"
    ]
    print(f"  Test 2e — automation added: ✓  {result.changes[0]!r}")


def test_tempo_automation_removed_without_manual_change():
    a = _snap(tempo=120.0, tempo_automated=True, tempo_min=80.0, tempo_max=120.0, tempo_points=5)
    b = _snap(tempo=120.0)
    result = diff_snapshots(a, b)
    assert result.changes == [
        "Automation de tempo desactivada: ahora fijo en 120 BPM — antes corría 80–120 BPM (5 pts)"
    ]
    print(f"  Test 2f — automation removed: ✓  {result.changes[0]!r}")


def test_non_automated_tempo_change_is_unaffected():
    """A set with no tempo automation must report exactly as it did before
    this feature existed."""
    a = _snap(tempo=120.0)
    b = _snap(tempo=128.0)
    result = diff_snapshots(a, b)
    assert result.summary == "Tempo changed: 120 → 128 BPM"


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


def test_duplicate_device_added_is_reported():
    """set() collapsed {PluginDevice} vs {PluginDevice, PluginDevice} to an
    empty diff — a real duplicate reverb went unreported. Counter must catch it."""
    a = _snap(tracks=[_track("1", "5-Audio", "Audio", devices=["PluginDevice"])])
    b = _snap(tracks=[_track("1", "5-Audio", "Audio", devices=["PluginDevice", "PluginDevice"])])
    result = diff_snapshots(a, b)
    assert len(result.changes) == 1
    assert result.summary == "Device 'PluginDevice' added to '5-Audio'"
    print(f"  Test 6b — duplicate device added: ✓  {result.summary!r}")


def test_duplicate_device_removed_is_reported():
    a = _snap(tracks=[_track("1", "5-Audio", "Audio", devices=["PluginDevice", "PluginDevice"])])
    b = _snap(tracks=[_track("1", "5-Audio", "Audio", devices=["PluginDevice"])])
    result = diff_snapshots(a, b)
    assert len(result.changes) == 1
    assert result.summary == "Device 'PluginDevice' removed from '5-Audio'"
    print(f"  Test 6c — duplicate device removed: ✓  {result.summary!r}")


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
    test_automated_base_change_only()
    test_automated_envelope_change_only()
    test_automated_base_and_envelope_change_both_reported()
    test_tempo_automation_added_without_manual_change()
    test_tempo_automation_removed_without_manual_change()
    test_non_automated_tempo_change_is_unaffected()
    test_track_added()
    test_track_removed()
    test_track_renamed()
    test_device_added()
    test_duplicate_device_added_is_reported()
    test_duplicate_device_removed_is_reported()
    test_multiple_changes_summary_truncated()
    print("\n15/15 tests passed.")
