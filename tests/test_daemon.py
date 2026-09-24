"""Persistent queue, settle and foreground-daemon tests."""

from __future__ import annotations

import contextlib
import http.client
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
import tray                                               # noqa: E402


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

    def test_headless_tray_message_is_not_doubled(self):
        # tray.create's Linux reason and daemon.run's own log line each used
        # to say "continuing headless", so a real headless Linux daemon
        # logged "...continuing headless; continuing headless." -- true but
        # confusing, and cheap to get wrong again since the phrase lives in
        # two files that don't check each other.
        self.configure()
        messages = []
        with self.service(messages) as service:
            service._quit_requested = True
            with mock.patch.object(daemon.tray, "create",
                                   return_value=tray.UnavailableTray(
                                       "no StatusNotifier backend on this "
                                       "Linux desktop")):
                service.run()
        headless = [message for message in messages
                   if "continuing headless" in message]
        self.assertEqual(len(headless), 1)
        self.assertEqual(headless[0].count("continuing headless"), 1)

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

    def test_log_page_is_served_by_the_daemon_lock_socket(self):
        self.configure()
        with self.service() as service:
            response = []

            def fetch_status():
                client = http.client.HTTPConnection(
                    "127.0.0.1", service.lock.port, timeout=2)
                client.request("GET", "/api/status?token=" + service.token)
                reply = client.getresponse()
                response.append((reply.status, reply.read()))
                client.close()

            thread = threading.Thread(target=fetch_status)
            thread.start()
            command = service.lock.wait(2, service.web.handle_connection)
            thread.join(2)
            self.assertEqual(command, "")
            self.assertEqual(response[0][0], 200)
            self.assertIn(b'"paused": false', response[0][1])

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
            self.assertEqual(version, ledger.SCHEMA_VERSION)
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

    def test_status_says_when_the_install_command_cannot_work_yet(self):
        """A Steam Deck was told to run `sudo pacman -S`, bare."""
        row = {"key": "exiftool", "purpose": "reading metadata",
               "installed": False,
               "install": "sudo pacman -S --noconfirm perl-image-exiftool",
               "install_note": "This system's root filesystem is managed "
                               "by the OS image, so the command below will "
                               "fail until it is unlocked first."}
        with mock.patch("daemon.wake", return_value=False), \
                mock.patch("platform_support.inventory", return_value=[row]):
            code, output, _errors = self.run_cli(
                "status", "--state", self.state_file)
        self.assertEqual(code, 0)
        self.assertIn("unlocked first", output)
        self.assertLess(output.index("unlocked first"),
                        output.index("sudo pacman"))

    def menu_click(self, answers, opened=True):
        """`open-log --start`, as the applications-menu entry runs it."""
        rules_file = os.path.join(self.directory, "rules.ini")
        with mock.patch("daemon.wake", return_value=False), \
                mock.patch("paths.state_dir", return_value=self.directory), \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch("autosort._wait_for_new_daemon",
                           return_value=answers), \
                mock.patch("logpage.open_log", return_value=opened) as page:
            result = self.run_cli("open-log", "--start", "--rules", rules_file,
                                  "--state", self.state_file)
        return result, popen, page, rules_file

    def daemon_log(self):
        try:
            with open(os.path.join(self.directory, "daemon.log"),
                      encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""

    def test_the_menu_entry_starts_a_daemon_that_is_not_running(self):
        """On Linux the menu entry is the only way in, and clicking it with
        nothing running printed to a terminal that did not exist."""
        (code, _output, _errors), popen, page, rules_file = \
            self.menu_click(answers=True)
        self.assertEqual(code, 0)
        command = popen.call_args[0][0]
        self.assertIn("watch", command)
        self.assertEqual(command[command.index("--rules") + 1], rules_file)
        self.assertEqual(command[command.index("--state") + 1],
                         self.state_file)
        self.assertTrue(popen.call_args[1].get("start_new_session"))
        page.assert_called_once_with(self.state_file)

    def test_a_menu_click_that_cannot_start_it_says_so_somewhere(self):
        (code, _output, errors), _popen, page, _rules = \
            self.menu_click(answers=False)
        self.assertEqual(code, 1)
        page.assert_not_called()
        self.assertIn("did not start", errors)
        self.assertIn("did not start", self.daemon_log())

    def test_a_page_that_would_not_open_says_so_somewhere(self):
        (code, _output, _errors), _popen, _page, _rules = \
            self.menu_click(answers=True, opened=False)
        self.assertEqual(code, 1)
        self.assertIn("could not open the log page", self.daemon_log())

    def test_open_log_from_a_terminal_still_starts_nothing(self):
        with mock.patch("daemon.wake", return_value=False), \
                mock.patch("subprocess.Popen") as popen:
            code, _output, errors = self.run_cli(
                "open-log", "--state", self.state_file)
        self.assertEqual(code, 1)
        self.assertIn("not running", errors)
        popen.assert_not_called()

    def test_sort_now_fails_clearly_when_daemon_is_absent(self):
        with mock.patch("daemon.wake", return_value=False):
            code, _output, errors = self.run_cli(
                "sort-now", "--state", self.state_file)
        self.assertEqual(code, 1)
        self.assertIn("not running", errors)

    def test_open_log_only_uses_the_daemon_stored_url(self):
        with mock.patch("daemon.wake", return_value=True), \
                mock.patch("logpage.open_log", return_value=True) as open_log:
            code, _output, errors = self.run_cli(
                "open-log", "--state", self.state_file)
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        open_log.assert_called_once_with(self.state_file)

    def test_open_log_refuses_when_no_daemon_owns_the_port(self):
        with mock.patch("daemon.wake", return_value=False), \
                mock.patch("logpage.open_log") as open_log:
            code, _output, errors = self.run_cli(
                "open-log", "--state", self.state_file)
        self.assertEqual(code, 1)
        self.assertIn("not running", errors)
        open_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class PauseReasons(unittest.TestCase):
    """A pause the daemon took must lift itself; a pause you took must not.

    A rules file that does not parse has to stop the sorter, but the pause
    has to lift when the file is fixed -- otherwise a typo stops sorting
    permanently and the only symptom is that nothing ever happens again.
    That is exactly what it did once.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        os.makedirs(self.root)
        self.rules_file = os.path.join(self.directory, "rules.ini")
        self.state = os.path.join(self.directory, "state.db")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def write_rules(self, when="kind = image"):
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = yes\nsettle_seconds = 0\n\n"
                         "[watch]\nfolders = %s\n\n[rule: images]\nwhen = %s\n"
                         "into = %s/out\n" % (self.root, when, self.directory))

    def service(self):
        return daemon.PollingDaemon(rule_path=self.rules_file,
                                    state_file=self.state, port=0,
                                    output=lambda _message: None)

    def test_a_broken_rules_file_pauses_and_a_fixed_one_resumes(self):
        self.write_rules(when="kind = = image")          # will not parse
        with self.service() as service:
            self.assertIsNone(service._reload_rules())
            self.assertTrue(service.journal.paused())
            self.assertEqual(service.journal.get_state("paused_by"), "rules")

            self.write_rules()                            # fixed
            self.assertIsNotNone(service._reload_rules())
            self.assertFalse(service.journal.paused(),
                             "a pause the daemon took must lift itself")

    def test_a_pause_you_asked_for_is_never_lifted(self):
        self.write_rules()
        with self.service() as service:
            service.journal.set_paused(True)              # by="user"
            self.assertIsNotNone(service._reload_rules())
            self.assertTrue(service.journal.paused(),
                            "the daemon must not resume what you paused")
