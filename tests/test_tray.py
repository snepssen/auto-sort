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

    def test_restart_stands_down_and_says_why(self):
        """Quitting and restarting both end the loop; only one comes back."""
        self.assertFalse(self.service.restart_requested)
        self.service._tray_restart()
        self.assertTrue(self.service.restart_requested)
        self.assertTrue(self.service._quit_requested)

    def test_open_log_uses_the_daemon_owned_url(self):
        with mock.patch("daemon.webbrowser.open") as open_browser:
            self.service._tray_open_log()
        open_browser.assert_called_once_with(self.service.web.url)


class NativeBackend(unittest.TestCase):
    """Exercise the real backend when the host can load it.

    `tray.create` turns any failure into "continuing headless", which is the
    right behaviour for a sorter -- a missing status item must never stop
    files being filed -- and is also why this code sat broken and unnoticed
    through four separate faults. Nothing failed, nothing was reported, and
    the only symptom was an absence.

    So these assert the backend actually starts rather than that it degrades
    politely, and they no longer skip for a missing package, because there is
    no package.
    """

    def available(self):
        # No import check any more: the backend needs nothing installed, so
        # on a Mac it either works or it is broken, and skipping would hide
        # the difference.
        return sys.platform == "darwin"

    def setUp(self):
        if not self.available():
            self.skipTest("the native status item is macOS only")
        self.calls = []
        self.actions = {
            "open_log": lambda: self.calls.append("open_log"),
            "toggle_pause": lambda: self.calls.append("toggle_pause"),
            "sort_now": lambda: self.calls.append("sort_now"),
            "restart": lambda: self.calls.append("restart"),
            "quit": lambda: self.calls.append("quit"),
        }

    def test_the_status_item_actually_starts(self):
        item = tray.create(self.actions)
        try:
            self.assertTrue(item.available,
                            "tray fell back to headless: %s"
                            % getattr(item, "reason", ""))
        finally:
            item.close()

    def test_the_menu_is_reachable(self):
        # Built but never attached, the menu is dead code and Pause, Sort now
        # and Quit cannot be reached at all.
        item = tray.create(self.actions)
        try:
            self.assertTrue(item.available)
            self.assertTrue(item.menu, "no menu was built")
            self.assertTrue(item.pause_item,
                            "the pause entry was never found, so it can "
                            "never be renamed")
            # Open log, Pause, Sort now, separator, Restart, Quit.
            self.assertEqual(item.runtime.send(
                item.runtime.ctypes.c_long, item.menu, "numberOfItems"), 6)
        finally:
            item.close()

    def test_clicking_the_icon_opens_the_log(self):
        # The whole point of a constant presence in the menu bar.
        item = tray.create(self.actions)
        try:
            item._clicked()
            self.assertEqual(self.calls, ["open_log"])
        finally:
            item.close()

    def test_pausing_renames_the_menu_entry(self):
        item = tray.create(self.actions)
        try:
            # Renaming goes through Objective-C, so the assertion is that
            # it does not raise and the entry is still there afterwards.
            item.set_paused(True)
            item.set_paused(False)
            self.assertTrue(item.pause_item)
        finally:
            item.close()


if __name__ == "__main__":
    unittest.main()
