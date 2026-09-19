"""Persistent queue, settle and foreground-daemon tests."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autosort                                          # noqa: E402
import bundles                                           # noqa: E402
import daemon                                            # noqa: E402
import fixtures                                          # noqa: E402
import ledger                                            # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402


class PersistentDaemon(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        self.output = os.path.join(self.directory, "output")
        self.rule_file = os.path.join(self.directory, "rules.ini")
        self.state_file = os.path.join(self.directory, "state.db")
        os.makedirs(self.root)
        os.makedirs(self.output)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def configure(self, settle=3, dry_run="no"):
        source = """
[settings]
dry_run = {dry_run}
settle_seconds = {settle}
poll_seconds = 1

[watch]
folders = {root}
depth = 3

[rule: images]
when = kind = image
into = {output}/Pictures
""".format(dry_run=dry_run, settle=settle, root=self.root,
           output=self.output)
        with open(self.rule_file, "w", encoding="utf-8") as handle:
            handle.write(source)

    def image(self, name="photo.png"):
        return fixtures.png(os.path.join(self.root, name))

    def service(self, messages=None, dry_run=None):
        return daemon.PollingDaemon(
            self.rule_file, self.state_file, dry_run=dry_run, port=0,
            output=(messages if messages is not None else []).append)

    def test_waits_for_stability_then_forces_preview_and_pauses(self):
        self.configure(settle=3)
        source = self.image()
        messages = []
        with self.service(messages) as service:
            self.assertEqual(service.cycle(now_value=100), [])
            self.assertEqual(service.cycle(now_value=102), [])
            preview = service.cycle(now_value=104)
            self.assertEqual(len(preview), 1)
            self.assertTrue(preview[0].forced_preview)
            self.assertTrue(service.journal.paused())
            self.assertTrue(os.path.exists(source))

            service.journal.set_paused(False)
            applied = service.cycle(now_value=105)
            self.assertEqual(len(applied), 1)
            self.assertEqual(applied[0].completed, 1)
            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.exists(
                os.path.join(self.output, "Pictures", "photo.png")))
        self.assertTrue(any("daemon paused" in message for message in messages))

    def test_change_restarts_settle_window(self):
        self.configure(settle=3, dry_run="yes")
        source = self.image()
        with self.service() as service:
            service.cycle(now_value=100)
            with open(source, "ab") as handle:
                handle.write(b"changed")
            self.assertEqual(service.cycle(now_value=102), [])
            row = service.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            self.assertEqual(row["stable_since"], 102)
            self.assertEqual(service.cycle(now_value=104), [])
            self.assertEqual(len(service.cycle(now_value=105)), 1)

    def test_dry_run_item_is_not_repeated_until_it_changes(self):
        self.configure(settle=0, dry_run="yes")
        self.image()
        with self.service() as service:
            self.assertEqual(len(service.cycle(now_value=100)), 1)
            self.assertEqual(service.cycle(now_value=101), [])
            row = service.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            self.assertEqual(row["status"], "done")

    def test_switching_from_dry_run_to_apply_requeues_existing_item(self):
        self.configure(settle=0, dry_run="yes")
        source = self.image()
        with self.service(dry_run=True) as previewer:
            self.assertEqual(len(previewer.cycle(now_value=100)), 1)
            self.assertTrue(os.path.exists(source))
        with self.service(dry_run=False) as applier:
            results = applier.cycle(now_value=101)
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].dry_run)
            self.assertFalse(os.path.exists(source))

    def test_output_below_watched_root_is_not_watched_again(self):
        source = """
[settings]
dry_run = no
settle_seconds = 0

[watch]
folders = {root}
depth = 3

[rule: images]
when = kind = image
into = Sorted
mode = copy
""".format(root=self.root)
        with open(self.rule_file, "w", encoding="utf-8") as handle:
            handle.write(source)
        original = self.image()
        with self.service() as service:
            service.cycle(now_value=100)
            service.journal.set_paused(False)
            service.cycle(now_value=101)
            self.assertTrue(os.path.exists(original))
            copied = os.path.join(self.root, "Sorted", "photo.png")
            self.assertTrue(os.path.exists(copied))
            service.cycle(now_value=102)
            queued = service.journal.connection.execute(
                "SELECT primary_path FROM queue").fetchall()
            self.assertEqual([row["primary_path"] for row in queued],
                             [original])

    def test_dynamic_output_directory_is_remembered(self):
        source = """
[settings]
dry_run = no
settle_seconds = 0

[watch]
folders = {root}
depth = 3

