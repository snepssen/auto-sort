"""Tier 1: what a file's own header says about it, and Tier 2: what a
program somebody happened to install says about it.

One entry point. `read(peek, kind, fmt, record)` picks the reader for the kind
and lets it add facts, and every reader is allowed to decline — an unsupported
format, a truncated file, a header that does not parse — by returning False.
Nothing here raises: a file that cannot be read is a file with fewer facts,
not a sorting run that stops halfway through somebody's Downloads folder.
"""

from __future__ import annotations

from . import audio, document, exiftool, image, probe, video

_BY_KIND = {
    "image": image.read,
    "audio": audio.read,
    "video": video.read,
    "document": document.read,
    "subtitle": document.read,
    "code": document.read,
}


def read(peek, kind, fmt, record):
    """Run the Tier 1 reader for this kind. Returns True if it read anything."""
    reader = _BY_KIND.get(kind)
    if reader is None:
        return False
    try:
        return bool(reader(peek, fmt, record))
    except Exception as error:               # noqa: BLE001 - see module docstring
        record.note("%s reader declined: %s" % (kind, error))
        return False


# Tier 2. Each one is asked only about the files that came back with a gap
# it could fill, so a folder of files that parsed cleanly launches no
# processes at all -- and a machine with neither program installed behaves
# exactly as it did before they were offered.
_ENRICHERS = (probe, exiftool)


def enrich(path, record):
    """Ask the optional programs about what is still missing.

    Never raises, never required, and never the reason a file is not
    sorted. An enricher that is not installed is indistinguishable here
    from one that found nothing, which is the point of the tier.
    """
    added = False
    for enricher in _ENRICHERS:
        try:
            if enricher.wanted(record) and enricher.read(path, record):
                added = True
        except Exception as error:           # noqa: BLE001
            record.note("%s declined: %s"
                        % (enricher.__name__.rsplit(".", 1)[-1], error))
    return added


# An audio-only MP4 arrives claiming to be video and vice versa, so the two
# container readers are tried in both directions when the first finds nothing.
_CONTAINER_FALLBACK = {"video": audio.read, "audio": video.read}


def read_with_fallback(peek, kind, fmt, record):
    if read(peek, kind, fmt, record):
        return True
    fallback = _CONTAINER_FALLBACK.get(kind)
    if fallback is None:
        return False
    try:
        return bool(fallback(peek, fmt, record))
    except Exception:                        # noqa: BLE001
        return False
