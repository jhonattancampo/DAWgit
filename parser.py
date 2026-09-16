"""ALS parser: decompresses gzip, parses XML, extracts tracks/tempo/time signature/scenes."""

import gzip
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Where a third-party plugin's display name lives, per plugin format. Each of
# these paths has a same-named decoy nested under its own Preset element
# (e.g. Vst3PluginInfo/Preset/Vst3Preset/Name is always empty) — a recursive
# `.//Name` search hits that decoy before the real one, so these must stay
# exact relative paths.
_PLUGIN_NAME_PATHS = (
    "PluginDesc/Vst3PluginInfo/Name",
    "PluginDesc/VstPluginInfo/PlugName",
    "PluginDesc/AuPluginInfo/Name",
)

# Native devices don't self-report a display name anywhere in the XML —
# checked first: DeviceId/@Name and BrowserContentPath both looked promising
# but neither holds up. DeviceId/@Name is just the tag repeated (confirmed
# across a real library scan, 1686 devices, 0 genuine mismatches — the only
# "mismatches" were a same-named child device nested inside a Rack, an
# artifact of a non-scoped `.//` search, not a real alternate name).
# BrowserContentPath tracks "last browsed content", which coincides with the
# device name for simple effects (Reverb, Gate, Delay) but is a sample or
# preset filename for Simpler/Sampler/Racks (e.g. a Simpler shows the loaded
# .wav there, not "Simpler") — unusable without a per-tag allowlist, which is
# no better than this map. So: a map, built from tags actually seen across
# this user's real Ableton library (~1700 devices, 104 files), not guessed.
# Renaming a device in Ableton still wins over this (see _device_name) — this
# only fires for an unrenamed native device.
_NATIVE_DEVICE_NAMES = {
    "Amp": "Amp",
    "AudioEffectGroupDevice": "Audio Effect Rack",
    "AutoFilter": "Auto Filter",
    "AutoPan": "Auto Pan",
    "Cabinet": "Cabinet",
    "Compressor2": "Compressor",
    "Delay": "Delay",
    "DrumBuss": "Drum Buss",
    "DrumGroupDevice": "Drum Rack",
    "Eq8": "EQ Eight",
    "Gate": "Gate",
    "GlueCompressor": "Glue Compressor",
    "InstrumentGroupDevice": "Instrument Rack",
    "InstrumentVector": "Wavetable",
    "MidiArpeggiator": "Arpeggiator",
    "MultiSampler": "Sampler",
    "OriginalSimpler": "Simpler",
    "Overdrive": "Overdrive",
    "Pedal": "Pedal",
    "PhaserNew": "Phaser-Flanger",
    "Reverb": "Reverb",
    "Roar": "Roar",
    "Saturator": "Saturator",
    "StereoGain": "Utility",
    "Tuner": "Tuner",
}


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
    tempo_automated: bool = False
    tempo_min: float | None = None
    tempo_max: float | None = None
    tempo_points: int = 0


def _find_master_track(liveset: ET.Element) -> ET.Element | None:
    """Return the master/main track element regardless of Live version.

    Live 11 uses <MasterTrack>, Live 12 renamed it to <MainTrack>.
    """
    master = liveset.find("MasterTrack")
    if master is None:
        master = liveset.find("MainTrack")
    return master


def _plugin_name(device_el: ET.Element) -> str | None:
    """The plugin's own display name (VST3/VST2/AU), or None for a native device."""
    for path in _PLUGIN_NAME_PATHS:
        name_el = device_el.find(path)
        if name_el is not None and name_el.get("Value", "").strip():
            return name_el.get("Value").strip()
    return None


def _device_name(device_el: ET.Element, track_name: str) -> str:
    user_name = device_el.find("UserName")
    renamed = user_name.get("Value", "").strip() if user_name is not None else ""

    # Third-party plugin: combine the user's own label with the plugin's
    # identity, since neither alone is enough to reconstruct the set later.
    plugin_name = _plugin_name(device_el)
    if plugin_name is not None:
        return f"{renamed} ({plugin_name})" if renamed else plugin_name

    # Native Ableton device: unchanged, a rename replaces the device name.
    if renamed:
        return renamed

    native_name = _NATIVE_DEVICE_NAMES.get(device_el.tag)
    if native_name is not None:
        return native_name

    logger.warning(
        "Unrecognized device <%s> on track %r — no UserName, plugin name, or "
        "known native device mapping; falling back to the tag name",
        device_el.tag, track_name,
    )
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


def _extract_tempo_automation(liveset: ET.Element) -> tuple[bool, float | None, float | None, int]:
    """Read the master track's tempo automation envelope, if any.

    Ableton always writes a lone envelope point matching the current Manual
    value even when tempo was never automated, so a single point is treated
    the same as "no envelope" — only 2+ points count as real automation.
    """
    master = _find_master_track(liveset)
    if master is None:
        return False, None, None, 0

    target = master.find("DeviceChain/Mixer/Tempo/AutomationTarget")
    if target is None:
        return False, None, None, 0
    target_id = target.get("Id")

    envelopes = master.find("AutomationEnvelopes/Envelopes")
    if envelopes is None:
        return False, None, None, 0

    for envelope in envelopes.findall("AutomationEnvelope"):
        pointee = envelope.find("EnvelopeTarget/PointeeId")
        if pointee is None or pointee.get("Value") != target_id:
            continue

        events = envelope.find("Automation/Events")
        if events is None:
            return False, None, None, 0

        values = [float(ev.get("Value")) for ev in events]
        if len(values) <= 1:
            return False, None, None, 0
        return True, min(values), max(values), len(values)

    return False, None, None, 0


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
            devices = [_device_name(d, track_name) for d in devices_el]

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
    tempo_automated, tempo_min, tempo_max, tempo_points = _extract_tempo_automation(liveset)

    return ParsedSnapshot(
        tempo=tempo,
        time_sig_num=time_sig_num,
        time_sig_den=time_sig_den,
        tracks=tracks,
        tempo_automated=tempo_automated,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        tempo_points=tempo_points,
    )
