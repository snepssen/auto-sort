#!/usr/bin/env python3
"""Offer the optional extras, and say plainly that nothing here is required.

The sorting itself needs nothing. Reading headers, learning naming
conventions, deciding, moving files and remembering all of it in the ledger
are done with `struct`, `re`, `os` and `sqlite3`, which every Python already
has. That is the point: once this is installed it runs by itself, improves by
itself, and does not turn into a development environment somebody has to
maintain.

So this module never blocks and never installs anything without being asked.
What it offers are three genuine capability upgrades, described by what they
let the tool do rather than by what they are called:

  ffprobe    durations and frame sizes for containers our own parsers decline
  exiftool   metadata from uncommon cameras and RAW formats

There is no third-party Python package in that list, on any platform. The
menu bar icon is built on the Objective-C runtime through `ctypes`, the same
way the Windows one is built on `Shell_NotifyIcon`, so neither needs anything
installed. Declining both programs above still leaves a working sorter; what
is lost is durations for a few container formats and metadata for a few
unusual cameras.

It never runs `sudo`. On a system whose package manager needs root the
commands are printed for somebody to run, because a program that silently
escalates is a program nobody should have installed.
"""

from __future__ import print_function

import sys

import platform_support as programs


def survey():
    return {"manager": programs.current_manager(),
            "optional": programs.missing()}


def command_for(program, manager):
    package = program.package_for(manager)
    if not package:
        return None
    return programs.MANAGERS[manager]["command"] + package.split()


def describe(program, manager):
    command = command_for(program, manager)
    return " ".join(command) if command else "(no %s package known)" % manager


def install(chosen, manager, on_line=None):
    """Install non-root packages and return (program, ok, detail) tuples."""
    results = []
    for program in chosen:
        command = command_for(program, manager)
        if not command:
            results.append((program, False, "no package known for this system"))
            continue
        if on_line:
            on_line("$ " + " ".join(command))
        try:
            import subprocess
            done = subprocess.run(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  universal_newlines=True, timeout=1800)
        except Exception as error:
            results.append((program, False, str(error)))
            continue
        programs.forget()
        if programs.find(program.key):
            results.append((program, True, ""))
        else:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            results.append((program, False, detail[-1] if detail else
                            "exit %d" % done.returncode))
    return results


def offer(assume_yes=False, quiet=False):
    state = survey()
    missing = state["optional"]
    manager = state["manager"]
    if not missing:
        if not quiet:
            print("Everything optional is already installed. auto-sort needs "
                  "nothing else.")
        return True
    if not quiet:
        print("auto-sort already works. These would let it do more:")
        for program in missing:
            print("  %-10s %s" % (program.key, program.purpose))
        print()
    if not manager:
        if not quiet:
            print("No supported package manager was found; continuing without them.")
        return True
    if programs.MANAGERS[manager]["needs_root"]:
        if not quiet:
            print("This system needs administrator approval; auto-sort will not run sudo.")
            if programs.immutable_root():
                print(programs.IMMUTABLE_NOTE)
            for program in missing:
                command = describe(program, manager)
                if not command.startswith("("):
                    print("  $ " + command)
        return True
    if not assume_yes:
        if not quiet:
            for program in missing:
                print("  " + describe(program, manager))
        try:
            answer = input("Install these optional tools with %s? [Y/n] " %
                           programs.MANAGERS[manager]["label"]).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return True
        if answer in ("n", "no"):
            return True
    results = install(missing, manager, on_line=None if quiet else print)
    if not quiet:
        for program, ok, detail in results:
            print("  %s %s%s" % ("+" if ok else "!", program.key,
                                  (": " + detail) if detail else ""))
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        # All entries are optional, so a launcher must always be able to start.
        return 0
    if "--optional-check" in argv:
        # The launchers use this to decide whether there is anything worth
        # asking about.  They ignore its non-zero result after offering help.
        return 0 if not (programs.missing()
                         or programs.missing_modules()) else 1
    return 0 if offer(assume_yes=("--yes" in argv or "-y" in argv)) else 1


if __name__ == "__main__":
    sys.exit(main())
