"""Restore a snapshot back onto disk as a real .als file.

Backups are written outside the watched tree (default: the data volume) so the
watcher never snapshots them. The .als itself is replaced atomically.
"""

import gzip
import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import db
import snapshots
from parser import parse_als

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/data/.dawgit-backup"))

RESTORE_WARNING = (
    "Ableton does not reload the .als on its own. If this set is currently open, "
    "close it WITHOUT saving and reopen it — otherwise Ableton will overwrite the "
    "restored file from memory on its next save."
)


class SnapshotNotFound(Exception):
    pass


class ProjectPathUnavailable(Exception):
    pass


@dataclass
class RestoreResult:
    snapshot_id: int
    project_path: str
    backup_path: str | None
    new_snapshot_id: int | None
    warning: str


def _backup_path(target: Path, backup_dir: Path) -> Path:
    """Unique name so two projects sharing a basename never collide."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:8]
    return backup_dir / f"{target.stem}__{stamp}__{digest}{target.suffix}"


def backup_current(target: Path, backup_dir: Path | None = None) -> Path | None:
    backup_dir = backup_dir or BACKUP_DIR
    if not target.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = _backup_path(target, backup_dir)
    shutil.copy2(target, destination)
    logger.info("Backed up %s -> %s", target, destination)
    return destination


def write_atomic(target: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, then os.replace."""
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".dawgit-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            shutil.copymode(target, tmp_path)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def restore_snapshot(
    snapshot_id: int,
    db_path: str | None = None,
    backup_dir: Path | None = None,
) -> RestoreResult:
    snapshot = db.get_snapshot(snapshot_id, path=db_path)
    if snapshot is None:
        raise SnapshotNotFound(f"Snapshot {snapshot_id} not found")

    gz = db.get_snapshot_gz(snapshot_id, path=db_path)
    if gz is None:
        raise SnapshotNotFound(f"Snapshot {snapshot_id} has no stored content")

    target = Path(snapshot["project_path"])
    if not target.parent.is_dir():
        raise ProjectPathUnavailable(
            f"Project folder is not available: {target.parent}"
        )

    backup = backup_current(target, backup_dir=backup_dir)
    write_atomic(target, gz)
    logger.info("Restored snapshot %d to %s", snapshot_id, target)

    # Record the revert as its own snapshot. The blob is reused, and the
    # content guard collapses the watcher event this write is about to trigger.
    parsed = parse_als(str(target))
    new_snapshot_id = snapshots.record_snapshot(
        str(target),
        gzip.decompress(gz),
        parsed,
        name_override=f"Restored from snapshot #{snapshot_id}",
        db_path=db_path,
    )

    return RestoreResult(
        snapshot_id=snapshot_id,
        project_path=str(target),
        backup_path=str(backup) if backup else None,
        new_snapshot_id=new_snapshot_id,
        warning=RESTORE_WARNING,
    )
