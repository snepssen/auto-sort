"""What a Word, OpenDocument, RTF, Markdown or text file says.

Only PDFs had their words read. Everything else was filed by its name and
its metadata, and `Tenancy - Tamas Torok.docx` says less about itself in
its name than on its first page. The people this program is for have
decades of `.docx`, `.odt` and `.rtf` in their Downloads.

Each reader gives the same two things the PDF reader gives: the text near
the top, and runs of `(font, text, size)` so that `pdftext.title` can find
the title the same way it does in a PDF -- by what is set bigger than the
body. A Word file records the size of every run, directly or through its
styles, so the title is found by size here too, in any language, with no
list of what styles are called.

Standard library only. The XML parts are read up to a few megabytes; a
document whose first page is further in than that has a stranger problem.
"""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree

from . import pdftext
from . import worddoc

MAX_PART = 4 * 1024 * 1024      # of any one XML part inside a document
MAX_CHARS = 8000                # of text kept, as for a PDF
MAX_PARAGRAPHS = 400            # read from the start of a document

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"

GAP = pdftext._GAP


def read(path, fmt, head=b""):
    """`(text, runs)` for a document, or `("", [])`. Never raises."""
    try:
        if fmt == "word":
            with open(path, "rb") as handle:
                start = handle.read(8)
            if start == worddoc.MAGIC:
                # Word 97-2003: text only, headed by the top of the page.
                return worddoc.text(_whole(path, worddoc.MAX_FILE)), []
            return _docx(path)
        if fmt == "opendocument-text":
            return _odt(path)
        if fmt == "rtf":
            return _rtf(_whole(path))
        if fmt == "markdown":
            return _markdown(_whole(path, MAX_CHARS * 4))
        if fmt == "plain-text":
            return _plain(_whole(path, MAX_CHARS * 4))
        if fmt == "pages":
            return _pages(path)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile,
            ElementTree.ParseError, UnicodeError, RecursionError):
        pass
    return "", []


def _whole(path, limit=MAX_PART):
    with open(path, "rb") as handle:
        return handle.read(limit)


def _text_of(runs):
    text = "".join(" " if font is GAP else text for font, text, _size in runs)
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def _part(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_PART:
        raise ValueError("%s is too big to read" % name)
    return archive.read(name)


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def _half_points(element):
    """A `w:sz` value, in points."""
    if element is None:
        return None
    size = element.find(_W + "sz")
    if size is None:
        return None
    try:
        return int(size.get(_W + "val")) / 2.0
    except (TypeError, ValueError):
        return None


def _word_styles(archive):
    """`(default size, {style id: (based on, size)})` from styles.xml."""
    default, styles = 11.0, {}
    try:
        root = ElementTree.fromstring(_part(archive, "word/styles.xml"))
    except KeyError:
        return default, styles
    defaults = root.find(_W + "docDefaults/" + _W + "rPrDefault/" + _W + "rPr")
    default = _half_points(defaults) or default
    for style in root.findall(_W + "style"):
        ident = style.get(_W + "styleId")
        based = style.find(_W + "basedOn")
        styles[ident] = (based.get(_W + "val") if based is not None else None,
                         _half_points(style.find(_W + "rPr")))
    return default, styles


def _style_size(styles, ident, default):
    seen = set()
    while ident and ident not in seen:
        seen.add(ident)
        based, size = styles.get(ident, (None, None))
        if size:
            return size
        ident = based
    return None


def _docx(path):
    with zipfile.ZipFile(path) as archive:
        default, styles = _word_styles(archive)
        root = ElementTree.fromstring(_part(archive, "word/document.xml"))
    body = root.find(_W + "body")
    runs = []
    total = 0
    for count, paragraph in enumerate(body.iter(_W + "p") if body is not None
                                      else ()):
        if count >= MAX_PARAGRAPHS or total >= MAX_CHARS:
            break
        properties = paragraph.find(_W + "pPr")
        style = properties.find(_W + "pStyle") if properties is not None \
            else None
        paragraph_size = (_style_size(styles, style.get(_W + "val"), default)
                          if style is not None else None) or \
            _style_size(styles, "Normal", default) or default
        for run in paragraph.iter(_W + "r"):
            run_properties = run.find(_W + "rPr")
            size = _half_points(run_properties)
            if size is None and run_properties is not None:
                character = run_properties.find(_W + "rStyle")
                if character is not None:
                    size = _style_size(styles, character.get(_W + "val"),
                                       default)
            text = "".join(
                node.text or "" if node.tag == _W + "t" else " "
                for node in run if node.tag in (_W + "t", _W + "tab"))
            if text:
                runs.append(("docx", text, size or paragraph_size))
                total += len(text)
        runs.append((GAP, " ", 0.0))
    return _text_of(runs), runs


# ---------------------------------------------------------------------------
# OpenDocument
# ---------------------------------------------------------------------------

def _points(value):
    match = re.match(r"([\d.]+)\s*pt$", value or "")
    return float(match.group(1)) if match else None


def _odf_styles(*roots):
    """`{style name: (parent, size)}` across styles.xml and content.xml."""
    styles = {}
    for root in roots:
        if root is None:
            continue
        for style in root.iter(_STYLE + "style"):
            properties = style.find(_STYLE + "text-properties")
            size = _points(properties.get(_FO + "font-size")) \
                if properties is not None else None
            styles[style.get(_STYLE + "name")] = (
                style.get(_STYLE + "parent-style-name"), size)
    return styles


def _odf_size(styles, name):
    seen = set()
    while name and name not in seen:
        seen.add(name)
        parent, size = styles.get(name, (None, None))
        if size:
            return size
        name = parent
    return None


def _odt(path):
    with zipfile.ZipFile(path) as archive:
        content = ElementTree.fromstring(_part(archive, "content.xml"))
        try:
            shared = ElementTree.fromstring(_part(archive, "styles.xml"))
        except KeyError:
            shared = None
    styles = _odf_styles(shared, content)
    default = _odf_size(styles, "Standard") or _odf_size(
        styles, "Default_20_Paragraph_20_Style") or 12.0
    runs = []
    total = 0
    count = 0
    for element in content.iter():
        if element.tag not in (_TEXT + "p", _TEXT + "h"):
            continue
        count += 1
        if count > MAX_PARAGRAPHS or total >= MAX_CHARS:
            break
        size = _odf_size(styles, element.get(_TEXT + "style-name")) or default
        if element.text:
            runs.append(("odt", element.text, size))
            total += len(element.text)
        for child in element:
            child_size = _odf_size(styles, child.get(_TEXT + "style-name")) \
                or size
            inner = "".join(child.itertext())
            if child.tag in (_TEXT + "s", _TEXT + "tab"):
                inner = " "
            if inner:
                runs.append(("odt", inner, child_size))
                total += len(inner)
            if child.tail:
                runs.append(("odt", child.tail, size))
        runs.append((GAP, " ", 0.0))
    return _text_of(runs), runs


# ---------------------------------------------------------------------------
# RTF
# ---------------------------------------------------------------------------

_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|"
                        rb"\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+")
