"""Persistent polling, settle detection and single-instance coordination.

The daemon never moves a path directly.  It observes bundle stat signatures in
SQLite until they remain unchanged for the configured interval, then hands the
bundle to ``sorter.build_plan`` and ``sorter.execute`` — the same transaction
used by the one-shot CLI.  This keeps background operation from becoming a
second, less-tested implementation of file safety.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import select
import socket
import stat
import time
import webbrowser

import bundles
import ledger as ledger_module
import mover
import logpage
import rules
import corrections as corrections_module
import sorter
import tray


DEFAULT_PORT = 47653
QUEUE_LIMIT = 100


class AlreadyRunning(Exception):
    pass


class DaemonLock(object):
    def __init__(self, journal, port=None):
        saved = journal.get_state("daemon_port")
        requested = int(port if port is not None
                        else (saved if saved is not None else DEFAULT_PORT))
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind(("127.0.0.1", requested))
            self.socket.listen(4)
            self.socket.setblocking(False)
        except OSError as error:
            self.socket.close()
            raise AlreadyRunning("auto-sort is already running on port %d: %s"
                                 % (requested, error))
        self.port = self.socket.getsockname()[1]
        journal.set_state("daemon_port", self.port)

    def close(self):
        self.socket.close()

    def wait(self, timeout, request_handler=None):
        """Wait for a wake request or timeout; return the small command sent."""
        ready, _writable, _errors = select.select(
            [self.socket], [], [], max(0.0, timeout))
        if not ready:
            return None
        try:
            connection, _address = self.socket.accept()
            connection.settimeout(1)
            initial = connection.recv(4096)
            if request_handler is not None:
                command = request_handler(connection, initial)
                if command is not None:
                    connection.close()
                    return command
            command = initial.decode("ascii", "replace").strip()
            connection.sendall(b"ok\n")
            connection.close()
            return command
        except OSError:
            return None


class PollingDaemon(object):
    def __init__(self, rule_path=None, state_file=None, dry_run=None,
                 port=None, output=None):
        self.rule_path = rule_path
        self.journal = ledger_module.Ledger(state_file)
        self.dry_run = dry_run
        self.output = output or (lambda message: print(message, flush=True))
        try:
            self.lock = DaemonLock(self.journal, port)
        except Exception:
            self.journal.close()
            raise
        self.journal.recover_processing_queue()
        self.token = secrets.token_urlsafe(24)
        self.journal.set_state("web_token", self.token)
        self.web = logpage.LogPage(self.journal, self.lock.port, self.token)
        self.rule_set = None
        self._rule_identity = None
        self._quit_requested = False
        self._sort_requested = False
        self.output("Log: %s" % self.web.url)

    def close(self):
        self.lock.close()
        self.journal.close()

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()

    def _reload_rules(self):
        try:
            loaded = rules.load(self.rule_path)
        except rules.RuleError as error:
            self.output("Rules error; sorting paused: %s" % error)
            self.journal.set_paused(True)
            return None
        identity = (loaded.source, loaded.source_hash)
        if identity != self._rule_identity:
            self.rule_set = loaded
            self._rule_identity = identity
            self.output("Loaded %d rules from %s"
                        % (len(loaded.rules), loaded.source))
        return self.rule_set

    # How often to look for files that were moved after being sorted. Rare
    # on purpose: the check is nearly free when nothing has moved -- one stat
    # per recorded placement -- and expensive when something has, because
    # finding where it went means indexing the tree. Somebody tidying up for
    # an hour should cost one index build, not seven hundred.
    CORRECTION_INTERVAL = 1800

    def _check_corrections(self, rule_set, now_value):
        """Notice disagreement, record it, and say so. Never act on it.

        The daemon is the right place to watch for this because corrections
        happen long after a sort, in Finder, when nobody is running anything.
        What it must not do is adjust: a placement that changes because of
        something inferred from a folder is the behaviour that makes a
        background process impossible to trust.
        """
        last = self.journal.get_state("corrections_checked_at")
        try:
            last_value = float(last or 0)
        except (TypeError, ValueError):
            last_value = 0.0
        if now_value - last_value < self.CORRECTION_INTERVAL:
            return
        self.journal.set_state("corrections_checked_at", repr(now_value))
        roots = [os.path.abspath(folder)
                 for folder in rule_set.watch.folders
                 if _root_available(os.path.abspath(folder))]
        if not roots:
            return
        try:
            found = corrections_module.detect(self.journal, roots)
        except (OSError, ValueError) as error:
            self.output("Could not check for corrections: %s" % error)
            return
        moved = [item for item in found if item[1] == "moved"]
        if moved:
            self.output("%d file%s moved after sorting. Run "
                        "`auto-sort corrections` to see what it suggests."
                        % (len(moved), "" if len(moved) == 1 else "s"))

    def cycle(self, now_value=None):
        now_value = time.time() if now_value is None else float(now_value)
        rule_set = self._reload_rules()
        if rule_set is None:
            return []
        self.journal.retain_queue_roots(rule_set.watch.folders)
        plan_fingerprint = sorter.rules_hash(rule_set)
        requested_dry = rule_set.settings.dry_run \
            if self.dry_run is None else bool(self.dry_run)
        queue_fingerprint = _queue_rules_hash(
            plan_fingerprint, requested_dry)
        messages = sorter.reconcile(self.journal)
        for message in messages:
            self.output("Recovered: %s" % message)

        results = []
        for root in rule_set.watch.folders:
            root = os.path.abspath(root)
            if not _root_available(root):
                self.output("Watched folder unavailable; queue retained: %s"
                            % root)
                continue
            self._observe_root(root, rule_set, queue_fingerprint, now_value)
            if self.journal.paused():
                continue
            ready = self.journal.ready_items(
                root, now_value - rule_set.settings.settle_seconds,
                queue_fingerprint, now_value, QUEUE_LIMIT)
            if not ready:
                continue
            results.extend(self._process_ready(
                root, ready, rule_set, queue_fingerprint, plan_fingerprint,
                requested_dry, now_value))
            if self.journal.paused():
                break
        if not self.journal.paused():
            self._check_corrections(rule_set, now_value)
        return results

    def _observe_root(self, root, rule_set, fingerprint, now_value):
        protected = self._protected(rule_set)
        completed_paths = self.journal.completed_destinations()
        completed = set(sorter._collision_key(path)
                        for path in completed_paths)
        completed_roots = tuple(
            directory for directory in
            set(os.path.dirname(path) for path in completed_paths)
            if sorter._collision_key(directory)
            != sorter._collision_key(root)
            and paths_inside(root, directory))
        destination_roots = tuple(
            destination for watched in rule_set.watch.folders
            for destination in _destination_roots(rule_set, watched)
            if paths_inside(root, destination))
        ignore_patterns = sorter.BUILTIN_IGNORE + tuple(rule_set.watch.ignore)
        observed = 0
        for item in bundles.walk(root, max_depth=rule_set.watch.depth):
            if any(sorter._collision_key(member) in protected
                   for member in item.members):
                continue
            if sorter._collision_key(item.primary) in completed \
                    or any(paths_inside(destination, item.primary)
                           for destination in completed_roots) \
                    or any(paths_inside(destination, item.primary)
                           for destination in destination_roots):
                continue
            if sorter._skip_reason(item, root, ignore_patterns, 0):
                continue
            try:
                signature = item_fingerprint(item)
            except OSError as error:
                self.output("Cannot observe %s: %s" % (item.primary, error))
                continue
            self.journal.observe_item(
                root, item.primary, item.members, item.is_dir, item.reason,
                item.sequence, signature, fingerprint, now_value)
            observed += 1
        self.journal.remove_unseen_items(root, now_value)
        return observed

    def _process_ready(self, root, rows, rule_set, queue_fingerprint,
                       plan_fingerprint, requested_dry, now_value):
        valid = []
        for row in rows:
            item = item_from_row(row)
            unavailable = next((member for member in item.members
                                if not mover.exclusively_available(member)),
                               None)
            if unavailable is not None:
                self.journal.set_queue_status(
                    row["id"], "failed", "still open for writing: %s"
                    % unavailable, now_value + 5)
                continue
            try:
                current = item_fingerprint(item)
            except OSError as error:
                self.journal.set_queue_status(
                    row["id"], "failed", str(error), now_value + 5,
                    increment_attempt=True)
                continue
            if current != row["fingerprint"]:
                self.journal.observe_item(
                    root, item.primary, item.members, item.is_dir, item.reason,
                    item.sequence, current, queue_fingerprint, now_value)
                continue
            valid.append((row, item))
        if not valid:
            return []

        has_preview = self.journal.has_preview(root, plan_fingerprint)
        if requested_dry or not has_preview:
            plan = sorter.build_plan(
                root, rule_set, exclude=self._protected_paths(rule_set),
                items=[item for _row, item in valid])
            if not plan.items:
                reasons = dict((sorter._collision_key(path), reason)
                               for path, reason in plan.skipped)
                for row, item in valid:
                    reason = reasons.get(
                        sorter._collision_key(item.primary), "nothing to do")
                    if _retryable_skip(reason):
                        self.journal.set_queue_status(
                            row["id"], "failed", reason, now_value + 5,
                            increment_attempt=True)
                    else:
                        self.journal.set_queue_status(
                            row["id"], "done", reason)
                return []
            result = sorter.execute(
                plan, rule_set, self.journal,
                dry_run=True if requested_dry else False)
            planned = set(sorter._collision_key(item.item.primary)
                          for item in plan.items)
            reasons = dict((sorter._collision_key(path), reason)
                           for path, reason in plan.skipped)
            for row, item in valid:
                key = sorter._collision_key(item.primary)
                if key not in planned and _retryable_skip(
                        reasons.get(key, "nothing to do")):
                    reason = reasons[key]
                    self.journal.set_queue_status(
                        row["id"], "failed", reason, now_value + 5,
                        increment_attempt=True)
                elif result.forced_preview and key in planned:
                    self.journal.set_queue_status(
                        row["id"], "pending", "awaiting preview approval")
                else:
                    self.journal.set_queue_status(
                        row["id"], "done", reasons.get(key))
            if result.forced_preview:
                for planned_item in plan.items:
                    self.output("Preview: %s %s -> %s [%s]" % (
                        planned_item.operation, planned_item.item.primary,
                        planned_item.members[0].destination,
                        planned_item.rule_name))
                self.journal.set_paused(True)
                self.output("Preview run %d created; daemon paused. Review it "
                            "and run 'auto-sort resume'." % result.run_id)
            return [result]

        results = []
        for row, item in valid:
            if self.journal.paused():
                break
            self.journal.set_queue_status(row["id"], "processing")
            plan = sorter.build_plan(
                root, rule_set, exclude=self._protected_paths(rule_set),
                items=[item])
            if not plan.items:
                reason = plan.skipped[0][1] if plan.skipped else "nothing to do"
                if _retryable_skip(reason):
                    self.journal.set_queue_status(
                        row["id"], "failed", reason, now_value + 5,
                        increment_attempt=True)
                else:
                    self.journal.set_queue_status(row["id"], "done", reason)
                continue
            result = sorter.execute(plan, rule_set, self.journal, dry_run=False)
            results.append(result)
            if result.failed:
                attempts = row["attempts"] + 1
                delay = min(300, 5 * (2 ** min(attempts, 6)))
                self.journal.set_queue_status(
                    row["id"], "failed", "; ".join(result.messages),
                    now_value + delay, increment_attempt=True)
            elif result.forced_preview:
                self.journal.set_queue_status(
                    row["id"], "pending", "awaiting preview approval")
                self.journal.set_paused(True)
                break
            else:
                self.journal.set_queue_status(row["id"], "done")
        return results

    def _protected_paths(self, rule_set):
        return (rule_set.source, self.journal.filename,
                self.journal.filename + "-wal",
                self.journal.filename + "-shm")

    def _protected(self, rule_set):
        return set(sorter._collision_key(path)
                   for path in self._protected_paths(rule_set) if path)

    def run(self, once=False):
        if not once and hasattr(os, "nice"):
            try:
                os.nice(10)
            except OSError:
                pass
        if once:
            self.cycle()
            return
        status_item = tray.create({
            "open_log": self._tray_open_log,
            "toggle_pause": self._tray_toggle_pause,
            "sort_now": self._tray_sort_now,
            "quit": self._tray_quit,
        })
        if not status_item.available:
            self.output("Tray: %s; continuing headless." % status_item.reason)
        try:
            while not self._quit_requested:
                self.cycle()
                interval = self.rule_set.settings.poll_seconds \
                    if self.rule_set is not None else 5
                status_item.set_paused(self.journal.paused())
                self._wait_with_tray(interval, status_item)
        finally:
            status_item.close()

    def _wait_with_tray(self, interval, status_item):
        deadline = time.monotonic() + interval
        while not self._quit_requested and not self._sort_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            status_item.pump(min(0.25, remaining))
            self.lock.wait(min(0.25, remaining), self.web.handle_connection)
        self._sort_requested = False

    def _tray_open_log(self):
        webbrowser.open(self.web.url)

    def _tray_toggle_pause(self):
        self.journal.set_paused(not self.journal.paused())

    def _tray_sort_now(self):
        self._sort_requested = True

    def _tray_quit(self):
        self._quit_requested = True


def item_fingerprint(item):
    """Hash names and lstat values, never file contents, for settle detection."""
    digest = hashlib.sha256()
    for member in item.members:
        _fingerprint_path(member, digest, os.path.dirname(item.primary))
    return digest.hexdigest()


def _fingerprint_path(path, digest, base):
    status = os.lstat(path)
    digest.update(os.fsencode(os.path.relpath(path, base)))
    digest.update(b"\0")
    digest.update(str(stat.S_IFMT(status.st_mode)).encode("ascii"))
    digest.update(b":")
    digest.update(str(status.st_size).encode("ascii"))
    digest.update(b":")
    digest.update(str(getattr(status, "st_mtime_ns",
                              int(status.st_mtime * 1e9))).encode("ascii"))
    digest.update(b":")
    digest.update(str(getattr(status, "st_ctime_ns",
                              int(status.st_ctime * 1e9))).encode("ascii"))
    digest.update(b":")
    digest.update(str(status.st_dev).encode("ascii"))
    digest.update(b":")
    digest.update(str(status.st_ino).encode("ascii"))
    digest.update(b"\0")
    if stat.S_ISDIR(status.st_mode):
        with os.scandir(path) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            _fingerprint_path(entry.path, digest, base)


def item_from_row(row):
    return bundles.Item(
        row["primary_path"], json.loads(row["members_json"]),
        bool(row["is_dir"]), row["reason"] or "", row["sequence_count"])


def _root_available(root):
    try:
        with os.scandir(root) as entries:
            next(entries, None)
        return True
    except OSError:
        return False


def _destination_roots(rule_set, source_root):
    """Return safe static output prefixes that live below a watched root."""
    templates = [rule.into for rule in rule_set.rules
                 if rule.into and rule.mode != "leave"]
    if rule_set.settings.unsorted == "gather":
        templates.append(rule_set.settings.unsorted_into)
    results = []
    source_key = sorter._collision_key(source_root)
    for template in templates:
        expanded = os.path.expanduser(os.path.expandvars(str(template)))
        drive, tail = os.path.splitdrive(expanded)
        absolute = tail.startswith(("/", "\\"))
        components = [part for part in re.split(r"[/\\]+", tail) if part]
        static = []
        for component in components:
            if "{" in component or "}" in component:
                break
            static.append(component)
        if not static:
            continue
        prefix = os.path.join(*static)
        if absolute:
            prefix = os.sep + prefix
        if drive:
            prefix = drive + prefix
        if not os.path.isabs(prefix):
            prefix = os.path.join(source_root, prefix)
        prefix = os.path.abspath(prefix)
        if sorter._collision_key(prefix) != source_key:
            results.append(prefix)
    return tuple(results)


def paths_inside(root, candidate):
    root_key = sorter._collision_key(root).rstrip(os.sep)
    candidate_key = sorter._collision_key(candidate)
    return candidate_key == root_key \
        or candidate_key.startswith(root_key + os.sep)


def _retryable_skip(reason):
    return reason.startswith((
        "cannot stat:", "identification failed:",
        "could not fingerprint item:",
    ))


def _queue_rules_hash(plan_fingerprint, dry_run):
    digest = hashlib.sha256()
    digest.update(plan_fingerprint.encode("ascii"))
    digest.update(b"\0dry" if dry_run else b"\0apply")
    return digest.hexdigest()


def wake(state_file=None, command="wake"):
    try:
        with ledger_module.Ledger(state_file) as journal:
            port = int(journal.get_state("daemon_port", DEFAULT_PORT))
        connection = socket.create_connection(("127.0.0.1", port), timeout=1)
        connection.sendall((command + "\n").encode("ascii"))
        connection.recv(16)
        connection.close()
        return True
    except (OSError, TypeError, ValueError):
        return False
