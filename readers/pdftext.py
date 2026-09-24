"""Reading what a PDF actually says, with nothing but zlib.

A file called `scan0001.pdf` tells a sorter nothing, and neither does
`Document1.pdf` or `20090314.pdf`. That is not an edge case: it is what a
decade of bank portals, scanner drivers and Windows "Save As" dialogues
produced, and those files are exactly the ones somebody is required to keep
for twenty-five years. The filename is a dead end and the metadata usually
is too. The words on the page are the only thing left.

Getting at them is less work than it sounds. A PDF's page content is a
stream of drawing operators, the streams are almost always compressed with
zlib, and the text-showing operators take their argument as a plain string.
There is no need to lay out a page, resolve a font or build an object graph
to find out that the first thing on it is the word `Rechnung`.

What this deliberately does not do is pretend to be a PDF library. It does
not resolve cross-reference tables, follow object streams, decrypt, or apply
any filter but Flate. Each of those would be real work in aid of a question
nobody asks a file sorter, and every one of them can fail on a malformed
file. When this cannot read a document it says so and the document keeps the
facts it already had.

The other half of the job is knowing when there is nothing to read. A page
that was scanned as an image carries no text at all, and saying "no
paperwork keywords found" about it would be a lie -- the right answer is
"this needs eyes, or OCR". Those two outcomes are reported separately.
"""

from __future__ import annotations

import collections
import re
import unicodedata
import zlib

MAX_BYTES = 4 * 1024 * 1024     # how far into the file to look at all
MAX_STREAMS = 60                # streams to inflate before giving up
MAX_CHARS = 8000                # text to keep; a letterhead is in the first few
# How much any one stream may inflate to. `zlib.decompress` has no output
# limit, and a PDF is compressed: a real 0.91 MB file in a real folder
# grew the process by 171 MB on its own, a hundred and ninety fold. Only
# the first few thousand characters are ever used, so a stream that wants
# more than this has nothing to offer that is worth the memory.
MAX_INFLATE = 4 * 1024 * 1024

# A page drawn as a photograph rather than set as type.
_IMAGE_HINT = re.compile(rb"/Subtype\s*/Image|/DCTDecode|/JPXDecode|/CCITTFaxDecode")
# Not the `stream` inside `endstream`: that is the end of one, and what
# follows it is the next object. It went unnoticed while every stream had
# to inflate, because those bytes never do; read as a plain stream, fonts
# and colour profiles became letters.
_STREAM = re.compile(rb"(?<!end)stream(?:\r\n|\n|\r)")
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
            b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
_OCTAL = re.compile(rb"\\([0-7]{1,3})")


# Objects, so that a font's ToUnicode map can be found from the name the
# content stream calls it by. There is no xref walk here: object headers are
# located by scanning, which is what a repair parser does and is unbothered
# by the broken tables that twenty-year-old files are full of.
# `(\d+)\s+\d+\s+obj` looks harmless and is quadratic on binary data.
# Inside a long run of digits every single offset is a fresh starting
# point, and each one consumes the rest of the run, fails to find
# whitespace, and gives a digit back one at a time. Measured: 8,000
# digits took 0.4s and each doubling quadrupled it, so the four
# megabytes this reads would have taken about a day -- which is what
# it was doing, on the daemon's only thread, with the tray frozen
# behind it.
#
# The lookbehind is the fix: a match can only begin where a digit run
# begins, so the engine tries each run once instead of once per digit.
# The bounded repeats keep any single attempt short. PDF object
# numbers and generations are small; nothing real is lost.
_OBJ = re.compile(rb"(?<![0-9])(\d{1,9})[ \t\r\n]{1,8}"
                  rb"\d{1,5}[ \t\r\n]{1,8}obj\b")
_TOUNICODE_REF = re.compile(rb"/ToUnicode\s+(\d+)\s+\d+\s+R")
_FONT_DICT = re.compile(rb"/Font\s*<<(.{0,4000}?)>>", re.S)
_FONT_REF = re.compile(rb"/([A-Za-z0-9_.+-]+)\s+(\d+)\s+\d+\s+R")
_CODESPACE = re.compile(rb"begincodespacerange(.*?)endcodespacerange", re.S)
_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")
_SET_FONT = re.compile(rb"/([A-Za-z0-9_.+-]+)\s+[-\d.]+\s+Tf")
# The same, keeping the size: `/F5 11 Tf` draws what follows at eleven points.
_SIZED_FONT = re.compile(rb"/([A-Za-z0-9_.+-]+)\s+(-?[\d.]+)\s+Tf")
# Some writers set every font at size 1 and scale the text matrix instead --
# `11 0 0 11 72 700 Tm` -- so the size a person sees is the product.
_TEXT_MATRIX = re.compile(rb"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+"
                          rb"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+Tm")


def _number(raw):
    try:
        return abs(float(raw))
    except (TypeError, ValueError):
        return 0.0

MAX_OBJECTS = 4000


def _hex_str(raw):
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", raw)
    if len(digits) % 2:
        digits += b"0"
    try:
        return bytes.fromhex(digits.decode("ascii"))
    except ValueError:
        return b""


def _utf16(raw):
    try:
        return raw.decode("utf-16-be", "ignore")
    except (UnicodeError, LookupError):
        return ""


