#!/usr/bin/env python3
"""auto-sort — identify, explain and safely file things as they arrive.

The command-line interface provides inspection, one-shot transactional sorts,
undo, and a persistent polling service.  The service deliberately calls the
same planner and mover as ``sort``: there is only one implementation entrusted
with filesystem changes.
"""

from __future__ import annotations

import json
import os
import sys

import bundles
import autostart
import daemon as daemon_module
import evidence
import identify
import ledger as ledger_module
import logpage
import paths
import rules
import sorter

VERSION = "0.4.0"

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


def explain(path, tier=identify.TIER_ALL, as_json=False, rule_set=None):
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
    evaluations = []
    if rule_set is not None:
        source_root = rule_set.source_root_for(path)
        evaluations = rule_set.evaluate(record, source_root)

    if as_json:
        print(json.dumps({
            "path": record.path,
            "facts": dict((name, {"value": fact.value, "source": fact.source,
                                  "confidence": round(fact.confidence, 3),
                                  "agreed_by": fact.corroborated_by})
                          for name, fact in record.items()),
            "notes": record.notes,
            "conflicts": [str(conflict) for conflict in record.conflicts],
            "rules": [{
                "name": result.rule.name,
                "matched": result.matched,
                "reason": result.reason,
                "mode": result.rule.mode,
                "destination": result.destination,
                "trace": [{"comparison": comparison, "matched": matched,
                           "actual": actual}
                          for comparison, matched, actual in result.trace],
            } for result in evaluations],
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
    if evaluations:
        print()
        print("  Rules")
        for result in evaluations:
            marker = "MATCH" if result.matched else "no"
            print("    %-16s %-24s %s" % (
                marker, result.rule.name, result.reason))
            if result.destination:
                print("      -> %s" % result.destination)
    print()
    return 0


def check_rules(filename=None):
    try:
        rule_set = rules.load(filename)
    except rules.RuleError as error:
        print("Rules error: %s" % error, file=sys.stderr)
        return 2
    print("Rules OK: %s" % rule_set.source)
    print("  %d watched folder%s" % (
        len(rule_set.watch.folders),
        "" if len(rule_set.watch.folders) == 1 else "s"))
    print("  %d rule%s" % (len(rule_set.rules),
                            "" if len(rule_set.rules) == 1 else "s"))
    print("  dry run %s" % ("on" if rule_set.settings.dry_run else "off"))
    return 0


def sort_folders(roots, rule_set, state_file=None, dry_run=None,
                 as_json=False):
    reports = []
    worst = 0
    with ledger_module.Ledger(state_file) as journal:
        recovery = sorter.reconcile(journal)
        for root in roots:
            if not os.path.isdir(root):
                print("Not a folder: %s" % root, file=sys.stderr)
                worst = max(worst, 2)
                continue
            protected = (rule_set.source, journal.filename,
                         journal.filename + "-wal", journal.filename + "-shm")
            plan = sorter.build_plan(root, rule_set, exclude=protected)
            result = sorter.execute(plan, rule_set, journal, dry_run)
            reports.append((plan, result))
            if result.failed:
                worst = max(worst, 1)
        if as_json:
            print(json.dumps({
                "recovery": recovery,
                "runs": [_sort_report(plan, result)
                         for plan, result in reports],
            }, indent=2))
            return worst

        for message in recovery:
            print("Recovered: %s" % message)
        for plan, result in reports:
            heading = "DRY RUN" if result.dry_run else "SORTED"
            print("%s  run %d  %s" % (heading, result.run_id, plan.root))
            if result.forced_preview:
                print("  First apply for this folder and rules: preview only.")
                print("  Review this list, then repeat with --apply.")
            for item in plan.items:
                for member in item.members:
                    verb = "copy" if item.operation == "copy" else "move"
                    print("  %-5s %s" % (verb, member.source))
                    print("        -> %s  [%s]" % (member.destination,
                                                   item.rule_name))
            for source, reason in plan.skipped:
                print("  leave %s  (%s)" % (source, reason))
            for message in result.messages:
                print("  ! %s" % message)
            print("  %d item%s planned, %d skipped" % (
                len(plan.items), "" if len(plan.items) == 1 else "s",
                len(plan.skipped)))
    return worst


def _sort_report(plan, result):
    return {
        "run_id": result.run_id,
        "root": plan.root,
        "dry_run": result.dry_run,
        "forced_preview": result.forced_preview,
        "completed": result.completed,
        "failed": result.failed,
        "items": [{
            "rule": item.rule_name,
            "operation": item.operation,
            "members": [{"source": member.source,
                         "destination": member.destination,
                         "size": member.size,
                         "sha256": member.sha256}
                        for member in item.members],
        } for item in plan.items],
        "skipped": [{"source": source, "reason": reason}
                    for source, reason in plan.skipped],
        "messages": result.messages,
    }


def undo_run(run_id="last", state_file=None, dry_run=False, as_json=False):
    try:
        with ledger_module.Ledger(state_file) as journal:
            recovery = sorter.reconcile(journal)
            result = sorter.undo(journal, run_id, dry_run=dry_run)
            rows = journal.moves(result.run_id)
    except (ValueError, OSError, RuntimeError) as error:
        print("Undo error: %s" % error, file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps({
            "run_id": result.run_id,
            "dry_run": result.dry_run,
            "completed": result.completed,
            "failed": result.failed,
            "recovery": recovery,
            "moves": [{"source": row["source"],
                       "destination": row["destination"],
                       "status": row["status"], "error": row["error"]}
                      for row in rows],
            "messages": result.messages,
        }, indent=2))
        return 1 if result.failed else 0
    for message in recovery:
        print("Recovered: %s" % message)
    print("%s  undo run %d" % (
        "DRY RUN" if result.dry_run else "UNDONE", result.run_id))
    for row in rows:
        print("  restore %s" % row["source"])
        print("       -> %s  (%s)" % (row["destination"], row["status"]))
    for message in result.messages:
        print("  ! %s" % message)
    return 1 if result.failed else 0


def watch(rule_path=None, state_file=None, dry_run=None, port=None,
          once=False):
    try:
        rule_set = rules.load(rule_path)
    except rules.RuleError as error:
        print("Rules error: %s" % error, file=sys.stderr)
        return 2
    if not rule_set.watch.folders:
        print("watch needs at least one folder in [watch]", file=sys.stderr)
        return 2
    try:
        with daemon_module.PollingDaemon(
                rule_path, state_file, dry_run, port) as service:
            service.run(once=once)
    except daemon_module.AlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nSorting stopped.")
    return 0


def daemon_control(command, state_file=None, as_json=False):
    if command in ("pause", "resume"):
        with ledger_module.Ledger(state_file) as journal:
            journal.set_paused(command == "pause")
        running = daemon_module.wake(state_file, command)
        if as_json:
            print(json.dumps({"paused": command == "pause",
                              "running": running}, indent=2))
        else:
            print("Sorting %s%s." % (
                "paused" if command == "pause" else "resumed",
                "" if running else " (daemon is not running)"))
        return 0

    if command == "sort-now":
        running = daemon_module.wake(state_file, command)
        if as_json:
            print(json.dumps({"running": running, "woken": running},
                             indent=2))
        elif running:
            print("Sort requested.")
        else:
            print("auto-sort daemon is not running", file=sys.stderr)
        return 0 if running else 1

    if command == "open-log":
        running = daemon_module.wake(state_file, "status")
        if not running:
            print("auto-sort daemon is not running", file=sys.stderr)
            return 1
        if not logpage.open_log(state_file):
            print("could not open the log page", file=sys.stderr)
            return 1
        return 0

    with ledger_module.Ledger(state_file) as journal:
        paused = journal.paused()
        counts = dict((row["status"], row["count"])
                      for row in journal.queue_counts())
        port = int(journal.get_state("daemon_port",
                                     daemon_module.DEFAULT_PORT))
    running = daemon_module.wake(state_file, "status")
    report = {"running": running, "paused": paused, "port": port,
              "queue": counts}
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Daemon: %s on 127.0.0.1:%d" % (
            "running" if running else "not running", port))
        print("Sorting: %s" % ("paused" if paused else "active"))
        print("Queue: %s" % (", ".join(
            "%s %d" % (name, count)
            for name, count in sorted(counts.items())) or "empty"))
    return 0


def manage_autostart(action="status", rule_path=None, as_json=False):
    try:
        if action == "install":
            rule_set = rules.load(rule_path)
            report = autostart.install(rule_set.source)
        elif action == "remove":
            report = autostart.remove()
        elif action == "status":
            report = autostart.status()
        else:
            print("autostart accepts status, install, or remove", file=sys.stderr)
            return 2
    except (autostart.AutostartError, rules.RuleError, OSError) as error:
        print("Autostart error: %s" % error, file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Autostart: %s" % ("installed" if report["installed"]
                                  else "not installed"))
        print("  %s (%s)" % (report["path"], report["platform"]))
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
  auto-sort check-rules [FILE]  validate a rules file without changing anything
  auto-sort sort [FOLDER]       plan a sort; dry-run unless configuration says otherwise
  auto-sort undo [RUN|last]     restore a completed move run
  auto-sort watch               run the persistent polling sorter
  auto-sort pause|resume        persistently pause or resume background sorting
  auto-sort status              show daemon and queue state
  auto-sort open-log            open the live loopback log page
  auto-sort sort-now            wake the daemon for an immediate scan
  auto-sort autostart [ACTION]  show, install, or remove login launch (default status)

