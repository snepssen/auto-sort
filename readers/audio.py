"""Tags and stream geometry, from the front and back of the file.

What people want from audio sorting is almost entirely in the tags: artist,
album, track, year. Those are written by the tagger rather than measured, so
they are `STRONG` rather than `CERTAIN` — a tag can be wrong, unlike a sample
rate — but they beat anything a filename says, which is the point of reading
them at all.

Duration is the other half. It is not a tag; it is derived from the stream,
and it separates a voice memo from an album track from a four-second sample
without anyone having to name anything.
"""

from __future__ import annotations

import struct

from evidence import CERTAIN, STRONG, LIKELY, WEAK
from .boxes import atoms

# The four tag vocabularies, mapped onto one set of names.
_ID3 = {
    "TIT2": "song_title", "TT2": "song_title",
    "TPE1": "artist", "TP1": "artist",
    "TPE2": "album_artist", "TP2": "album_artist",
    "TALB": "album", "TAL": "album",
    "TRCK": "track", "TRK": "track",
    "TPOS": "disc", "TPA": "disc",
    "TCON": "genre", "TCO": "genre",
    "TYER": "year", "TYE": "year", "TDRC": "year", "TDRL": "year",
    "TCOM": "composer", "TCM": "composer",
    "TPUB": "publisher", "TENC": "encoded_by", "TSSE": "encoder",
    "TBPM": "bpm", "TKEY": "musical_key", "TCMP": "compilation",
    "COMM": "comment", "TSRC": "isrc",
}
_VORBIS = {
    "title": "song_title", "artist": "artist", "albumartist": "album_artist",
    "album": "album", "tracknumber": "track", "discnumber": "disc",
    "genre": "genre", "date": "year", "composer": "composer",
    "organization": "publisher", "encoder": "encoder", "bpm": "bpm",
    "comment": "comment", "isrc": "isrc", "musicbrainz_trackid": "mbid",
}
_ILST = {
    "\xa9nam": "song_title", "\xa9ART": "artist", "aART": "album_artist",
    "\xa9alb": "album", "trkn": "track", "disk": "disc",
    "\xa9gen": "genre", "gnre": "genre", "\xa9day": "year",
    "\xa9wrt": "composer", "\xa9too": "encoder", "cprt": "copyright",
    "\xa9cmt": "comment", "cpil": "compilation", "tmpo": "bpm",
    "desc": "description", "ldes": "description",
}
_RIFF_INFO = {"INAM": "song_title", "IART": "artist", "IPRD": "album",
              "IGNR": "genre", "ICRD": "year", "ISFT": "encoder",
              "ICMT": "comment", "ITRK": "track"}

_LOSSLESS = {"flac", "wav", "aiff", "alac", "monkeys-audio", "wavpack",
             "tta", "dsd", "core-audio"}

_MPEG_RATES = (
    # version, layer -> bitrate table index
    (None, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
)
_SAMPLE_RATES = {0: (44100, 22050, 11025), 1: (48000, 24000, 12000),
                 2: (32000, 16000, 8000)}


def _text(raw, encoding_byte=None):
    if isinstance(raw, str):
        return raw.strip("\x00").strip() or None
    if encoding_byte is None:
        data, codec = raw, "utf-8"
    else:
        data = raw
        codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be",
                 3: "utf-8"}.get(encoding_byte, "latin-1")
    try:
        text = data.decode(codec, "replace")
    except (LookupError, UnicodeDecodeError):
        text = data.decode("latin-1", "replace")
    return text.replace("\x00", " ").strip() or None


def _id3v2(peek):
    """ID3v2.2, 2.3 and 2.4 frames. Sizes are syncsafe from 2.4 onwards."""
    data = peek.at(0, 262144)
    if data[:3] != b"ID3":
        return {}, 0
    major = data[3]
    size = 0
    for byte in data[6:10]:
        size = (size << 7) | (byte & 0x7F)
    end = min(10 + size, len(data))
    found = {}
    cursor = 10
    if data[5] & 0x40:                       # extended header
        try:
            cursor += struct.unpack(">I", data[10:14])[0]
        except struct.error:
            pass
    width = 3 if major == 2 else 4
    while cursor + width + (3 if major == 2 else 6) <= end:
        name = data[cursor:cursor + width].decode("latin-1", "replace")
        if not name.strip("\x00").strip():
            break
        if major == 2:
            length = int.from_bytes(data[cursor + 3:cursor + 6], "big")
            head = 6
        else:
            raw_size = data[cursor + 4:cursor + 8]
            if major >= 4:
                length = 0
                for byte in raw_size:
                    length = (length << 7) | (byte & 0x7F)
            else:
                length = int.from_bytes(raw_size, "big")
            head = 10
        if length <= 0 or cursor + head + length > end:
            break
        body = data[cursor + head:cursor + head + length]
        key = _ID3.get(name)
        if key and body:
            if name.startswith("COMM") and len(body) > 4:
                body = body[4:].split(b"\x00")[-1]
                value = _text(body)
            else:
                value = _text(body[1:], body[0] if body else 0)
            if value:
                found.setdefault(key, value)
        cursor += head + length
    return found, 10 + size


