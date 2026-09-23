"""Plan, execute and undo one-shot sorting runs.

Planning is deliberately separate from execution.  The complete source and
destination set is known, collision-checked and hashed before the first path is
touched.  Execution then journals every member before moving it.  A failed
bundle move is rolled back member by member; the ledger retains both the
failure and the rollback instead of pretending nothing happened.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import time

import bundles
import costs
import duplicates
import identify
import mirror
import mover
import paths
from readers import ocr


BUILTIN_IGNORE = (
    "*.part", "*.crdownload", "*.download", "*.tmp", "~$*",
    ".DS_Store", "Thumbs.db", ".autosortignore",
)
PLAN_VERSION = 1


class PlannedMember(object):
    def __init__(self, source, destination, size, sha256):
        self.source = source
        self.destination = destination
        self.size = size
        self.sha256 = sha256
        self.ledger_id = None


class PlannedItem(object):
    def __init__(self, item, rule_name, operation, members, facts=None,
                 holding=False):
        self.item = item
        self.rule_name = rule_name
        self.operation = operation
        self.members = members
        self.facts = facts or {}
        # Whether the rule that chose this destination called it provisional.
        # Carried into the ledger so that a later regroup can find what is
        # waiting without depending on a rule still having the same name.
        self.holding = holding


class Plan(object):
    def __init__(self, root, items=None, skipped=None):
        self.root = os.path.abspath(root)
        self.items = items or []
        self.skipped = skipped or []

    @property
    def members(self):
        return [member for item in self.items for member in item.members]


class RunResult(object):
    def __init__(self, run_id, dry_run, completed=0, failed=0, skipped=0,
                 forced_preview=False, messages=None):
        self.run_id = run_id
        self.dry_run = dry_run
        self.completed = completed
        self.failed = failed
        self.skipped = skipped
        self.forced_preview = forced_preview
        self.messages = messages or []


def rules_hash(rule_set):
    digest = hashlib.sha256()
    digest.update(("auto-sort-plan-%d\0" % PLAN_VERSION).encode("ascii"))
    if getattr(rule_set, "source_hash", None):
        # This is the content that was actually parsed, not a second read of a
        # file that could have changed between planning and execution.
        digest.update(rule_set.source_hash.encode("ascii"))
    else:
        for rule in rule_set.rules:
            digest.update(rule.name.encode("utf-8"))
            digest.update(rule.when_text.encode("utf-8"))
            digest.update(str(rule.into).encode("utf-8"))
            digest.update(str(rule.rename).encode("utf-8"))
    return digest.hexdigest()


def _read(reader, item, ocr_mode="auto", tools="auto"):
    """Facts about one item, from wherever it is safe to read them.

    Without a `reader` this is a plain call, which is what the one-shot
    commands and the tests do. With one, the reading happens in a process
    that can be killed if it stops answering -- the difference between a
    file nobody can read and a program nobody can quit.
    """
    if reader is None:
        # No supervisor: the tiers do the gating, and `off` means the
        # programs are not run at all.
        tier = identify.TIER_HEADER if tools == "off" else identify.TIER_ALL
        return identify.identify(item, tier=tier), ""
    return reader.read(item, ocr=ocr_mode)


def _note_cost(journal, path, watch, note="", record=None):
    """File away a reading, but only the ones worth a person's attention.

    Silent about everything ordinary, and silent about everything if there
    is no ledger to write to. It must also never be the reason a sort
    fails: a measurement is a courtesy and the file is the job.
    """
    # A page that had to be photographed and read back is slow because that
    # is what it costs, not because anything went wrong. It earns a row only
    # if it was slow even for that.
    read_by_ocr = record is not None and record.value("read_by") == "ocr"
    if journal is None or not watch.notable(
            costs.OCR_SLOW_SECONDS if read_by_ocr else None):
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    reason = watch.reason()
    if note:
        reason = "%s (%s)" % (reason, note)
    try:
        journal.record_cost(path, os.path.basename(path), size,
                            watch.seconds, watch.growth, watch.peak,
                            reason)
    except Exception:                        # noqa: BLE001
        pass


def build_plan(root, rule_set, exclude=(), items=None, journal=None,
               reader=None, progress=None):
    """Plan a sort. `journal` lets it recognise files it has filed before.

    The ledger is optional here on purpose: a plan is still a plan without
    it, just one that cannot tell a second copy from a first.
    """
    root = os.path.abspath(root)
    # Both processes have to agree about this, so it is set here for the one
    # doing the reading and sent across with every request for the other.
    ocr_mode = getattr(rule_set.settings, "ocr", "auto")
    ocr.configure(ocr_mode)
    tools_mode = getattr(rule_set.settings, "tools", "auto")
    if reader is not None and hasattr(reader, "helpers"):
        reader.helpers.mode = tools_mode
        # So that an expensive reading is paid for once. The first run
        # against a new folder is forced to be a preview and the next one
        # reads it all again; without this every scan is read twice before
        # anything has even gone wrong.
        if reader.helpers.journal is None:
            reader.helpers.journal = journal
    seen = duplicates.Index(journal)
    planned = []
    skipped = []
    reserved = set()
    excluded = set(_collision_key(path) for path in exclude if path)
    ignore_patterns = BUILTIN_IGNORE + tuple(rule_set.watch.ignore)

    # Materialise before executing anything.  Otherwise os.walk can discover a
    # destination directory created halfway through its own traversal.
    supplied_items = items is not None
    items = list(bundles.walk(root, max_depth=rule_set.watch.depth)) \
        if items is None else list(items)
    seen_count = 0
    for item in items:
        # Before the reading rather than after it: the name on screen should
        # be the file being worked on, not the last one that finished.
        seen_count += 1
        if progress is not None:
            progress(seen_count, len(items), os.path.basename(item.primary))
        if any(_collision_key(member) in excluded for member in item.members):
            skipped.append((item.primary, "auto-sort's active configuration "
                            "or state file"))
            continue
        # A persistent watcher already proved explicit queue items stable over
        # multiple observations.  The one-shot scanner still uses wall-clock
        # age as its best available approximation.
        settle_seconds = 0 if supplied_items \
            else rule_set.settings.settle_seconds
        reason = _skip_reason(item, root, ignore_patterns, settle_seconds)
        if reason:
            skipped.append((item.primary, reason))
            continue

        # Timed, because identification is the stage that reads bytes
        # somebody else wrote and is therefore the stage that hangs. The
        # `with` wraps the `try` rather than the other way round so that a
        # file which failed is still measured: a file that took four
        # minutes and then raised is the single most interesting row this
        # table can hold.
        watch = costs.Watch()
        try:
            with watch:
                record, failure = _read(reader, item, ocr_mode, tools_mode)
        except (OSError, ValueError) as error:
            record, failure = None, "identification failed: %s" % error
        # The reading happened in another process, so the memory this one
        # grew by is not the file's cost. The worker measured itself.
        watch.adopt(getattr(reader, "last_reading", None))
        _note_cost(journal, item.primary, watch, failure, record)
        if failure:
            skipped.append((item.primary, failure))
            continue
        if record.value("dataless"):
            skipped.append((item.primary,
                            "cloud placeholder is not present on this device"))
            continue
        # Identical to something already filed, or to something earlier in
        # this same plan. The second case matters as much as the first:
        # copies usually arrive together, because the copy was the point.
        if len(item.members) == 1 and not item.is_dir:
            duplicates.annotate(record, seen, item.primary,
                                record.value("size"))

        decision, near_miss = rule_set.decide(record, source_root=root)
        if decision is None:
            if rule_set.settings.unsorted != "gather":
                skipped.append((item.primary, _no_match_reason(near_miss)))
                continue
            destination_dir = _unsorted_directory(
                rule_set.settings.unsorted_into, root)
            primary_destination = os.path.join(
                destination_dir, paths.sanitise(os.path.basename(item.primary)))
            rule_name = "[unsorted]"
            operation = "move"
            renamed = False
            holding = True
        elif decision.rule.mode == "leave":
            skipped.append((item.primary,
                            "rule %r says leave" % decision.rule.name))
            continue
        else:
            primary_destination = decision.destination
            rule_name = decision.rule.name
            operation = decision.rule.mode
            holding = decision.rule.holding
            renamed = decision.rule.rename is not None

        # Spell the destination folder the way the disk already spells it,
        # so that `Firefox` and `firefox` -- the same program named by two
        # different people a decade apart -- do not become two folders on a
        # case-sensitive filesystem.
        _folder, _base = os.path.split(primary_destination)
        primary_destination = os.path.join(paths.settled(_folder), _base)

        try:
            destinations = _member_destinations(
                item, primary_destination, renamed)
        except ValueError as error:
            skipped.append((item.primary, str(error)))
            continue

        sources = [os.path.abspath(member) for member in item.members]
        if all(os.path.normcase(source) == os.path.normcase(destination)
               for source, destination in zip(sources, destinations)):
            skipped.append((item.primary, "already at its destination"))
            continue
        if item.is_dir and any(paths.inside(item.primary, destination)
                               for destination in destinations):
            skipped.append((item.primary,
                            "destination would be inside the source package"))
            continue

        destinations, collision = _resolve_collisions(
            item, destinations, sources, reserved,
            rule_set.settings.on_collision)
        if collision:
            skipped.append((item.primary, collision))
            continue

        copy_problem = None
        for source, destination in zip(sources, destinations):
            if operation == "copy" or not mover.same_volume(source, destination):
                copy_problem = mover.unsafe_to_copy(source)
                if copy_problem:
                    break
        if copy_problem:
            skipped.append((item.primary, "cannot preserve storage identity: %s"
                            % copy_problem))
            continue

        members = []
        try:
            for source, destination in zip(sources, destinations):
                members.append(PlannedMember(
                    source, destination, mover.size_path(source),
                    mover.hash_path(source)))
        except (OSError, mover.MoveError) as error:
            skipped.append((item.primary, "could not fingerprint item: %s"
                            % error))
            continue
        reserved.update(_collision_key(path) for path in destinations)
        for member in members:
            # Anything planned is about to exist at its destination, so a
            # later copy in this same run is a duplicate of it.
            seen.remember(member.size, member.sha256, member.destination,
                          must_exist=False)
        planned.append(PlannedItem(item, rule_name, operation, members,
                                   record.as_dict(), holding))
    return Plan(root, planned, skipped)


def execute(plan, rule_set, ledger, dry_run=None):
    fingerprint = rules_hash(rule_set)
    requested_dry_run = rule_set.settings.dry_run \
        if dry_run is None else bool(dry_run)
    forced_preview = (not requested_dry_run
                      and not ledger.has_preview(plan.root, fingerprint))
    dry_run = requested_dry_run or forced_preview
    run_id = ledger.start_run("sort", plan.root, fingerprint, dry_run)
    messages = []

    for item_number, planned_item in enumerate(plan.items, 1):
        for member_number, member in enumerate(planned_item.members, 1):
            member.ledger_id = ledger.add_move(
                run_id, item_number, member_number, planned_item.operation,
                planned_item.rule_name, member.source, member.destination,
                member.size, member.sha256,
                status="dry-run" if dry_run else "planned",
                facts=planned_item.facts, holding=planned_item.holding)

    if dry_run:
        ledger.record_preview(plan.root, fingerprint, run_id)
        summary = "%d items, %d members, %d skipped" % (
            len(plan.items), len(plan.members), len(plan.skipped))
        ledger.finish_run(run_id, "dry-run", summary)
        if forced_preview:
            messages.append("first apply for this folder and rules was forced "
                            "to a dry run")
        return RunResult(run_id, True, skipped=len(plan.skipped),
                         forced_preview=forced_preview, messages=messages)

    completed = failed = 0
    created_directories = set()
    mirror_root = ledger.get_state("mirror_root") \
        if ledger.get_state("mirror_enabled") == "yes" else ""
    for planned_item in plan.items:
        moved = []
        item_failed = False
        for member_index, member in enumerate(planned_item.members):
            # Asked before the transfer, while the answer is still "these do
            # not exist". Afterwards there is no way to tell what this run
            # made from what was already there.
            created_directories.update(paths.missing_ancestors(
                os.path.dirname(member.destination)))
            status = "copying" if planned_item.operation == "copy" else "moving"
            ledger.update_move(member.ledger_id, status)
            try:
                result = mover.transfer(
                    member.source, member.destination,
                    operation=planned_item.operation,
                    expected_hash=member.sha256,
                    preserve_dates=rule_set.settings.preserve_dates)
            except (OSError, mover.MoveError) as error:
                ledger.update_move(member.ledger_id, "failed", str(error))
                messages.append("%s: %s" % (member.source, error))
                failed += 1
                item_failed = True
                for remaining in planned_item.members[member_index + 1:]:
                    ledger.update_move(remaining.ledger_id, "cancelled",
                                       "another member of the bundle failed")
                break
            final_status = "copied" if result.copied \
                and not result.source_removed else "done"
            ledger.update_move(member.ledger_id, final_status)
            if result.source_removed:
                # A remembered reading is keyed to where the file was, and
                # it is not there any more. Left behind it would sit in the
                # database until compaction, describing a path nothing will
                # ever ask about again.
                try:
                    ledger.forget_readings(member.source)
                except Exception:            # noqa: BLE001
                    pass
            # The intention to keep a second copy is recorded now, while the
            # file is known to be here and its hash is in hand. Whether the
            # other disk is plugged in is a separate question, asked later by
            # whoever drains the queue -- sorting does not wait on it.
            if mirror_root:
                relative = mirror.relative_for(member.destination)
                if relative:
                    ledger.queue_mirror(member.ledger_id, member.destination,
                                        relative, member.size, member.sha256)
            moved.append(member)

        if item_failed and planned_item.operation == "move":
            for member in reversed(moved):
                try:
                    mover.transfer(member.destination, member.source,
                                   operation="move",
                                   expected_hash=member.sha256,
                                   preserve_dates=True)
                except (OSError, mover.MoveError) as error:
                    ledger.update_move(member.ledger_id, "failed",
                                       "move succeeded; rollback failed: %s"
                                       % error)
                    messages.append("rollback failed for %s: %s"
                                    % (member.destination, error))
                else:
                    ledger.update_move(member.ledger_id, "rolled-back")
        elif not item_failed:
            completed += 1

    ledger.record_directories(run_id, created_directories)
    # A rolled-back item leaves the folders it was halfway into. They are
    # this run's, and they are empty, so they go now rather than waiting for
    # an undo that may never be asked for.
    if failed:
        paths.prune_empty(created_directories)

    status = "partial" if failed else "completed"
    summary = "%d items completed, %d failed, %d skipped" % (
        completed, failed, len(plan.skipped))
    ledger.finish_run(run_id, status, summary)
    return RunResult(run_id, False, completed, failed, len(plan.skipped),
                     messages=messages)


def undo(ledger, run_id=None, dry_run=False):
    original = ledger.latest_undoable_run() if run_id in (None, "last") \
        else ledger.run(int(run_id))
    if original is None:
        raise ValueError("no run is available to undo")
    original_moves = ledger.moves(original["id"], ("done",), reverse=True)
    if not original_moves:
        raise ValueError("run %s has no completed moves to undo" % original["id"])

    undo_run = ledger.start_run("undo", original["source_root"],
                                original["rules_hash"], dry_run)
    messages = []
    completed = failed = 0
    groups = {}
    for row in original_moves:
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except (KeyError, TypeError, ValueError):
            facts = {}
        new_id = ledger.add_move(
            undo_run, row["item_number"], row["member_number"], "move",
            "[undo %s]" % original["id"],
            row["destination"], row["source"], row["size"], row["sha256"],
            status="dry-run" if dry_run else "planned", facts=facts)
        groups.setdefault(row["item_number"], []).append((new_id, row))

    if dry_run:
        count = sum(len(group) for group in groups.values())
        ledger.finish_run(undo_run, "dry-run",
                          "%d moves would be restored" % count)
        return RunResult(undo_run, True)

    for item_number in sorted(groups, reverse=True):
        group = groups[item_number]
        problem = None
        for _new_id, original_row in group:
            source = original_row["destination"]
            destination = original_row["source"]
            if os.path.lexists(destination):
                problem = "original path is occupied: %s" % destination
                break
            if not os.path.lexists(source):
                problem = "moved file is missing: %s" % source
                break
            try:
                if mover.hash_path(source) != original_row["sha256"]:
                    problem = "file changed since it was sorted: %s" % source
                    break
            except OSError as error:
                problem = "cannot verify %s: %s" % (source, error)
                break
        if problem:
            for new_id, _row in group:
                ledger.update_move(new_id, "undo-failed", problem)
            messages.append(problem)
            failed += 1
            continue

        restored = []
        item_error = None
        for new_id, original_row in group:
            source = original_row["destination"]
            destination = original_row["source"]
            try:
                ledger.update_move(new_id, "moving")
                mover.transfer(source, destination, operation="move",
                               expected_hash=original_row["sha256"],
                               preserve_dates=True)
            except (OSError, mover.MoveError) as error:
                item_error = "%s: %s" % (source, error)
                ledger.update_move(new_id, "undo-failed", str(error))
                break
            restored.append((new_id, original_row))

        if item_error:
            for new_id, original_row in reversed(restored):
                try:
                    mover.transfer(original_row["source"],
                                   original_row["destination"],
                                   operation="move",
                                   expected_hash=original_row["sha256"],
                                   preserve_dates=True)
                except (OSError, mover.MoveError) as error:
                    ledger.update_move(
                        new_id, "undo-failed",
                        "undo succeeded; rollback failed: %s" % error)
                    messages.append("undo rollback failed for %s: %s"
                                    % (original_row["source"], error))
                else:
                    ledger.update_move(new_id, "rolled-back")
            restored_ids = set(new_id for new_id, _row in restored)
            for new_id, _row in group:
                if new_id not in restored_ids \
                        and ledger.connection.execute(
                            "SELECT status FROM moves WHERE id = ?",
                            (new_id,)).fetchone()[0] == "planned":
                    ledger.update_move(new_id, "cancelled",
                                       "another member of the bundle failed")
            messages.append(item_error)
            failed += 1
            continue

        for new_id, original_row in restored:
            ledger.update_move(new_id, "done")
            ledger.update_move(original_row["id"], "undone",
                               restored_to=original_row["source"])
        completed += 1

    status = "partial" if failed else "completed"
    removed_directories = []
    if not failed:
        # Undo is meant to put the folder back as it was, and a tree of empty
        # `Photos/2026/Canon EOS R6` folders is not how it was.
        removed_directories = paths.prune_empty(
            ledger.directories_for_run(original["id"]))
        ledger.mark_directories_removed(original["id"], removed_directories)
    ledger.finish_run(undo_run, status,
                      "%d restored, %d failed" % (completed, failed))
    if not failed:
        ledger.finish_run(original["id"], "undone",
                          original["summary"])
    return RunResult(undo_run, False, completed, failed,
                     messages=messages)


def reconcile(ledger):
    """Resolve journal rows left between intent and confirmation by a crash."""
    messages = []
    for row in ledger.incomplete_moves():
        source_exists = os.path.lexists(row["source"])
        destination_exists = os.path.lexists(row["destination"])
        destination_matches = False
        if destination_exists:
            try:
                destination_matches = mover.hash_path(row["destination"]) \
                    == row["sha256"]
            except OSError:
                pass
        if destination_matches and (row["operation"] == "copy"
                                    or not source_exists):
            status = "copied" if row["operation"] == "copy" else "done"
            ledger.update_move(row["id"], status,
                               "confirmed after interrupted run")
            messages.append("confirmed interrupted operation %d" % row["id"])
        elif source_exists and not destination_exists:
            ledger.update_move(row["id"], "failed",
                               "interrupted before filesystem change")
        else:
            ledger.update_move(row["id"], "failed",
                               "interrupted in an ambiguous state")
            messages.append("operation %d needs inspection" % row["id"])
    for run in ledger.running_runs():
        counts = dict((row["status"], row["count"])
                      for row in ledger.move_statuses(run["id"]))
        if any(counts.get(status) for status in ("planned", "moving", "copying")):
            continue
        failures = counts.get("failed", 0) + counts.get("undo-failed", 0)
        status = "partial" if failures else "recovered"
        ledger.finish_run(run["id"], status,
                          "reconciled after an interrupted process")
    return messages


def _no_match_reason(near_miss):
    """Why nothing matched, in the words of the rule that came closest.

    "no rule matched" is true and useless. A rules file that quietly does
    nothing is the most common way this tool will be wrong, and the reason is
    almost always one gate on one rule — a confidence floor, or a destination
    template needing a tag the file does not carry.
    """
    if near_miss is None:
        return "no rule matched"
    return "no rule matched (closest: %r %s)" % (near_miss.rule.name,
                                                 near_miss.reason)


def _skip_reason(item, root, ignore_patterns, settle_seconds):
    for member in item.members:
        if _matches_any(member, root, ignore_patterns):
            return "ignored by pattern"
        if _ignored_by_files(member, root):
            return "ignored by .autosortignore"
        try:
            status = os.lstat(member)
        except OSError as error:
            return "cannot stat: %s" % error
        if stat.S_ISLNK(status.st_mode):
            return "symbolic links are never followed"
        if not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
            return "special filesystem entries are not moved"
        if settle_seconds and time.time() - _latest_mtime(member) \
                < settle_seconds:
            return "has not been unchanged for %.1f seconds" % settle_seconds
    return None


def _matches_any(path, root, patterns):
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    name = os.path.basename(path)
    for pattern in patterns:
        cleaned = pattern.strip().replace("\\", "/").rstrip("/")
        if not cleaned:
            continue
        if fnmatch.fnmatch(name.lower(), cleaned.lower()) \
                or fnmatch.fnmatch(relative.lower(), cleaned.lower()):
            return True
        if "/" not in cleaned and any(
                fnmatch.fnmatch(part.lower(), cleaned.lower())
                for part in relative.split("/")):
            return True
    return False


def _ignored_by_files(path, root):
    """A compact gitignore-style matcher for per-directory ignore files."""
    root = os.path.abspath(root)
    directory = os.path.dirname(os.path.abspath(path))
    ancestors = []
    while paths.inside(root, directory):
        ancestors.append(directory)
        if _collision_key(directory) == _collision_key(root):
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    ignored = False
    for base in reversed(ancestors):
        filename = os.path.join(base, ".autosortignore")
        try:
            with open(filename, "r", encoding="utf-8",
                      errors="replace") as handle:
                patterns = handle.readlines()
        except OSError:
            continue
        relative = os.path.relpath(path, base).replace(os.sep, "/")
        parts = relative.split("/")
        for line in patterns:
            pattern = line.rstrip("\r\n")
            if not pattern or pattern.lstrip().startswith("#"):
                continue
            negate = pattern.startswith("!")
            if negate:
                pattern = pattern[1:]
            if pattern.startswith("\\#") or pattern.startswith("\\!"):
                pattern = pattern[1:]
            pattern = pattern.strip("/")
            if not pattern:
                continue
            if "/" in pattern:
                matched = fnmatch.fnmatch(relative, pattern) \
                    or fnmatch.fnmatch(relative, pattern + "/**")
            else:
                matched = any(fnmatch.fnmatch(part, pattern) for part in parts)
            if matched:
                ignored = not negate
    return ignored


def _latest_mtime(path):
    latest = os.lstat(path).st_mtime
    if not os.path.isdir(path):
        return latest
    for directory, subdirectories, filenames in os.walk(path,
                                                          followlinks=False):
        for name in list(subdirectories) + filenames:
            try:
                latest = max(latest,
                             os.lstat(os.path.join(directory, name)).st_mtime)
            except OSError:
                continue
    return latest


def _unsorted_directory(template, root):
    expanded = os.path.expanduser(os.path.expandvars(template))
    return os.path.abspath(expanded if os.path.isabs(expanded)
                           else os.path.join(root, expanded))


def _member_destinations(item, primary_destination, renamed):
    directory = os.path.dirname(primary_destination)
    primary_name = os.path.basename(item.primary)
    destination_name = os.path.basename(primary_destination)
    name_changed = destination_name != primary_name
    if not renamed and not name_changed:
        return [primary_destination if member == item.primary else
                os.path.join(directory,
                             paths.sanitise(os.path.basename(member)))
                for member in item.members]
    if item.sequence or "part archive" in item.reason:
        raise ValueError("a sequence or multipart archive cannot be renamed")
    source_stem = os.path.splitext(primary_name)[0]
    destination_stem = os.path.splitext(destination_name)[0]
    destinations = []
    for member in item.members:
        name = os.path.basename(member)
        if member == item.primary:
            target = destination_name
        elif name.lower().startswith(source_stem.lower()):
            target = destination_stem + name[len(source_stem):]
        else:
            target = name
        destinations.append(os.path.join(directory, paths.sanitise(target)))
    return destinations


def _resolve_collisions(item, destinations, sources, reserved, policy):
    def unavailable(candidate, index):
        folded = _collision_key(candidate)
        same_member = folded == _collision_key(sources[index])
        return folded in reserved or (os.path.lexists(candidate)
                                      and not same_member)

    keys = [_collision_key(path) for path in destinations]
    if len(set(keys)) != len(keys):
        return destinations, "bundle members collapse to the same portable name"
    if not any(unavailable(path, index)
               for index, path in enumerate(destinations)):
        return destinations, None
    if policy == "skip":
        return destinations, "destination already exists"
    if item.sequence or "part archive" in item.reason:
        return destinations, "cannot safely suffix a sequence or multipart archive"

    primary_name = os.path.basename(destinations[0])
    stem, extension = os.path.splitext(primary_name)
    for counter in range(2, 10000):
        suffixed_stem = "%s (%d)" % (stem, counter)
        candidate_primary = os.path.join(
            os.path.dirname(destinations[0]), suffixed_stem + extension)
        candidates = _member_destinations(item, candidate_primary,
                                           candidate_primary != item.name)
        if not any(unavailable(path, index)
                   for index, path in enumerate(candidates)):
            return candidates, None
    return destinations, "could not find collision-free names"


def _collision_key(path):
    # Conservative on purpose: case-only distinctions disappear on Windows,
    # most macOS volumes and the removable FAT volumes this tool targets.
    return os.path.normpath(os.path.abspath(path)).casefold()
