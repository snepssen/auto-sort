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


if __name__ == "__main__":
    unittest.main()