Options
  --tier stat|signature|header|all   how far up the ladder to climb (default all)
  --depth N                          how far into subfolders to look (scan, default 3)
  --list N                           print the first N items (scan)
  --rules FILE                       rules to show while explaining a file
  --state FILE                       ledger database (default: platform state folder)
  --apply                            perform a sort after its required preview
  --dry-run                          force a read-only sort or undo preview
  --once                             run one watch cycle and exit
  --port N                           loopback daemon port (default 47653)
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
    rule_path = None
    state_file = None
    dry_run = None
    once = False
    port = None
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
        elif argument == "--rules" and argv:
            rule_path = argv.pop(0)
        elif argument == "--state" and argv:
            state_file = argv.pop(0)
        elif argument == "--apply":
            dry_run = False
        elif argument == "--dry-run":
            dry_run = True
        elif argument == "--once":
            once = True
        elif argument == "--port" and argv:
            try:
                port = int(argv.pop(0))
            except ValueError:
                print("--port needs a number", file=sys.stderr)
                return 2
            if not 0 <= port <= 65535:
                print("--port must be between 0 and 65535", file=sys.stderr)
                return 2
        elif argument.startswith("-"):
            print("Unknown option: %s" % argument, file=sys.stderr)
            return 2
        else:
            targets.append(argument)

    if command == "explain":
        if not targets:
            print("explain needs a path", file=sys.stderr)
            return 2
        rule_set = None
        candidate = paths.rules_file(rule_path)
        if rule_path or os.path.exists(candidate):
            try:
                rule_set = rules.load(rule_path)
            except rules.RuleError as error:
                print("Rules error: %s" % error, file=sys.stderr)
                return 2
        worst = 0
        for target in targets:
            worst = max(worst, explain(target, tier, as_json, rule_set))
        return worst
    if command == "scan":
        return scan(targets[0] if targets else ".", tier, depth, show,
                    as_json)
    if command == "check-rules":
        if len(targets) > 1:
            print("check-rules takes at most one file", file=sys.stderr)
            return 2
        return check_rules(targets[0] if targets else rule_path)
    if command == "sort":
        try:
            rule_set = rules.load(rule_path)
        except rules.RuleError as error:
            print("Rules error: %s" % error, file=sys.stderr)
            return 2
        roots = targets or rule_set.watch.folders
        if not roots:
            print("sort needs a folder or [watch] folders", file=sys.stderr)
            return 2
        return sort_folders(roots, rule_set, state_file, dry_run, as_json)
    if command == "undo":
        if len(targets) > 1:
            print("undo takes one run number or 'last'", file=sys.stderr)
            return 2
        return undo_run(targets[0] if targets else "last", state_file,
                        dry_run is True, as_json)
    if command == "watch":
        if targets:
            print("watch takes no paths; configure [watch] folders",
                  file=sys.stderr)
            return 2
        return watch(rule_path, state_file, dry_run, port, once)
    if command in ("pause", "resume", "status", "open-log", "sort-now"):
        if targets:
            print("%s takes no arguments" % command, file=sys.stderr)
            return 2
        return daemon_control(command, state_file, as_json)
    if command == "autostart":
        if len(targets) > 1:
            print("autostart takes one action", file=sys.stderr)
            return 2
        return manage_autostart(targets[0] if targets else "status",
                                rule_path, as_json)
    print("Unknown command: %s\n" % command, file=sys.stderr)
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
