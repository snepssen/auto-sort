"""Supervising the stage that can hang, so that the rest cannot.

auto-sort identifies files, serves its log page and pumps its tray icon.
Until now all three shared one thread, which meant any of them could stop
the other two, and the only symptom a person got was the menu bar icon
quietly disappearing. It happened twice in development and both times the
cause was a file, not a bug in the loop.

Identification is the stage that reads what somebody else wrote, so it is
the stage that moves out. Everything here is about one property:

**A worker that has stopped answering must be stoppable.** Threads cannot
give that. A thread spinning inside CPython's regular expression engine
never releases the interpreter lock, and when that was measured the main
loop managed a single tick in twenty-three seconds. `asyncio` is worse, not
better: a coroutine that never awaits never yields. Only a process can be
preempted, so the worker is a process.

Note what is still a thread here, and why it is fine. The parent reads the
worker's replies on a thread that does nothing but block on a pipe -- and a
thread blocked on I/O has released the lock, which is exactly the case
threads are good for. The rule is not "threads are bad". It is that a
thread cannot be taken away from work it refuses to stop doing.

Three things this deliberately does not do:

It does not fail closed. If a worker cannot be started at all -- no
interpreter, a sandbox, a packaging arrangement nobody anticipated -- then
identification happens in this process exactly as it used to. A supervisor
that stops a tool from working because supervision is unavailable has made
things worse.

It does not lose the file. A worker killed for hanging costs one file's
*facts*, not the file. It is reported unread, with a reason, and the sort
carries on.

It does not enforce the memory budget. The ceiling passed to a worker is a
fuse set far above the 100 MB target, there only so that one file cannot
take the machine down. A file that genuinely needs more memory is given it
and written into the cost report, because a silent kill would be invisible
to everybody -- there is no telemetry here to notice it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

try:
    import queue
except ImportError:                          # pragma: no cover - Python 2
    queue = None

import evidence
import identify
import readers

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "identify_worker.py")
TOOL_WORKER = os.path.join(HERE, "tool_worker.py")

# How long one file may take before the worker is assumed lost. Generous on
# purpose: a header read from a sleeping external drive is slow and fine,
# and this number exists to catch the file that is never going to finish,
# not the one that is taking its time.
TIMEOUT_SECONDS = 30.0

# The fuse. Not the budget -- see the module docstring.
CEILING_MB = 2048

# After this many failures to start, stop trying and read in this process.
# A machine where workers cannot be spawned will not start being one.
GIVE_UP_AFTER = 3

_STOPPED = object()


class Unavailable(Exception):
    """A worker could not be started at all."""


class Channel(object):
    """One worker process, spoken to a line of JSON at a time.

    The same machinery serves both kinds of worker, because the difference
    between them is policy rather than plumbing: how long to wait, what to
    do about a silence, and whether a dead one is worth starting again.
    """

    def __init__(self, command, timeout, label, ceiling_mb=0):
        # A callable, not a list, wherever the command names a file that
        # might move: it is resolved at spawn time so that the worker a
        # channel runs is whatever the module points at *now*.
        self.command = command
        self.timeout = float(timeout)
        self.label = label
        self.ceiling_mb = int(ceiling_mb)
        self.spawn_failures = 0
        self.killed = 0              # stopped for not answering
        self.restarts = 0
        self.asked = 0
        self.last_used = time.monotonic()
        self._process = None
        self._replies = None

    # -- the process -------------------------------------------------------

    def _spawn(self):
        command = list(self.command() if callable(self.command)
                       else self.command)
        script = command[2] if len(command) > 2 else ""
        if not sys.executable or not os.path.exists(script):
            raise Unavailable("no worker to run")
        options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                   "stderr": subprocess.PIPE, "cwd": HERE}
        if os.name == "nt":
            # Without this a console window flashes up for every worker,
            # on a program whose entire user interface is a tray icon.
            options["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        else:
            # Its own process group, so that killing a hung worker also
            # kills whatever it had started -- an `ffprobe` left running on
            # a file nobody is waiting for any more is exactly the kind of
            # orphan that makes a fan spin for an hour.
            options["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **options)
        except OSError as error:
            raise Unavailable(str(error))

        replies = queue.Queue()

        def pump(stream, sink):
            # Nothing but blocking on a pipe. See the module docstring.
            try:
                for line in stream:
                    sink.put(line)
            except (ValueError, OSError):
                pass
            finally:
                sink.put(_STOPPED)

        reader = threading.Thread(target=pump, args=(process.stdout, replies))
        reader.daemon = True
        reader.start()

        # stderr is drained too, or a worker that writes a lot of warnings
        # fills its pipe buffer and blocks forever -- a deadlock that would
        # look exactly like the hang this module exists to prevent.
        noise = threading.Thread(target=_drain, args=(process.stderr,))
        noise.daemon = True
        noise.start()

        self._process = process
        self._replies = replies

    def alive(self):
        return self._process is not None and self._process.poll() is None

    def ensure(self):
        if self.alive():
            return True
        if self.spawn_failures >= GIVE_UP_AFTER:
            return False
        try:
            self._spawn()
        except Unavailable:
            self.spawn_failures += 1
            self._process = None
            return False
        return True

    def idle_seconds(self):
        return time.monotonic() - self.last_used

    def stop(self, kill=False):
        process = self._process
        self._process = None
        self._replies = None
        if process is None:
            return
        try:
            if kill:
                _kill_group(process)
            else:
                if process.stdin:
                    process.stdin.close()
                process.terminate()
            process.wait(timeout=5)
        except Exception:                    # noqa: BLE001
            try:
                process.kill()
            except Exception:                # noqa: BLE001
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:                # noqa: BLE001
                pass

    # -- one exchange ------------------------------------------------------

    def ask(self, request):
        """`(reply, error)`. Exactly one of the two is ever meaningful.

        The error is a sentence for the person who will read it in the log,
        not for a developer.
        """
        self.last_used = time.monotonic()
        if not self.ensure():
            return None, "%s could not be started" % self.label
        if not self._write(request):
            # It died between the check and the write. Start again once; a
            # second failure is the caller's to fall back from.
            self.stop(kill=True)
            self.restarts += 1
            if not self.ensure() or not self._write(request):
                return None, "%s could not be reached" % self.label

        self.asked += 1
        try:
            line = self._replies.get(timeout=self.timeout)
        except queue.Empty:
            # This is the case the whole module is for.
            self.stop(kill=True)
            self.killed += 1
            return None, ("stopped after %s without an answer; the file was "
                          "left alone" % _duration(self.timeout))
        finally:
            self.last_used = time.monotonic()
        if line is _STOPPED:
            self.stop()
            self.restarts += 1
            return None, "%s stopped unexpectedly on this file" % self.label
        try:
            reply = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.stop(kill=True)
            self.restarts += 1
            return None, "%s answered with something unreadable" % self.label
        if reply.get("fatal"):
            # It took itself out of service after answering.
            self.stop()
            self.restarts += 1
        if not reply.get("ok"):
            return None, str(reply.get("error") or "could not be read")
        return reply, ""

    def _write(self, request):
        try:
            self._process.stdin.write(
                (json.dumps(request) + "\n").encode("utf-8"))
            self._process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def report(self):
        return {"asked": self.asked, "killed": self.killed,
                "restarts": self.restarts,
                "spawn_failures": self.spawn_failures,
                "running": self.alive()}


class Helpers(object):
    """One process per optional program, started only when one is needed.

    The tools are other people's programs reading other people's files, and
    they are slow in ways that have nothing to do with anything going wrong:
    OCR is seconds a page by its nature. Run inside the identify worker --
    which is where they were -- a single scanned page stops that worker
    reading anything else for as long as it takes, and a supervisor watching
    it cannot tell "wedged on a file" from "waiting for tesseract".

    So each tool gets a process of its own, and the answer comes back to the
    supervisor rather than to the worker that found the gap. Three
    consequences, all of them the point:

    **Each tool can be given its own patience.** A hung `ffprobe` is a
    problem after twenty-five seconds; OCR is not, at the same age.

    **Killing one costs only its own work.** The identify worker keeps its
    place, and the file it was reading is unaffected.

    **Nothing starts until something needs it.** A tool worker costs about
    20 MB, mostly the interpreter, and three of them at once would put this
    program past the footprint it aims at. So they are started on the first
    file that wants one and stopped again after a couple of minutes idle --
    which on most folders means never started at all.
    """

    # How long each tool may take over one file before it is assumed lost.
    # OCR's is long for the first page a Mac ever reads: Vision prepares
    # its models then, which was measured at two minutes. After that it is
    # under a second, and a page that takes five minutes is lost anyway.
    PATIENCE = {"ffprobe": 25.0, "exiftool": 25.0, "tesseract": 330.0}
    DEFAULT_PATIENCE = 30.0

    # Idle time before a tool worker is stopped again. Long enough to serve
    # a run of scanned pages without paying to start again for each.
    IDLE_SECONDS = 120.0

    def __init__(self, mode="auto", ocr="auto", journal=None):
        self.mode = mode
        self.ocr = ocr
        self.journal = journal
        self.channels = {}
        self.in_process = 0
        self.recalled = 0            # files answered from what was kept
        self.last_reading = {}       # what the tools said this file cost

    @property
    def where(self):
        """Where the tools are to be run: `workers`, `inline` or `nowhere`.

        One answer rather than a pair of booleans, because the three cases
        are genuinely different and the interesting mistake is treating
        "not in workers" as "inline" -- which is how `tools = off` ended up
        running every tool inside the identify worker instead of nowhere.
        """
        if self.mode == "off":
            return "nowhere"
        if self.mode == "inline" or queue is None:
            return "inline"
        return "workers"

    @property
    def separate(self):
        """Whether the tools run somewhere other than the identify worker."""
        return self.where == "workers"

    def enrich(self, path, record):
        """Ask each tool that wants this file, in its own process.

        Returns the number of tools that added something. Never raises: a
        tool that cannot be reached leaves the facts absent, which is what
        happens on a machine where it is not installed at all.
        """
        if self.mode == "off":
            return 0
        self.last_reading = {}
        added = 0
        for name, enricher in readers.enrichers():
            try:
                if not enricher.wanted(record):
                    continue
            except Exception:                # noqa: BLE001
                continue
            kept = self._remembered(name, path, record)
            if kept is not None:
                # Answered, whether or not the answer was anything. A tool
                # that found nothing found nothing last time too, and the
                # second pass should not pay to be told so again.
                added += 1 if kept else 0
                continue
            before = set(record.names())
            if self._ask(name, path, record):
                added += 1
            self._remember(name, path, record, before)
        return added

    # -- asking twice about the same file ----------------------------------

    def _remembered(self, name, path, record):
        """Use what this tool said last time, if the file has not changed.

        Returns None when there is nothing kept -- which is not the same as
        a kept answer of "nothing", and telling the two apart is the whole
        saving on a folder of files the tools have no interest in.

        A file passes through the funnel once, and reading a scanned page
        costs a second and a half and a process. But it is read more than
        once even so: the first run against a new folder is forced to be a
        preview, and the run that follows it reads everything again. Twice
        for every scan, before anything has gone wrong.

        The answer cannot change while the file does not, so it is kept
        against the file's size and modification time and used again.
        """
        if self.journal is None:
            return None
        mark = _fingerprint(path) + _method(name)
        if not mark:
            return None
        try:
            delta = self.journal.recall_reading(path, name, mark)
        except Exception:                    # noqa: BLE001
            return None
        if delta is None:
            return None                      # never asked, or asked about
                                             # a file that has changed since
        self.recalled += 1
        record.reader_ran(name, "what it said last time, unchanged since")
        return _apply(record, delta)

    def _remember(self, name, path, record, before):
        if self.journal is None:
            return
        mark = _fingerprint(path) + _method(name)
        if not mark:
            return
        delta = _difference(record, before)
        if not (delta["facts"] or delta["dropped"]):
            # A tool that found nothing is worth remembering too: the
            # second pass should not pay to be told nothing again.
            delta = {"facts": [], "dropped": [], "notes": [], "readers": []}
        try:
            self.journal.remember_reading(path, name, mark, delta,
                                          self.last_reading.get("seconds", 0))
        except Exception:                    # noqa: BLE001
            pass

    def _ask(self, name, path, record):
        if not self.separate:
            return self._here(name, path, record)
        channel = self.channels.get(name)
        if channel is None:
            channel = Channel(
                lambda tool=name: [sys.executable, "-u", TOOL_WORKER,
                                   "--tool", tool, "--ocr", self.ocr],
                self.PATIENCE.get(name, self.DEFAULT_PATIENCE),
                name)
            self.channels[name] = channel
        reply, error = channel.ask({"path": path,
                                    "record": record.as_wire()})
        if error:
            record.note("%s: %s" % (name, error))
            # A worker that cannot be started at all is not a reason to do
            # without the facts on a machine that has the program.
            if "could not be started" in error:
                return self._here(name, path, record)
            return False
        _keep_worst(self.last_reading, reply.get("cost"))
        try:
            fresh = evidence.from_wire(reply["record"])
        except (KeyError, TypeError, ValueError):
            return False
        return _merge(record, fresh)

    def _here(self, name, path, record):
        """The fallback: run the tool in this process, as it used to be."""
        self.in_process += 1
        enricher = dict(readers.enrichers()).get(name)
        if enricher is None:
            return False
        try:
            return bool(enricher.read(path, record))
        except Exception as error:           # noqa: BLE001
            record.note("%s declined: %s" % (name, error))
            return False

    def reap(self, idle_seconds=None):
        """Stop the tool workers nobody has needed for a while."""
        limit = self.IDLE_SECONDS if idle_seconds is None else idle_seconds
        stopped = []
        for name, channel in list(self.channels.items()):
            if channel.alive() and channel.idle_seconds() >= limit:
                channel.stop()
                stopped.append(name)
        return stopped

    def report(self):
        return dict((name, channel.report())
                    for name, channel in self.channels.items())

    def close(self):
        for channel in self.channels.values():
            channel.stop()
        self.channels.clear()


def _method(name):
    """How a tool is asked, as part of what its answer is kept against.

    An answer holds only while the file *and the question* are unchanged.
    Asking tesseract to turn a page the right way up first changed what it
    says about an upside-down scan; kept against the file alone, the old
    reading -- `OTOZ JUN!` -- would have been replayed for ever. A reader
    module states its `METHOD`, and changing it means asking again.
    """
    for known, module in readers.enrichers():
        if known == name:
            return ":%s" % getattr(module, "METHOD", 1)
    return ""


def _fingerprint(path):
    """Size and modification time, which is what everything else here uses.

    Not a hash: hashing every file to avoid reading some of them would cost
    more than the reading. A file edited within the same second and back to
    the same size would be missed, and that is the same bet the settle
    detector already makes.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    return "%d:%d" % (stat.st_size, stat.st_mtime_ns)


