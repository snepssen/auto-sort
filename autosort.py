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
import corrections as corrections_module
import autostart
import daemon as daemon_module
import evidence
import identify
import ledger as ledger_module
import logpage
import paths
import propose as propose_module
import regroup as regroup_module
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
            plan = sorter.build_plan(root, rule_set, exclude=protected,
                                     journal=journal)
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


def start(rule_path=None, state=None, port=None, once=False):
    """What double-clicking the launcher does: set up if needed, then run.

    Somebody who has just downloaded this should not have to learn a command
    line to use it, and should not be shown a usage message for their
    trouble. So: write a starter rules file if there is not one, say where it
    is and what it will do, and then start watching. Everything after that is
    the icon and the log page.
    """
    target = paths.rules_file(rule_path)
    fresh = not os.path.exists(target)
    if fresh:
        print()
        print("  First run. Writing a rules file you can edit later:")
        if init(target) != 0:
            return 1

    try:
        rule_set = rules.load(target)
    except rules.RuleError as error:
        print("Rules error: %s" % error, file=sys.stderr)
        return 1

    print()
    print("  Watching")
    for folder in rule_set.watch.folders:
        print("    %s" % propose_module.userdirs.short(folder))
    if rule_set.settings.dry_run:
        print()
        print("  Dry run is on, so nothing will actually move. When you are")
        print("  happy with what the log shows, set dry_run = no in")
        print("  %s" % propose_module.userdirs.short(target))
    print()
    return watch(target, state, None, port, once)


def regroup(root=None, rule_path=None, state=None, apply_changes=False,
            dry_run=None, as_json=False):
    """Move already-filed files into structure that has since become visible.

    The point of the whole exercise: a folder teaches auto-sort gradually, and
    the files that arrived before it had learnt anything were put in a holding
    folder because there was nothing better to do with them. Without this they
    stay there while only new arrivals benefit, and the only remedy is
    dragging them back into Downloads, which is absurd.
    """
    try:
        rule_set = rules.load(rule_path)
    except rules.RuleError as error:
        print("Rules error: %s" % error, file=sys.stderr)
        return 1

    with ledger_module.Ledger(state) as journal:
        plans = regroup_module.build(journal, rule_set,
                                     os.path.abspath(root) if root else None)
        total, by_rule, by_destination = regroup_module.summarise(plans)

        if as_json:
            print(json.dumps({
                "promotions": total,
                "by_rule": [{"rule": name, "files": count}
                            for name, count in by_rule],
                "by_destination": [{"folder": folder, "files": count}
                                   for folder, count in by_destination],
            }, indent=2, default=str))
            return 0

        holding = [rule.name for rule in rule_set.rules if rule.holding]
        print()
        if not holding:
            print("  No rule is marked `holding = yes`, so nothing is")
            print("  waiting to be promoted. Catch-all rules written by")
            print("  `auto-sort propose` carry that mark.")
            print()
            return 0
        if not total:
            print("  Nothing to regroup: every file in a holding folder is")
            print("  still there because nothing better has been learnt yet.")
            print()
            return 0

        print("  %s file%s can move out of a holding folder into structure"
              % ("{:,}".format(total), "" if total == 1 else "s"))
        print("  that has become visible since they were filed.")
        print()
        print("  Into")
        for folder, count in by_destination:
            shown = propose_module.userdirs.short(folder)
            if len(shown) > 52:
                # Keep the end: the folder name is the interesting part, and
                # fifty characters of shared prefix tells nobody anything.
                shown = "..." + shown[-49:]
            print("    %-52s %s" % (shown, "{:,}".format(count)))
        print()
        print("  By rule")
        for name, count in by_rule:
            print("    %-34s %s" % (name[:34], "{:,}".format(count)))

        if not apply_changes:
            print()
            print("  Nothing has moved. Add --apply to do it.")
            print()
            return 0

        print()
        for plan_root, plan in plans:
            result = sorter.execute(plan, rule_set, journal,
                                    dry_run=False if dry_run is None
                                    else dry_run)
            label = "would move" if result.dry_run else "moved"
            print("  %s %d item%s from %s"
                  % (label, result.completed or len(plan.items),
                     "" if len(plan.items) == 1 else "s",
                     propose_module.userdirs.short(plan_root)))
            if result.forced_preview:
                print("  ! first regroup for this folder was a preview; "
                      "run it again to move")
            for message in result.messages:
                print("    %s" % message)
        print()
    return 0


