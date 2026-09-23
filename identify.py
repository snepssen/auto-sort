"""The ladder: cheap facts first, and stop as soon as the answer is settled.

The order is the design. Statting a file costs nothing, so everything gets
that. The extension costs nothing, so everything gets that too. Reading eight
kilobytes costs a seek, so it happens for everything but is the first thing
that can be skipped on a slow volume. Parsing a header costs a little more and
only happens for kinds that have one worth parsing.

Crucially the *name* is read after the kind is known, because the filename
detectors need to be told what they are looking at — an ungated music parser
reads an artist and a title out of a tax return. And provenance is read last
only because it is independent of everything else, not because it is weak: it
is frequently the strongest fact in the record.
"""

from __future__ import annotations

import datetime
import os
import re
import stat as stat_module

import bundles
import evidence
import folders
import kinds
import names as names_module
import provenance
import readers
import signatures
from evidence import CERTAIN, STRONG, LIKELY, WEAK

# How far up the ladder to climb. Every tier is additive, and stopping early
# is always safe — it produces a thinner record, never a wrong one.
TIER_STAT = 0        # stat, extension, path
TIER_SIGNATURE = 1   # magic numbers and the text sniffer
TIER_HEADER = 2      # format headers: EXIF, ID3, moov, PDF info
TIER_PROGRAMS = 3    # ffprobe and exiftool, where they exist and are needed
TIER_ALL = 3


class Sink(object):
    """Adapts the `(detector, name, value, confidence)` readers to a Record."""

    def __init__(self, record, prefix):
        self.record = record
        self.prefix = prefix

    def add(self, detector, name, value, confidence):
        self.record.set(name, value, self.prefix + detector, confidence)

    def note(self, sentence):
        self.record.note(sentence)


def identify(target, tier=TIER_ALL, record=None):
    """Everything knowable about one item, as an `evidence.Record`.

    `target` is a path or a `bundles.Item`. Passing the item is preferred: a
    record for a bundle should describe the bundle, and the sidecar count is
    something rules legitimately match on.
    """
    item = target if isinstance(target, bundles.Item) else bundles.Item(target)
    path = item.primary
    # Not `record or Record(path)`. Record defines __len__, so an empty one is
    # falsy and a caller's record was silently replaced by a fresh one — the
    # facts went into an object that was then thrown away.
    if record is None:
        record = evidence.Record(path)

    _stat(item, record)
    if tier < TIER_SIGNATURE or record.value("dataless"):
        _name_and_provenance(item, record)
        derive(record)
        return record

    _bytes(path, record, tier)
    _name_and_provenance(item, record)
    derive(record)
    return record


# ---------------------------------------------------------------------------

def _stat(item, record):
    path = item.primary
    name = os.path.basename(path)
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""

    record.set("name", name, "path", CERTAIN)
    record.set("stem", stem, "path", CERTAIN)
    record.set("ext", ext.lower(), "path", CERTAIN)
    record.set("dir", os.path.dirname(path), "path", CERTAIN)
    record.set("path", path, "path", CERTAIN)

    if item.is_dir:
        record.set("is_dir", True, "path", CERTAIN)
    if len(item.members) > 1:
        record.set("bundle", True, "bundle", CERTAIN)
        record.set("members", len(item.members), "bundle", CERTAIN)
        record.set("bundle_role", "primary", "bundle", CERTAIN)
        if item.reason:
            record.note(item.reason)
    if item.sequence:
        record.set("sequence_frames", item.sequence, "bundle", CERTAIN)

    try:
        status = os.stat(path, follow_symlinks=False)
    except OSError as error:
        record.note("could not stat: %s" % error)
        return
    if stat_module.S_ISLNK(status.st_mode):
        record.set("symlink", True, "stat", CERTAIN)
    size = item.size if len(item.members) > 1 else status.st_size
    record.set("size", size, "stat", CERTAIN)
    if size == 0:
        record.set("empty", True, "stat", CERTAIN)
    record.set("modified", _iso(status.st_mtime), "stat", CERTAIN)
    record.set("added", _iso(getattr(status, "st_birthtime", status.st_ctime)),
               "stat", CERTAIN)
    record.set("age", round((_now() - status.st_mtime) / 86400.0, 1),
               "stat", CERTAIN)

    claim = kinds.classify_extension(ext)
    if claim:
        record.set("kind", claim[0], "extension", LIKELY)
        record.set("format", claim[1], "extension", LIKELY)
    else:
        options = kinds.candidates(ext)
        if options:
            record.note("extension %r could be %s — the bytes decide"
                        % (ext.lower(),
                           " or ".join("%s/%s" % pair for pair in options)))
        elif ext:
            record.note("extension %r is not in the table" % ext.lower())
    if item.is_dir and not claim:
        record.set("kind", "app" if item.primary.endswith(".app")
                   else "folder", "path", CERTAIN)
    if item.is_dir and bundles.is_package(item.primary):
        # A package is one document the system presents as a file; surveying
        # its insides would describe an application's resources rather than
        # the thing itself.
        record.set("is_package", True, "path", CERTAIN)
    elif item.is_dir:
        # The same judgement the walker made when it kept this folder whole,
        # recorded where a rule can see it. Without it a project arrives as
        # a folder of mixed things and falls through to the last catch-all
        # -- whole, which is the part that matters, but somewhere nobody
        # would look for a CV.
        kind = bundles.project_kind(item.primary)
        if kind:
            record.set("project", kind, "folder", CERTAIN)


