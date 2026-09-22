"""A line that says something is still happening, for the times it is not
obvious that anything is.

Four hundred and sixty-three files took over two minutes, almost all of it
reading PDFs. On a folder with twenty years in it that is real time with no
feedback at all, and a program that has gone quiet is indistinguishable from
a program that has gone wrong — which is exactly the thing this project has
spent its last two changes making sure is not happening.

Three rules, all of them about not being annoying:

**Only when somebody is watching.** If the output is a pipe or a file,
nothing is written. `auto-sort propose > rules.ini` should not have a
progress counter in it, and a daemon's log should not be a hundred thousand
lines of one file's name each.

**Never more than ten times a second.** A counter that costs measurable
time is a counter reporting on itself.

**Leave nothing behind.** The line is erased when the work finishes, so what
remains on screen is the result and not the scaffolding.
"""

from __future__ import annotations

import shutil
import sys
import time

MIN_INTERVAL = 0.1

# Below this many items the work is over before a counter would say
# anything, and a line that appears and vanishes reads as a flicker.
WORTH_SHOWING = 20


def _watching(stream):
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _short(text, room):
    """Middle-elided, because the end of a filename is the informative part."""
    if room <= 1 or len(text) <= room:
        return text
    if room < 6:
        return text[-room:]
    tail = int((room - 1) * 0.6)
    return text[:room - 1 - tail] + "…" + text[-tail:]


def _remaining(done, total, started):
    """An estimate, or nothing at all when it would be a guess."""
    elapsed = time.monotonic() - started
    if done < 3 or elapsed < 3 or done >= total:
        return ""
    left = (elapsed / done) * (total - done)
    if left < 5:
        return ""
    if left < 90:
        return ", about %ds left" % int(left)
    return ", about %d min left" % max(1, int(round(left / 60.0)))


class Ticker(object):
    """One self-overwriting line of progress on a terminal, or silence."""

    def __init__(self, total=0, label="Reading", stream=None):
        self.stream = sys.stderr if stream is None else stream
        self.total = int(total)
        self.label = label
        self.done = 0
        self.width = 0            # of the line as last written, for erasing
        self.started = time.monotonic()
        self._last = 0.0
        self.enabled = _watching(self.stream)

    def step(self, name="", done=None):
        self.done = self.done + 1 if done is None else int(done)
        if not self.enabled:
            return
        # A total of zero means nobody knows how many there are -- a survey
        # does not find that out until it has finished -- so it counts up
        # instead of towards something, and promises nothing about when.
        if self.total and self.total < WORTH_SHOWING:
            return
        now = time.monotonic()
        if now - self._last < MIN_INTERVAL and self.done != self.total:
            return
        self._last = now
        if self.total:
            head = "  %s %s/%s%s" % (
                self.label, "{:,}".format(self.done),
                "{:,}".format(self.total),
                _remaining(self.done, self.total, self.started))
        else:
            head = "  %s %s" % (self.label, "{:,}".format(self.done))
        columns = shutil.get_terminal_size((80, 24)).columns
        room = columns - len(head) - 3
        line = head if room < 8 or not name \
            else "%s  %s" % (head, _short(name, room))
        self._write("\r" + line.ljust(self.width)[:columns - 1])
        self.width = len(line)

    def tick(self, done, total, name=""):
        """The shape `build_plan` reports in: how far, how many, which one."""
        self.total = int(total)
        self.step(name, done)

    def close(self):
        """Erase the line. Safe to call twice, and on a Ticker that never
        wrote anything."""
        if self.enabled and self.width:
            self._write("\r" + " " * min(self.width, 200) + "\r")
        self.width = 0

    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            # A closed or redirected stream must never be the thing that
            # stops a sort. Stop reporting and carry on working.
            self.enabled = False

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()


def none():
    """A ticker that never shows anything, for callers that want one
    unconditionally."""
    ticker = Ticker(0)
    ticker.enabled = False
    return ticker
