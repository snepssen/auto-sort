"""Geometry, duration and track layout — the facts that sort video.

Almost nothing here is about what the video shows. It is about shape:
how long, what aspect, how many audio tracks, whether there are subtitles
burned into the container, what wrote it. Those four facts separate a phone
clip from an episode from a film from a screen recording, and they are all in
the first megabyte.

The encoder string is the quiet winner. `Lavf` means something transcoded it,
`HandBrake` means somebody ripped it, and `com.apple.quicktime.model` in an
MOV names the iPhone that shot it — the same fact EXIF gives for a photograph,
in the only place video keeps it.
"""

from __future__ import annotations

import struct

from evidence import CERTAIN, STRONG, LIKELY, WEAK
from .boxes import atoms

# 1904-01-01 to 1970-01-01, which is the epoch QuickTime chose.
_MAC_EPOCH = 2082844800

_HANDLERS = {b"vide": "video", b"soun": "audio", b"sbtl": "subtitle",
             b"text": "subtitle", b"subt": "subtitle", b"clcp": "subtitle",
             b"tmcd": "timecode", b"meta": "metadata"}


def _mp4(peek):
    data = peek.at(0, 2097152)
    found = {"audio_tracks": 0, "subtitle_tracks": 0, "video_tracks": 0}
    wanted = {"mvhd", "tkhd", "hdlr", "stsd", "ilst", "mdhd", "stts", "keys"}
    tracks = []
    current = None
    for path, body in atoms(data, wanted, limit=256):
        name = path.rsplit("/", 1)[-1]
        if name == "mvhd" and len(body) >= 20:
            version = body[0]
            if version == 1:
                timescale = int.from_bytes(body[20:24], "big")
                duration = int.from_bytes(body[24:32], "big")
                created = int.from_bytes(body[4:12], "big")
            else:
                timescale = int.from_bytes(body[12:16], "big")
                duration = int.from_bytes(body[16:20], "big")
                created = int.from_bytes(body[4:8], "big")
            if timescale and duration and duration != 0xFFFFFFFF:
                found["duration"] = round(duration / float(timescale), 2)
            if created > _MAC_EPOCH:
                found["created_epoch"] = created - _MAC_EPOCH
        elif name == "tkhd" and len(body) >= 84:
            offset = 20 if body[0] == 1 else 8
            try:
                width = int.from_bytes(body[offset + 68:offset + 70], "big")
                height = int.from_bytes(body[offset + 72:offset + 74], "big")
                matrix_a = struct.unpack(">i", body[offset + 28:offset + 32])[0]
                matrix_b = struct.unpack(">i", body[offset + 32:offset + 36])[0]
            except (struct.error, IndexError):
                width = height = 0
                matrix_a = matrix_b = 0
            current = {"width": width, "height": height,
                       "rotated": bool(matrix_b) and not matrix_a}
            tracks.append(current)
        elif name == "hdlr" and len(body) >= 12:
            kind = _HANDLERS.get(body[8:12])
            if kind == "video":
                found["video_tracks"] += 1
                if current:
                    current["kind"] = "video"
            elif kind == "audio":
                found["audio_tracks"] += 1
            elif kind == "subtitle":
                found["subtitle_tracks"] += 1
        elif name == "stsd" and len(body) >= 16:
            codec = body[12:16].decode("latin-1", "replace").strip()
            if codec.isprintable() and codec not in ("", "mp4a"):
                found.setdefault("codec", codec)
        elif name == "ilst":
            from .audio import _ilst
            tags = _ilst(body)
            for key in ("encoder", "song_title", "description", "year"):
                if tags.get(key):
                    found.setdefault(key, tags[key])

    for marker, key in ((b"com.apple.quicktime.model", "camera"),
                        (b"com.apple.quicktime.software", "software"),
                        (b"com.apple.quicktime.make", "camera_make"),
                        (b"com.apple.quicktime.location.ISO6709", "gps")):
        at = data.find(marker)
        if at != -1:
            window = data[at + len(marker):at + len(marker) + 96]
            text = _printable(window)
            if text:
                found.setdefault(key, text)

    for track in tracks:
        if track.get("kind") == "video" and track["width"]:
            width, height = track["width"], track["height"]
            if track.get("rotated"):
                width, height = height, width
            found["width"], found["height"] = width, height
            break
    return found


