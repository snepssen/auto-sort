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

# Files whose whole job is to refer to other files in the same folder: a
# build manifest, a project file, a repository. A folder holding one is a
# project, and its contents only work together -- a CV's `.tex` does not
# compile without the `altacv.cls` beside it, a Reaper session is nothing
# without its audio, a repository is nothing without the rest of the tree.
# This is knowledge about file formats, like the signature table, and not
# about anybody's language.
PROJECT_NAMES = {
    ".git", ".hg", ".svn",
    "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
    "makefile", "cmakelists.txt", "build.gradle", "pom.xml", "gemfile",
    "composer.json", "latexmkrc", ".latexmkrc", "dockerfile",
}
PROJECT_SUFFIXES = (
    ".sln", ".csproj", ".rpp", ".als", ".flp", ".ptx", ".cpr", ".kdenlive",
    ".prproj", ".aep", ".veg", ".drp", ".blend", ".kra", ".psd", ".afdesign",
    ".uproject", ".godot", ".unity",
)

# A LaTeX document is only a project when it has company. A lone `.tex` is a
# document like any other; one with a class file, a style or a bibliography
# beside it is a build that breaks the moment they are separated.
_LATEX_COMPANIONS = (".cls", ".sty", ".bib", ".bst", ".bbx", ".cbx")


# What each marker says the project is, for the `project` fact.
_PROJECT_KIND = (
    ((".git", ".hg", ".svn"), "repository"),
    (("package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
      "makefile", "cmakelists.txt", "build.gradle", "pom.xml", "gemfile",
      "composer.json", "dockerfile", ".sln", ".csproj"), "code"),
    (("latexmkrc", ".latexmkrc"), "latex"),
    ((".rpp", ".als", ".flp", ".ptx", ".cpr"), "audio"),
    ((".kdenlive", ".prproj", ".aep", ".veg", ".drp"), "video"),
    ((".blend", ".uproject", ".godot", ".unity"), "3d"),
    ((".kra", ".psd", ".afdesign"), "artwork"),
)


def project_kind(path):
    """What sort of project a folder is, or None if it is not one.

    Found on a real machine the hard way: ten CV folders, each a LaTeX
    build with its class file and a font map beside the `.tex`, were sorted
    one file at a time by name. Every `altacv.cls` ended up pooled in one
    folder, away from every CV that needed it, and none of the ten would
    compile afterwards. Nothing was lost -- everything was journalled -- but
    a sorter that breaks a build to file it has not tidied anything.

    Decided by what is inside, never by what the folder is called.
    """
    try:
        names = os.listdir(path)
    except OSError:
        return None
    lowered = [name.lower() for name in names]
    for markers, kind in _PROJECT_KIND:
        for name in lowered:
            if name in markers or any(marker.startswith(".")
                                      and name.endswith(marker)
                                      and marker not in (".git", ".hg",
                                                         ".svn")
                                      for marker in markers):
                return kind
    has_companion = any(name.endswith(_LATEX_COMPANIONS) for name in lowered)
    if has_companion:
        for name in names:
            if name.lower().endswith(".tex") and _is_latex_root(
                    os.path.join(path, name)):
                return "latex"
    return None


def is_project(path):
    """True for an ordinary folder whose contents only work together."""
    return project_kind(path) is not None


def _is_latex_root(path):
    """A `.tex` that starts a document, rather than one that is included."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False
    return b"\\documentclass" in head


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
        # A project is kept whole the same way a package is, and for the
        # same reason: moving part of one breaks the rest. The watched
        # folder itself is never one -- it is where things arrive, not a
        # thing -- so only folders inside it are asked.
        projects = [name for name in subdirectories
                    if name not in packages and not name.startswith(".")
                    and name not in ignore
                    and is_project(os.path.join(directory, name))]
        subdirectories[:] = [name for name in subdirectories
                             if name not in packages and name not in projects
                             and name not in ignore
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
        for name in projects:
            yield Item(os.path.join(directory, name),
                       [os.path.join(directory, name)], is_dir=True,
                       reason="project folder")
        for name in inbox_directories:
            yield Item(os.path.join(directory, name),
                       [os.path.join(directory, name)], is_dir=True,
                       reason="top-level inbox folder")
