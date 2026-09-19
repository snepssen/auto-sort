"""What the bytes say, when the name cannot be trusted.

A `.jpg` that is a PNG, a `.mp4` with no video in it, an `invoice.pdf.exe` —
these are ordinary contents of the folders this tool exists for, and the
extension is a claim rather than evidence. This module reads the first few
kilobytes and answers the same question the extension answered, at a
confidence the extension can never reach.

The table is ours rather than libmagic's, because libmagic is a dependency and
this has to run on a machine with nothing installed. That is a smaller loss
than it sounds: libmagic's value is its very long tail of scientific and
historical formats, and the tail that matters in somebody's Downloads folder
is a few hundred entries long.

**Refiners are where the work actually is.** A magic number usually identifies
a *container*, not a kind. `RIFF` is a WAV, an AVI or a WebP. `ftyp` is an MP4,
an iPhone photo, a Canon raw or an audiobook. `PK\x03\x04` is a zip, and also
every Office document, every EPUB, every Android package and every USDZ model
ever made. Stopping at the magic number would file half a disk under
`archive`, so each shared container has a function that opens it far enough to
tell which it is.
"""

from __future__ import annotations

import json
import os
import re
import zipfile

HEAD_BYTES = 8192
TAIL_BYTES = 1024


class Peek(object):
    """A file held open just long enough to identify it.

    The head is read once and reused by every signature and refiner. Anything
    deeper — the ISO descriptor at 32769, a zip's central directory — is a
    seek, so the common case costs one read of 8 KB rather than a scan.
    """

    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)
        self._fh = open(path, "rb")
        self.head = self._fh.read(HEAD_BYTES)
        self._tail = None

    def at(self, offset, length):
        if offset + length <= len(self.head):
            return self.head[offset:offset + length]
        if offset >= self.size:
            return b""
        try:
            self._fh.seek(offset)
            return self._fh.read(length)
        except (OSError, ValueError):
            return b""

    def tail(self, length=TAIL_BYTES):
        if self._tail is None:
            start = max(0, self.size - length)
            self._tail = self.at(start, min(length, self.size))
        return self._tail

    def close(self):
        try:
            self._fh.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# Refiners: one shared container, several kinds
# ---------------------------------------------------------------------------

_FTYP_BRANDS = {
    "qt  ": ("video", "quicktime"),
    "M4A ": ("audio", "mp4-audio"), "M4B ": ("audio", "mp4-audio"),
    "M4P ": ("audio", "mp4-audio"), "m4a ": ("audio", "mp4-audio"),
    "M4V ": ("video", "mp4"), "M4VH": ("video", "mp4"), "M4VP": ("video", "mp4"),
    "heic": ("image", "heif"), "heix": ("image", "heif"),
    "heim": ("image", "heif"), "heis": ("image", "heif"),
    "hevc": ("image", "heif"), "hevx": ("image", "heif"),
    "mif1": ("image", "heif"), "msf1": ("image", "heif"),
    "avif": ("image", "avif"), "avis": ("image", "avif"),
    "crx ": ("image", "raw"),            # Canon CR3
    "jxl ": ("image", "jxl"),
    "f4v ": ("video", "flash-video"),
    "mj2s": ("image", "jpeg2000"), "mjp2": ("image", "jpeg2000"),
}


def _refine_ftyp(peek):
    """ISO base media: the brand says which of six kinds this actually is."""
    brand = peek.at(8, 4).decode("latin-1")
    hit = _FTYP_BRANDS.get(brand)
    if hit:
        return hit + (brand.strip(),)
    if brand[:3] in ("3gp", "3g2"):
        return ("video", "3gpp", brand.strip())
    # Unknown brand: look at the compatible-brand list before assuming video,
    # because Apple writes `mif1` second on some HEIC exports.
    compatible = peek.at(16, 32).decode("latin-1")
    for known, hit in _FTYP_BRANDS.items():
        if known in compatible:
            return hit + (known.strip(),)
    return ("video", "mp4", brand.strip())