def corrections(root=None, state=None, out=None, as_json=False):
    """Notice what was moved after auto-sort placed it, and what that implies.

    The most valuable signal the tool has, and the cheapest: it is simply the
    difference between the ledger and the disk. Nothing is applied -- what
    comes out is a candidate rule with its count, for somebody to accept or
    throw away.
    """
    roots = [os.path.abspath(root)] if root else []
    with ledger_module.Ledger(state) as journal:
        found = corrections_module.detect(journal, roots,
                                          os.path.abspath(root) if root
                                          else None)
        rows = journal.corrections("moved")
        among = corrections_module.population(
            journal, os.path.abspath(root) if root else None)
        preferences = corrections_module.induce(rows, among)
        overridden = corrections_module.overridden_rules(rows)

        if as_json:
            print(json.dumps({
                "checked": len(found),
                "moved": sum(1 for _r, outcome, _p in found
                             if outcome == "moved"),
                "missing": sum(1 for _r, outcome, _p in found
                               if outcome == "missing"),
                "preferences": [{"fact": p.fact, "value": p.value,
                                 "folder": p.folder, "support": p.support,
                                 "precision": round(p.precision, 3)}
                                for p in preferences],
                "overridden": [{"rule": name, "times": count}
                               for name, count in overridden],
            }, indent=2, default=str))
            return 0

        moved = [item for item in found if item[1] == "moved"]
        missing = [item for item in found if item[1] == "missing"]
        print()
        if not rows and not found:
            print("  Nothing has been moved since auto-sort placed it.")
            print("  (corrections are how it learns what you actually want)")
            print()
            return 0
        if found:
            print("  Since the last check: %d placement%s changed"
                  % (len(found), "" if len(found) == 1 else "s"))
            print("    %d found somewhere else, %d gone"
                  % (len(moved), len(missing)))
        print("  %d correction%s recorded in total"
              % (len(rows), "" if len(rows) == 1 else "s"))

        if overridden:
            print()
            print("  Rules you overrode")
            for name, count in overridden[:10]:
                print("    %-34s %d time%s" % (name[:34], count,
                                               "" if count == 1 else "s"))

        print()
        if preferences:
            print("  What that suggests")
            for preference in preferences:
                print("    %s = %s  ->  %s"
                      % (preference.fact, preference.value,
                         propose_module._short(preference.folder)))
                print("      %d file%s, %.0f%% of them"
                      % (preference.support,
                         "" if preference.support == 1 else "s",
                         preference.precision * 100))
        else:
            print("  Not enough agreement yet to suggest a rule.")
            print("  %d more consistent correction%s would do it."
                  % (corrections_module.MIN_SUPPORT,
                     "s" if corrections_module.MIN_SUPPORT != 1 else ""))

        if out and preferences:
            try:
                with open(out, "w", encoding="utf-8") as handle:
                    handle.write("; Rules learnt from files you moved after\n"
                                 "; auto-sort placed them. Nothing here has\n"
                                 "; been applied.\n\n")
                    for preference in preferences:
                        handle.write(preference.rule() + "\n\n")
            except OSError as error:
                print("Could not write %s: %s" % (out, error), file=sys.stderr)
                return 1
            print()
            print("  Wrote %s -- read it, then paste what you agree with"
                  % out)
            print("  into your rules file.")
        print()
    return 0


