"""What one file cost to read, for the few files where that is a number
worth keeping.

There is no crash reporter here and there never will be, so the ledger has
to be the telemetry. Two files have frozen this program during development
— a name whose digits made a regex quadratic, and a 0.91 MB PDF whose
streams inflated to 171 MB — and both were invisible until somebody went
looking with `ps`. Had either one simply written down what it cost, it
would have been a bug report on day one instead of a mystery about a
disappearing menu bar icon.

So: time every identification, and keep the ones that were expensive.

Two things this deliberately is not.

It is not a profiler. One wall-clock reading and one memory high-water mark
per file, both of which the operating system is already keeping — nothing
is sampled, nothing is instrumented, and a file that behaves costs a
`time.monotonic()` call.

It is not a limit. Nothing here refuses, kills or skips anything. A file
that needs 340 MB to read gets 340 MB and a line in a table saying so. The
person whose fan is spinning can then see which file it was, which is the
whole point; deciding what to do about it is theirs.

Memory is a high-water mark, which has a consequence worth stating. The
figure recorded is how much a file pushed the process *above its previous
worst*, so the first pathological file is measured in full and an identical
one read a minute later looks free. That is the right bias for finding the
outlier and the wrong bias for measuring an average, and this module only
claims to do the first.
"""

from __future__ import annotations

import os
import sys
import time

# Over a second of wall clock to read one file is already strange; the
# quadratic regex spent hours. Any file over this is worth a row.
SLOW_SECONDS = 1.0

# Reading a scanned page means launching another program and waiting for it
# to look at a photograph, which is seconds by its nature rather than by
# anything going wrong. Judging that against the ordinary threshold would
# fill the report with every scan in the folder and bury the one file that
# actually misbehaved -- expensive is relative to what the file asked for.
OCR_SLOW_SECONDS = 25.0

# Growth, by one file, over everything the process had ever used before it.
# 16 MB is far past any header this program reads on purpose and far below
# the 171 MB that started this.
GREEDY_BYTES = 16 * 1024 * 1024

# The stated footprint target. Crossing it is recorded, never punished:
# a file that genuinely needs more memory should get it.
SOFT_BUDGET = 100 * 1024 * 1024


def _unix_peak():
    try:
        import resource
    except ImportError:                      # Windows has no resource module
        return None
    try:
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # macOS reports bytes and Linux reports kilobytes, for the same field of
    # the same struct. Nothing in the API says which; the platform does.
    return int(value) if sys.platform == "darwin" else int(value) * 1024


def _windows_peak():
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    import winapi
    handle = winapi.load("kernel32").GetCurrentProcess()
    ok = winapi.load("psapi").GetProcessMemoryInfo(
        handle, ctypes.byref(counters), counters.cb)
    return int(counters.PeakWorkingSetSize) if ok else None


def peak_bytes():
    """The most memory this process has ever held, or None if unknowable.

    None rather than zero, and the distinction is the usual one: a platform
    that cannot answer has not answered, and a reading of zero would be a
    lie that a report would happily average in.
    """
    try:
        if os.name == "nt":
            return _windows_peak()
        return _unix_peak()
    except Exception:                        # noqa: BLE001 - never worth a crash
        # Measurement failing must never be what breaks a sort. The file is
        # more important than the reading taken while looking at it.
        return None


class Watch(object):
    """One reading, taken around whatever the `with` block does.

    Used as a context manager so that a file which raises is still measured:
    the interesting ones are exactly the ones that go wrong.
    """

    def __init__(self):
        self.seconds = 0.0
        self.growth = None
        self.peak = None
        self._started = None
        self._before = None

    def __enter__(self):
        self._before = peak_bytes()
        self._started = time.monotonic()
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.seconds = time.monotonic() - self._started
        self.peak = peak_bytes()
        if self.peak is not None and self._before is not None:
            self.growth = max(0, self.peak - self._before)
        return False                         # never swallow the exception

    def adopt(self, reading):
        """Take in a measurement made where the work actually happened.

        Since identification moved into a worker process, the memory this
        process grew by says nothing about what reading a file cost: the
        reading happens somewhere else. The worker measures itself the same
        way and sends its numbers back with the facts, and the worst of the
        two is what the file is charged -- "the most memory any one process
        needed while reading this".

        Without this the 171 MB PDF that started the whole cost report
        would now be recorded as costing nothing at all.
        """
        if not reading:
            return
        growth = reading.get("growth")
        peak = reading.get("peak")
        if growth is not None:
            self.growth = growth if self.growth is None \
                else max(self.growth, growth)
        if peak is not None:
            self.peak = peak if self.peak is None else max(self.peak, peak)

    def reading(self):
        """This measurement, in the shape `adopt` takes."""
        return {"seconds": self.seconds, "growth": self.growth,
                "peak": self.peak}

    def notable(self, slow_seconds=None):
        """Whether this reading is worth a row in the ledger.

        Most files are not. A folder of twenty thousand holiday photographs
        should produce no rows at all, because nothing in it was expensive
        and a table of twenty thousand unremarkable readings answers no
        question anybody has.
        """
        if self.seconds >= (SLOW_SECONDS if slow_seconds is None
                            else slow_seconds):
            return True
        return self.growth is not None and self.growth >= GREEDY_BYTES

    def reason(self):
        """Plain words for why this file is listed, for the report."""
        parts = []
        if self.seconds >= SLOW_SECONDS:
            parts.append("took %.1fs to read" % self.seconds)
        if self.growth is not None and self.growth >= GREEDY_BYTES:
            parts.append("needed %s of memory" % human(self.growth))
        if self.peak is not None and self.peak >= SOFT_BUDGET:
            parts.append("took the program past its %s budget"
                         % human(SOFT_BUDGET))
        return ", and ".join(parts) or "was measured"


def human(count):
    """Bytes as something a person reads, or an em dash for absent."""
    if count is None:
        return "—"
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit) if unit != "B" \
                else "%d B" % int(size)
        size /= 1024
    return "%.1f GB" % size


def measure(function, *args, **kwargs):
    """Run something and return `(result, Watch)`. For callers that would
    rather not open a block."""
    watch = Watch()
    with watch:
        result = function(*args, **kwargs)
    return result, watch
