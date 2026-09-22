"""Saying that something is still happening, without becoming the something.

463 files took over two minutes, almost all of it reading PDFs, and said
nothing while it did. On a twenty-year folder that silence is the problem:
a program that has gone quiet looks exactly like a program that has gone
wrong.
"""

from __future__ import annotations

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import progress                                          # noqa: E402


class Watched(io.StringIO):
    def isatty(self):
        return True


def frames(ticker):
    return [part for part in ticker.stream.getvalue().split("\r")
            if ticker.label in part]


class OnlyWhenSomebodyIsWatching(unittest.TestCase):

    def test_a_pipe_gets_nothing(self):
        """`auto-sort propose > rules.ini` must not have a counter in it."""
        ticker = progress.Ticker(label="Reading", stream=io.StringIO())
        for index in range(1, 200):
            ticker.tick(index, 199, "file.pdf")
        ticker.close()
        self.assertEqual(ticker.stream.getvalue(), "")

    def test_a_terminal_gets_a_line(self):
        ticker = progress.Ticker(label="Reading", stream=Watched())
        ticker.tick(1, 200, "first.pdf")
        self.assertIn("Reading 1/200", frames(ticker)[0])

    def test_a_handful_of_files_is_not_worth_a_counter(self):
        ticker = progress.Ticker(label="Reading", stream=Watched())
        for index in range(1, 6):
            ticker.tick(index, 5, "file.pdf")
        self.assertEqual(frames(ticker), [])

    def test_a_stream_that_breaks_stops_reporting_rather_than_raising(self):
        """A courtesy must never be the thing that stops a sort."""
        ticker = progress.Ticker(label="Reading", stream=Watched())
        ticker.tick(1, 100, "one.pdf")
        ticker.stream.close()
        # The last step of a run always writes, rate limit or not, so this
        # is the write that meets the closed stream.
        ticker.tick(100, 100, "two.pdf")     # must not raise
        self.assertFalse(ticker.enabled)


class WhatTheLineSays(unittest.TestCase):

    def ticker(self, **options):
        return progress.Ticker(stream=Watched(), label="Reading", **options)

    def test_it_counts_towards_something_when_that_is_known(self):
        ticker = self.ticker()
        ticker.tick(50, 400, "x.pdf")
        self.assertIn("50/400", frames(ticker)[0])

    def test_it_counts_up_when_nothing_knows_the_total(self):
        """A survey does not find out how many there are until it is done."""
        ticker = self.ticker()
        ticker.step(done=4000)
        self.assertIn("Reading 4,000", frames(ticker)[0])
        self.assertNotIn("/", frames(ticker)[0])

    def test_the_last_step_is_always_shown(self):
        ticker = self.ticker()
        for index in range(1, 101):
            ticker.tick(index, 100, "file.pdf")
        self.assertIn("100/100", frames(ticker)[-1])

    def test_it_does_not_report_ten_thousand_times_a_second(self):
        ticker = self.ticker()
        for index in range(1, 5001):
            ticker.tick(index, 5000, "file.pdf")
        # First, last, and whatever the clock allowed in between.
        self.assertLess(len(frames(ticker)), 40)

    def test_a_long_name_does_not_wrap_the_line(self):
        ticker = self.ticker()
        ticker.tick(1, 100, "a-really-quite-unreasonably-long-file-name-"
                            "that-somebody-actually-has.pdf")
        for frame in frames(ticker):
            self.assertLess(len(frame), 200)

    def test_an_estimate_is_offered_only_when_it_is_not_a_guess(self):
        self.assertEqual(progress._remaining(1, 1000, 0.0), "")
        self.assertEqual(progress._remaining(500, 500, 0.0), "")

    def test_the_line_is_erased_afterwards(self):
        ticker = self.ticker()
        ticker.tick(1, 100, "file.pdf")
        ticker.close()
        self.assertTrue(ticker.stream.getvalue().endswith("\r"))
        self.assertEqual(ticker.width, 0)

    def test_closing_twice_is_harmless(self):
        ticker = self.ticker()
        ticker.tick(1, 100, "file.pdf")
        ticker.close()
        ticker.close()

    def test_a_silent_ticker_can_be_used_like_any_other(self):
        ticker = progress.none()
        ticker.tick(1, 100, "file.pdf")
        ticker.close()
        self.assertFalse(ticker.enabled)


class ElidingTheMiddle(unittest.TestCase):
    """The end of a filename is the informative part of it."""

    def test_it_keeps_both_ends(self):
        short = progress._short("firefox-1.5.0.12.installer.exe", 20)
        self.assertEqual(len(short), 20)
        self.assertTrue(short.endswith(".exe"))
        self.assertTrue(short.startswith("fire"))

    def test_a_name_that_fits_is_untouched(self):
        self.assertEqual(progress._short("short.pdf", 40), "short.pdf")


if __name__ == "__main__":
    unittest.main()
