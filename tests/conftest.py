"""Shared fixtures and XML mutation helpers for the DAWgit test suite."""

import copy
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import db
from parser import _find_master_track

FIXTURE = Path(__file__).parent / "fixtures" / "sample.als"

MISSING_FIXTURE = (
    f"Test fixture not found: {FIXTURE}\n\n"
    "The parser, snapshot and restore tests run against a real Ableton set.\n"
    "Drop a disposable .als (Live 11 or 12) at tests/fixtures/sample.als and re-run."
)


def _fixture() -> Path:
    if not FIXTURE.exists():
        pytest.fail(MISSING_FIXTURE, pytrace=False)
    return FIXTURE


@pytest.fixture
def fixture_path() -> Path:
    return _fixture()


@pytest.fixture
def fixture_xml() -> bytes:
    """Uncompressed LiveSet XML of the real fixture."""
    return gzip.decompress(_fixture().read_bytes())


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> str:
    """Isolated SQLite file, also installed as the module-level default."""
    path = str(tmp_path / "dawgit.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)
    return path


def with_tempo(xml_bytes: bytes, bpm: float) -> bytes:
    """Structural change: a different tempo the diff engine can see.

    The master track is resolved through the parser rather than a fixed path,
    so a Live 12 fixture (<MainTrack>) works the same as a Live 11 one
    (<MasterTrack>). Hardcoding the Live 11 name here would break the snapshot
    and restore suites on a fixture that has nothing to do with them.
    """
    root = ET.fromstring(xml_bytes)
    liveset = root.find("LiveSet")
    assert liveset is not None, "fixture has no <LiveSet> element"

    master = _find_master_track(liveset)
    assert master is not None, "fixture has no master/main track"

    manual = master.find("DeviceChain/Mixer/Tempo/Manual")
    assert manual is not None, "master track has no tempo element"

    manual.set("Value", str(bpm))
    return ET.tostring(root, encoding="utf-8")


def with_tempo_automation(xml_bytes: bytes, values: list[float]) -> bytes:
    """Structural change: give the master track's tempo real automation.

    Ableton always writes a lone envelope point matching Manual even when
    tempo was never automated — this replaces that baseline point with one
    FloatEvent per value in `values`, so the parser sees real automation.
    """
    root = ET.fromstring(xml_bytes)
    liveset = root.find("LiveSet")
    assert liveset is not None, "fixture has no <LiveSet> element"

    master = _find_master_track(liveset)
    assert master is not None, "fixture has no master/main track"

    target = master.find("DeviceChain/Mixer/Tempo/AutomationTarget")
    assert target is not None, "master track has no tempo AutomationTarget"
    target_id = target.get("Id")

    envelopes = master.find("AutomationEnvelopes/Envelopes")
    assert envelopes is not None, "master track has no AutomationEnvelopes/Envelopes"

    envelope = next(
        (
            e for e in envelopes.findall("AutomationEnvelope")
            if (p := e.find("EnvelopeTarget/PointeeId")) is not None
            and p.get("Value") == target_id
        ),
        None,
    )
    assert envelope is not None, "master track has no tempo automation envelope"

    events = envelope.find("Automation/Events")
    assert events is not None, "tempo envelope has no Events container"

    for child in list(events):
        events.remove(child)
    for i, value in enumerate(values):
        ET.SubElement(events, "FloatEvent", {"Id": str(i), "Time": str(i * 4), "Value": str(value)})

    return ET.tostring(root, encoding="utf-8")


def _find_track_by_name(liveset: ET.Element, track_name: str) -> ET.Element:
    tracks_container = liveset.find("Tracks")
    assert tracks_container is not None, "fixture has no <Tracks> element"
    for track in tracks_container:
        name_el = track.find("Name/EffectiveName")
        if name_el is not None and name_el.get("Value") == track_name:
            return track
    raise AssertionError(f"track {track_name!r} not found")


def _reachable_devices(track: ET.Element) -> ET.Element:
    """The Devices container the parser actually reads for a track — the
    first `DeviceChain/Devices` match, same traversal as `_extract_tracks`."""
    devices_el = track.find(".//DeviceChain/Devices")
    assert devices_el is not None, "track has no reachable Devices list"
    return devices_el


def with_duplicate_device(xml_bytes: bytes, track_name: str, device_tag: str) -> bytes:
    """Duplicate an existing device on `track_name`, e.g. a second Tuner.

    Exercises the diff engine's device count, not just presence/absence.
    """
    root = ET.fromstring(xml_bytes)
    liveset = root.find("LiveSet")
    assert liveset is not None, "fixture has no <LiveSet> element"

    devices_el = _reachable_devices(_find_track_by_name(liveset, track_name))
    original = devices_el.find(device_tag)
    assert original is not None, f"track {track_name!r} has no <{device_tag}> device"

    devices_el.append(copy.deepcopy(original))
    return ET.tostring(root, encoding="utf-8")


def with_plugin_device(
    xml_bytes: bytes,
    track_name: str,
    source_track_name: str,
    user_name: str | None = None,
) -> bytes:
    """Add a real third-party PluginDevice (found anywhere under
    `source_track_name`) onto `track_name`'s reachable Devices list.

    Reuses a plugin's real, verified PluginDesc XML instead of hand-rolling
    VstPluginInfo/Vst3PluginInfo/AuPluginInfo — those formats each hide a
    same-named decoy under their own Preset, so inventing the shape risks
    testing a structure that doesn't match real Ableton output.
    """
    root = ET.fromstring(xml_bytes)
    liveset = root.find("LiveSet")
    assert liveset is not None, "fixture has no <LiveSet> element"

    source_track = _find_track_by_name(liveset, source_track_name)
    plugin = next((el for el in source_track.iter() if el.tag == "PluginDevice"), None)
    assert plugin is not None, f"track {source_track_name!r} has no PluginDevice"
    plugin = copy.deepcopy(plugin)

    if user_name is not None:
        user_name_el = plugin.find("UserName")
        assert user_name_el is not None, "PluginDevice has no <UserName>"
        user_name_el.set("Value", user_name)

    devices_el = _reachable_devices(_find_track_by_name(liveset, track_name))
    devices_el.append(plugin)
    return ET.tostring(root, encoding="utf-8")


def with_cosmetic_change(xml_bytes: bytes, marker: str = "dawgit") -> bytes:
    """Different bytes, identical parsed state — stands in for a MIDI edit,
    which the parser cannot see either."""
    return xml_bytes.replace(b"<Ableton", f"<!-- {marker} -->\n<Ableton".encode(), 1)


def write_als(path: Path, xml_bytes: bytes) -> None:
    path.write_bytes(gzip.compress(xml_bytes, 6))


def row_count(db_file: str, table: str) -> int:
    import sqlite3

    with sqlite3.connect(db_file) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
