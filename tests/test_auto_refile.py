"""The background sorter going back over what it filed, by itself.

A sorter called auto-sort that leaves the old mess where an older rules
file put it, until somebody thinks to type `refile`, keeps half its
promise. So a new rules file -- or a new reader -- is answered once: the
filed files are judged again and the ones a category now claims move.
Once, and not on a clock, because reading every filed document again is
the most expensive thing the program does.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                            # noqa: E402
import ledger                                            # noqa: E402
import regroup                                           # noqa: E402

BEFORE = """
[settings]
dry_run = no
{setting}

[watch]
folders = {root}

[rule: anything]
when = name is set
into = {out}/Misc
"""

AFTER = """
[settings]
dry_run = no
{setting}

[watch]
folders = {root}

[rule: letters]
when = name ~ *letter*
into = {out}/Letters

[rule: anything]
when = name is set
into = {out}/Misc
"""


class RefilingByItself(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-autorefile-")
        self.root = os.path.join(self.dir, "in")
        self.out = os.path.join(self.dir, "out")
        self.rules = os.path.join(self.dir, "rules.ini")
        self.state = os.path.join(self.dir, "state.db")
        os.makedirs(self.root)
        self.filed = self.place("offer letter.txt")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def place(self, name):
        """A file the first rules file put in Misc, as the ledger says."""
        where = os.path.join(self.out, "Misc")
        os.makedirs(where, exist_ok=True)
        path = os.path.join(where, name)
        with open(path, "w") as handle:
            handle.write("Dear applicant")
        with ledger.Ledger(self.state) as journal:
            run = journal.start_run("sort", source_root=self.root,
                                    dry_run=False)
            journal.add_move(run, 1, 1, "move", "anything",
                             os.path.join(self.root, name), path, 1, "",
                             status="done", facts={"kind": "document"})
            journal.finish_run(run, "completed")
        return path

    def write(self, text, setting=""):
        with open(self.rules, "w") as handle:
            handle.write(text.format(root=self.root, out=self.out,
                                     setting=setting))

    def service(self, messages):
        return daemon.PollingDaemon(self.rules, self.state, port=0,
                                    output=messages.append)

    def letter(self):
        return os.path.join(self.out, "Letters", "offer letter.txt")

    def test_a_new_rule_takes_the_files_it_claims(self):
        self.write(BEFORE)
        messages = []
        with self.service(messages) as service:
            service.cycle(now_value=100)
            self.assertTrue(os.path.exists(self.filed))
            self.write(AFTER)
            # The first apply for new rules is a preview, as every first
            # apply is; the one after it moves.
            service.cycle(now_value=101)
            service.cycle(now_value=102)
        self.assertFalse(os.path.exists(self.filed))
        self.assertTrue(os.path.exists(self.letter()))
        self.assertTrue(any(message.startswith("Refiled 1 item")
                            for message in messages), messages)

    def test_it_happens_once_for_each_change_not_on_a_clock(self):
        self.write(AFTER)
        messages = []
        with self.service(messages) as service:
            for moment in range(100, 104):
                service.cycle(now_value=moment)
            self.assertTrue(os.path.exists(self.letter()))
            asked = []
            original = regroup.filed

            def counting(*args, **kwargs):
                asked.append(1)
                return original(*args, **kwargs)
            regroup.filed = counting
            try:
                for moment in range(104, 108):
                    service.cycle(now_value=moment)
            finally:
                regroup.filed = original
        self.assertEqual(asked, [])

    def test_report_only_says_so(self):
        self.write(AFTER, "regroup = report")
        messages = []
        with self.service(messages) as service:
            service.cycle(now_value=100)
            service.cycle(now_value=101)
        self.assertTrue(os.path.exists(self.filed))
        self.assertEqual(
            [message for message in messages
             if "would go to a different category" in message],
            ["1 filed file would go to a different category now. Run "
             "`auto-sort refile` to see, or set regroup = apply."])

    def test_off_never_looks(self):
        self.write(AFTER, "regroup = off")
        messages = []
        with self.service(messages) as service:
            service.cycle(now_value=100)
            service.cycle(now_value=101)
        self.assertTrue(os.path.exists(self.filed))
        self.assertFalse(any("categor" in message for message in messages))

    def test_a_file_somebody_moved_is_left_where_they_put_it(self):
        self.write(BEFORE)
        messages = []
        mine = os.path.join(self.dir, "mine")
        os.makedirs(mine)
        moved = os.path.join(mine, "offer letter.txt")
        os.rename(self.filed, moved)
        with self.service(messages) as service:
            service.cycle(now_value=100)
            self.write(AFTER)
            service.cycle(now_value=101)
            service.cycle(now_value=102)
        self.assertTrue(os.path.exists(moved))
        self.assertFalse(os.path.exists(self.letter()))

    def test_a_few_at_a_time_so_the_icon_keeps_answering(self):
        for number in range(2, 8):
            self.place("letter %d.txt" % number)
        self.write(AFTER)
        messages = []
        read = []
        import review
        original = review.refresh_held

        def counting(journal, **kwargs):
            read.append(len(kwargs.get("rows") or []))
            return original(journal, **kwargs)
        with self.service(messages) as service:
            service.REFILE_BATCH = 3
            review.refresh_held = counting
            try:
                for moment in range(100, 112):
                    service.cycle(now_value=moment)
            finally:
                review.refresh_held = original
        self.assertTrue(read and max(read) <= 3, read)
        self.assertEqual(sorted(os.listdir(os.path.join(self.out,
                                                        "Letters"))),
                         sorted(["offer letter.txt"]
                                + ["letter %d.txt" % n for n in range(2, 8)]))

    def test_read_by_the_worker_that_can_be_stopped(self):
        """A filed file is somebody else's bytes, like any new arrival."""
        self.write(AFTER)
        messages = []
        read = []
        with self.service(messages) as service:
            original = service.reader.read

            def recording(item, *args, **kwargs):
                read.append(os.path.basename(item.primary))
                return original(item, *args, **kwargs)
            service.reader.read = recording
            with unittest.mock.patch("identify.identify",
                                     side_effect=AssertionError(
                                         "read in the daemon's process")):
                service.reader.enabled = True
                for moment in range(100, 103):
                    service.cycle(now_value=moment)
        self.assertIn("offer letter.txt", read)
        self.assertTrue(os.path.exists(self.letter()))

    def test_a_file_the_reader_gives_up_on_stays_put(self):
        self.write(AFTER)
        messages = []
        with self.service(messages) as service:
            service.reader.read = lambda item, *args, **kwargs: (
                None, "the reader stopped answering")
            for moment in range(100, 103):
                service.cycle(now_value=moment)
        self.assertTrue(os.path.exists(self.filed))


