"""Pydantic models for FastAPI request/response schemas."""

from pydantic import BaseModel, Field


class TrackDetail(BaseModel):
    track_id: str
    track_name: str
    track_type: str
    devices: list[str]


class SnapshotListItem(BaseModel):
    id: int
    timestamp: str
    name_auto: str | None
    name_user: str | None
    project_path: str


class SnapshotDetail(BaseModel):
    id: int
    timestamp: str
    name_auto: str | None
    name_user: str | None
    project_path: str
    tempo: float
    tempo_automated: bool
    tempo_min: float | None
    tempo_max: float | None
    tempo_points: int
    time_sig: str
    tracks: list[TrackDetail]


class DiffResponse(BaseModel):
    snapshot_a_id: int
    snapshot_b_id: int
    changes: list[str]
    summary: str


class RenameRequest(BaseModel):
    name: str | None = Field(
        default=None,
        description="User-supplied name. null clears it and falls back to name_auto.",
    )


class RestoreResponse(BaseModel):
    snapshot_id: int
    project_path: str
    backup_path: str | None
    new_snapshot_id: int | None
    warning: str


class HealthResponse(BaseModel):
    status: str
    db_ok: bool
    watcher_active: bool
    watch_path: str | None


class WatchRequest(BaseModel):
    path: str


class WatchStatus(BaseModel):
    active: bool
    path: str | None
