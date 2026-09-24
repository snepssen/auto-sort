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
import struct
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
        if fmt == "email":
            return _email(path)
        if fmt == "calendar":
            return _calendar(_whole(path, MAX_PART))
        if fmt == "html":
            return _html(_whole(path, MAX_PART))
        if fmt == "delimited":
            return _plain(_whole(path, MAX_CHARS * 4))
        if fmt == "excel":
            with open(path, "rb") as handle:
                start = handle.read(8)
            if start == worddoc.MAGIC:
                return _xls(_whole(path, worddoc.MAX_FILE)), []
            return _xlsx(path)
        if fmt == "numbers":
            return _numbers(path)
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
    runs = [(KNOWN, title.group(1), 24.0), (GAP, " ", 0.0),
            ("md", plain, 12.0)]
    return plain, runs


def _pages(path):
    """A Pages document: the PDF preview older ones keep, or the text of
    newer ones, which is inside `Index/Document.iwa` -- Snappy-compressed
    protobuf, where the body is a UTF-8 string of its own."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for preview in ("QuickLook/Preview.pdf", "preview.pdf"):
            if preview in names:
                data = _part(archive, preview)
                break
        else:
            if "Index/Document.iwa" in names:
                return _iwa_text(_part(archive, "Index/Document.iwa")), []
            return "", []

    class _Peek(object):
        def at(self, offset, size):
            return data[offset:offset + size]

    runs, _image_hint, _data = pdftext._collect(_Peek())
    if not runs:
        return "", []
    return pdftext._keep_readable_fonts(runs)[:MAX_CHARS], runs


# How far one Pages document may decompress. The text of a long report is
# a few hundred kilobytes; this is well past it.
MAX_IWA = 16 * 1024 * 1024


def _iwa_text(data):
    """The longest run of text in a Pages document's archive.

    Without the schema, the body is recognisable anyway: it is by far the
    longest stretch of the decompressed archive that is text -- on a real
    document, 5,097 characters against nothing else over a few dozen.
    """
    out = bytearray()
    at = 0
    while at + 4 <= len(data):
        length = int.from_bytes(data[at + 1:at + 4], "little")
        chunk = data[at + 4:at + 4 + length]
        at += 4 + length
        try:
            out += _unsnappy(chunk, MAX_IWA - len(out))
        except (IndexError, ValueError):
            break
        if len(out) >= MAX_IWA:
            break
    return _longest_string(bytes(out))


# Scanned for strings at most this far into the decompressed archive.
MAX_IWA_SCAN = 4 * 1024 * 1024


def _longest_string(data):
    """The longest protobuf string field that is text.

    Taken by the field's own length rather than as a run of printable
    bytes: the length in front of a string is often a printable byte
    itself, and read as a run the body began with it.
    """
    best = ""
    limit = min(len(data), MAX_IWA_SCAN)
    at = 0
    while at < limit:
        if data[at] & 7 != 2 or data[at] >> 3 == 0:
            at += 1
            continue
        try:
            length, start = _varint(data, at + 1)
        except (IndexError, ValueError):
            at += 1
            continue
        if length < 40 or length <= len(best) or start + length > len(data):
            at += 1
            continue
        try:
            candidate = data[start:start + length].decode("utf-8")
        except UnicodeDecodeError:
            at += 1
            continue
        printable = sum(1 for char in candidate
                        if char.isprintable() or char in "\n\t\u2029")
        if printable >= 0.95 * len(candidate):
            best = candidate
            at = start + length
            continue
        at += 1
    return re.sub(r"\s+", " ", best).strip()[:MAX_CHARS]


def _varint(data, at):
    shift = result = 0
    for _step in range(10):
        byte = data[at]
        at += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, at
        shift += 7
    raise ValueError("varint too long")


def _unsnappy(data, room):
    """Snappy's raw format, which is all a Pages archive chunk is."""
    expected, at = _varint(data, 0)
    if expected > room:
        raise ValueError("would decompress past the limit")
    out = bytearray()
    while at < len(data):
        tag = data[at]
        at += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                size = length - 59
                length = int.from_bytes(data[at:at + size], "little")
                at += size
            length += 1
            out += data[at:at + length]
            at += length
            continue
        if kind == 1:
            length = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | data[at]
            at += 1
        elif kind == 2:
            length = (tag >> 2) + 1
            offset = int.from_bytes(data[at:at + 2], "little")
            at += 2
        else:
            length = (tag >> 2) + 1
            offset = int.from_bytes(data[at:at + 4], "little")
            at += 4
        if not 0 < offset <= len(out):
            raise ValueError("copy from before the start")
        for _index in range(length):
            out.append(out[-offset])
        if len(out) > expected:
            raise ValueError("longer than it said")
    return bytes(out)