def _id3v1(peek):
    tail = peek.tail(128)
    if len(tail) < 128 or tail[:3] != b"TAG":
        return {}
    def field(start, width):
        return _text(tail[start:start + width], 0)
    found = {}
    for key, start, width in (("song_title", 3, 30), ("artist", 33, 30),
                              ("album", 63, 30), ("year", 93, 4),
                              ("comment", 97, 30)):
        value = field(start, width)
        if value:
            found[key] = value
    if tail[125] == 0 and tail[126]:
        found["track"] = tail[126]
    return found


def _mpeg_stream(peek, offset):
    """Bitrate, sample rate, channels and duration for an MPEG audio file."""
    data = peek.at(offset, 8192)
    for index in range(len(data) - 4):
        if data[index] != 0xFF or (data[index + 1] & 0xE0) != 0xE0:
            continue
        header = data[index:index + 4]
        version_bits = (header[1] >> 3) & 0x03
        layer_bits = (header[1] >> 1) & 0x03
        rate_index = (header[2] >> 2) & 0x0F
        sample_index = (header[2] >> 2) & 0x03
        sample_index = (header[2] >> 2) & 0x03
        channel_mode = (header[3] >> 6) & 0x03
        if version_bits == 1 or layer_bits == 0 or rate_index in (0, 15):
            continue
        rates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224,
                 256, 320)
        bitrate = rates[rate_index] if rate_index < len(rates) else None
        version_key = {3: 0, 2: 1, 0: 2}.get(version_bits, 0)
        sample_rate = _SAMPLE_RATES.get((header[2] >> 2) & 0x03,
                                        (44100, 22050, 11025))[version_key]
        found = {"channels": 1 if channel_mode == 3 else 2}
        if bitrate:
            found["bitrate"] = bitrate * 1000
        if sample_rate:
            found["samplerate"] = sample_rate
        # Xing/Info gives a frame count, which is the only accurate duration
        # for a variable-bitrate file.
        window = data[index:index + 2048]
        for marker in (b"Xing", b"Info"):
            at = window.find(marker)
            if at != -1:
                flags = int.from_bytes(window[at + 4:at + 8], "big")
                if flags & 1:
                    frames = int.from_bytes(window[at + 8:at + 12], "big")
                    samples = 1152 if version_bits == 3 else 576
                    if frames and sample_rate:
                        found["duration"] = round(
                            frames * samples / float(sample_rate), 2)
                break
        if "duration" not in found and bitrate:
            found["duration"] = round((peek.size - offset) * 8.0
                                      / (bitrate * 1000), 2)
        return found
    return {}


def _flac(peek):
    data = peek.at(0, 262144)
    found = {}
    cursor = 4
    while cursor + 4 <= len(data):
        header = data[cursor]
        block_type = header & 0x7F
        length = int.from_bytes(data[cursor + 1:cursor + 4], "big")
        body = data[cursor + 4:cursor + 4 + length]
        if block_type == 0 and len(body) >= 18:      # STREAMINFO
            bits = int.from_bytes(body[10:18], "big")
            rate = (bits >> 44) & 0xFFFFF
            channels = ((bits >> 41) & 0x07) + 1
            depth = ((bits >> 36) & 0x1F) + 1
            samples = bits & 0xFFFFFFFFF
            found["samplerate"] = rate
            found["channels"] = channels
            found["bit_depth"] = depth
            if rate and samples:
                found["duration"] = round(samples / float(rate), 2)
        elif block_type == 4:                         # VORBIS_COMMENT
            found.update(_vorbis_comment(body))
        if header & 0x80:
            break
        cursor += 4 + length
    return found


def _vorbis_comment(body, start=0):
    found = {}
    try:
        cursor = start
        vendor = int.from_bytes(body[cursor:cursor + 4], "little")
        cursor += 4 + vendor
        count = int.from_bytes(body[cursor:cursor + 4], "little")
        cursor += 4
        for _ in range(min(count, 128)):
            length = int.from_bytes(body[cursor:cursor + 4], "little")
            cursor += 4
            entry = body[cursor:cursor + length].decode("utf-8", "replace")
            cursor += length
            key, _, value = entry.partition("=")
            mapped = _VORBIS.get(key.strip().lower())
            if mapped and value.strip():
                found.setdefault(mapped, value.strip())
    except (IndexError, ValueError):
        pass
    return found


