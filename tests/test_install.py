"""The installer: one pasted line, for somebody who has never used a terminal.

What it must never do is the thing a careless installer does: replace a
folder it did not make, trust an archive that writes outside its folder, or
lose the rules and history on an update. And the whole thing is run once,
into a throwaway home, from an archive of this checkout.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import install                                           # noqa: E402


def archive(files, top="auto-sort-main"):
    """A zip laid out as GitHub's branch archives are: one top folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, (text, mode) in files.items():
            info = zipfile.ZipInfo("%s/%s" % (top, name) if top else name)
            info.external_attr = (mode | stat.S_IFREG) << 16
            bundle.writestr(info, text)
    return buffer.getvalue()


PROGRAM = {"autosort.py": ("print('auto-sort')\n", 0o644),
           "start.sh": ("#!/bin/sh\n", 0o755)}


class Unpacking(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-install-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_top_folder_is_found_and_launchers_stay_executable(self):
        staging, top = install.unpack(archive(PROGRAM), self.dir)
        self.assertTrue(os.path.isfile(os.path.join(top, "autosort.py")))
        if os.name != "nt":
            self.assertTrue(os.stat(os.path.join(top, "start.sh")).st_mode
                            & stat.S_IXUSR)
        self.assertTrue(top.startswith(staging))

    def test_a_path_outside_the_folder_stops_it(self):
        bad = dict(PROGRAM)
        bad["../../escaped.txt"] = ("x", 0o644)
        with self.assertRaises(install.InstallError):
            install.unpack(archive(bad), self.dir)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "escaped.txt")))
        self.assertEqual([name for name in os.listdir(self.dir)], [])

    def test_something_that_is_not_auto_sort_stops_it(self):
        with self.assertRaises(install.InstallError):
            install.unpack(archive({"README": ("hello", 0o644)}), self.dir)
        with self.assertRaises(install.InstallError):
            install.unpack(b"not a zip", self.dir)
        self.assertEqual(os.listdir(self.dir), [])


class Placing(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-place-")
        self.folder = os.path.join(self.dir, "auto-sort")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def new(self, marker_text="v2"):
        staging, top = install.unpack(archive(dict(
            PROGRAM, **{"VERSION": (marker_text, 0o644)})), self.dir)
        return staging, top

    def test_a_folder_it_did_not_make_is_left_alone(self):
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "notes.txt"), "w") as handle:
            handle.write("mine")
        _staging, top = self.new()
        with self.assertRaises(install.InstallError):
            install.place(top, self.folder)
        with open(os.path.join(self.folder, "notes.txt")) as handle:
            self.assertEqual(handle.read(), "mine")

    def test_an_update_keeps_the_previous_version_and_the_rules(self):
        _staging, top = self.new("v1")
        install.place(top, self.folder)
        with open(os.path.join(self.folder, "rules.ini"), "w") as handle:
            handle.write("[settings]\n")
        _staging, top = self.new("v2")
        install.place(top, self.folder)
        with open(os.path.join(self.folder, "VERSION")) as handle:
            self.assertEqual(handle.read(), "v2")
        with open(os.path.join(self.folder + ".previous", "VERSION")) as h:
            self.assertEqual(h.read(), "v1")
        self.assertTrue(os.path.exists(os.path.join(self.folder,
                                                    "rules.ini")))
        self.assertTrue(os.path.exists(os.path.join(self.folder,
                                                    install.MARKER)))


class Asking(unittest.TestCase):

    def test_nobody_there_means_the_default(self):
        with mock.patch("sys.stdin", io.StringIO("")):
            self.assertTrue(install.ask("Start at login?", False))
            self.assertFalse(install.ask("Something risky?", False,
                                         default=False))
        self.assertTrue(install.ask("anything", True))

    def test_each_system_has_its_own_place(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertTrue(install.default_folder().endswith(
                os.path.join("Applications", "auto-sort")))
        with mock.patch("sys.platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg"}):
            self.assertEqual(install.default_folder(),
                             os.path.join("/xdg", "auto-sort"))
        with mock.patch("sys.platform", "win32"), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\L"}):
            self.assertTrue(install.default_folder().endswith(
                os.path.join("Programs", "auto-sort")))


class TheWholeThing(unittest.TestCase):
    """This checkout, installed into a throwaway home, from nothing."""

    def test_install_then_update(self):
        home = tempfile.mkdtemp(prefix="autosort-home-")
        self.addCleanup(shutil.rmtree, home, True)
        files = subprocess.run(["git", "-C", HERE, "ls-files"],
                               capture_output=True, text=True)
        if files.returncode != 0:
            self.skipTest("needs a git checkout to archive")
        bundle = os.path.join(home, "auto-sort-main.zip")
        with zipfile.ZipFile(bundle, "w") as out:
            for name in files.stdout.split("\n"):
                if name and os.path.isfile(os.path.join(HERE, name)):
                    out.write(os.path.join(HERE, name),
                              "auto-sort-main/" + name)
        folder = os.path.join(home, "apps", "auto-sort")
        environment = dict(os.environ, HOME=home, USERPROFILE=home,
                           XDG_CONFIG_HOME=os.path.join(home, ".config"),
                           XDG_STATE_HOME=os.path.join(home, ".state"),
                           XDG_DATA_HOME=os.path.join(home, ".data"),
                           APPDATA=os.path.join(home, "AppData"),
                           LOCALAPPDATA=os.path.join(home, "Local"))
        command = [sys.executable, os.path.join(HERE, "install.py"),
                   "--folder", folder, "--archive", bundle, "--yes",
                   "--no-tools", "--no-login", "--no-start"]
        for _attempt in ("install", "update"):
            done = subprocess.run(command, env=environment,
                                  capture_output=True, text=True,
                                  timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.isfile(os.path.join(folder, "autosort.py")))
        self.assertTrue(os.path.isdir(folder + ".previous"))
        self.assertIn("Updating", done.stdout)
        check = subprocess.run(
            [sys.executable, os.path.join(folder, "autosort.py"),
             "check-rules"], env=environment, capture_output=True,
            text=True, timeout=120)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("dry run on", check.stdout)


if __name__ == "__main__":
    unittest.main()
