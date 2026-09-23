"""A process per optional program, started only when one is needed.

The tools are other people's programs reading other people's files, and they
are slow in ways that have nothing to do with anything going wrong -- OCR is
seconds a page by its nature. Run inside the identify worker, one scanned
page stops that worker reading anything else for as long as it takes, and a
supervisor watching from outside cannot tell "wedged on a file" from
"waiting for tesseract".

So each tool gets its own process and its own patience, and hands the answer
back to the supervisor rather than to the worker that found the gap.

Nothing here needs any of the programs installed.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundles                                           # noqa: E402
import costs                                             # noqa: E402
import evidence                                          # noqa: E402
import identify                                          # noqa: E402
import jobs                                              # noqa: E402
import readers                                           # noqa: E402
import rules                                             # noqa: E402
import tool_worker                                       # noqa: E402


def a_record(**facts):
    record = evidence.Record("/tmp/thing.mkv")
    record.set("kind", "video", "test", evidence.CERTAIN)
    for name, value in facts.items():
        record.set(name, value, "header", evidence.CERTAIN)
    return record


class WhoIsAsked(unittest.TestCase):

    def test_every_tool_is_named_after_its_program(self):
        names = [name for name, _module in readers.enrichers()]
        self.assertEqual(names, ["ffprobe", "exiftool", "tesseract"])

    def test_each_one_knows_whether_it_is_wanted(self):
        for _name, enricher in readers.enrichers():
            self.assertTrue(hasattr(enricher, "wanted"))
            self.assertTrue(hasattr(enricher, "read"))

    def test_a_tool_is_given_its_own_patience(self):
        """A hung ffprobe is a problem at an age OCR is not."""
        self.assertGreater(jobs.Helpers.PATIENCE["tesseract"],
                           jobs.Helpers.PATIENCE["ffprobe"])


class OneProcessPerTool(unittest.TestCase):

    def setUp(self):
        self.helpers = jobs.Helpers(mode="auto")
        self.asked = []

    def tearDown(self):
        self.helpers.close()

    def stub(self, added=True, error=""):
        """Stand in for a tool worker, without starting one."""
        def ask(request, name=None):
            self.asked.append(name)
            if error:
                return None, error
            record = evidence.from_wire(request["record"])
            if added:
                record.set("duration", 128.4, "ffprobe", evidence.CERTAIN)
            return {"ok": True, "record": record.as_wire(),
                    "cost": {"growth": 5 * 1024 * 1024, "peak": 40e6}}, ""
        return ask

    def test_nothing_starts_until_a_file_needs_it(self):
        self.assertEqual(self.helpers.channels, {})

    def test_only_the_tools_that_want_the_file(self):
        record = a_record()                  # a video with no duration
        with mock.patch.object(jobs.Channel, "ask",
                               autospec=True,
                               side_effect=lambda ch, req: self.stub()(
                                   req, ch.label)):
            self.helpers.enrich("/tmp/thing.mkv", record)
        self.assertEqual(self.asked, ["ffprobe"])
        self.assertEqual(list(self.helpers.channels), ["ffprobe"])

    def test_the_answer_comes_back_merged(self):
        record = a_record()
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=lambda ch, req: self.stub()(
                                   req, ch.label)):
            self.helpers.enrich("/tmp/thing.mkv", record)
        self.assertEqual(record.value("duration"), 128.4)

    def test_a_fact_the_worker_let_go_of_comes_back_let_go_of(self):
        """A page that has been read is no longer waiting to be read."""
        record = a_record()
        record.set("needs_ocr", True, "pdf-text", evidence.STRONG)

        def answer(channel, request):
            fresh = evidence.from_wire(request["record"])
            fresh.drop("needs_ocr")
            fresh.set("read_by", "ocr", "ocr", evidence.CERTAIN)
            return {"ok": True, "record": fresh.as_wire()}, ""

        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=answer):
            self.helpers.enrich("/tmp/scan.pdf", record)
        self.assertEqual(record.value("read_by"), "ocr")
        self.assertFalse(record.has("needs_ocr"))

    def test_what_it_cost_comes_back_too(self):
        record = a_record()
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=lambda ch, req: self.stub()(
                                   req, ch.label)):
            self.helpers.enrich("/tmp/thing.mkv", record)
        self.assertEqual(self.helpers.last_reading["growth"], 5 * 1024 * 1024)

    def test_a_tool_that_cannot_be_reached_loses_its_facts_only(self):
        record = a_record()
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               side_effect=lambda ch, req: self.stub(
                                   error="stopped after 25 seconds without "
                                         "an answer")(req, ch.label)):
            self.helpers.enrich("/tmp/thing.mkv", record)
        self.assertFalse(record.has("duration"))
        self.assertEqual(record.value("kind"), "video")
        self.assertTrue(any("ffprobe" in note for note in record.notes))

    def test_a_tool_that_will_not_start_runs_here_instead(self):
        """A supervisor that stops the tool working has made it worse."""
        record = a_record()
        with mock.patch.object(jobs.Channel, "ask", autospec=True,
                               return_value=(None, "ffprobe could not be "
                                                   "started")), \
                mock.patch("readers.probe.read", return_value=True) as here:
            self.helpers.enrich("/tmp/thing.mkv", record)
        self.assertTrue(here.called)
        self.assertEqual(self.helpers.in_process, 1)

    def test_off_asks_nobody(self):
        self.helpers.mode = "off"
        with mock.patch.object(jobs.Channel, "ask") as ask:
            self.assertEqual(self.helpers.enrich("/tmp/thing.mkv",
                                                 a_record()), 0)
        ask.assert_not_called()

    def test_inline_runs_them_here(self):
        self.helpers.mode = "inline"
        self.assertFalse(self.helpers.separate)
        with mock.patch("readers.probe.read", return_value=True), \
                mock.patch.object(jobs.Channel, "ask") as ask:
            self.helpers.enrich("/tmp/thing.mkv", a_record())
        ask.assert_not_called()
        self.assertEqual(self.helpers.in_process, 1)


class LettingGoAgain(unittest.TestCase):
    """20 MB each, mostly interpreter. A folder swept an hour ago should
    not still be holding an OCR process for it."""

    def test_an_idle_worker_is_stopped(self):
        helpers = jobs.Helpers()
        channel = mock.Mock()
        channel.alive.return_value = True
        channel.idle_seconds.return_value = 600.0
        helpers.channels["tesseract"] = channel
        self.assertEqual(helpers.reap(), ["tesseract"])
        channel.stop.assert_called_once()

    def test_a_busy_one_is_left_alone(self):
        helpers = jobs.Helpers()
        channel = mock.Mock()
        channel.alive.return_value = True
        channel.idle_seconds.return_value = 1.0
        helpers.channels["ffprobe"] = channel
        self.assertEqual(helpers.reap(), [])
        channel.stop.assert_not_called()

    def test_closing_stops_all_of_them(self):
        helpers = jobs.Helpers()
        first, second = mock.Mock(), mock.Mock()
        helpers.channels.update({"a": first, "b": second})
        helpers.close()
        first.stop.assert_called_once()
        second.stop.assert_called_once()
        self.assertEqual(helpers.channels, {})


class TheWorkerItself(unittest.TestCase):

    def setUp(self):
        self.record = a_record()

    def test_it_runs_the_one_tool_it_is_for(self):
        with mock.patch("readers.probe.read", return_value=True) as read:
            reply = tool_worker.handle(
                "ffprobe", {"path": "/tmp/x.mkv",
                            "record": self.record.as_wire()})
        self.assertTrue(reply["ok"])
        self.assertTrue(read.called)

    def test_it_says_what_the_reading_cost(self):
        with mock.patch("readers.probe.read", return_value=True):
            reply = tool_worker.handle(
                "ffprobe", {"path": "/tmp/x.mkv",
                            "record": self.record.as_wire()})
        self.assertIn("seconds", reply["cost"])

    def test_a_tool_that_throws_loses_one_file(self):
        with mock.patch("readers.probe.read",
                        side_effect=RuntimeError("exploded")):
            reply = tool_worker.handle(
                "ffprobe", {"path": "/tmp/x.mkv",
                            "record": self.record.as_wire()})
        self.assertFalse(reply["ok"])
        self.assertIn("exploded", reply["error"])
        self.assertFalse(reply.get("fatal"))

    def test_running_out_of_memory_retires_it(self):
        with mock.patch("readers.probe.read", side_effect=MemoryError()):
            reply = tool_worker.handle(
                "ffprobe", {"path": "/tmp/x.mkv",
                            "record": self.record.as_wire()})
        self.assertTrue(reply["fatal"])

    def test_a_tool_nobody_has_heard_of(self):
        self.assertFalse(tool_worker.handle("nonesuch", {"path": "/x"})["ok"])

    def test_a_request_with_no_path(self):
        self.assertFalse(tool_worker.handle("ffprobe", {})["ok"])

    def test_a_record_that_does_not_make_sense(self):
        reply = tool_worker.handle("ffprobe",
                                   {"path": "/x", "record": "not a record"})
        self.assertFalse(reply["ok"])


class WhereTheMeasurementComesFrom(unittest.TestCase):
    """Since the reading moved out of this process, this process's own
    memory says nothing about what a file cost."""

    def test_a_watch_takes_in_a_workers_reading(self):
        watch = costs.Watch()
        watch.growth = 1024
        watch.adopt({"growth": 171 * 1024 * 1024, "peak": 206 * 1024 * 1024})
        self.assertEqual(watch.growth, 171 * 1024 * 1024)

    def test_the_worse_of_the_two_wins(self):
        watch = costs.Watch()
        watch.growth = 400 * 1024 * 1024
        watch.adopt({"growth": 1024})
        self.assertEqual(watch.growth, 400 * 1024 * 1024)

    def test_a_missing_reading_changes_nothing(self):
        watch = costs.Watch()
        watch.growth = 2048
        watch.adopt(None)
        watch.adopt({})
        self.assertEqual(watch.growth, 2048)

    def test_absent_stays_absent(self):
        watch = costs.Watch()
        watch.growth = None
        watch.adopt({"peak": 5})
        self.assertIsNone(watch.growth)


class EndToEnd(unittest.TestCase):
    """With real processes, but without needing any tool installed."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-tools-")
        self.path = os.path.join(self.dir, "note.txt")
        with open(self.path, "wb") as handle:
            handle.write(b"hello")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_file_no_tool_wants_starts_no_tool(self):
        with jobs.Reader(tools="auto") as reader:
            record, error = reader.read(bundles.Item(self.path))
            self.assertEqual(error, "")
            self.assertEqual(record.value("kind"), "document")
            self.assertEqual(reader.helpers.channels, {})

    def test_the_identify_worker_is_asked_for_everything_but_the_tools(self):
        with jobs.Reader(tools="auto") as reader:
            with mock.patch.object(jobs.Channel, "ask",
                                   return_value=(None, "x")) as ask:
                reader.read(bundles.Item(self.path))
            asked = ask.call_args[0][0]
        self.assertEqual(asked["tier"], identify.TIER_HEADER)

    def test_inline_asks_the_identify_worker_for_everything(self):
        with jobs.Reader(tools="inline") as reader:
            with mock.patch.object(jobs.Channel, "ask",
                                   return_value=(None, "x")) as ask:
                reader.read(bundles.Item(self.path))
            asked = ask.call_args[0][0]
        self.assertEqual(asked["tier"], identify.TIER_ALL)

    def test_the_setting_accepts_only_what_it_means(self):
        self.assertEqual(rules.Settings({"tools": "inline"}).tools, "inline")
        self.assertEqual(rules.Settings({}).tools, "auto")
        with self.assertRaises(rules.RuleError):
            rules.Settings({"tools": "sometimes"})


if __name__ == "__main__":
    unittest.main()
