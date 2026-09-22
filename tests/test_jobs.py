"""Killing the thing that will not stop.

Identification is the one stage in auto-sort whose input is written by
somebody else, and it is the stage that has hung twice. It now runs in a
process, because a process is the only thing here that can be taken away
from work it refuses to finish. These tests are mostly about what happens
when it goes wrong: a worker that never answers, a worker that cannot be
started at all, a worker that runs out of memory.
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
import evidence                                          # noqa: E402
import identify_worker                                   # noqa: E402
import jobs                                              # noqa: E402
import ledger                                            # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEAF = """
import sys, time
for line in sys.stdin:
    time.sleep(600)          # answers nothing, ever
"""


class CarryingFactsAcrossAPipe(unittest.TestCase):
    """A record has to survive JSON without quietly changing its mind."""

    def test_values_keep_their_types(self):
        record = evidence.Record("/tmp/thing.jpg")
        record.set("name", "thing.jpg", "extension", evidence.CERTAIN)
        record.set("size", 4096, "stat", evidence.CERTAIN)
        record.set("aspect", 1.5, "header", evidence.STRONG)
        record.set("litter", False, "name", evidence.WEAK)
        record.set("raw", b"\x00\xff", "tiff", evidence.STRONG)
        record.set("tags", ("a", "b"), "id3", evidence.LIKELY)
        back = evidence.from_wire(record.as_wire())
        self.assertEqual(back.value("size"), 4096)
        self.assertEqual(back.value("aspect"), 1.5)
        self.assertIs(back.value("litter"), False)
        self.assertEqual(back.value("raw"), b"\x00\xff")
        self.assertEqual(back.value("tags"), ("a", "b"))

    def test_confidence_and_source_survive(self):
        """A rule with its own min_confidence decides on these."""
        record = evidence.Record("/tmp/x")
        record.set("kind", "image", "signature", evidence.CERTAIN)
        record.set("genre", "rock", "name", evidence.WEAK)
        back = evidence.from_wire(record.as_wire())
        self.assertEqual(back.confidence("kind"), evidence.CERTAIN)
        self.assertEqual(back.confidence("genre"), evidence.WEAK)
        self.assertEqual(back.source("kind"), "signature")

    def test_corroboration_is_not_counted_twice(self):
        """Rebuilt by assignment, not by replaying `set`."""
        record = evidence.Record("/tmp/x")
        record.set("artist", "Boards of Canada", "id3", evidence.STRONG)
        record.set("artist", "Boards of Canada", "name", evidence.WEAK)
        raised = record.confidence("artist")
        back = evidence.from_wire(record.as_wire())
        self.assertEqual(back.confidence("artist"), raised)

    def test_disagreements_survive(self):
        record = evidence.Record("/tmp/x")
        record.set("kind", "audio", "signature", evidence.CERTAIN)
        record.set("kind", "document", "extension", evidence.LIKELY)
        back = evidence.from_wire(record.as_wire())
        self.assertEqual(back.value("kind"), "audio")
        self.assertEqual(len(back.conflicts), 1)

    def test_notes_and_readers_survive(self):
        record = evidence.Record("/tmp/x")
        record.note("the extension disagrees with the header")
        record.reader_ran("pdftext", "no text layer")
        back = evidence.from_wire(record.as_wire())
        self.assertEqual(back.notes, ["the extension disagrees with the header"])
        self.assertEqual(back.readers, [("pdftext", "no text layer")])


class ReadingInAnotherProcess(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-jobs-")
        self.readers = []

    def tearDown(self):
        for reader in self.readers:
            reader.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def reader(self, **options):
        made = jobs.Reader(**options)
        self.readers.append(made)
        return made

    def file(self, name, content=b"hello"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return bundles.Item(path)

    def test_a_real_file_comes_back_identified(self):
        record, error = self.reader().read(self.file("note.txt"))
        self.assertEqual(error, "")
        self.assertEqual(record.value("kind"), "document")
        self.assertEqual(record.value("name"), "note.txt")

    def test_one_worker_serves_many_files(self):
        """Spawning per file would cost more than the reading it protects."""
        reader = self.reader()
        reader.read(self.file("one.txt"))
        first = reader._process.pid
        reader.read(self.file("two.txt"))
        self.assertEqual(reader._process.pid, first)

    def test_a_worker_that_never_answers_is_killed(self):
        path = os.path.join(self.dir, "deaf_worker.py")
        with open(path, "w") as handle:
            handle.write(DEAF)
        reader = self.reader(timeout=0.5)
        jobs.WORKER, real = path, jobs.WORKER
        try:
            started = time.time()
            record, error = reader.read(self.file("trouble.pdf"))
        finally:
            jobs.WORKER = real
        self.assertIsNone(record)
        self.assertIn("without an answer", error)
        # The point of the whole module: it came back.
        self.assertLess(time.time() - started, 10)
        self.assertEqual(reader.killed, 1)

    def test_it_recovers_after_a_kill(self):
        path = os.path.join(self.dir, "deaf_worker.py")
        with open(path, "w") as handle:
            handle.write(DEAF)
        reader = self.reader(timeout=0.5)
        jobs.WORKER, real = path, jobs.WORKER
        try:
            reader.read(self.file("trouble.pdf"))
        finally:
            jobs.WORKER = real
        record, error = reader.read(self.file("fine.txt"))
        self.assertEqual(error, "")
        self.assertEqual(record.value("name"), "fine.txt")

    def test_no_worker_means_reading_it_here(self):
        """Supervision being unavailable must not stop the tool working."""
        reader = self.reader()
        jobs.WORKER, real = os.path.join(self.dir, "not-there.py"), jobs.WORKER
        try:
            record, error = reader.read(self.file("fine.txt"))
        finally:
            jobs.WORKER = real
        self.assertEqual(error, "")
        self.assertEqual(record.value("name"), "fine.txt")
        self.assertEqual(reader.in_process, 1)
        # One failure is not a verdict: it keeps trying until GIVE_UP_AFTER.
        self.assertEqual(reader.spawn_failures, 1)
        self.assertTrue(reader.report()["supervised"])

    def test_it_stops_trying_to_spawn_after_a_few_failures(self):
        reader = self.reader()
        jobs.WORKER, real = os.path.join(self.dir, "not-there.py"), jobs.WORKER
        try:
            for index in range(jobs.GIVE_UP_AFTER + 2):
                reader.read(self.file("f%d.txt" % index))
        finally:
            jobs.WORKER = real
        self.assertEqual(reader.spawn_failures, jobs.GIVE_UP_AFTER)
        self.assertFalse(reader.report()["supervised"])

    def test_disabling_it_reads_here(self):
        reader = self.reader(enabled=False)
        record, error = reader.read(self.file("fine.txt"))
        self.assertEqual(error, "")
        self.assertEqual(reader.in_process, 1)


class WhenTheWorkerItselfGoesWrong(unittest.TestCase):
    """The child's own behaviour, without a pipe in the way."""

    def test_a_reader_that_throws_loses_one_file_not_the_run(self):
        real = identify_worker.identify.identify

        def explode(*_args, **_kwargs):
            raise TypeError("a reader has a bug")

        identify_worker.identify.identify = explode
        try:
            reply = identify_worker.handle({"primary": "/tmp/x"})
        finally:
            identify_worker.identify.identify = real
        self.assertFalse(reply["ok"])
        self.assertIn("identification failed", reply["error"])
        self.assertFalse(reply.get("fatal"))

    def test_running_out_of_memory_retires_the_worker(self):
        real = identify_worker.identify.identify

        def starve(*_args, **_kwargs):
            raise MemoryError()

        identify_worker.identify.identify = starve
        try:
            reply = identify_worker.handle({"primary": "/tmp/x"})
        finally:
            identify_worker.identify.identify = real
        self.assertFalse(reply["ok"])
        self.assertIn("memory", reply["error"])
        self.assertTrue(reply["fatal"])

    def test_a_request_with_no_path_is_answered_not_crashed(self):
        self.assertFalse(identify_worker.handle({})["ok"])

    def test_the_ceiling_is_set_where_it_can_be(self):
        """A fuse far above the budget, and no error where it is refused."""
        self.assertIn(identify_worker.set_ceiling(4096), (True, False))
        self.assertFalse(identify_worker.set_ceiling(0))


