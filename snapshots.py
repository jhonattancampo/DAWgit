"""Snapshot recording pipeline shared by the watcher and the restore endpoint.

The guard is content-based: a snapshot is skipped only when the XML is
byte-identical to the latest snapshot of the same project. A structural diff
that comes back empty still produces a snapshot, because the parser only sees
tempo, time signature, tracks and devices — clips, notes, automation and mixer
state are invisible to it, and those edits must not be silently dropped.
"""

import gzip
import hashlib
import logging

import db
from diff import diff_snapshots
from parser import ParsedSnapshot

logger = logging.getLogger(__name__)

GZIP_LEVEL = 6

NAME_INITIAL = "Initial snapshot"
NAME_NO_STRUCTURAL_CHANGES = "Edits (no structural changes)"


def xml_hash(xml_bytes: bytes) -> str:
    """SHA-256 of the uncompressed XML — stable across gzip levels."""
    return hashlib.sha256(xml_bytes).hexdigest()


def parsed_to_dict(parsed: ParsedSnapshot) -> dict:
    """Shape a ParsedSnapshot like a row from db.get_snapshot, for diffing."""
    return {
        "meta": {
            "tempo": parsed.tempo,
            "time_sig_num": parsed.time_sig_num,
            "time_sig_den": parsed.time_sig_den,
            "track_count": len(parsed.tracks),
        },
        "tracks": [
            {
                "track_id": t.track_id,
                "track_name": t.track_name,
                "track_type": t.track_type,
                "devices": t.devices,
            }
            for t in parsed.tracks
        ],
    }


def record_snapshot(
    project_path: str,
    xml_bytes: bytes,
    parsed: ParsedSnapshot,
    name_override: str | None = None,
    db_path: str | None = None,
) -> int | None:
    """Store a snapshot unless the content is unchanged.

    Returns the new snapshot id, or None when the XML is identical to the
    latest snapshot for this project.
    """
    blob_hash = xml_hash(xml_bytes)
    latest = db.get_latest_snapshot(project_path, path=db_path)

    if latest is not None and latest["blob_hash"] == blob_hash:
        logger.info(
            "Content unchanged for %s (hash %s…) — skipping snapshot",
            project_path,
            blob_hash[:12],
        )
        return None

    if name_override is not None:
        name = name_override
    elif latest is None:
        name = NAME_INITIAL
    else:
        previous = db.get_snapshot(latest["id"], path=db_path)
        result = diff_snapshots(previous, parsed_to_dict(parsed))
        name = result.summary if result.changes else NAME_NO_STRUCTURAL_CHANGES

    snapshot_id = db.save_snapshot(
        project_path,
        blob_hash,
        gzip.compress(xml_bytes, GZIP_LEVEL),
        parsed,
        name_auto=name,
        path=db_path,
    )
    logger.info("Snapshot saved id=%d name=%r", snapshot_id, name)
    return snapshot_id
