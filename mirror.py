"""A second copy, on a disk that is usually asleep and sometimes in a drawer.

Nobody this program is for has a backup. That is the whole problem and it is
not solved by telling them to make one -- it is solved by the thing that is
already moving their files putting a copy somewhere else while it does.

So: off by default, because out of the box auto-sort should touch nothing but
the folders the computer already has. Turned on by choosing a second place to
put things, which is a few clicks on the log page. From then on every file it
files is also copied there.

**Sorting never waits for a backup and never fails because of one.** The
external disk is unplugged, asleep, full, or was taken to a caravan in
Wales. That is the ordinary case, not the exception. So the intention to copy
is recorded in the ledger the instant a file is placed, and the copying
happens later, whenever the disk is actually there. A drive that is missing
for three weeks means a queue three weeks long and nothing else: no failed
sorts, no error the person has to understand, no files left in Downloads.

**Copied, verified, then named.** The copy is written beside its final name
and hashed as it goes; it only takes the real name once the bytes match what
was recorded. A power cut during a copy leaves a stray part-file rather than
a half file wearing the name of a whole one -- which is the difference
between a backup and a story about a backup.

**Nothing here deletes.** Not from the mirror, ever. If somebody throws away
the original next year the copy stays, which is what a person means by
"backup" even when what they asked for was "sync".
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil

MAX_ATTEMPTS = 5          # before a file is set aside rather than retried forever
CHUNK = 1024 * 1024


class Unavailable(Exception):
    """The second disk is not there. Not an error -- a reason to wait."""


# Every platform's bin, by the names they actually use on disk.
_BINS = (".Trash", ".Trashes", "Trash (auto-sort)", "$RECYCLE.BIN",
         "RECYCLER")


def in_a_bin(path):
    """Is this file in the wastebasket?

    auto-sort puts duplicates there, so without this the backup faithfully
    preserves everything the person just threw away -- and a backup that
    resurrects the bin is worse than none, because restoring it undoes the
    tidying that made them trust the program.
    """
    parts = os.path.abspath(path).split(os.sep)
    return any(part in _BINS or part.startswith("Trash-") for part in parts)


def relative_for(path, home=None):
    """Where a file sits under the mirror, as a shadow of the home folder.

    A backup nobody can read is a backup nobody will restore from, so the
    mirror is laid out exactly like what it copies: `Documents/Rechnung/x.pdf`
    stays `Documents/Rechnung/x.pdf`. Anything outside the home folder keeps
    its own shape under `Elsewhere`, because two disks' worth of absolute
    paths cannot share one tree.
    """
    home = os.path.abspath(home or os.path.expanduser("~"))
    path = os.path.abspath(path)
    if in_a_bin(path):
        return None
    if path.startswith(os.path.join(home, "")):
        return os.path.relpath(path, home)
    drive, rest = os.path.splitdrive(path)
    rest = rest.lstrip(os.sep).lstrip("/")
    return os.path.join("Elsewhere", rest) if rest else None


def available(root):
    """Is the second place actually there and writable right now?

    Checked by writing, not by `os.path.isdir`. A mount point whose disk has
    gone is still a directory: it is simply an empty one on the machine's own
    disk, and copying a backup into it silently fills the boot drive with a
    copy of itself.
    """
    if not root:
        return False
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        return False
    probe = os.path.join(root, ".auto-sort-writable")
    try:
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
    except OSError:
        return False
    return True


def _hashed_copy(source, target):
    digest = hashlib.sha256()
    with open(source, "rb") as reader, open(target, "wb") as writer:
        while True:
            chunk = reader.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def copy_one(source, root, relative, expected=""):
    """Put one file on the second disk. Returns the path it landed at.

    Raises `Unavailable` when the disk is not there or has no room, which
    the caller treats as "later" rather than "failed".
    """
    if not available(root):
        raise Unavailable("the second location is not available")
    target = os.path.join(os.path.expanduser(root), relative)
    folder = os.path.dirname(target)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as error:
        raise Unavailable("could not make %s: %s" % (folder, error))

    if os.path.exists(target) and expected:
        # Already there and already right: a backfill run twice costs a stat.
        try:
            if os.path.getsize(target) == os.path.getsize(source):
                return target
        except OSError:
            pass

    part = target + ".auto-sort-part"
    try:
        written = _hashed_copy(source, part)
    except OSError as error:
        _forget(part)
        if error.errno in (errno.ENOSPC, errno.EDQUOT, errno.EROFS,
                           errno.EIO, errno.ENODEV, errno.ENXIO):
            raise Unavailable("no room or no disk: %s" % error)
        raise
    if expected and written != expected:
        _forget(part)
        raise IOError("copy of %s did not match what was filed" % relative)
    try:
        os.replace(part, target)
        shutil.copystat(source, target)
    except OSError as error:
        _forget(part)
        raise Unavailable("could not finish %s: %s" % (relative, error))
    return target


def _forget(path):
    try:
        os.remove(path)
    except OSError:
        pass


def drain(journal, root, limit=200, on_progress=None):
    """Copy what is waiting. Returns `(copied, still waiting, skipped)`.

    Stops at the first sign the disk has gone rather than working through
    two thousand files to fail at each one.
    """
    if not available(root):
        return 0, len(journal.pending_mirror(limit=10000)), 0
    copied = skipped = 0
    for row in journal.pending_mirror(limit):
        source = row["source"]
        if not os.path.isfile(source):
            # Moved, renamed or binned since. The mirror is not a sync and
            # does not chase; what is already copied stays copied.
            journal.mirror_failed(row["id"], "no longer at %s" % source,
                                  give_up=True)
            skipped += 1
            continue
        try:
            copy_one(source, root, row["relative"], row["sha256"])
        except Unavailable as error:
            journal.mirror_failed(row["id"], error)
            break
        except (OSError, IOError) as error:
            journal.mirror_failed(
                row["id"], error,
                give_up=row["attempts"] + 1 >= MAX_ATTEMPTS)
            continue
        journal.mirror_done(row["id"])
        copied += 1
        if on_progress:
            on_progress(copied)
    waiting = len(journal.pending_mirror(limit=10000))
    return copied, waiting, skipped


def backfill(journal, home=None, limit=100000):
    """Queue everything auto-sort has already filed and still can find.

    Turning a backup on should protect what somebody already has, not only
    what arrives next. Twenty years of files were sorted before they clicked
    the button; a backup that starts from the click is a backup of nothing
    for a very long time.

    Costs one stat per recorded move and no copying -- `queue_mirror`
    ignores what is already queued, so this is safe to run as often as
    anybody likes.
    """
    queued = 0
    for row in journal.connection.execute(
            "SELECT id, destination, size, sha256 FROM moves "
            "WHERE status IN ('done','copied') AND undone_at IS NULL "
            "AND destination <> '' ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall():
        destination = row["destination"]
        if not destination or not os.path.isfile(destination):
            continue
        relative = relative_for(destination, home)
        if not relative:
            continue
        journal.queue_mirror(row["id"], destination, relative,
                             row["size"], row["sha256"])
        queued += 1
    return queued
