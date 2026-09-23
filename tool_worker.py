"""One optional program, in a process of its own.

The identify worker reads bytes. This one waits for somebody else's program
to finish looking at a file — `ffprobe` at a container, `exiftool` at a
photograph's metadata, `tesseract` at a picture of a page — and those are
slow in ways that have nothing to do with anything going wrong. OCR is
seconds a page by its nature.

Run inside the identify worker, as they were at first, one scanned page
stops that worker reading anything else for as long as it takes, and a
supervisor watching from outside cannot tell "wedged on a file" from
"waiting for tesseract". Each tool therefore gets its own process, with its
own patience, and hands its answer back to the supervisor rather than to the
worker that found the gap.

A record arrives, the one tool this process is for is run against it, and
the record goes back. What comes back is merged by the supervisor under the
same rule the tools follow anyway: gaps, never arguments. The record that
went out is the authority on everything it already knew, because it was
built by the reader that had the file's own header in front of it.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import costs                                             # noqa: E402
import evidence                                          # noqa: E402
import readers                                           # noqa: E402
from readers import ocr                                  # noqa: E402


def handle(name, request):
    """One file, one tool. Never raises."""
    path = request.get("path")
    if not path:
        return {"ok": False, "error": "no path given"}
    enricher = dict(readers.enrichers()).get(name)
    if enricher is None:
        return {"ok": False, "error": "no tool called %r" % name}
    try:
        record = evidence.from_wire(request.get("record") or {})
    except (TypeError, ValueError, KeyError) as error:
        return {"ok": False, "error": "unreadable record: %s" % error}
    try:
        watch = costs.Watch()
        with watch:
            enricher.read(path, record)
        return {"ok": True, "record": record.as_wire(),
                "cost": watch.reading()}
    except MemoryError:
        return {"ok": False, "error": "needed more memory than there is",
                "fatal": True}
    except Exception as error:               # noqa: BLE001
        # One file's facts, not the run. The same rule as everywhere else.
        return {"ok": False,
                "error": "%s failed: %s: %s"
                         % (name, type(error).__name__, error)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    name = ""
    for index, argument in enumerate(argv):
        if argument == "--tool" and index + 1 < len(argv):
            name = argv[index + 1]
        if argument == "--ocr" and index + 1 < len(argv):
            ocr.configure(argv[index + 1])

    # Claimed before any reader can run. A stray print from anything
    # upstream would otherwise arrive in the middle of a reply, and the
    # supervisor would see a protocol error instead of a file.
    out = sys.stdout
    sys.stdout = sys.stderr

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            reply = {"ok": False, "error": "unreadable request"}
        else:
            reply = handle(name, request)
        out.write(json.dumps(reply) + "\n")
        out.flush()
        if reply.get("fatal"):
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
