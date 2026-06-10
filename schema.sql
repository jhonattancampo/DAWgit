CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT    NOT NULL,
    timestamp    TEXT    NOT NULL,
    name_auto    TEXT,
    xml_raw      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_meta (
    snapshot_id  INTEGER PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
    tempo        REAL    NOT NULL,
    time_sig_num INTEGER NOT NULL,
    time_sig_den INTEGER NOT NULL,
    track_count  INTEGER NOT NULL
);

-- track_type: MIDI | Audio | Return | Master | Group
CREATE TABLE IF NOT EXISTS snapshot_tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    track_id     TEXT    NOT NULL,
    track_name   TEXT    NOT NULL,
    track_type   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    track_id    TEXT    NOT NULL,
    device_name TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_project  ON snapshots(project_path);
CREATE INDEX IF NOT EXISTS idx_snapshots_time     ON snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_tracks_snapshot    ON snapshot_tracks(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_devices_snapshot   ON snapshot_devices(snapshot_id);
