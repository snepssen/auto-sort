"""The text of a Word 97-2003 `.doc`, from its piece table.

Twenty years of Downloads are full of these, and until this nothing read
them: a `Staff Handbook.doc` was filed by its name and nothing else. The
format is a compound file -- a small file system in one file -- holding a
`WordDocument` stream whose text is found through a table of pieces, each
either 8-bit (cp1252) or UTF-16. All of it is documented, and all of it
reads with the standard library.

Text only. Which text is set larger is in the character formatting, a
second structure as large as this one, so a `.doc` is headed by the top of
its first page, as a PDF with no larger type is.

Everything here is bounded: sector chains are followed at most as far as
the file is long, and a document is read to `MAX_CHARS` and no further. A
file that is damaged, encrypted, or not Word at all is an empty answer.
"""

from __future__ import annotations

import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_CHARS = 8000
MAX_FILE = 64 * 1024 * 1024
_END = 0xFFFFFFFE
_FREE = 0xFFFFFFFF


class _Compound(object):
    """Just enough of the compound file format to read a named stream."""

    def __init__(self, data):
        if not data.startswith(MAGIC) or len(data) < 512:
            raise ValueError("not a compound file")
        self.data = data
        self.sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_sector = 1 << struct.unpack_from("<H", data, 0x20)[0]
        if self.sector not in (512, 4096) or self.mini_sector != 64:
            raise ValueError("unexpected sector sizes")
        self.limit = len(data) // self.sector + 1
        first_directory = struct.unpack_from("<I", data, 0x30)[0]
        self.cutoff = struct.unpack_from("<I", data, 0x38)[0]
        first_mini_fat = struct.unpack_from("<I", data, 0x3C)[0]
        first_difat = struct.unpack_from("<I", data, 0x44)[0]
        difat_count = struct.unpack_from("<I", data, 0x48)[0]

        fat_sectors = [value for value in struct.unpack_from("<109I", data, 0x4C)
                       if value < _END]
        at = first_difat
        per = self.sector // 4
        for _hop in range(min(difat_count, self.limit)):
            if at >= _END:
                break
            values = struct.unpack_from("<%dI" % per, data, self._offset(at))
            fat_sectors.extend(value for value in values[:-1] if value < _END)
            at = values[-1]
        self.fat = []
        for number in fat_sectors[:self.limit]:
            self.fat.extend(struct.unpack_from("<%dI" % per, data,
                                               self._offset(number)))

        directory = self._chain(first_directory)
        self.entries = {}
        root = None
        for index in range(len(directory) // 128):
            entry = directory[index * 128:(index + 1) * 128]
            length = struct.unpack_from("<H", entry, 64)[0]
            kind = entry[66]
            if kind not in (1, 2, 5) or not 2 <= length <= 64:
                continue
            name = entry[:length - 2].decode("utf-16-le", "replace")
            start, size = struct.unpack_from("<II", entry, 116)
            if kind == 5:
                root = (start, size)
            elif kind == 2:
                self.entries[name] = (start, size)
        self.mini_stream = self._chain(root[0])[:root[1]] if root else b""
        self.mini_fat = []
        if first_mini_fat < _END:
            chain = self._chain(first_mini_fat)
            self.mini_fat = list(struct.unpack_from("<%dI" % (len(chain) // 4),
                                                    chain))

    def _offset(self, number):
        offset = (number + 1) * self.sector
        if offset + self.sector > len(self.data):
            raise ValueError("sector %d is past the end of the file" % number)
        return offset

    def _chain(self, first):
        pieces = []
        at = first
        for _hop in range(self.limit):
            if at >= _END or at >= len(self.fat):
                break
            offset = self._offset(at)
            pieces.append(self.data[offset:offset + self.sector])
            at = self.fat[at]
        return b"".join(pieces)

    def _mini_chain(self, first, size):
        pieces = []
        at = first
        for _hop in range(len(self.mini_fat) + 1):
            if at >= _END or at >= len(self.mini_fat):
                break
            offset = at * self.mini_sector
            pieces.append(self.mini_stream[offset:offset + self.mini_sector])
            at = self.mini_fat[at]
        return b"".join(pieces)[:size]

    def stream(self, name):
        if name not in self.entries:
            raise KeyError(name)
        start, size = self.entries[name]
        if size < self.cutoff:
            return self._mini_chain(start, size)
        return self._chain(start)[:size]


def text(data):
    """The main text of a Word 97-2003 document, or ""."""
    if len(data) > MAX_FILE:
        return ""
    try:
        return _text(_Compound(data))
    except (ValueError, KeyError, struct.error, IndexError):
        return ""


def _text(compound):
    word = compound.stream("WordDocument")
    if len(word) < 0x1AA or struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
        return ""
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if flags & 0x0100:                  # encrypted: nothing to read
        return ""
    table = compound.stream("1Table" if flags & 0x0200 else "0Table")

    # FibBase, then fibRgW, fibRgLw and fibRgFcLcb, each preceded by its
    # count -- located rather than assumed, as the specification says.
    at = 32
    words = struct.unpack_from("<H", word, at)[0]
    at += 2 + 2 * words
    longs = struct.unpack_from("<H", word, at)[0]
    rg_lw = at + 2
    main_length = struct.unpack_from("<I", word, rg_lw + 3 * 4)[0]  # ccpText
    at = rg_lw + 4 * longs
    pairs = struct.unpack_from("<H", word, at)[0]
    rg_fc = at + 2
    if pairs <= 33:
        return ""
    clx_at, clx_length = struct.unpack_from("<II", word, rg_fc + 33 * 8)
    clx = table[clx_at:clx_at + clx_length]

    # The Clx: any number of property records, then the piece table.
    at = 0
    while at < len(clx) and clx[at] == 0x01:
        at += 3 + struct.unpack_from("<H", clx, at + 1)[0]
    if at >= len(clx) or clx[at] != 0x02:
        return ""
    length = struct.unpack_from("<I", clx, at + 1)[0]
    plc = clx[at + 5:at + 5 + length]
    count = (len(plc) - 4) // 12
    if count <= 0:
        return ""
    positions = struct.unpack_from("<%dI" % (count + 1), plc, 0)
    out = []
    total = 0
    for index in range(count):
        start, end = positions[index], positions[index + 1]
        if start >= main_length:
            break
        end = min(end, main_length)
        descriptor = 4 * (count + 1) + index * 8
        fc = struct.unpack_from("<I", plc, descriptor + 2)[0]
        characters = end - start
        if fc & 0x40000000:
            offset = (fc & ~0x40000000) // 2
            piece = word[offset:offset + characters].decode("cp1252",
                                                            "replace")
        else:
            piece = word[fc:fc + 2 * characters].decode("utf-16-le",
                                                        "replace")
        out.append(piece)
        total += len(piece)
        if total >= MAX_CHARS * 2:
            break
    return _clean("".join(out))[:MAX_CHARS]


def _clean(raw):
    """Paragraph marks to spaces, field instructions left out."""
    kept = []
    depth = 0                      # inside a field's instruction
    for char in raw:
        code = ord(char)
        if code == 0x13:           # field begins: its instruction follows
            depth += 1
            continue
        if code == 0x14:           # the instruction ends; its result is text
            depth = max(0, depth - 1)
            continue
        if code == 0x15:           # field ends
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        kept.append(" " if code < 32 else char)
    return " ".join("".join(kept).split())