def _unescape(raw):
    """PDF string escapes, which are C's with a line-continuation rule."""
    out = bytearray()
    index = 0
    length = len(raw)
    while index < length:
        byte = raw[index:index + 1]
        if byte != b"\\":
            out += byte
            index += 1
            continue
        index += 1
        if index >= length:
            break
        nxt = raw[index:index + 1]
        if nxt in _ESCAPES:
            out += _ESCAPES[nxt]
            index += 1
        elif nxt in (b"\n", b"\r"):         # a backslash at end of line
            index += 1                      # means no character at all
            if nxt == b"\r" and raw[index:index + 1] == b"\n":
                index += 1
        elif nxt.isdigit():
            match = _OCTAL.match(raw, index - 1)
            if match:
                out.append(int(match.group(1), 8) & 0xFF)
                index = match.end()
            else:
                index += 1
        else:
            out += nxt                      # \q is just q
            index += 1
    return bytes(out)


def _decode(raw):
    """One PDF string to text, guessing only between its two encodings."""
    if raw[:2] == b"\xfe\xff":
        try:
            return raw[2:].decode("utf-16-be", "replace")
        except (UnicodeError, LookupError):
            return ""
    return raw.decode("latin-1", "replace")


def _strings(data):
    """`[(start, end, raw bytes)]` for every string in the stream, in order.

    Bytes rather than text, because what they mean depends on the font that
    was current when they were drawn, and that is the caller's problem.
    Parentheses nest in PDF and are not always escaped, so this counts depth
    rather than reaching for a regular expression that cannot.
    """
    found = []
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        if byte == 0x28:                              # (
            where = index
            depth = 1
            index += 1
            buffer = bytearray()
            while index < length and depth:
                char = data[index]
                if char == 0x5C:                      # backslash escapes the
                    buffer += data[index:index + 2]   # next byte whatever it is
                    index += 2
                    continue
                if char == 0x28:
                    depth += 1
                elif char == 0x29:
                    depth -= 1
                    if not depth:
                        index += 1
                        break
                buffer.append(char)
                index += 1
            found.append((where, index, _unescape(bytes(buffer))))
        elif byte == 0x3C and data[index + 1:index + 2] != b"<":   # <  not  <<
            close = data.find(b">", index)
            if close == -1:
                break
            found.append((index, close + 1,
                          _hex_str(data[index + 1:close])))
            index = close + 1
        else:
            index += 1
    return found


def _inflate(body):
    """The decompressed contents of an object's stream, if it has one."""
    match = _STREAM.search(body)
    if not match:
        return None
    end = body.find(b"endstream", match.end())
    raw = body[match.end():end if end != -1 else len(body)]
    return _contents(body[:match.start()], raw)


def _contents(dictionary, raw):
    """A stream's bytes as its dictionary says they are stored.

    Compression is optional. A stream with no `/Filter` is its own
    contents, and writers old and new leave them that way: Qt, behind
    every wkhtmltopdf document, stores its character maps plain. Only
    inflating meant two real payslips' maps were never found, their text
    came out as glyph numbers, and they were sent to be OCR'd -- as though
    a document that was typed were a photograph of one.
    """
    if b"/Filter" not in dictionary:
        return raw[:MAX_INFLATE]
    return _unzip(raw)


def _dictionary_before(data, stream_start):
    """The dictionary of the stream starting at `stream_start`."""
    head = data[max(0, stream_start - 600):stream_start]
    # The dictionary belongs to this stream only from its object header on;
    # anything before that is the tail of the previous object.
    header = head.rfind(b" obj")
    return head[header:] if header != -1 else head[-300:]


def _unzip(raw):
    """Inflate, but never past `MAX_INFLATE`.

    `decompressobj().decompress(data, limit)` stops at the limit and leaves
    the rest in `unconsumed_tail`, which is exactly the behaviour wanted:
    take what is useful, refuse to be told to allocate a gigabyte.
    """
    try:
        return zlib.decompressobj().decompress(raw, MAX_INFLATE)
    except zlib.error:
        return None


_RANGE_ENTRY = re.compile(
    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*"
    rb"(?:\[([^\]]*)\]|<([0-9A-Fa-f]+)>)")


def _parse_cmap(body):
    """A ToUnicode CMap as `(codes, width)`.

    `width` is how many bytes one character code takes, which the codespace
    range states and which is two for every subset font a modern writer
    produces. Getting it wrong turns a document into alternating characters
    and nulls, so it is read rather than assumed.
    """
    codes = {}
    width = 1
    space = _CODESPACE.search(body)
    if space:
        bounds = _HEX.findall(space.group(1))
        if bounds:
            width = max(1, min(4, len(_hex_str(bounds[0])) or 1))
    # The codes the table lists are what the font draws with, whatever the
    # codespace claims. A real certificate's fonts declared `<0000> <FFFF>`
    # and listed `<77> <0077>`: read two bytes at a time, not one character
    # of it matched and the whole certificate went blank.
    listed = collections.Counter(
        len(_hex_str(source))
        for block in _BFCHAR.findall(body) + _BFRANGE.findall(body)
        for source in _HEX.findall(block)[:1])
    if listed:
        commonest = listed.most_common(1)[0][0]
        if 1 <= commonest <= 4:
            width = commonest

    for block in _BFCHAR.findall(body):
        items = _HEX.findall(block)
        for index in range(0, len(items) - 1, 2):
            source = _hex_str(items[index])
            target = _utf16(_hex_str(items[index + 1]))
            if source and target:
                codes[int.from_bytes(source, "big")] = target

    for block in _BFRANGE.findall(body):
        # `<low> <high> <first>` maps a run; `<low> <high> [<a> <b> ...]`
        # lists each destination instead. Qt writes only the second, and
        # read as though it were the first, the triples inside its list
        # became mappings of their own -- a document of wrong letters.
        for match in _RANGE_ENTRY.finditer(block):
            start = int(match.group(1), 16)
            stop = int(match.group(2), 16)
            if stop < start or stop - start > 65535:
                continue
            if match.group(3):
                targets = _HEX.findall(match.group(3))
                for offset, dest in enumerate(targets[:stop - start + 1]):
                    target = _utf16(_hex_str(dest))
                    if target:
                        codes[start + offset] = target
                continue
            base = _hex_str(match.group(4))
            if not base:
                continue
            first = int.from_bytes(base, "big")
            for offset in range(stop - start + 1):
                codes[start + offset] = _utf16(
                    (first + offset).to_bytes(len(base), "big"))
    return codes, width


