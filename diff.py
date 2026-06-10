"""Diff engine: compares two snapshots and produces human-readable change descriptions."""

from dataclasses import dataclass


@dataclass
class DiffResult:
    changes: list[str]
    summary: str


def _build_summary(changes: list[str]) -> str:
    if not changes:
        return "No changes"
    if len(changes) == 1:
        return changes[0]
    if len(changes) == 2:
        return f"{changes[0]}, {changes[1]}"
    return f"{len(changes)} changes — {changes[0]}, {changes[1]}..."


def diff_snapshots(snapshot_a: dict, snapshot_b: dict) -> DiffResult:
    meta_a = snapshot_a["meta"]
    meta_b = snapshot_b["meta"]
    changes = []

    if abs(meta_a["tempo"] - meta_b["tempo"]) > 0.5:
        changes.append(
            f"Tempo changed: {meta_a['tempo']:g} → {meta_b['tempo']:g} BPM"
        )

    num_a, den_a = meta_a["time_sig_num"], meta_a["time_sig_den"]
    num_b, den_b = meta_b["time_sig_num"], meta_b["time_sig_den"]
    if (num_a, den_a) != (num_b, den_b):
        changes.append(
            f"Time signature changed: {num_a}/{den_a} → {num_b}/{den_b}"
        )

    tracks_a = {t["track_id"]: t for t in snapshot_a["tracks"]}
    tracks_b = {t["track_id"]: t for t in snapshot_b["tracks"]}

    for tid, track in tracks_b.items():
        if tid not in tracks_a:
            changes.append(f"Track '{track['track_name']}' added ({track['track_type']})")

    for tid, track in tracks_a.items():
        if tid not in tracks_b:
            changes.append(f"Track '{track['track_name']}' removed")

    for tid in tracks_a.keys() & tracks_b.keys():
        ta, tb = tracks_a[tid], tracks_b[tid]

        if ta["track_name"] != tb["track_name"]:
            changes.append(f"Track renamed: '{ta['track_name']}' → '{tb['track_name']}'")

        # devices are stored as a flat list on snapshot_tracks rows;
        # the dict passed here comes from get_snapshot which returns track rows.
        # device lists live on ParsedTrack during parsing but are NOT persisted
        # per-track in the current schema — they're accessible via xml_raw.
        # For Phase 1 we compare devices only when the caller provides them.
        devices_a = set(ta.get("devices") or [])
        devices_b = set(tb.get("devices") or [])
        track_name = tb["track_name"]
        for dev in devices_b - devices_a:
            changes.append(f"Device '{dev}' added to '{track_name}'")
        for dev in devices_a - devices_b:
            changes.append(f"Device '{dev}' removed from '{track_name}'")

    return DiffResult(changes=changes, summary=_build_summary(changes))
