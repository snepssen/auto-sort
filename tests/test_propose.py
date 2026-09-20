"""Site conventions, and structure derived from a folder rather than planned.

Two things are tested here that are easy to get wrong in opposite directions.

A site detector that is too eager is worse than none: `focus10advanced` is
fifteen characters of letters and digits and is a word, not a Twitter media
id, and claiming it would scatter files into a folder for a site they never
came from. Several cases below are names that must *not* match.

A proposal that is too eager is worse than none for the same reason. One
prolific artist and twenty one-offs is not a structure, and a tool that
cheerfully makes twenty-one folders has turned a pile into a deeper pile. The
median test is what refuses that, and it is tested from both sides.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures                                          # noqa: E402
import identify                                          # noqa: E402
import names                                             # noqa: E402
import propose                                           # noqa: E402
import rules                                             # noqa: E402
import sites                                             # noqa: E402


class SiteConventions(unittest.TestCase):

    def read(self, filename, kind="image"):
        stem, _, ext = filename.rpartition(".")
        name, found = sites.read(stem or filename, ext, kind)
        return name, dict((fact, value) for fact, (value, _c) in found.items())

    def test_furaffinity_carries_the_artist(self):
        name, found = self.read("1770665382.flaich_illustration16.png")
        self.assertEqual(name, "furaffinity")
        self.assertEqual(found["uploader"], "flaich")
        self.assertEqual(found["post_id"], "1770665382")

    def test_furaffinity_uploader_ends_at_the_first_underscore(self):
        _name, found = self.read(
            "1497725735.multyashka-sweet_by_multyashka-_sweet_"
            "[resized_for_web].jpg")
        self.assertEqual(found["uploader"], "multyashka-sweet")

    def test_a_checksum_name_is_marked_as_carrying_nothing(self):
        name, found = self.read("9212888c5c4816ac0d7fc2be7baa8027.webm",
                                "video")
        self.assertEqual(name, "hash")
        self.assertTrue(found["opaque"])
        self.assertEqual(found["hash_algorithm"], "md5")
        self.assertNotIn("uploader", found)

    def test_twitter_id(self):
        name, found = self.read("GzVwz2cXEAIcI4s.jpeg")
        self.assertEqual(name, "twitter")
        self.assertTrue(found["opaque"])

    def test_words_are_not_twitter_ids(self):
        # Every one of these is fifteen characters and none is an id.
        for stem in ("focus10advanced", "budget2026final", "holiday2026pic1",
                     "chapter12draft3"):
            name, _found = self.read(stem + ".jpg")
            self.assertIsNone(name, stem)

    def test_pixiv_and_deviantart(self):
        self.assertEqual(self.read("98765432_p0_master1200.jpg")[0], "pixiv")
        name, found = self.read("My Art_by_SomeArtist_d9abcdef.png")
        self.assertEqual(name, "deviantart")
        self.assertEqual(found["uploader"], "someartist")

    def test_booru_tag_lists_lead_with_the_artist(self):
        _name, found = self.read(
            "__artistname_canine_male__a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.jpg")
        self.assertEqual(found["uploader"], "artistname")
        self.assertIn("canine", found["tags"])

    def test_documents_are_not_site_downloads(self):
        self.assertIsNone(self.read("1770665382.flaich_report.pdf",
                                    "document")[0])

    def test_the_detectors_reach_the_record(self):
        found = dict((n, v) for n, v, _c, _s
                     in names.read("1770665382.flaich_art.png",
                                   "image").facts)
        self.assertEqual(found["uploader"], "flaich")
        self.assertEqual(found["site"], "furaffinity")


class Proposals(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def make(self, name, maker=None, **kwargs):
        maker = maker or fixtures.png
        path = maker(os.path.join(self.directory, name), **kwargs)
        old = time.time() - 600
        os.utime(path, (old, old))
        return path

    def assess(self, **kwargs):
        found = propose.survey(self.directory, tier=identify.TIER_ALL,
                               **kwargs)
        return found, dict((p.key, p) for p in propose.assess(found))

    def test_many_files_per_artist_is_a_structure(self):
        for artist in ("alpha", "beta", "gamma"):
            for index in range(6):
                self.make("17706653%02d.%s_piece%d.png"
                          % (index, artist, index))
        found, results = self.assess()
        self.assertEqual(found.items, 18)
        proposal = results["site-uploader"]
        self.assertTrue(proposal.accepted, proposal.reason)
        self.assertEqual(proposal.groups, 3)
        self.assertEqual(proposal.median, 6)

    def test_one_file_per_artist_is_not(self):
        for index in range(20):
            self.make("17706653%02d.artist%d_piece.png" % (index, index))
        _found, results = self.assess()
        proposal = results["site-uploader"]
        self.assertFalse(proposal.accepted)
        self.assertIn("alone in a folder", proposal.reason)

    def test_a_fact_almost_nothing_has_is_not_a_structure(self):
        self.make("IMG_4021.jpg", fixtures.jpeg)
        for index in range(60):
            self.make("plain%02d.png" % index)
        _found, results = self.assess()
        self.assertFalse(results["camera"].accepted)
        self.assertIn("%", results["camera"].reason)

    def test_checksum_named_files_are_counted_as_residue(self):
        for index in range(6):
            self.make("%032x.png" % index)
        found, _results = self.assess()
        self.assertEqual(found.opaque, 6)
        # No site in a bare hash name, so there is no handle at all.
        self.assertEqual(found.opaque_unplaceable, 6)

    def test_a_site_named_file_is_opaque_but_placeable(self):
        for index in range(6):
            self.make("GzVwz2cXEAIcI%02d.jpeg" % index)
        found, _results = self.assess()
        self.assertEqual(found.opaque, 6)
        self.assertEqual(found.opaque_unplaceable, 0)

    def test_identifiers_are_never_proposed_as_folders(self):
        for index in range(30):
            self.make("17706653%02d.artist_piece%d.png" % (index, index))
        found, _results = self.assess()
        for banned in ("post_id", "name", "path", "size"):
            self.assertNotIn(banned, found.values,
                             "%s would make one folder per file" % banned)

    # -- the file it writes has to be a file the engine can run -----------

    def test_the_proposal_loads_as_rules(self):
        for artist in ("alpha", "beta"):
            for index in range(5):
                self.make("17706653%02d.%s_piece%d.png"
                          % (index, artist, index))
        found, _results = self.assess()
        body = propose.render(found, propose.assess(found))
        target = os.path.join(self.directory, "proposed.ini")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(body)
        rule_set = rules.load(target)
        self.assertTrue(rule_set.rules)
        self.assertTrue(rule_set.settings.dry_run,
                        "a proposal must never arrive with dry run off")

    def test_a_funnel_files_into_the_system_folders(self):
        import userdirs
        for index in range(4):
            self.make("plain%02d.png" % index)
        found, _results = self.assess()
        found.root = userdirs.path("downloads")   # pretend it is the funnel
        body = propose.render(found, propose.assess(found))
        self.assertIn(userdirs.short(userdirs.path("pictures")), body)

    def test_another_volume_is_sorted_in_place(self):
        # Filing a USB stick into ~/Pictures would copy every file onto the
        # internal disk instead of renaming it where it lies.
        import userdirs
        for index in range(4):
            self.make("plain%02d.png" % index)
        found, _results = self.assess()
        original = propose.same_volume_as_home
        propose.same_volume_as_home = lambda _target: False
        try:
            body = propose.render(found, propose.assess(found))
        finally:
            propose.same_volume_as_home = original
        self.assertIn(os.path.join(found.root, "Sorted"), body)
        self.assertNotIn(userdirs.short(userdirs.path("pictures")) + "/",
                         body)
        self.assertIn("every file onto the internal disk", body)

    def test_every_kind_present_gets_somewhere_to_go(self):
        # The funnel property: nothing may be left behind, or the folder
        # simply refills.
        self.make("a.png")
        self.make("b.jpg", fixtures.jpeg)
        self.make("c.mp3", fixtures.mp3)
        self.make("d.pdf", fixtures.pdf)
        found, _results = self.assess()
        body = propose.render(found, propose.assess(found))
        for kind in found.kinds:
            self.assertIn("[rule: remaining %s]" % kind, body,
                          "%s has nowhere to go" % kind)
        self.assertIn("[rule: anything left]", body)

    def test_an_empty_folder_still_writes_a_usable_file(self):
        found, _results = self.assess()
        body = propose.render(found, propose.assess(found))
        self.assertIn("[rule: anything left]", body)
        target = os.path.join(self.directory, "empty.ini")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(body)
        self.assertTrue(rules.load(target).rules)

    def test_the_survey_stops_counting_runaway_facts(self):
        original = propose.MAX_DISTINCT
        propose.MAX_DISTINCT = 5
        try:
            for index in range(12):
                self.make("plain%02d.png" % index, width=100 + index,
                          height=80)
            found, _results = self.assess()
            self.assertIn("format", found.values)
        finally:
            propose.MAX_DISTINCT = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