# ---------------------------------------------------------------------------
# Email, calendar invitations, saved web pages
# ---------------------------------------------------------------------------

# The font name of a run that is a title known outright -- a subject line,
# an event's summary, a page's <title> -- rather than one found by size.
KNOWN = "known title"


def known_title(runs):
    """The title a document states outright, or ""."""
    if runs and runs[0][0] == KNOWN:
        return runs[0][1]
    return ""


def _titled(title, text, size=24.0):
    """Runs for a document whose title is known outright.

    Not for `pdftext.title` to find: it filters words that are not title
    words and weighs sizes against the body, and a subject line is the
    subject line -- "Order confirmation & receipt" kept its ampersand, and
    a one-line invitation is not read as all body.
    """
    title = re.sub(r"\s+", " ", title or "").strip()
    text = re.sub(r"\s+", " ", text or "").strip()[:MAX_CHARS]
    if not title:
        return text, []
    return (title + " " + text).strip()[:MAX_CHARS], [
        (KNOWN, title, size), (GAP, " ", 0.0), ("body", text or " ", 12.0)]


def _email(path):
    """An email says what it is about in its subject line."""
    with open(path, "rb") as handle:
        data = handle.read(MAX_PART)
    if data.startswith(worddoc.MAGIC):
        return _outlook(data)
    if data[:12].split(b"\n", 1)[0].strip().isdigit():
        data = data.split(b"\n", 1)[1]           # Apple Mail's .emlx
    import email
    from email import policy
    message = email.message_from_bytes(data, policy=policy.default)
    subject = str(message.get("subject", "") or "")
    body = ""
    try:
        part = message.get_body(preferencelist=("plain", "html"))
        if part is not None:
            body = part.get_content()
            if part.get_content_type() == "text/html":
                body = _html_text(body)
    except (KeyError, LookupError, ValueError, TypeError):
        body = ""
    return _titled(subject, body)


def _outlook(data):
    """Outlook's .msg: a compound file whose properties are streams."""
    compound = worddoc._Compound(data)

    def prop(code):
        for suffix, encoding in (("001F", "utf-16-le"), ("001E", "cp1252")):
            try:
                raw = compound.stream("__substg1.0_%s%s" % (code, suffix))
            except KeyError:
                continue
            return raw.decode(encoding, "replace").rstrip("\x00")
        return ""
    return _titled(prop("0037"), prop("1000"))


