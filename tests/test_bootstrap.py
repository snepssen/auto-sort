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


_RUNTIME_EXISTED = None


def setUpModule():
    global _RUNTIME_EXISTED
    _RUNTIME_EXISTED = os.path.exists(programs.runtime_dir())


def tearDownModule():
    """Nothing in this file may create the real runtime environment.

    Written after a test called `offer(assume_yes=True)` without stubbing the
    module list, which happily built a forty-megabyte virtual environment in
    the user's Application Support and pip-installed into it. Checked at the
    end of the module rather than inside one test, because the test that does
    the damage is not the test that would notice.

    It compares against what was there when the module started, because on a
    machine where somebody has actually installed the optional extras the
    directory exists legitimately and asserting it is absent would fail the
    suite for doing nothing wrong.
    """
    real = programs.runtime_dir()
    if os.path.exists(real) and not _RUNTIME_EXISTED:
        raise AssertionError(
            "the test run created %s -- stub programs.missing_modules or "
            "bootstrap.install_modules in whichever test calls offer()"
            % real)


class BootstrapTests(unittest.TestCase):
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
        with mock.patch.object(programs, "missing", return_value=[]), \
                mock.patch.object(programs, "missing_modules",
                                  return_value=[]):
            self.assertEqual(bootstrap.main(["--optional-check"]), 0)
        with mock.patch.object(programs, "missing",
                               return_value=[programs.PROGRAMS["ffprobe"]]), \
                mock.patch.object(programs, "missing_modules",
                                  return_value=[]):
            self.assertEqual(bootstrap.main(["--optional-check"]), 1)

    def test_a_missing_python_package_also_counts_as_something_to_offer(self):
        # Otherwise a Mac with no PyObjC is never asked, and the icon simply
        # never appears.
        with mock.patch.object(programs, "missing", return_value=[]), \
                mock.patch.object(
                    programs, "missing_modules",
                    return_value=[programs.MODULES["pyobjc"]]):
            self.assertEqual(bootstrap.main(["--optional-check"]), 1)

    def test_nothing_in_the_registry_is_required(self):
        # The whole point: sorting, learning and the ledger run on the
        # standard library, so every entry here is a capability upgrade.
        for module in programs.MODULES.values():
            self.assertFalse(module.required)

    def test_a_python_package_goes_into_our_own_environment(self):
        # Never into the Python that happens to be running: most system
        # Pythons refuse it outright under PEP 668, and the ones that allow
        # it should not be written to by a file sorter.
        fake = os.path.join(self.directory, "runtime", "bin", "python3")
        with mock.patch.object(programs, "runtime_python",
                               return_value=fake):
            command = programs.module_command(programs.MODULES["pyobjc"])
        self.assertEqual(command[0], fake)
        self.assertEqual(command[1:4], ["-m", "pip", "install"])
        self.assertIn("pyobjc-framework-Cocoa", command)

    def test_installing_never_needs_root_and_never_uses_user(self):
        fake = os.path.join(self.directory, "runtime", "bin", "python3")
        with mock.patch.object(programs, "runtime_python",
                               return_value=fake):
            command = programs.module_command(programs.MODULES["pyobjc"])
        self.assertNotIn("sudo", command)
        self.assertNotIn("--user", command,
                         "--user is refused on an externally managed Python "
                         "and shares a directory with everything else on one "
                         "that allows it")



    def test_the_offer_never_prompts_when_nobody_can_answer(self):
        # A launcher started by the system has no stdin. An input() there
        # hangs forever and takes the sorter down with it.
        with mock.patch.object(programs, "missing", return_value=[]), \
                mock.patch.object(
                    programs, "missing_modules",
                    return_value=[programs.MODULES["pyobjc"]]), \
                mock.patch.object(bootstrap, "install_modules") as install:
            self.assertTrue(bootstrap.offer(quiet=True))
        install.assert_not_called()

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
                mock.patch.object(programs, "missing_modules",
                                  return_value=[]), \
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
