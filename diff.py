"""Diff engine: compares two snapshots and produces human-readable change descriptions."""

from collections import Counter
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


def _fmt_tempo_range(tempo_min: float, tempo_max: float) -> str:
    return f"{tempo_min:g}–{tempo_max:g} BPM"


def _fmt_tempo_env(tempo_min: float, tempo_max: float, tempo_points: int) -> str:
    return f"{_fmt_tempo_range(tempo_min, tempo_max)} ({tempo_points} pts)"


def diff_snapshots(snapshot_a: dict, snapshot_b: dict) -> DiffResult:
    meta_a = snapshot_a["meta"]
    meta_b = snapshot_b["meta"]
    changes = []

    tempo_a, tempo_b = meta_a["tempo"], meta_b["tempo"]
    automated_a = meta_a.get("tempo_automated", False)
    automated_b = meta_b.get("tempo_automated", False)
    env_a = (meta_a.get("tempo_min"), meta_a.get("tempo_max"), meta_a.get("tempo_points", 0))
    env_b = (meta_b.get("tempo_min"), meta_b.get("tempo_max"), meta_b.get("tempo_points", 0))
    manual_changed = abs(tempo_a - tempo_b) > 0.5

    # "Base" (Manual) and "envelope" (min/max/points) are independent axes
    # once tempo is automated — the base value stops being what plays, so
    # reporting them as one merged sentence (as this used to) can show a
    # number that's outside the range the same line claims is audible.
    if automated_a and not automated_b:
        changes.append(
            f"Automation de tempo desactivada: ahora fijo en {tempo_b:g} BPM — "
            f"antes corría {_fmt_tempo_env(*env_a)}"
        )
    elif not automated_a and automated_b:
        changes.append(
            f"Automation de tempo activada: ahora corre {_fmt_tempo_env(*env_b)} — "
            f"antes fijo en {tempo_a:g} BPM"
        )
    elif automated_a and automated_b:
        if manual_changed:
            changes.append(
                f"Tempo base: {tempo_a:g} → {tempo_b:g} BPM "
                f"(manda la automation: {_fmt_tempo_range(env_b[0], env_b[1])})"
            )
        if env_a != env_b:
            changes.append(
                f"Automation de tempo cambió: ahora corre {_fmt_tempo_env(*env_b)} — "
                f"antes {_fmt_tempo_env(*env_a)}"
            )
    elif manual_changed:  # neither automated
        changes.append(f"Tempo changed: {tempo_a:g} → {tempo_b:g} BPM")

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

        # Devices are persisted per track in snapshot_devices and returned by
        # db.get_snapshot; callers building a snapshot dict by hand may omit them.
        #
        # Counter, not set: a second instance of the same plugin (two EQ
        # Eights on one track) is a real change a set would silently
        # collapse. Signal-chain order isn't tracked here — that would need
        # real sequence alignment to tell "reordered" apart from "removed
        # one, added a different one at that slot", which is more than an
        # MVP text diff needs.
        devices_a = Counter(ta.get("devices") or [])
        devices_b = Counter(tb.get("devices") or [])
        track_name = tb["track_name"]

        for dev, n in sorted((devices_b - devices_a).items()):
            label = f"'{dev}' ×{n}" if n > 1 else f"'{dev}'"
            changes.append(f"Device {label} added to '{track_name}'")
        for dev, n in sorted((devices_a - devices_b).items()):
            label = f"'{dev}' ×{n}" if n > 1 else f"'{dev}'"
            changes.append(f"Device {label} removed from '{track_name}'")

    return DiffResult(changes=changes, summary=_build_summary(changes))
