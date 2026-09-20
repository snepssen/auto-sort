"""Grouping, because the thing being sorted is often not one file.

This is the part a file sorter gets wrong quietly. Moving `model.obj` without
`model.mtl` leaves a 3D model that opens grey. Moving a film without its `.srt`
loses the subtitles a person spent an evening finding. Moving the JPEG out of a
raw+JPEG pair separates two halves of one photograph. In every case the tool
looks like it worked.

So the scanner does not emit paths. It emits **items**, and an item is either
one file, one file with its sidecars, a directory the operating system treats
as a single document, or a numbered sequence of frames. Whatever the rules
decide, the whole item moves.

The grouping is by stem, which is what every program that writes a sidecar
actually uses. `Film.mkv`, `Film.en.srt`, `Film.nfo` and `Film-poster.jpg` all
begin with `Film`, and nothing more clever than that is needed or safe.
"""

from __future__ import annotations

import os
import re

import kinds

# Directories the operating system presents as one document. Descending into
# one of these produces hundreds of meaningless items and destroys the thing
# if any of them is moved.
PACKAGE_SUFFIXES = (
    ".app", ".bundle", ".framework", ".plugin", ".kext", ".prefpane",
    ".qlgenerator", ".component", ".mdimporter", ".xpc",
    ".pages", ".numbers", ".key", ".rtfd", ".textbundle",
    ".sparsebundle", ".photoslibrary", ".aplibrary", ".migpkg",
    ".fcpbundle", ".imovielibrary", ".theater", ".logicx", ".band",
    ".xcodeproj", ".xcworkspace", ".playground", ".dSYM",
    ".lrcat", ".lrdata", ".aedoc", ".scriv", ".graffle", ".sketch",
    ".download", ".abbu", ".mpkg", ".pkg", ".idml",
)

# Directory names that are one unit of media, or litter that travels with one.
PACKAGE_NAMES = {"bdmv", "video_ts", "audio_ts", "certificate", "__macosx",
                 "certificate.stream"}

# Extensions that only ever accompany something else.
SIDECAR_EXTENSIONS = {
    "srt", "vtt", "ass", "ssa", "sub", "idx", "sup", "smi", "sbv", "ttml",
    "lrc", "nfo", "sfv", "md5", "sha1", "sha256", "cue", "m3u", "m3u8",
    "aae", "thm", "xmp", "mtl", "bif", "torrent", "url", "txt", "log",
    "jpg", "jpeg", "png", "webp",          # cover art, only when stems match
}

# Sidecars that are only sidecars when the primary is the right kind.
_ART_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
_ART_PRIMARY_KINDS = {"video", "audio", "document", "model3d"}

RAW_EXTENSIONS = {ext for ext, (kind, fmt) in kinds.EXTENSIONS.items()
                  if fmt == "raw"}

# Which kind wins when an item has several candidates for its primary file.
_PRIMARY_ORDER = ("video", "audio", "image", "model3d", "document", "app",
                  "disk-image", "archive", "code", "data", "font",
                  "subtitle", "unknown")

_MULTIPART = (
    re.compile(r"^(?P<stem>.+?)\.part\d+\.rar$", re.I),
    re.compile(r"^(?P<stem>.+?)\.r\d{2,3}$", re.I),
    re.compile(r"^(?P<stem>.+?\.(?:7z|zip|tar|rar))\.\d{3}$", re.I),
    re.compile(r"^(?P<stem>.+?)\.z\d{2}$", re.I),
    re.compile(r"^(?P<stem>.+?)\.\d{3}$"),
)
_SEQUENCE = re.compile(r"^(?P<prefix>.*?[^0-9])(?P<number>0\d{2,7})$")
SEQUENCE_THRESHOLD = 8


class Item(object):
    """One thing to sort: a file, a file with sidecars, or a directory."""

    __slots__ = ("primary", "members", "is_dir", "reason", "sequence")

    def __init__(self, primary, members=None, is_dir=None, reason="",
                 sequence=0):
        self.primary = primary
        self.members = members or [primary]
        # `None` means "look". An item built from a bare path -- which is
        # what `explain` and any caller outside the scanner does -- would
        # otherwise claim a directory is a file, and then nothing that
        # depends on knowing it is a directory ever runs.
        self.is_dir = os.path.isdir(primary) if is_dir is None else is_dir
        self.reason = reason
        self.sequence = sequence

    @property
    def name(self):
        return os.path.basename(self.primary)

    @property
    def size(self):
        total = 0
        for member in self.members:
            try:
                total += os.path.getsize(member)
            except OSError:
                pass
        return total

    def __len__(self):
        return len(self.members)

    def __repr__(self):
        if len(self.members) == 1:
            return "Item(%s)" % self.name
        return "Item(%s + %d)" % (self.name, len(self.members) - 1)


def is_package(path):
    """True for a directory the system treats as one file."""
    lowered = os.path.basename(path).lower()
    if lowered in PACKAGE_NAMES:
        return True
    return any(lowered.endswith(suffix) for suffix in PACKAGE_SUFFIXES)


def _stem_key(name):
    """The part of a filename a sidecar would share.

    Language tags and the `-poster`/`-fanart` decorations are stripped so that
    `Film.en.forced.srt` groups with `Film.mkv`. Only one layer of each, so a
    file genuinely called `Report.2024.final.pdf` is not reduced to `Report`.
    """
    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    stem = re.sub(r"[-_](poster|fanart|banner|thumb|cover|folder|art|"
                  r"backdrop|logo|clearart|proof)$", "", stem, flags=re.I)
    stem = re.sub(r"\.(?:[a-z]{2,3}(?:-[a-z]{2,4})?)"
                  r"(?:\.(?:forced|sdh|cc|hi|default))?$", "", stem,
                  flags=re.I)
    return stem.lower()


