"""Going back for files that were filed before the pattern was visible.

A folder teaches auto-sort gradually, so the files that arrive first are
always filed worst -- there was nothing better to do with them at the time.
The failure this prevents is those files staying in a holding folder forever
while only later arrivals benefit, with the only remedy being to drag them
back into Downloads, which is absurd.

The risk on the other side is churn. Reconsidering everything on every rules
change would reshuffle a disk quietly and endlessly, so only two things may
happen: a file placed by a rule marked `holding = yes` may move to a rule that
is not, and nothing else is ever reconsidered.
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

import bundles                                           # noqa: E402
import fixtures                                          # noqa: E402
import ledger                                            # noqa: E402
import regroup                                           # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402


class Promotion(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        self.home = os.path.join(self.directory, "home")
        os.makedirs(self.root)
        os.makedirs(self.home)
        self.state = os.path.join(self.directory, "state.db")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def drop(self, name):
        path = fixtures.png(os.path.join(self.root, name))
        old = time.time() - 600
        os.utime(path, (old, old))
        return path

    def rules_for(self, body):
        filename = os.path.join(self.directory, "rules.ini")
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = no\nsettle_seconds = 0\n\n"
                         "[watch]\nfolders = %s\ndepth = 2\n\n%s"
                         % (self.root, body))
        return rules.load(filename)

    HOLDING_ONLY = """
[rule: holding images]
when = kind = image
into = {home}/Unfiled
holding = yes
"""

    LEARNED = """
[rule: by artist]
when    = kind = image
extract = stem re ^\\d+\\.(?P<artist>[a-z0-9-]+)_
into    = {home}/By artist/{{artist}}

[rule: holding images]
when = kind = image
into = {home}/Unfiled
holding = yes
"""

    def sort_with(self, rule_set):
        """Sort twice: the first apply for a folder is always a preview."""
        with ledger.Ledger(self.state) as journal:
            for _attempt in range(2):
                plan = sorter.build_plan(self.root, rule_set)
                if not plan.items:
                    break
                sorter.execute(plan, rule_set, journal, dry_run=False)

    def regroup_with(self, rule_set, journal, attempts=2):
        """Regroup the same way: a promotion is a move like any other, so the
        first apply against a new rules file is a preview too."""
        moved = 0
        for _attempt in range(attempts):
            plans = regroup.build(journal, rule_set)
            if not plans:
                break
            for _root, plan in plans:
                result = sorter.execute(plan, rule_set, journal,
                                        dry_run=False)
                if not result.dry_run:
                    moved += result.completed
        return moved

    def test_a_stranded_file_is_promoted_once_the_pattern_appears(self):
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        stranded = os.path.join(self.home, "Unfiled",
                                "1770665300.koul_early.png")
        self.assertTrue(os.path.exists(stranded))

        learned = self.rules_for(self.LEARNED.format(home=self.home))
        with ledger.Ledger(self.state) as journal:
            plans = regroup.build(journal, learned)
            total, by_rule, _by_destination = regroup.summarise(plans)
            self.assertEqual(total, 1)
            self.assertEqual(by_rule[0][0], "by artist")
            self.assertEqual(self.regroup_with(learned, journal), 1)

        self.assertFalse(os.path.exists(stranded))
        self.assertTrue(os.path.exists(os.path.join(
            self.home, "By artist", "koul", "1770665300.koul_early.png")))

    def test_a_file_placed_by_an_ordinary_rule_is_never_reconsidered(self):
        # The churn guard: only holding placements are revisited, so editing
        # a rules file cannot silently reshuffle a disk.
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.LEARNED.format(home=self.home)))
        placed = os.path.join(self.home, "By artist", "koul",
                              "1770665300.koul_early.png")
        self.assertTrue(os.path.exists(placed))

        moved_elsewhere = self.rules_for("""
[rule: by artist]
when    = kind = image
extract = stem re ^\\d+\\.(?P<artist>[a-z0-9-]+)_
into    = %s/Somewhere else/{artist}
""" % self.home)
        with ledger.Ledger(self.state) as journal:
            self.assertEqual(regroup.build(journal, moved_elsewhere), [])
        self.assertTrue(os.path.exists(placed))

    def test_nothing_happens_when_there_is_still_no_better_answer(self):
        self.drop("1770665300.koul_early.png")
        holding = self.rules_for(self.HOLDING_ONLY.format(home=self.home))
        self.sort_with(holding)
        with ledger.Ledger(self.state) as journal:
            self.assertEqual(regroup.build(journal, holding), [])

    def test_regrouping_twice_does_nothing_the_second_time(self):
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        learned = self.rules_for(self.LEARNED.format(home=self.home))
        with ledger.Ledger(self.state) as journal:
            self.regroup_with(learned, journal)
            self.assertEqual(regroup.build(journal, learned), [])

    def test_a_file_the_person_moved_is_left_alone(self):
        # They already answered the question; `corrections` learns from that,
        # and this must not overrule it.
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        stranded = os.path.join(self.home, "Unfiled",
                                "1770665300.koul_early.png")
        mine = os.path.join(self.home, "My own folder")
        os.makedirs(mine)
        shutil.move(stranded, os.path.join(mine, "1770665300.koul_early.png"))

        learned = self.rules_for(self.LEARNED.format(home=self.home))
        with ledger.Ledger(self.state) as journal:
            self.assertEqual(regroup.build(journal, learned), [])
        self.assertTrue(os.path.exists(
            os.path.join(mine, "1770665300.koul_early.png")))

    def test_the_ledger_records_that_a_placement_was_provisional(self):
        # By a flag rather than by the rule's name, because names change
        # every time a rules file is regenerated.
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        with ledger.Ledger(self.state) as journal:
            rows = [row for row in journal.placed_moves()
                    if row["status"] == "done"]
            self.assertTrue(rows)
            self.assertTrue(all(row["holding"] for row in rows))

    def test_promotion_survives_the_rule_being_renamed(self):
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        renamed = self.rules_for("""
[rule: by artist]
when    = kind = image
extract = stem re ^\\d+\\.(?P<artist>[a-z0-9-]+)_
into    = %s/By artist/{artist}

[rule: a completely different name for the holding pen]
when = kind = image
into = %s/Unfiled
holding = yes
""" % (self.home, self.home))
        with ledger.Ledger(self.state) as journal:
            total, _by_rule, _by_dest = regroup.summarise(
                regroup.build(journal, renamed))
        self.assertEqual(total, 1)

    def test_the_first_regroup_against_new_rules_is_a_preview(self):
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        stranded = os.path.join(self.home, "Unfiled",
                                "1770665300.koul_early.png")
        learned = self.rules_for(self.LEARNED.format(home=self.home))
        with ledger.Ledger(self.state) as journal:
            moved = self.regroup_with(learned, journal, attempts=1)
        self.assertEqual(moved, 0)
        self.assertTrue(os.path.exists(stranded))

    def test_an_undo_puts_a_promotion_back(self):
        self.drop("1770665300.koul_early.png")
        self.sort_with(self.rules_for(self.HOLDING_ONLY.format(
            home=self.home)))
        learned = self.rules_for(self.LEARNED.format(home=self.home))
        with ledger.Ledger(self.state) as journal:
            self.regroup_with(learned, journal)
            sorter.undo(journal, "last")
        self.assertTrue(os.path.exists(os.path.join(
            self.home, "Unfiled", "1770665300.koul_early.png")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
