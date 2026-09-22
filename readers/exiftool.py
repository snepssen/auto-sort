"""Tier 2: asking exiftool about an image whose metadata nothing here reads.

`tiff.py` parses the image file directory structure by hand, which covers
JPEG, TIFF and every camera raw worth the name — DNG, CR2, NEF, ARW, ORF,
RW2, PEF are all TIFFs with private tags, so one parser reads them all. What
it does not cover is the formats that keep their metadata somewhere else
entirely: HEIC's item property boxes, XMP packets written by editing
software, the maker notes of a camera nobody has heard of, AVCHD's sidecar
structure.

Those are each a parser, and each one would be a few hundred lines serving a
handful of files. `exiftool` is twenty years of exactly that work, so where
somebody has it installed it is asked, and where they do not the facts are
absent and the rules that need them decline. Which is what happened before
this file existed.

**The scanner question is asked again here, and that matters.** A flatbed
writes into the same Make and Model tags a camera does, so metadata arriving
through this route goes through the same test as metadata read directly: no
exposure tags plus a device that names itself a scanner means the device is
filed under `scanner` and not `camera`. Skipping that would put twenty years
of somebody's paperwork in Pictures, in folders named after an Epson, which
is the exact bug `scans.py` exists to prevent.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_support                                  # noqa: E402
from evidence import STRONG                              # noqa: E402
from . import scans, tiff                                # noqa: E402

TIMEOUT_SECONDS = 20

# `-n` asks for numbers rather than prettified strings, so a shutter speed
# is 0.004 and not "1/250 sec" and a GPS coordinate is a number rather than
# a sentence with a compass point in it.
_ARGUMENTS = ("-j", "-n", "-fast2")

# exiftool's names for the things this program already has names for.
_FACTS = (
    ("Make", "make"), ("Model", "model"), ("LensModel", "lens"),
    ("Lens", "lens"), ("Software", "software"), ("Artist", "artist"),
    ("Creator", "artist"), ("ImageDescription", "description"),
    ("ISO", "iso"), ("FocalLength", "focal_length"),
    ("FNumber", "aperture"), ("ExposureTime", "exposure_time"),
    ("SerialNumber", "body_serial"),
    ("XResolution", "x_resolution"), ("YResolution", "y_resolution"),
    ("ResolutionUnit", "resolution_unit"),
)

_DATES = (("DateTimeOriginal", "taken"), ("CreateDate", "digitised"),
          ("ModifyDate", "content_modified"))


def wanted(record):
    """Whether this file has a gap worth starting a process for.

    Only pictures whose header this program could not read at all -- which
    is what "no width" means, since every built-in image reader gets that
    much or nothing.

    The obvious gate is "no camera and no date", and it was measured and
    rejected: on one real folder that asked exiftool about 301 pictures,
    took twenty seconds, and found nothing, because a PNG saved from a
    website has no metadata for anybody to read and a JPEG whose EXIF was
    stripped has none either. Asking a second program to confirm an absence
    the first one established is a process launch for no fact.

    What is left is the long tail this program was always going to lose to:
    a PSD, a JPEG XL, a camera raw whose vendor did not follow TIFF closely
    enough for `tiff.py`. There the built-in reader declines outright, and
    exiftool is the difference between some facts and none.
    """
    if record.value("kind") != "image":
        return False
    if record.value("dataless"):
        return False
    return not record.has("width")


def read(path, record):
    """Whatever exiftool knows that this program did not. True if anything."""
    text = platform_support.output("exiftool",
                                   list(_ARGUMENTS) + ["--", path],
                                   TIMEOUT_SECONDS)
    if not text:
        return False
    try:
        report = json.loads(text)
    except ValueError:
        return False
    if not isinstance(report, list) or not report:
        return False
    fields = report[0]
    if not isinstance(fields, dict):
        return False

    found = {}
    for name, key in _FACTS:
        value = fields.get(name)
        if value not in (None, "") and key not in found:
            found[key] = value

    width = _whole(fields.get("ImageWidth") or fields.get("ExifImageWidth"))
    height = _whole(fields.get("ImageHeight") or fields.get("ExifImageHeight"))

    before = len(record)
    # The same question, asked the same way: a scanner writes into the tags
    # a camera writes into, and the answer decides which fact the device
    # goes under. See the module docstring.
    scan = scans.detect(found, width, height, record)
    device = "scanner" if scan else "camera"

    _fill(record, "width", width)
    _fill(record, "height", height)
    _fill(record, device, _text(found.get("model")))
    _fill(record, device + "_make", _text(found.get("make")))
    for key, fact in (("lens", "lens"), ("software", "software"),
                      ("artist", "author"), ("description", "description"),
                      ("iso", "iso"), ("focal_length", "focal_length"),
                      ("aperture", "aperture"),
                      ("body_serial", "serial")):
        _fill(record, fact, _text(found.get(key)))

    for name, fact in _DATES:
        _fill(record, fact, tiff.normalise_datetime(fields.get(name)))

    gps = _coordinates(fields)
    _fill(record, "gps", gps)

    added = len(record) - before
    if added:
        record.reader_ran("exiftool", "%d fact(s) nothing here could read"
                          % added)
    return bool(added)


def _fill(record, name, value):
    """Gaps only. What was read from the file itself already won."""
    if value in (None, "") or record.has(name):
        return
    record.set(name, value, "exiftool", STRONG)


def _coordinates(fields):
    """`lat,lon`, in the same shape the built-in reader produces."""
    latitude = fields.get("GPSLatitude")
    longitude = fields.get("GPSLongitude")
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if str(fields.get("GPSLatitudeRef", "")).upper().startswith("S") \
            and latitude > 0:
        latitude = -latitude
    if str(fields.get("GPSLongitudeRef", "")).upper().startswith("W") \
            and longitude > 0:
        longitude = -longitude
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return "%s,%s" % (round(latitude, 6), round(longitude, 6))


def _whole(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text[:200] or None