def _objects(data):
    """`{object number: (start, stop)}` for the objects in `data`.

    Offsets, not slices. `data[a:b]` copies, and four thousand objects of up
    to sixty-four kilobytes each is a quarter of a gigabyte of copies to
    answer a question about a handful of them. Measured on a real folder:
    two hundred PDFs peaked at 237 MB this way and at 34 MB once the copying
    stopped.
    """
    # The *last* definition of each object, because that is the one that
    # counts. A PDF edited after it was made has the changes appended, and
    # an object defined again later replaces the earlier one -- four real
    # contracts had been reordered that way, and reading the first
    # definitions followed the page tree as it was before the edit.
    objects = {}
    for count, match in enumerate(_OBJ.finditer(data)):
        if count >= MAX_OBJECTS:
            break
        number = int(match.group(1))
        end = data.find(b"endobj", match.end())
        objects[number] = (match.end(),
                           end if end != -1 else match.end() + 65536)
    return objects


_REF = rb"(\d+)\s+\d+\s+R"
_PAGES_REF = re.compile(rb"/Pages\s+" + _REF)
_KIDS = re.compile(rb"/Kids\s*\[([^\]]*)\]")
_ANY_REF = re.compile(_REF)
_CONTENTS = re.compile(rb"/Contents\s*(\[[^\]]*\]|" + _REF + rb")")
_RESOURCES_REF = re.compile(rb"/Resources\s+" + _REF)
_XOBJECTS = re.compile(rb"/XObject\s*<<(.*?)>>", re.S)
_FONT_DICT_REF = re.compile(rb"/Font\s+" + _REF)


def _first_page(data, objects):
    """`(streams, fonts)`: what page one draws, and with which fonts.

    `streams` is the object numbers of the streams drawn on page one, in
    order. `fonts` is `{resource name: font object}` as page one and the
    forms it draws declare them -- see `_font_maps` for why that matters.

    A PDF's page order is its page tree, not where the pages sit in the
    file, and the two can disagree. Four real six-page employment contracts
    stored the form attached at the back *before* the contract's first page,
    and read in file order they were titled after that form's section
    heading -- `Luik A`, "Part A" -- instead of the contract's own title.

    Returns `([], {})` whenever the tree cannot be followed: compressed
    object streams, a damaged catalog, anything unexpected. Then the file is
    read in file order, which is what always happened before.
    """
    def body(number, limit=8192):
        span = objects.get(number)
        if not span:
            return b""
        start, stop = span
        return data[start:min(stop, start + limit)]

    # The catalog the file's *last* trailer names. An edited file can carry
    # the old catalog and a new one side by side, and only the newest root
    # describes the document as it now is.
    catalog = None
    root = None
    for match in re.finditer(rb"/Root\s+" + _REF, data):
        root = int(match.group(1))
    if root is not None:
        catalog = body(root, 2048) or None
    if catalog is None:
        for number in objects:
            text = body(number, 2048)
            if re.search(rb"/Type\s*/Catalog\b", text):
                catalog = text
                break
    if catalog is None:
        return [], {}
    pages = _PAGES_REF.search(catalog)
    if not pages:
        return [], {}
    node = int(pages.group(1))
    page = None
    for _depth in range(12):
        text = body(node)
        if re.search(rb"/Type\s*/Page\b(?!s)", text):
            page = text
            break
        kids = _KIDS.search(text)
        first = _ANY_REF.search(kids.group(1)) if kids else None
        if not first:
            return [], {}
        node = int(first.group(1))
    if page is None:
        return [], {}

    ordered = []
    contents = _CONTENTS.search(page)
    if contents:
        ordered.extend(int(ref) for ref in
                       _ANY_REF.findall(contents.group(1) if contents.group(1)
                                        .startswith(b"[") else
                                        contents.group(0)))
    # Forms the page draws with `Do` are part of the page too: that is how
    # the contracts above drew everything but a signature. One level of
    # them, and the forms those draw, is as deep as real files go.
    def resources_of(text):
        indirect = _RESOURCES_REF.search(text)
        return body(int(indirect.group(1))) if indirect else text

    fonts = {}

    def note_fonts(resources):
        declared = [match.group(1) for match in _FONT_DICT.finditer(resources)]
        declared += [body(int(match.group(1)), 4096)
                     for match in _FONT_DICT_REF.finditer(resources)]
        for dictionary in declared:
            for name, number in _FONT_REF.findall(dictionary):
                fonts.setdefault(name.decode("latin-1"), int(number))

    resources = resources_of(page)
    note_fonts(resources)
    for level in range(2):
        found = []
        for match in _XOBJECTS.finditer(resources):
            found.extend(int(ref) for ref in _ANY_REF.findall(match.group(1)))
        found = [number for number in found if number not in ordered]
        if not found:
            break
        ordered.extend(found)
        resources = b"".join(resources_of(body(number, 4096))
                             for number in found)
        note_fonts(resources)
    return ordered, fonts


