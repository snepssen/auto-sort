"""Reading a page that was photographed rather than typed.

Some documents have no text in them at all. A page put through a scanner is
a picture of words, and every rule this program has -- the induction that
learns what a household's post calls itself, the heading window, the whole
business of letting documents name their own categories -- needs characters
to work on. Without them a scan can only be held.

So: an **optional external program**, found at runtime the way `ffprobe` and
`exiftool` are meant to be, and never a dependency. Absent, nothing changes
and a scan is held exactly as before. Present, the page is read and the
existing induction does the rest, with no new vocabulary anywhere -- what
comes back is text, and this program already knows what to do with text.

Three things worth saying about the shape of it.

**What comes back is weaker evidence, and says so.** A heading lifted from a
text layer is what the document literally contains. A heading from OCR is a
machine's reading of a photograph of it, and `rn` becomes `m` at any
resolution somebody's fax ever used. It is recorded at LIKELY where a text
layer is STRONG, which is the confidence model doing exactly what it exists
for rather than a special case.

**It runs where it can be killed.** OCR is seconds of somebody else's C
code on somebody else's file, which is the definition of the work the
identify worker exists to contain. It gets its own timeout well inside the
worker's, so that a page that will not finish costs one file rather than a
restart.

**It is off unless it is installed.** No download, no bundled model, no
"enable OCR?" dialogue on first run. Installing `tesseract` is the switch,
and `ocr = off` in the rules file is there for somebody who has it installed
for other reasons and does not want minutes of processor time spent on a
folder of holiday photographs of menus.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_support                                  # noqa: E402
from evidence import CERTAIN, LIKELY                      # noqa: E402

# The top of a page is what says what the page is; everything else is
# mentioned further down. The same window the text-layer reader uses.
LETTERHEAD = 500
HEADING_WORDS = 6

# One page at a time, and one page is all that is ever wanted: the heading
# window is the first five hundred characters. Well inside the identify
# worker's own thirty seconds, so that a page which will not finish is one
# unread file rather than a killed worker and a restart.
TIMEOUT_SECONDS = 20

# Enough for the heading induction and then some. A page of dense type is
# around three thousand characters.
MAX_CHARS = 8000

# "auto" means "if tesseract is there". Set from the rules file, and carried
# into the identify worker with the rest of the request, so that both
# processes agree about it.
_mode = "auto"


def configure(mode):
    """`auto` or `off`, from the rules file."""
    global _mode
    _mode = "off" if str(mode).strip().lower() in ("off", "no", "false") \
        else "auto"


def mode():
    return _mode


def available():
    """The OCR program, or None -- which is a perfectly ordinary answer."""
    if _mode == "off":
        return None
    return platform_support.find("tesseract")


def wanted(record):
    """Whether this file is a page nobody has managed to read.

    `needs_ocr` is set by whichever reader gave up: the PDF reader when a
    document has no text layer, the image reader when a scanned page came
    through a scanner. Both mean the same thing here.
    """
    if not available():
        return False
    return bool(record.value("needs_ocr"))


def read(path, record):
    """Read the page, whatever kind of file it arrived in.

    Two shapes, one answer. A PDF holds its page as a picture inside it,
    which is lifted out byte for byte; a scan that arrived as a JPEG or a
    TIFF *is* the picture. Everything recorded afterwards is identical,
    because by then it is text either way.
    """
    from . import pdftext                    # here, to keep the import cheap

    text = ""
    detail = ""
    if record.value("format") == "pdf":
        try:
            with open(path, "rb") as handle:
                page = pdftext.page_image(handle.read(pdftext.MAX_BYTES * 4))
        except (OSError, ValueError, MemoryError):
            page = None
        if page is None:
            record.note("no text layer, and no page-sized picture to read")
            return False
        image, width, height = page
        text = read_image(image)
        detail = "%dx%d page" % (width, height)
        if text:
            record.set("scan_pixels", width * height, "ocr", CERTAIN)
    else:
        text = read_file(path)
        detail = "scanned page"

    if not text:
        record.note("a page was read by OCR and produced nothing")
        return False

    # It was read, so it is no longer waiting to be.
    record.drop("needs_ocr")
    record.set("read_by", "ocr", "ocr", CERTAIN)
    record.set("words_read", len(text.split()), "ocr", CERTAIN)
    from .document import heading_of
    heading = heading_of(text)
    if heading:
        record.set("heading", heading[:80], "ocr", LIKELY)
    record.reader_ran("ocr", "%d words from a %s" % (len(text.split()), detail))
    return True


def read_image(data, suffix=".jpg", languages=""):
    """Text from an image held in memory, or "" if it cannot be read.

    Never raises. A file that could not be read is a file with fewer facts,
    which is the rule everywhere else in this package and is not suspended
    because the reading involved another program.
    """
    program = available()
    if not program or not data:
        return ""
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.write(data)
        handle.close()
        return _run(program, handle.name, languages)
    except (OSError, ValueError):
        return ""
    finally:
        if handle is not None:
            try:
                os.unlink(handle.name)
            except OSError:
                pass


def read_file(path, languages=""):
    """Text from an image already on disk -- a scanned JPEG or TIFF."""
    program = available()
    if not program:
        return ""
    return _run(program, path, languages)


def _run(program, path, languages=""):
    command = [program, path, "stdout"]
    if languages:
        command += ["-l", languages]
    try:
        done = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    if done.returncode != 0:
        return ""
    text = (done.stdout or b"").decode("utf-8", "replace")
    return " ".join(text.split())[:MAX_CHARS]


def languages_installed():
    """Which languages the installed OCR can read, for the log and for
    `explain`. Empty when there is no OCR."""
    program = available()
    if not program:
        return []
    try:
        done = subprocess.run([program, "--list-langs"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=15, universal_newlines=True)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = (done.stdout or "").splitlines()
    return [line.strip() for line in lines[1:] if line.strip()]
