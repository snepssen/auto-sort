"""Not leaving the wrapping lying where the sweet was.

Sorting everything out of `Downloads/UK/Payslips` left `Payslips` standing,
and `UK` around it, and twenty-two more like them. A funnel with the
skeleton of its old contents still in it is not empty to anybody looking at
it.
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
import ledger                                            # noqa: E402
import paths                                             # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402


class WhatCountsAsEmpty(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-wrap-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, relative, content=None):
        path = os.path.join(self.dir, relative)
        if content is None:
            os.makedirs(path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(content)
        return path

    def test_nothing_at_all(self):
        self.assertTrue(paths.hollow(self.make("UK/Payslips")))

    def test_only_what_the_system_left(self):
        self.make("UK/.DS_Store", "x")
        self.make("UK/Payslips/._hidden", "x")
        self.assertTrue(paths.hollow(os.path.join(self.dir, "UK")))

    def test_one_real_file_anywhere_inside(self):
        self.make("UK/Payslips/.DS_Store", "x")
        self.make("UK/Gleneagles/rota.pdf", "x")
        self.assertFalse(paths.hollow(os.path.join(self.dir, "UK")))


class FindingTheWrapping(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-wrap2-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_whole_emptied_tree_goes_as_one(self):
        os.makedirs(os.path.join(self.dir, "UK", "Payslips"))
        os.makedirs(os.path.join(self.dir, "UK", "Gleneagles"))
        found = paths.emptied(
            [os.path.join(self.dir, "UK", "Payslips", "a.pdf"),
             os.path.join(self.dir, "UK", "Gleneagles", "b.pdf")], self.dir)
        self.assertEqual(found, [os.path.realpath(os.path.join(self.dir,
                                                               "UK"))])

    def test_it_stops_at_a_folder_that_still_holds_something(self):
        os.makedirs(os.path.join(self.dir, "UK", "Payslips"))
        with open(os.path.join(self.dir, "UK", "keep.pdf"), "w") as handle:
            handle.write("x")
        found = paths.emptied(
            [os.path.join(self.dir, "UK", "Payslips", "a.pdf")], self.dir)
        self.assertEqual([os.path.basename(f) for f in found], ["Payslips"])

    def test_the_watched_folder_itself_never_goes(self):
        """It is where things arrive, not a thing."""
        found = paths.emptied([os.path.join(self.dir, "a.pdf")], self.dir)
        self.assertEqual(found, [])

    def test_a_folder_outside_the_watched_one_is_not_its_business(self):
        elsewhere = tempfile.mkdtemp()
        try:
            found = paths.emptied([os.path.join(elsewhere, "a.pdf")],
                                  self.dir)
            self.assertEqual(found, [])
        finally:
            shutil.rmtree(elsewhere, ignore_errors=True)


class WrappingItMadeItself(unittest.TestCase):
    """A regroup empties holding folders outside the watched one. Those
    were auto-sort's own, and were left standing empty all the same."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-wrap-own-")
        self.unfiled = os.path.join(self.dir, "Documents", "Unfiled")
        self.month = os.path.join(self.unfiled, "2023-09")
        os.makedirs(self.month)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        run = self.journal.start_run("sort", source_root=self.dir,
                                     dry_run=False)
        self.journal.record_directories(run, [self.month])

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_folder_it_made_and_emptied_is_found(self):
        found = paths.emptied_of_our_own(
            [os.path.join(self.month, "contract.pdf")],
            self.journal.made_directory)
        self.assertEqual(found, [self.month])

    def test_it_stops_at_the_first_folder_it_did_not_make(self):
        """`Unfiled` was there before; only the month under it was made."""
        found = paths.emptied_of_our_own(
            [os.path.join(self.month, "contract.pdf")],
            self.journal.made_directory)
        self.assertNotIn(self.unfiled, found)

    def test_a_folder_somebody_made_is_never_touched(self):
        theirs = os.path.join(self.dir, "Documents", "Mine")
        os.makedirs(theirs)
        self.assertEqual(paths.emptied_of_our_own(
            [os.path.join(theirs, "a.pdf")], self.journal.made_directory), [])

    def test_one_that_still_holds_something_stays(self):
        with open(os.path.join(self.month, "left.pdf"), "w") as handle:
            handle.write("x")
        self.assertEqual(paths.emptied_of_our_own(
            [os.path.join(self.month, "contract.pdf")],
            self.journal.made_directory), [])