def _extension(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""


def _kind(name):
    claim = kinds.classify_extension(_extension(name))
    return claim[0] if claim else "unknown"


def _rank(path):
    kind = _kind(os.path.basename(path))
    try:
        index = _PRIMARY_ORDER.index(kind)
    except ValueError:
        index = len(_PRIMARY_ORDER)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return (index, -size)


def group(directory, names=None):
    """Every item in one directory. Does not descend; the scanner does that.

    `names` lets a caller pass a listing it already has, which matters on a
    network share where a second `listdir` is a second round trip.
    """
    try:
        entries = names if names is not None else os.listdir(directory)
    except OSError:
        return []

    files = []
    packages = []
    for name in entries:
        path = os.path.join(directory, name)
        if os.path.isdir(path):
            if is_package(path):
                packages.append(Item(path, [path], is_dir=True,
                                     reason="package directory"))
            continue
        files.append(path)

    items = list(packages)
    claimed = set()

    # Multi-part archives first: their parts do not share a stem in the way
    # everything else does.
    parts = {}
    for path in files:
        name = os.path.basename(path)
        for pattern in _MULTIPART:
            match = pattern.match(name)
            if match:
                parts.setdefault(match.group("stem").lower(), []).append(path)
                break
    for stem, members in parts.items():
        if len(members) < 2:
            continue
        members.sort()
        items.append(Item(members[0], members, reason="%d-part archive"
                          % len(members)))
        claimed.update(members)

    # Numbered sequences: a folder of frames is one render, not four thousand
    # images, and filing them individually is how a sorter earns its removal.
    sequences = {}
    for path in files:
        if path in claimed:
            continue
        name = os.path.basename(path)
        match = _SEQUENCE.match(name.rsplit(".", 1)[0] if "." in name[1:]
                                else name)
        if match and _kind(name) in ("image", "video", "model3d"):
            key = (match.group("prefix").lower(), _extension(name))
            sequences.setdefault(key, []).append(path)
    for key, members in sequences.items():
        if len(members) < SEQUENCE_THRESHOLD:
            continue
        members.sort()
        items.append(Item(members[0], members, reason="%d-frame sequence"
                          % len(members), sequence=len(members)))
        claimed.update(members)

    # Everything else groups by stem.
    by_stem = {}
    for path in files:
        if path in claimed:
            continue
        by_stem.setdefault(_stem_key(os.path.basename(path)), []).append(path)

    for stem, members in by_stem.items():
        if len(members) == 1:
            items.append(Item(members[0]))
            continue
        members.sort(key=_rank)
        primary = members[0]
        primary_kind = _kind(os.path.basename(primary))
        kept = [primary]
        loose = []
        for other in members[1:]:
            extension = _extension(os.path.basename(other))
            if extension in _ART_EXTENSIONS:
                # A JPEG beside a film is cover art. A JPEG beside another
                # JPEG is just two photographs.
                if primary_kind in _ART_PRIMARY_KINDS:
                    kept.append(other)
                else:
                    loose.append(other)
            elif extension in SIDECAR_EXTENSIONS:
                kept.append(other)
            elif extension in RAW_EXTENSIONS or primary_kind == "image":
                # raw + JPEG, or HEIC + MOV for a Live Photo.
                kept.append(other)
            elif _kind(os.path.basename(other)) == primary_kind:
                loose.append(other)
            else:
                kept.append(other)
        reason = ""
        if len(kept) > 1:
            reason = "%d sidecar%s share the stem %r" % (
                len(kept) - 1, "" if len(kept) == 2 else "s", stem)
        items.append(Item(primary, kept, reason=reason))
        for path in loose:
            items.append(Item(path))

    items.sort(key=lambda item: item.primary)
    return items


def walk(root, max_depth=3, ignore=()):
    """Items under `root`, depth-limited, not descending into packages."""
    root = os.path.abspath(root)
    for directory, subdirectories, names in os.walk(root):
        depth = directory[len(root):].count(os.sep)
        # Packages have to be collected before the descent list is filtered,
        # or they are pruned and then never yielded — the bug that silently
        # skips every .app and every Pages document on the disk.
        packages = [name for name in subdirectories
                    if is_package(os.path.join(directory, name))]
        subdirectories[:] = [name for name in subdirectories
                             if name not in packages and name not in ignore
                             and not name.startswith(".")]
        # A depth-zero watch is an inbox: ordinary top-level folders must be
        # handled as atomic items or the inbox can never become empty. Do not
        # inspect or split their contents; the mover hashes and journals the
        # complete directory just like a platform package.
        inbox_directories = list(subdirectories) \
            if max_depth == 0 and depth == 0 else []
        if depth >= max_depth:
            subdirectories[:] = []
        for item in group(directory, names):
            yield item
        for name in packages:
            yield Item(os.path.join(directory, name),
                       [os.path.join(directory, name)], is_dir=True,
                       reason="package directory")
        for name in inbox_directories:
            yield Item(os.path.join(directory, name),
                       [os.path.join(directory, name)], is_dir=True,
                       reason="top-level inbox folder")
