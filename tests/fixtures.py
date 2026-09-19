"""Files built at test time rather than committed as blobs.

A repository full of sample JPEGs and MP3s is a repository nobody can review
and a licence question waiting to happen. Every fixture here is assembled from
its own specification — the eight bytes of a PNG signature, a real ID3v2
frame, a genuine zip central directory — so what the tests assert against is
the format, not a file somebody once downloaded.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile


def png(path, width=1920, height=1080, alpha=True):
    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2,
                         0, 0, 0)
    body = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
    body += _chunk(b"tEXt", b"Software\x00auto-sort test suite")
    body += _chunk(b"IEND", b"")
    return _write(path, body)


def _chunk(name, payload):
    import zlib
    return (struct.pack(">I", len(payload)) + name + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF))


def jpeg(path, width=4032, height=3024, make="Canon", model="Canon EOS R6",
         taken="2026:09:19 14:03:22"):
    """A JPEG whose APP1 holds a real little-endian TIFF directory."""
    entries = []
    extra = io.BytesIO()
    extra.seek(0)
    tail = bytearray()

    def add(tag, kind, value):
        if kind == 2:
            raw = value.encode("ascii") + b"\x00"
            count = len(raw)
            if count <= 4:
                payload = raw.ljust(4, b"\x00")
            else:
                offset = 8 + 2 + 12 * 4 + 4 + len(tail)
                tail.extend(raw)
                payload = struct.pack("<I", offset)
        else:
            count = 1
            payload = struct.pack("<I", value)
        entries.append(struct.pack("<HHI", tag, kind, count) + payload)

    add(0x010F, 2, make)
    add(0x0110, 2, model)
    add(0x0112, 3, 1)
    add(0x9003, 2, taken)
    directory = struct.pack("<H", len(entries)) + b"".join(entries) \
        + struct.pack("<I", 0)
    tiff = b"II*\x00" + struct.pack("<I", 8) + directory + bytes(tail)
    app1 = b"Exif\x00\x00" + tiff

    sof = struct.pack(">BHH", 8, height, width) + b"\x03\x01\x22\x00"
    body = b"\xff\xd8"
    body += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    body += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    body += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x00" * 64
    body += b"\xff\xd9"
    return _write(path, body)


def mp3(path, artist="Test Artist", title="Test Title", album="Test Album",
        track="7"):
    frames = b""
    for name, value in (("TPE1", artist), ("TIT2", title), ("TALB", album),
                        ("TRCK", track)):
        payload = b"\x03" + value.encode("utf-8")
        size = len(payload)
        syncsafe = bytes(((size >> 21) & 0x7F, (size >> 14) & 0x7F,
                          (size >> 7) & 0x7F, size & 0x7F))
        frames += name.encode("ascii") + syncsafe + b"\x00\x00" + payload
    total = len(frames)
    syncsafe = bytes(((total >> 21) & 0x7F, (total >> 14) & 0x7F,
                      (total >> 7) & 0x7F, total & 0x7F))
    header = b"ID3\x04\x00\x00" + syncsafe
    # A single valid MPEG-1 layer III frame header: 128 kbit/s, 44.1 kHz.
    audio = b"\xff\xfb\x90\x00" + b"\x00" * 4096
    return _write(path, header + frames + audio)


def wav(path, seconds=3.0, rate=44100, channels=2, bits=16):
    byte_rate = rate * channels * bits // 8
    data_size = int(byte_rate * seconds)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, byte_rate,
                      channels * bits // 8, bits)
    body = (b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", data_size) + b"\x00" * min(
                data_size, 8192))
    return _write(path, body)


def docx(path, title="Quarterly Report", author="A Person", pages=12):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr(
            "docProps/core.xml",
            '<?xml version="1.0"?><cp:coreProperties>'
            "<dc:title>%s</dc:title><dc:creator>%s</dc:creator>"
            "</cp:coreProperties>" % (title, author))
        archive.writestr(
            "docProps/app.xml",
            "<Properties><Pages>%d</Pages><Application>Microsoft Office Word"
            "</Application></Properties>" % pages)
    return _write(path, buffer.getvalue())


def pdf(path, producer="HP ScanJet Pro firmware", pages=3):
    body = ("%%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            "2 0 obj<</Type/Pages/Count %d/Kids[]>>endobj\n"
            "trailer<</Info<</Producer(%s)/Title(Scanned document)>>>>\n"
            "%%%%EOF\n" % (pages, producer)).encode("latin-1")
    return _write(path, body)


def mp4(path, width=1920, height=1080, seconds=42.0, audio_tracks=1):
    timescale = 1000

    def box(name, payload):
        return struct.pack(">I", len(payload) + 8) + name + payload

    mvhd = box(b"mvhd", b"\x00" * 12 + struct.pack(">II", timescale,
                                                   int(seconds * timescale))
               + b"\x00" * 80)
    tkhd_body = bytearray(b"\x00" * 84)
    struct.pack_into(">i", tkhd_body, 36, 0x00010000)
    struct.pack_into(">HH", tkhd_body, 76, width, 0)
    struct.pack_into(">HH", tkhd_body, 80, height, 0)
    video = box(b"trak", box(b"tkhd", bytes(tkhd_body))
                + box(b"mdia", box(b"hdlr", b"\x00" * 8 + b"vide"
                                   + b"\x00" * 12)))
    sound = b""
    for _ in range(audio_tracks):
        sound += box(b"trak", box(b"tkhd", bytes(84))
                     + box(b"mdia", box(b"hdlr", b"\x00" * 8 + b"soun"
                                        + b"\x00" * 12)))
    moov = box(b"moov", mvhd + video + sound)
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    return _write(path, ftyp + moov + box(b"mdat", b"\x00" * 1024))


def text(path, body):
    return _write(path, body.encode("utf-8") if isinstance(body, str)
                  else body)


def _write(path, body):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as stream:
        stream.write(body)
    return path
