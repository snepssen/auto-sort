"""Keeping the ledger from needing its own disk.

Fifty thousand moves is nothing if it took ten years and a great deal if it
took a week. The same size means "this machine is busy and fine" or "this
will want its own disk by Tuesday", so size alone is the wrong trigger and
the rate is measured too.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger                                            # noqa: E402


def when(days_ago):
    return (datetime.datetime.now()
            - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class KnowingWhenToTidy(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-upkeep-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, number, days_ago, status="done", holding=0, facts=True):
        run = self.journal.start_run("sort", source_root=self.dir,
                                     dry_run=status == "dry-run")
        move_id = self.journal.add_move(
            run, number, 1, "move", "a rule",
            os.path.join(self.dir, "f%d" % number),
            os.path.join(self.dir, "out", "f%d" % number),
            1000, "", status=status,
            facts={"heading": "Rechnung %d" % number, "kind": "document"}
            if facts else None,
            holding=holding)
        self.journal.connection.execute(
            "UPDATE moves SET created_at = ? WHERE id = ?",
            (when(days_ago), move_id))
        self.journal.connection.commit()
        return move_id

    def test_a_young_ledger_says_nothing_about_a_year(self):
        """Half a day of history is not a growth rate."""
        for number in range(20):
            self.add(number, days_ago=0)
        self.assertEqual(self.journal.usage()["bytes_per_day"], 0.0)
        # A size limit it cannot reach, so only the rate is under test.
        self.assertEqual(self.journal.should_compact(10 ** 12, 1), "")

    def test_size_alone_can_trigger_it(self):
        self.add(1, days_ago=400)
        self.add(2, days_ago=0)
        reason = self.journal.should_compact(1, 10 ** 12)
        self.assertIn("reached", reason)

    def test_so_can_the_rate_while_it_is_still_small(self):
        """The case that matters: small today, enormous by Tuesday."""
        for number in range(40):
            self.add(number, days_ago=1)
        for number in range(40, 80):
            self.add(number, days_ago=0)
        reason = self.journal.should_compact(10 ** 12, 1)
        self.assertIn("growing", reason)
        self.assertIn("a year", reason)

    def test_previews_are_dropped_because_they_moved_nothing(self):
        for number in range(5):
            self.add(number, days_ago=40, status="dry-run")
        self.add(99, days_ago=40, status="done")
        result = self.journal.compact(keep_facts_days=365,
                                      keep_dry_run_days=7)
        self.assertEqual(result["previews_removed"], 5)
        self.assertEqual(self.journal.usage()["rows"], 1)

    def test_a_recent_preview_is_left_alone(self):
        self.add(1, days_ago=0, status="dry-run")
        self.journal.compact(keep_facts_days=365, keep_dry_run_days=7)
        self.assertEqual(self.journal.usage()["rows"], 1)

    def test_an_old_move_keeps_its_history_and_loses_its_evidence(self):
        """Where it went survives; what was known about it does not."""
        move_id = self.add(1, days_ago=400)
        self.journal.compact(keep_facts_days=90, keep_dry_run_days=7)
        row = self.journal.move(move_id)
        self.assertIsNotNone(row)
        self.assertIsNone(row["facts_json"])
        self.assertTrue(row["source"])
        self.assertTrue(row["destination"])
        self.assertEqual(row["rule_name"], "a rule")

    def test_a_file_a_regroup_could_still_promote_keeps_everything(self):
        """`regroup` is the one thing that reads old facts and acts on them."""
        move_id = self.add(1, days_ago=400, holding=1)
        self.journal.compact(keep_facts_days=90, keep_dry_run_days=7)
        self.assertIsNotNone(self.journal.move(move_id)["facts_json"])

    def test_the_search_that_people_open_the_page_for_still_works(self):
        for number in range(6):
            self.add(number, days_ago=400)
        self.journal.compact(keep_facts_days=90, keep_dry_run_days=7)
        self.assertEqual(len(self.journal.search_moves("f3", 50)), 1)

    def test_tidying_makes_it_smaller_not_larger(self):
        """VACUUM rewrites through the write-ahead log, so without
        collapsing that afterwards the ledger measures bigger than before."""
        for number in range(200):
            self.add(number, days_ago=400, status="dry-run")
        result = self.journal.compact(keep_facts_days=90,
                                      keep_dry_run_days=7)
        self.assertLess(result["bytes_after"], result["bytes_before"])


if __name__ == "__main__":
    unittest.main()