def _font_maps(data, objects=None, own=None):
    """`{resource name: (codes, width)}` for the fonts this file declares.

    A name is taken from the first resource dictionary that defines it,
    which is right far more often than not and wrong for a file that reuses
    `/F7` for a different font on a later page.

    That was measured, not imagined: 3 of 172 employment contracts declared
    `/F7` first as a Calibri with two-byte codes and then, on page one, as
    Times-Bold -- and the contract's title, drawn in Times-Bold, went
    through Calibri's table and came out as nothing. So when page one's own
    fonts are known (`own`, from `_first_page`) a second answer is returned
    for page one, with those names meaning what page one means by them.

    Returns `(maps, page_one_maps)`; the second is the first when `own` is
    empty.
    """
    # Offsets, not slices. `data[a:b]` copies, and four thousand objects of
    # up to sixty-four kilobytes each is a quarter of a gigabyte of copies
    # to answer a question about a handful of them. Measured on a real
    # folder: two hundred PDFs peaked at 237 MB this way and at 34 MB once
    # the copying stopped.
    if objects is None:
        objects = _objects(data)

    # A view costs nothing and `re` searches it happily.
    view = memoryview(data)
    cmap_of_font = {}
    for number, (start, stop) in objects.items():
        ref = _TOUNICODE_REF.search(view[start:stop])
        if ref:
            cmap_of_font[number] = int(ref.group(1))

    parsed = {}

    def table(font):
        target = cmap_of_font.get(font)
        if target is None or target not in objects:
            return None
        if target not in parsed:
            # Only the few objects that really are character maps are
            # ever copied out of the view.
            start, stop = objects[target]
            body = _inflate(bytes(view[start:stop]))
            parsed[target] = _parse_cmap(body) if body else ({}, 1)
        return parsed[target] if parsed[target][0] else None

    maps = {}
    for dictionary in _FONT_DICT.findall(data):
        for name, number in _FONT_REF.findall(dictionary):
            label = name.decode("latin-1")
            if label in maps:
                continue
            found = table(int(number))
            if found:
                maps[label] = found
    if not own:
        return maps, maps
    page_one = dict(maps)
    for label, number in own.items():
        found = table(number)
        if found:
            page_one[label] = found
        else:
            # A font with no table of its own: its bytes are characters.
            page_one.pop(label, None)
    return maps, page_one


def _through(raw, codes, width):
    """One string's bytes mapped through a font's own table.

    A one-byte font's table is often only the exceptions -- a ligature, a
    curly quote -- and every other code is the character it looks like,
    which is how these strings read before any table was consulted. A
    two-byte code has no such fallback: it is a glyph number and means
    nothing on its own.
    """
    out = []
    for index in range(0, len(raw) - width + 1, width):
        code = int.from_bytes(raw[index:index + width], "big")
        found = codes.get(code)
        if found is None and width == 1:
            found = chr(code)
        out.append(found or "")
    return "".join(out)


# What the smallest real document says. A one-line invoice -- a heading, a
# sender, an amount and a payment term -- is about a dozen words, and it is
# a perfectly ordinary thing for somebody to have. Below this is page
# furniture: "Seite 1 von 3", a stamp, the label a scanner writes in the
# corner. Calling that "the text of this document" would stop a page that
# really does need OCR from ever getting it.
#
# Checked against a real folder: of 334 files without a page-sized picture
# in them, 20 have no words at all, 13 have fewer than eight, and 296 have
# more than twenty. The threshold has a wide gap to sit in.
MIN_WORDS = 8

# Three letters or more, in any script. Two-letter tokens are mostly the
# wreckage of hyphenation and abbreviations, and counting them makes noise
# look more like prose than it is.
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _readable(text):
    """Is this words, or is it a font nobody can map without the font?

    Text drawn with a subset-encoded CID font comes back as byte pairs that
    decode to nothing in particular. It is not text and must not be offered
    as text, because a keyword search over noise eventually finds a keyword.

    This used to ask whether letters made up 45% of the *characters*, and
    that was wrong in a way that cost a great deal. An invoice is full of
    amounts, dates, customer numbers and reference codes:

        chars: 62994   letters: 22425   ratio: 0.36   ->  rejected
        words with letters: 3035

    Three thousand words of German, thrown away for being 36% letters. On
    one real folder the test rejected 177 documents that had between them
    twenty distinct heading words -- five of which headed 171 of the 177,
    because they were all from the same sender and all said the same thing
    at the top. Every one of those was then held as an unreadable scan.

    So: count *words*, not characters. Two-byte CID codes come back as
    control characters, which are turned into spaces before this is asked,
    so they produce almost no words and are still refused.

    Counting words is not enough on its own, though, and the folder that
    proved it had 172 files in it -- see `_glyph_codes` below.

    What none of this can tell apart is a one-byte subset font whose
    encoding is a substitution of the *same alphabet* -- real words with
    the letters swapped. That is a cipher of prose and has the shape of
    prose, so no test without a dictionary will catch it. The cost if it
    happens is a category named something nobody can read, which is visible
    in the log, listed by `check-rules`, and one line to delete.
    """
    if len(_WORD.findall(text)) < MIN_WORDS:
        return False
    return not _glyph_codes(text)


# Characters that never turn up inside a word in any script: symbols,
# currency signs, maths operators, formatting marks. A page of Greek is
# entirely above ASCII and has none of these; a page of glyph numbers read
# as Latin-1 is full of them.
# Not `Lo`: that category holds every Chinese, Japanese, Hebrew and Thai
# letter, and a page of any of them is not soup.
_NOT_IN_WORDS = ("So", "Sk", "Sc", "Sm", "Cf", "Co", "Cn", "No")

