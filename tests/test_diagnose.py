"""`auto-sort diagnose`: what went wrong somewhere nobody here has been.

It is pasted into public issues, so the first thing held to is that it
names no file: not a watched path, not a rule -- rules are learnt from
somebody's own documents -- and not a name quoted in the daemon's log.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
        self.assertIn("refiling failed", problems[0])

    def test_real_log_shapes_cannot_leak_names_even_without_quotes(self):
        messages = [
            "Watched folder unavailable; queue retained: /media/Alice Smith/Tax Returns",
            "Watched folder unavailable; queue retained: C:\\Users\\Alice Smith\\Tax Returns",
            "Could not add Payslips Jansen (3) to the rules: permission denied",
            "Rules error; sorting paused: unknown section [Jansen Payroll]",
            "Could not look at filed files again: O'Brien's return.pdf failed",
            "ERROR 未知の給与明細.pdf alice@example.org secret-token",
            "Traceback (most recent call last):",
            "  File '/home/Alice Smith/Payroll.py', line 4",
            "    raise ValueError('Payroll secret')",
        ]
        with open(os.path.join(self.log_dir, "daemon.log"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(messages))
        found = self.collect()
        for text in (diagnose.render(found), json.dumps(found), diagnose.report_text(found)):
            for private in ("Alice", "Smith", "Tax Returns", "Jansen", "Payroll",
                            "O'Brien", "給与", "alice@", "secret-token", "return.pdf"):
                self.assertNotIn(private, text)
            self.assertIn("watched folder unavailable", text)
            self.assertIn("writing learned rules failed", text)
            self.assertIn("rules invalid; sorting paused", text)

    def test_redirected_folders_are_states_not_paths(self):
        with mock.patch("userdirs.all_dirs", return_value={
                "documents": self.dir,
                "downloads": "/mnt/Alice Smith/Tax Returns",
                "private-folder-name": "also-private"}):
            found = self.collect()["standard folders"]
        self.assertEqual(found["documents"], "available")
        self.assertEqual(found["downloads"], "missing or inaccessible")
        self.assertNotIn("private-folder-name", found)
        self.assertNotIn(self.dir, json.dumps(found))
        self.assertNotIn("Alice", json.dumps(found))

    def test_invalid_rules_never_export_parser_error_text(self):
        with open(self.rules, "w") as handle:
            handle.write("[Alice Payroll]\nsecret = medical records\n")
        found = self.collect()["rules"]
        self.assertTrue(found["loads"].startswith("no"))
        self.assertNotIn("Alice", json.dumps(found))
        self.assertNotIn("medical", json.dumps(found))

    def test_saved_tray_errors_and_unexpected_fields_are_filtered(self):
        with ledger.Ledger(self.state) as journal:
            journal.set_state("tray_report", json.dumps({
                "backend": "StatusNotifierItem", "available": False,
                "registered": True, "watcher": "org.kde.StatusNotifierWatcher",
                "reason": "native tray unavailable: /home/Alice Payroll/private.so",
                "extra-private-field": "medical records",
                "host_asked": {"ContextMenu": 3, "GetLayout": "Alice", "Alice": 7}}))
        found = self.collect()["daemon"]["tray"]
        self.assertEqual(found["host_asked"], {"ContextMenu": 3})
        self.assertTrue(found["registered"])
        self.assertEqual(found["watcher"], "org.kde.StatusNotifierWatcher")
        for private in ("Alice", "Payroll", "private.so", "medical", "extra-private"):
            self.assertNotIn(private, json.dumps(found))


class SystemMetadata(unittest.TestCase):

    def test_known_desktop_details_survive_but_custom_names_do_not(self):
        with mock.patch.dict(os.environ, {
                "XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "XDG_SESSION_TYPE": "wayland",
                "DESKTOP_SESSION": "/home/Alice/custom-session",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/Alice/private-bus"}):
            found = diagnose._desktop()
        self.assertEqual(found["XDG_CURRENT_DESKTOP"], "ubuntu:gnome")
        self.assertEqual(found["XDG_SESSION_TYPE"], "wayland")
        self.assertEqual(found["DBUS_SESSION_BUS_ADDRESS"], "set")
        self.assertNotIn("Alice", json.dumps(found))
        self.assertNotIn("custom-session", json.dumps(found))

    def test_os_metadata_drops_custom_distribution_and_kernel_names(self):
        release = 'ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Alice Payroll Linux"\n'
        with mock.patch("diagnose.sys.platform", "linux"), \
                mock.patch("builtins.open", mock.mock_open(read_data=release)), \
                mock.patch("platform.release", return_value="6.8.0-Alice-Payroll"):
            self.assertEqual(diagnose._system(), "ubuntu 24.04, Linux 6.8.0")
        with mock.patch("builtins.open", mock.mock_open(
                read_data='ID=Alice\nVERSION_ID="Payroll"\n')):
            self.assertEqual(diagnose._os_release(), "other/omitted unknown")


class OnTheDesktop(unittest.TestCase):
    """REPORT-A-PROBLEM: a file somebody can find, fill in and send."""

    def test_what_happened_comes_first_and_nothing_is_overwritten(self):
        desktop = tempfile.mkdtemp(prefix="autosort-desktop-")
        self.addCleanup(shutil.rmtree, desktop, True)
        found = {"auto-sort": "0.9.0", "daemon": {"state": "running"}}
        first = diagnose.write_to_desktop(found, desktop, now=0)
        second = diagnose.write_to_desktop(found, desktop, now=0)
        self.assertNotEqual(first, second)
        self.assertEqual(len(os.listdir(desktop)), 2)
        with open(first, encoding="utf-8") as handle:
            text = handle.read()
        self.assertLess(text.index("WHAT HAPPENED?"),
                        text.index("HOW TO SEND IT"))
        self.assertLess(text.index("HOW TO SEND IT"),
                        text.index("auto-sort: 0.9.0"))
        self.assertIn(diagnose.ISSUES, text)


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

    def test_bus_errors_do_not_export_socket_paths(self):
        with mock.patch("dbuswire.Connection", side_effect=OSError(
                13, "permission denied", "/home/Alice Payroll/private-bus")):
            found = diagnose._bus()
        self.assertIn("OS error 13", found["reachable"])
        self.assertNotIn("Alice", json.dumps(found))

    def test_bus_responses_are_not_trusted_as_public_text(self):
        bus = mock.Mock()
        bus.call.side_effect = lambda *args, **kwargs: (
            ["Alice Payroll"] if kwargs["member"] == "GetNameOwner" else
            [dbuswire.Variant("s", "Alice Payroll")] if kwargs["member"] == "Get" else
            ["Alice Payroll", "Private Vendor", "/home/Alice", "Private Spec"])
        found = diagnose._bus(bus)
        text = json.dumps(found)
        self.assertNotIn("Alice", text)
        self.assertNotIn("Private", text)
        self.assertIn("host registered: unknown", text)


@unittest.skipIf(os.name == "nt", "POSIX launcher")
class ShellFallback(unittest.TestCase):
    """Run the real launcher against a broken installation, without a desktop."""

    def test_failed_report_keeps_stderr_and_environment_out_of_the_attachment(self):
        with tempfile.TemporaryDirectory(prefix="autosort-report-") as directory:
            script = os.path.join(directory, "REPORT-A-PROBLEM.sh")
            shutil.copyfile(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                        "REPORT-A-PROBLEM.sh"), script)
            commands = os.path.join(directory, "commands")
            os.mkdir(commands)
            desktop = os.path.join(directory, "Alice Payroll")
            os.mkdir(desktop)
            programs = {
                "python3": '#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\n'
                           'echo "PermissionError: /mnt/Alice Payroll/secret.pdf" >&2\nexit 1\n',
                "python": '#!/bin/sh\nexit 1\n',
                "xdg-user-dir": '#!/bin/sh\nprintf "%s\\n" "$REPORT_TEST_DESKTOP"\n',
                "xdg-open": '#!/bin/sh\nexit 0\n',
                "open": '#!/bin/sh\nexit 0\n',
                "uname": '#!/bin/sh\necho "Linux"\n',
            }
            for name, contents in programs.items():
                target = os.path.join(commands, name)
                with open(target, "w") as handle:
                    handle.write(contents)
                os.chmod(target, 0o755)
            environment = dict(os.environ, PATH=commands + os.pathsep + os.defpath,
                               REPORT_TEST_DESKTOP=desktop,
                               XDG_CURRENT_DESKTOP="Alice Payroll",
                               XDG_SESSION_TYPE="/private/secret-session")
            for available in (True, False):
                with self.subTest(python_available=available):
                    if not available:
                        shutil.copyfile(os.path.join(commands, "python"),
                                        os.path.join(commands, "python3"))
                    result = subprocess.run(["/bin/sh", script], env=environment,
                                            capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    reports = os.listdir(desktop)
                    self.assertEqual(len(reports), 1)
                    with open(os.path.join(desktop, reports[0])) as handle:
                        text = handle.read()
                    for private in ("Alice", "Payroll", "secret.pdf", "secret-session"):
                        self.assertNotIn(private, text)
                    self.assertIn("system: Linux", text)
                    self.assertIn("full report: failed" if available else
                                  "full report: unavailable without Python", text)
                    os.unlink(os.path.join(desktop, reports[0]))


@unittest.skipUnless(os.name == "nt", "Windows launcher; runs on Windows CI")
class WindowsFallback(unittest.TestCase):

    def test_broken_install_does_not_attach_its_traceback(self):
        with tempfile.TemporaryDirectory(prefix="autosort-report-") as directory:
            script = os.path.join(directory, "REPORT-A-PROBLEM.bat")
            shutil.copyfile(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                        "REPORT-A-PROBLEM.bat"), script)
            with open(os.path.join(directory, "autosort.py"), "w") as handle:
                handle.write("raise RuntimeError('Alice Payroll secret.pdf')\n")
            desktop = os.path.join(directory, "Alice Payroll")
            os.mkdir(desktop)
            # Keep the report and the text-file opener away from the user's
            # real desktop. Python and the batch script itself run for real.
            commands = os.path.join(directory, "commands")
            os.mkdir(commands)
            with open(os.path.join(commands, "powershell.cmd"), "w") as handle:
                handle.write('@echo off\n'
                             'if "%~3" == "[Environment]::GetFolderPath(\'Desktop\')" goto desktop\n'
                             'echo 2026-01-01 0000\nexit /b\n'
                             ':desktop\necho %REPORT_TEST_DESKTOP%\n')
            with open(os.path.join(commands, "notepad.cmd"), "w") as handle:
                handle.write('@exit\n')
            environment = dict(os.environ, REPORT_TEST_DESKTOP=desktop)
            environment["PATH"] = os.pathsep.join((commands, os.path.dirname(sys.executable),
                                                   os.environ.get("PATH", "")))
            result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/c", script],
                                    env=environment, input="\n", capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            reports = os.listdir(desktop)
            self.assertEqual(len(reports), 1)
            with open(os.path.join(desktop, reports[0])) as handle:
                text = handle.read()
            self.assertIn("full report: failed", text)
            self.assertIn("system: Windows", text)
            for private in ("Alice", "Payroll", "secret.pdf", "Traceback", directory):
                self.assertNotIn(private, text)


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