class ClearingItAway(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-wrap3-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_truly_empty_tree_is_simply_removed(self):
        tree = os.path.join(self.dir, "UK", "Payslips")
        os.makedirs(tree)
        binned = []
        outcome = paths.clear_away(os.path.join(self.dir, "UK"),
                                   binned.append)
        self.assertEqual(outcome, "removed")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "UK")))
        self.assertEqual(binned, [])

    def test_litter_goes_to_the_bin_not_to_nothing(self):
        """This program does not delete files. Not even `.DS_Store`."""
        os.makedirs(os.path.join(self.dir, "UK"))
        with open(os.path.join(self.dir, "UK", ".DS_Store"), "w") as handle:
            handle.write("x")
        binned = []
        outcome = paths.clear_away(os.path.join(self.dir, "UK"),
                                   binned.append)
        self.assertEqual(outcome, "binned")
        self.assertEqual(binned, [os.path.join(self.dir, "UK")])

    def test_litter_with_no_bin_to_hand_is_left_alone(self):
        os.makedirs(os.path.join(self.dir, "UK"))
        with open(os.path.join(self.dir, "UK", ".DS_Store"), "w") as handle:
            handle.write("x")
        self.assertEqual(paths.clear_away(os.path.join(self.dir, "UK")), "")
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "UK")))

    def test_a_folder_with_something_real_is_never_touched(self):
        os.makedirs(os.path.join(self.dir, "UK"))
        with open(os.path.join(self.dir, "UK", "rota.pdf"), "w") as handle:
            handle.write("x")
        binned = []
        self.assertEqual(paths.clear_away(os.path.join(self.dir, "UK"),
                                          binned.append), "")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "UK",
                                                    "rota.pdf")))
        self.assertEqual(binned, [])


class AfterASort(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-wrap4-")
        self.inbox = os.path.join(self.dir, "Downloads")
        self.out = os.path.join(self.dir, "Sorted")
        os.makedirs(os.path.join(self.inbox, "UK", "Payslips"))
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(self.inbox, "UK", "Payslips", name),
                      "w") as handle:
                handle.write(name)
        with open(os.path.join(self.dir, "rules.ini"), "w") as handle:
            handle.write("[settings]\ndry_run = no\nsettle_seconds = 0\n\n"
                         "[watch]\nfolders = %s\n\n[rule: all]\n"
                         "when = name is set\ninto = %s\n"
                         % (self.inbox, self.out))
        self.rule_set = rules.load(os.path.join(self.dir, "rules.ini"))
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def sort(self):
        items = list(bundles.walk(self.inbox, max_depth=3))
        plan = sorter.build_plan(self.inbox, self.rule_set, items=items)
        sorter.execute(plan, self.rule_set, self.journal, dry_run=True)
        return sorter.execute(plan, self.rule_set, self.journal,
                              dry_run=False)

    def test_the_skeleton_does_not_stay_behind(self):
        with mock.patch.object(sorter, "_to_the_bin") as bin_send:
            result = self.sort()
        self.assertEqual(os.listdir(self.inbox), [])
        bin_send.assert_not_called()
        self.assertTrue(any("cleared away" in m for m in result.messages))

    def test_a_preview_clears_nothing(self):
        items = list(bundles.walk(self.inbox, max_depth=3))
        plan = sorter.build_plan(self.inbox, self.rule_set, items=items)
        sorter.execute(plan, self.rule_set, self.journal, dry_run=True)
        self.assertTrue(os.path.isdir(os.path.join(self.inbox, "UK")))

    def test_undo_puts_the_folder_back_with_the_files(self):
        with mock.patch.object(sorter, "_to_the_bin"):
            self.sort()
        sorter.undo_everything(self.journal)
        self.assertEqual(sorted(os.listdir(os.path.join(
            self.inbox, "UK", "Payslips"))), ["a.txt", "b.txt"])


if __name__ == "__main__":
    unittest.main()