# Where the two populations sit, measured on 346 real files: text is under
# 20% above ASCII and under 11% odd characters; glyph codes are over 40%
# and around 18%. The thresholds sit in the gap rather than on either edge.
_MOSTLY_HIGH = 0.4
_SOME_ODD = 0.05


def _not_in_a_word(char):
    """A symbol, a mark, or a control character from the top half of the
    byte range -- none of which any script puts inside a word. Tabs and
    newlines are control characters too, and are left out of it."""
    category = unicodedata.category(char)
    return category in _NOT_IN_WORDS or (category == "Cc" and ord(char) >= 0x80)


def _glyph_codes(text):
    """Is this a subset font's glyph numbers rather than anybody's words?

    A font that ships only the glyphs it uses numbers them from scratch and
    describes them in a `ToUnicode` table. Without that table the numbers
    are all there is, and reading them as characters produces a stream that
    passes every test for "words" while meaning nothing:

        ììª® êí0@Âè ï®ÞÍà

    It would name a folder that. One real folder had 171 documents whose
    headings agreed on it exactly, which is worse than useless: agreement
    is what this program treats as evidence.

    Two measurements, and it takes both. **Mostly above ASCII**, because
    glyph numbers scatter across the whole byte range while German and
    Dutch and Hungarian stay near the bottom of it. And **containing
    characters that words never contain** -- a currency sign or an arrow in
    the middle of what claims to be a word. A page of Greek or Cyrillic is
    entirely above ASCII and entirely letters, so it passes; that is why it
    takes both and not either.
    """
    sample = text[:4000]
    if not sample:
        return False
    above = sum(1 for char in sample if ord(char) > 127) / float(len(sample))
    if above <= _MOSTLY_HIGH:
        return False
    odd = sum(1 for char in sample if _not_in_a_word(char))
    return odd / float(len(sample)) > _SOME_ODD


# Moving the pen, rather than drawing a space, is how PDF separates words.
_MOVES = (b"Td", b"TD", b"Tm", b"T*", b"'", b'"', b"TJ", b"ET")
_KERN = re.compile(rb"-?\d+(?:\.\d+)?")
# Tuned against real documents: word gaps sit well past -100 thousandths of
# an em, while the kerning inside a word rarely passes -60.
_KERN_IS_A_SPACE = -100.0


# A pen move within a line: `tx ty Td`.
_STEP = re.compile(rb"(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+T[dD]")
# Most strings one character long: the writer places glyphs, not words.
_GLYPH_AT_A_TIME = 0.6
_GLYPH_STRINGS = 20
# In that mode, a step along the line longer than this many ems is a jump
# to somewhere else -- the next column, a tab stop -- rather than the width
# of the glyph just drawn.
_GLYPH_STEP = 1.5


def _gap_is_a_space(gap, glyph_size=0.0):
    """Did the writer move the pen far enough between these two strings?

    `glyph_size` is the font size when the stream is drawn a glyph at a
    time, as Qt draws everything (and so every wkhtmltopdf document): each
    character its own string, each moved to with `Td`, and the spaces drawn
    as glyphs of their own. There, a step the width of a letter is not a
    word gap -- read as one, a payslip came out as `S D   S t a f f i n g`
    and was sent off to be OCR'd as having no words at all.
    """
    if not gap:
        return False
    if glyph_size and not any(move in gap for move in
                              (b"Tm", b"T*", b"'", b'"', b"TJ", b"ET")):
        steps = _STEP.findall(gap)
        if steps and all(abs(_number(ty) or 0.0) < 0.01
                         and 0.0 <= (_number(tx) or 0.0)
                         <= glyph_size * _GLYPH_STEP
                         for tx, ty in steps):
            return False
    if any(move in gap for move in _MOVES):
        return True
    for number in _KERN.findall(gap):
        try:
            if float(number) <= _KERN_IS_A_SPACE:
                return True
        except ValueError:
            continue
    return False


# Marks a word gap in a list of runs, so that dropping a font's text later
# does not glue its neighbours together. Not a font, and never judged.
_GAP = object()

# Marks where page one's streams end, when the page tree could be followed.
_PAGE_END = object()


def _page_runs(body, maps, current=None):
    """One content stream as `([(font, text)], font in use at the end)`.

    The font in use is passed in and handed back because a page's content
    is often split across several streams and the font is set only in the
    first. One real PDF writer did exactly that for every page of a series
    of 172 documents, and reading each stream fresh made nine words in ten
    look as though they had been drawn with no font at all.
    """
    # What was in use when the previous stream ended, font and size both.
    if isinstance(current, tuple):
        current, carried = current
    else:
        carried = 0.0
    switches = [(match.start(), match.group(1).decode("latin-1"),
                 _number(match.group(2)))
                for match in _SIZED_FONT.finditer(body)]
    scalings = [(match.start(), abs(_number(match.group(4)) or 1.0))
                for match in _TEXT_MATRIX.finditer(body)]
    drawn_strings = []
    position = 0
    scaled = 0
    size = carried
    scale = 1.0
    for where, ends, raw in _strings(body):
        while position < len(switches) and switches[position][0] <= where:
            current = switches[position][1]
            size = switches[position][2] or size
            position += 1
        while scaled < len(scalings) and scalings[scaled][0] <= where:
            scale = scalings[scaled][1] or 1.0
            scaled += 1
        entry = maps.get(current) if current else None
        if entry:
            codes, width = entry
            drawn = _through(raw, codes, width)
        else:
            # No table for this font: the bytes are most likely already
            # characters, which is true of every PDF written before font
            # subsetting became universal -- and those are the old files
            # this exists for.
            drawn = _decode(raw)
        drawn_strings.append((where, ends, current, size, scale, drawn))

    # Judged over the whole stream: one writer draws it all the same way.
    singles = sum(1 for entry in drawn_strings if len(entry[5]) == 1)
    glyph_at_a_time = (len(drawn_strings) >= _GLYPH_STRINGS
                       and singles >= len(drawn_strings) * _GLYPH_AT_A_TIME)

    runs = []
    previous_end = None
    for where, ends, font, drawn_size, drawn_scale, drawn in drawn_strings:
        if previous_end is not None and _gap_is_a_space(
                body[previous_end:where],
                (drawn_size or 1.0) if glyph_at_a_time else 0.0):
            runs.append((_GAP, " ", 0.0))
        previous_end = ends
        if drawn:
            runs.append((font, drawn,
                         round((drawn_size or 0.0) * drawn_scale, 1)))
    return runs, (current, size)


