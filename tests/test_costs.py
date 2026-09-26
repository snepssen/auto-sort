"""The only feedback loop this program is allowed to have.

Two files have frozen auto-sort during development and neither said so. The
measurements here exist so that the third one does: what it cost, which file
it was, and nothing sent anywhere.
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

import costs                                             # noqa: E402
import ledger                                            # noqa: E402
import sorter                                            # noqa: E402


def spend(seconds):
    """Take at least `seconds` by the clock the watch reads.

    Not `time.sleep`: on Windows a sleep is timed by a different clock and
    can end a fraction early by this one, and a block that took 9.7 ms
    failed a test for being measured at 9.7 ms.
    """
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        pass


class TakingTheReading(unittest.TestCase):

    def test_a_watch_times_the_block(self):
        with costs.Watch() as watch:
            spend(0.02)
        self.assertGreaterEqual(watch.seconds, 0.02)

    def test_a_failure_is_still_measured(self):
        """The file that raised after four minutes is the interesting one."""
        watch = costs.Watch()
        with self.assertRaises(ValueError):
            with watch:
                spend(0.01)
                raise ValueError("unreadable")
        self.assertGreaterEqual(watch.seconds, 0.01)

    def test_ordinary_files_are_not_notable(self):
        with costs.Watch() as watch:
            pass
        self.assertFalse(watch.notable())

    def test_slow_is_notable(self):
        watch = costs.Watch()
        watch.seconds = costs.SLOW_SECONDS + 0.5
        self.assertTrue(watch.notable())
        self.assertIn("to read", watch.reason())

    def test_greedy_is_notable(self):
        watch = costs.Watch()
        watch.growth = costs.GREEDY_BYTES * 4
        self.assertTrue(watch.notable())
        self.assertIn("memory", watch.reason())

    def test_unmeasurable_memory_is_absent_not_zero(self):
        """A platform that cannot answer has not answered."""
        watch = costs.Watch()
        watch.growth = None
        self.assertFalse(watch.notable())
        self.assertEqual(costs.human(None), "—")

    def test_peak_is_a_number_or_nothing(self):
        peak = costs.peak_bytes()
        if peak is not None:
            self.assertGreater(peak, 0)

    def test_measure_returns_the_result_too(self):
        value, watch = costs.measure(sum, [1, 2, 3])
        self.assertEqual(value, 6)
        self.assertIsInstance(watch, costs.Watch)


class KeepingTheWorstReading(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-costs-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def note(self, path, seconds, growth=None):
        self.journal.record_cost(path, os.path.basename(path), 1024,
                                 seconds, growth, growth, "because")

    def test_one_row_per_file(self):
        self.note("/a/slow.pdf", 3.0)
        self.note("/a/slow.pdf", 1.5)
        rows = self.journal.expensive()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["readings"], 2)

    def test_the_worst_reading_wins_not_the_latest(self):
        """The second pass reads from the page cache and looks innocent."""
        self.note("/a/slow.pdf", 40.0, 200 * 1024 * 1024)
        self.note("/a/slow.pdf", 0.2, 1024)
        row = self.journal.expensive()[0]
        self.assertEqual(row["seconds"], 40.0)
        self.assertEqual(row["growth"], 200 * 1024 * 1024)

    def test_a_missing_measurement_does_not_erase_a_real_one(self):
        self.note("/a/slow.pdf", 2.0, 99 * 1024 * 1024)
        self.note("/a/slow.pdf", 2.0, None)
        self.assertEqual(self.journal.expensive()[0]["growth"],
                         99 * 1024 * 1024)

    def test_a_fast_memory_hog_outranks_a_merely_slow_file(self):
        """The 171 MB PDF read in a third of a second. It comes first."""
        self.note("/a/slow.pdf", 8.0, None)
        self.note("/a/huge.pdf", 0.3, 171 * 1024 * 1024)
        self.assertEqual(self.journal.expensive()[0]["file_name"], "huge.pdf")

    def test_forgetting_a_row(self):
        self.note("/a/slow.pdf", 3.0)
        self.assertEqual(self.journal.forget_cost(
            self.journal.expensive()[0]["id"]), 1)
        self.assertEqual(self.journal.expensive(), [])

    def test_nothing_expensive_is_the_normal_answer(self):
        self.assertEqual(self.journal.expensive(), [])

    def test_compaction_forgets_files_nobody_has_seen_in_months(self):
        """A file dealt with a year ago should not still be on the list."""
        self.note("/a/old.pdf", 9.0)
        self.note("/a/new.pdf", 9.0)
        self.journal.connection.execute(
            "UPDATE costs SET last_seen = '2001-01-01T00:00:00' "
            "WHERE file_name = 'old.pdf'")
        self.journal.connection.commit()
        report = self.journal.compact()
        self.assertEqual(report["costs_forgotten"], 1)
        self.assertEqual([row["file_name"]
                          for row in self.journal.expensive()], ["new.pdf"])


class WhatTheSorterWritesDown(unittest.TestCase):
    """The wiring: a plan records the expensive ones and ignores the rest."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-costwire-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_an_ordinary_reading_writes_nothing(self):
        watch = costs.Watch()
        watch.seconds = 0.01
        sorter._note_cost(self.journal, os.path.join(self.dir, "x"), watch)
        self.assertEqual(self.journal.expensive(), [])

    def test_an_expensive_reading_is_recorded(self):
        path = os.path.join(self.dir, "awkward.pdf")
        with open(path, "wb") as handle:
            handle.write(b"x" * 500)
        watch = costs.Watch()
        watch.seconds = 30.0
        sorter._note_cost(self.journal, path, watch)
        row = self.journal.expensive()[0]
        self.assertEqual(row["file_name"], "awkward.pdf")
        self.assertEqual(row["size"], 500)

    def test_a_file_that_vanished_is_still_recorded(self):
        """It was expensive whether or not it is still there afterwards."""
        watch = costs.Watch()
        watch.seconds = 30.0
        sorter._note_cost(self.journal, os.path.join(self.dir, "gone.pdf"),
                          watch)
        self.assertEqual(self.journal.expensive()[0]["size"], 0)

    def test_measuring_never_breaks_a_sort(self):
        """A courtesy must not be the reason the job fails."""
        watch = costs.Watch()
        watch.seconds = 30.0
        self.journal.close()                 # every write from here throws
        sorter._note_cost(self.journal, os.path.join(self.dir, "x"), watch)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))

    def test_no_ledger_means_no_recording(self):
        watch = costs.Watch()
        watch.seconds = 30.0
        sorter._note_cost(None, "/nowhere", watch)


if __name__ == "__main__":
    unittest.main()
