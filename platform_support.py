"""Find optional enrichment programs without making them dependencies.

Adapted for auto-sort from siphon's self-contained bootstrap pattern.  The
table lives here rather than in a shared checkout so this program can still be
started from a copied folder or USB drive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


class Program(object):
    def __init__(self, key, binaries, purpose, packages):
        self.key = key
        self.binaries = tuple(binaries)
        self.purpose = purpose
        self.required = False
        self.packages = dict(packages)

    def package_for(self, manager):
        return self.packages.get(manager)

    def install_line(self):
        manager = current_manager()
        package = self.package_for(manager) if manager else None
        if not package:
            return ""
        return " ".join(MANAGERS[manager]["command"] + package.split())


PROGRAMS = {
    "ffprobe": Program(
        "ffprobe", ("ffprobe",),
        "reading detailed audio and video metadata",
        {"brew": "ffmpeg", "apt": "ffmpeg", "dnf": "ffmpeg",
         "pacman": "ffmpeg", "winget": "Gyan.FFmpeg"}),
    "exiftool": Program(
        "exiftool", ("exiftool",),
        "reading metadata from uncommon cameras and RAW formats",
        {"brew": "exiftool", "apt": "libimage-exiftool-perl",
         "dnf": "perl-Image-ExifTool", "pacman": "perl-image-exiftool",
         "winget": "ExifTool.ExifTool"}),
}


# `needs_root` means bootstrap shows the exact command but never runs sudo.
MANAGERS = {
    "brew": {"probe": "brew", "command": ["brew", "install"],
             "needs_root": False, "label": "Homebrew"},
    "winget": {"probe": "winget", "command": ["winget", "install", "-e", "--id"],
               "needs_root": False, "label": "winget"},
    "apt": {"probe": "apt-get", "command": ["sudo", "apt-get", "install", "-y"],
            "needs_root": True, "label": "apt"},
    "dnf": {"probe": "dnf", "command": ["sudo", "dnf", "install", "-y"],
            "needs_root": True, "label": "dnf"},
    "pacman": {"probe": "pacman", "command": ["sudo", "pacman", "-S", "--noconfirm"],
               "needs_root": True, "label": "pacman"},
}

_manager = None
_cache = {}
_EXTRA_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                os.path.expanduser("~/.local/bin"), "/usr/bin")


def current_manager():
    global _manager
    if _manager is None:
        for key in ("brew", "winget", "apt", "dnf", "pacman"):
            if shutil.which(MANAGERS[key]["probe"]):
                _manager = key
                break
        else:
            _manager = False
    return _manager or None


def locate(*binaries):
    """Find an executable even when Finder did not inherit the shell PATH."""
    for binary in binaries:
        found = shutil.which(binary)
        if found:
            return found
        for directory in _EXTRA_PATHS:
            candidate = os.path.join(directory, binary)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def find(key):
    if key not in _cache:
        _cache[key] = locate(*PROGRAMS[key].binaries)
    return _cache[key]


def forget():
    _cache.clear()


def version(key):
    path = find(key)
    if not path:
        return None
    try:
        done = subprocess.run([path, "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (done.stdout or done.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def missing():
    return [program for key, program in PROGRAMS.items() if not find(key)]


# ---------------------------------------------------------------------------
# Python packages, which are a different question from external programs
# ---------------------------------------------------------------------------

class Module(object):
    """An importable package that unlocks something, and never a requirement.

    auto-sort is standard library only, and that is not an aesthetic
    preference: everything it does to earn its name -- read headers, learn
    conventions, decide, move, remember in the ledger -- works on a bare
    Python with nothing installed. `sqlite3`, `struct`, `re` and `os` are the
    whole toolkit, and they are already there.

    There is exactly one thing a bare Python cannot do, which is put an icon
    in a Mac's menu bar. Cocoa is not reachable without PyObjC, and Apple
    stopped shipping it with the system Python, so the choice is to offer the
    install or to leave Mac users with no visible presence at all. Offering it
    is the smaller compromise, and declining still leaves a working sorter
    with its log page.
    """

    def __init__(self, key, module, purpose, package, platforms=()):
        self.key = key
        self.module = module
        self.purpose = purpose
        self.package = package
        self.platforms = tuple(platforms)
        self.required = False

    def applies_here(self):
        if not self.platforms:
            return True
        if os.name == "nt":
            return "windows" in self.platforms
        if sys.platform == "darwin":
            return "macos" in self.platforms
        return "linux" in self.platforms

    def present(self):
        try:
            import importlib.util
            return importlib.util.find_spec(self.module) is not None
        except (ImportError, ValueError, AttributeError):
            return False


MODULES = {
    "pyobjc": Module(
        "pyobjc", "AppKit",
        "the menu bar icon, so there is something to click",
        "pyobjc-framework-Cocoa", platforms=("macos",)),
}


def in_virtualenv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def module_command(module):
    """The pip invocation for this interpreter, without ever needing root.

    `--user` outside a virtual environment, because a tool that installs into
    a system Python's site-packages is a tool that breaks the system Python.
    """
    command = [sys.executable, "-m", "pip", "install"]
    if not in_virtualenv():
        command.append("--user")
    command.append(module.package)
    return command


def missing_modules():
    return [module for module in MODULES.values()
            if module.applies_here() and not module.present()]


def pip_available():
    try:
        import importlib.util
        return importlib.util.find_spec("pip") is not None
    except (ImportError, ValueError, AttributeError):
        return False
