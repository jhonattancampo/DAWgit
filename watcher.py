"""File watcher: monitors a folder for .als changes using watchdog with debounce."""

import gzip
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from watchdog.observers import Observer

import db
from diff import diff_snapshots
from parser import parse_als

logger = logging.getLogger(__name__)

_observer: Observer | None = None
_watch_path: str | None = None
_lock = threading.Lock()

DEBOUNCE_SECONDS = 0.5


class _ALSHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        super().__init__()
        self._timers: dict[str, threading.Timer] = {}

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = str(event.src_path)
        if not path.endswith(".als"):
            return
        if "/Backup/" in path or "\\Backup\\" in path:
            logger.info("Skipping backup file: %s", path)
            return
        self._schedule(path)

    def _schedule(self, path: str) -> None:
        existing = self._timers.pop(path, None)
        if existing:
            existing.cancel()
        timer = threading.Timer(DEBOUNCE_SECONDS, self._trigger, args=(path,))
        self._timers[path] = timer
        timer.start()

    def _trigger(self, path: str) -> None:
        self._timers.pop(path, None)
        logger.info("Snapshot triggered: %s", path)
        try:
            parsed = parse_als(path)
            with open(path, "rb") as f:
                xml_raw = gzip.decompress(f.read()).decode("utf-8")

            existing = db.get_snapshots(path)
            if existing:
                prev = db.get_snapshot(existing[0]["id"])
                result = diff_snapshots(prev, {
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
                })
                name = result.summary
            else:
                name = "Initial snapshot"

            snapshot_id = db.save_snapshot(path, xml_raw, parsed)
            db.update_snapshot_name(snapshot_id, name)
            logger.info("Snapshot saved id=%d name=%r", snapshot_id, name)
        except Exception:
            logger.exception("Failed to process snapshot for %s", path)


def start_watcher(path: str) -> None:
    global _observer, _watch_path
    with _lock:
        if _observer is not None:
            logger.warning("Watcher already running on %s", _watch_path)
            return
        handler = _ALSHandler()
        observer = Observer()
        observer.schedule(handler, path, recursive=True)
        observer.start()
        _observer = observer
        _watch_path = path
        logger.info("Watcher started on %s", path)


def stop_watcher() -> None:
    global _observer, _watch_path
    with _lock:
        if _observer is None:
            return
        _observer.stop()
        _observer.join()
        _observer = None
        _watch_path = None
        logger.info("Watcher stopped")


def watcher_status() -> dict:
    with _lock:
        return {"active": _observer is not None, "path": _watch_path}
