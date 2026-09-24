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
import re
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


# Vision, the text recognition every Mac has had since 10.15, reached
# through `osascript` -- which every Mac also has -- so that reading a
# scanned page needs nothing installed. Measured on real pages against
# tesseract: `Werkpostfiche` where tesseract read `Werkoostfiche`, and a
# certificate with a decorative border read as its words rather than as
# `ray Es Ss iS}`. The first call on a machine prepares the models and was
# measured at two minutes; every call after, under half a second a page.
VISION = "vision"
VISION_TIMEOUT_SECONDS = 300
_VISION_SCRIPT = r"""
ObjC.import("Vision");
function run(argv) {
  var url = $.NSURL.fileURLWithPath(argv[0]);
  var handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $());
  var request = $.VNRecognizeTextRequest.alloc.init;
  request.recognitionLevel = 0;
  request.usesLanguageCorrection = true;
  if (request.respondsToSelector("setAutomaticallyDetectsLanguage:")) {
    request.automaticallyDetectsLanguage = true;
  }
  if (!handler.performRequestsError($([request]), $())) { return ""; }
  var results = request.results, lines = [];
  for (var i = 0; i < results.count; i++) {
    var best = results.objectAtIndex(i).topCandidates(1).firstObject;
    if (best) { lines.push(ObjC.unwrap(best.string)); }
  }
  return lines.join("\n");
}
"""


def _vision_here():
    """Whether this Mac has Vision's text recognition (10.15 and later)."""
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/osascript"):
        return False
    import platform
    try:
        major, minor = (int(part) for part in
                        (platform.mac_ver()[0].split(".") + ["0"])[:2])
    except ValueError:
        return False
    return (major, minor) >= (10, 15)


def available():
    """What reads pages here -- `VISION` or tesseract's path -- or None.

    None is a perfectly ordinary answer. On a Mac it is Vision, which
    reads better than tesseract and is already there; anywhere else it is
    tesseract, if somebody installed it.
    """
    if _mode == "off":
        return None
    if _vision_here():
        return VISION
    return platform_support.find("tesseract")


def engine_name():
    """For `explain` and the log page: which engine reads pages here."""
    found = available()
    if found == VISION:
        return "macOS text recognition"
    return "tesseract" if found else ""


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

    if text is None:
        # The program failed or ran out of time: that says nothing about
        # the page, which is still waiting to be read.
        record.note("OCR could not read the page")
        return False
    if not text:
        # Looked at, and nothing there: a map, a photograph, a signature.
        # It is not waiting for a program any more -- the program is here
        # and has answered -- and counting it as waiting told somebody to
        # install something they already had.
        record.drop("needs_ocr")
        record.set("read_by", "ocr", "ocr", CERTAIN)
        record.set("words_read", 0, "ocr", CERTAIN)
        record.note("a page was read by OCR and produced nothing")
        return False

    # It was read, so it is no longer waiting to be.
    record.drop("needs_ocr")
    record.set("read_by", "ocr", "ocr", CERTAIN)
    record.set("words_read", len(text.split()), "ocr", CERTAIN)
    from .document import heading_of
    # Only tesseract reads a certificate's border as `ray Es Ss iS}`; the
    # same stepping-over cut "HollCert Opleiding & Training" off Vision's
    # clean reading of a page, because `&` is not a word.
    heading = heading_of(text if last_engine == VISION
                         else _past_the_border(text))
    if heading:
        record.set("heading", heading[:80], "ocr", LIKELY)
    record.reader_ran("ocr", "%d words from a %s, read by %s" % (
        len(text.split()), detail,
        "macOS text recognition" if last_engine == VISION else "tesseract"))
    return True


_TOKEN_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


def _wordlike(token):
    """Three letters or more, and mostly letters: not `iS}`, `=z`, `#`."""
    letters = len(_TOKEN_LETTERS.findall(token))
    return letters >= 3 and letters >= 0.75 * len(token)


def _past_the_border(text):
    """OCR text from where the words begin.

    A certificate's decorative border reads, to OCR, as `ray Es Ss iS} Ea
    iS` -- and the heading is taken from the top, so that was the heading.
    Words start at the first run of three word-like tokens; if there is no
    such run the text is returned as it was.
    """
    tokens = text.split()
    for index in range(len(tokens) - 2):
        if all(_wordlike(token) for token in tokens[index:index + 3]):
            return " ".join(tokens[index:])
    return text


def read_image(data, suffix=None, languages=""):
    """Text from an image held in memory: "" when the page has none, None
    when it could not be read at all.

    Never raises. A file that could not be read is a file with fewer facts,
    which is the rule everywhere else in this package and is not suspended
    because the reading involved another program.
    """
    program = available()
    if not program or not data:
        return None
    if suffix is None:
        suffix = ".png" if data.startswith(b"\x89PNG") else ".jpg"
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.write(data)
        handle.close()
        return _run(program, handle.name, languages)
    except (OSError, ValueError):
        return None
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
        return None
    return _run(program, path, languages)


# How a page is asked for, as part of what a kept reading is kept against
# (see `jobs._method`). 2: pages are turned the right way up first.
# 3: a page with nothing on it is recorded as read, not left waiting.
# 4: pages stored as compressed pixels are read; headings skip the border.
# 5: on a Mac, pages are read by Vision rather than tesseract.
# 6: the border is stepped over in tesseract's readings only.
METHOD = 6

# Whether this install can tell which way up a page is, per program path.
_turns = {}


def _can_turn_pages(program):
    """True when orientation detection is installed.

    Asked once per program and remembered: it costs a process. Without the
    `osd` data, asking for orientation detection is an error on some
    versions, so it is only asked for when it can be answered.
    """
    if program not in _turns:
        _turns[program] = "osd" in languages_installed()
    return _turns[program]


# Which engine produced the last reading: Vision's output is clean, and
# only tesseract's needs the decorative border stepped over.
last_engine = ""


def _run(program, path, languages=""):
    global last_engine
    if program == VISION:
        text = _run_vision(path)
        if text is not None:
            last_engine = VISION
            return text
        # Vision failing is not the end: tesseract, where it is installed.
        program = platform_support.find("tesseract")
        if not program:
            return None
    last_engine = "tesseract"
    command = [program, path, "stdout"]
    if languages:
        command += ["-l", languages]
    # A page fed through the scanner the wrong way up reads, without this,
    # as `UM@d uaysuaig ... OTOZ JUN!` -- a real form's "JUNI 2020" upside
    # down. Automatic segmentation with orientation detection turns it
    # first; an upright page reads the same either way, for about a fifth
    # of a second more.
    if _can_turn_pages(program):
        command += ["--psm", "1"]
    try:
        done = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    text = (done.stdout or b"").decode("utf-8", "replace")
    return " ".join(text.split())[:MAX_CHARS]


def _run_vision(path):
    try:
        done = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", _VISION_SCRIPT,
             path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=VISION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
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
