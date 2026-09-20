"""The folders the operating system already made, which mostly go unused.

Every desktop ships with Pictures, Movies, Music and Documents, and on most
machines they sit empty while everything the person owns piles up in
Downloads. auto-sort files into them rather than inventing a parallel
structure, because a second set of media folders beside the ones the system
already has is not tidying, it is another mess with a nicer name.

Finding them is a per-platform question and not a guess:

*Linux* has a specification for it. `~/.config/user-dirs.dirs` is written by
the desktop environment and is where a localised or relocated Pictures folder
is recorded -- `XDG_PICTURES_DIR="$HOME/Bilder"` -- so it is read rather than
assumed.

*Windows* keeps the same information in the registry, under Shell Folders,
which is how it survives somebody moving Documents onto another drive. The
standard library can read it.

*macOS* puts them at fixed English paths and localises only the display name,
so `~/Pictures` is correct even when Finder says `Bilder`.

The fallback everywhere is the English name under the home directory, which
is right far more often than it is wrong and is never destructive: a folder
that does not exist is created the first time something is filed into it.
"""

from __future__ import annotations

import os
import re
import sys

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

# The names this project uses, and what each platform calls them.
_XDG = {
    "pictures": "XDG_PICTURES_DIR", "video": "XDG_VIDEOS_DIR",
    "music": "XDG_MUSIC_DIR", "documents": "XDG_DOCUMENTS_DIR",
    "downloads": "XDG_DOWNLOAD_DIR", "desktop": "XDG_DESKTOP_DIR",
    "templates": "XDG_TEMPLATES_DIR", "public": "XDG_PUBLICSHARE_DIR",
}
_WINDOWS_KEYS = {
    "pictures": "My Pictures", "video": "My Video", "music": "My Music",
    "documents": "Personal", "downloads": "{374DE290-123F-4565-9164-"
                                          "39C4925E467B}",
    "desktop": "Desktop",
}
_DEFAULTS = {
    "pictures": "Pictures", "music": "Music", "documents": "Documents",
    "downloads": "Downloads", "desktop": "Desktop", "public": "Public",
    "templates": "Templates",
}

_cache = None


def home():
    return os.path.expanduser("~")


def _video_default():
    # macOS calls it Movies; everyone else calls it Videos. Getting this
    # wrong creates an empty second folder beside the real one.
    return "Movies" if IS_MACOS else "Videos"


def _read_xdg():
    found = {}
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(),
                                                               ".config")
    path = os.path.join(config, "user-dirs.dirs")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            body = handle.read()
    except OSError:
        return found
    for name, key in _XDG.items():
        match = re.search(r'^\s*%s\s*=\s*"?(.*?)"?\s*$' % re.escape(key),
                          body, re.M)
        if not match:
            continue
        value = match.group(1).replace("$HOME", home())
        value = os.path.expandvars(os.path.expanduser(value))
        if value and value != home():
            found[name] = value
    return found


def _read_windows():
    found = {}
    try:
        import winreg
    except ImportError:
        return found
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                r"\Shell Folders") as key:
            for name, value_name in _WINDOWS_KEYS.items():
                try:
                    value, _kind = winreg.QueryValueEx(key, value_name)
                except OSError:
                    continue
                if value:
                    found[name] = os.path.expandvars(value)
    except OSError:
        return found
    return found


def all_dirs(refresh=False):
    """{name: path} for every standard folder, resolved for this machine."""
    global _cache
    if _cache is not None and not refresh:
        return _cache

    found = dict((name, os.path.join(home(), folder))
                 for name, folder in _DEFAULTS.items())
    found["video"] = os.path.join(home(), _video_default())

    if IS_WINDOWS:
        found.update(_read_windows())
    elif not IS_MACOS:
        found.update(_read_xdg())

    _cache = found
    return found


def path(name):
    """One standard folder by name, whether or not it exists yet."""
    return all_dirs().get(name)


def existing():
    """Only the standard folders that are actually present on this machine."""
    return dict((name, folder) for name, folder in all_dirs().items()
                if os.path.isdir(folder))


# Where each kind of file belongs, when nothing more specific is known.
# `data`, `code` and `app` have no system folder anywhere, so they go under
# Documents rather than having one invented for them at the top of the home
# directory.
KIND_HOMES = {
    "image": ("pictures", ""),
    "video": ("video", ""),
    "audio": ("music", ""),
    "document": ("documents", ""),
    "subtitle": ("video", "Subtitles"),
    "model3d": ("documents", "3D"),
    "font": ("documents", "Fonts"),
    "archive": ("documents", "Archives"),
    "disk-image": ("documents", "Disk Images"),
    "app": ("documents", "Installers"),
    "code": ("documents", "Code"),
    "data": ("documents", "Data"),
    "unknown": ("documents", "Unsorted"),
}


def home_for(kind, subfolder=""):
    """The folder a kind belongs in, as a path.

    An unrecognised kind lands under Documents/Unsorted rather than being
    refused. A funnel that keeps things back because it does not know what
    they are is a funnel that fills up.
    """
    name, default_subfolder = KIND_HOMES.get(kind, KIND_HOMES["unknown"])
    parts = [path(name)]
    if subfolder:
        parts.append(subfolder)
    elif default_subfolder:
        parts.append(default_subfolder)
    return os.path.join(*parts)


def catch_all_for(kind):
    """Where a file of this kind goes when no rule was more specific.

    Dated, because a holding folder without dates becomes the same problem
    as the Downloads folder it was meant to empty. Kinds whose home is
    already a named subfolder keep it; the four that map onto a bare system
    folder get `Unfiled` so that nothing lands loose in Pictures.
    """
    name, subfolder = KIND_HOMES.get(kind, KIND_HOMES["unknown"])
    parts = [path(name)]
    parts.append(subfolder if subfolder else "Unfiled")
    return os.path.join(*parts)


def short(target):
    """`~/Pictures` rather than the whole thing, for a file somebody reads."""
    if not target:
        return target
    base = home()
    return "~" + target[len(base):] if target.startswith(base) else target