def propose(root=".", tier=identify.TIER_HEADER, depth=3, out=None,
            limit=None, as_json=False):
    """Survey a folder and write the rules it turns out to need.

    The report is the point as much as the file is. It says what was found,
    what was proposed, what was considered and rejected and why, and how much
    of the folder would still be left alone -- because a proposal that sorts
    an eighth of a folder and looks tidy is the failure mode here.
    """
    if not os.path.isdir(root):
        print("Not a folder: %s" % root, file=sys.stderr)
        return 1

    def progress(count):
        sys.stderr.write("\r  surveyed %s items..." % "{:,}".format(count))
        sys.stderr.flush()

    found = propose_module.survey(root, tier=tier, depth=depth, limit=limit,
                                  on_progress=None if as_json else progress)
    if not as_json:
        sys.stderr.write("\r" + " " * 40 + "\r")
    proposals = propose_module.assess(found)
    body = propose_module.render(found, proposals)

    if as_json:
        print(json.dumps({
            "root": found.root, "items": found.items, "files": found.files,
            "bytes": found.bytes, "opaque": found.opaque,
            "kinds": dict(found.kinds), "sites": dict(found.sites),
            "proposed": [{"facet": p.key, "covered": p.covered,
                          "groups": p.groups, "median": p.median}
                         for p in proposals if p.accepted],
            "rejected": [{"facet": p.key, "reason": p.reason}
                         for p in proposals if not p.accepted],
        }, indent=2, default=str))
        return 0

    accepted = [p for p in proposals if p.accepted]
    print()
    print("  %s" % found.root)
    print("  %s items, %s files, %s"
          % ("{:,}".format(found.items), "{:,}".format(found.files),
             propose_module._size(found.bytes)))
    print()
    print("  What is in there")
    for kind, count in found.kinds.most_common(10):
        share = 100.0 * count / found.items if found.items else 0
        print("    %-14s %7s  %4.0f%%  %s"
              % (kind, "{:,}".format(count), share, "▌" * int(share / 4)))
    if found.sites:
        print()
        print("  Downloaded from")
        for site, count in found.sites.most_common(8):
            print("    %-16s %7s" % (site, "{:,}".format(count)))

    if found.conventions:
        print()
        print("  Naming conventions learnt from the filenames")
        for convention in found.conventions:
            source = propose_module.convention_source(found, convention)
            print("    %-24s %4d files -> %3d folders, %.0f%% shared%s"
                  % (convention.describe()[:24], convention.count,
                     convention.groups, convention.concentration * 100,
                     "   (all from %s)" % source if source else ""))
            values = convention.fields[convention.category][2]
            print("      %s" % ", ".join(
                name for name, _count in values.most_common(5)))

    print()
    print("  Structure this folder suggests")
    if not accepted:
        print("    nothing grouped well enough to propose")
    for proposal in sorted(accepted, key=lambda p: p.facet.precedence):
        print("    %-18s %6s items -> %4d folder%s, %2.0f%% sharing one"
              % (proposal.key, "{:,}".format(proposal.covered),
                 proposal.groups, " " if proposal.groups == 1 else "s",
                 proposal.share * 100))
    print()
    if propose_module.is_funnel(found.root):
        print("  Where this folder empties to")
    else:
        print("  Where this folder sorts to (in place: not the home volume)")
    destinations = {}
    for kind, count in found.kinds.most_common():
        if not kind:
            continue
        target = propose_module.userdirs.short(
            propose_module.catch_all_root(found, kind))
        destinations.setdefault(target, []).append((kind, count))
    for target, kinds in sorted(destinations.items(),
                                key=lambda pair: -sum(c for _k, c in
                                                      pair[1])):
        summary = ", ".join("%s %s" % (kind, "{:,}".format(count))
                            for kind, count in kinds[:4])
        print("    %-28s %s" % (target, summary))
    print("    (anything a rule above did not claim; nothing stays behind)")

    rejected = [p for p in proposals if not p.accepted]
    if rejected:
        print()
        print("  Considered, not proposed")
        for proposal in rejected:
            print("    %-18s %s" % (proposal.key, proposal.reason))
    if found.opaque:
        print()
        placed = found.opaque - found.opaque_unplaceable
        print("  %s items are named after a checksum or a site id."
              % "{:,}".format(found.opaque))
        if placed:
            print("    %s of them still name the site they came from."
                  % "{:,}".format(placed))
        if found.opaque_unplaceable:
            print("    %s have no handle at all -- that is where filename"
                  % "{:,}".format(found.opaque_unplaceable))
            print("    analysis ends and reading the content would begin.")

    if out:
        try:
            with open(out, "w", encoding="utf-8") as handle:
                handle.write(body)
        except OSError as error:
            print("Could not write %s: %s" % (out, error), file=sys.stderr)
            return 1
        print()
        print("  Wrote %s" % out)
        print("    auto-sort sort %s --rules %s"
              % (propose_module._short(found.root), out))
    else:
        print()
        print("  (pass --out FILE to write these as a rules file)")
    print()
    return 0


