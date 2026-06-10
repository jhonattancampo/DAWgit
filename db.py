"""SQLite connection, schema initialization, and query helpers."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from parser import ParsedSnapshot

DB_PATH = os.environ.get("DB_PATH", "/data/dawgit.db")
_SCHEMA = Path(__file__).with_name("schema.sql").read_text()


def _connect(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str = DB_PATH) -> None:
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


def save_snapshot(
    project_path: str,
    xml_raw: str,
    parsed: ParsedSnapshot,
    path: str = DB_PATH,
) -> int:
    timestamp = datetime.now(timezone.utc).isoformat()

    with _connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (project_path, timestamp, name_auto, xml_raw) VALUES (?, ?, ?, ?)",
            (project_path, timestamp, None, xml_raw),
        )
        snapshot_id = cur.lastrowid

        conn.execute(
            "INSERT INTO snapshot_meta (snapshot_id, tempo, time_sig_num, time_sig_den, track_count) VALUES (?, ?, ?, ?, ?)",
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


def get_snapshots(project_path: str, path: str = DB_PATH) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id, timestamp, name_auto FROM snapshots WHERE project_path = ? ORDER BY timestamp DESC",
            (project_path,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshot(snapshot_id: int, path: str = DB_PATH) -> dict | None:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
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


def update_snapshot_name(snapshot_id: int, name: str, path: str = DB_PATH) -> None:
    with _connect(path) as conn:
        conn.execute(
            "UPDATE snapshots SET name_auto = ? WHERE id = ?", (name, snapshot_id)
        )
