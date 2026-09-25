"""`auto-sort diagnose`: what went wrong somewhere nobody here has been.

It is pasted into public issues, so the first thing held to is that it
names no file: not a watched path, not a rule -- rules are learnt from
somebody's own documents -- and not a name quoted in the daemon's log.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                            # noqa: E402
import dbuswire                                          # noqa: E402
import diagnose                                          # noqa: E402
import ledger                                            # noqa: E402
import tray                                              # noqa: E402

RULES = """
[settings]
dry_run = no

[watch]
folders = %(root)s

[rule: what the page calls itself: Loonbrief Jansen]
when = heading contains Loonbrief
into = %(root)s/Loonbrief Jansen
"""


class NamesNoFile(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-diagnose-")
        self.state = os.path.join(self.dir, "state.db")
        self.rules = os.path.join(self.dir, "rules.ini")
        with open(self.rules, "w") as handle:
            handle.write(RULES % {"root": self.dir})
        self.log_dir = os.path.join(self.dir, "state")
        os.makedirs(self.log_dir)
        with open(os.path.join(self.log_dir, "daemon.log"), "w") as handle:
            for _ in range(3):
                handle.write("2026-09-25 10:00:0%d Could not look at filed "
                             "files again: [Errno 2] No such file: "
                             "'/home/someone/Documents/Payslip Jansen.pdf'\n"
                             % _)
            handle.write("2026-09-25 10:01:00 Sorted 3 files from "
                         "/home/someone/Downloads\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def collect(self):
        with mock.patch("paths.state_dir", return_value=self.log_dir):
            return diagnose.collect(self.state, self.rules,
                                    bus=_Bus(watchers=True))

    def test_the_report_says_nothing_about_whose_files(self):
        text = diagnose.render(self.collect())
        for secret in ("Jansen", "Loonbrief", "Payslip", "/home/someone",
                       self.dir, os.path.expanduser("~") + os.sep):
            self.assertNotIn(secret, text)
        self.assertIn("rules: 1", text)
        self.assertTrue(text.startswith("```text"))

    def test_a_complaint_repeated_is_one_line_with_a_count(self):
        problems = self.collect()["recent problems in the log"]
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].endswith("(x3)"), problems)
        self.assertIn("<name>", problems[0])

    def test_paths_and_quoted_names_are_taken_out(self):
        self.assertEqual(
            diagnose.redact("moved /a/b/c.pdf and 'x y.txt' to C:\\Users\\z"),
            "moved <path> and <name> to <path>")


class TheSessionBus(unittest.TestCase):
    """What a Linux report says about the tray's side of the bus."""

    def test_who_is_there_and_who_is_not(self):
        found = diagnose._bus(_Bus(watchers=True))
        self.assertEqual(found["reachable"], "yes")
        self.assertIn(":1.9", found["org.kde.StatusNotifierWatcher"])
        self.assertIn("host registered: True",
                      found["org.kde.StatusNotifierWatcher"])
        self.assertEqual(found["org.freedesktop.StatusNotifierWatcher"],
                         "nobody")
        self.assertIn("Plasma", found["notifications"])

    def test_a_desktop_with_no_tray_at_all(self):
        found = diagnose._bus(_Bus(watchers=False))
        self.assertEqual(found["org.kde.StatusNotifierWatcher"], "nobody")

    def test_no_bus(self):
        with mock.patch("dbuswire.Connection",
                        side_effect=dbuswire.DBusError("no session bus")):
            self.assertIn("no", diagnose._bus()["reachable"])


class TheDaemonKeepsTheTrayReport(unittest.TestCase):

    def test_what_the_tray_saw_reaches_the_ledger(self):
        directory = tempfile.mkdtemp(prefix="autosort-trayreport-")
        self.addCleanup(shutil.rmtree, directory, True)
        rules_file = os.path.join(directory, "rules.ini")
        with open(rules_file, "w") as handle:
            handle.write("[settings]\ndry_run = yes\n[watch]\nfolders = %s\n"
                         % directory)
        state = os.path.join(directory, "state.db")
        with daemon.PollingDaemon(rules_file, state, port=0,
                                  output=[].append) as service:
            service._keep_tray_report(tray.UnavailableTray("no host here"))
        with ledger.Ledger(state) as journal:
            report = json.loads(journal.get_state("tray_report"))
        self.assertEqual(report, {"available": False, "backend": "none",
                                  "reason": "no host here"})


class _Bus(object):
    """Just enough session bus to answer what `diagnose` asks."""

    closed = False

    def __init__(self, watchers):
        self.watchers = watchers

    def call(self, destination, path, interface, member, signature="",
             body=(), timeout=None):
        if member == "GetNameOwner":
            if self.watchers and body[0] == "org.kde.StatusNotifierWatcher":
                return [":1.9"]
            raise dbuswire.DBusError("NameHasNoOwner")
        if member == "Get":
            return [dbuswire.Variant("b", True)]
        if member == "GetServerInformation":
            return ["Plasma", "KDE", "6.7.3", "1.2"]
        raise dbuswire.DBusError("UnknownMethod")

    def close(self):
        self.closed = True


if __name__ == "__main__":
    unittest.main()
