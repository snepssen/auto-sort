"""The identify-catalogue stage, as a process that can be killed.

This is the only part of auto-sort whose input is effectively untrusted. It
reads bytes somebody else wrote, with parsers that have already been sent
into a spin twice: once by a filename whose digits made a regular expression
quadratic, and once by a PDF whose streams inflated a hundred and ninety
fold. Everything else here walks directories and renames files.

So it runs alone, in its own process, where it can be stopped. A thread
cannot: CPython's regular expression engine never releases the interpreter
lock, and a thread spinning inside one starved the main loop down to a
single tick in twenty-three seconds when that was measured. The property
needed was never concurrency. It is preemption, and a process is the only
thing here that can be preempted.

The protocol is one JSON object per line in and one per line out, because
the facts were already JSON on their way to the ledger and a pipe asks for
nothing more. Anything this process prints that is not a reply -- a warning
from a library, a stray print left in a reader -- would corrupt that stream,
so stdout is claimed on the first line of `main` and everything else is sent
to stderr, where the parent can log it and nobody can be confused by it.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bundles                                           # noqa: E402
import identify                                          # noqa: E402
from readers import ocr                                   # noqa: E402


def set_ceiling(megabytes):
    """A fuse, not a budget.

    Set far above anything a reader should need, so that crossing it means
    something has gone wrong rather than that a file was large. auto-sort
    aims at 100 MB and gives a file that genuinely needs more whatever it
    needs; this exists only so that one file cannot take the machine down
    with it, and it kills this worker and nothing else.

    Windows has no `resource` module and no equivalent that is reachable
    from the standard library, so there the timeout is the only guard. That
    is a real gap and is written down rather than pretended away.
    """
    if not megabytes:
        return False
    try:
        import resource
    except ImportError:
        return False
    limit = int(megabytes) * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
        return True
    except (ValueError, OSError):
        # Some platforms refuse an address-space limit outright. Better to
        # run without one than to refuse to identify anything.
        return False


def handle(request):
    """One file in, one record out. Never raises."""
    primary = request.get("primary")
    if not primary:
        return {"ok": False, "error": "no path given"}
    # Sent with every request rather than read from the rules here: this
    # process has no rules file, and the two must not be able to disagree.
    ocr.configure(request.get("ocr", "auto"))
    try:
        item = bundles.Item(primary,
                            members=request.get("members") or None,
                            is_dir=request.get("is_dir"),
                            reason=request.get("reason", ""),
                            sequence=request.get("sequence", 0))
        record = identify.identify(item, tier=request.get("tier",
                                                          identify.TIER_ALL))
        return {"ok": True, "record": record.as_wire()}
    except MemoryError:
        # The fuse blew, or the machine is out. Either way this process is
        # now an unreliable narrator: it reports the one file and stops,
        # and the parent starts a fresh one for whatever comes next.
        return {"ok": False, "error": "needed more memory than the ceiling "
                                      "allows", "fatal": True}
    except RecursionError:
        return {"ok": False, "error": "nested past any sensible depth",
                "fatal": True}
    except Exception as error:               # noqa: BLE001
        # A reader that throws is a bug, and one bad file must not stop the
        # nineteen thousand behind it. The file is reported unread, with
        # what went wrong, and the sort carries on.
        # The same words the in-process path uses, so that the daemon's
        # decision about whether a skip is worth retrying does not change
        # depending on which side of the pipe the reading happened.
        return {"ok": False,
                "error": "identification failed: %s: %s"
                         % (type(error).__name__, error)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ceiling = 0
    for index, argument in enumerate(argv):
        if argument == "--ceiling-mb" and index + 1 < len(argv):
            ceiling = int(argv[index + 1])

    # Claimed before any reader can be imported into this process, never
    # mind run. A single stray `print` upstream would otherwise arrive in
    # the middle of a reply and the parent would see a protocol error
    # instead of a file.
    out = sys.stdout
    sys.stdout = sys.stderr

    set_ceiling(ceiling)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            reply = {"ok": False, "error": "unreadable request"}
        else:
            reply = handle(request)
        out.write(json.dumps(reply) + "\n")
        out.flush()
        if reply.get("fatal"):
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
