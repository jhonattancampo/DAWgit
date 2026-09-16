"""FastAPI application entrypoint for DAWgit."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import db
import restore as restore_service
import watcher
from diff import diff_snapshots
from models import (
    DiffResponse,
    RestoreResponse,
    SnapshotDetail,
    SnapshotListItem,
    TrackDetail,
    WatchRequest,
    WatchStatus,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    projects_path = os.environ.get("ABLETON_PROJECTS_PATH")
    if projects_path:
        if Path(projects_path).exists():
            watcher.start_watcher(projects_path)
        else:
            logger.warning(
                "ABLETON_PROJECTS_PATH=%s does not exist — watcher not started", projects_path
            )
    else:
        logger.warning("ABLETON_PROJECTS_PATH not set — watcher not started")
    yield
    watcher.stop_watcher()


app = FastAPI(title="DAWgit", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _snapshot_to_detail(snap: dict) -> SnapshotDetail:
    meta = snap["meta"]
    return SnapshotDetail(
        id=snap["id"],
        timestamp=snap["timestamp"],
        name_auto=snap["name_auto"],
        project_path=snap["project_path"],
        tempo=meta["tempo"],
        time_sig=f"{meta['time_sig_num']}/{meta['time_sig_den']}",
        tracks=[
            TrackDetail(
                track_id=t["track_id"],
                track_name=t["track_name"],
                track_type=t["track_type"],
                devices=t["devices"],
            )
            for t in snap["tracks"]
        ],
    )


@app.get("/snapshots", response_model=list[SnapshotListItem])
def list_snapshots(project: str) -> list[SnapshotListItem]:
    rows = db.get_snapshots(project)
    return [
        SnapshotListItem(
            id=r["id"],
            timestamp=r["timestamp"],
            name_auto=r["name_auto"],
            project_path=project,
        )
        for r in rows
    ]


@app.get("/snapshots/{snapshot_id}", response_model=SnapshotDetail)
def get_snapshot(snapshot_id: int) -> SnapshotDetail:
    snap = db.get_snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return _snapshot_to_detail(snap)


@app.get("/snapshots/{snapshot_id}/xml")
def get_snapshot_xml(snapshot_id: int) -> dict:
    xml = db.get_snapshot_xml(snapshot_id)
    if xml is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"snapshot_id": snapshot_id, "xml": xml}


@app.post("/snapshots/{snapshot_id}/restore", response_model=RestoreResponse)
def restore_snapshot(snapshot_id: int) -> RestoreResponse:
    try:
        result = restore_service.restore_snapshot(snapshot_id)
    except restore_service.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except restore_service.ProjectPathUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Restore failed for snapshot %d", snapshot_id)
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc

    return RestoreResponse(
        snapshot_id=result.snapshot_id,
        project_path=result.project_path,
        backup_path=result.backup_path,
        new_snapshot_id=result.new_snapshot_id,
        warning=result.warning,
    )


@app.get("/diff/{id_a}/{id_b}", response_model=DiffResponse)
def diff(id_a: int, id_b: int) -> DiffResponse:
    snap_a = db.get_snapshot(id_a)
    snap_b = db.get_snapshot(id_b)
    if snap_a is None or snap_b is None:
        raise HTTPException(status_code=404, detail="One or both snapshots not found")
    if snap_a["project_path"] != snap_b["project_path"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot diff snapshots from different projects: "
                   f"{snap_a['project_path']!r} vs {snap_b['project_path']!r}",
        )
    result = diff_snapshots(snap_a, snap_b)
    return DiffResponse(
        snapshot_a_id=id_a,
        snapshot_b_id=id_b,
        changes=result.changes,
        summary=result.summary,
    )


@app.post("/watch", response_model=WatchStatus)
def start_watch(body: WatchRequest) -> WatchStatus:
    if not Path(body.path).exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")
    watcher.stop_watcher()
    watcher.start_watcher(body.path)
    return WatchStatus(**watcher.watcher_status())


@app.delete("/watch", response_model=WatchStatus)
def stop_watch() -> WatchStatus:
    watcher.stop_watcher()
    return WatchStatus(**watcher.watcher_status())


@app.get("/watch/status", response_model=WatchStatus)
def watch_status() -> WatchStatus:
    return WatchStatus(**watcher.watcher_status())
