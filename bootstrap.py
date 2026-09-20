#!/usr/bin/env python3
"""Offer installation of optional auto-sort enrichment programs.

No program in this module is required.  It is deliberately only a capability
upgrade: declining, lacking a package manager, or a failed install never stops
the sorter itself from running.
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
            print("Optional enrichment programs are already available.")
        return True
    if not quiet:
        print("auto-sort works without these optional programs:")
        for program in missing:
            print("  %-10s %s" % (program.key, program.purpose))
    if not manager:
        if not quiet:
            print("No supported package manager was found; continuing without them.")
        return True
    if programs.MANAGERS[manager]["needs_root"]:
        if not quiet:
            print("This system needs administrator approval; auto-sort will not run sudo.")
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
        return 0 if not programs.missing() else 1
    return 0 if offer(assume_yes=("--yes" in argv or "-y" in argv)) else 1


if __name__ == "__main__":
    sys.exit(main())
