"""Shared fixtures and XML mutation helpers for the DAWgit test suite."""

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import db

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
    """Structural change: a different tempo the diff engine can see."""
    root = ET.fromstring(xml_bytes)
    manual = root.find("LiveSet/MasterTrack/DeviceChain/Mixer/Tempo/Manual")
    assert manual is not None, "fixture has no master tempo element"
    manual.set("Value", str(bpm))
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
