"""ALS parser: decompresses gzip, parses XML, extracts tracks/tempo/time signature/scenes."""

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


TRACK_TYPE_MAP = {
    "MidiTrack":   "MIDI",
    "AudioTrack":  "Audio",
    "ReturnTrack": "Return",
    "MasterTrack": "Master",
    "GroupTrack":  "Group",
}


@dataclass
class ParsedTrack:
    track_id: str
    track_name: str
    track_type: str
    devices: list[str] = field(default_factory=list)


@dataclass
class ParsedSnapshot:
    tempo: float
    time_sig_num: int
    time_sig_den: int
    tracks: list[ParsedTrack] = field(default_factory=list)


def _find_master_track(liveset: ET.Element) -> ET.Element | None:
    """Return the master/main track element regardless of Live version.

    Live 11 uses <MasterTrack>, Live 12 renamed it to <MainTrack>.
    """
    master = liveset.find("MasterTrack")
    if master is None:
        master = liveset.find("MainTrack")
    return master


def _device_name(device_el: ET.Element) -> str:
    user_name = device_el.find("UserName")
    if user_name is not None and user_name.get("Value", "").strip():
        return user_name.get("Value").strip()

    device_id = device_el.find(".//DeviceId")
    if device_id is not None and device_id.get("Name", "").strip():
        return device_id.get("Name").strip()

    return device_el.tag


def _extract_tempo(liveset: ET.Element) -> float:
    master = _find_master_track(liveset)
    if master is not None:
        manual = master.find("DeviceChain/Mixer/Tempo/Manual")
        if manual is not None:
            return float(manual.get("Value"))

    # Last-resort scan: first <Tempo><Manual> with a plausible BPM value
    for tempo_el in liveset.iter("Tempo"):
        manual = tempo_el.find("Manual")
        if manual is not None:
            try:
                val = float(manual.get("Value", ""))
                if 20 <= val <= 999:
                    return val
            except ValueError:
                continue

    raise ValueError(
        "Could not find Tempo element. Ableton version may not be supported. "
        "Please report this .als file."
    )


def _extract_time_sig(liveset: ET.Element) -> tuple[int, int]:
    for rts in liveset.iter("RemoteableTimeSignature"):
        time_el = rts.find("Time")
        if time_el is not None and time_el.get("Value") == "0":
            num = int(rts.find("Numerator").get("Value"))
            den = int(rts.find("Denominator").get("Value"))
            return num, den
    raise ValueError(
        "Could not find TimeSignature element. Ableton version may not be supported. "
        "Please report this .als file."
    )


def _extract_tracks(liveset: ET.Element) -> list[ParsedTrack]:
    tracks_container = liveset.find("Tracks")
    if tracks_container is None:
        return []

    result = []
    for el in tracks_container:
        track_type = TRACK_TYPE_MAP.get(el.tag)
        if track_type is None:
            continue

        name_el = el.find("Name/EffectiveName")
        track_name = name_el.get("Value", "") if name_el is not None else ""

        devices_el = el.find(".//DeviceChain/Devices")
        devices = []
        if devices_el is not None:
            devices = [_device_name(d) for d in devices_el]

        result.append(ParsedTrack(
            track_id=el.get("Id", ""),
            track_name=track_name,
            track_type=track_type,
            devices=devices,
        ))

    return result


def parse_als(path: str) -> ParsedSnapshot:
    with open(path, "rb") as f:
        raw = gzip.decompress(f.read())

    root = ET.fromstring(raw)
    liveset = root.find("LiveSet")
    if liveset is None:
        raise ValueError(f"No <LiveSet> element found in {path}")

    tempo = _extract_tempo(liveset)
    time_sig_num, time_sig_den = _extract_time_sig(liveset)
    tracks = _extract_tracks(liveset)

    return ParsedSnapshot(
        tempo=tempo,
        time_sig_num=time_sig_num,
        time_sig_den=time_sig_den,
        tracks=tracks,
    )
