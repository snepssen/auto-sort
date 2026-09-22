"""Eleven years of Firefox installers in one folder.

That is the canonical thing this tool exists for, and until now it read
`firefox-1.5.0.12.installer.exe` as a product called "firefox 1 5 0 12" with
no version at all, and filed it by the month it was downloaded.

Nothing here is a list of products. The product is whatever precedes the
version number in the file's own name, which is how installers have been
named since installers existed.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundles                                           # noqa: E402
import names                                             # noqa: E402
import paths                                             # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(filename, kind="app"):
    context = names.Context(filename, kind=kind)
    found = names.Found()
    names.detect_software(context, found)
    facts = dict((entry[0], entry[1]) for entry in found.facts)
    facts["_labels"] = [label[0] for label in found.labels]
    facts["_confidence"] = dict((entry[0], entry[2]) for entry in found.facts)
    return facts


class ReadingAVersion(unittest.TestCase):

    def test_a_version_followed_by_a_dotted_tag(self):
        """The bug: the guard said "ends here" and meant "ends the name"."""
        facts = read("firefox-1.5.0.12.installer.exe")
        self.assertEqual(facts["version"], "1.5.0.12")
        self.assertEqual(facts["product"], "firefox")

    def test_a_version_followed_by_a_release_channel(self):
        facts = read("firefox-52.9.0esr.win32.installer.exe")
        self.assertEqual(facts["version"], "52.9.0")
        self.assertEqual(facts["channel"], "esr")
        self.assertEqual(facts["product"], "firefox")

    def test_the_channels_that_were_already_understood_still_are(self):
        self.assertEqual(read("app-2.0-beta.exe")["channel"], "beta")
        self.assertEqual(read("tool-1.2.3-rc2.msi")["channel"], "rc2")

    def test_a_dotted_setup_word(self):
        facts = read("Thunderbird 78.6.0.setup.exe")
        self.assertEqual(facts["version"], "78.6.0")
        self.assertEqual(facts["product"], "Thunderbird")

    def test_the_ordinary_cases_are_unharmed(self):
        for filename, product, version in (
                ("Firefox Setup 115.0.2.exe", "Firefox", "115.0.2"),
                ("blender-4.2.1-windows-x64.msi", "blender", "4.2.1"),
                ("python-3.12.4-amd64.exe", "python", "3.12.4"),
                ("node-v20.11.1-x64.msi", "node", "20.11.1"),
                ("GIMP 2.10.34 setup.exe", "GIMP", "2.10.34")):
            facts = read(filename)
            self.assertEqual(facts["product"], product, filename)
            self.assertEqual(facts["version"], version, filename)

    def test_an_installers_extension_is_evidence_by_itself(self):
        """A .dmg is an installer whether or not its name admits it."""
        facts = read("AdobeReader_11.0.10.dmg", kind="disk-image")
        self.assertEqual(facts["product"], "AdobeReader")
        self.assertIn("installer", facts["_labels"])

    def test_an_archive_with_a_version_is_not_an_installer(self):
        """A release is not a setup program, and the extension says which."""
        facts = read("project-1.2.3.zip", kind="archive")
        self.assertEqual(facts["version"], "1.2.3")
        self.assertEqual(facts["_labels"], [])
        self.assertNotIn("product", facts)

    def test_two_signals_agreeing_clear_the_default_floor(self):
        """A product nobody can match on is a fact that does no work."""
        strong = read("firefox-1.5.0.12.installer.exe")
        self.assertGreaterEqual(strong["_confidence"]["product"], 0.6)
        # One signal only: still recorded, still below the floor, because a
        # cut with nothing corroborating it really is a guess.
        weak = read("Docker Desktop Installer.exe")
        self.assertLess(weak["_confidence"]["product"], 0.6)

    def test_a_name_with_no_version_at_all_invents_none(self):
        facts = read("7z2301-x64.exe")
        self.assertNotIn("version", facts)

    def test_a_date_is_not_a_version(self):
        self.assertNotIn("version", read("backup-2024.01.15.exe"))


class SpellingAFolderTheWayTheDiskDoes(unittest.TestCase):
    """`Firefox` and `firefox` are one folder to a person.

    Tested against a stubbed filesystem rather than a real one because the
    case that matters only exists on a case-sensitive filesystem, and the
    machine running these tests usually has the other kind.
    """

    def settled(self, wanted, existing):
        """`wanted`, resolved against a parent holding `existing` folders."""
        real = set(existing)

        def isdir(path):
            return path in real or path == "/base"

        with mock.patch.object(paths.os.path, "isdir", isdir), \
                mock.patch.object(paths, "_folders_in",
                                  return_value=dict(
                                      (name.rsplit("/", 1)[-1].lower(),
                                       name.rsplit("/", 1)[-1])
                                      for name in existing)):
            return paths.settled(wanted)

    def test_a_folder_that_exists_in_another_case_is_that_folder(self):
        self.assertEqual(
            self.settled("/base/firefox", ["/base/Firefox"]), "/base/Firefox")

    def test_an_exact_match_is_left_exactly_alone(self):
        self.assertEqual(
            self.settled("/base/Firefox", ["/base/Firefox"]), "/base/Firefox")

    def test_a_folder_that_does_not_exist_is_created_as_asked(self):
        self.assertEqual(
            self.settled("/base/blender", ["/base/Firefox"]), "/base/blender")

    def test_nothing_to_settle_against(self):
        self.assertEqual(self.settled("/base/vlc", []), "/base/vlc")

    def test_an_empty_path_is_returned_unchanged(self):
        self.assertEqual(paths.settled(""), "")

    def test_a_real_folder_is_recognised(self):
        directory = tempfile.mkdtemp()
        try:
            self.assertEqual(paths.settled(directory), directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_listing_is_not_repeated_for_every_file(self):
        """A dry run over a twenty-thousand-entry folder lists it once."""
        directory = tempfile.mkdtemp()
        try:
            paths._CASE_CACHE.clear()
            with mock.patch.object(paths.os, "scandir",
                                   wraps=paths.os.scandir) as listing:
                for _ in range(20):
                    paths.settled(os.path.join(directory, "Installers"))
            self.assertEqual(listing.call_count, 1)
        finally:
            paths._CASE_CACHE.clear()
            shutil.rmtree(directory, ignore_errors=True)


class FilingThemByWhatTheyAre(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-installers-")
        self.root = os.path.join(self.dir, "in")
        os.makedirs(self.root)
        self.rule_set = rules.load(os.path.join(REPO, "rules.example.ini"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def plan_for(self, *filenames):
        for filename in filenames:
            with open(os.path.join(self.root, filename), "wb") as handle:
                handle.write(b"MZ")
        items = list(bundles.walk(self.root, max_depth=1))
        plan = sorter.build_plan(self.root, self.rule_set, items=items)
        return dict((os.path.basename(item.item.primary),
                     (item.members[0].destination, item.rule_name))
                    for item in plan.items)

    def test_a_decade_of_one_program_lands_in_one_folder(self):
        placed = self.plan_for("firefox-1.5.0.12.installer.exe",
                               "Firefox Setup 115.0.2.exe")
        folders = set(os.path.dirname(where).lower()
                      for where, _rule in placed.values())
        self.assertEqual(len(folders), 1, placed)
        self.assertTrue(folders.pop().endswith("installers/firefox"))

    def test_one_that_admits_nothing_still_leaves_the_funnel(self):
        placed = self.plan_for("7z2301-x64.exe")
        where, rule = placed["7z2301-x64.exe"]
        self.assertEqual(rule, "installers")
        self.assertIn("Installers", where)


if __name__ == "__main__":
    unittest.main()
