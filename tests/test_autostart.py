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
            # The menu entry starts what it opens, with the same rules.
            with open(autostart.launcher_target(), encoding="utf-8") as handle:
                launcher = handle.read()
            self.assertIn('"open-log" "--start" "--rules"', launcher)
            self.assertIn(autostart._desktop_quote(self.rules), launcher)
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


def _read_exec(value):
    """An Exec value read back the way the desktop entry spec says a
    launcher must: string escapes first, then field codes, then quoting."""
    unescaped, index = [], 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            unescaped.append({"s": " ", "n": "\n", "t": "\t", "r": "\r",
                              "\\": "\\"}.get(nxt, "\\" + nxt))
            index += 2
        else:
            unescaped.append(value[index])
            index += 1
    text = "".join(unescaped)
    arguments, current, quoted, index = [], None, False, 0
    while index < len(text):
        char = text[index]
        if char == "%":
            if text[index + 1:index + 2] != "%":
                raise ValueError("field code in %r" % value)
            current = (current or "") + "%"
            index += 2
            continue
        if quoted and char == "\\":
            current += text[index + 1]
            index += 2
            continue
        if char == '"':
            quoted = not quoted
            current = current or ""
        elif char == " " and not quoted:
            if current is not None:
                arguments.append(current)
            current = None
        else:
            if quoted and char in "`$":
                raise ValueError("unescaped %r in %r" % (char, value))
            current = (current or "") + char
        index += 1
    if current is not None:
        arguments.append(current)
    return arguments


class DesktopEntryQuoting(unittest.TestCase):
    """A path that reaches an Exec line has to come out of it unchanged.

    Only `"` and `\\` were escaped, and only once. A rules file under a
    folder called `100%` wrote a field code, `$` and a backtick were left
    for a shell to expand, and desktop-file-validate rejected all three --
    which is how an autostart entry quietly does not start anything.
    """

    AWKWARD = ["/home/John Smith/rules.ini", "/home/x/100%/rules.ini",
               '/home/x/it"s/rules.ini', "/home/x/$HOME/rules.ini",
               "/home/x/a`b/rules.ini", "/home/x/back\\slash/rules.ini"]

    def exec_line(self, arguments):
        entry = autostart._desktop_entry(arguments)
        return [line[len("Exec="):] for line in entry.splitlines()
                if line.startswith("Exec=")][0]

    def test_every_path_comes_back_as_it_went_in(self):
        for path in self.AWKWARD:
            arguments = ["/usr/bin/python3", path, "watch"]
            self.assertEqual(_read_exec(self.exec_line(arguments)),
                             arguments, path)

    def test_the_spec_spellings(self):
        self.assertEqual(autostart._desktop_quote("100%"), '"100%%"')
        self.assertEqual(autostart._desktop_quote("$HOME"), '"\\\\$HOME"')
        self.assertEqual(autostart._desktop_quote("a`b"), '"a\\\\`b"')
        self.assertEqual(autostart._desktop_quote('it"s'), '"it\\\\"s"')
        self.assertEqual(autostart._desktop_quote("a\\b"), '"a\\\\\\\\b"')


if __name__ == "__main__":
    unittest.main()
