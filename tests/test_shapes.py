"""Conventions learnt from a corpus, and sources taken from the OS.

Together these replace a table of per-site patterns, which is the thing that
cannot be finished: there is always another site, and filling the table means
somebody visiting it to collect samples first.

Two mechanisms do it instead. The operating system already recorded the URL
every downloaded file came from, so the source is known exactly and for free,
including for sites nothing has ever seen. And a naming convention is by
definition a thing that repeats, so it can be counted out of the filenames
rather than described in advance.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence                                          # noqa: E402
import hosts                                             # noqa: E402
import rules                                             # noqa: E402
import shapes                                            # noqa: E402


class Hostnames(unittest.TestCase):

    def test_delivery_network_decoration_is_stripped(self):
        for hostname, expected in (
                ("d.furaffinity.net", "furaffinity"),
                ("static1.e621.net", "e621"),
                ("pbs.twimg.com", "twimg"),
                ("video.twimg.com", "twimg"),
                ("scontent-man2-1.xx.fbcdn.net", "fbcdn"),
                ("cdn.bsky.app", "bsky"),
                ("thumb.wikimedia.org", "wikimedia"),
                ("uk.pinterest.com", "pinterest"),
                ("i.redd.it", "redd"),
                ("files.mastodon.social", "mastodon")):
            self.assertEqual(hosts.source(hostname), expected, hostname)

    def test_a_service_name_that_looks_like_a_cdn_code_survives(self):
        # `e621` matches the [a-z]{1,3}\d{1,4} shape that identifies shard
        # names like `s3` and `c1`, and is the service.
        self.assertEqual(hosts.source("static1.e621.net"), "e621")
        self.assertEqual(hosts.source("e926.net"), "e926")

    def test_multipart_suffixes(self):
        self.assertEqual(hosts.registrable("www.bbc.co.uk"), "bbc.co.uk")
        self.assertEqual(hosts.source("www.bbc.co.uk"), "bbc")
        self.assertEqual(hosts.source("snepssen.github.io"), "snepssen")

    def test_addresses_are_left_alone(self):
        self.assertEqual(hosts.source("192.168.1.5"), "192.168.1.5")

    def test_nothing_in_nothing_out(self):
        for value in (None, "", "   "):
            self.assertIsNone(hosts.source(value))


class LearntConventions(unittest.TestCase):

    def furaffinity(self, artists):
        """`<unix time>.<artist>_<title>` -- the shape, not the site."""
        stems = []
        stamp = 1500000000
        for artist, count in artists.items():
            for index in range(count):
                stamp += 1
                stems.append("%d.%s_piece_%d" % (stamp, artist, index))
        return stems

    def test_the_repeating_field_is_found_without_knowing_the_site(self):
        stems = self.furaffinity({"multyashka-sweet": 13, "keihound": 6,
                                  "koul": 5, "lemas": 4, "gothwolf": 3})
        found, depth = shapes.learn_best(stems)
        self.assertEqual(len(found), 1)
        convention = found[0]
        self.assertIsNotNone(convention.category)
        self.assertEqual(convention.groups, 5)
        self.assertIn("(?P<group>", convention.pattern("group"))
        self.assertEqual(depth, 2)

    def test_the_pattern_it_writes_matches_what_taught_it(self):
        import re
        stems = self.furaffinity({"alpha": 8, "beta-two": 7, "gamma3": 6})
        found, _depth = shapes.learn_best(stems)
        pattern = re.compile(found[0].pattern("group"))
        for stem in stems:
            match = pattern.search(stem)
            self.assertIsNotNone(match, stem)
            self.assertIn(match.group("group"),
                          ("alpha", "beta-two", "gamma3"))

    def test_identifiers_are_never_offered_as_folders(self):
        # Three values at least: two is a coin toss, not a category.
        stems = self.furaffinity({"alpha": 7, "beta": 7, "gamma": 6})
        convention = shapes.learn_best(stems)[0][0]
        # Field 0 is the timestamp: different in every file.
        self.assertNotEqual(convention.category, 0)

    def test_names_people_typed_are_not_a_convention(self):
        # No machine identifier anywhere, so this is a person naming things.
        stems = ["Flying Without Wings 2026-07-11", "Yodelling Till Dawn 2026",
                 "A03 Green Aura 2026-09-05", "B04 Oli 2026-09-05",
                 "B06 VoidKitty 2026-09-05", "Lonely Hearts 2026-08-01",
                 "Prism Game 2026-08-29", "Golden Aura 2026-09-05",
                 "Red Aura 2026-09-05", "Blue Aura 2026-09-05"]
        found, _depth = shapes.learn_best(stems)
        self.assertEqual(found, [])

    def test_a_folder_of_unique_names_yields_nothing(self):
        stems = ["%032x" % index for index in range(50)]
        self.assertEqual(shapes.learn_best(stems)[0], [])

    def test_concentration_prefers_a_real_long_tail_to_a_flat_one(self):
        # One prolific artist and a tail that still repeats: a structure.
        good = shapes.concentration([13, 4, 3, 2, 2, 2, 2, 1, 1, 1])
        # A thousand files, a thousand values: not one.
        bad = shapes.concentration([1] * 30)
        self.assertGreater(good, 0.6)
        self.assertEqual(bad, 0.0)

    def test_depth_is_chosen_to_explain_the_most_files(self):
        # Titles with differing word counts shatter the shape at depth 3 and
        # resolve at depth 2.
        stems = []
        stamp = 1500000000
        for artist in ("alpha", "beta", "gamma"):
            for index in range(5):
                stamp += 1
                words = "_".join(["word"] * (index + 1))
                stems.append("%d.%s_%s" % (stamp, artist, words))
        found, depth = shapes.learn_best(stems)
        self.assertEqual(depth, 2)
        self.assertEqual(found[0].count, 15)

    def test_a_short_field_is_not_a_category(self):
        # `p0`, `p1`, `p2` repeat beautifully and mean nothing as folders.
        stems = ["%d_p%d" % (90000000 + index, index % 3)
                 for index in range(30)]
        found, _depth = shapes.learn_best(stems)
        for convention in found:
            if convention.category is not None:
                values = convention.fields[convention.category][2]
                self.assertFalse(all(len(value) <= 2 for value in values))


class Extraction(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def build(self, body):
        filename = os.path.join(self.directory, "rules.ini")
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write("[settings]\nmin_confidence = 0.5\n\n[watch]\n"
                         "folders = %s\n\n%s" % (self.directory, body))
        return rules.load(filename)

    def record(self, stem, **extra):
        made = evidence.Record("/x/%s.png" % stem)
        made.set("stem", stem, "path", evidence.CERTAIN)
        made.set("name", stem + ".png", "path", evidence.CERTAIN)
        made.set("kind", "image", "signature", evidence.CERTAIN)
        for key, value in extra.items():
            made.set(key, value, "test", evidence.STRONG)
        return made

    def test_a_captured_field_becomes_a_folder(self):
        rule_set = self.build(
            "[rule: by artist]\nwhen = kind = image\n"
            "extract = stem re ^\\d+\\.(?P<artist>[a-z0-9-]+)_\n"
            "into = /out/{artist}\n")
        decision, _miss = rule_set.decide(
            self.record("1789229605.kostino_doppel"), source_root="/x")
        self.assertEqual(decision.destination,
                         "/out/kostino/1789229605.kostino_doppel.png")

    def test_the_original_filename_is_kept(self):
        # The bug this exists for renamed every file to the captured value
        # and dropped its extension.
        rule_set = self.build(
            "[rule: by artist]\nwhen = kind = image\n"
            "extract = stem re ^\\d+\\.(?P<artist>[a-z0-9-]+)_\n"
            "into = /out/{artist}\n")
        decision, _miss = rule_set.decide(
            self.record("1789229605.kostino_doppel"), source_root="/x")
        self.assertTrue(decision.destination.endswith(
            "1789229605.kostino_doppel.png"))

    def test_capturing_a_protected_name_is_refused_when_the_file_loads(self):
        for group in ("name", "ext", "stem", "kind"):
            with self.assertRaises(rules.RuleError) as caught:
                self.build("[rule: bad]\nwhen = kind = image\n"
                           "extract = stem re ^(?P<%s>[a-z]+)\n"
                           "into = /out/{%s}\n" % (group, group))
            self.assertIn(group, str(caught.exception))

    def test_a_pattern_that_does_not_match_declines(self):
        rule_set = self.build(
            "[rule: by artist]\nwhen = kind = image\n"
            "extract = stem re ^\\d+\\.(?P<artist>[a-z0-9-]+)_\n"
            "into = /out/{artist}\n")
        decision, near_miss = rule_set.decide(self.record("holiday-photo"),
                                              source_root="/x")
        self.assertIsNone(decision)
        self.assertIn("extract did not match", near_miss.reason)

    def test_a_pattern_with_no_named_group_is_refused(self):
        with self.assertRaises(rules.RuleError):
            self.build("[rule: bad]\nwhen = kind = image\n"
                       "extract = stem re ^\\d+\\.([a-z]+)_\n"
                       "into = /out/x\n")

    def test_a_malformed_extract_is_refused(self):
        with self.assertRaises(rules.RuleError):
            self.build("[rule: bad]\nwhen = kind = image\n"
                       "extract = nonsense\ninto = /out/x\n")

    def test_a_learnt_pattern_survives_the_config_file_round_trip(self):
        # Derived character classes can contain `#` and `;`, which are
        # configparser's inline comment markers.
        stems = ["%d.artist#%d_title" % (1500000000 + index, index % 4)
                 for index in range(20)]
        found, _depth = shapes.learn_best(stems)
        self.assertTrue(found)
        pattern = found[0].pattern("group")
        rule_set = self.build("[rule: learnt]\nwhen = kind = image\n"
                              "extract = stem re %s\ninto = /out/{group}\n"
                              % pattern)
        self.assertEqual(rule_set.rules[0].extract.pattern, pattern)


class WordsAndNotWords(unittest.TestCase):
    """A little soup survives being filtered out of a page, and one word
    of it is enough to name a folder."""

    def word(self, text):
        return shapes._is_a_word(text)

    def test_glyph_numbers_are_not_a_word(self):
        """No Latin-script language spells a word out of accented letters
        alone."""
        self.assertFalse(self.word("ÍäÎá"))
        self.assertFalse(self.word("ªÎÞª¾êÞ"))

    def test_accented_words_are_words(self):
        for word in ("München", "Számla", "Đường", "Öbb", "Rechnung"):
            self.assertTrue(self.word(word), word)

    def test_scripts_that_are_not_latin_at_all_are_words(self):
        """The word for "invoice" in several languages has no plain
        letters in it, and refusing them would be refusing the feature."""
        for word in ("Τιμολόγιο", "Счёт", "請求書", "발행"):
            self.assertTrue(self.word(word), word)

    def test_nothing_with_a_symbol_in_the_middle_of_it(self):
        self.assertFalse(self.word("Rech¾nung"))
        self.assertFalse(self.word("€uro"))

    def test_a_word_of_no_letters_at_all(self):
        self.assertFalse(self.word("4711"))
        self.assertFalse(self.word(""))


class NearTheFront(unittest.TestCase):
    """A document says what it is before it says anything else."""

    def test_a_word_that_leads(self):
        self.assertTrue(shapes._near_the_front([0, 0, 0, 1]))

    def test_a_word_on_the_second_line_of_a_letterhead(self):
        """A letter that leads with its sender still names itself next."""
        self.assertTrue(shapes._near_the_front([1, 2, 1, 2]))

    def test_a_word_buried_in_a_sentence(self):
        """Measured: `Service` sat at position 3 and was never once first."""
        self.assertFalse(shapes._near_the_front([3, 4, 3, 5]))

    def test_nowhere_at_all(self):
        self.assertFalse(shapes._near_the_front([]))


class WhatAFolderChoosesForItself(unittest.TestCase):

    def test_soup_never_becomes_a_category(self):
        headings = ["ÍäÎá 4711 2019"] * 30 + [
            "Rechnung Stadtwerke", "Rechnung Muenchen", "Rechnung Januar",
            "Mietvertrag Wohnung", "Mietvertrag Garage", "Mietvertrag Keller",
            "Steuerbescheid 2019", "Steuerbescheid 2020",
            "Steuerbescheid 2021"]
        words = [word for word, _count in shapes.learn_terms(headings)]
        self.assertNotIn("ÍäÎá", words)
        self.assertIn("Rechnung", words)

    def test_a_word_only_ever_buried_is_not_a_category(self):
        headings = ["Rechnung Stadtwerke Muenchen fuer Service %d" % n
                    for n in range(8)]
        headings += ["Mietvertrag Wohnung", "Mietvertrag Garage",
                     "Mietvertrag Keller", "Steuerbescheid 2019",
                     "Steuerbescheid 2020", "Steuerbescheid 2021"]
        words = [word for word, _count in shapes.learn_terms(headings)]
        self.assertNotIn("Service", words)


class TheSoftwareIsNotTheDocument(unittest.TestCase):
    """German payslips open `Programmversion: zvoove Payroll ...`: the
    program that printed them, stamping its version on every page."""

    def test_a_word_yields_to_a_wider_one_that_also_leads(self):
        headings = (["Programmversion zvoove Payroll Lohn Abrechnung %d" % n
                     for n in range(4)]
                    + ["Payroll statement week %d" % n for n in range(15)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(20)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(20)])
        found = [word for word, _count in shapes.learn_terms(headings)]
        self.assertNotIn("Programmversion", found)
        self.assertIn("Payroll", found)

    def test_but_not_to_the_town_the_letters_came_from(self):
        """`Muenchen` is wider than every kind of letter in the pile, and
        sits at the end of the heading every time."""
        headings = (["Steuerbescheid 2011 Finanzamt Muenchen %d" % n
                     for n in range(3)]
                    + ["Rechnung Stadtwerke Muenchen GmbH %d" % n
                       for n in range(3)]
                    + ["Mietvertrag Wohnung Hausverwaltung %d" % n
                       for n in range(3)])
        found = [word for word, _count in shapes.learn_terms(headings)]
        self.assertIn("Steuerbescheid", found)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WhatToCallTheFolder(unittest.TestCase):
    """A word finds a category; the documents often say what it is called."""

    def test_the_phrase_they_all_say(self):
        """Three UK tax forms were learnt as `Details`."""
        headings = ["Part Details of employee leaving work",
                    "Part Details of employee leaving work",
                    "Details of employee leaving work Part"]
        self.assertEqual(shapes.shared_phrase("Details", headings),
                         "Details of employee leaving work")

    def test_a_word_with_nothing_shared_around_it_stays_a_word(self):
        headings = ["Rechnung Stadtwerke", "Rechnung Telekom",
                    "Rechnung vom Mai"]
        self.assertEqual(shapes.shared_phrase("Rechnung", headings),
                         "Rechnung")

    def test_a_straggler_does_not_shorten_it(self):
        """Nine in ten is agreement; OCR and variants make the tenth."""
        headings = (["ARBEIDSOVEREENKOMST VOOR UITZENDARBEID"] * 20
                    + ["ARBEIDSOVEREENKOMST VOOR"])
        self.assertEqual(shapes.shared_phrase("ARBEIDSOVEREENKOMST",
                                              headings),
                         "ARBEIDSOVEREENKOMST VOOR UITZENDARBEID")

    def test_it_does_not_end_on_a_joining_word(self):
        headings = ["Rechnung Nr 1", "Rechnung Nr 2", "Rechnung Nr 3"]
        self.assertEqual(shapes.shared_phrase("Rechnung", headings),
                         "Rechnung")

    def test_the_owners_name_is_never_part_of_it(self):
        headings = ["Loonbrief Tamas Torok"] * 5
        self.assertEqual(shapes.shared_phrase(
            "Loonbrief", headings, exclude={"tamas", "torok"}), "Loonbrief")

    def test_too_few_to_say_anything(self):
        self.assertEqual(shapes.shared_phrase(
            "Details", ["Details of employee leaving work"] * 2), "Details")


class TheRuleForIt(unittest.TestCase):

    def test_found_by_the_word_and_filed_under_the_phrase(self):
        import propose
        lines = propose.term_rule(
            "heading", "what the page calls itself", "Details", 3,
            "documents say it", "/Docs", values=[
                "Details of employee leaving work"] * 3)
        self.assertIn("when = heading contains Details", lines)
        self.assertIn("into = /Docs/Details of employee leaving work", lines)