def _ogg(peek):
    data = peek.at(0, 65536)
    found = {}
    rate = None
    opus = data.find(b"OpusHead")
    if opus != -1:
        try:
            found["channels"] = data[opus + 9]
            rate = 48000                      # Opus granules are always 48 kHz
            found["samplerate"] = int.from_bytes(
                data[opus + 12:opus + 16], "little") or 48000
        except IndexError:
            pass
    vorbis = data.find(b"\x01vorbis")
    if vorbis != -1:
        try:
            found["channels"] = data[vorbis + 11]
            rate = int.from_bytes(data[vorbis + 12:vorbis + 16], "little")
            found["samplerate"] = rate
        except IndexError:
            pass
    for marker in (b"\x03vorbis", b"OpusTags"):
        at = data.find(marker)
        if at != -1:
            offset = at + len(marker)
            found.update(_vorbis_comment(data, offset))
            break
    # The granule position on the last page is the sample count.
    tail = peek.tail(65536)
    last = tail.rfind(b"OggS")
    if last != -1 and rate:
        try:
            granule = int.from_bytes(tail[last + 6:last + 14], "little")
            if 0 < granule < 2 ** 60:
                found["duration"] = round(granule / float(rate), 2)
        except (IndexError, ValueError):
            pass
    return found


def _mp4(peek):
    data = peek.at(0, 1048576)
    found = {}
    for path, body in atoms(data, {"mvhd", "ilst", "stsd"}):
        if path.endswith("mvhd") and len(body) >= 20:
            version = body[0]
            try:
                if version == 1:
                    timescale = int.from_bytes(body[20:24], "big")
                    duration = int.from_bytes(body[24:32], "big")
                else:
                    timescale = int.from_bytes(body[12:16], "big")
                    duration = int.from_bytes(body[16:20], "big")
                if timescale and duration and duration != 0xFFFFFFFF:
                    found["duration"] = round(duration / float(timescale), 2)
            except (ValueError, IndexError):
                pass
        elif path.endswith("stsd") and len(body) >= 36:
            # AudioSampleEntry: channel count, sample size and a 16.16 fixed
            # point sample rate, at fixed offsets after the entry header.
            try:
                found.setdefault("codec", body[12:16].decode("latin-1"))
                channels = int.from_bytes(body[32:34], "big")
                depth = int.from_bytes(body[34:36], "big")
                rate = int.from_bytes(body[40:42], "big")
                if 0 < channels <= 16:
                    found.setdefault("channels", channels)
                if depth in (8, 16, 24, 32):
                    found.setdefault("bit_depth", depth)
                if 8000 <= rate <= 384000:
                    found.setdefault("samplerate", rate)
            except (ValueError, IndexError, UnicodeDecodeError):
                pass
        elif path.endswith("ilst"):
            found.update(_ilst(body))
    if found.get("duration") and not found.get("bitrate"):
        found["bitrate"] = None            # left to the caller's file size
    return found


def _ilst(body):
    found = {}
    cursor = 0
    while cursor + 8 <= len(body):
        size = int.from_bytes(body[cursor:cursor + 4], "big")
        name = body[cursor + 4:cursor + 8].decode("latin-1", "replace")
        if size < 8 or cursor + size > len(body):
            break
        key = _ILST.get(name)
        inner = body[cursor + 8:cursor + size]
        if key:
            at = inner.find(b"data")
            if at != -1:
                payload = inner[at + 12:]
                if name in ("trkn", "disk") and len(payload) >= 4:
                    found.setdefault(key, int.from_bytes(payload[2:4], "big"))
                elif name in ("cpil", "tmpo") and payload:
                    found.setdefault(key, int.from_bytes(payload[:2], "big")
                                     if len(payload) >= 2 else payload[0])
                else:
                    text = payload.decode("utf-8", "replace").strip("\x00")
                    if text.strip():
                        found.setdefault(key, text.strip())
        cursor += size
    return found


