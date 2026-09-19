"""TIFF image file directories, which is where EXIF actually lives.

One parser covers more ground here than anywhere else in the project. EXIF is
a TIFF file embedded in a JPEG's APP1 segment; a TIFF is a TIFF; and every
camera raw format worth the name — DNG, CR2, NEF, ARW, ORF, RW2, PEF — is a
TIFF with private tags. Parsing the directory structure once and reading tags
by number means the same two hundred lines identify the camera that took a
JPEG, a raw, and the frame of a video shot on the same body.

Only the tags worth sorting by are named. The rest are read and ignored,
because a table of nine hundred tag numbers would be the largest file in the
repository and would answer no question anybody asks a file sorter.
"""

from __future__ import annotations

import struct

# tag -> (name, which directory it is expected in)
TAGS = {
    0x0100: "pixel_width", 0x0101: "pixel_height",
    0x010E: "description", 0x010F: "make", 0x0110: "model",
    0x0112: "orientation", 0x0131: "software", 0x0132: "modified",
    0x013B: "artist", 0x8298: "copyright",
    0x829A: "exposure_time", 0x829D: "aperture",
    0x8827: "iso", 0x8833: "iso", 0x9003: "taken", 0x9004: "digitised",
    0x920A: "focal_length", 0x9209: "flash",
    0xA002: "pixel_width", 0xA003: "pixel_height",
    0xA430: "camera_owner", 0xA431: "body_serial", 0xA434: "lens",
    0xA435: "lens_serial", 0xC612: "dng_version", 0xC614: "unique_model",
    0x00FE: "subfile_type", 0x9C9B: "xp_title", 0x9C9E: "xp_keywords",
}
_POINTERS = {0x8769: "exif", 0x8825: "gps", 0xA005: "interop"}

_GPS = {0x0001: "lat_ref", 0x0002: "lat", 0x0003: "lon_ref", 0x0004: "lon",
        0x0005: "alt_ref", 0x0006: "alt", 0x0007: "gps_time",
        0x001D: "gps_date"}

# TIFF type -> (struct code, bytes)
_TYPES = {1: ("B", 1), 2: ("c", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8),
          6: ("b", 1), 7: ("B", 1), 8: ("h", 2), 9: ("i", 4), 10: ("ii", 8),
          11: ("f", 4), 12: ("d", 8)}


