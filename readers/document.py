"""Who wrote it, with what, and how long it is.

The producer string is the fact worth having. A PDF whose `/Producer` is a
scanner's firmware is a piece of paper somebody scanned; one produced by Word
is something they wrote; one produced by a browser's print dialogue is a
receipt or a ticket. That single field separates three piles that otherwise
look identical from the outside, and it is free.

Page counts come second, and honestly: a PDF that stores its page tree in a
compressed object stream will not give one up without a real parser, so the
count is reported when the file volunteers it and left absent when it does
not. An absent page count costs a rule; a wrong one costs a misfiled document.
"""

from __future__ import annotations

import re
import zipfile

from evidence import CERTAIN, STRONG, LIKELY, WEAK
from . import pdftext

LETTERHEAD = 500                # characters of the top of the page to read
HEADING_WORDS = 6               # of those, how many make up the heading

_PDF_INFO = re.compile(
    rb"/(Producer|Creator|Title|Author|Subject|Keywords|CreationDate|"
    rb"ModDate)\s*\(((?:[^()\\]|\\.)*)\)", re.S)
_PDF_HEX = re.compile(rb"/(Producer|Creator|Title|Author)\s*<([0-9A-Fa-f]+)>")
_PDF_COUNT = re.compile(rb"/Type\s*/Pages[^>]{0,200}?/Count\s+(\d+)", re.S)
_PDF_COUNT_ALT = re.compile(rb"/Count\s+(\d+)[^>]{0,200}?/Type\s*/Pages", re.S)
_PDF_VERSION = re.compile(rb"^%PDF-(\d\.\d)")
_PDF_DATE = re.compile(rb"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?")


def _pdf_text(raw):
    """A PDF string literal, which may be PDFDocEncoding or UTF-16."""
    text = re.sub(rb"\\([nrtbf()\\])", lambda m: {
        b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
        b"f": b"\f"}.get(m.group(1), m.group(1)), raw)
    if text.startswith(b"\xfe\xff"):
        return text[2:].decode("utf-16-be", "replace").strip()
    return text.decode("latin-1", "replace").strip()


def _pdf(peek):
    found = {}
    version = _PDF_VERSION.match(peek.head)
    if version:
        found["pdf_version"] = version.group(1).decode("ascii")
    # The Info dictionary is near the end in a linearised file and near the
    # start otherwise, so both ends are read and neither is scanned entirely.
    window = peek.head + peek.tail(131072)
    for match in _PDF_INFO.finditer(window):
        key = match.group(1).decode("ascii").lower()
        value = _pdf_text(match.group(2))
        if value:
            found.setdefault(key, value)
    for match in _PDF_HEX.finditer(window):
        key = match.group(1).decode("ascii").lower()
        if key not in found:
            try:
                raw = bytes.fromhex(match.group(2).decode("ascii"))
                text = (raw[2:].decode("utf-16-be", "replace")
                        if raw.startswith(b"\xfe\xff")
                        else raw.decode("latin-1", "replace"))
                if text.strip():
                    found[key] = text.strip()
            except ValueError:
                pass
    count = _PDF_COUNT.search(window) or _PDF_COUNT_ALT.search(window)
    if count:
        pages = int(count.group(1))
        if 0 < pages < 100000:
            found["pages"] = pages
    elif peek.size < 8388608:
        # Small enough to count page objects directly, which is exact when
        # the file is not using object streams.
        whole = peek.at(0, peek.size)
        pages = len(re.findall(rb"/Type\s*/Page[^s]", whole))
        if pages:
            found["pages"] = pages
    if b"/Encrypt" in window:
        found["encrypted"] = True
    if b"/Linearized" in peek.head:
        found["linearised"] = True
    return found


_CORE = re.compile(rb"<(?:dc|cp|dcterms):(\w+)[^>]*>([^<]{1,300})</")
_APP = re.compile(rb"<(Pages|Words|Company|Application|AppVersion|"
                  rb"TotalTime|Paragraphs|Slides)>([^<]{1,120})</")
_OPF = re.compile(rb"<dc:(\w+)[^>]*>([^<]{1,300})</")

_CORE_NAMES = {"title": "doc_title", "creator": "author",
               "subject": "subject", "description": "description",
               "lastmodifiedby": "last_author", "created": "content_created",
               "modified": "content_modified", "keywords": "keywords",
               "language": "language", "publisher": "publisher",
               "date": "content_created"}