HOLDING = """
[settings]
dry_run = no

[watch]
folders = {root}

[rule: waiting]
when = name is set
into = {out}/Unfiled
holding = yes
"""

ADOPTED = """
[settings]
dry_run = no

[watch]
folders = {root}

[rule: letters]
when = name ~ *letter*
into = {out}/Letters

[rule: waiting]
when = name is set
into = {out}/Unfiled
holding = yes
"""


class PromotedWhenARuleArrives(unittest.TestCase):
    """Adopting a category moves what was waiting for it now, not later."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-promote-")
        self.root = os.path.join(self.dir, "in")
        self.out = os.path.join(self.dir, "out")
        self.rules = os.path.join(self.dir, "rules.ini")
        self.state = os.path.join(self.dir, "state.db")
        os.makedirs(self.root)
        where = os.path.join(self.out, "Unfiled")
        os.makedirs(where)
        self.waiting = os.path.join(where, "offer letter.txt")
        with open(self.waiting, "w") as handle:
            handle.write("Dear applicant")
        with ledger.Ledger(self.state) as journal:
            run = journal.start_run("sort", source_root=self.root,
                                    dry_run=False)
            journal.add_move(run, 1, 1, "move", "waiting",
                             os.path.join(self.root, "offer letter.txt"),
                             self.waiting, 1, "", status="done",
                             facts={"kind": "document"}, holding=True)
            journal.finish_run(run, "completed")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text):
        with open(self.rules, "w") as handle:
            handle.write(text.format(root=self.root, out=self.out))

    def test_within_a_cycle_or_two_of_the_rule(self):
        self.write(HOLDING)
        messages = []
        with daemon.PollingDaemon(self.rules, self.state, port=0,
                                  output=messages.append) as service:
            service.cycle(now_value=1000)
            self.write(ADOPTED)
            # Seconds later, far inside the half-hour between checks.
            service.cycle(now_value=1005)
            service.cycle(now_value=1010)
        self.assertFalse(os.path.exists(self.waiting))
        self.assertTrue(os.path.exists(
            os.path.join(self.out, "Letters", "offer letter.txt")), messages)


class TheReaderMark(unittest.TestCase):

    def test_is_stable_within_a_run(self):
        self.assertEqual(regroup.reader_mark(), regroup.reader_mark())
        self.assertEqual(len(regroup.reader_mark()), 16)


if __name__ == "__main__":
    unittest.main()
