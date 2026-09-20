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
import re
import stat

import bundles
import kinds
import mover
import userdirs


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


# ---------------------------------------------------------------------------
# Finding copies that are already filed, which the ledger cannot see
# ---------------------------------------------------------------------------
#
# The index above answers "have I filed this before", which is the right
# question for a file arriving in a funnel and the wrong one for a disk that
# already has the same recording in two folders. It can only know what
# auto-sort itself moved: a copy somebody filed by hand is invisible to it,
# and on a real machine that is most of them -- three of the four duplicate
# pairs found on the first disk this ran against were invisible for exactly
# that reason.
#
# So this reads what is on disk instead of what the ledger remembers. Same
# economy as above: sizes come free from walking, and only a size collision
# is worth opening a file for.

# Which copy is the real one, decided in two parts.
#
# First: is it in the folder its kind belongs to? A lyric sheet is a document
# and belongs under Documents however many times it was copied next to the
# music it was written for. That question comes first because it is the same
# one the sorter itself answers -- documents to Documents, music to Music --
# and a duplicate check that contradicted it would file a thing one way and
# tidy it the other.
#
# Second, among copies that agree on that: how it got there. A funnel is
# somewhere files pass through, a holding folder is somewhere auto-sort put
# what it could not place, and anywhere else is somewhere a person chose.
INTAKE = 0        # a watched folder: Downloads, the funnel
HOLDING = 1       # Unfiled, Unsorted: auto-sort's own "not yet" drawer
KEPT = 2          # anywhere else, which means somebody put it there

MIN_SIZE = 4096   # below this, identical files are usually stubs and icons


_TOKEN = re.compile(r"[A-Za-z0-9]+")
_HEXISH = re.compile(r"^[0-9a-f]{4,}$", re.I)

# Below this a name is machine noise rather than something a person wrote.
READABLE = 0.5


def name_information(stem):
    """Roughly how much a name tells somebody, from 0 to 1.

    No vocabulary and no list: a run of digits or of hex characters carries
    nothing a person can use, and a run of letters usually does. A UUID
    scores near zero however long it is, which is the point.
    """
    tokens = _TOKEN.findall(stem)
    if not tokens:
        return 0.0
    useful = 0
    for token in tokens:
        if token.isdigit():
            continue
        if _HEXISH.match(token) and not token.isalpha():
            continue
        if any(character.isalpha() for character in token):
            useful += 1
    return useful / float(len(tokens))


class Group(object):
    """Files that are byte for byte the same, and what to do about them."""

    def __init__(self, digest, size, paths, places):
        self.digest = digest
        self.size = size
        # Best place first, so `keeper` is simply the first one.
        self.paths = sorted(
            paths, key=lambda path: (tuple(-part for part in places[path]),
                                     path))
        self.places = places

    @property
    def keeper(self):
        return self.paths[0]

    @property
    def losers(self):
        """Copies in a lesser place than the keeper. Possibly none.

        Except when binning one would throw away the only readable name.
        The copy that survives is the one somebody will have to find again,
        and a folder of `exec-63512093-74d5-4282-a7fc-159ff1ce12ea.png`
        where `B04 - Oli.png` used to exist has lost something the bytes do
        not hold. Reclaiming disk is not worth that, so the group is
        reported and left whole.
        """
        best = self.places[self.keeper]
        if self.keeper_is_nameless:
            return []
        return [path for path in self.paths[1:] if self.places[path] < best]

    @property
    def keeper_is_nameless(self):
        keeper = name_information(os.path.splitext(
            os.path.basename(self.keeper))[0])
        if keeper >= READABLE:
            return False
        return any(name_information(os.path.splitext(
            os.path.basename(path))[0]) >= READABLE
            for path in self.paths[1:])

    @property
    def undecided(self):
        """Copies in a place as good as the keeper's.

        Two deliberate copies in two deliberate folders are somebody's
        filing, not a mistake to correct. They are reported and left alone.
        """
        best = self.places[self.keeper]
        if self.keeper_is_nameless:
            return list(self.paths[1:])
        return [path for path in self.paths[1:] if self.places[path] >= best]

    def wasted(self):
        return self.size * len(self.losers)


def _under(path, prefix):
    """Is `path` inside `prefix`? Expanded, because a rules file says `~`."""
    prefix = os.path.abspath(os.path.expanduser(prefix))
    return path == prefix or path.startswith(os.path.join(prefix, ""))


def at_home(path):
    """Is this file under the folder its own kind belongs in?

    By extension alone. Opening the file would be more certain and would
    cost a read of every candidate to answer a question the extension gets
    right for the cases this decides -- a `.md` beside a `.wav`.
    """
    extension = os.path.splitext(path)[1].lstrip(".").lower()
    classified = kinds.classify_extension(extension)
    if not classified:
        return False
    home = userdirs.KIND_HOMES.get(classified[0])
    if not home:
        return False
    try:
        return _under(os.path.abspath(path), userdirs.path(home[0]))
    except (OSError, KeyError):
        return False


def place_of(path, intake=(), holding=()):
    """`(in its own home, how it got here)` -- bigger is the better copy."""
    path = os.path.abspath(path)
    level = KEPT
    for prefix in holding:
        if _under(path, prefix):
            level = HOLDING
            break
    else:
        for prefix in intake:
            if _under(path, prefix):
                level = INTAKE
                break
    return (1 if at_home(path) else 0, level)


def scan(folders, intake=(), holding=(), min_size=MIN_SIZE, limit=200000,
         on_progress=None):
    """Groups of byte-identical files under `folders`.

    Holding is tested before intake on purpose: auto-sort's holding folders
    are normally *inside* a watched tree, and the more specific answer is
    the useful one.
    """
    by_size = collections.defaultdict(list)
    seen = 0
    for folder in folders:
        for directory, subdirectories, filenames in os.walk(folder):
            # A package is one thing wearing a folder's clothes. Walking
            # into a Photos library finds hundreds of identical thumbnails
            # that the library is entitled to keep, buries the real answer,
            # and reads somebody's photographs to do it. The same test the
            # rest of the project uses, so there is one list of these.
            subdirectories[:] = [
                name for name in subdirectories
                if not name.startswith(".")
                and not bundles.is_package(os.path.join(directory, name))]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                path = os.path.join(directory, filename)
                try:
                    status = os.lstat(path)
                except OSError:
                    continue
                if not stat.S_ISREG(status.st_mode):
                    continue        # symlinks are not copies of anything
                if status.st_size < min_size:
                    continue
                by_size[status.st_size].append(path)
                seen += 1
                if on_progress and seen % 2000 == 0:
                    on_progress(seen)
                if seen >= limit:
                    break

    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue            # a size nothing else shares cannot be a copy
        by_digest = collections.defaultdict(list)
        for path in paths:
            try:
                by_digest[mover.hash_path(path)].append(path)
            except OSError:
                continue
        for digest, same in by_digest.items():
            if len(same) < 2:
                continue
            places = dict((path, place_of(path, intake, holding))
                          for path in same)
            groups.append(Group(digest, size, same, places))
    groups.sort(key=lambda group: -group.wasted())
    return groups