def _bytes(path, record, tier):
    if record.value("is_dir"):
        # A directory is read too, just not byte by byte: what it holds is
        # what decides where it belongs. Without this a folder is routed on
        # the single fact that it is a folder, which is how a video project
        # and a hundred and sixty-six pieces of artwork both ended up filed
        # as documents.
        if not record.value("is_package"):
            try:
                folders.read(path, record)
            except OSError as error:
                record.note("could not survey folder: %s" % error)
        return
    try:
        peek = signatures.Peek(path)
    except OSError as error:
        record.note("could not read: %s" % error)
        return
    try:
        hit = signatures.sniff(peek)
        if hit is None:
            hit = signatures.sniff_text(peek)
            if hit:
                record.set("text", True, "sniffer", CERTAIN)
        if hit:
            kind, fmt, detail, confidence = hit
            record.set("kind", kind, "signature", confidence)
            record.set("format", fmt, "signature", confidence)
            record.reader_ran("signature", "%s/%s%s" % (
                kind, fmt, " (%s)" % detail if detail else ""))
            if detail:
                record.set("container_detail", detail, "signature", CERTAIN)
        else:
            record.reader_ran("signature", "no match")
            record.set("kind", "data", "no signature", WEAK)

        extension_kind = kinds.classify_extension(record.value("ext"))
        if hit and extension_kind and extension_kind[1] != record.value(
                "format") and extension_kind[0] != record.value("kind"):
            record.set("extension_lies", True, "signature", CERTAIN)
            record.note("extension says %s/%s, the bytes say %s/%s"
                        % (extension_kind[0], extension_kind[1],
                           record.value("kind"), record.value("format")))

        if tier >= TIER_HEADER:
            readers.read_with_fallback(peek, record.value("kind"),
                                       record.value("format"), record)
        if tier >= TIER_PROGRAMS:
            # Only for the files that still have a gap. `--tier header` is
            # therefore also the way to say "do not start any processes".
            readers.enrich(path, record)
    finally:
        peek.close()


def _name_and_provenance(item, record):
    found = names_module.read(record.value("name", ""), record.value("kind"))
    for name, value, confidence, source in found.facts:
        record.set(name, value, source, confidence)
    if found.facts:
        record.reader_ran("filename", "%d facts" % len(found.facts))

    before = len(record)
    provenance.read(item.primary, Sink(record, ""))
    if len(record) > before:
        record.reader_ran("provenance", "%d facts" % (len(record) - before))


_DURATION_BANDS = ((5, "clip"), (60, "short"), (600, "medium"),
                   (1800, "long"), (4800, "feature"))

# Fur Affinity's download host serves files as
# ``<upload epoch>.<creator>_<title>.<ext>``.  The shape alone is not enough
# evidence to call the middle segment a creator, but the matching provenance
# host makes it a useful, source-backed classification fact.
_FURAFFINITY_NAME = re.compile(r"^\d{10}\.([A-Za-z0-9-]+)_.+")


def derive(record):
    """Facts that follow from other facts, and nothing new from the disk."""
    host = str(record.value("from_host", "")).lower()
    if host == "furaffinity.net" or host.endswith(".furaffinity.net"):
        match = _FURAFFINITY_NAME.match(str(record.value("name", "")))
        if match:
            record.set("creator", match.group(1),
                       "derived:filename+provenance", STRONG)

    width, height = record.value("width"), record.value("height")
    if width and height and not record.has("aspect"):
        record.set("aspect", round(width / float(height), 4), "derived",
                   CERTAIN)
    aspect = record.value("aspect")
    if aspect:
        record.set("orientation_class",
                   "square" if 0.95 <= aspect <= 1.05
                   else ("portrait" if aspect < 1 else "landscape"),
                   "derived", CERTAIN)

    duration = record.value("duration")
    if duration:
        for ceiling, label in _DURATION_BANDS:
            if duration < ceiling:
                record.set("duration_class", label, "derived", CERTAIN)
                break
        else:
            record.set("duration_class", "epic", "derived", CERTAIN)

    # The best date available, named once so rules do not have to try five
    # facts in order. Capture beats creation beats filesystem, because the
    # filesystem date is when the file arrived here, not when it happened.
    for source_fact in ("taken", "created", "content_created", "digitised"):
        value = record.value(source_fact)
        if value:
            record.set("happened", str(value)[:19], "derived:" + source_fact,
                       record.confidence(source_fact))
            break
    else:
        name_date = record.value("name_date")
        if name_date:
            record.set("happened", name_date, "derived:name_date", LIKELY)
        elif record.value("added"):
            record.set("happened", record.value("added"), "derived:added",
                       WEAK)

    # A screenshot claimed by the filename and by the dimensions is no longer
    # a guess; this is the corroboration the evidence model exists for.
    if record.value("capture") == "screenshot":
        fact = record.fact("capture")
        if fact and len(fact.corroborated_by) >= 1:
            record.note("screenshot agreed by %s"
                        % " and ".join([fact.source] + fact.corroborated_by))

    size = record.value("size")
    if size is not None:
        record.set("size_class", _size_class(size), "derived", CERTAIN)


def _size_class(size):
    for ceiling, label in ((1024, "tiny"), (1048576, "small"),
                           (52428800, "medium"), (1073741824, "large")):
        if size < ceiling:
            return label
    return "huge"


def _iso(timestamp):
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return None


def _now():
    return datetime.datetime.now().timestamp()
