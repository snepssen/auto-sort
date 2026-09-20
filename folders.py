"""What a folder contains, so that it can be filed like anything else.

Every other item in this program is classified by reading it. A directory was
the one exception: it was routed on the single fact that it *is* a directory,
which is how a video project and a hundred and sixty-six pieces of artwork
both ended up in the documents cabinet. "It is a folder" answers what kind of
thing it is and says nothing at all about where it belongs.

So a folder is surveyed the same way a disk is: walk it, classify each file by
extension, and report what dominates. Extensions only -- no file is opened --
because this runs on directories that may hold a hundred thousand files and
the answer does not need a magic number to be right about a folder that is
ninety-eight per cent video.

**Bytes decide, not file counts.** A folder of thirty-four videos and six
subtitle files is a video folder, and one of twenty-three clips beside
eighty-three thumbnails still is. Counting files would call both of those
something else. The count is recorded too, because the two disagreeing is
itself worth knowing, but `contains` follows the bytes.

**Nothing is claimed when nothing dominates.** Below the threshold the folder
is marked mixed and `contains` is left unset, so a rule keyed on it simply
declines -- the same way every other absent fact behaves here.
"""

from __future__ import annotations

import collections
import os

import bundles
import kinds

# Enough to characterise a folder, few enough to stay cheap on a directory
# with a hundred thousand files in it. The answer to "what is this mostly"
# does not change between the four thousandth file and the last.
MAX_FILES = 4000

# How much of the bytes one kind needs before the folder is called that.
STRONG_SHARE = 0.80
LIKELY_SHARE = 0.60

IGNORED = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}


class Contents(object):
    """The result of a survey: what is in there, and how sure that is."""

    __slots__ = ("files", "bytes", "by_bytes", "by_count", "truncated")

    def __init__(self):
        self.files = 0
        self.bytes = 0
        self.by_bytes = collections.Counter()
        self.by_count = collections.Counter()
        self.truncated = False

    @property
    def dominant(self):
        """(kind, share of bytes), or (None, 0.0) when the folder is empty."""
        if not self.bytes:
            # No bytes at all: fall back to what there is most of, so a
            # folder of empty files is still described rather than refused.
            if not self.by_count:
                return None, 0.0
            kind, count = self.by_count.most_common(1)[0]
            return kind, count / float(self.files or 1)
        kind, size = self.by_bytes.most_common(1)[0]
        return kind, size / float(self.bytes)

    @property
    def dominant_by_count(self):
        if not self.by_count:
            return None
        return self.by_count.most_common(1)[0][0]

    def __repr__(self):
        kind, share = self.dominant
        return "Contents(%s %.0f%% of %d files)" % (kind, share * 100,
                                                    self.files)


def survey(path, max_files=MAX_FILES):
    """Walk a directory and report what it is made of. Opens nothing."""
    found = Contents()
    for directory, subdirectories, names in os.walk(path):
        # A package inside is one opaque thing, not a thousand resources: an
        # .app in a folder would otherwise drown everything else in it.
        subdirectories[:] = [
            name for name in subdirectories
            if not name.startswith(".")
            and not bundles.is_package(os.path.join(directory, name))]
        for name in names:
            if name.startswith(".") or name.lower() in IGNORED:
                continue
            if found.files >= max_files:
                found.truncated = True
                return found
            claim = kinds.classify_extension(_extension(name))
            kind = claim[0] if claim else "unknown"
            try:
                size = os.path.getsize(os.path.join(directory, name))
            except OSError:
                size = 0
            found.files += 1
            found.bytes += size
            found.by_bytes[kind] += size
            found.by_count[kind] += 1
    return found


def _extension(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""


def read(path, record, max_files=MAX_FILES):
    """Add what a folder contains to `record`. Returns the survey."""
    from evidence import CERTAIN, STRONG, LIKELY

    found = survey(path, max_files)
    record.set("contains_files", found.files, "folder", CERTAIN)
    record.set("contains_bytes", found.bytes, "folder", CERTAIN)
    if found.truncated:
        record.set("contains_truncated", True, "folder", CERTAIN)
    if not found.files:
        record.set("empty", True, "folder", CERTAIN)
        record.reader_ran("folder", "empty")
        return found

    kind, share = found.dominant
    record.set("contains_share", round(share, 3), "folder", CERTAIN)
    by_count = found.dominant_by_count
    if by_count:
        record.set("contains_by_count", by_count, "folder", CERTAIN)

    if kind and share >= LIKELY_SHARE:
        record.set("contains", kind, "folder",
                   STRONG if share >= STRONG_SHARE else LIKELY)
    else:
        # Nothing dominates, so nothing is claimed. A rule keyed on
        # `contains` declines, which is what an absent fact does everywhere
        # else in this program.
        record.set("contains_mixed", True, "folder", CERTAIN)

    if by_count and kind and by_count != kind:
        record.note("mostly %s by size but %s by file count"
                    % (kind, by_count))
    record.reader_ran("folder", "%d files, %s %.0f%%"
                      % (found.files, kind or "mixed", share * 100))
    return found
