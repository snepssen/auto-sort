"""Two things in the state folder that grow, and nothing that shrank them.

The log is now the only account of what a daemon nobody started from a
terminal has been doing, so it grows forever. And a run that named its own
`--state` leaves a database behind that nothing ever looks at again.

Neither is deleted. Deleting is not something this program does -- the log
keeps its recent end and loses its beginning, and an abandoned database is
mentioned and left exactly where it is.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                            # noqa: E402
import paths                                             # noqa: E402


class TrimmingALog(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-log-")
        self.log = os.path.join(self.dir, "daemon.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, lines):
        with open(self.log, "wb") as handle:
            for index in range(lines):
                handle.write(b"line %06d: something happened\n" % index)

    def test_a_short_log_is_left_alone(self):
        self.write(10)
        before = os.path.getsize(self.log)
        self.assertEqual(paths.trim_file(self.log, 64 * 1024), 0)
        self.assertEqual(os.path.getsize(self.log), before)

    def test_a_long_log_keeps_its_recent_end(self):
        self.write(40000)
        freed = paths.trim_file(self.log, 16 * 1024)
        self.assertGreater(freed, 0)
        with open(self.log, "rb") as handle:
            kept = handle.read()
        self.assertLess(len(kept), 40 * 1024)
        self.assertIn(b"line 039999", kept)
        self.assertNotIn(b"line 000001", kept)

    def test_it_says_that_it_trimmed(self):
        self.write(40000)
        paths.trim_file(self.log, 16 * 1024)
        with open(self.log, "rb") as handle:
            self.assertTrue(handle.readline().startswith(b"[earlier"))

    def test_it_keeps_whole_lines(self):
        """Half a sentence from a sentence nobody can see the start of."""
        self.write(40000)
        paths.trim_file(self.log, 16 * 1024)
        with open(self.log, "rb") as handle:
            handle.readline()                 # the trimmed marker
            self.assertRegex(handle.readline(), br"^line \d{6}: ")

    def test_the_file_keeps_its_place_on_disk(self):
        """The daemon's own stdout is this file, and launchd's too."""
        self.write(40000)
        before = os.stat(self.log).st_ino
        paths.trim_file(self.log, 16 * 1024)
        self.assertEqual(os.stat(self.log).st_ino, before)

    def test_a_log_that_is_not_there_is_not_a_problem(self):
        self.assertEqual(paths.trim_file(os.path.join(self.dir, "no.log")), 0)


class NoticingWhatNothingUses(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-strays-")
        self.active = os.path.join(self.dir, "state.db")
        for name in ("state.db", "state.db-wal", "state.db-shm",
                     "downloads-inbox.db", "downloads-inbox.db-wal",
                     "daemon.log", "rules.ini"):
            with open(os.path.join(self.dir, name), "wb") as handle:
                handle.write(b"x" * 100)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def found(self):
        return [os.path.basename(path)
                for path, _size in paths.strays(self.dir, [self.active])]

    def test_the_live_ledger_is_not_a_stray(self):
        self.assertNotIn("state.db", self.found())

    def test_nor_are_its_own_side_files(self):
        """A `-wal` beside the live database is the live database."""
        self.assertNotIn("state.db-wal", self.found())
        self.assertNotIn("state.db-shm", self.found())

    def test_an_abandoned_database_is(self):
        self.assertIn("downloads-inbox.db", self.found())
        self.assertIn("downloads-inbox.db-wal", self.found())

    def test_nothing_that_is_not_a_database(self):
        self.assertNotIn("daemon.log", self.found())
        self.assertNotIn("rules.ini", self.found())

    def test_a_folder_that_is_not_there(self):
        self.assertEqual(paths.strays("/nowhere/at/all", []), [])

    def test_sizes_come_back_with_them(self):
        sizes = dict(paths.strays(self.dir, [self.active]))
        self.assertEqual(
            sizes[os.path.join(self.dir, "downloads-inbox.db")], 100)


class TheDaemonSayingSo(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-tidy-")
        self.rules = os.path.join(self.dir, "rules.ini")
        with open(self.rules, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = yes\n\n[watch]\nfolders = %s\n"
                         "\n[rule: images]\nwhen = kind = image\ninto = %s/out\n"
                         % (self.dir, self.dir))
        self.said = []
        self.service = daemon.PollingDaemon(
            self.rules, os.path.join(self.dir, "state.db"), port=0,
            output=self.said.append)

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def stray(self, name="old-run.db", size=2 * 1024 * 1024):
        with open(os.path.join(self.dir, name), "wb") as handle:
            handle.write(b"x" * size)

    def test_it_mentions_them(self):
        self.stray()
        self.service._tidy_state_dir(1000.0)
        self.assertTrue(any("unused database" in line for line in self.said),
                        self.said)

    def test_it_mentions_them_once(self):
        """A remark repeated hourly is a complaint."""
        self.stray()
        self.service._tidy_state_dir(1000.0)
        self.service.journal.set_state("state_dir_checked_at", "0")
        self.service._tidy_state_dir(200000.0)
        self.assertEqual(
            len([line for line in self.said if "unused database" in line]), 1)

    def test_a_new_one_appearing_is_worth_saying_again(self):
        self.stray()
        self.service._tidy_state_dir(1000.0)
        self.stray("another-run.db")
        self.service.journal.set_state("state_dir_checked_at", "0")
        self.service._tidy_state_dir(200000.0)
        self.assertEqual(
            len([line for line in self.said if "unused database" in line]), 2)

    def test_a_tidy_folder_says_nothing(self):
        self.said[:] = []                    # the daemon announced its URL
        self.service._tidy_state_dir(1000.0)
        self.assertEqual(self.said, [])

    def test_it_does_not_check_every_cycle(self):
        self.stray()
        self.service._tidy_state_dir(1000.0)
        self.said[:] = []
        self.service._tidy_state_dir(1001.0)
        self.assertEqual(self.said, [])

    def test_it_never_removes_anything(self):
        self.stray()
        self.service._tidy_state_dir(1000.0)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "old-run.db")))


class TheLogSaysWhen(unittest.TestCase):
    """Old "sorting paused" lines read exactly like a daemon stuck now."""

    def test_each_line_carries_the_time(self):
        import contextlib
        import io
        import re
        import daemon
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            daemon._stamped("Loaded 46 rules")
        self.assertRegex(out.getvalue(),
                         r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d Loaded 46 rules\n$")


if __name__ == "__main__":
    unittest.main()
