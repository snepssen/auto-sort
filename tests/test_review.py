"""Rules the record has already judged.

Induction proposes generously because it must: a word heading three
documents may be the kind of document or the town it was posted from, and
nothing in the page says which. Running the sorter settles it, and this is
the part that reads the answer back out.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures                                          # noqa: E402
import ledger                                            # noqa: E402
import review                                            # noqa: E402
import rules                                             # noqa: E402

RULES = """
[settings]
dry_run = no
min_confidence = 0.4

[watch]
folders = ~/Downloads

[rule: Rechnung]
when = heading contains Rechnung
into = ~/Documents/Rechnung

[rule: Stadtwerke]
when = heading contains Stadtwerke
into = ~/Documents/Stadtwerke

[rule: Kontoauszug]
when = heading contains Kontoauszug
into = ~/Documents/Kontoauszug

[rule: anything left]
when = name is set
into = ~/Documents/Unsorted
holding = yes
"""


class WhatTheRecordShows(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-review-")
        path = os.path.join(self.dir, "r.ini")
        with open(path, "w") as handle:
            handle.write(RULES)
        self.rule_set = rules.load(path)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def file_one(self, run, number, heading, winner):
        self.journal.add_move(
            run, number, 1, "move", winner,
            os.path.join(self.dir, "doc%d.pdf" % number),
            os.path.join(self.dir, "out", "doc%d.pdf" % number),
            1, "", status="done",
            facts={"heading": heading, "name": "doc%d.pdf" % number})

    def bills(self, count=6):
        run = self.journal.start_run("sort", source_root=self.dir,
                                     dry_run=False)
        for number in range(count):
            self.file_one(run, number,
                          "Rechnung Nr %d Stadtwerke Muenchen" % number,
                          "Rechnung")
        self.journal.finish_run(run, "done")

    def test_a_rule_always_beaten_is_reported(self):
        self.bills()
        dead, files = review.dead_rules(self.journal, self.rule_set)
        self.assertEqual(files, 6)
        names = [use.name for use in dead]
        self.assertIn("Stadtwerke", names)
        self.assertNotIn("Rechnung", names)

    def test_it_says_what_beat_it(self):
        self.bills()
        dead, _files = review.dead_rules(self.journal, self.rule_set)
        beaten = [use for use in dead if use.name == "Stadtwerke"][0]
        self.assertEqual(beaten.shadowed, 6)
        self.assertEqual(beaten.shadowed_by.most_common(1)[0][0], "Rechnung")

    def test_two_trials_are_not_enough_to_condemn_a_rule(self):
        """The same threshold as everywhere else: three is a pattern."""
        self.bills(count=2)
        dead, _files = review.dead_rules(self.journal, self.rule_set)
        self.assertEqual(dead, [])

    def test_a_rule_that_never_matched_anything_is_not_condemned(self):
        """Silence is not evidence. `Kontoauszug` never saw a Kontoauszug."""
        self.bills()
        dead, _files = review.dead_rules(self.journal, self.rule_set)
        self.assertNotIn("Kontoauszug", [use.name for use in dead])

    def test_a_catch_all_is_never_condemned(self):
        """It exists to be last. The day it fires is the day it earns its keep.

        Judging it by the same count would tell somebody to delete the rule
        that guarantees nothing is left behind.
        """
        self.bills(count=20)
        dead, _files = review.dead_rules(self.journal, self.rule_set)
        self.assertNotIn("anything left", [use.name for use in dead])

    def test_a_rule_that_wins_sometimes_is_safe(self):
        run = self.journal.start_run("sort", source_root=self.dir,
                                     dry_run=False)
        for number in range(5):
            self.file_one(run, number, "Rechnung Stadtwerke", "Rechnung")
        for number in range(5, 8):
            self.file_one(run, number, "Stadtwerke Jahresabrechnung",
                          "Stadtwerke")
        self.journal.finish_run(run, "done")
        dead, _files = review.dead_rules(self.journal, self.rule_set)
        self.assertEqual(dead, [])


class RulesThatJustMiss(unittest.TestCase):
    """A rule asking about a fact that is there, for a value that is not.

    The failure this is for: `from_host ~ *.furaffinity.net` against
    forty-three pictures recorded as `from_host = furaffinity.net`. A
    leading `*.` needs something in front of the dot. They went to a
    holding folder, the person moved them back, and it happened again.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-near-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.run = self.journal.start_run("sort", source_root=self.dir,
                                          dry_run=False)
        self.number = 0

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def filed(self, rule_name, facts, times=1):
        for _ in range(times):
            self.number += 1
            move = self.journal.add_move(
                self.run, self.number, 1, "move", rule_name,
                os.path.join(self.dir, "f%d" % self.number),
                os.path.join(self.dir, "out", "f%d" % self.number),
                10, "", "done", facts=facts)
            self.journal.update_move(move, "done")

    def rules_for(self, body):
        path = os.path.join(self.dir, "rules.ini")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[watch]\nfolders = %s\n\n%s" % (self.dir, body))
        return rules.load(path)

    def test_a_glob_that_cannot_match_what_is_there(self):
        rule_set = self.rules_for(
            "[rule: artwork]\nwhen = from_host ~ *.furaffinity.net\n"
            "into = %s/art\n\n"
            "[rule: pictures]\nwhen = kind = image\ninto = %s/pics\n"
            % (self.dir, self.dir))
        self.filed("pictures", {"kind": "image",
                                "from_host": "furaffinity.net"}, times=5)
        misses, _files = review.near_misses(self.journal, rule_set)
        self.assertEqual([miss.rule.name for miss in misses], ["artwork"])
        self.assertEqual(misses[0].actual[0], ("furaffinity.net", 5))

    def test_a_rule_for_something_you_do_not_own_is_left_alone(self):
        """It is supposed to match nothing until the day it does."""
        rule_set = self.rules_for(
            "[rule: models]\nwhen = kind = model3d\ninto = %s/models\n\n"
            "[rule: pictures]\nwhen = kind = image\ninto = %s/pics\n"
            % (self.dir, self.dir))
        self.filed("pictures", {"kind": "image"}, times=5)
        misses, _files = review.near_misses(self.journal, rule_set)
        self.assertEqual(misses, [])

    def test_a_rule_that_works_is_not_reported(self):
        rule_set = self.rules_for(
            "[rule: artwork]\nwhen = from_host = furaffinity.net\n"
            "into = %s/art\n" % self.dir)
        self.filed("artwork", {"kind": "image",
                               "from_host": "furaffinity.net"}, times=5)
        misses, _files = review.near_misses(self.journal, rule_set)
        self.assertEqual(misses, [])

    def test_one_or_two_files_are_not_evidence_of_anything(self):
        rule_set = self.rules_for(
            "[rule: artwork]\nwhen = from_host ~ *.furaffinity.net\n"
            "into = %s/art\n\n"
            "[rule: pictures]\nwhen = kind = image\ninto = %s/pics\n"
            % (self.dir, self.dir))
        self.filed("pictures", {"kind": "image",
                                "from_host": "furaffinity.net"}, times=2)
        misses, _files = review.near_misses(self.journal, rule_set)
        self.assertEqual(misses, [])

    def test_what_counts_as_nearly(self):
        self.assertTrue(review._nearly("*.furaffinity.net", "furaffinity.net"))
        self.assertTrue(review._nearly("example.com", "www.example.com"))
        self.assertFalse(review._nearly("archive", "document"))
        self.assertFalse(review._nearly("a", "audio"))


