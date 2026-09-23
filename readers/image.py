"""How big, what shape, and which camera — from the header, not the pixels.

Nothing here decodes an image. Every fact comes out of a header that is at
most a few hundred bytes in, which is why this is affordable on a folder with
forty thousand photographs in it.

The one inference worth explaining is the screenshot test. A screenshot has no
camera tags and has the exact pixel dimensions of a display — or twice them,
on a retina panel. Neither fact alone means much; together they are strong
enough to file by, and they cost nothing. It is the closest this project gets
to looking at content, and it gets there without a model.
"""

from __future__ import annotations

import re
import struct

from evidence import CERTAIN, STRONG, LIKELY, WEAK
from . import scans, tiff

# Displays people actually own, plus their retina doubles. A photograph that
# happens to land on one of these is possible; a photograph that lands on one
# of these *and* carries no camera tag is not worth worrying about.
_SCREEN_SIZES = set()
for _w, _h in ((1280, 720), (1280, 800), (1366, 768), (1440, 900),
               (1536, 864), (1600, 900), (1680, 1050), (1920, 1080),
               (1920, 1200), (2048, 1152), (2240, 1400), (2256, 1504),
               (2304, 1440), (2560, 1440), (2560, 1600), (2880, 1800),
               (3024, 1964), (3072, 1920), (3440, 1440), (3456, 2234),
               (3840, 2160), (5120, 2880), (6016, 3384),
               (750, 1334), (1080, 1920), (1125, 2436), (1170, 2532),
               (1179, 2556), (1284, 2778), (1290, 2796), (1206, 2622),
               (1320, 2868), (828, 1792), (640, 1136), (1242, 2688),
               (1536, 2048), (1620, 2160), (1668, 2388), (2048, 2732)):
    _SCREEN_SIZES.add((_w, _h))
    _SCREEN_SIZES.add((_h, _w))
    _SCREEN_SIZES.add((_w * 2, _h * 2))
    _SCREEN_SIZES.add((_h * 2, _w * 2))
del _w, _h

