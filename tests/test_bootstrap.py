"""Optional setup never becomes a runtime requirement."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap                                           # noqa: E402
import platform_support as programs                        # noqa: E402


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        programs.forget()

    def tearDown(self):
        programs.forget()

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


if __name__ == "__main__":
    unittest.main()