if __name__ == "__main__":
    unittest.main()


class CategoriesThatArriveLater(unittest.TestCase):
    """Rules are generated once and the post keeps coming.

    A kind of letter that did not exist when the rules were written has no
    rule of its own, so it is claimed by whatever else happens to match --
    usually the company that sent it, because that word is on the page too
    and does have a rule. The documents are not lost; they are sorted by who
    wrote them instead of what they are, and nothing says so.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-emerge-")
        path = os.path.join(self.dir, "r.ini")
        with open(path, "w") as handle:
            handle.write(RULES)
        self.rule_set = rules.load(path)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.run = self.journal.start_run("sort", source_root=self.dir,
                                          dry_run=False)
        self.number = 0

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def filed(self, heading, winner):
        self.number += 1
        self.journal.add_move(
            self.run, self.number, 1, "move", winner,
            os.path.join(self.dir, "d%d.pdf" % self.number),
            os.path.join(self.dir, "out", "d%d.pdf" % self.number),
            1, "", status="done",
            facts={"heading": heading, "name": "d%d.pdf" % self.number,
                   "kind": "document"})

    def test_a_new_kind_of_letter_is_noticed(self):
        for number in range(5):
            self.filed("Rechnung Nr %d Stadtwerke Muenchen" % number,
                       "Rechnung")
        for number in range(4):
            # Different account number and amount every time, as always.
            self.filed("Mahnung Nr %d Stadtwerke Konto %d Betrag %d"
                       % (7000 + number, 88123400 + number, 40 + number),
                       "Stadtwerke")
        new, seen = review.emerging(self.journal, self.rule_set)
        self.assertEqual(seen, 9)
        self.assertIn("Mahnung", [word for word, _count in new])

    def test_what_already_has_a_rule_is_not_offered_again(self):
        for number in range(6):
            self.filed("Rechnung Nr %d Stadtwerke" % number, "Rechnung")
        new, _seen = review.emerging(self.journal, self.rule_set)
        self.assertNotIn("Rechnung", [word for word, _count in new])

    def test_the_parts_that_change_every_time_are_never_categories(self):
        """Account numbers, amounts and dates vary by design.

        This is the founding rule of the project pointed at the inside of a
        page: what repeats is a category, what differs every time is an
        identifier. The numbers differ in all nine documents below and not
        one of them is offered as a folder.
        """
        for number in range(9):
            self.filed("Rechnung Nr %d Stadtwerke Konto %d Betrag %d,%d0 EUR"
                       % (4000 + number, 88123400 + number * 7,
                          40 + number * 11, number), "Rechnung")
        new, _seen = review.emerging(self.journal, self.rule_set)
        for word, _count in new:
            self.assertFalse(word.isdigit(), word)
            self.assertTrue(any(ch.isalpha() for ch in word), word)

    def test_a_word_on_every_document_is_a_letterhead_not_a_category(self):
        for number in range(9):
            self.filed("Rechnung Nr %d Stadtwerke Muenchen" % number,
                       "Rechnung")
        new, _seen = review.emerging(self.journal, self.rule_set)
        self.assertNotIn("Muenchen", [word for word, _count in new])


class AdoptingWithoutRewriting(unittest.TestCase):
    """Adding a rule to a file somebody has edited, and changing nothing else."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-adopt-")
        self.text = RULES

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def block(self, word):
        return ["[rule: %s]" % word, "when = heading contains %s" % word,
                "into = ~/Documents/%s" % word, ""]

    def test_it_lands_above_the_catch_all_not_below_it(self):
        """Below the catch-all it would be adopted and never fire."""
        merged = review.adopt(self.text, [self.block("Mahnung")])
        lines = merged.splitlines()
        self.assertLess(lines.index("[rule: Mahnung]"),
                        lines.index("[rule: anything left]"))

    def test_it_lands_above_the_rule_it_has_to_beat(self):
        merged = review.adopt(self.text, [self.block("Mahnung")],
                              above=["Stadtwerke"])
        lines = merged.splitlines()
        self.assertLess(lines.index("[rule: Mahnung]"),
                        lines.index("[rule: Stadtwerke]"))

    def test_every_other_line_is_untouched(self):
        """The file belongs to whoever wrote it."""
        edited = "; MY OWN NOTES\n" + self.text
        block = self.block("Mahnung")
        merged = review.adopt(edited, [block])
        lines = merged.splitlines()
        at = lines.index("[rule: Mahnung]")
        # Cut out exactly what was inserted; what is left must be identical,
        # blank lines and all -- filtering by content would hide a lost one.
        del lines[at:at + len(block)]
        self.assertEqual(lines, edited.splitlines())
        self.assertTrue(merged.startswith("; MY OWN NOTES"))

    def test_nothing_to_add_leaves_the_file_exactly_as_it_was(self):
        self.assertEqual(review.adopt(self.text, []), self.text)