def _riff(peek):
    data = peek.at(0, 262144)
    found = {}
    cursor = 12
    while cursor + 8 <= len(data):
        name = data[cursor:cursor + 4]
        size = int.from_bytes(data[cursor + 4:cursor + 8], "little")
        body = data[cursor + 8:cursor + 8 + size]
        if name == b"fmt " and len(body) >= 16:
            channels, rate, byte_rate = struct.unpack("<HII", body[2:12])
            bits = struct.unpack("<H", body[14:16])[0]
            found["channels"] = channels
            found["samplerate"] = rate
            found["bit_depth"] = bits
            if byte_rate:
                found["bitrate"] = byte_rate * 8
                found["_byte_rate"] = byte_rate
        elif name == b"data":
            rate = found.get("_byte_rate")
            length = size if size else peek.size - cursor - 8
            if rate:
                found["duration"] = round(length / float(rate), 2)
            break
        elif name == b"LIST" and body[:4] == b"INFO":
            inner = 4
            while inner + 8 <= len(body):
                tag = body[inner:inner + 4].decode("latin-1", "replace")
                length = int.from_bytes(body[inner + 4:inner + 8], "little")
                value = _text(body[inner + 8:inner + 8 + length], 0)
                if _RIFF_INFO.get(tag) and value:
                    found.setdefault(_RIFF_INFO[tag], value)
                inner += 8 + length + (length & 1)
        cursor += 8 + size + (size & 1)
    found.pop("_byte_rate", None)
    return found


def _aiff(peek):
    data = peek.at(0, 65536)
    cursor = 12
    found = {}
    while cursor + 8 <= len(data):
        name = data[cursor:cursor + 4]
        size = int.from_bytes(data[cursor + 4:cursor + 8], "big")
        body = data[cursor + 8:cursor + 8 + size]
        if name == b"COMM" and len(body) >= 18:
            channels = int.from_bytes(body[0:2], "big")
            frames = int.from_bytes(body[2:6], "big")
            depth = int.from_bytes(body[6:8], "big")
            rate = _extended_float(body[8:18])
            found.update({"channels": channels, "bit_depth": depth})
            if rate:
                found["samplerate"] = int(rate)
                found["duration"] = round(frames / rate, 2)
            break
        cursor += 8 + size + (size & 1)
    return found


def _extended_float(raw):
    """IEEE 754 80-bit, which AIFF uses for the sample rate and nothing else."""
    try:
        exponent = int.from_bytes(raw[0:2], "big") & 0x7FFF
        mantissa = int.from_bytes(raw[2:10], "big")
        if exponent == 0 or exponent == 0x7FFF:
            return None
        return mantissa * (2.0 ** (exponent - 16383 - 63))
    except (IndexError, ValueError, OverflowError):
        return None


def read(peek, fmt, record):
    found = {}
    try:
        if fmt in ("mp3", "aac"):
            tags, offset = _id3v2(peek)
            found.update(tags)
            found.update(_mpeg_stream(peek, offset))
            for key, value in _id3v1(peek).items():
                found.setdefault(key, value)
        elif fmt == "flac":
            found = _flac(peek)
        elif fmt in ("ogg", "opus", "speex"):
            found = _ogg(peek)
        elif fmt in ("mp4-audio", "alac"):
            found = _mp4(peek)
        elif fmt == "wav":
            found = _riff(peek)
        elif fmt == "aiff":
            found = _aiff(peek)
        else:
            return False
    except (struct.error, IndexError, ValueError, OSError, UnicodeError):
        return False
    if not found:
        return False

    tag_source = {"mp3": "id3", "aac": "id3", "flac": "vorbis",
                  "ogg": "vorbis", "opus": "vorbis", "wav": "riff-info",
                  "aiff": "aiff"}.get(fmt, "mp4-tags")

    for key in ("song_title", "artist", "album_artist", "album", "genre",
                "composer", "publisher", "comment", "isrc", "mbid",
                "encoder", "encoded_by", "musical_key", "description",
                "copyright"):
        if found.get(key):
            record.set(key, found[key], tag_source, STRONG)

    for key in ("track", "disc", "bpm"):
        value = found.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            head = value.split("/")[0].strip()
            value = int(head) if head.isdigit() else None
        if isinstance(value, int) and value > 0:
            record.set(key, value, tag_source, STRONG)

    year = found.get("year")
    if year:
        digits = "".join(ch for ch in str(year) if ch.isdigit())[:4]
        if len(digits) == 4 and 1900 <= int(digits) <= 2099:
            record.set("year", int(digits), tag_source, STRONG)
            record.set("released", str(year)[:10], tag_source, STRONG)

    for key in ("duration", "channels", "samplerate", "bitrate", "bit_depth"):
        if found.get(key):
            record.set(key, found[key], "stream", CERTAIN)

    if fmt in _LOSSLESS:
        record.set("lossless", True, "format", CERTAIN)
    channels = found.get("channels")
    if channels:
        record.set("mono", channels == 1, "stream", CERTAIN)
    record.reader_ran("audio:" + fmt, "%d fields" % len(found))
    return True