def _ooxml(peek):
    found = {}
    try:
        with zipfile.ZipFile(peek.path) as archive:
            names = set(archive.namelist())
            for member, pattern in (("docProps/core.xml", _CORE),
                                    ("meta.xml", _CORE),
                                    ("OEBPS/content.opf", _OPF),
                                    ("content.opf", _OPF)):
                if member not in names:
                    continue
                body = archive.read(member)[:65536]
                for match in pattern.finditer(body):
                    key = match.group(1).decode("ascii", "replace").lower()
                    value = match.group(2).decode("utf-8", "replace").strip()
                    mapped = _CORE_NAMES.get(key)
                    if mapped and value:
                        found.setdefault(mapped, value)
            if "docProps/app.xml" in names:
                body = archive.read("docProps/app.xml")[:32768]
                for match in _APP.finditer(body):
                    key = match.group(1).decode("ascii").lower()
                    value = match.group(2).decode("utf-8", "replace").strip()
                    if key in ("pages", "words", "slides", "paragraphs") \
                            and value.isdigit():
                        found[key] = int(value)
                    elif key in ("application", "company") and value:
                        found["producer" if key == "application"
                              else "company"] = value
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return {}
    return found


_RTF_INFO = re.compile(rb"\\(author|title|company|operator)\s?([^\\}]{1,120})")


def _rtf(peek):
    found = {}
    for match in _RTF_INFO.finditer(peek.head):
        key = match.group(1).decode("ascii")
        value = match.group(2).decode("latin-1", "replace").strip()
        if value:
            found.setdefault({"author": "author", "title": "doc_title",
                              "company": "company",
                              "operator": "last_author"}[key], value)
    return found


def _plain(peek):
    """Lines and words, which is all a text file has to say about itself."""
    text = peek.head.decode("utf-8", "replace")
    complete = peek.size <= len(peek.head)
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    found = {"lines": lines} if complete else {}
    if complete:
        found["words"] = len(text.split())
    return found


_BY_FORMAT = {
    "pdf": _pdf, "rtf": _rtf,
    "word": _ooxml, "excel": _ooxml, "powerpoint": _ooxml,
    "opendocument-text": _ooxml, "opendocument-sheet": _ooxml,
    "opendocument-slides": _ooxml, "epub": _ooxml,
    "plain-text": _plain, "markdown": _plain, "delimited": _plain,
}

_SCANNER_PRODUCERS = re.compile(
    r"(scan|canoscan|epson|brother|fujitsu|scansnap|kofax|"
    r"paperport|hp (officejet|scanjet|smart)|xerox|ricoh|konica|"
    r"camscanner|genius scan|adobe scan|office lens|swiftscan)", re.I)
_BROWSER_PRODUCERS = re.compile(
    r"(skia/pdf|chrome|chromium|safari|webkit|firefox|edge|"
    r"quartz pdfcontext|wkhtmltopdf|puppeteer|headless)", re.I)
_OFFICE_PRODUCERS = re.compile(
    r"(microsoft|word|excel|powerpoint|libreoffice|openoffice|pages|"
    r"keynote|numbers|google docs|indesign|illustrator|latex|pdftex|"
    r"xetex|pandoc|quarto)", re.I)


def read(peek, fmt, record):
    reader = _BY_FORMAT.get(fmt)
    if reader is None:
        return False
    try:
        found = reader(peek)
    except (OSError, ValueError, UnicodeError, re.error):
        return False
    if not found:
        return False

    source = {"pdf": "pdf-info", "rtf": "rtf-info"}.get(fmt, "doc-props")
    for key, fact in (("producer", "producer"), ("creator", "creator"),
                      ("title", "doc_title"), ("doc_title", "doc_title"),
                      ("author", "author"), ("subject", "subject"),
                      ("description", "description"),
                      ("keywords", "keywords"), ("company", "company"),
                      ("last_author", "last_author"),
                      ("language", "language"), ("publisher", "publisher")):
        if found.get(key):
            record.set(fact, str(found[key])[:300], source, STRONG)
    for key in ("pages", "words", "slides", "lines", "pdf_version"):
        if found.get(key):
            record.set(key, found[key], source, CERTAIN)
    for key in ("encrypted", "linearised"):
        if found.get(key):
            record.set(key, True, source, CERTAIN)

    for key, fact in (("creationdate", "content_created"),
                      ("moddate", "content_modified"),
                      ("content_created", "content_created"),
                      ("content_modified", "content_modified")):
        stamp = _stamp(found.get(key))
        if stamp:
            record.set(fact, stamp, source, STRONG)

    # What made this document is what it is for.
    made_by = " ".join(str(found.get(key, "")) for key in
                       ("producer", "creator", "company"))
    if made_by.strip():
        if _SCANNER_PRODUCERS.search(made_by):
            record.set("capture", "scan", "producer", STRONG)
            record.set("scan_of", "page", "producer", STRONG)
            record.note("producer %r is scanning software"
                        % made_by.strip()[:60])
        elif _BROWSER_PRODUCERS.search(made_by):
            record.set("capture", "printed-web-page", "producer", LIKELY)
        elif _OFFICE_PRODUCERS.search(made_by):
            record.set("capture", "authored", "producer", LIKELY)
    if fmt == "pdf" and not found.get("encrypted"):
        _read_the_page(peek, record)
    record.reader_ran("document:" + fmt, "%d fields" % len(found))
    return True