_SOF = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg(peek):
    """Walk the segment chain for the frame header and the EXIF block."""
    data = peek.head
    found = {}
    cursor = 2
    exif_at = None
    while cursor + 4 <= len(data):
        if data[cursor] != 0xFF:
            cursor += 1
            continue
        marker = data[cursor + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            cursor += 2
            continue
        try:
            length = struct.unpack(">H", data[cursor + 2:cursor + 4])[0]
        except struct.error:
            break
        if marker in _SOF:
            try:
                _precision, height, width = struct.unpack(
                    ">BHH", data[cursor + 4:cursor + 9])
                found["pixel_height"] = height
                found["pixel_width"] = width
                found["progressive"] = marker in (0xC2, 0xC6, 0xCA)
            except struct.error:
                pass
            break
        if marker == 0xE1 and data[cursor + 4:cursor + 10] == b"Exif\x00\x00":
            exif_at = cursor + 10
        if marker == 0xDA:                 # start of scan: no headers beyond
            break
        cursor += 2 + length

    if exif_at is None:
        # A large EXIF block can push the frame header past 8 KB; the segment
        # offset is still in the head, so a second read is targeted rather
        # than a scan.
        marker = peek.head.find(b"Exif\x00\x00")
        exif_at = marker + 6 if marker != -1 else None
    if exif_at is not None:
        block = peek.at(exif_at, 65536)
        for key, value in tiff.read(block).items():
            found.setdefault(key, value)
    return found


def _png(peek):
    found = {}
    data = peek.head
    try:
        width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
        found["pixel_width"] = width
        found["pixel_height"] = height
        found["bit_depth"] = depth
        found["alpha"] = colour in (4, 6)
    except struct.error:
        return found
    if b"acTL" in data[:4096]:
        found["animated"] = True
    for match in re.finditer(rb"(tEXt|iTXt)", data[:8192]):
        start = match.end()
        chunk = data[start:start + 200]
        key, _, rest = chunk.partition(b"\x00")
        name = key.decode("latin-1", "replace").strip().lower()
        if name in ("software", "source", "description", "comment",
                    "creation time", "author", "parameters"):
            value = rest.split(b"\x00")[-1][:180]
            text = value.decode("utf-8", "replace").strip()
            if text:
                found.setdefault("software" if name == "software"
                                 else "description", text)
    return found


def _gif(peek):
    try:
        width, height = struct.unpack("<HH", peek.head[6:10])
    except struct.error:
        return {}
    found = {"pixel_width": width, "pixel_height": height, "alpha": True}
    if peek.head.count(b"\x00\x21\xf9") > 1 or b"NETSCAPE" in peek.head:
        found["animated"] = True
    return found


def _webp(peek):
    data = peek.head
    chunk = data[12:16]
    found = {}
    try:
        if chunk == b"VP8X":
            flags = data[20]
            found["alpha"] = bool(flags & 0x10)
            found["animated"] = bool(flags & 0x02)
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            found["pixel_width"], found["pixel_height"] = width, height
        elif chunk == b"VP8 ":
            found["pixel_width"] = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            found["pixel_height"] = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        elif chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            found["pixel_width"] = (bits & 0x3FFF) + 1
            found["pixel_height"] = ((bits >> 14) & 0x3FFF) + 1
            found["alpha"] = bool((bits >> 28) & 1)
    except (struct.error, IndexError):
        pass
    return found


def _bmp(peek):
    try:
        width, height = struct.unpack("<ii", peek.head[18:26])
        return {"pixel_width": abs(width), "pixel_height": abs(height)}
    except struct.error:
        return {}


def _heif(peek):
    """`ispe` carries the canvas size — but there is one per stored item.

    A HEIC off a phone holds the photograph, a thumbnail, and often a depth
    map, each with its own `ispe`. Taking the first one reports the thumbnail,
    which is how a 12-megapixel photo comes back as 640x896. The largest is
    the picture.
    """
    window = peek.at(0, 262144)
    found = {}
    best = 0
    marker = window.find(b"ispe")
    while marker != -1:
        try:
            width, height = struct.unpack(">II",
                                          window[marker + 8:marker + 16])
        except struct.error:
            break
        if 0 < width < 100000 and 0 < height < 100000 \
                and width * height > best:
            best = width * height
            found["pixel_width"], found["pixel_height"] = width, height
        marker = window.find(b"ispe", marker + 4)

    # Finding the Exif item properly means parsing `iinf` for the item id
    # and `iloc` for its offset. The string "Exif" also appears in `iinf`
    # itself as the item's type, so searching for it lands on the index
    # rather than the data — which is why the first attempt at this read a
    # thumbnail's worth of nothing.
    #
    # Instead: look for TIFF byte-order marks directly and *verify* each one
    # by parsing it. A run of image data that happens to spell `MM\x00*` will
    # not also parse as a directory containing a camera model, so a candidate
    # that yields one is the real thing.
    for key, value in _find_exif(peek).items():
        found.setdefault(key, value)
    return found


def _find_exif(peek, window_size=4194304, tries=16):
    haystack = peek.at(0, window_size)
    for marker in (b"MM\x00*", b"II*\x00"):
        at = haystack.find(marker)
        attempts = 0
        while at != -1 and attempts < tries:
            parsed = tiff.read(haystack[at:at + 131072])
            if parsed.get("make") or parsed.get("model") \
                    or parsed.get("taken"):
                return parsed
            attempts += 1
            at = haystack.find(marker, at + 4)
    return {}


_SVG_SIZE = re.compile(rb'\b(width|height)\s*=\s*["\']([0-9.]+)', re.I)
_SVG_VIEWBOX = re.compile(rb'viewBox\s*=\s*["\']\s*[-0-9.]+\s+[-0-9.]+\s+'
                          rb'([0-9.]+)\s+([0-9.]+)', re.I)


def _svg(peek):
    found = {"alpha": True, "vector": True}
    box = _SVG_VIEWBOX.search(peek.head)
    if box:
        found["pixel_width"] = int(float(box.group(1)))
        found["pixel_height"] = int(float(box.group(2)))
        return found
    for match in _SVG_SIZE.finditer(peek.head[:2048]):
        key = "pixel_width" if match.group(1).lower() == b"width" \
            else "pixel_height"
        found.setdefault(key, int(float(match.group(2))))
    return found


_BY_FORMAT = {
    "jpeg": _jpeg, "png": _png, "gif": _gif, "webp": _webp, "bmp": _bmp,
    "heif": _heif, "avif": _heif, "svg": _svg,
    "tiff": lambda peek: tiff.read(peek.at(0, 262144)),
    "raw": lambda peek: tiff.read(peek.at(0, 262144)),
}

_LOSSLESS = {"png", "tiff", "bmp", "raw", "netpbm", "psd", "openexr"}


def read(peek, fmt, record):
    """Add what the header says to `record`. Returns True if anything was read."""
    reader = _BY_FORMAT.get(fmt)
    if reader is None:
        return False
    try:
        found = reader(peek)
    except (struct.error, IndexError, ValueError, OSError):
        return False
    if not found:
        return False

    source = "exif" if found.get("make") or found.get("taken") else "header"
    width = found.get("pixel_width")
    height = found.get("pixel_height")
    if isinstance(width, int) and isinstance(height, int) and width and height:
        # EXIF orientation 5-8 means the stored image is rotated; the
        # dimensions a person sees are swapped.
        if found.get("orientation") in (5, 6, 7, 8):
            width, height = height, width
            record.note("dimensions swapped for EXIF orientation %s"
                        % found["orientation"])
        record.set("width", width, source, CERTAIN)
        record.set("height", height, source, CERTAIN)
        record.set("aspect", round(width / float(height), 4), source, CERTAIN)
        record.set("megapixels", round(width * height / 1e6, 1), source,
                   CERTAIN)

    # Decided before the device is named, because what the device *is*
    # changes which fact it belongs under.
    scan = scans.detect(found, width, height, record)
    device = "scanner" if scan else "camera"

    # Model alone names the device; the Make is kept beside it under
    # `<device>_make` rather than glued onto the front. Prepending it reads
    # worse on exactly the files people have most of — "Apple iPhone 13 mini",
    # "NIKON CORPORATION NIKON D7000" — and loses nothing, since the Make is
    # the very next fact along.
    for key, fact in (("make", device + "_make"), ("model", device),
                      ("lens", "lens"), ("software", "software"),
                      ("gps", "gps"), ("artist", "author"),
                      ("description", "description"),
                      ("iso", "iso"), ("focal_length", "focal_length"),
                      ("aperture", "aperture"), ("body_serial", "serial")):
        if found.get(key) not in (None, ""):
            record.set(fact, found[key], "exif", STRONG)

    for key, fact in (("taken", "taken"), ("digitised", "digitised"),
                      ("modified", "content_modified")):
        stamp = tiff.normalise_datetime(found.get(key))
        if stamp:
            record.set(fact, stamp, "exif", STRONG)

    for key in ("alpha", "animated", "progressive", "vector", "bit_depth"):
        if key in found:
            record.set(key, found[key], "header", CERTAIN)
    if fmt in _LOSSLESS:
        record.set("lossless", True, "format", CERTAIN)
    if found.get("dng_version"):
        record.set("format", "raw", "exif", CERTAIN)

    # The screenshot test, which is free and right almost always.
    if width and height and not found.get("make") and not scan:
        if (width, height) in _SCREEN_SIZES:
            record.set("capture", "screenshot", "dimensions", LIKELY)
            record.note("no camera tags and %dx%d is a display size"
                        % (width, height))
    # A scanned page is a document that happens to be stored as a picture,
    # and the only thing standing between it and every rule this program
    # has is that nobody has read the words on it. Saying so is this
    # reader's whole part in that; the reading happens in a process of its
    # own, because it means waiting on somebody else's program.
    #
    # Only a page. A scan of a photograph -- `scan_of = print` -- is
    # somebody digitising an album, and OCR over a picture of a beach costs
    # seconds and returns nothing.
    if scan and record.value("scan_of") == "page":
        record.set("needs_ocr", True, "scan", STRONG)

    record.reader_ran("image:" + fmt, "%d fields" % len(found))
    return True
