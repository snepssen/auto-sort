"""Per-user autostart installation is explicit, reversible and local."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autostart                                         # noqa: E402


class Result(object):
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Autostart(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.rules = os.path.join(self.directory, "rules.ini")
        with open(self.rules, "w", encoding="utf-8") as handle:
            handle.write("[watch]\nfolders = /tmp\n")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_linux_desktop_file_is_opt_in_and_reversible(self):
        config = os.path.join(self.directory, "config")
        with mock.patch("autostart.platform_name", return_value="linux"), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config}, clear=False):
            before = autostart.status()
            self.assertFalse(before["installed"])
            installed = autostart.install(self.rules)
            self.assertTrue(installed["installed"])
            with open(installed["path"], encoding="utf-8") as handle:
                entry = handle.read()
            self.assertIn("[Desktop Entry]", entry)
            self.assertIn("watch", entry)
            removed = autostart.remove()
            self.assertFalse(removed["installed"])

    def test_macos_plist_uses_background_launchctl_scope(self):
        home = os.path.join(self.directory, "home")
        calls = []

        def runner(arguments, **_kwargs):
            calls.append(arguments)
            return Result()

        with mock.patch("autostart.platform_name", return_value="macos"), \
                mock.patch("autostart.os.path.expanduser",
                           side_effect=lambda value: value.replace("~", home, 1)), \
                mock.patch("autostart.os.getuid", return_value=501):
            installed = autostart.install(self.rules, runner)
            self.assertTrue(installed["installed"])
            self.assertEqual(calls[-1][:3], ["launchctl", "bootstrap", "gui/501"])
            autostart.remove(runner)
        self.assertFalse(os.path.exists(installed["path"]))

    def test_failed_activation_is_reported(self):
        home = os.path.join(self.directory, "home")
        with mock.patch("autostart.platform_name", return_value="macos"), \
                mock.patch("autostart.os.path.expanduser",
                           side_effect=lambda value: value.replace("~", home, 1)), \
                mock.patch("autostart.os.getuid", return_value=501):
            with self.assertRaises(autostart.AutostartError):
                autostart.install(self.rules,
                                  lambda *_args, **_kwargs: Result(1, stderr=b"bad"))


if __name__ == "__main__":
    unittest.main()