class RulesThatMatchAndThenDecline(unittest.TestCase):
    """The only failure in this program the person cannot see.

    `min_confidence` is a floor on acting, not on matching. A rule whose
    destination needs a fact that is only a guess matches perfectly and then
    does nothing -- and the file still moves, to a catch-all, silently.
    """

    BROKEN = """
[settings]
dry_run = yes
min_confidence = 0.6

[watch]
folders = %s

[rule: scans by year]
when = kind = document and capture = scan
into = ~/Documents/Scans/{happened:%%Y}

[rule: anything left]
when = name is set
into = ~/Documents/Unsorted
holding = yes
"""

    WORKING = BROKEN.replace("{happened:%%Y}", "{added:%%Y}")

    def setUp(self):
        # The rules file lives outside the folder being scanned, or it is
        # itself one of the files scanned and the counts are off by one.
        self.home = tempfile.mkdtemp(prefix="autosort-reach-")
        self.dir = os.path.join(self.home, "Downloads")
        os.makedirs(self.dir)
        for number in range(5):
            fixtures.pdf(os.path.join(self.dir, "scan%04d.pdf" % number),
                         producer="HP ScanJet Pro firmware", image_only=True)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def rules_from(self, template):
        path = os.path.join(self.home, "r.ini")
        with open(path, "w") as handle:
            handle.write(template % self.dir)
        return rules.load(path)

    def test_a_rule_that_can_never_fill_its_destination_is_named(self):
        broken, files = review.unreachable(self.rules_from(self.BROKEN),
                                           [self.dir])
        self.assertEqual(files, 5)
        self.assertEqual([reach.name for reach in broken], ["scans by year"])
        self.assertIn("below confidence", broken[0].reason)
        self.assertIn("happened", broken[0].reason)

    def test_the_same_rule_filed_by_a_certain_fact_is_fine(self):
        broken, _files = review.unreachable(self.rules_from(self.WORKING),
                                            [self.dir])
        self.assertEqual(broken, [])

    def test_one_decline_is_not_a_verdict(self):
        """A rule waiting for the right file has not failed."""
        for name in os.listdir(self.dir):
            if name.startswith("scan") and name != "scan0000.pdf":
                os.remove(os.path.join(self.dir, name))
        broken, files = review.unreachable(self.rules_from(self.BROKEN),
                                           [self.dir])
        self.assertEqual(files, 1)
        self.assertEqual(broken, [])