def _unfolded(text):
    """iCalendar lines continue on the next line when it starts with a space."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _calendar(data):
    text = _unfolded(_decoded(data))
    event = re.search(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
    block = event.group(1) if event else text

    def field(name):
        match = re.search(r"^%s(?:;[^:\r\n]*)?:(.*)$" % name, block, re.M)
        if not match:
            return ""
        return match.group(1).replace("\\n", " ").replace("\\,", ",") \
            .replace("\\;", ";").strip()
    return _titled(field("SUMMARY"),
                   " ".join(filter(None, (field("LOCATION"),
                                          field("DESCRIPTION")))))


_HTML_DROP = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>",
                        re.S | re.I)
_HTML_TAG = re.compile(r"<[^>]+>")


def _html_text(markup):
    import html
    body = _HTML_DROP.sub(" ", markup)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    return html.unescape(_HTML_TAG.sub(" ", body))


def _html(data):
    """A saved page is called what its <title> says."""
    import html
    markup = _decoded(data)
    title = re.search(r"<title[^>]*>(.*?)</title>", markup, re.S | re.I)
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", markup, re.S | re.I)
    name = title or heading
    named = html.unescape(_HTML_TAG.sub(" ", name.group(1))) if name else ""
    return _titled(named, _html_text(markup))


# ---------------------------------------------------------------------------
# Spreadsheets
# ---------------------------------------------------------------------------

_SHEET = re.compile(r'<sheet\b[^>]*\bname="([^"]*)"')
_SHARED = re.compile(r"<si>(.*?)</si>", re.S)
_CELL_TEXT = re.compile(r"<t[^>]*>(.*?)</t>", re.S)


def _xlsx(path):
    """An Excel workbook's sheet names and the text in its cells, in order.

    Text only -- a statement's title cell, its column headings -- which is
    what the workbook keeps in one place, `sharedStrings.xml`, in the order
    it was first typed. The numbers are not what it is called.
    """
    import html
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            return "", []
        workbook = _part(archive, "xl/workbook.xml").decode("utf-8", "replace")
        shared = _part(archive, "xl/sharedStrings.xml").decode(
            "utf-8", "replace") if "xl/sharedStrings.xml" in names else ""
    strings = []
    total = 0
    for item in _SHARED.finditer(shared):
        value = html.unescape("".join(_CELL_TEXT.findall(item.group(1))))
        if value.strip():
            strings.append(value)
            total += len(value)
            if total >= MAX_CHARS:
                break
    sheets = [html.unescape(name) for name in _SHEET.findall(workbook)]
    text = re.sub(r"\s+", " ", " ".join(strings)).strip()[:MAX_CHARS]
    if not text:
        text = " ".join(sheets)
    return text, []


_BIFF_BOF = 0x0809
_BIFF_EOF = 0x000A
_BIFF_FILEPASS = 0x002F
_BIFF_SHEET = 0x0085
_BIFF_SST = 0x00FC
_BIFF_CONTINUE = 0x003C
_BIFF_LABEL = 0x0204


def _xls(data):
    """An Excel 97-2003 workbook: the same text as `_xlsx`, from records.

    The workbook is a stream of records inside a compound file -- the one
    `worddoc` already reads. Excel 97 onwards keeps every piece of cell text
    once, in a shared string table, in the order it was first typed; Excel
    5 and 95 wrote each into the cell itself. Either way it is the text,
    not the numbers, that says what the file is. An encrypted workbook is
    an empty answer, as an encrypted `.doc` is.
    """
    if len(data) > worddoc.MAX_FILE:
        return ""
    try:
        compound = worddoc._Compound(data)
        for name in ("Workbook", "Book"):
            if name in compound.entries:
                stream = compound.stream(name)
                break
        else:
            return ""
        strings, sheets = _biff_strings(stream)
    except (ValueError, KeyError, IndexError, struct.error):
        return ""
    text = re.sub(r"\s+", " ", " ".join(strings)).strip()[:MAX_CHARS]
    return text or " ".join(sheets)


def _biff_records(stream):
    at = 0
    while at + 4 <= len(stream):
        kind, length = struct.unpack_from("<HH", stream, at)
        yield kind, stream[at + 4:at + 4 + length]
        at += 4 + length


def _biff_strings(stream):
    """([cell text], [sheet names]) from a workbook stream."""
    strings, sheets = [], []
    records = list(_biff_records(stream))
    modern = True                    # BIFF8, Excel 97 onwards
    total = 0
    for index, (kind, body) in enumerate(records):
        if kind == _BIFF_BOF and index == 0 and len(body) >= 2:
            modern = struct.unpack_from("<H", body, 0)[0] >= 0x0600
        elif kind == _BIFF_FILEPASS:
            return [], []
        elif kind == _BIFF_SHEET and len(body) > 7:
            count = body[6]
            if modern:
                wide = body[7] & 1
                raw = body[8:8 + count * (2 if wide else 1)]
                sheets.append(raw.decode("utf-16-le" if wide else "latin-1",
                                         "replace"))
            else:
                sheets.append(body[7:7 + count].decode("cp1252", "replace"))
        elif kind == _BIFF_SST:
            parts = [body[8:]]
            for follow, more in records[index + 1:]:
                if follow != _BIFF_CONTINUE:
                    break
                parts.append(more)
            for value in _Segments(parts).strings(MAX_CHARS):
                if value.strip():
                    strings.append(value)
                    total += len(value)
            if total >= MAX_CHARS:
                break
        elif kind == _BIFF_LABEL and len(body) > 8:
            count = struct.unpack_from("<H", body, 6)[0]
            if modern:
                wide = body[8] & 1
                raw = body[9:9 + count * (2 if wide else 1)]
                value = raw.decode("utf-16-le" if wide else "latin-1",
                                   "replace")
            else:
                value = body[8:8 + count].decode("cp1252", "replace")
            if value.strip():
                strings.append(value)
                total += len(value)
                if total >= MAX_CHARS:
                    break
    return strings, sheets


class _Segments(object):
    """The shared string table, read across the records it is split over.

    A record holds at most 8224 bytes, so a long table goes on in CONTINUE
    records -- and a string cut in the middle of its characters starts
    again with one byte saying whether the rest is one byte a character or
    two. Everything else simply carries on.
    """

    def __init__(self, parts):
        self.parts = [part for part in parts]
        self.index = 0
        self.at = 0

    def _next(self):
        self.index += 1
        self.at = 0
        if self.index >= len(self.parts):
            raise ValueError("the string table ends early")

    def take(self, count):
        out = []
        while count > 0:
            part = self.parts[self.index]
            if self.at >= len(part):
                self._next()
                continue
            piece = part[self.at:self.at + count]
            out.append(piece)
            self.at += len(piece)
            count -= len(piece)
        return b"".join(out)

    def characters(self, count, wide):
        out = []
        while count > 0:
            part = self.parts[self.index]
            if self.at >= len(part):
                self._next()
                wide = self.parts[self.index][:1] == b"\x01"
                self.at = 1
                continue
            width = 2 if wide else 1
            fits = min(count, (len(part) - self.at) // width)
            if fits <= 0:
                raise ValueError("a character split across records")
            raw = part[self.at:self.at + fits * width]
            out.append(raw.decode("utf-16-le" if wide else "latin-1",
                                  "replace"))
            self.at += fits * width
            count -= fits
        return "".join(out)

    def strings(self, limit):
        """Each string in turn; a damaged table ends where it breaks."""
        total = 0
        while total < limit:
            part = self.parts[self.index]
            if self.at >= len(part) and self.index + 1 >= len(self.parts):
                return
            try:
                count = struct.unpack("<H", self.take(2))[0]
                flags = self.take(1)[0]
                runs = struct.unpack("<H", self.take(2))[0] \
                    if flags & 8 else 0
                extra = struct.unpack("<I", self.take(4))[0] \
                    if flags & 4 else 0
                value = self.characters(count, flags & 1)
                self.take(4 * runs + extra)
            except (ValueError, IndexError, struct.error):
                return
            total += len(value)
            yield value


def _numbers(path):
    """A Numbers document keeps its text as Pages does."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "Index/Document.iwa" not in names:
            return "", []
        return _iwa_text(_part(archive, "Index/Document.iwa")), []
