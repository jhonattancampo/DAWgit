"""SQLite connection, schema initialization, and query helpers."""

import gzip
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from parser import ParsedSnapshot

DB_PATH = os.environ.get("DB_PATH", "/app/data/dawgit.db")
_SCHEMA = Path(__file__).with_name("schema.sql").read_text()

# Columns of `snapshots` that are safe to read on every request.
# `blob_hash` is included; the blob itself lives in `blobs` and is only
# fetched through get_snapshot_xml / get_snapshot_gz.
_SNAPSHOT_COLUMNS = "id, project_path, timestamp, name_auto, name_user, blob_hash"


def _resolve(path: str | None) -> str:
    return path if path is not None else DB_PATH


@contextmanager
def _session(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection, run inside a transaction, and always close it.

    sqlite3's own context manager commits/rolls back but does NOT close,
    which leaks the handle until garbage collection.
    """
    conn = sqlite3.connect(_resolve(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    Path(_resolve(path)).parent.mkdir(parents=True, exist_ok=True)
    with _session(path) as conn:
        conn.executescript(_SCHEMA)


def ping(path: str | None = None) -> bool:
    """Cheap liveness check for /health."""
    try:
        with _session(path) as conn:
            conn.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def save_snapshot(
    project_path: str,
    blob_hash: str,
    gz: bytes,
    parsed: ParsedSnapshot,
    name_auto: str | None = None,
    path: str | None = None,
) -> int:
    """Insert a snapshot, reusing the blob when its hash is already stored."""
    timestamp = datetime.now(timezone.utc).isoformat()

    with _session(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blobs (hash, gz) VALUES (?, ?)", (blob_hash, gz)
        )

        cur = conn.execute(
            "INSERT INTO snapshots (project_path, timestamp, name_auto, name_user, blob_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_path, timestamp, name_auto, None, blob_hash),
        )
        snapshot_id = cur.lastrowid

        conn.execute(
            "INSERT INTO snapshot_meta (snapshot_id, tempo, time_sig_num, time_sig_den, track_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, parsed.tempo, parsed.time_sig_num, parsed.time_sig_den, len(parsed.tracks)),
        )

        conn.executemany(
            "INSERT INTO snapshot_tracks (snapshot_id, track_id, track_name, track_type) VALUES (?, ?, ?, ?)",
            [(snapshot_id, t.track_id, t.track_name, t.track_type) for t in parsed.tracks],
        )

        conn.executemany(
            "INSERT INTO snapshot_devices (snapshot_id, track_id, device_name) VALUES (?, ?, ?)",
            [
                (snapshot_id, t.track_id, dev)
                for t in parsed.tracks
                for dev in t.devices
            ],
        )

    return snapshot_id


def get_snapshots(project_path: str, path: str | None = None) -> list[dict]:
    with _session(path) as conn:
        rows = conn.execute(
            f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE project_path = ? "
            "ORDER BY timestamp DESC, id DESC",
            (project_path,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_snapshot(project_path: str, path: str | None = None) -> dict | None:
    """Most recent snapshot row for a project. Does not touch the blob."""
    with _session(path) as conn:
        row = conn.execute(
            f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE project_path = ? "
            "ORDER BY timestamp DESC, id DESC LIMIT 1",
            (project_path,),
        ).fetchone()
    return dict(row) if row else None


def get_snapshot(snapshot_id: int, path: str | None = None) -> dict | None:
    with _session(path) as conn:
        row = conn.execute(
            f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None

        meta = conn.execute(
            "SELECT * FROM snapshot_meta WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()

        tracks = conn.execute(
            "SELECT * FROM snapshot_tracks WHERE snapshot_id = ? ORDER BY id",
            (snapshot_id,),
        ).fetchall()

        devices = conn.execute(
            "SELECT track_id, device_name FROM snapshot_devices WHERE snapshot_id = ? ORDER BY id",
            (snapshot_id,),
        ).fetchall()

    devices_by_track: dict[str, list[str]] = {}
    for d in devices:
        devices_by_track.setdefault(d["track_id"], []).append(d["device_name"])

    return {
        **dict(row),
        "meta": dict(meta) if meta else None,
        "tracks": [
            {**dict(t), "devices": devices_by_track.get(t["track_id"], [])}
            for t in tracks
        ],
    }


def get_snapshot_gz(snapshot_id: int, path: str | None = None) -> bytes | None:
    """Raw gzipped LiveSet XML — exactly what belongs on disk as an .als."""
    with _session(path) as conn:
        row = conn.execute(
            "SELECT b.gz FROM snapshots s JOIN blobs b ON b.hash = s.blob_hash WHERE s.id = ?",
            (snapshot_id,),
        ).fetchone()
    return row["gz"] if row else None


def get_snapshot_xml(snapshot_id: int, path: str | None = None) -> str | None:
    gz = get_snapshot_gz(snapshot_id, path=path)
    return gzip.decompress(gz).decode("utf-8") if gz is not None else None


def update_snapshot_user_name(snapshot_id: int, name: str | None, path: str | None = None) -> bool:
    """Set the user-supplied name. Leaves name_auto untouched."""
    with _session(path) as conn:
        cur = conn.execute(
            "UPDATE snapshots SET name_user = ? WHERE id = ?", (name, snapshot_id)
        )
    return cur.rowcount > 0


def delete_snapshot(snapshot_id: int, path: str | None = None) -> bool:
    """Delete a snapshot. Meta/tracks/devices cascade; the blob is dropped
    only once no other snapshot references it."""
    with _session(path) as conn:
        row = conn.execute(
            "SELECT blob_hash FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return False

        conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        conn.execute(
            "DELETE FROM blobs WHERE hash = ? "
            "AND NOT EXISTS (SELECT 1 FROM snapshots WHERE blob_hash = ?)",
            (row["blob_hash"], row["blob_hash"]),
        )
    return True
