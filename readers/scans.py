"""Telling a scanned page from a photograph, without reading a word of it.

A scanner is a camera as far as EXIF is concerned: it writes into the same
tags, so the machinery that identifies an iPhone identifies an Epson for
free. What it does *not* write is an exposure -- no shutter speed, no
aperture, no ISO, no focal length -- because none of those mean anything to a
lamp on a rail. That absence is the first half of the test.

The second half is the platen. A scan is a picture of a rectangle of known
physical size, and the file says so twice: the pixel dimensions and the
resolution. Divide one by the other and a scanned sheet of A4 gives back
8.27 by 11.69 inches, every time, on every scanner ever sold.

Neither half is enough alone, and the reason is worth recording. Absence of
exposure tags only means "not a camera", which is equally true of artwork,
memes and screenshots. And shape alone is worthless: a run over one real
library flagged six pieces of digital art as A4 purely because root-two is a
pleasant aspect to crop to. It is the *conjunction* -- a page-shaped image at
a resolution a scanner actually offers -- that means something, and only
because the 72 dpi that every editor and every web export writes is not a
resolution anybody scans at.

Photographic print sizes are deliberately not detected on shape alone. A 4x6
print is 3:2, which is simply what most cameras produce, and 5x7 is within a
whisker of A4. Those are recognised only once a scanner has been named
outright, where the size is a detail rather than the evidence.
"""

from __future__ import annotations

from evidence import STRONG, LIKELY

# Substrings that name a scanner in the Make or Model tag. Manufacturer names
# alone are useless here -- Canon and Epson both sell cameras -- so every
# entry has to be a scanner line, not a marque. "scan" on its own covers
# CanoScan, ScanJet, ScanSnap and the many devices that simply say "Scanner".
_DEVICES = (
    "scan",                      # canoscan, scanjet, scansnap, "scanner"
    "lide", "perfection", "documate", "workcentre", "imageclass",
    "imagerunner", "pixma", "officejet", "deskjet", "envy photo",
    "mfc-", "dcp-", "ads-",      # brother multifunction and sheetfeds
    "fi-", "ix500", "ix1400", "ix1500", "ix1600", "s1300", "sv600",
    "kv-s", "ds-510", "ds-530", "ds-570", "epson stylus", "smartsource",
)

# Software that only ever writes a file because somebody scanned something.
_SOFTWARE = (
    "epson scan", "epsonscan", "vuescan", "naps2", "silverfast", "scangear",
    "captureperfect", "captureontouch", "paperport", "readiris", "abbyy",
    "finereader", "hp solution", "hp smart", "hp scan", "image capture",
    "windows fax and scan", "simple scan", "xsane", "scanbot", "genius scan",
    "scanner pro", "docscan", "camscanner", "turboscan", "scansnap",
)

# An editor writing 300 dpi into an export is not a scanner, and Photoshop
# does it constantly. Without this veto every print-resolution artwork in a
# library becomes a scanned page.
_EDITORS = (
    "photoshop", "gimp", "lightroom", "affinity", "illustrator", "indesign",
    "capture one", "luminar", "pixelmator", "paint.net", "krita",
    "clip studio", "paint tool sai", "procreate", "blender", "imagemagick",
    "ffmpeg", "canva", "figma", "sketch", "inkscape", "picasa", "irfanview",
)

# A camera writes at least one of these. A scanner writes none of them.
_EXPOSURE = ("exposure_time", "aperture", "iso", "focal_length", "lens")

# Resolutions scanners actually offer. 72 is excluded on purpose: it is the
# default every editor and web exporter writes, so including it would make
# the shape test fire on most of the internet.
_SCAN_DPI = (100, 120, 150, 200, 240, 300, 360, 400, 600, 720, 800, 1200,
             1600, 2400, 3200, 4800, 9600)

# Short and long edge in inches. Pages only -- print sizes live below.
_PAGES = (
    ("A3", 11.693, 16.535), ("A4", 8.268, 11.693), ("A5", 5.827, 8.268),
    ("A6", 4.134, 5.827), ("B4", 9.843, 13.898), ("B5", 6.929, 9.843),
    ("Letter", 8.5, 11.0), ("Legal", 8.5, 14.0), ("Tabloid", 11.0, 17.0),
    ("Executive", 7.25, 10.5), ("Folio", 8.5, 13.0),
)