def _refine_ebml(peek):
    """Matroska and WebM share EBML; the DocType element separates them."""
    marker = peek.head.find(b"\x42\x82")
    if marker != -1:
        size = peek.head[marker + 2]
        doctype = peek.head[marker + 3:marker + 3 + (size & 0x7F)]
        if doctype.startswith(b"webm"):
            return ("video", "webm", "webm")
        if doctype.startswith(b"matroska"):
            return ("video", "matroska", "matroska")
    return ("video", "matroska", "")


_RIFF_FORMS = {
    b"WAVE": ("audio", "wav"), b"AVI ": ("video", "avi"),
    b"WEBP": ("image", "webp"), b"RMID": ("audio", "midi"),
    b"ACON": ("image", "icon"), b"CDDA": ("audio", "wav"),
    b"AVIX": ("video", "avi"),
}


def _refine_riff(peek):
    form = peek.at(8, 4)
    hit = _RIFF_FORMS.get(form)
    if hit:
        return hit + (form.decode("latin-1").strip(),)
    return ("data", "riff", form.decode("latin-1", "replace").strip())


_OGG_CODECS = (
    (b"OpusHead", ("audio", "opus")),
    (b"\x01vorbis", ("audio", "ogg")),
    (b"\x80theora", ("video", "ogg-video")),
    (b"\x7fFLAC", ("audio", "ogg")),
    (b"Speex   ", ("audio", "speex")),
    (b"\x01video\x00", ("video", "ogg-video")),
)


def _refine_ogg(peek):
    for marker, hit in _OGG_CODECS:
        if marker in peek.head:
            return hit + (marker.strip(b"\x01\x7f\x80").decode("latin-1"),)
    return ("audio", "ogg", "")


_ZIP_CONTENTS = (
    ("AndroidManifest.xml", ("app", "android")),
    ("Payload/", ("app", "ios")),
    ("META-INF/MANIFEST.MF", ("code", "java")),
    ("3D/3dmodel.model", ("model3d", "3mf")),
    ("word/", ("document", "word")),
    ("xl/", ("document", "excel")),
    ("ppt/", ("document", "powerpoint")),
    ("visio/", ("document", "visio")),
    ("Index/Slide", ("document", "keynote")),
    ("Index/Tables", ("document", "numbers")),
    ("Index/Document.iwa", ("document", "pages")),
)

_ZIP_MIMETYPES = {
    b"application/epub+zip": ("document", "epub"),
    b"application/vnd.oasis.opendocument.text": ("document",
                                                 "opendocument-text"),
    b"application/vnd.oasis.opendocument.spreadsheet":
        ("document", "opendocument-sheet"),
    b"application/vnd.oasis.opendocument.presentation":
        ("document", "opendocument-slides"),
    b"application/vnd.oasis.opendocument.graphics": ("image", "opendocument"),
}


def _refine_zip(peek):
    """Half the document formats in the world are a zip with a convention.

    The central directory is read rather than the first local header, because
    the first entry in an OOXML file is not reliably `[Content_Types].xml` and
    guessing from it mislabels documents produced by anything but Word.
    """
    # The uncompressed `mimetype` entry, when present, is definitive and sits
    # at a fixed place by specification.
    if peek.at(30, 8) == b"mimetype":
        declared = peek.at(38, 64).split(b"P")[0].strip()
        for mimetype, hit in _ZIP_MIMETYPES.items():
            if declared.startswith(mimetype):
                return hit + (mimetype.decode("ascii"),)

    try:
        with zipfile.ZipFile(peek.path) as archive:
            names = archive.namelist()[:400]
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        return ("archive", "zip", "unreadable central directory")

    for prefix, hit in _ZIP_CONTENTS:
        for name in names:
            if name.startswith(prefix):
                return hit + (prefix,)
    for name in names:
        if name.endswith((".usda", ".usdc", ".usd")):
            return ("model3d", "usd", "usdz")
    if any(name.endswith((".jpg", ".png", ".webp", ".gif")) for name in names) \
            and not any("/" in name.strip("/") for name in names):
        return ("document", "comic-archive", "flat images")
    return ("archive", "zip", "%d entries" % len(names))