def _printable(window):
    """The first run of printable text in a metadata value box."""
    out = []
    for byte in window:
        if 32 <= byte < 127:
            out.append(chr(byte))
        elif out:
            break
    text = "".join(out).strip()
    return text if len(text) > 1 else None


# ---------------------------------------------------------------------------
# Matroska
# ---------------------------------------------------------------------------

_EBML_IDS = {
    0x1549A966: "info", 0x2AD7B1: "timecode_scale", 0x4489: "duration",
    0x4D80: "muxing_app", 0x5741: "writing_app", 0x7BA9: "title",
    0x4461: "date", 0x1654AE6B: "tracks", 0xAE: "track_entry",
    0x83: "track_type", 0x86: "codec_id", 0x22B59C: "language",
    0xE0: "video", 0xB0: "pixel_width", 0xBA: "pixel_height",
    0x23E383: "default_duration", 0xE1: "audio", 0x9F: "channels",
    0xB5: "sampling", 0x536E: "track_name",
}
_EBML_MASTER = {0x1549A966, 0x1654AE6B, 0xAE, 0xE0, 0xE1, 0x18538067}


def _vint(data, cursor, keep_marker=False):
    """EBML variable-length integer. Returns (value, bytes consumed)."""
    if cursor >= len(data):
        return None, 0
    first = data[cursor]
    if first == 0:
        return None, 0
    length = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 8:
            return None, 0
    value = first if keep_marker else first & (mask - 1)
    for index in range(1, length):
        if cursor + index >= len(data):
            return None, 0
        value = (value << 8) | data[cursor + index]
    return value, length


def _ebml_id(data, cursor):
    value, length = _vint(data, cursor, keep_marker=True)
    return value, length


def _matroska(peek):
    data = peek.at(0, 2097152)
    found = {"audio_tracks": 0, "subtitle_tracks": 0, "video_tracks": 0}
    scale = 1000000

    def walk(start, end, depth):
        cursor = start
        entry = {}
        while cursor < end and depth < 8:
            identifier, id_length = _ebml_id(data, cursor)
            if identifier is None:
                return
            size, size_length = _vint(data, cursor + id_length)
            if size is None:
                return
            body_at = cursor + id_length + size_length
            body_end = min(body_at + size, end)
            name = _EBML_IDS.get(identifier)
            if identifier in _EBML_MASTER or identifier == 0x18538067:
                walk(body_at, body_end, depth + 1)
            elif name:
                body = data[body_at:body_end]
                _absorb(found, name, body, scale)
            cursor = body_end
            if size == 0:
                cursor += 1

    header_size, header_length = _vint(data, 4)
    start = 4 + header_length + (header_size or 0)
    walk(start, len(data), 0)
    if found.get("_duration_raw") and found.get("_scale"):
        found["duration"] = round(found["_duration_raw"]
                                  * found["_scale"] / 1e9, 2)
    found.pop("_duration_raw", None)
    found.pop("_scale", None)
    if found.get("_default_duration"):
        found["fps"] = round(1e9 / found["_default_duration"], 3)
        found.pop("_default_duration")
    return found


