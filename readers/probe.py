"""Tier 2: asking ffprobe about a container this program could not parse.

The built-in readers parse the containers worth parsing by hand — MP4 and
QuickTime's `moov`, Matroska's elements, the ID3 and Vorbis and FLAC tag
blocks — because that is a few hundred lines and makes the common case free
and dependency-less. What they cannot do is the long tail: a fragmented MP4
whose `moov` is at the end of a nine-gigabyte file, an AVI written by a
camcorder in 2004, a WMV, a stream remuxed by something that left the header
half-written.

So when a video or a piece of audio comes back with no duration, and
`ffprobe` happens to be installed, it is asked. Measured on one real folder:
**9 of 45 videos** had no duration from the built-in parser, and every one
of them is a file somebody would reasonably expect to be sorted by length.

Three things make this safe to have and safe to be without.

**It only runs when something is missing.** A file whose header parsed
cleanly never launches a process, so the cost is paid by the files that
would otherwise have no facts at all.

**It fills gaps and never argues.** A fact the built-in reader established
is left exactly as it was. Two parsers disagreeing about a duration by three
hundredths of a second is not a disagreement worth recording, and the file's
own header is the better authority about itself anyway.

**Absent, nothing changes.** No ffprobe means no facts, which means a rule
that needs a duration declines, which is the same thing that happened
before this file existed.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_support                                  # noqa: E402
from evidence import CERTAIN, STRONG                     # noqa: E402

TIMEOUT_SECONDS = 20

# What is asked for. Deliberately not `-show_frames`, which reads the whole
# file: everything here comes out of the container's own index.
_ARGUMENTS = ("-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", "-show_chapters")


def wanted(record):
    """Whether this file has a gap worth starting a process for."""
    kind = record.value("kind")
    if kind not in ("video", "audio"):
        return False
    if not record.has("duration"):
        return True
    return kind == "video" and not record.has("width")


def read(path, record):
    """Fill in what the built-in parser could not. Returns True if it did."""
    text = platform_support.output("ffprobe",
                                   list(_ARGUMENTS) + ["--", path],
                                   TIMEOUT_SECONDS)
    if not text:
        return False
    try:
        report = json.loads(text)
    except ValueError:
        return False
    if not isinstance(report, dict):
        return False

    before = len(record)
    _container(report.get("format") or {}, record)
    _streams(report.get("streams") or [], record)
    chapters = report.get("chapters")
    if chapters and not record.has("chapters"):
        record.set("chapters", len(chapters), "ffprobe", CERTAIN)
    added = len(record) - before
    if added:
        record.reader_ran("ffprobe", "%d fact(s) the header did not give"
                          % added)
    return bool(added)


def _fill(record, name, value, confidence=CERTAIN):
    """Set a fact only if nothing established it already.

    Gaps, not arguments -- see the module docstring.
    """
    if value in (None, "") or record.has(name):
        return
    record.set(name, value, "ffprobe", confidence)


def _container(container, record):
    if not isinstance(container, dict):
        return
    _fill(record, "duration", _seconds(container.get("duration")))
    _fill(record, "bitrate", _whole(container.get("bit_rate")))
    tags = container.get("tags") or {}
    lowered = dict((str(key).lower(), value) for key, value in tags.items())
    # Not `encoder`. A container's own encoder tag is the muxer that wrote
    # the file -- "Lavf62.4.100" -- and what anybody means by the encoder of
    # a video is the codec its picture is in, which the stream knows.
    for tag, fact in (("title", "title"), ("artist", "artist"),
                      ("album", "album"), ("genre", "genre")):
        _fill(record, fact, _text(lowered.get(tag)), STRONG)
    _fill(record, "created", _text(lowered.get("creation_time")), STRONG)


def _streams(streams, record):
    audio = subtitles = 0
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        kind = stream.get("codec_type")
        if kind == "video" and not stream.get("disposition", {}).get(
                "attached_pic"):
            # Cover art is a video stream as far as a container is
            # concerned, and an album's artwork is not the film's picture.
            _fill(record, "width", _whole(stream.get("width")))
            _fill(record, "height", _whole(stream.get("height")))
            _fill(record, "encoder", _text(stream.get("codec_name")), STRONG)
            _fill(record, "fps", _rate(stream.get("avg_frame_rate")
                                       or stream.get("r_frame_rate")))
            if not record.has("duration"):
                _fill(record, "duration", _seconds(stream.get("duration")))
        elif kind == "audio":
            audio += 1
            if audio == 1:
                _fill(record, "channels", _whole(stream.get("channels")))
                _fill(record, "samplerate", _whole(stream.get("sample_rate")))
                if record.value("kind") == "audio":
                    _fill(record, "encoder",
                          _text(stream.get("codec_name")), STRONG)
                    _fill(record, "duration",
                          _seconds(stream.get("duration")))
        elif kind == "subtitle":
            subtitles += 1
    if audio and not record.has("audio_tracks"):
        record.set("audio_tracks", audio, "ffprobe", CERTAIN)
    if subtitles and not record.has("subtitle_tracks"):
        record.set("subtitle_tracks", subtitles, "ffprobe", CERTAIN)
    if audio == 1 and not record.has("mono"):
        channels = record.value("channels")
        if channels:
            record.set("mono", channels == 1, "ffprobe", CERTAIN)


def _seconds(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # A container that reports zero or a nonsense length has not reported
    # one. Absent is a better answer than a duration of nothing.
    return round(number, 3) if 0 < number < 86400 * 30 else None


def _whole(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _rate(value):
    """`30000/1001` is how a container says 29.97."""
    if not value:
        return None
    text = str(value)
    if "/" in text:
        top, _, bottom = text.partition("/")
        try:
            top, bottom = float(top), float(bottom)
        except ValueError:
            return None
        if bottom == 0:
            return None
        rate = top / bottom
    else:
        try:
            rate = float(text)
        except ValueError:
            return None
    return round(rate, 3) if 0 < rate < 1000 else None


def _text(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text[:200] or None
