"""Optional tray controls must never make the daemon unavailable."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                             # noqa: E402
import tray                                               # noqa: E402


class TrayAvailability(unittest.TestCase):

    def test_linux_without_status_notifier_is_explicitly_headless(self):
        with mock.patch("tray.sys.platform", "linux"):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("StatusNotifier", item.reason)

    def test_windows_backend_failure_falls_back_without_raising(self):
        with mock.patch("tray.sys.platform", "win32"), \
                mock.patch("tray._windows_tray", side_effect=OSError("Shell")):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("unavailable", item.reason)

    def test_macos_import_failure_falls_back_without_raising(self):
        with mock.patch("tray.sys.platform", "darwin"), \
                mock.patch("tray._mac_tray", side_effect=ImportError("PyObjC")):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("unavailable", item.reason)


class TrayActions(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.rules_file = os.path.join(self.directory, "rules.ini")
        self.state_file = os.path.join(self.directory, "state.db")
        inbox = os.path.join(self.directory, "inbox")
        os.makedirs(inbox)
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write("[watch]\nfolders = %s\n" % inbox)
        self.service = daemon.PollingDaemon(
            self.rules_file, self.state_file, port=0,
            output=lambda _message: None)

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_pause_sort_and_quit_actions_are_local_state_changes(self):
        self.assertFalse(self.service.journal.paused())
        self.service._tray_toggle_pause()
        self.assertTrue(self.service.journal.paused())
        self.service._tray_toggle_pause()
        self.assertFalse(self.service.journal.paused())
        self.service._tray_sort_now()
        self.assertTrue(self.service._sort_requested)
        self.service._tray_quit()
        self.assertTrue(self.service._quit_requested)

    def test_open_log_uses_the_daemon_owned_url(self):
        with mock.patch("daemon.webbrowser.open") as open_browser:
            self.service._tray_open_log()
        open_browser.assert_called_once_with(self.service.web.url)


if __name__ == "__main__":
    unittest.main()
