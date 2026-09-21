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

import re
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
_STREAM = re.compile(rb"stream\r\n|stream\n|stream\r")
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
    return _unzip(raw)


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

    for block in _BFCHAR.findall(body):
        items = _HEX.findall(block)
        for index in range(0, len(items) - 1, 2):
            source = _hex_str(items[index])
            target = _utf16(_hex_str(items[index + 1]))
            if source and target:
                codes[int.from_bytes(source, "big")] = target

    for block in _BFRANGE.findall(body):
        # `<low> <high> <first>` maps a run, and the bracketed form lists
        # each destination instead. Only the run form is common.
        for low, high, dest in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
                block):
            start = int(low, 16)
            stop = int(high, 16)
            if stop < start or stop - start > 65535:
                continue
            base = _hex_str(dest)
            if not base:
                continue
            first = int.from_bytes(base, "big")
            for offset in range(stop - start + 1):
                codes[start + offset] = _utf16(
                    (first + offset).to_bytes(len(base), "big"))
    return codes, width


def _font_maps(data):
    """`{resource name: (codes, width)}` for the fonts this file declares.

    A name is taken from the first resource dictionary that defines it. A
    document that reuses `/F1` for a different font on a later page would be
    read wrongly from that page on, which is a trade made deliberately: the
    alternative is resolving page trees, and only the front of the file is
    ever read here anyway.
    """
    # Offsets, not slices. `data[a:b]` copies, and four thousand objects of
    # up to sixty-four kilobytes each is a quarter of a gigabyte of copies
    # to answer a question about a handful of them. Measured on a real
    # folder: two hundred PDFs peaked at 237 MB this way and at 34 MB once
    # the copying stopped.
    objects = {}
    for count, match in enumerate(_OBJ.finditer(data)):
        if count >= MAX_OBJECTS:
            break
        number = int(match.group(1))
        if number not in objects:
            end = data.find(b"endobj", match.end())
            objects[number] = (match.end(),
                               end if end != -1 else match.end() + 65536)

    # A view costs nothing and `re` searches it happily.
    view = memoryview(data)
    cmap_of_font = {}
    for number, (start, stop) in objects.items():
        ref = _TOUNICODE_REF.search(view[start:stop])
        if ref:
            cmap_of_font[number] = int(ref.group(1))

    maps = {}
    parsed = {}
    for dictionary in _FONT_DICT.findall(data):
        for name, number in _FONT_REF.findall(dictionary):
            label = name.decode("latin-1")
            if label in maps:
                continue
            target = cmap_of_font.get(int(number))
            if target is None or target not in objects:
                continue
            if target not in parsed:
                # Only the few objects that really are character maps are
                # ever copied out of the view.
                start, stop = objects[target]
                body = _inflate(bytes(view[start:stop]))
                parsed[target] = _parse_cmap(body) if body else ({}, 1)
            if parsed[target][0]:
                maps[label] = parsed[target]
    return maps


def _through(raw, codes, width):
    """One string's bytes mapped through a font's own table."""
    out = []
    for index in range(0, len(raw) - width + 1, width):
        code = int.from_bytes(raw[index:index + width], "big")
        out.append(codes.get(code, ""))
    return "".join(out)


def _readable(text):
    """Is this words, or is it a font nobody can map without the font?

    Text drawn with a subset-encoded CID font comes back as byte pairs that
    decode to nothing in particular. It is not text and must not be offered
    as text, because a keyword search over noise eventually finds a keyword.
    """
    if len(text) < 12:
        return False
    letters = sum(1 for char in text if char.isalpha())
    return letters >= len(text) * 0.45


# Moving the pen, rather than drawing a space, is how PDF separates words.
_MOVES = (b"Td", b"TD", b"Tm", b"T*", b"'", b'"', b"TJ", b"ET")
_KERN = re.compile(rb"-?\d+(?:\.\d+)?")
# Tuned against real documents: word gaps sit well past -100 thousandths of
# an em, while the kerning inside a word rarely passes -60.
_KERN_IS_A_SPACE = -100.0


def _gap_is_a_space(gap):
    """Did the writer move the pen far enough between these two strings?"""
    if not gap:
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


def _page_text(body, maps):
    """The readable text of one content stream, font changes honoured."""
    switches = [(match.start(), match.group(1).decode("latin-1"))
                for match in _SET_FONT.finditer(body)]
    pieces = []
    current = None
    position = 0
    previous_end = None
    for where, ends, raw in _strings(body):
        while position < len(switches) and switches[position][0] <= where:
            current = switches[position][1]
            position += 1
        if previous_end is not None and _gap_is_a_space(body[previous_end:where]):
            pieces.append(" ")
        previous_end = ends
        entry = maps.get(current) if current else None
        if entry:
            codes, width = entry
            pieces.append(_through(raw, codes, width))
        else:
            # No table for this font: the bytes are most likely already
            # characters, which is true of every PDF written before font
            # subsetting became universal -- and those are the old files
            # this exists for.
            pieces.append(_decode(raw))
    return "".join(pieces)


def extract(peek):
    """`(text, image_only)` for a PDF, without unpacking the document.

    `text` is what could be read from the streams nearest the front of the
    file, which in practice is the first page: the part with the letterhead
    on it. `image_only` says the file holds pictures and no readable type,
    which is a scanned page and a different problem.
    """
    data = peek.at(0, MAX_BYTES)
    if not data:
        return "", False
    if b"/Encrypt" in data[-2048:] or b"/Encrypt" in data[:2048]:
        return "", False

    try:
        maps = _font_maps(data)
    except (re.error, ValueError, OverflowError, zlib.error):
        maps = {}

    pieces = []
    total = 0
    streams = 0
    for match in _STREAM.finditer(data):
        if streams >= MAX_STREAMS or total >= MAX_CHARS:
            break
        end = data.find(b"endstream", match.end())
        if end == -1:
            break
        body = data[match.end():end]
        streams += 1
        # Capped, and tolerant of a wrongly written length -- which is common
        # enough that giving up on the file would be an overreaction.
        body = _unzip(body)
        if body is None:
            continue
        if b"BT" not in body:            # no text block: a picture or a path
            continue
        piece = _page_text(body, maps)
        if piece:
            pieces.append(piece + " ")
            total += len(piece)

    text = "".join(pieces)
    # Unmapped codes come back as control characters; they are not words.
    text = "".join(char if char >= " " or char in "\t\n" else " "
                   for char in text)
    text = re.sub(r"\s+", " ", text).strip()
    if not _readable(text):
        return "", bool(_IMAGE_HINT.search(data))
    return text[:MAX_CHARS], False
