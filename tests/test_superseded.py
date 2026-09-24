"""A placement is over once auto-sort itself moved the file on.

A regroup takes a file out of `Unfiled` and files it properly, and that is
a second placement of the same file. The first one used to stand beside it:
every regrouped file was counted twice by the rule reports -- 169 contracts
read as 338 -- and every one of them looked, to anything checking, like a
file somebody had moved away by hand.
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


class MovedOnByItself(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-superseded-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.downloads = os.path.join(self.dir, "Downloads", "contract.pdf")
        self.unfiled = os.path.join(self.dir, "Unfiled", "contract.pdf")
        self.filed = os.path.join(self.dir, "Contracts", "contract.pdf")

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def move(self, action, source, destination, rule="rule", holding=False,
             facts=None, operation="move"):
        run = self.journal.start_run(action, source_root=self.dir,
                                     dry_run=False)
        move = self.journal.add_move(run, 1, 1, operation, rule, source,
                                     destination, 1, "", status="done",
                                     facts=facts or {"kind": "document"},
                                     holding=holding)
        self.journal.finish_run(run, "completed")
        return move

    def placed(self):
        return [row["id"] for row in self.journal.placed_moves()]

    def test_the_first_placement_ends_when_it_is_regrouped(self):
        held = self.move("sort", self.downloads, self.unfiled,
                         "anything left", holding=True)
        promoted = self.move("sort", self.unfiled, self.filed, "contracts")
        self.assertEqual(self.placed(), [promoted])
        self.assertNotIn(held, [row["id"]
                                for row in self.journal.held_moves()])

    def test_undoing_the_regroup_makes_it_stand_again(self):
        held = self.move("sort", self.downloads, self.unfiled,
                         "anything left", holding=True)
        promoted = self.move("sort", self.unfiled, self.filed, "contracts")
        self.journal.update_move(promoted, "undone")
        self.assertEqual(self.placed(), [held])

    def test_a_spare_sent_to_the_bin_is_no_longer_placed(self):
        self.move("sort", self.downloads, self.filed)
        self.move("duplicates", self.filed, "/Bin/contract.pdf",
                  operation="trash")
        self.assertEqual(self.placed(), [])

    def test_a_move_somewhere_else_does_not_end_it(self):
        kept = self.move("sort", self.downloads, self.filed)
        self.move("sort", os.path.join(self.dir, "Downloads", "other.pdf"),
                  os.path.join(self.dir, "Contracts", "other.pdf"))
        self.assertIn(kept, self.placed())


class WaitingToBeRead(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-waiting-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_putting_a_scan_back_is_not_filing_one(self):
        """197 undo moves were counted as 197 pages filed unread."""
        back = os.path.join(self.dir, "scan.pdf")
        with open(back, "w") as handle:
            handle.write("%PDF")
        run = self.journal.start_run("undo", source_root=self.dir,
                                     dry_run=False)
        self.journal.add_move(run, 1, 1, "move", "restore",
                              os.path.join(self.dir, "Unfiled", "scan.pdf"),
                              back, 1, "", status="done",
                              facts={"needs_ocr": True})
        self.assertEqual(self.journal.waiting_for_reading(), 0)


class TheSchema(unittest.TestCase):

    def test_there_is_an_index_to_find_the_later_move_by(self):
        folder = tempfile.mkdtemp(prefix="autosort-schema-")
        try:
            with ledger.Ledger(os.path.join(folder, "state.db")) as journal:
                names = [row[0] for row in journal.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'")]
            self.assertIn("moves_by_source", names)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