def init(destination=None):
    """Write a starter rules file, and never over one that already exists.

    The starter is a real file in the repository rather than a string in the
    code, so that it is reviewed, tested against fixtures like everything
    else, and can be read before it is installed.
    """
    target = os.path.abspath(destination) if destination \
        else paths.rules_file()
    example = paths.example_rules_file()
    if os.path.exists(target):
        print("There is already a rules file at %s" % target)
        print("Nothing was changed. Check it with: auto-sort check-rules")
        return 1
    if not os.path.exists(example):
        print("The starter rules file is missing from this checkout: %s"
              % example, file=sys.stderr)
        return 1
    try:
        with open(example, "r", encoding="utf-8") as source:
            body = source.read()
        paths.ensure(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(body)
    except OSError as error:
        print("Could not write %s: %s" % (target, error), file=sys.stderr)
        return 1

    try:
        rule_set = rules.load(target)
        summary = "%d rules, watching %d folder%s" % (
            len(rule_set.rules), len(rule_set.watch.folders),
            "" if len(rule_set.watch.folders) == 1 else "s")
    except rules.RuleError as error:
        print("Wrote %s, but it does not parse: %s" % (target, error),
              file=sys.stderr)
        return 1

    print("Wrote %s" % target)
    print("  %s, and dry run is on." % summary)
    print()
    print("Read it, then:")
    print("  auto-sort sort ~/Downloads     see what it would do")
    print("  auto-sort explain FILE         ask why one file goes where it does")
    return 0


USAGE = """auto-sort %s

  auto-sort start               set up if needed, then run in the background
  auto-sort explain PATH        every fact about one file, and where it came from
  auto-sort scan FOLDER         what is in a folder, grouped into items
  auto-sort init                write a starter rules file if there is not one
  auto-sort propose [FOLDER]    survey a folder and derive the rules it needs
  auto-sort corrections [FOLDER] what you moved afterwards, and what it implies
  auto-sort regroup [FOLDER]    re-file what was filed before the pattern showed
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
  --out FILE                         write proposed rules to FILE (propose)
  --limit N                          stop surveying after N items (propose)
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
    out_file = None
    limit = None
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
        elif argument == "--out" and argv:
            out_file = argv.pop(0)
        elif argument == "--limit" and argv:
            limit = int(argv.pop(0))
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
    if command == "start":
        return start(rule_path, state_file, port, once)
    if command == "regroup":
        return regroup(targets[0] if targets else None, rule_path,
                       state_file, dry_run is False, dry_run, as_json)
    if command == "corrections":
        return corrections(targets[0] if targets else None, state_file,
                           out_file, as_json)
    if command == "propose":
        return propose(targets[0] if targets else ".", tier, depth,
                       out_file, limit, as_json)
    if command == "init":
        return init(targets[0] if targets else None)
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