# Groups whose contents are not the document's text.
_RTF_SKIP = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict",
             b"header", b"footer", b"headerl", b"headerr", b"footerl",
             b"footerr", b"object", b"themedata", b"colorschememapping",
             b"latentstyles", b"datastore", b"xmlnstbl", b"listtable",
             b"listoverridetable", b"rsidtbl", b"generator", b"filetbl"}


def _rtf(data):
    runs = []
    stack = []
    size = 12.0
    skip = False
    pending_star = False
    unicode_skip = 0
    total = 0
    for match in _RTF_TOKEN.finditer(data):
        if total >= MAX_CHARS:
            break
        word, number, hexed, symbol, brace, text = match.groups()
        if brace == b"{":
            stack.append((size, skip))
            pending_star = False
            continue
        if brace == b"}":
            if stack:
                size, skip = stack.pop()
            continue
        if symbol == b"*":
            pending_star = True
            continue
        if word is not None:
            if pending_star or word in _RTF_SKIP:
                skip = True
            pending_star = False
            if word == b"fs" and number:
                size = int(number) / 2.0
            elif word in (b"par", b"line", b"sect", b"page") and not skip:
                runs.append((GAP, " ", 0.0))
            elif word == b"tab" and not skip:
                runs.append(("rtf", " ", size))
            elif word == b"u" and number and not skip:
                runs.append(("rtf", chr(int(number) % 65536), size))
                unicode_skip = 1
            continue
        if hexed is not None:
            if unicode_skip:
                unicode_skip -= 1
                continue
            if not skip:
                runs.append(("rtf", bytes([int(hexed, 16)]).decode("cp1252",
                                                                   "replace"),
                             size))
            continue
        if symbol is not None and not skip:
            runs.append(("rtf", symbol.decode("latin-1"), size))
            continue
        if text and not skip:
            if unicode_skip:
                text = text[unicode_skip:]
                unicode_skip = 0
            decoded = text.decode("cp1252", "replace")
            runs.append(("rtf", decoded, size))
            total += len(decoded)
    return _text_of(runs), runs


# ---------------------------------------------------------------------------
# Plain text, Markdown, Pages
# ---------------------------------------------------------------------------

def _decoded(data):
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8", "replace")


def _plain(data):
    text = re.sub(r"\s+", " ", _decoded(data)).strip()[:MAX_CHARS]
    return text, []


_MARKDOWN_TITLE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.M)


def _markdown(data):
    """A Markdown file names itself with its first `# ` line."""
    text = _decoded(data)
    title = _MARKDOWN_TITLE.search(text[:MAX_CHARS])
    plain, _runs = _plain(data)
    if not title:
        return plain, []
    # Drawn bigger than the body, as the heading it is.
    runs = [("md", title.group(1), 24.0), (GAP, " ", 0.0),
            ("md", plain, 12.0)]
    return plain, runs


def _pages(path):
    """A Pages document keeps a picture of itself: a PDF, in older ones."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for preview in ("QuickLook/Preview.pdf", "preview.pdf"):
            if preview in names:
                data = _part(archive, preview)
                break
        else:
            return "", []

    class _Peek(object):
        def at(self, offset, size):
            return data[offset:offset + size]

    runs, _image_hint, _data = pdftext._collect(_Peek())
    if not runs:
        return "", []
    return pdftext._keep_readable_fonts(runs)[:MAX_CHARS], runs