def _difference(record, before):
    """What a tool pass changed, in the shape that can be replayed later."""
    facts = []
    for name in record.names():
        if name in before:
            continue
        fact = record.fact(name)
        facts.append([name, evidence._encode(fact.value), fact.source,
                      fact.confidence])
    dropped = [name for name in before if not record.has(name)]
    return {"facts": facts, "dropped": dropped,
            "notes": list(record.notes[-4:]),
            "readers": [list(pair) for pair in record.readers[-2:]]}


def _apply(record, delta):
    """Replay a kept reading onto a fresh record."""
    changed = False
    for entry in delta.get("facts", []):
        try:
            name, value, source, confidence = entry
        except (TypeError, ValueError):
            continue
        if record.has(name):
            continue
        record.set(name, evidence._decode(value), source, confidence)
        changed = True
    for name in delta.get("dropped", []):
        if record.has(name):
            record.drop(name)
            changed = True
    for sentence in delta.get("notes", []):
        if sentence not in record.notes:
            record.note(sentence)
    return changed


def _keep_worst(into, reading):
    """The worst of several readings, so one file has one honest number."""
    if not reading:
        return into
    for key in ("growth", "peak"):
        value = reading.get(key)
        if value is None:
            continue
        current = into.get(key)
        into[key] = value if current is None else max(current, value)
    return into