class WhatASortDoesWithAFileThatWillNotRead(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-jobplan-")
        self.root = os.path.join(self.dir, "in")
        os.makedirs(self.root)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.rule_set = rules.load(os.path.join(REPO, "rules.example.ini"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, name):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(b"hello")
        return path

    def test_the_unreadable_one_is_set_aside_and_the_rest_are_sorted(self):
        awkward = self.make("awkward.pdf")
        self.make("ordinary.txt")
        items = list(bundles.walk(self.root, max_depth=3))

        class Stubborn(object):
            """Answers for everything except one file."""

            def read(self, item, tier=None, ocr="auto"):
                if item.primary == awkward:
                    return None, ("stopped after 30 seconds without an "
                                  "answer; the file was left alone")
                import identify
                return identify.identify(item), ""

        plan = sorter.build_plan(self.root, self.rule_set, items=items,
                                 journal=self.journal, reader=Stubborn())
        planned = [os.path.basename(item.item.primary) for item in plan.items]
        self.assertEqual(planned, ["ordinary.txt"])
        reasons = dict(plan.skipped)
        self.assertIn("without an answer", reasons[awkward])

    def test_a_file_that_hung_says_so_in_the_cost_report(self):
        """The hang that was invisible now leaves a note by itself."""
        awkward = self.make("awkward.pdf")
        items = [item for item in bundles.walk(self.root, max_depth=3)
                 if item.primary == awkward]

        class Slow(object):
            def read(self, item, tier=None, ocr="auto"):
                time.sleep(0.05)
                return None, "stopped after 30 seconds without an answer"

        import costs
        real = costs.SLOW_SECONDS
        costs.SLOW_SECONDS = 0.01
        try:
            sorter.build_plan(self.root, self.rule_set, items=items,
                              journal=self.journal, reader=Slow())
        finally:
            costs.SLOW_SECONDS = real
        rows = self.journal.expensive()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file_name"], "awkward.pdf")
        self.assertIn("without an answer", rows[0]["reason"])


if __name__ == "__main__":
    unittest.main()