def _page_text(body, maps, current=None):
    """The readable text of one content stream, font changes honoured."""
    runs, _current = _page_runs(body, maps, current)
    return _keep_readable_fonts(runs)


def _keep_readable_fonts(runs):
    """Join the runs, leaving out every font whose text is glyph numbers.

    Judged a *font* at a time, across everything it drew, because that is
    the unit the problem comes in. A subset font shipped without its
    character map produces glyph numbers wherever it is used, and a font
    with one produces words wherever it is used; what varies is only how
    much of it there is in one place.

    The first version judged each drawn string on its own, and on a real
    series of 172 documents the strings were three or four characters long
    -- too short for any statistic to mean anything -- so the soup went
    straight through into what the record said each page was called:
    `êí0@Â ÍäÎá`. Pooled by font, the same text is thousands of characters
    and the verdict is not close.
    """
    drawn = collections.defaultdict(list)
    for font, text, _size in runs:
        if font is not _GAP and font is not _PAGE_END and font is not None:
            drawn[font].append(text)
    unreadable = set(font for font, texts in drawn.items()
                     if _glyph_codes("".join(texts)))
    kept = []
    for font, text, _size in runs:
        if font is _PAGE_END:
            continue
        if font is _GAP:
            kept.append(text)
        elif font is None:
            # Drawn before any font was set anywhere we could see. There is
            # no font to pool it by, so it is judged a string at a time --
            # weaker, and the best that can be done without one.
            if not _glyph_codes(text):
                kept.append(text)
        elif font not in unreadable:
            kept.append(text)
    return "".join(kept)


# What a stream's own dictionary says when it is something other than a
# page's drawing instructions. None of these is text on a page, and several
# of them contain the two bytes `BT` by chance.
#
# Matched as whole names on the keys that say what the stream *is*. The first
# version matched substrings and refused every page of a real payslip,
# because `/Image` is also the start of `/ImageC` in the list of things the
# page is allowed to draw -- which says nothing about what the stream is.
_NOT_PAGE_CONTENT = re.compile(
    rb"/(?:Type|Subtype)\s*/(?:EmbeddedFile|Image|Metadata|ObjStm|XRef)"
    rb"(?![A-Za-z0-9])"
    rb"|/(?:Length[123]|FontFile[23]?)(?![A-Za-z0-9])"
    rb"|/N\s+[134](?![0-9])\s*/Alternate")

# Drawing instructions are text: operators, numbers, names and strings. A
# stream that decompresses to mostly something else is not one, whatever its
# dictionary claims.
_MOSTLY_PRINTABLE = 0.9