def _merge(record, fresh):
    """Take facts a tool worker found and the original record lacks.

    Only the new ones, and this is the same rule the tool itself follows in
    process: gaps, not arguments. The record that went out is the authority
    on everything it already knew -- it was built by the reader that had the
    file's own header in front of it.
    """
    added = 0
    for name in fresh.names():
        if record.has(name):
            continue
        fact = fresh.fact(name)
        record.set(name, fact.value, fact.source, fact.confidence)
        added += 1
    # A fact the worker *dropped* has to cross the boundary too. The worker
    # started from exactly this record, so anything it no longer holds it
    # let go of on purpose -- `needs_ocr` on a page that has now been read.
    # Without this the record comes back saying both that it was read by
    # OCR and that it is still waiting to be.
    for name in list(record.names()):
        if not fresh.has(name):
            record.drop(name)
            added += 1
    for sentence in fresh.notes:
        if sentence not in record.notes:
            record.note(sentence)
    for reader_name, detail in fresh.readers:
        if (reader_name, detail) not in record.readers:
            record.reader_ran(reader_name, detail)
    return added > 0


class Reader(object):
    """Identification, done somewhere it can be interrupted.

    Used as a context manager, or closed explicitly. One worker serves many
    files: spawning a process per file would be the obvious design and the
    wrong one, because process spawn on Windows is expensive enough to
    dominate the reading it is protecting.
    """

    def __init__(self, timeout=TIMEOUT_SECONDS, ceiling_mb=CEILING_MB,
                 enabled=True, tools="auto", ocr="auto", journal=None):
        self.ceiling_mb = int(ceiling_mb)
        self.enabled = bool(enabled) and queue is not None
        self.in_process = 0          # files read here instead, as a fallback
        self.helpers = Helpers(tools if self.enabled else "inline", ocr,
                               journal)
        # What the workers said the last file cost them. The supervisor's
        # own memory says nothing about it: the reading happens elsewhere.
        self.last_reading = {}
        self.channel = Channel(
            lambda: [sys.executable, "-u", WORKER,
                     "--ceiling-mb", str(self.ceiling_mb)],
            timeout, "the reader", ceiling_mb)

    @property
    def timeout(self):
        return self.channel.timeout

    @timeout.setter
    def timeout(self, seconds):
        self.channel.timeout = float(seconds)

    # -- the work ----------------------------------------------------------

    def read(self, item, tier=identify.TIER_ALL, ocr="auto"):
        """Facts about one item, as `(record, error)`.

        Exactly one of the two is ever meaningful: a record, or a sentence
        saying why there is not one.
        """
        self.helpers.ocr = ocr
        self.last_reading = {}
        # With the tools in processes of their own, the identify worker is
        # asked for everything *except* them -- which is exactly what the
        # header tier already means -- and the supervisor does that pass
        # itself. The answer comes back here rather than to the worker that
        # found the gap.
        where = self.helpers.where if tier >= identify.TIER_PROGRAMS \
            else "nowhere"
        # Only `inline` leaves the tools to the identify worker. Both of the
        # others ask it for everything *except* them -- which is exactly
        # what the header tier already means -- and then either run them
        # here, in processes of their own, or not at all.
        inner = tier if where == "inline" else identify.TIER_HEADER

        if not self.enabled or not self.channel.ensure():
            record, error = self._here(item, inner, ocr)
        else:
            reply, error = self.channel.ask(
                {"primary": item.primary, "members": list(item.members),
                 "is_dir": bool(item.is_dir), "reason": item.reason,
                 "sequence": item.sequence, "tier": inner, "ocr": ocr})
            if error and "could not be" in error:
                record, error = self._here(item, inner, ocr)
            elif error:
                return None, error
            else:
                _keep_worst(self.last_reading, reply.get("cost"))
                try:
                    record = evidence.from_wire(reply["record"])
                except (KeyError, TypeError, ValueError) as problem:
                    return None, ("the reader's answer did not make sense: %s"
                                  % problem)
        if record is None:
            return None, error
        if where == "workers":
            self.helpers.enrich(item.primary, record)
            _keep_worst(self.last_reading, self.helpers.last_reading)
            identify.derive(record)
        return record, ""

    def _here(self, item, tier, ocr="auto"):
        """The fallback: read it in this process, as before."""
        self.in_process += 1
        try:
            return identify.identify(item, tier=tier), ""
        except (OSError, ValueError) as error:
            return None, "identification failed: %s" % error

    def reap(self):
        """Let go of tool workers nobody has needed for a while."""
        return self.helpers.reap()

    @property
    def killed(self):
        return self.channel.killed + sum(
            channel.killed for channel in self.helpers.channels.values())

    @property
    def restarts(self):
        return self.channel.restarts + sum(
            channel.restarts for channel in self.helpers.channels.values())

    @property
    def spawn_failures(self):
        return self.channel.spawn_failures

    @property
    def _process(self):
        """The reader's own process, for tests and for the daemon's log."""
        return self.channel._process

    def report(self):
        """What supervision actually did, for the daemon's log."""
        return {"killed": self.killed, "restarts": self.restarts,
                "read_in_process": self.in_process,
                "recalled": self.helpers.recalled,
                "spawn_failures": self.spawn_failures,
                "supervised": self.enabled
                and self.channel.spawn_failures < GIVE_UP_AFTER,
                "tools": self.helpers.report()}

    def close(self):
        self.helpers.close()
        self.channel.stop()

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()


def _duration(seconds):
    if seconds >= 1:
        return "%.0f seconds" % seconds
    return "%.2f seconds" % seconds


def _drain(stream):
    try:
        for _ in stream:
            pass
    except (ValueError, OSError):
        pass


def _kill_group(process):
    """Kill the worker and anything it started.

    `ffprobe` and `exiftool` are run by readers, and a worker killed while
    waiting on one would otherwise leave it running on a file nobody wants
    the answer about any more.
    """
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(process.pid), 9)
            return
        except (OSError, AttributeError):
            pass
    process.kill()
