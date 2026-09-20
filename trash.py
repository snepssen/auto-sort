"""Putting a file in the operating system's own bin, and never past it.

auto-sort does not delete. That has been true since the first line of it and
it stays true here: the bin is a place the file still exists, that the person
already knows how to open, and that they empty on their own schedule. Every
desktop has one and every desktop user understands it, which is worth more
than any folder this program could invent and then have to explain.

The move is also recorded in the ledger like any other, so `auto-sort undo`
brings it back without anybody opening the bin at all. Two ways back is the
right number for the only operation here that looks like losing something.

Each platform keeps its bin somewhere different and Linux additionally wants
a note saying where the file came from, which is what makes `Restore` work
in a file manager. Nothing here needs a library: it is a rename and, on
Linux, a small text file beside it.
"""

from __future__ import annotations

import os
import sys
import time


class TrashError(Exception):
    """The bin could not be reached, so the file was left where it is."""


def _unique(folder, name):
    """A name not already taken in the bin, because bins collect repeats."""
    candidate = os.path.join(folder, name)
    if not os.path.exists(candidate):
        return candidate
    stem, extension = os.path.splitext(name)
    for number in range(2, 10000):
        candidate = os.path.join(folder, "%s (%d)%s"
                                 % (stem, number, extension))
        if not os.path.exists(candidate):
            return candidate
    raise TrashError("the bin already holds every name this file could take")


def _nearest_existing(path):
    """The closest ancestor that exists, so a path can be stat'd at all.

    `folder_for` is asked about files that have not been written yet, and a
    failed stat used to read as "different volume" -- which sent a home file
    to the root disk's bin.
    """
    path = os.path.abspath(path)
    while not os.path.exists(path) and path != os.path.dirname(path):
        path = os.path.dirname(path)
    return path


def _same_volume(one, other):
    try:
        return (os.stat(_nearest_existing(one)).st_dev
                == os.stat(_nearest_existing(other)).st_dev)
    except OSError:
        return False


def _mac_folder(path):
    home = os.path.expanduser("~/.Trash")
    if _same_volume(path, os.path.expanduser("~")):
        return home
    # A file on an external disk goes into that disk's own bin. Moving it to
    # the home one would be a copy and a delete across volumes, which is the
    # one thing this must not turn into.
    mount = _nearest_existing(path)
    while not os.path.ismount(mount) and mount != os.path.dirname(mount):
        mount = os.path.dirname(mount)
    return os.path.join(mount, ".Trashes", str(os.getuid()))


def _xdg_folder(path):
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    home_trash = os.path.join(base, "Trash")
    if _same_volume(path, os.path.expanduser("~")):
        return home_trash
    mount = _nearest_existing(path)
    while not os.path.ismount(mount) and mount != os.path.dirname(mount):
        mount = os.path.dirname(mount)
    return os.path.join(mount, ".Trash-%d" % os.getuid())


def folder_for(path):
    """Where this file's bin is, without creating anything."""
    if sys.platform == "darwin":
        return _mac_folder(path)
    if os.name == "nt":
        # Windows keeps its Recycle Bin behind a shell call rather than a
        # folder anybody may rename into. Until that is written and actually
        # run on Windows, a plainly named folder beside the profile is
        # honest: the file is still there and still findable, and the ledger
        # can still undo it. Claiming to use the Recycle Bin without ever
        # having tested it would be the worse answer.
        return os.path.join(os.path.expanduser("~"), "Trash (auto-sort)")
    return _xdg_folder(path)


def send(path, files_only=True):
    """Move one file to the bin. Returns where it went.

    Raises `TrashError` rather than falling back to deleting. There is no
    circumstance in which this function removes a file.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise TrashError("nothing at %s" % path)
    if files_only and not os.path.isfile(path):
        raise TrashError("%s is not a file" % path)

    base = folder_for(path)
    wants_info = os.name != "nt" and sys.platform != "darwin"
    target_folder = os.path.join(base, "files") if wants_info else base
    try:
        os.makedirs(target_folder, exist_ok=True)
        if wants_info:
            os.makedirs(os.path.join(base, "info"), exist_ok=True)
    except OSError as error:
        raise TrashError("could not open the bin: %s" % error)

    destination = _unique(target_folder, os.path.basename(path))
    if wants_info:
        # Written before the move, so a crash leaves a stray note rather
        # than a file in the bin that nothing knows the way back from.
        note = os.path.join(base, "info",
                            os.path.basename(destination) + ".trashinfo")
        try:
            with open(note, "w") as handle:
                handle.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n"
                             % (path, time.strftime("%Y-%m-%dT%H:%M:%S")))
        except OSError as error:
            raise TrashError("could not write the bin's note: %s" % error)

    try:
        os.rename(path, destination)
    except OSError as error:
        raise TrashError("could not move it to the bin: %s" % error)
    return destination