def _is_page_content(data, stream_start, body):
    """Is this stream something a page draws with, rather than a thing
    stored inside the file?

    A real series of 171 payslips each carried a whole PDF attached inside
    it -- `/Type/EmbeddedFile`, the original from before the file was
    modified -- and its insides were read as though they were the page:
    `endobj`, `FontDescriptor`, and forty thousand characters of compressed
    binary taken as letters, against seven thousand of real text. Three
    bytes of that binary, `RBU`, turned up in all 171 because they all
    carried the same attachment, and were offered as the name the series
    had chosen for itself.
    """
    dictionary = _dictionary_before(data, stream_start)
    if _NOT_PAGE_CONTENT.search(dictionary):
        return False
    if not body:
        return False
    sample = body[:4096]
    printable = sum(1 for byte in sample
                    if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable >= len(sample) * _MOSTLY_PRINTABLE


# How much bigger than the body a line must be drawn to count as a title.
# One point is the smallest step any writer uses for emphasis; less is the
# rounding of a scaled matrix.
_EMPHASIS = 1.0
# A title is a few words. More than this at one size is a paragraph set
# large, or a table header row, not the name of the document.
_TITLE_WORDS = 12

# How far into a document to look for its title: about one dense page of
# drawn text. A document names itself on its first page, and later pages
# have section headings -- four six-page employment contracts were titled
# `Luik A` ("Part A") from a form attached at the back, printed larger than
# the contract's own title on page one.
_TITLE_REACH = 4000


def title(runs, exclude=()):
    """What a document calls itself in type bigger than its body text.

    "A document says what it is at the top" was learnt from CVs and letters
    somebody wrote themselves, where the first line is the title. Official
    paperwork does not work like that. A Belgian employment contract puts
    a block of registration numbers, insurers and funds first and its title
    four hundred characters down; German payslips start with the payroll
    program's version stamp. Position cannot find those titles without
    also finding the certifications a CV mentions halfway down.

    Size can, in any language. The title is drawn bigger than the body --
    and on real paperwork the only things drawn bigger still are *who it is
    for*: the recipient's name and address, or a stamp saying who signed.
    So the title is the largest emphasised text that is not about the
    owner, whose name `exclude` carries.
    """
    exclude = set(word.lower() for word in (exclude or ()))
    runs = _join_capitals(runs)
    tiers = collections.OrderedDict()
    weight = collections.Counter()
    last = None
    drawn = 0
    for font, text, size in runs:
        # Page one, where the page tree says where it ends; otherwise about
        # a page's worth of text.
        if font is _PAGE_END or drawn >= _TITLE_REACH:
            break
        if font is _GAP:
            if last is not None and tiers.get(last):
                tiers[last].append(" ")
            continue
        if not size:
            continue
        tiers.setdefault(size, []).append(text)
        weight[size] += len(text)
        drawn += len(text)
        last = size
    if not weight:
        return ""
    body = weight.most_common(1)[0][0]
    for size in sorted(tiers, reverse=True):
        if size < body + _EMPHASIS:
            break
        words = "".join(tiers[size]).split()
        if not words or not _WORD.search(" ".join(words)):
            continue
        folded = set(_fold_word(word) for token in words
                     for word in _WORD.findall(token))
        if folded & exclude:
            continue                    # the owner's name: for, not what
        words = _once([word for word in words if _a_title_word(word)])
        if not words or len(words) > _TITLE_WORDS:
            continue
        # A greeting set large -- `Dear Hiring Manager,`, `Sehr geehrte
        # Damen und Herren,`, `Geachte heer,` -- ends with a comma in nearly
        # every language that uses one, and a title never does.
        if words[-1].endswith(","):
            continue
        return " ".join(words)
    return ""


def _join_capitals(runs):
    """Put a decorative capital back on the word it starts.

    A drop capital is drawn on its own at a larger size, so taken tier by
    tier a cover letter's greeting reads `ear Hiring Manager` and a
    certificate's title loses its first word. A fragment of one or two
    characters drawn with no gap after it belongs to the text that follows,
    whatever size either was drawn at.
    """
    joined = []
    carry = ""
    for index, (font, text, size) in enumerate(runs):
        if font is _PAGE_END:
            joined.append((font, text, size))
            continue
        if font is _GAP:
            # Not joined across a gap, although a drop capital is usually
            # followed by one -- the pen jumps right to make room for it.
            # `Luik A` followed by body text in lower case has exactly the
            # same shape, and without where each letter sits on the page
            # the two cannot be told apart. The one real case, a cover
            # letter's greeting, is not a title anyway.
            if carry:
                joined.append((None, carry, size))
                carry = ""
            joined.append((font, text, size))
            continue
        following = runs[index + 1] if index + 1 < len(runs) else None
        if (len(text.strip()) <= 2 and text.strip().isalpha()
                and following is not None and following[0] is not _GAP):
            carry += text.strip()
            continue
        joined.append((font, carry + text, size))
        carry = ""
    if carry:
        joined.append((None, carry, 0.0))
    return joined


def _a_title_word(token):
    """A word, a number, or a short connecting word -- not a stray glyph.

    One Belgian tax form's title came out `Luik Í1 Í@ A`: the heading of a
    section, and three glyphs from a font with no character map. Anything
    of a letter or two that is not plain Latin is dropped.
    """
    stripped = token.strip(".,:;()[]\"'-–—")
    if not stripped:
        return False
    if stripped.isdigit():
        return True
    if _WORD.search(stripped):
        return True
    return len(stripped) <= 3 and all("a" <= char.lower() <= "z"
                                      for char in stripped)


def _once(words):
    """A title drawn on every page reads `Loonbrief Loonbrief Loonbrief`."""
    seen = set()
    kept = []
    for word in words:
        key = word.lower()
        if key in seen:
            break
        seen.add(key)
        kept.append(word)
    return kept


def _fold_word(word):
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(char for char in decomposed
                   if not unicodedata.combining(char))


def read(peek, exclude=()):
    """`(text, image_only, title)`. See `extract` and `title`."""
    runs, image_hint, data = _collect(peek)
    if runs is None:
        return "", False, ""
    return _finish(runs, image_hint, data) + (title(runs, exclude),)


def extract(peek):
    """`(text, image_only)` for a PDF, without unpacking the document.

    `text` is what could be read from the streams nearest the front of the
    file, which in practice is the first page: the part with the letterhead
    on it. `image_only` says the file holds pictures and no readable type,
    which is a scanned page and a different problem.
    """
    runs, image_hint, data = _collect(peek)
    if runs is None:
        return "", False
    return _finish(runs, image_hint, data)


def _in_page_order(data, objects, first):
    """Every stream in the file, with page one's first.

    The rest follow in file order, which is as good a guess as any and is
    what everything was read in before. `first` is page one's streams, from
    `_first_page`. Returns `(streams, how many of them are page one)`.
    """
    matches = list(_STREAM.finditer(data))
    if not first:
        return matches, 0
    owner = {}
    for number, (start, stop) in objects.items():
        owner[start] = number
    rank = dict((number, index) for index, number in enumerate(first))
    starts = sorted(owner)

    def number_of(match):
        # The object a stream belongs to is the last one to start before it.
        import bisect
        index = bisect.bisect_right(starts, match.start()) - 1
        return owner[starts[index]] if index >= 0 else None

    def key(pair):
        position, match = pair
        number = number_of(match)
        return (0, rank[number]) if number in rank else (1, position)

    ordered = sorted(enumerate(matches), key=key)
    on_page_one = sum(1 for pair in ordered if key(pair)[0] == 0)
    return [match for _position, match in ordered], on_page_one


def _collect(peek):
    """Every run of text the page content draws, with its font and size.

    Returns `(runs, image_hint, data)`, or `(None, ...)` for a file that is
    empty or encrypted.
    """
    data = peek.at(0, MAX_BYTES)
    if not data:
        return None, False, data
    if b"/Encrypt" in data[-2048:] or b"/Encrypt" in data[:2048]:
        return None, False, data

    try:
        objects = _objects(data)
    except (re.error, ValueError, OverflowError):
        objects = {}
    try:
        first, own = _first_page(data, objects)
    except (re.error, ValueError, OverflowError):
        first, own = [], {}
    try:
        maps, page_one_maps = _font_maps(data, objects, own)
    except (re.error, ValueError, OverflowError, zlib.error):
        maps, page_one_maps = {}, {}

    runs = []
    total = 0
    streams = 0
    # Carried from stream to stream, as it is on the page. See `_page_runs`.
    current = None
    ordered, on_page_one = _in_page_order(data, objects, first)
    for index, match in enumerate(ordered):
        if on_page_one and index == on_page_one:
            runs.append((_PAGE_END, "", 0.0))
        fonts = page_one_maps if index < on_page_one else maps
        if streams >= MAX_STREAMS or total >= MAX_CHARS * 4:
            break
        end = data.find(b"endstream", match.end())
        if end == -1:
            break
        body = data[match.end():end]
        streams += 1
        # Capped, and tolerant of a wrongly written length -- which is common
        # enough that giving up on the file would be an overreaction.
        body = _contents(_dictionary_before(data, match.start()), body)
        if body is None:
            continue
        if b"BT" not in body:            # no text block: a picture or a path
            continue
        if not _is_page_content(data, match.start(), body):
            continue
        found, current = _page_runs(body, fonts, current)
        runs.extend(found)
        runs.append((_GAP, " ", 0.0))
        total += sum(len(text) for _font, text, _size in found)
    return runs, bool(_IMAGE_HINT.search(data)), data


def _finish(runs, image_hint, data):
    """The readable text of the collected runs, or a verdict of none."""
    # Judged once, over everything each font drew in the whole document --
    # the more of a font there is to look at, the less the verdict is a
    # guess. The collection runs further than the text kept, for the same
    # reason.
    text = _keep_readable_fonts(runs)
    # Unmapped codes come back as control characters; they are not words.
    text = "".join(char if char >= " " or char in "\t\n" else " "
                   for char in text)
    text = re.sub(r"\s+", " ", text).strip()
    if not _readable(text):
        return "", image_hint
    return text[:MAX_CHARS], False


# ---------------------------------------------------------------------------
# The page as a picture, for the files that really are one
# ---------------------------------------------------------------------------

# How far back from a `stream` keyword to look for the dictionary that
# describes it. Image dictionaries are short -- a colour space, a filter, a
# width, a height -- and a kilobyte covers every one seen in a real folder.
_DICT_LOOKBACK = 1500

# Below this, an image is a logo rather than a page. Measured: of 195 PDFs
# on one machine with no readable text layer, 185 hold nothing bigger than a
# letterhead graphic and only 10 hold an actual photographed page. OCR on a
# 218x62 logo costs a process launch and returns the sender's name, which
# the rest of the document already said.
MIN_PAGE_PIXELS = 700 * 700

# A page scan at 300 dpi is a few megabytes; far past that and something is
# wrong enough to leave alone.
MAX_IMAGE_BYTES = 24 * 1024 * 1024

_WIDTH = re.compile(rb"/Width\s+(\d{1,6})")
_HEIGHT = re.compile(rb"/Height\s+(\d{1,6})")


def page_image(data):
    """The largest embedded JPEG that is big enough to be a page.

    Returns `(bytes, width, height)` or None. Only DCTDecode, and
    deliberately: a JPEG inside a PDF is a JPEG, copied out byte for byte
    with no decoding, no colour management and no third-party library. The
    other filters would each need a decoder written here to produce an image
    anything else could read, which is a great deal of code for the one file
    in twenty that uses them.

    Largest rather than first. Nearly every scanned page arrives with the
    sender's logo in front of it, and the first image in the file is that
    logo every time.
    """
    best = None
    for match in _STREAM.finditer(data):
        head = data[max(0, match.start() - _DICT_LOOKBACK):match.start()]
        if b"/DCTDecode" not in head:
            continue
        end = data.find(b"endstream", match.end())
        if end == -1:
            continue
        if end - match.end() > MAX_IMAGE_BYTES:
            continue
        body = data[match.end():end].rstrip(b"\r\n")
        # The bytes have to be a JPEG in their own right; a stream that only
        # mentions DCTDecode nearby is not one.
        if not body.startswith(b"\xff\xd8\xff"):
            continue
        # The last ones before the stream, not the first: what is in front
        # of a page's stream is usually the logo's dictionary, and reading
        # the logo's size as the page's is how a scan gets skipped for
        # being 218 by 62.
        widths = _WIDTH.findall(head)
        heights = _HEIGHT.findall(head)
        if not (widths and heights):
            continue
        width, height = int(widths[-1]), int(heights[-1])
        pixels = width * height
        if pixels < MIN_PAGE_PIXELS:
            continue
        if best is None or pixels > best[1] * best[2]:
            best = (body, width, height)
    return best
