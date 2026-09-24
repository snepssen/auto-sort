"""Reading filed files again, and moving the ones a category now claims.

A file a category placed was judged by what the reader said on the day it
arrived. Four employment contracts sat in a folder named after their
letterhead because the reader of the day missed the title. `refile` reads
them again -- and moves a file only to a real category, never into a pen,
and never one somebody moved themselves.
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
import regroup                                           # noqa: E402
import rules                                             # noqa: E402

RULES = """
[settings]
dry_run = no

[watch]
folders = %(root)s/in

[rule: letters]
when = name ~ *letter*
into = %(root)s/Letters

[rule: anything left]
when = name is set
into = %(root)s/Unfiled
holding = yes
"""


class WhatIsReconsidered(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-refile-")
        path = os.path.join(self.dir, "r.ini")
        with open(path, "w") as handle:
            handle.write(RULES % {"root": self.dir})
        self.rule_set = rules.load(path)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def placed(self, folder, name, rule="old word", holding=False,
               members=1):
        run = self.journal.start_run("sort", source_root=self.dir,
                                     dry_run=False)
        where = os.path.join(self.dir, folder)
        os.makedirs(where, exist_ok=True)
        first = None
        for number in range(members):
            path = os.path.join(where, name if number == 0
                                else "%s.%d.srt" % (name, number))
            with open(path, "w") as handle:
                handle.write("x")
            move = self.journal.add_move(
                run, 1, number + 1, "move", rule,
                os.path.join(self.dir, "in", os.path.basename(path)), path,
                1, "", status="done", facts={"kind": "document"},
                holding=holding)
            first = first or (move, path)
        self.journal.finish_run(run, "completed")
        return first

    def considered(self):
        return [os.path.basename(candidate.path) for candidate
                in regroup.filed(self.journal, self.rule_set)]

    def test_a_file_a_category_placed_is_considered(self):
        self.placed("Erkenningsnummer", "letter.pdf")
        self.assertEqual(self.considered(), ["letter.pdf"])

    def test_a_held_file_is_regroups_business_not_this(self):
        self.placed("Unfiled", "letter.pdf", rule="anything left",
                    holding=True)
        self.assertEqual(self.considered(), [])

    def test_a_file_somebody_moved_is_left_alone(self):
        _move, path = self.placed("Erkenningsnummer", "letter.pdf")
        os.rename(path, os.path.join(self.dir, "mine.pdf"))
        self.assertEqual(self.considered(), [])

    def test_a_bundle_is_not_split_up(self):
        self.placed("Films", "letter.mkv", members=2)
        self.assertEqual(self.considered(), [])

    def test_it_moves_to_the_category_that_claims_it_now(self):
        self.placed("Erkenningsnummer", "letter.pdf")
        plans = regroup.build(self.journal, self.rule_set,
                              pick=regroup.filed)
        destinations = [item.members[0].destination
                        for _root, plan in plans for item in plan.items]
        self.assertEqual(destinations,
                         [os.path.join(self.dir, "Letters", "letter.pdf")])

    def moves(self):
        plans = regroup.build(self.journal, self.rule_set,
                              pick=regroup.filed)
        return [item.members[0].destination
                for _root, plan in plans for item in plan.items]

    def test_never_back_into_a_pen(self):
        """A file its rule no longer claims, and no other category does,
        stays where it is while that rule is still in the file."""
        self.placed("Letters", "payslip.pdf", rule="letters")
        self.assertEqual(self.moves(), [])

    def test_a_file_whose_rule_was_deleted_is_decided_afresh(self):
        """"Delete any line you disagree with and its folder goes with it."
        Folders named `This` and `your` outlived the rules that made them."""
        self.placed("This", "certificate.pdf", rule="what the page: This")
        self.assertEqual(self.moves(), [
            os.path.join(self.dir, "Unfiled", "certificate.pdf")])

    def test_a_file_already_where_it_belongs_stays(self):
        self.placed("Letters", "letter.pdf", rule="letters")
        plans = regroup.build(self.journal, self.rule_set,
                              pick=regroup.filed)
        self.assertEqual(
            [item for _root, plan in plans for item in plan.items], [])


if __name__ == "__main__":
    unittest.main()
