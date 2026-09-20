"""Restarting the daemon, because it cannot reload its own code.

The daemon re-reads its rules whenever the file changes, but a change to
auto-sort itself only takes effect in a new process -- and it needs one at
exactly the moment it is least obvious, right after a change, when everything
looks fine and the old code is still running.

Two things here were wrong when first written and are the reason for most of
these tests. It asked the service manager to restart *its* daemon even when
told to restart a different one, and it called the job done as soon as
anything answered, which the old process does right up until it exits.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import contextlib
import io
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autosort                                          # noqa: E402
import autostart                                         # noqa: E402
import daemon                                            # noqa: E402
import ledger                                            # noqa: E402


class Reporting(unittest.TestCase):
    """These call the command, which talks to a person; the suite need not
    listen."""

    def restart(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return autosort.restart(**kwargs)

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.state = os.path.join(self.directory, "state.db")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_nothing_running_and_nothing_installed_says_so(self):
        with mock.patch.object(autostart, "restart",
                               return_value=(False, "no login item")), \
                mock.patch.object(daemon, "running_port", return_value=None), \
                mock.patch.object(daemon, "running_pid", return_value=None):
            self.assertEqual(self.restart(state=self.state), 1)

    def test_a_named_daemon_never_kickstarts_the_managed_one(self):
        # Naming a state file or a port means a particular daemon. Asking
        # launchd to bounce its own would restart something else entirely
        # and then report success, which is what it did.
        with mock.patch.object(autostart, "restart") as service, \
                mock.patch.object(daemon, "running_port", return_value=1234), \
                mock.patch.object(daemon, "running_pid", return_value=11), \
                mock.patch.object(daemon, "wake", return_value=False):
            self.restart(state=self.state)
            self.restart(port=48999)
        service.assert_not_called()

    def test_the_managed_path_is_used_when_no_daemon_is_named(self):
        with mock.patch.object(autostart, "restart",
                               return_value=(True, "")) as service, \
                mock.patch.object(autosort, "_wait_for_new_daemon",
                                  return_value=True):
            self.assertEqual(self.restart(), 0)
        service.assert_called_once()

    def test_the_old_process_answering_is_not_a_restart(self):
        # `running_pid` keeps returning the pid we asked to go away, so the
        # wait must not be satisfied by it.
        with mock.patch.object(daemon, "running_pid", return_value=77):
            self.assertFalse(
                autosort._wait_for_new_daemon(self.state, 77, 0.6))

    def test_a_different_pid_is_a_restart(self):
        with mock.patch.object(daemon, "running_pid", return_value=78):
            self.assertTrue(
                autosort._wait_for_new_daemon(self.state, 77, 2))

    def test_a_failed_service_restart_is_reported(self):
        with mock.patch.object(autostart, "restart",
                               return_value=(True, "")), \
                mock.patch.object(autosort, "_wait_for_new_daemon",
                                  return_value=False):
            self.assertEqual(self.restart(), 1)


class ServiceRestart(unittest.TestCase):

    def test_no_login_item_means_nothing_to_ask(self):
        with mock.patch.object(autostart, "status",
                               return_value={"installed": False,
                                             "platform": "macos",
                                             "path": "/x"}):
            restarted, reason = autostart.restart()
        self.assertFalse(restarted)
        self.assertIn("no login item", reason)

    def test_platforms_without_a_service_manager_decline(self):
        # An XDG autostart entry and a Startup shortcut say what to run at
        # login and manage nothing afterwards.
        for platform in ("linux", "windows"):
            with mock.patch.object(autostart, "status",
                                   return_value={"installed": True,
                                                 "platform": platform,
                                                 "path": "/x"}):
                restarted, reason = autostart.restart()
            self.assertFalse(restarted)
            self.assertIn(platform, reason)

    def test_macos_asks_launchctl_to_replace_the_process(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(autostart, "status",
                               return_value={"installed": True,
                                             "platform": "macos",
                                             "path": "/x"}):
            restarted, reason = autostart.restart(runner=runner)
        self.assertTrue(restarted, reason)
        self.assertEqual(calls[0][:3], ["launchctl", "kickstart", "-k"])

    def test_a_launchctl_failure_is_passed_back(self):
        def runner(_command, **_kwargs):
            return mock.Mock(returncode=3, stdout=b"", stderr=b"no such job")

        with mock.patch.object(autostart, "status",
                               return_value={"installed": True,
                                             "platform": "macos",
                                             "path": "/x"}):
            restarted, reason = autostart.restart(runner=runner)
        self.assertFalse(restarted)
        self.assertIn("no such job", reason)


class StopOnRequest(unittest.TestCase):
    """A running daemon has to be able to be told to stand down.

    Until now only the tray's Quit could end the loop, so a command-line
    restart had no way to reach a daemon at all on a machine without one.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.rules = os.path.join(self.directory, "rules.ini")
        with open(self.rules, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = yes\n\n[watch]\nfolders = %s\n"
                         "\n[rule: images]\nwhen = kind = image\ninto = %s/out\n"
                         % (self.directory, self.directory))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_a_quit_command_ends_the_wait(self):
        state = os.path.join(self.directory, "state.db")
        with daemon.PollingDaemon(rule_path=self.rules, state_file=state,
                                  port=0,
                                  output=lambda _m: None) as service:
            item = mock.Mock(available=False)
            with mock.patch.object(service.lock, "wait",
                                   return_value="quit"):
                service._wait_with_tray(5, item)
            self.assertTrue(service._quit_requested)

    def test_the_pid_is_recorded_so_a_restart_can_tell_them_apart(self):
        state = os.path.join(self.directory, "state.db")
        with daemon.PollingDaemon(rule_path=self.rules, state_file=state,
                                  port=0, output=lambda _m: None) as service:
            recorded = service.journal.get_state("daemon_pid")
        self.assertEqual(int(recorded), os.getpid())


if __name__ == "__main__":
    unittest.main(verbosity=2)
