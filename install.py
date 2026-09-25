#!/usr/bin/env python3
"""Install auto-sort, or bring an installed one up to date.

Meant to be run by somebody who has never used a terminal before and has
pasted one line into one, so it asks few questions, answers the obvious
ones itself, and says in plain words what it did and where:

    macOS, Linux:  curl -fsSL https://raw.githubusercontent.com/snepssen/auto-sort/main/install.sh | sh
    Windows:       powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/snepssen/auto-sort/main/install.ps1 | iex"

Both lines fetch this file and run it with the Python they found. It then:

1. downloads the latest auto-sort into a folder of its own -- never over a
   folder it did not put there itself;
2. offers the optional programs (`bootstrap.py`, which never runs sudo);
3. writes the starter rules if there are none: the first sort is a
   preview on the page, and sorting starts by itself 15 minutes later
   unless somebody pauses it -- every move undoable;
4. offers to start auto-sort at login, and starts it now.

Run again, it updates: the new code replaces the old, and the rules, the
ledger and everything auto-sort has learnt -- which live outside the code
folder -- are untouched. Standard library only, like everything else, and
written for Python 3.8.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

REPOSITORY = "https://github.com/snepssen/auto-sort"
ARCHIVE = REPOSITORY + "/archive/refs/heads/%s.zip"
MARKER = ".installed-by-auto-sort"
MAX_ARCHIVE = 64 * 1024 * 1024
TIMEOUT = 60


class InstallError(Exception):
    """Something the person has to be told about, in words they can use."""


def default_folder():
    """Where the program lives: the place each system keeps per-user apps."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Applications", "auto-sort")
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            home, "AppData", "Local")
        return os.path.join(base, "Programs", "auto-sort")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        home, ".local", "share")
    return os.path.join(base, "auto-sort")


