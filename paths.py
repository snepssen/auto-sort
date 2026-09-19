"""Where things live, and how to write a filename that survives the journey.

Two jobs that look unrelated and are not. Both are about the destination's
rules rather than the source's: where a configuration file belongs on *this*
platform, and what a filename is allowed to contain on *that* volume.

The second one is where sorters break. A file called `AC/DC — Back in Black
?.flac` is perfectly legal on APFS and impossible on the FAT32 stick it is
being copied to, and a tool that discovers this at the point of writing has
already half-moved something. Names are sanitised against the filesystem they
are going *to*, before anything is attempted.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

APPLICATION = "auto-sort"

# Illegal on Windows, and on any FAT volume anywhere — which is most USB
# sticks and most SD cards, including ones plugged into a Mac.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", "com0", "com1", "com2", "com3",
             "com4", "com5", "com6", "com7", "com8", "com9", "lpt0", "lpt1",
             "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"}

# Filesystems that cannot hold a name a Unix filesystem can.
RESTRICTED_FILESYSTEMS = {"msdos", "exfat", "vfat", "fat32", "fat", "ntfs",
                          "smbfs", "cifs"}


def config_dir():
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MACOS:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or \
            os.path.expanduser("~/.config")
    return os.path.join(base, APPLICATION)


def state_dir():
    """Where the ledger lives. Separate from config: one is yours, one is ours."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
            or os.path.expanduser("~")
    elif IS_MACOS:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or \
            os.path.expanduser("~/.local/state")
    return os.path.join(base, APPLICATION)


def rules_file(explicit=None):
    """The rules file to read: an argument, then the checkout, then config.

    A `rules.ini` beside the code wins so that somebody can try a rule set
    without touching the one their daemon is running.
    """
    if explicit:
        return os.path.abspath(explicit)
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "rules.ini")
    if os.path.exists(local):
        return local
    return os.path.join(config_dir(), "rules.ini")


def ledger_file():
    return os.path.join(state_dir(), "state.db")


def ensure(directory):
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    return directory


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def filesystem_of(path):
    """A lowercase filesystem name for the volume `path` is on, or None.

    Used only to decide how hard to sanitise. Unknown means strict, because
    guessing permissive and being wrong means a failed write halfway through
    a move.
    """
    if IS_WINDOWS:
        return "ntfs"
    try:
        import subprocess
        if IS_MACOS:
            output = subprocess.run(["/sbin/mount"], capture_output=True,
                                    text=True, timeout=5).stdout
            best = None
            for line in output.splitlines():
                match = re.match(r"^\S+ on (.+?) \((\w+)", line)
                if match and path.startswith(match.group(1)):
                    if best is None or len(match.group(1)) > len(best[0]):
                        best = (match.group(1), match.group(2).lower())
            return best[1] if best else None
    except (OSError, ValueError, ImportError):
        return None
    return None


def sanitise(component, strict=True, limit=120):
    """One path component, safe to create on the destination.

    `strict` is the Windows and FAT rule set, which is a superset of everyone
    else's. Applied by default, because the alternative is a name that works
    until the day somebody sorts onto a memory card.
    """
    if component is None:
        return ""
    text = str(component)
    text = unicodedata.normalize("NFC", text)
    # Separators become a dash rather than vanishing: `AC/DC` should read as
    # `AC-DC`, not `ACDC`.
    text = text.replace("/", "-").replace("\\", "-")
    text = _ILLEGAL.sub("", text) if strict else text
    text = text.replace(os.sep, "-")
    if os.altsep:
        text = text.replace(os.altsep, "-")
    text = re.sub(r"\s+", " ", text).strip()
    # Windows silently drops these, so two names that differ only by a
    # trailing dot collide after the fact.
    text = text.rstrip(". ")
    if strict and text.split(".")[0].lower() in _RESERVED:
        text = "_" + text
    if len(text) > limit:
        stem, dot, extension = text.rpartition(".")
        if dot and len(extension) <= 8:
            keep = max(1, limit - len(extension) - 1)
            text = stem[:keep].rstrip(". ") + "." + extension
        else:
            text = text[:limit].rstrip(". ")
    return text or "_"


def inside(root, candidate):
    """True when `candidate` really is under `root`, symlinks resolved.

    A destination template is built from tag text, and tag text comes off the
    internet. `../../..` in an album name must not be able to write outside
    the folder somebody chose.
    """
    try:
        root_real = os.path.realpath(root)
        candidate_real = os.path.realpath(candidate)
    except OSError:
        return False
    if candidate_real == root_real:
        return True
    return candidate_real.startswith(root_real.rstrip(os.sep) + os.sep)


_SUFFIXED = re.compile(r"^(?P<stem>.*?) \((?P<number>\d+)\)$")


def unique(path, taken=()):
    """A path that does not exist yet, by adding ` (2)`, ` (3)` and so on.

    Never overwrites, and never silently merges. `taken` lets a caller reserve
    names it is about to create but has not created yet, which matters when a
    single run files four `Untitled.png` into one folder.
    """
    if not os.path.exists(path) and path not in taken:
        return path
    directory, name = os.path.split(path)
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    match = _SUFFIXED.match(stem)
    if match:
        stem = match.group("stem")
        counter = int(match.group("number")) + 1
    else:
        counter = 2
    while counter < 10000:
        candidate_name = "%s (%d)%s" % (stem, counter,
                                        ("." + extension) if dot else "")
        candidate = os.path.join(directory, candidate_name)
        if not os.path.exists(candidate) and candidate not in taken:
            return candidate
        counter += 1
    raise OSError("could not find a free name beside %s" % path)


def long_path(path):
    """Windows needs a prefix to exceed 260 characters. Everyone else does not."""
    if not IS_WINDOWS:
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\") or len(absolute) < 250:
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute
