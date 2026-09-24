"""Putting everything back, for the day the sorting itself got better.

Somebody who has watched the program learn something wants the whole folder
tipped back into the funnel so it can be filed again by rules that know
more. One run at a time, that was four hundred commands.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundles                                           # noqa: E402
import ledger                                            # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402


class PuttingEverythingBack(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-undoall-")
        self.inbox = os.path.join(self.dir, "in")
        self.out = os.path.join(self.dir, "out")
        os.makedirs(self.inbox)
        self.rules = os.path.join(self.dir, "rules.ini")
        with open(self.rules, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = no\nsettle_seconds = 0\n\n"
                         "[watch]\nfolders = %s\n\n[rule: all]\n"
                         "when = name is set\ninto = %s\n"
                         % (self.inbox, self.out))
        self.rule_set = rules.load(self.rules)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def sort_one(self, name):
        with open(os.path.join(self.inbox, name), "w") as handle:
            handle.write(name)
        items = [item for item in bundles.walk(self.inbox, max_depth=1)
                 if os.path.basename(item.primary) == name]
        plan = sorter.build_plan(self.inbox, self.rule_set, items=items)
        # A preview first, as the program insists on for a new folder.
        sorter.execute(plan, self.rule_set, self.journal, dry_run=True)
        return sorter.execute(plan, self.rule_set, self.journal,
                              dry_run=False)

    def test_every_run_is_put_back(self):
        for name in ("a.txt", "b.txt", "c.txt"):
            self.sort_one(name)
        self.assertEqual(sorted(os.listdir(self.inbox)), [])
        results = sorter.undo_everything(self.journal)
        self.assertEqual(len(results), 3)
        self.assertEqual(sorted(os.listdir(self.inbox)),
                         ["a.txt", "b.txt", "c.txt"])

    def test_newest_first(self):
        """A file walked forward twice is walked back the way it came."""
        for name in ("a.txt", "b.txt"):
            self.sort_one(name)
        order = []
        sorter.undo_everything(self.journal,
                               on_run=lambda run, _n: order.append(run))
        self.assertEqual(order, sorted(order, reverse=True))

    def test_a_preview_moves_nothing(self):
        self.sort_one("a.txt")
        sorter.undo_everything(self.journal, dry_run=True)
        self.assertEqual(os.listdir(self.inbox), [])

    def test_undo_runs_are_not_themselves_undone(self):
        """Undoing the undo would sort everything again."""
        self.sort_one("a.txt")
        sorter.undo_everything(self.journal)
        self.assertEqual(self.journal.undoable_runs(), [])
        self.assertEqual(sorter.undo_everything(self.journal), [])

    def test_a_file_moved_away_by_hand_is_reported_not_forced(self):
        self.sort_one("a.txt")
        self.sort_one("b.txt")
        os.remove(os.path.join(self.out, "a.txt"))
        results = sorter.undo_everything(self.journal)
        self.assertEqual(sum(r.completed for r in results), 1)
        self.assertEqual(sum(r.failed for r in results), 1)
        self.assertEqual(os.listdir(self.inbox), ["b.txt"])


class FittingARuleName(unittest.TestCase):
    """`...itself: Detail 3` read as a name with a 3 in it."""

    def test_a_long_generated_name_shows_the_part_after_the_colon(self):
        import autosort
        self.assertEqual(
            autosort._fit("what the page calls itself: Details", 34),
            "Details")

    def test_a_short_name_is_left_alone(self):
        import autosort
        self.assertEqual(autosort._fit("contracts: Konvert", 34),
                         "contracts: Konvert")

    def test_what_still_does_not_fit_says_it_was_cut(self):
        import autosort
        self.assertTrue(autosort._fit("x" * 50, 34).endswith("…"))


if __name__ == "__main__":
    unittest.main()