def download(url, limit=MAX_ARCHIVE):
    """The bytes at `url`, bounded, or InstallError."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except OSError as error:
        raise InstallError("could not download auto-sort (%s). Check the "
                           "internet connection and try again." % error)
    if len(data) > limit:
        raise InstallError("the download was larger than auto-sort ever is; "
                           "stopped rather than trust it")
    return data


def unpack(archive, parent):
    """The archive's one top folder, unpacked into a new folder in `parent`.

    Checked before it is trusted: every member must stay inside the folder
    (a zip can name `../../` paths), and it must be auto-sort.
    """
    staging = tempfile.mkdtemp(prefix=".auto-sort-new-", dir=parent)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            root = os.path.realpath(staging)
            for member in bundle.namelist():
                target = os.path.realpath(os.path.join(staging, member))
                if target != root and not target.startswith(root + os.sep):
                    raise InstallError("the download contained a path "
                                       "outside its folder; stopped")
            bundle.extractall(staging)
            for info in bundle.infolist():
                # Zip keeps Unix permissions in the high bits; extractall
                # drops them, and the launchers must stay executable.
                mode = info.external_attr >> 16
                if mode & 0o111:
                    path = os.path.join(staging, info.filename)
                    os.chmod(path, os.stat(path).st_mode | 0o755)
    except zipfile.BadZipFile:
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallError("the download was not a zip archive; try again")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    entries = [name for name in os.listdir(staging)
               if not name.startswith(".")]
    top = os.path.join(staging, entries[0]) if len(entries) == 1 else staging
    if not os.path.isfile(os.path.join(top, "autosort.py")):
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallError("the download did not contain auto-sort")
    return staging, top


def place(top, folder):
    """Put the unpacked program at `folder`, replacing only our own.

    A folder that exists and was not made by this installer is somebody's,
    whatever it is called: a checkout they work in, or something else
    entirely. It is never replaced. The previous version is kept beside the
    new one as `<folder>.previous` until the next update, so a broken
    download can be put back by renaming a folder.
    """
    if os.path.exists(folder) and os.listdir(folder) and \
            not os.path.exists(os.path.join(folder, MARKER)):
        raise InstallError(
            "%s already exists and was not made by this installer, so it "
            "was left alone. Move it, or install elsewhere with --folder."
            % folder)
    with open(os.path.join(top, MARKER), "w") as handle:
        handle.write("This folder is replaced when auto-sort is updated. "
                     "Your rules and history live elsewhere.\n")
    previous = folder + ".previous"
    if os.path.exists(folder):
        # Rules somebody kept beside the code travel with it.
        for name in os.listdir(folder):
            if name == "rules.ini" or name.startswith("rules.ini."):
                shutil.copy2(os.path.join(folder, name),
                             os.path.join(top, name))
        if os.path.exists(previous):
            shutil.rmtree(previous)
        os.replace(folder, previous)
    os.replace(top, folder)


def ask(question, assume_yes, default=True):
    """Yes or no; the default when nobody is there to answer."""
    if assume_yes or not sys.stdin or not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def run(folder, *arguments):
    """auto-sort's own command line, from the installed copy."""
    return subprocess.call([sys.executable,
                            os.path.join(folder, "autosort.py")]
                           + list(arguments))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install auto-sort, or update an installed one.")
    parser.add_argument("--folder", default=default_folder(),
                        help="where to put it (default: %(default)s)")
    parser.add_argument("--branch", default="main",
                        help="which version to fetch (default: main)")
    parser.add_argument("--archive", help=argparse.SUPPRESS)
    parser.add_argument("--yes", "-y", action="store_true",
                        help="answer yes to every question")
    parser.add_argument("--no-tools", action="store_true",
                        help="do not offer the optional programs")
    parser.add_argument("--no-login", action="store_true",
                        help="do not start auto-sort at login")
    parser.add_argument("--no-start", action="store_true",
                        help="do not start it now")
    options = parser.parse_args(argv)

    if sys.version_info < (3, 8):
        print("auto-sort needs Python 3.8 or newer; this is %d.%d."
              % sys.version_info[:2])
        return 1
    folder = os.path.abspath(os.path.expanduser(options.folder))
    updating = os.path.exists(os.path.join(folder, MARKER))
    print()
    print("  %s auto-sort in %s" % ("Updating" if updating else "Installing",
                                    folder))
    try:
        parent = os.path.dirname(folder)
        os.makedirs(parent, exist_ok=True)
        if options.archive:
            with open(options.archive, "rb") as handle:
                archive = handle.read()
        else:
            print("  Downloading the latest version...")
            archive = download(ARCHIVE % options.branch)
        staging, top = unpack(archive, parent)
        try:
            place(top, folder)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except (InstallError, OSError) as error:
        print()
        print("  Nothing was changed: %s" % error)
        return 1
    print("  Done.")

    if not options.no_tools:
        print()
        # Its own questions, in its own words; it never runs sudo.
        subprocess.call([sys.executable,
                         os.path.join(folder, "bootstrap.py")]
                        + (["--yes"] if options.yes else []))

    # The starter rules, if there are none. Their first sort is a preview.
    fresh = not _asks(folder, "import os, paths; "
                              "ok = os.path.exists(paths.rules_file())")
    if fresh:
        print()
        run(folder, "init")

    if not options.no_login and ask(
            "\n  Start auto-sort by itself whenever you log in?",
            options.yes):
        run(folder, "autostart", "install")
    if not options.no_start:
        print()
        # An update: the running daemon is still the old code until it is
        # replaced. Then the page, starting the daemon first if nothing is
        # running -- the first thing somebody new should see.
        if _asks(folder, "import daemon; ok = bool(daemon.running_port())"):
            run(folder, "restart")
        run(folder, "open-log", "--start")

    print()
    print("  auto-sort is in %s" % folder)
    print("  Open its page:     %s" % _command(folder, "open-log"))
    if fresh:
        print()
        print("  First it shows, on that page, where everything would go.")
        print("  After 15 minutes it starts sorting by itself; click Keep")
        print("  previewing to wait longer, or Start sorting now. Every move")
        print("  can be undone.")
    print()
    print("  To update later, run the same line you installed it with.")
    print("  If something goes wrong: %s" % os.path.join(
        folder, "REPORT-A-PROBLEM" + (".bat" if sys.platform.startswith("win")
                                      else ".command"
                                      if sys.platform == "darwin" else ".sh")))
    print()
    return 0


def _asks(folder, code):
    """Run a line of auto-sort's own Python; True if it sets `ok`."""
    program = ("import sys; sys.path.insert(0, %r)\n%s\n"
               "sys.exit(0 if ok else 1)" % (folder, code))
    try:
        return subprocess.call([sys.executable, "-c", program],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0
    except OSError:
        return False


def _command(folder, *arguments):
    python = sys.executable
    if " " in python:
        python = '"%s"' % python
    script = os.path.join(folder, "autosort.py")
    if " " in script:
        script = '"%s"' % script
    return " ".join([python, script] + list(arguments))


if __name__ == "__main__":
    sys.exit(main())