def _absorb(found, name, body, scale):
    if name == "timecode_scale":
        found["_scale"] = int.from_bytes(body, "big") or 1000000
    elif name == "duration":
        try:
            found["_duration_raw"] = (struct.unpack(">f", body)[0]
                                      if len(body) == 4
                                      else struct.unpack(">d", body)[0])
        except struct.error:
            pass
    elif name in ("muxing_app", "writing_app"):
        text = body.decode("utf-8", "replace").strip("\x00").strip()
        if text:
            found.setdefault("encoder", text)
    elif name == "title":
        found.setdefault("song_title",
                         body.decode("utf-8", "replace").strip("\x00"))
    elif name == "date":
        pass
    elif name == "track_type":
        kind = int.from_bytes(body, "big")
        if kind == 1:
            found["video_tracks"] += 1
        elif kind == 2:
            found["audio_tracks"] += 1
        elif kind in (0x11, 17):
            found["subtitle_tracks"] += 1
    elif name == "pixel_width":
        found.setdefault("width", int.from_bytes(body, "big"))
    elif name == "pixel_height":
        found.setdefault("height", int.from_bytes(body, "big"))
    elif name == "default_duration":
        found.setdefault("_default_duration", int.from_bytes(body, "big"))
    elif name == "codec_id":
        found.setdefault("codec",
                         body.decode("latin-1", "replace").strip("\x00"))
    elif name == "language":
        text = body.decode("latin-1", "replace").strip("\x00")
        if text and text != "und":
            found.setdefault("language", text)


# ---------------------------------------------------------------------------
# AVI
# ---------------------------------------------------------------------------

def _avi(peek):
    data = peek.at(0, 262144)
    found = {"audio_tracks": 0, "subtitle_tracks": 0, "video_tracks": 0}
    at = data.find(b"avih")
    if at != -1:
        try:
            (micros, _max_rate, _pad, _flags, frames) = struct.unpack(
                "<IIIII", data[at + 8:at + 28])
            width, height = struct.unpack("<II", data[at + 40:at + 48])
            found["width"], found["height"] = width, height
            if micros:
                found["fps"] = round(1e6 / micros, 3)
                if frames:
                    found["duration"] = round(frames * micros / 1e6, 2)
        except (struct.error, IndexError):
            pass
    for marker, key in ((b"vids", "video_tracks"), (b"auds", "audio_tracks"),
                        (b"txts", "subtitle_tracks")):
        found[key] = data.count(b"strh" + marker) or data.count(marker)
    return found


_BY_FORMAT = {
    "mp4": _mp4, "quicktime": _mp4, "3gpp": _mp4, "mp4-audio": _mp4,
    "matroska": _matroska, "webm": _matroska,
    "avi": _avi,
}


def read(peek, fmt, record):
    reader = _BY_FORMAT.get(fmt)
    if reader is None:
        return False
    try:
        found = reader(peek)
    except (struct.error, IndexError, ValueError, OSError, RecursionError):
        return False
    if not found:
        return False

    source = {"matroska": "ebml", "webm": "ebml", "avi": "riff"}.get(
        fmt, "iso-bmff")

    width, height = found.get("width"), found.get("height")
    if width and height:
        record.set("width", width, source, CERTAIN)
        record.set("height", height, source, CERTAIN)
        record.set("aspect", round(width / float(height), 4), source, CERTAIN)
        record.set("resolution_class", _resolution_class(width, height),
                   "derived", STRONG)
    for key in ("duration", "fps", "codec", "language"):
        if found.get(key):
            record.set(key, found[key], source, CERTAIN)
    for key in ("audio_tracks", "subtitle_tracks", "video_tracks"):
        if key in found:
            record.set(key, found[key], source, CERTAIN)
    for key in ("encoder", "software", "camera", "camera_make", "gps",
                "song_title", "description"):
        if found.get(key):
            record.set(key, found[key], source, STRONG)
    if found.get("created_epoch"):
        import datetime
        stamp = datetime.datetime.fromtimestamp(found["created_epoch"])
        record.set("created", stamp.strftime("%Y-%m-%d %H:%M:%S"), source,
                   STRONG)

    # A container with no video track is audio, whatever the extension says.
    if found.get("video_tracks") == 0 and found.get("audio_tracks"):
        record.set("kind", "audio", source + ":no video track", CERTAIN)
        record.note("container has %d audio track(s) and no video"
                    % found["audio_tracks"])
    record.reader_ran("video:" + fmt, "%d fields" % len(found))
    return True


def _resolution_class(width, height):
    short = min(width, height)
    for floor, label in ((2000, "4k"), (1000, "1080p"), (700, "720p"),
                         (540, "576p"), (460, "480p")):
        if short >= floor:
            return label
    return "low"
