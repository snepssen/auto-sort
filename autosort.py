#!/usr/bin/env python3
"""auto-sort — work out what a file is, and eventually put it somewhere.

This milestone does the working out and none of the putting. Two commands:

    auto-sort explain PATH      every fact, its source, and how sure it is
    auto-sort scan FOLDER       what is in there, grouped and counted

`explain` is the one that matters. Every argument anybody will ever have with
this tool is about a single file that went somewhere surprising, and the whole
answer has to be one command and one screen. It ships now, with the engine,
rather than later with the user interface, because a classifier whose
reasoning cannot be read is a classifier nobody should let near their disk.
"""

from __future__ import annotations

import json
import os
import sys

import bundles
import evidence
import identify

VERSION = "0.1.0"

_TIERS = {"stat": identify.TIER_STAT, "signature": identify.TIER_SIGNATURE,
          "header": identify.TIER_HEADER, "all": identify.TIER_ALL}


def _size(count):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return "%.0f %s" % (count, unit) if unit == "B" \
                else "%.1f %s" % (count, unit)
        count /= 1024.0
    return str(count)


def _colourless(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def explain(path, tier=identify.TIER_ALL, as_json=False):
    if not os.path.exists(path):
        print("No such file: %s" % path, file=sys.stderr)
        return 1
    directory = os.path.dirname(os.path.abspath(path)) or "."
    target = None
    for item in bundles.group(directory):
        if os.path.abspath(path) in [os.path.abspath(member)
                                     for member in item.members]:
            target = item
            break
    if target is None:
        target = bundles.Item(os.path.abspath(path))

    record = identify.identify(target, tier=tier)

    if as_json:
        print(json.dumps({
            "path": record.path,
            "facts": dict((name, {"value": fact.value, "source": fact.source,
                                  "confidence": round(fact.confidence, 3),
                                  "agreed_by": fact.corroborated_by})
                          for name, fact in record.items()),
            "notes": record.notes,
            "conflicts": [str(conflict) for conflict in record.conflicts],
        }, indent=2, default=str))
        return 0

    print()
    print("  %s" % record.value("name", os.path.basename(path)))
    print("  %s" % record.value("path"))
    details = []
    if record.value("size") is not None:
        details.append(_size(record.value("size")))
    if record.value("modified"):
        details.append("modified " + record.value("modified"))
    if len(target.members) > 1:
        details.append("%d files in this item" % len(target.members))
    if details:
        print("  %s" % "   ".join(details))
    if len(target.members) > 1:
        for member in target.members[1:]:
            print("      + %s" % os.path.basename(member))

    if record.readers:
        print()
        print("  Read by")
        for name, detail in record.readers:
            print("    %-16s %s" % (name, detail))

    print()
    print("  What is known")
    for name, fact in record.items():
        if name in ("path", "dir"):
            continue
        agreed = ""
        if fact.corroborated_by:
            agreed = "  agreed by " + ", ".join(fact.corroborated_by)
        print("    %-20s %-26s %-8s %s%s" % (
            name, _colourless(str(fact.value), 26),
            evidence.band(fact.confidence), fact.source, agreed))

    if record.conflicts:
        print()
        print("  Disagreements")
        for conflict in record.conflicts:
            print("    %s" % conflict)

    if record.notes:
        print()
        print("  Notes")
        for note in record.notes:
            print("    - %s" % note)
    print()
    return 0


def scan(root, tier=identify.TIER_ALL, depth=3, show=0, as_json=False):
    if not os.path.isdir(root):
        print("Not a folder: %s" % root, file=sys.stderr)
        return 1

    counts = {}
    labels = {}
    formats = {}
    rows = []
    total = bundled = unknown = 0

    for item in bundles.walk(root, max_depth=depth):
        record = identify.identify(item, tier=tier)
        total += 1
        kind = record.value("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
        fmt = record.value("format", "?")
        formats[fmt] = formats.get(fmt, 0) + 1
        label = record.value("looks_like") or record.value("capture")
        if label:
            labels[label] = labels.get(label, 0) + 1
        if len(item.members) > 1:
            bundled += 1
        if kind == "unknown" or record.confidence("kind") < evidence.LIKELY:
            unknown += 1
        if show or as_json:
            rows.append((record, item))

    if as_json:
        print(json.dumps([{
            "path": record.value("path"), "kind": record.value("kind"),
            "format": record.value("format"),
            "looks_like": record.value("looks_like"),
            "members": len(item.members),
            "confidence": round(record.confidence("kind"), 3),
        } for record, item in rows], indent=2, default=str))
        return 0

    print()
    print("  %s" % os.path.abspath(root))
    print("  %d items, %d of them bundles, %d not confidently identified"
          % (total, bundled, unknown))
    print()
    print("  By kind")
    for kind, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        share = 100.0 * count / total if total else 0
        print("    %-14s %6d  %4.0f%%  %s" % (kind, count, share,
                                              "▌" * int(share / 3)))
    if labels:
        print()
        print("  What they look like")
        for label, count in sorted(labels.items(), key=lambda p: -p[1])[:14]:
            print("    %-24s %6d" % (label, count))
    if show:
        print()
        print("  Items")
        for record, item in rows[:show]:
            extra = ""
            if len(item.members) > 1:
                extra = "  +%d" % (len(item.members) - 1)
            print("    %-46s %-10s %-16s%s" % (
                _colourless(record.value("name", "?"), 46),
                record.value("kind", "?"),
                record.value("looks_like") or record.value("capture") or "",
                extra))
    print()
    return 0


USAGE = """auto-sort %s

  auto-sort explain PATH        every fact about one file, and where it came from
  auto-sort scan FOLDER         what is in a folder, grouped into items

Options
  --tier stat|signature|header|all   how far up the ladder to climb (default all)
  --depth N                          how far into subfolders to look (scan, default 3)
  --list N                           print the first N items (scan)
  --json                             machine-readable output
""" % VERSION


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print(VERSION)
        return 0

    command = argv.pop(0)
    tier = identify.TIER_ALL
    depth = 3
    show = 0
    as_json = False
    targets = []
    while argv:
        argument = argv.pop(0)
        if argument == "--tier" and argv:
            tier = _TIERS.get(argv.pop(0), identify.TIER_ALL)
        elif argument == "--depth" and argv:
            depth = int(argv.pop(0))
        elif argument == "--list" and argv:
            show = int(argv.pop(0))
        elif argument == "--json":
            as_json = True
        elif argument.startswith("-"):
            print("Unknown option: %s" % argument, file=sys.stderr)
            return 2
        else:
            targets.append(argument)

    if command == "explain":
        if not targets:
            print("explain needs a path", file=sys.stderr)
            return 2
        worst = 0
        for target in targets:
            worst = max(worst, explain(target, tier, as_json))
        return worst
    if command == "scan":
        return scan(targets[0] if targets else ".", tier, depth, show,
                    as_json)
    print("Unknown command: %s\n" % command, file=sys.stderr)
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