def _read_the_page(peek, record):
    """What the document says, for the ones whose name says nothing.

    `scan0001.pdf`, `Document1.pdf`, `20090314.pdf`: the files a decade of
    bank portals and scanner drivers produced, which somebody is obliged to
    keep and which no filename rule will ever place. The words are the only
    evidence there is, and a page that says `Rechnung` four times is an
    invoice whatever it is called.

    Nothing here decides what the document *is*. It reports what the page
    calls itself and stops, because a table of document types can only ever
    know the languages somebody typed into it, and the pile this tool is for
    is whatever twenty years in one household happened to contain.

    What replaces the table is counting. A word that heads three separate
    documents is a category those documents chose for themselves --
    `Rechnung`, `Invoice`, `Factura`, `Szamla`, a landlord's name, a
    hospital's -- and one that heads every document is a letterhead, not a
    category. That distinction is already made, by the same induction that
    learns naming conventions from filenames, and it needs no vocabulary at
    all.

    Only the top of the page is offered, and that part matters: a document
    announces what it is at the top and mentions all sorts of other things
    further down. Read whole, a CV that lists two certifications looks like
    a certificate and a covering letter that mentions a booking looks like a
    ticket -- both observed on real files. False signals began at six
    hundred characters on that corpus, so the window sits at five hundred.
    """
    try:
        text, image_only, drawn_title = pdftext.read(
            peek, exclude=_owner_words())
    except (OSError, ValueError, MemoryError, re.error):
        return
    if image_only:
        # A PDF is a page whatever is printed on it, which is what lets the
        # same holding rule cover a scanned JPEG and a scanned PDF.
        record.set("scan_of", "page", "pdf-text", STRONG)
        record.set("text_layer", False, "pdf-text", CERTAIN)
        # Not "no keywords found": there was nothing to find. A page that
        # was photographed rather than typed needs eyes or OCR, and saying
        # so is what lets it be held rather than guessed at -- and is what
        # the OCR pass, which runs in a process of its own, looks for.
        record.set("needs_ocr", True, "pdf-text", STRONG)
        record.note("no text layer: this page is an image of a page")
        return
    if not text:
        return
    record.set("text_layer", True, "pdf-text", CERTAIN)
    record.set("words_read", len(text.split()), "pdf-text", CERTAIN)
    # The title the page draws large, where there is one, and otherwise the
    # first words at the top. See `pdftext.title` for why size and not
    # position -- and why the owner's own name is never taken for it.
    if drawn_title:
        record.set("title_drawn", drawn_title[:80], "pdf-text", STRONG)
    heading = " ".join(drawn_title.split()[:HEADING_WORDS]) if drawn_title \
        else heading_of(text)
    if heading:
        record.set("heading", heading[:80], "pdf-text", STRONG)


def _owner_words():
    """The owner's name, folded, so a page's title is never taken to be the
    person it is addressed to. Asked lazily and never allowed to fail."""
    try:
        import owner
        return set(_fold(word) for word in owner.names())
    except Exception:                        # noqa: BLE001
        return set()


def _fold(word):
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", str(word).lower())
    return "".join(char for char in decomposed
                   if not unicodedata.combining(char))


_HAS_A_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def heading_of(text):
    """What the top of a page calls itself: its first few *words*.

    Words, not tokens. A payslip or a statement often opens with a band of
    dates, account numbers and reference codes, and taking the first six
    tokens of one real series gave `31.12.2019 94112559131 0001202000 ...`
    for 171 documents -- a heading with nothing in it for the induction to
    count, so a series of 171 letters from one sender could never name its
    own folder. The same window is kept, five hundred characters, because
    that is where a document says what it is; within it, the numbers are
    stepped over rather than taken.
    """
    words = [token for token in text[:LETTERHEAD].split()
             if _HAS_A_WORD.search(token)]
    return " ".join(words[:HEADING_WORDS])


def _stamp(value):
    if not value:
        return None
    text = str(value)
    match = _PDF_DATE.search(text.encode("latin-1", "replace"))
    if match:
        parts = [match.group(index) or b"00" for index in range(1, 7)]
        decoded = [part.decode("ascii") for part in parts]
        return "%s-%s-%s %s:%s:%s" % tuple(decoded)
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:19].replace("T", " ")
    return None