_OLE2_HINTS = (
    (b"W\x00o\x00r\x00d\x00D\x00o\x00c", ("document", "word")),
    (b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k", ("document", "excel")),
    (b"B\x00o\x00o\x00k", ("document", "excel")),
    (b"P\x00o\x00w\x00e\x00r\x00P\x00o\x00i\x00n\x00t", ("document",
                                                         "powerpoint")),
    (b"V\x00i\x00s\x00i\x00o", ("document", "visio")),
    (b"I\x00n\x00s\x00t\x00a\x00l\x00l", ("app", "windows-installer")),
)


def _refine_ole2(peek):
    """The pre-2007 Office container. The stream names give it away."""
    window = peek.head + peek.tail(4096)
    for marker, hit in _OLE2_HINTS:
        if marker in window:
            return hit + ("compound file stream",)
    return ("document", "compound-file", "")


def _refine_mpegts(peek):
    """0x47 once is a coincidence; three times 188 bytes apart is not."""
    if peek.at(188, 1) == b"\x47" and peek.at(376, 1) == b"\x47":
        return ("video", "mpeg-ts", "188-byte packets")
    return None


def _refine_java_or_macho(peek):
    """CAFEBABE is a Java class file and also a Mach-O universal binary.

    The next four bytes are a count of architectures in one and a version in
    the other, and no Java class has ever declared major version 0.
    """
    following = int.from_bytes(peek.at(4, 4), "big")
    if following < 40:
        return ("app", "macos-app", "universal binary")
    return ("code", "java", "class file")


def _refine_form(peek):
    """FORM is the IFF wrapper; the type at byte 8 says which IFF this is."""
    form = peek.at(8, 4)
    if form in (b"AIFF", b"AIFC"):
        return ("audio", "aiff", form.decode("ascii"))
    if form == b"8SVX":
        return ("audio", "8svx", "iff")
    if form == b"ILBM":
        return ("image", "ilbm", "iff")
    if form == b"DJVU" or form == b"DJVM":
        return ("document", "djvu", "iff")
    return None


def _refine_tar(peek):
    return ("archive", "tar", "ustar")


def _refine_stl(peek):
    """Binary STL has no magic — only a size that agrees with its own count."""
    count = int.from_bytes(peek.at(80, 4), "little")
    if 84 + count * 50 == peek.size:
        return ("model3d", "stl", "%d triangles" % count)
    return None


# ---------------------------------------------------------------------------
# The table. Order is priority: the first match wins, so anything that is a
# special case of another signature is listed above it.
# ---------------------------------------------------------------------------

_S = (
    # offset, magic, kind, format, refiner
    (0, b"\x89PNG\r\n\x1a\n", "image", "png", None),
    (0, b"\xff\xd8\xff", "image", "jpeg", None),
    (0, b"GIF87a", "image", "gif", None),
    (0, b"GIF89a", "image", "gif", None),
    (0, b"BM", "image", "bmp", None),
    (0, b"\x00\x00\x01\x00", "image", "icon", None),
    (0, b"\x00\x00\x02\x00", "image", "icon", None),
    (0, b"icns", "image", "icon", None),
    (0, b"8BPS", "image", "psd", None),
    (0, b"gimp xcf", "image", "xcf", None),
    (0, b"\x76\x2f\x31\x01", "image", "openexr", None),
    (0, b"#?RADIANCE", "image", "radiance", None),
    (0, b"#?RGBE", "image", "radiance", None),
    (0, b"DDS ", "image", "dds", None),
    (0, b"\x0a\x02\x01", "image", "pcx", None),
    (0, b"\xff\x0a", "image", "jxl", None),
    (0, b"\x00\x00\x00\x0cJXL ", "image", "jxl", None),
    (0, b"\x00\x00\x00\x0cjP  ", "image", "jpeg2000", None),
    (0, b"\xff\x4f\xff\x51", "image", "jpeg2000", None),
    (0, b"FUJIFILMCCD-RAW", "image", "raw", None),
    (0, b"FOVb", "image", "raw", None),
    (6, b"HEAPCCDR", "image", "raw", None),
    (0, b"P1", "image", "netpbm", None), (0, b"P2", "image", "netpbm", None),
    (0, b"P3", "image", "netpbm", None), (0, b"P4", "image", "netpbm", None),
    (0, b"P5", "image", "netpbm", None), (0, b"P6", "image", "netpbm", None),
    (0, b"II*\x00", "image", "tiff", None),
    (0, b"MM\x00*", "image", "tiff", None),
    (0, b"II+\x00", "image", "tiff", None),

    (4, b"ftyp", None, None, _refine_ftyp),
    (0, b"\x1a\x45\xdf\xa3", None, None, _refine_ebml),
    (0, b"RIFF", None, None, _refine_riff),
    (0, b"RF64", "audio", "wav", None),
    (0, b"OggS", None, None, _refine_ogg),
    (0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "video", "windows-media", None),
    (0, b"FLV\x01", "video", "flash-video", None),
    (0, b"\x00\x00\x01\xba", "video", "program-stream", None),
    (0, b"\x00\x00\x01\xb3", "video", "mpeg", None),
    (0, b"\x47", None, None, _refine_mpegts),
    (0, b"\x06\x0e\x2b\x34\x02\x05\x01\x01", "video", "mxf", None),
    (0, b".RMF", "video", "realvideo", None),
    (0, b"RED1", "video", "redcode", None),
    (0, b"RED2", "video", "redcode", None),
    (0, b"YUV4MPEG2", "video", "yuv4mpeg", None),

    (0, b"fLaC", "audio", "flac", None),
    (0, b"ID3", "audio", "mp3", None),
    (0, b"\xff\xfb", "audio", "mp3", None), (0, b"\xff\xf3", "audio", "mp3", None),
    (0, b"\xff\xf2", "audio", "mp3", None), (0, b"\xff\xfa", "audio", "mp3", None),
    (0, b"\xff\xf1", "audio", "aac", None), (0, b"\xff\xf9", "audio", "aac", None),
    (0, b"FORM", None, None, _refine_form),
    (0, b"caff", "audio", "core-audio", None),
    (0, b".snd", "audio", "sun-audio", None),
    (0, b"\x0b\x77", "audio", "ac3", None),
    (0, b"\x7f\xfe\x80\x01", "audio", "dts", None),
    (0, b"#!AMR", "audio", "amr", None),
    (0, b"MThd", "audio", "midi", None),
    (0, b"MAC ", "audio", "monkeys-audio", None),
    (0, b"wvpk", "audio", "wavpack", None),
    (0, b"TTA1", "audio", "tta", None),
    (0, b"DSD ", "audio", "dsd", None),
    (0, b"FRM8", "audio", "dsd", None),
    (0, b"Extended Module:", "audio", "tracker", None),
    (0, b"IMPM", "audio", "tracker", None),
    (44, b"SCRM", "audio", "tracker", None),
    (0, b"#EXTM3U", "audio", "playlist", None),

    (0, b"%PDF-", "document", "pdf", None),
    (0, b"%!PS", "document", "postscript", None),
    (0, b"{\\rtf", "document", "rtf", None),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", None, None, _refine_ole2),
    (0, b"AT&TFORM", "document", "djvu", None),
    (0, b"ITSF", "document", "chm", None),
    (60, b"BOOKMOBI", "document", "mobi", None),
    (0, b"\xffWPC", "document", "wordperfect", None),
    (0, b"PK\x03\x04", None, None, _refine_zip),
    (0, b"PK\x05\x06", "archive", "zip", None),

    (0, b"Rar!\x1a\x07", "archive", "rar", None),
    (0, b"7z\xbc\xaf\x27\x1c", "archive", "7z", None),
    (0, b"\x1f\x8b", "archive", "gzip", None),
    (0, b"BZh", "archive", "bzip2", None),
    (0, b"\xfd7zXZ\x00", "archive", "xz", None),
    (0, b"\x28\xb5\x2f\xfd", "archive", "zstd", None),
    (0, b"\x04\x22\x4d\x18", "archive", "lzma", None),
    (0, b"\x5d\x00\x00", "archive", "lzma", None),
    (0, b"MSCF", "archive", "cab", None),
    (0, b"\x60\xea", "archive", "arj", None),
    (2, b"-lh", "archive", "lha", None),
    (7, b"**ACE**", "archive", "ace", None),
    (0, b"StuffIt", "archive", "stuffit", None),
    (0, b"SIT!", "archive", "stuffit", None),
    (0, b"070707", "archive", "cpio", None),
    (0, b"\x1f\x9d", "archive", "compress", None),
    (0, b"MSWIM", "archive", "wim", None),
    (257, b"ustar", None, None, _refine_tar),
    (0, b"\xed\xab\xee\xdb", "app", "rpm", None),
    (0, b"!<arch>\ndebian", "app", "debian", None),

    (32769, b"CD001", "disk-image", "iso", None),
    (0, b"conectix", "disk-image", "vhd", None),
    (0, b"vhdxfile", "disk-image", "vhdx", None),
    (0, b"KDMV", "disk-image", "vmdk", None),
    (0, b"# Disk Descriptor", "disk-image", "vmdk", None),
    (0, b"QFI\xfb", "disk-image", "qcow", None),
    (0, b"<<< Oracle VM VirtualBox Disk Image >>>", "disk-image",
     "virtualbox", None),

    (0, b"MZ", "app", "windows-executable", None),
    (0, b"\x7fELF", "app", "linux-executable", None),
    (0, b"\xca\xfe\xba\xbe", None, None, _refine_java_or_macho),
    (0, b"\xcf\xfa\xed\xfe", "app", "macos-app", None),
    (0, b"\xce\xfa\xed\xfe", "app", "macos-app", None),
    (0, b"\xfe\xed\xfa\xcf", "app", "macos-app", None),
    (0, b"\xfe\xed\xfa\xce", "app", "macos-app", None),

    (0, b"glTF", "model3d", "gltf", None),
    (0, b"Kaydara FBX Binary", "model3d", "fbx", None),
    (0, b"BLENDER", "model3d", "blender", None),
    (0, b"ply\n", "model3d", "ply", None),
    (0, b"ply\r\n", "model3d", "ply", None),
    (0, b"PXR-USDC", "model3d", "usd", None),
    (0, b"\x4d\x4d\x00\x00", "model3d", "3ds", None),
    (0, b"VOX ", "model3d", "voxel", None),
    (0, b"SketchUp Model", "model3d", "sketchup", None),
    (0, b"AC1", "model3d", "autocad", None),

    (0, b"\x00\x01\x00\x00\x00", "font", "truetype", None),
    (0, b"true", "font", "truetype", None),
    (0, b"ttcf", "font", "truetype", None),
    (0, b"OTTO", "font", "opentype", None),
    (0, b"wOFF", "font", "woff", None),
    (0, b"wOF2", "font", "woff", None),
    (0, b"\x80\x01", "font", "type1", None),
    (0, b"STARTFONT", "font", "bitmap-font", None),

    (0, b"SQLite format 3\x00", "data", "sqlite", None),
    (0, b"bplist00", "data", "property-list", None),
    (0, b"d8:announce", "data", "torrent", None),
    (0, b"\x00\x00\x00\x01Bud1", "data", "binary", None),
    (0, b"L\x00\x00\x00\x01\x14\x02\x00", "data", "shortcut", None),
    (0, b"-----BEGIN", "data", "key-material", None),
    (0, b"\x93NUMPY", "data", "numpy", None),
    (0, b"PAR1", "data", "columnar", None),
    (0, b"\x89HDF\r\n\x1a\n", "data", "hdf", None),
    (0, b"Obj\x01", "data", "columnar", None),
    (0, b"regf", "data", "registry", None),
    (0, b"\x80\x02\x7d", "data", "pickle", None),
    (0, b"SIMPLE  =", "data", "binary", None),
)

SIGNATURES = _S

# Formats whose magic is two or three bytes long, or is a value that turns up
# by chance in other files. They are still the best answer available, but they
# are not the same evidence as an eight-byte PNG header, and the difference
# has to reach the record or a coincidence outranks a correct extension.
SHORT_MAGIC = {
    "bmp", "mp3", "aac", "ac3", "gzip", "compress", "lzma", "arj", "lha",
    "pcx", "netpbm", "type1", "truetype", "3ds", "icon", "pickle", "dts",
    "windows-executable", "linux-executable", "raw",
}


def confidence_for(fmt):
    from evidence import CERTAIN, STRONG
    return STRONG if fmt in SHORT_MAGIC else CERTAIN


def sniff(peek):
    """(kind, format, detail) from the bytes, or None if nothing matched.

    `detail` is a short phrase for the log — the ftyp brand, the EBML doctype,
    the number of entries in a zip. It exists so that `explain` can say *why*
    rather than only *what*.
    """
    from evidence import CERTAIN
    for offset, magic, kind, fmt, refiner in SIGNATURES:
        if peek.at(offset, len(magic)) != magic:
            continue
        if refiner is None:
            return (kind, fmt, "", confidence_for(fmt))
        refined = refiner(peek)
        if refined is not None:
            return refined + (confidence_for(refined[1]),)
        # A refiner that declines means the magic was a coincidence — 0x47 in
        # a file that is not a transport stream — so keep looking.
    # Binary STL is the one format with no magic worth special-casing, because
    # 3D printing produces an enormous number of them.
    if peek.size > 84 and peek.path.lower().endswith(".stl"):
        hit = _refine_stl(peek)
        if hit:
            return hit + (CERTAIN,)
    return None


# ---------------------------------------------------------------------------
# Text, which has no magic number and an enormous number of kinds
# ---------------------------------------------------------------------------

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

_TEXT_SHAPES = (
    # A compiled pattern against the decoded head, and what it means. Order
    # matters: a TTML file is also XML, and an OBJ is also plain text.
    (re.compile(r"^WEBVTT"), ("subtitle", "webvtt")),
    (re.compile(r"^\s*\[Script Info\]", re.I), ("subtitle",
                                                "advanced-substation")),
    (re.compile(r"^\s*\d+\s*\r?\n\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->"),
     ("subtitle", "subrip")),
    (re.compile(r"^\{\d+\}\{\d+\}"), ("subtitle", "microdvd")),
    (re.compile(r"^#\s*VobSub index file", re.I), ("subtitle", "vobsub")),
    (re.compile(r"^\s*(\[[a-z]{2}:[^\]]*\]\s*)*\[\d{2}:\d{2}[.:]\d{2}\]"),
     ("subtitle", "lyrics")),
    (re.compile(r"^Scenarist_SCC"), ("subtitle", "eia-608")),
    (re.compile(r"^#!\s*\S*\b(bash|sh|zsh|ksh|dash)\b"), ("code", "shell")),
    (re.compile(r"^#!\s*\S*\bpython"), ("code", "python")),
    (re.compile(r"^#!\s*\S*\b(node|deno|bun)\b"), ("code", "javascript")),
    (re.compile(r"^#!\s*\S*\bperl\b"), ("code", "perl")),
    (re.compile(r"^#!\s*\S*\bruby\b"), ("code", "ruby")),
    (re.compile(r"^#!"), ("code", "shell")),
    (re.compile(r"^\s*<\?xml[^>]*\?>\s*<(tt|tt:tt)\b", re.I | re.S),
     ("subtitle", "timed-text")),
    (re.compile(r"^\s*<(\?xml[^>]*\?>\s*)?<?svg\b", re.I), ("image", "svg")),
    (re.compile(r"<svg\b", re.I), ("image", "svg")),
    (re.compile(r"<COLLADA\b", re.I), ("model3d", "collada")),
    (re.compile(r"<X3D\b", re.I), ("model3d", "x3d")),
    (re.compile(r"^#VRML", re.I), ("model3d", "x3d")),
    (re.compile(r"<!DOCTYPE\s+html", re.I), ("code", "html")),
    (re.compile(r"<html[\s>]", re.I), ("code", "html")),
    (re.compile(r"^\s*<\?xml", re.I), ("code", "xml")),
    (re.compile(r"^\s*ISO-10303"), ("model3d", "step")),
    (re.compile(r"^\s*solid\s.*\n\s*facet normal", re.I), ("model3d", "stl")),
    (re.compile(r"^(#[^\n]*\n)*\s*(v|vn|vt)\s+-?\d", re.M),
     ("model3d", "wavefront")),
    (re.compile(r"^\s*newmtl\s", re.M), ("model3d", "wavefront")),
    (re.compile(r"^;?\s*(FLAVOR|generated by|G(0|1|21|28|90)\b)", re.M),
     ("model3d", "gcode")),
    (re.compile(r"^\s*\[InternetShortcut\]", re.I), ("data", "shortcut")),
    (re.compile(r"^\s*\{\\rtf"), ("document", "rtf")),
    (re.compile(r"^\s*\\documentclass|^\s*\\begin\{document\}", re.M),
     ("document", "latex")),
)


def _decode_head(head):
    """The head as text, and the encoding, or (None, None) if it is binary.

    A NUL byte in the first kilobyte is the reliable tell for binary — no text
    encoding in circulation produces one — with a printable-ratio check behind
    it for the legacy single-byte encodings that have no marker at all.
    """
    for bom, encoding in _BOMS:
        if head.startswith(bom):
            try:
                return head[len(bom):].decode(encoding, "ignore"), encoding
            except (UnicodeDecodeError, LookupError):
                return None, None
    if b"\x00" in head[:1024]:
        return None, None
    try:
        return head.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # A truncated multi-byte character at the 8 KB boundary is not a reason to
    # call a file binary, so try again without the last few bytes.
    try:
        return head[:-4].decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    printable = sum(1 for byte in head if 32 <= byte < 127
                    or byte in (9, 10, 13))
    if head and printable / len(head) > 0.92:
        return head.decode("latin-1", "replace"), "latin-1"
    return None, None


def _looks_delimited(text):
    """Consistent delimiter counts across the first few lines, and at least two.

    Deliberately strict. A log file with a comma in every line is not a CSV,
    and filing it as a spreadsheet is a worse outcome than leaving it as text.
    """
    lines = [line for line in text.splitlines()[:10] if line.strip()]
    if len(lines) < 2:
        return None
    for delimiter, fmt in ((",", "csv"), ("\t", "tsv"), (";", "csv")):
        counts = [line.count(delimiter) for line in lines]
        if counts[0] >= 1 and len(set(counts)) == 1:
            return fmt
    return None


def sniff_text(peek):
    """(kind, format, detail, confidence) for a file with no magic, or None.

    Runs only after the signature table has declined, because a great many
    text formats start with something the table already recognises.

    The confidence split is the point of this function. A file that begins
    `WEBVTT` is a WebVTT file and nothing else, so that is `STRONG`. A file
    that merely decodes as UTF-8 is text — which is certain — but its *kind*
    is not established at all: `.bat`, `.py`, `.html`, `.md` and `.srt` are
    all text, and the extension knows which one better than the bytes do. So
    the generic answer lands at `WEAK` and loses to the extension, while a
    recognised shape beats it. Getting this backwards files every shell
    script in Documents.
    """
    from evidence import STRONG, LIKELY, WEAK
    if not peek.head:
        return None                 # an empty file is empty, not text
    text, encoding = _decode_head(peek.head)
    if text is None:
        return None
    stripped = text.lstrip("﻿ \t\r\n")
    for pattern, hit in _TEXT_SHAPES:
        if pattern.search(stripped[:4096]):
            return hit + (encoding, STRONG)
    if stripped[:1] in "{[":
        try:
            json.loads(stripped)
            return ("code", "json", encoding, STRONG)
        except ValueError:
            # A JSON file longer than the head will not parse from a prefix;
            # the shape of the first line is the fallback and it is weaker.
            if re.match(r'^[\[{]\s*["\[{]', stripped):
                return ("code", "json", "prefix only", LIKELY)
    delimited = _looks_delimited(stripped)
    if delimited:
        return ("document", "delimited", delimited, LIKELY)
    return ("document", "plain-text", encoding, WEAK)