[rule: images]
when = kind = image
into = {{kind}}
mode = copy
""".format(root=self.root)
        with open(self.rule_file, "w", encoding="utf-8") as handle:
            handle.write(source)
        original = self.image("first.png")
        with self.service() as service:
            service.cycle(now_value=100)
            service.journal.set_paused(False)
            service.cycle(now_value=101)
            output_root = os.path.join(self.root, "image")
            newcomer = fixtures.png(os.path.join(output_root, "new.png"))
            service.cycle(now_value=102)
            queued = [row["primary_path"] for row in
                      service.journal.connection.execute(
                          "SELECT primary_path FROM queue")]
            self.assertIn(original, queued)
            self.assertNotIn(newcomer, queued)

    def test_output_that_is_also_a_watched_root_is_excluded(self):
        sorted_root = os.path.join(self.root, "Sorted")
        os.makedirs(sorted_root)
        source = """
[settings]
dry_run = no
settle_seconds = 0

[watch]
folders = {root}, {sorted_root}
depth = 3

[rule: images]
when = kind = image
into = {sorted_root}
mode = copy
""".format(root=self.root, sorted_root=sorted_root)
        with open(self.rule_file, "w", encoding="utf-8") as handle:
            handle.write(source)
        self.image()
        with self.service() as service:
            service.cycle(now_value=100)
            service.journal.set_paused(False)
            service.cycle(now_value=101)
            service.cycle(now_value=102)
            copied = os.path.join(sorted_root, "photo.png")
            queued = service.journal.connection.execute(
                "SELECT primary_path FROM queue").fetchall()
            self.assertNotIn(copied,
                             [row["primary_path"] for row in queued])

    def test_output_ancestor_does_not_exclude_the_watched_inbox(self):
        parent = os.path.dirname(self.root)
        source = """
[settings]
dry_run = yes
settle_seconds = 0

[watch]
folders = {root}

[rule: images]
when = kind = image
into = {parent}
""".format(root=self.root, parent=parent)
        with open(self.rule_file, "w", encoding="utf-8") as handle:
            handle.write(source)
        original = self.image()
        with self.service() as service:
            results = service.cycle(now_value=100)
            self.assertEqual(len(results), 1)
            row = service.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            self.assertEqual(row["primary_path"], original)
            self.assertEqual(row["status"], "done")

    def test_unavailable_root_retains_queued_work(self):
        self.configure(settle=30)
        self.image()
        with self.service() as service:
            service.cycle(now_value=100)
            moved_root = self.root + "-away"
            os.rename(self.root, moved_root)
            service.cycle(now_value=200)
            count = service.journal.connection.execute(
                "SELECT COUNT(*) FROM queue").fetchone()[0]
            self.assertEqual(count, 1)

    def test_removing_a_watch_root_prunes_its_stale_queue(self):
        self.configure(settle=30)
        self.image()
        with self.service() as service:
            service.cycle(now_value=100)
            other = os.path.join(self.directory, "other")
            os.makedirs(other)
            with open(self.rule_file, "w", encoding="utf-8") as handle:
                handle.write("""
