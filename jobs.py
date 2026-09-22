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

try:
    import queue
except ImportError:                          # pragma: no cover - Python 2
    queue = None

import evidence
import identify

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "identify_worker.py")

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


class Reader(object):
    """Identification, done somewhere it can be interrupted.

    Used as a context manager, or closed explicitly. One worker serves many
    files: spawning a process per file would be the obvious design and the
    wrong one, because process spawn on Windows is expensive enough to
    dominate the reading it is protecting.
    """

    def __init__(self, timeout=TIMEOUT_SECONDS, ceiling_mb=CEILING_MB,
                 enabled=True):
        self.timeout = float(timeout)
        self.ceiling_mb = int(ceiling_mb)
        self.enabled = bool(enabled) and queue is not None
        self.spawn_failures = 0
        self.killed = 0              # workers stopped for not answering
        self.restarts = 0
        self.in_process = 0          # files read here instead, as a fallback
        self._process = None
        self._replies = None
        self._pump = None

    # -- the worker itself -------------------------------------------------

    def _spawn(self):
        if not os.path.exists(WORKER) or not sys.executable:
            raise Unavailable("no worker to run")
        command = [sys.executable, "-u", WORKER,
                   "--ceiling-mb", str(self.ceiling_mb)]
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

        thread = threading.Thread(target=pump,
                                  args=(process.stdout, replies))
        thread.daemon = True
        thread.start()

        # stderr is drained too, or a worker that writes a lot of warnings
        # fills its pipe buffer and blocks forever -- a deadlock that would
        # look exactly like the hang this module exists to prevent.
        noise = threading.Thread(target=_drain, args=(process.stderr,))
        noise.daemon = True
        noise.start()

        self._process = process
        self._replies = replies
        self._pump = thread

    def _ensure(self):
        if self._process is not None and self._process.poll() is None:
            return True
        if not self.enabled or self.spawn_failures >= GIVE_UP_AFTER:
            return False
        try:
            self._spawn()
        except Unavailable:
            self.spawn_failures += 1
            self._process = None
            return False
        return True

    def _stop(self, kill=False):
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

    # -- the work ----------------------------------------------------------

    def read(self, item, tier=identify.TIER_ALL):
        """Facts about one item, as `(record, error)`.

        Exactly one of the two is ever meaningful: a record, or a sentence
        saying why there is not one. The sentence is written for the person
        who will read it in the log, not for a developer.
        """
        if not self._ensure():
            return self._here(item, tier)

        request = {"primary": item.primary, "members": list(item.members),
                   "is_dir": bool(item.is_dir), "reason": item.reason,
                   "sequence": item.sequence, "tier": tier}
        try:
            self._process.stdin.write(
                (json.dumps(request) + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (OSError, ValueError):
            # The worker died between the check and the write. Start again
            # once; a second failure falls through to reading it here.
            self._stop(kill=True)
            self.restarts += 1
            if not self._ensure():
                return self._here(item, tier)
            try:
                self._process.stdin.write(
                    (json.dumps(request) + "\n").encode("utf-8"))
                self._process.stdin.flush()
            except (OSError, ValueError):
                return self._here(item, tier)

        try:
            line = self._replies.get(timeout=self.timeout)
        except queue.Empty:
            # This is the case the whole module is for.
            self._stop(kill=True)
            self.killed += 1
            return None, ("stopped after %s without an answer; the file "
                          "was left alone" % _duration(self.timeout))
        if line is _STOPPED:
            self._stop()
            self.restarts += 1
            return None, "the reader stopped unexpectedly on this file"

        try:
            reply = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._stop(kill=True)
            self.restarts += 1
            return None, "the reader answered with something unreadable"

        if reply.get("fatal"):
            # The worker took itself out of service after answering.
            self._stop()
            self.restarts += 1
        if not reply.get("ok"):
            return None, str(reply.get("error") or "could not be read")
        try:
            return evidence.from_wire(reply["record"]), ""
        except (KeyError, TypeError, ValueError) as error:
            return None, "the reader's answer did not make sense: %s" % error

    def _here(self, item, tier):
        """The fallback: read it in this process, as before."""
        self.in_process += 1
        try:
            return identify.identify(item, tier=tier), ""
        except (OSError, ValueError) as error:
            return None, "identification failed: %s" % error

    def report(self):
        """What supervision actually did, for the daemon's log."""
        return {"killed": self.killed, "restarts": self.restarts,
                "read_in_process": self.in_process,
                "spawn_failures": self.spawn_failures,
                "supervised": self.enabled and self.spawn_failures
                < GIVE_UP_AFTER}

    def close(self):
        self._stop()

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