# Only ever consulted once a scanner has been identified some other way.
_PRINTS = (
    ("3.5x5", 3.5, 5.0), ("4x6", 4.0, 6.0), ("5x7", 5.0, 7.0),
    ("6x8", 6.0, 8.0), ("8x10", 8.0, 10.0), ("8x12", 8.0, 12.0),
    ("2.5x3.5", 2.5, 3.5),          # wallet prints and trading cards
    ("6x6", 6.0, 6.0), ("3.5x3.5", 3.5, 3.5),   # 120 roll film squares
)

_TOLERANCE = 0.12               # inches, about three millimetres an edge


def _mentions(text, needles):
    if not text:
        return None
    lowered = str(text).lower()
    for needle in needles:
        if needle in lowered:
            return needle
    return None


def _dpi(found):
    """One resolution in dots per inch, or None if the two axes disagree.

    A scan is square-pixelled by construction. Axes that differ mean either a
    deliberately stretched image or a tag nobody filled in honestly, and
    either way the inches that follow would be fiction.
    """
    horizontal = found.get("x_resolution")
    vertical = found.get("y_resolution")
    if not isinstance(horizontal, (int, float)) or horizontal <= 0:
        return None
    if isinstance(vertical, (int, float)) and vertical > 0:
        if abs(horizontal - vertical) > 0.5:
            return None
    unit = found.get("resolution_unit")
    if unit == 3:                       # dots per centimetre
        horizontal *= 2.54
    elif unit not in (2, None):         # 1 means "no unit": the ratio is
        return None                     # meaningless, so the inches are too
    dpi = int(round(horizontal))
    return dpi if dpi in _SCAN_DPI else None


def _paper(width, height, dpi, table):
    """The name of the sheet this many pixels at this resolution would be."""
    short = min(width, height) / float(dpi)
    long_edge = max(width, height) / float(dpi)
    for name, paper_short, paper_long in table:
        if (abs(short - paper_short) <= _TOLERANCE
                and abs(long_edge - paper_long) <= _TOLERANCE):
            return name
    return None


def detect(found, width, height, record):
    """Add `capture = scan` and what it is a scan of, when the tags say so.

    Returns a verdict dict when this is a scan and None when it is not, so
    that the caller can file the device under `scanner` rather than
    `camera`. That distinction is not cosmetic: a rule that gathers
    photographs by the body that took them will happily gather scanned tax
    returns into a folder named after an Epson, and a rule keyed on `camera`
    is the natural way for anybody to write that rule.
    """
    if any(found.get(tag) not in (None, "") for tag in _EXPOSURE):
        return None                     # a camera took this; nothing to do

    device = _mentions(found.get("make"), _DEVICES) \
        or _mentions(found.get("model"), _DEVICES)
    software = _mentions(found.get("software"), _SOFTWARE)
    editor = _mentions(found.get("software"), _EDITORS)

    named = bool(device or software)
    if named:
        record.set("capture", "scan", "exif", STRONG)
        record.note("scanned: the file names %s"
                    % (found.get("model") or found.get("make") or
                       found.get("software")))

    sized = (isinstance(width, int) and isinstance(height, int)
             and width > 0 and height > 0)
    dpi = _dpi(found) if sized else None

    page = None
    if dpi is not None and not (editor and not named):
        page = _paper(width, height, dpi, _PAGES)
    if page:
        record.set("capture", "scan", "dimensions", LIKELY)
        record.set("scan_of", "page", "dimensions", LIKELY)
        record.set("paper", page, "dimensions", LIKELY)
        record.set("scan_dpi", dpi, "exif", STRONG)
        record.note("%dx%d at %d dpi is %s, and there are no exposure tags"
                    % (width, height, dpi, page))
        return {"scan_of": "page", "paper": page, "dpi": dpi}

    if not named:
        return None

    verdict = {"scan_of": "page", "paper": None, "dpi": dpi}
    if dpi is not None:
        record.set("scan_dpi", dpi, "exif", STRONG)
        print_size = _paper(width, height, dpi, _PRINTS)
        if print_size:
            record.set("scan_of", "print", "exif", STRONG)
            record.set("paper", print_size, "exif", STRONG)
            record.note("a scanned %s photographic print" % print_size)
            return {"scan_of": "print", "paper": print_size, "dpi": dpi}
    record.set("scan_of", "page", "exif", LIKELY)
    return verdict
