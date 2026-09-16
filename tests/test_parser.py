"""Tests for the ALS parser against real fixture files."""

import gzip
import logging
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

import db
import snapshots
from conftest import with_duplicate_device, with_plugin_device, with_tempo_automation
from parser import _device_name, parse_als


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

    # Devices: 'Bass' track has Simpler + Tuner — the real Ableton UI names,
    # not the internal tags (OriginalSimpler, Tuner)
    bass_track = next((t for t in result["tracks"] if t["track_name"] == "Bass"), None)
    assert bass_track is not None, "Bass track not found in results"
    assert set(bass_track["devices"]) == {"Simpler", "Tuner"}, (
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


def test_duplicate_devices_are_preserved_in_the_parsed_list(fixture_xml, tmp_path):
    """The parsed list isn't deduped — diff.py's set() was the one collapsing
    a second device instance, not the parser."""
    xml = with_duplicate_device(fixture_xml, "Bass", "Tuner")
    als = tmp_path / "duplicate_tuner.als"
    als.write_bytes(gzip.compress(xml, 6))

    parsed = parse_als(str(als))
    bass = next(t for t in parsed.tracks if t.track_name == "Bass")
    assert bass.devices.count("Tuner") == 2


def test_native_device_names_use_the_ableton_ui_label():
    """Native devices don't self-report a name in the XML — the tag is
    Ableton's internal class name, not what's on screen. Built from a real
    scan of the user's library (tags actually seen), not guessed."""
    cases = [
        ("OriginalSimpler", "Simpler"),
        ("Eq8", "EQ Eight"),
        ("StereoGain", "Utility"),
        ("Compressor2", "Compressor"),
        ("DrumGroupDevice", "Drum Rack"),
        ("AudioEffectGroupDevice", "Audio Effect Rack"),
        ("Reverb", "Reverb"),  # tag already matches the UI label
    ]
    for tag, expected in cases:
        el = ET.fromstring(f"<{tag} />")
        assert _device_name(el, "Bass") == expected, f"{tag} -> expected {expected!r}"


def test_third_party_plugin_name_is_extracted(fixture_xml, tmp_path):
    """Real VST3 plugin (reused from the fixture, not hand-rolled XML) must
    resolve to its own name, not the generic 'PluginDevice' tag fallback."""
    xml = with_plugin_device(fixture_xml, "Drum Group", source_track_name="Drum Group")
    als = tmp_path / "with_plugin.als"
    als.write_bytes(gzip.compress(xml, 6))

    parsed = parse_als(str(als))
    drum_group = next(t for t in parsed.tracks if t.track_name == "Drum Group")
    assert "The Panda Rooms" in drum_group.devices
    assert "PluginDevice" not in drum_group.devices


def test_renamed_plugin_combines_user_name_and_plugin_name(fixture_xml, tmp_path):
    """A device the user renamed in Ableton keeps the plugin's identity too —
    'MyLabel (PluginName)' — since neither alone reconstructs the set later."""
    xml = with_plugin_device(
        fixture_xml, "Drum Group", source_track_name="Drum Group", user_name="Reverb Guitarra"
    )
    als = tmp_path / "renamed_plugin.als"
    als.write_bytes(gzip.compress(xml, 6))

    parsed = parse_als(str(als))
    drum_group = next(t for t in parsed.tracks if t.track_name == "Drum Group")
    assert "Reverb Guitarra (The Panda Rooms)" in drum_group.devices


def test_unrecognized_device_falls_back_to_tag_and_logs_warning(caplog):
    """No UserName, no plugin name, no DeviceId — an unsupported device shape.
    Fall back to the tag, but warn loudly so it doesn't hide in the history."""
    mystery = ET.fromstring("<MysteryDevice />")

    with caplog.at_level(logging.WARNING, logger="parser"):
        name = _device_name(mystery, "Bass")

    assert name == "MysteryDevice"
    assert "MysteryDevice" in caplog.text
    assert "Bass" in caplog.text


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