class Reader(object):
    """A TIFF structure inside some buffer, read lazily by tag number."""

    def __init__(self, data, base=0):
        self.data = data
        self.base = base
        self.order = None
        self.first = None
        # HEIF writes a four-byte offset before the TIFF header, and some
        # JPEG writers pad. Nudge forward to the byte-order mark rather than
        # refusing a block that plainly contains one.
        if data[base:base + 2] not in (b"II", b"MM"):
            for nudge in range(1, 12):
                if data[base + nudge:base + nudge + 2] in (b"II", b"MM"):
                    base = self.base = base + nudge
                    break
        marker = data[base:base + 2]
        if marker == b"II":
            self.order = "<"
        elif marker == b"MM":
            self.order = ">"
        else:
            return
        try:
            magic, offset = struct.unpack(self.order + "HI",
                                          data[base + 2:base + 8])
        except struct.error:
            self.order = None
            return
        if magic not in (42, 43):          # 43 is BigTIFF, header only
            self.order = None
            return
        self.first = offset

    def ok(self):
        return self.order is not None and self.first is not None

    def _value(self, kind, count, raw, offset):
        code, width = _TYPES.get(kind, (None, None))
        if code is None:
            return None
        if kind == 2:                       # ASCII
            text = raw.split(b"\x00")[0]
            try:
                return text.decode("utf-8").strip() or None
            except UnicodeDecodeError:
                return text.decode("latin-1", "replace").strip() or None
        if kind == 7:                       # UNDEFINED: leave as bytes
            return raw
        values = []
        for index in range(min(count, 64)):
            chunk = raw[index * width:(index + 1) * width]
            if len(chunk) < width:
                break
            if kind in (5, 10):                 # RATIONAL and SRATIONAL
                pair = "II" if kind == 5 else "ii"
                top, bottom = struct.unpack(self.order + pair, chunk)
                values.append(top / bottom if bottom else 0.0)
            else:
                values.append(struct.unpack(self.order + code, chunk)[0])
        if not values:
            return None
        return values[0] if len(values) == 1 else values

    def directory(self, offset, names=None):
        """One IFD as a dict, plus any sub-directory pointers it held."""
        names = names or TAGS
        found = {}
        pointers = {}
        start = self.base + offset
        try:
            count = struct.unpack(self.order + "H", self.data[start:start + 2])[0]
        except struct.error:
            return found, pointers, None
        if count > 512:                     # corrupt, or not really an IFD
            return found, pointers, None
        cursor = start + 2
        for _ in range(count):
            entry = self.data[cursor:cursor + 12]
            if len(entry) < 12:
                break
            tag, kind, length = struct.unpack(self.order + "HHI", entry[:8])
            width = _TYPES.get(kind, (None, 0))[1] * length
            if width <= 4:
                raw = entry[8:8 + max(width, 1)]
            else:
                where = struct.unpack(self.order + "I", entry[8:12])[0]
                raw = self.data[self.base + where:self.base + where + width]
            if tag in _POINTERS:
                try:
                    pointers[_POINTERS[tag]] = struct.unpack(
                        self.order + "I", entry[8:12])[0]
                except struct.error:
                    pass
            elif tag in names:
                value = self._value(kind, length, raw, cursor)
                if value is not None:
                    found.setdefault(names[tag], value)
            cursor += 12
        following = None
        try:
            following = struct.unpack(self.order + "I",
                                      self.data[cursor:cursor + 4])[0]
        except struct.error:
            pass
        return found, pointers, following


def _decimal(parts, reference):
    """GPS rationals to a signed decimal degree."""
    try:
        if not isinstance(parts, (list, tuple)) or len(parts) < 2:
            return None
        degrees = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
    except (TypeError, ValueError):
        return None
    value = degrees + minutes / 60.0 + seconds / 3600.0
    if str(reference).upper().startswith(("S", "W")):
        value = -value
    return round(value, 6)


def read(data, base=0):
    """Every named tag from IFD0, the Exif IFD and the GPS IFD, as one dict."""
    reader = Reader(data, base)
    if not reader.ok():
        return {}
    found, pointers, following = reader.directory(reader.first)

    for name in ("exif", "interop"):
        if name in pointers:
            more, deeper, _ = reader.directory(pointers[name])
            for key, value in more.items():
                found.setdefault(key, value)
            pointers.update(deeper)

    if "gps" in pointers:
        gps, _p, _n = reader.directory(pointers["gps"], _GPS)
        latitude = _decimal(gps.get("lat"), gps.get("lat_ref"))
        longitude = _decimal(gps.get("lon"), gps.get("lon_ref"))
        if latitude is not None and longitude is not None:
            found["gps"] = "%s,%s" % (latitude, longitude)

    # The second IFD of a raw file usually holds the full-size dimensions;
    # IFD0 holds the embedded thumbnail, which is not what anybody means by
    # "how big is this photo".
    if following and following != reader.first:
        more, _p, _n = reader.directory(following)
        if more.get("pixel_width") and not found.get("pixel_width"):
            found["pixel_width"] = more["pixel_width"]
            found["pixel_height"] = more.get("pixel_height")
    return found


def normalise_datetime(value):
    """EXIF writes `2026:09:19 14:03:22`; everything else wants ISO."""
    if not isinstance(value, str) or len(value) < 19:
        return None
    text = value.strip().replace("/", ":")
    if text[4] == ":" and text[7] == ":":
        text = text[:4] + "-" + text[5:7] + "-" + text[8:]
    if text[:4].isdigit() and text[:4] != "0000":
        return text[:19]
    return None
