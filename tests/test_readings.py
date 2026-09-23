"""Paying for an expensive reading once.

A file passes through the funnel once and is then sorted, so the work of
reading it should happen once too. It did not: the first run against a new
folder is forced to be a preview, and the run that follows reads everything
again. Every scanned page was read twice before anything had gone wrong.

What a tool says cannot change while the file does not, so it is kept
against the file's size and modification time.
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

import evidence                                          # noqa: E402
import jobs                                              # noqa: E402
import ledger                                            # noqa: E402


class KeepingWhatWasSaid(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-readings-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.path = os.path.join(self.dir, "scan.pdf")
        with open(self.path, "wb") as handle:
            handle.write(b"%PDF-1.4\n")

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_reading_comes_back_for_the_same_file(self):
        mark = jobs._fingerprint(self.path)
        self.journal.remember_reading(self.path, "tesseract", mark,
                                      {"facts": []}, 1.5)
        self.assertIsNotNone(
            self.journal.recall_reading(self.path, "tesseract", mark))

    def test_a_changed_file_is_read_again(self):
        mark = jobs._fingerprint(self.path)
        self.journal.remember_reading(self.path, "tesseract", mark,
                                      {"facts": []}, 1.5)
        with open(self.path, "ab") as handle:
            handle.write(b"more")
        self.assertIsNone(self.journal.recall_reading(
            self.path, "tesseract", jobs._fingerprint(self.path)))

    def test_one_tool_does_not_answer_for_another(self):
        mark = jobs._fingerprint(self.path)
        self.journal.remember_reading(self.path, "tesseract", mark,
                                      {"facts": []}, 1.5)
        self.assertIsNone(
            self.journal.recall_reading(self.path, "ffprobe", mark))

    def test_a_file_that_is_not_there_has_no_fingerprint(self):
        self.assertEqual(jobs._fingerprint("/nowhere/at/all"), "")

    def test_a_reading_is_forgotten_when_the_file_moves_on(self):
        mark = jobs._fingerprint(self.path)
        self.journal.remember_reading(self.path, "tesseract", mark,
                                      {"facts": []}, 1.5)
        self.assertEqual(self.journal.forget_readings(self.path), 1)
        self.assertIsNone(
            self.journal.recall_reading(self.path, "tesseract", mark))

    def test_compaction_lets_go_of_old_ones(self):
        """An optimisation, not history."""
        mark = jobs._fingerprint(self.path)
        self.journal.remember_reading(self.path, "tesseract", mark,
                                      {"facts": []}, 1.5)
        self.journal.connection.execute(
            "UPDATE readings SET made_at = '2001-01-01T00:00:00'")
        self.journal.connection.commit()
        self.journal.compact()
        self.assertIsNone(
            self.journal.recall_reading(self.path, "tesseract", mark))


class WhatIsKept(unittest.TestCase):
    """The difference a tool pass made, in a shape that can be replayed."""

    def record(self):
        record = evidence.Record("/tmp/scan.pdf")
        record.set("kind", "document", "signature", evidence.CERTAIN)
        record.set("needs_ocr", True, "pdf-text", evidence.STRONG)
        return record

    def test_facts_added(self):
        record = self.record()
        before = set(record.names())
        record.set("heading", "Rechnung Nr 4711", "ocr", evidence.LIKELY)
        delta = jobs._difference(record, before)
        self.assertEqual([entry[0] for entry in delta["facts"]], ["heading"])

    def test_facts_dropped(self):
        record = self.record()
        before = set(record.names())
        record.drop("needs_ocr")
        self.assertEqual(jobs._difference(record, before)["dropped"],
                         ["needs_ocr"])

    def test_replaying_it_gives_the_same_record(self):
        first = self.record()
        before = set(first.names())
        first.set("heading", "Rechnung Nr 4711", "ocr", evidence.LIKELY)
        first.set("words_read", 323, "ocr", evidence.CERTAIN)
        first.drop("needs_ocr")
        delta = jobs._difference(first, before)

        second = self.record()
        jobs._apply(second, delta)
        self.assertEqual(second.value("heading"), "Rechnung Nr 4711")
        self.assertEqual(second.value("words_read"), 323)
        self.assertFalse(second.has("needs_ocr"))

    def test_a_replayed_fact_keeps_its_strength(self):
        """A heading from OCR is LIKELY however it reached the record."""
        first = self.record()
        before = set(first.names())
        first.set("heading", "Rechnung", "ocr", evidence.LIKELY)
        second = self.record()
        jobs._apply(second, jobs._difference(first, before))
        self.assertEqual(second.confidence("heading"), evidence.LIKELY)
        self.assertEqual(second.source("heading"), "ocr")

    def test_values_that_are_not_plain_survive_the_trip(self):
        first = self.record()
        before = set(first.names())
        first.set("tags", ("a", "b"), "tool", evidence.STRONG)
        second = self.record()
        jobs._apply(second, jobs._difference(first, before))
        self.assertEqual(second.value("tags"), ("a", "b"))

    def test_it_never_overrules_what_is_already_known(self):
        first = self.record()
        before = set(first.names())
        first.set("heading", "from last time", "ocr", evidence.LIKELY)
        second = self.record()
        second.set("heading", "from the text layer", "pdf-text",
                   evidence.STRONG)
        jobs._apply(second, jobs._difference(first, before))
        self.assertEqual(second.value("heading"), "from the text layer")


class NotAskingTwice(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-recall-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.path = os.path.join(self.dir, "thing.mkv")
        with open(self.path, "wb") as handle:
            handle.write(b"\x1a\x45\xdf\xa3")
        self.helpers = jobs.Helpers(mode="auto", journal=self.journal)

    def tearDown(self):
        self.helpers.close()
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def a_video(self):
        record = evidence.Record(self.path)
        record.set("kind", "video", "signature", evidence.CERTAIN)
        return record

    def answer(self, _channel, request):
        fresh = evidence.from_wire(request["record"])
        fresh.set("duration", 128.4, "ffprobe", evidence.CERTAIN)
        return {"ok": True, "record": fresh.as_wire()}, ""

    def test_the_second_time_costs_no_process(self):
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=self.answer) as ask:
            self.helpers.enrich(self.path, self.a_video())
            self.assertEqual(ask.call_count, 1)
            second = self.a_video()
            self.helpers.enrich(self.path, second)
            self.assertEqual(ask.call_count, 1, "it asked again")
        self.assertEqual(second.value("duration"), 128.4)
        self.assertEqual(self.helpers.recalled, 1)

    def test_a_changed_file_is_asked_about_again(self):
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=self.answer) as ask:
            self.helpers.enrich(self.path, self.a_video())
            with open(self.path, "ab") as handle:
                handle.write(b"more bytes")
            self.helpers.enrich(self.path, self.a_video())
        self.assertEqual(ask.call_count, 2)

    def test_a_tool_that_found_nothing_is_also_remembered(self):
        """The second pass should not pay to be told nothing again."""
        def nothing(_channel, request):
            return {"ok": True, "record": request["record"]}, ""

        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=nothing) as ask:
            self.helpers.enrich(self.path, self.a_video())
            self.helpers.enrich(self.path, self.a_video())
        self.assertEqual(ask.call_count, 1)

    def test_without_a_ledger_it_simply_asks(self):
        helpers = jobs.Helpers(mode="auto")          # no journal
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=self.answer) as ask:
            helpers.enrich(self.path, self.a_video())
            helpers.enrich(self.path, self.a_video())
        helpers.close()
        self.assertEqual(ask.call_count, 2)

    def test_a_ledger_that_throws_is_not_the_end_of_anything(self):
        self.journal.close()                 # every read from here raises
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=self.answer):
            record = self.a_video()
            self.helpers.enrich(self.path, record)
        self.assertEqual(record.value("duration"), 128.4)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))


if __name__ == "__main__":
    unittest.main()