[settings]
settle_seconds = 30
[watch]
folders = {other}
""".format(other=other))
            service.cycle(now_value=101)
            count = service.journal.connection.execute(
                "SELECT COUNT(*) FROM queue").fetchone()[0]
            self.assertEqual(count, 0)

    def test_interrupted_processing_is_retryable_after_restart(self):
        self.configure(settle=30)
        self.image()
        with self.service() as service:
            service.cycle(now_value=100)
            row = service.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            service.journal.set_queue_status(row["id"], "processing")
        with self.service() as restarted:
            row = restarted.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIn("stopped", row["error"])

    def test_transient_planning_error_is_retried_not_forgotten(self):
        self.configure(settle=0, dry_run="yes")
        self.image()
        with self.service() as service:
            with mock.patch("sorter.mover.hash_path",
                            side_effect=OSError("temporarily busy")):
                self.assertEqual(service.cycle(now_value=100), [])
            row = service.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIn("temporarily busy", row["error"])
            self.assertEqual(len(service.cycle(now_value=106)), 1)
            row = service.journal.connection.execute(
                "SELECT * FROM queue").fetchone()
            self.assertEqual(row["status"], "done")

    def test_one_transient_failure_does_not_hide_inside_a_batch(self):
        self.configure(settle=0, dry_run="yes")
        first = self.image("first.png")
        second = self.image("second.png")
        real_hash = sorter.mover.hash_path

        def sometimes_busy(path):
            if path == second:
                raise OSError("temporarily busy")
            return real_hash(path)

        with self.service() as service:
            with mock.patch("sorter.mover.hash_path",
                            side_effect=sometimes_busy):
                results = service.cycle(now_value=100)
            self.assertEqual(len(results), 1)
            rows = service.journal.connection.execute(
                "SELECT * FROM queue ORDER BY primary_path").fetchall()
            self.assertEqual([row["primary_path"] for row in rows],
                             [first, second])
            self.assertEqual([row["status"] for row in rows],
                             ["done", "failed"])

    def test_invalid_rules_pause_without_touching_source(self):
        self.configure()
        source = self.image()
        with open(self.rule_file, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = perhaps\n")
        with self.service() as service:
            self.assertEqual(service.cycle(now_value=100), [])
            self.assertTrue(service.journal.paused())
        self.assertTrue(os.path.exists(source))

    def test_loopback_port_is_a_single_instance_lock(self):
        self.configure()
        with self.service() as first:
            with self.assertRaises(daemon.AlreadyRunning):
                daemon.PollingDaemon(
                    self.rule_file, self.state_file, port=first.lock.port,
                    output=lambda _message: None)

    def test_wake_reaches_the_running_instance(self):
        self.configure()
        with self.service() as service:
            result = []
            thread = threading.Thread(
                target=lambda: result.append(
                    daemon.wake(self.state_file, "sort-now")))
            thread.start()
            command = service.lock.wait(2)
            thread.join(2)
            self.assertEqual(command, "sort-now")
            self.assertEqual(result, [True])

    def test_watch_once_cli_uses_the_persistent_queue(self):
        self.configure(settle=0, dry_run="yes")
        source = self.image()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = autosort.main([
                "watch", "--once", "--rules", self.rule_file,
                "--state", self.state_file, "--port", "0",
            ])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(source))
        with ledger.Ledger(self.state_file) as journal:
            self.assertEqual(journal.queue_counts()[0]["status"], "done")
            count = journal.connection.execute(
                "SELECT COUNT(*) FROM runs").fetchone()[0]
            self.assertEqual(count, 1)

    def test_version_one_database_is_migrated_without_losing_state(self):
        with ledger.Ledger(self.state_file) as journal:
            journal.set_paused(True)
            with journal.connection:
                journal.connection.execute("DROP TABLE queue")
                journal.connection.execute("PRAGMA user_version = 1")
        with ledger.Ledger(self.state_file) as migrated:
            version = migrated.connection.execute(
                "PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 2)
            self.assertTrue(migrated.paused())
            self.assertEqual(migrated.queue_counts(), [])

    def test_directory_fingerprint_changes_for_nested_content(self):
        package = os.path.join(self.root, "Thing.app")
        os.makedirs(os.path.join(package, "Contents"))
        nested = os.path.join(package, "Contents", "info")
        with open(nested, "wb") as handle:
            handle.write(b"one")
        item = bundles.Item(package, is_dir=True)
        before = daemon.item_fingerprint(item)
        with open(nested, "wb") as handle:
            handle.write(b"two-two")
        self.assertNotEqual(before, daemon.item_fingerprint(item))


class DaemonCli(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.state_file = os.path.join(self.directory, "state.db")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def run_cli(self, *arguments):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(errors):
            result = autosort.main(list(arguments))
        return result, output.getvalue(), errors.getvalue()

    def test_pause_and_resume_are_persistent(self):
        with mock.patch("daemon.wake", return_value=False):
            code, output, _errors = self.run_cli(
                "pause", "--state", self.state_file)
            self.assertEqual(code, 0)
            self.assertIn("paused", output)
            with ledger.Ledger(self.state_file) as journal:
                self.assertTrue(journal.paused())

            code, output, _errors = self.run_cli(
                "resume", "--state", self.state_file)
            self.assertEqual(code, 0)
            self.assertIn("resumed", output)
            with ledger.Ledger(self.state_file) as journal:
                self.assertFalse(journal.paused())

    def test_status_json_reports_queue_and_process_state(self):
        with mock.patch("daemon.wake", return_value=False):
            code, output, _errors = self.run_cli(
                "status", "--state", self.state_file, "--json")
        self.assertEqual(code, 0)
        self.assertIn('"running": false', output)
        self.assertIn('"queue": {}', output)

    def test_sort_now_fails_clearly_when_daemon_is_absent(self):
        with mock.patch("daemon.wake", return_value=False):
            code, _output, errors = self.run_cli(
                "sort-now", "--state", self.state_file)
        self.assertEqual(code, 1)
        self.assertIn("not running", errors)


if __name__ == "__main__":
    unittest.main()
