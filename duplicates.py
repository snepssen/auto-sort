"""Recognising a file auto-sort has already filed somewhere else.

The ledger has recorded a sha256 for every file it has ever moved, and until
now did nothing with them. So `duplicate_of` was a fact no reader ever set,
and the rule written against it could never fire -- the information was
there, complete, and unused.

Duplicates mostly arrive by accident rather than intent. macOS has no cut and
paste for files, so moving one means copying it and then remembering to go
back and delete the original, and the second half is the half that does not
happen. Two identical files then sit in two places for no reason anybody
chose.

**Size first, hash second.** Hashing every file to find out whether it has
been seen before would make identification cost a full read of the disk. Two
files of different sizes cannot be identical, and size comes free from the
`stat` that has already happened -- so only a size collision is worth reading
for, and on a real ledger that is a small fraction of candidates.

**A duplicate is only a duplicate of something that still exists.** The
ledger remembers where a file was put, not where it is now: it may have been
moved, renamed or thrown away since. A match whose other copy has gone is not
reported, because there is nothing left for it to be a duplicate of.

Nothing here deletes anything, and nothing here decides what to do about a
duplicate. It sets a fact; a rule decides.
"""

from __future__ import annotations

import collections
import os

import mover


class Index(object):
    """Sizes and hashes of everything auto-sort has filed, for lookup."""

    def __init__(self, journal=None, source_root=None):
        self.by_size = collections.defaultdict(list)   # size -> [(sha, path)]
        self.hashed = 0
        self.checked = 0
        if journal is not None:
            self._load(journal, source_root)

    def _load(self, journal, source_root):
        sql = ("SELECT m.sha256, m.destination, m.size FROM moves m "
               "JOIN runs r ON r.id = m.run_id "
               "WHERE m.status IN ('done', 'copied') AND m.undone_at IS NULL "
               "AND m.sha256 <> ''")
        parameters = []
        if source_root:
            sql += " AND r.source_root = ?"
            parameters.append(source_root)
        for digest, destination, size in journal.connection.execute(
                sql, parameters).fetchall():
            self.remember(size, digest, destination, must_exist=True)

    def remember(self, size, digest, path, must_exist=True):
        """Add a known file.

        `must_exist` separates the two kinds of entry. One comes from the
        ledger and describes a file that was put somewhere previously, which
        may since have been moved or thrown away -- so it only counts while
        it is still there. The other is something planned a moment ago in
        this same run: it does not exist yet and is about to, and requiring
        it to exist means two copies arriving together are never recognised
        as copies. Which is the commonest case there is, because the copy is
        usually why they arrived together.
        """
        entry = (digest, path, bool(must_exist))
        bucket = self.by_size[int(size or 0)]
        if entry not in bucket:
            bucket.append(entry)

    def find(self, path, size=None):
        """Where an identical copy already is, or None.

        Reads `path` only when something already filed shares its size.
        """
        self.checked += 1
        try:
            size = int(os.path.getsize(path) if size is None else size)
        except (OSError, TypeError, ValueError):
            return None
        candidates = self.by_size.get(size)
        if not candidates or size == 0:
            return None
        # A recorded copy counts only while it is still where it was put; a
        # planned one counts because it is about to be.
        live = [(digest, other) for digest, other, must_exist in candidates
                if os.path.abspath(other) != os.path.abspath(path)
                and (not must_exist or os.path.exists(other))]
        if not live:
            return None
        try:
            digest = mover.hash_path(path)
        except (OSError, mover.MoveError):
            return None
        self.hashed += 1
        for known, other in live:
            if known == digest:
                return other
        return None


def annotate(record, index, path, size=None):
    """Set `duplicate_of` on a record when an identical copy already exists."""
    if index is None:
        return None
    from evidence import CERTAIN
    other = index.find(path, size)
    if other:
        record.set("duplicate_of", other, "ledger", CERTAIN)
        record.note("identical to a file already filed at %s" % other)
    return other
