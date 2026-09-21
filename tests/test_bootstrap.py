"""Optional setup never becomes a runtime requirement."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap                                           # noqa: E402
import platform_support as programs                        # noqa: E402



class BootstrapTests(unittest.TestCase):
    """The claim this file defends: auto-sort needs nothing installed."""

    def test_there_are_no_python_dependencies_at_all(self):
        # The menu bar icon was the only one, for about a day. It is built on
        # the Objective-C runtime through ctypes now, the same way the
        # Windows tray is built on Shell_NotifyIcon, so the list is empty and
        # is meant to stay that way.
        self.assertEqual(programs.MODULES, {})
        self.assertEqual(programs.missing_modules(), [])

    def setUp(self):
        programs.forget()
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        programs.forget()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_every_program_is_optional(self):
        self.assertTrue(programs.PROGRAMS)
        self.assertFalse(any(program.required for program in programs.PROGRAMS.values()))
        self.assertEqual(bootstrap.main(["--check"]), 0)

    def test_optional_check_distinguishes_a_missing_capability(self):
        with mock.patch.object(programs, "missing", return_value=[]):
            self.assertEqual(bootstrap.main(["--optional-check"]), 0)
        with mock.patch.object(programs, "missing",
                               return_value=[programs.PROGRAMS["ffprobe"]]):
            self.assertEqual(bootstrap.main(["--optional-check"]), 1)

    def test_package_commands_are_data_driven(self):
        ffprobe = programs.PROGRAMS["ffprobe"]
        self.assertEqual(bootstrap.command_for(ffprobe, "brew"),
                         ["brew", "install", "ffmpeg"])
        self.assertEqual(bootstrap.command_for(ffprobe, "apt"),
                         ["sudo", "apt-get", "install", "-y", "ffmpeg"])

    def test_root_manager_is_print_only(self):
        with mock.patch.object(programs, "current_manager", return_value="apt"), \
                mock.patch.object(programs, "missing",
                                  return_value=[programs.PROGRAMS["ffprobe"]]), \
                mock.patch.object(bootstrap, "install") as install:
            self.assertTrue(bootstrap.offer(assume_yes=True, quiet=True))
        install.assert_not_called()

    def test_missing_optional_programs_do_not_make_offer_fail(self):
        with mock.patch.object(programs, "current_manager", return_value=None), \
                mock.patch.object(programs, "missing",
                                  return_value=[programs.PROGRAMS["exiftool"]]):
            self.assertTrue(bootstrap.offer(quiet=True))

    def test_immutable_root_warns_that_the_printed_command_will_fail(self):
        # A stock SteamOS reports "pacman" as the manager and needs_root is
        # True, so the naive message is a command that looks runnable and
        # is not: pacman can't write to a locked root without an explicit
        # unlock first. That gap is exactly what steamos-readonly signals.
        printed = []
        # offer() prints via builtins.print in quiet=False mode; capture it.
        with mock.patch("builtins.print", side_effect=lambda *a: printed.append(" ".join(str(x) for x in a))), \
                mock.patch.object(programs, "current_manager", return_value="pacman"), \
                mock.patch.object(programs, "missing",
                                  return_value=[programs.PROGRAMS["exiftool"]]), \
                mock.patch.object(programs, "immutable_root", return_value=True):
            bootstrap.offer(assume_yes=True, quiet=False)
        joined = "\n".join(printed)
        self.assertIn("unlocked", joined)
        self.assertIn("sudo pacman", joined)

    def test_immutable_root_detects_steamos_readonly_binary(self):
        with mock.patch.object(shutil, "which",
                               side_effect=lambda name: "/usr/bin/steamos-readonly"
                               if name == "steamos-readonly" else None):
            self.assertTrue(programs.immutable_root())
        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(os.path, "exists", return_value=False):
            self.assertFalse(programs.immutable_root())


if __name__ == "__main__":
    unittest.main()
