# What is not built yet

Everything here is either measured or marked as a guess. The numbers come
from running the thing on real machines during development, and where a
number is missing it is missing because nobody has measured it, not because
it is small.

Done is in [DESIGN.md](DESIGN.md#build-order). This is the other list.

---

## 1. The job manager

The one that matters, because it is the only item here whose absence is
invisible until it happens to somebody.

> **Part of this is now built.** The identify-catalogue stage runs in its
> own process and is killed if it stops answering. Folder-discovery and
> sort-job still run in the main loop. The argument below is kept in full
> because it is what the rest of the work is measured against.

### What broke, and what it cost

auto-sort identified files, served its log page, and pumped its tray icon on
**one thread**. Any of those three could stop the other two, and when it
happened the only symptom a person got was that the menu bar icon
disappeared. It happened twice in development, both times from a file rather
than a bug in the loop:

| What | Measured |
| --- | --- |
| A regex that was quadratic on digits | 8,000 digits took 0.4s, every doubling quadrupled it — four megabytes was about **a day**, on the main thread |
| A 0.91 MB PDF whose streams inflated without a cap | grew the process by **171 MB**, about a hundred and ninety fold |

Both are fixed. Both were found by accident. The next one will be a file
nobody has thought of, and it will present identically: the icon vanishes,
sorting stops, the log page stops answering, and there is no telemetry and
no crash reporter to say so — by design.

### Why threads do not fix it

Tested, not assumed. A catastrophic regex was run on a worker thread while
the main loop tried to tick every 10ms:

```
worker regex took : 22.99s
main loop ticks   : 1 over 23.00s     (it wanted 2,300)
```

CPython's regex engine does not release the GIL, so a "background" worker
starves the foreground completely. `asyncio` is worse, not better: a
coroutine that never awaits never yields, and a spinning regex never awaits.

**The property needed is not concurrency. It is preemption** — and the only
thing that can be preempted here is a process.

Measured again after the change, with a real catastrophic backtrack burning
a core inside a worker and the parent ticking every 10ms:

```
worker spun for   : 5.01s
main loop ticks   : 407 over 5.01s     (an idle loop manages 420)
verdict           : stopped after 5 seconds without an answer
```

97% of idle, against one tick in twenty-three seconds before. The worker was
killed, the file was set aside with a reason, and the next file was read by
a fresh worker.

One thing that is still a thread, deliberately: the parent reads replies on
a thread that does nothing but block on a pipe. A thread blocked on I/O has
released the lock. The rule was never "threads are bad" — it is that a
thread cannot be taken away from work it refuses to stop doing.

### The three stages

They are separated because they **fail differently**, not merely to spread
load. Their names are the ones that describe them:

**folder-discovery** — walks trees, finds candidates. *Not built.*
Fails on: permissions, dead network mounts, symlink loops, a volume that
disappears mid-walk. Cheap, restartable, holds no state worth protecting.
Correct response to a hang: kill and retry later.

**identify-catalogue** — reads bytes and works out what a file is. **Built**
(`jobs.py`, `identify_worker.py`).
Fails on: adversarial or merely strange file contents. This is the only
stage whose input is effectively untrusted, and both production hangs came
from here. It now runs in a process with a 30-second per-file timeout and a
2 GB address-space fuse, one worker serving many files, and a killed worker
costs that file's facts rather than the file: it is reported unread, with a
reason, and the sort carries on. Facts cross the pipe as JSON carrying their
source and confidence, so a rule decides on exactly what it would have
decided on in-process.

Three things it will not do. It will not fail closed — where a worker cannot
be started at all, reading happens in the main process exactly as before,
because a supervisor that stops the tool working has made things worse. It
will not enforce the memory budget; the ceiling is a fuse set far above it.
And it does not help a file that hangs the *main* process, because there is
no longer a path for one to.

Cost, measured: **0.19 ms per file** of pipe overhead, against the ~260 ms a
real PDF takes to read. On twenty thousand files that is four seconds.

Still open here: Windows has no `resource` module, so there the timeout is
the only guard — the memory fuse is Unix-only and that is a real gap.

**sort-job** — moves files and journals them. *Not built.*
Fails on: the filesystem. Must be transactional, and **must never be killed
mid-move**. This stage is why "just add timeouts everywhere" is wrong: the
correct timeout for identify is five seconds and the correct timeout for a
cross-volume move of a 40 GB video is not.

That asymmetry is the argument for the split. One supervisor, three kinds of
worker, three different policies.

### Budget, and why it is not a wall

100 MB is the target for auto-sort's own footprint. Today, measured:

| | |
| --- | --- |
| Program on disk | **880 KB** of Python, ~1,016 KB with the log page and docs |
| Installed dependencies | **none** |
| Daemon at rest | **35 MB** |
| Reading 336 real PDFs | **62 MB** (was 230 MB before the decompression cap) |

**The budget must not be enforced by killing.** A file that genuinely needs
more memory should get it — and because there is no telemetry, a silent kill
would be invisible to everybody including the person it happened to. So:

- **Soft budget (100 MB)** — a tripwire. Crossing it is *recorded*, not
  punished: "this file needed 340 MB to read".
- **Hard ceiling** — a fuse, set far higher, existing only so one file
  cannot take the machine down. `resource.setrlimit(RLIMIT_AS)` on a worker
  process, stdlib, and a breach kills that worker and nothing else.
- The file is then **set aside**, exactly as the mirror queue already sets
  aside a file it cannot copy — a status and a reason, not a silence.

### The reporting is the point — **built**

There is no crash reporter and there never will be, so the ledger has to be
the telemetry. It is already local, already durable, and already records
every move with its facts.

It now also records what reading a file cost. Every identification is timed
and its memory high-water mark taken; anything over a second, or over 16 MB
of growth, keeps a row in a `costs` table, and the log page has a **Slow &
heavy files** view listing them worst first. `auto-sort costs` prints the
same thing. One row per file rather than one per reading, keeping the
*worst* reading rather than the latest — the second pass over a folder reads
from the page cache and looks innocent.

Nothing is refused or skipped because of a reading. A file that genuinely
needs 340 MB gets 340 MB and a line saying so.

This was built first, before the job manager, because it is what will find
the next pathological file. The 171 MB PDF was invisible until someone went
looking with `ps`; had auto-sort written down "this file cost 171 MB", it
would have been a bug report on day one.

Two limits worth stating. The memory figure is a high-water mark, so the
first bad file is measured in full and an identical one read a minute later
looks free — the right bias for finding an outlier and the wrong one for an
average. And a file that hangs *forever* still never finishes, so it never
writes its row. Only the job manager can fix that one, which is the rest of
this section.

### Open questions

- ~~How facts cross the process boundary.~~ Answered: JSON over a pipe,
  one object per line, carrying value, source and confidence.
- ~~Process pool or one worker per file?~~ Answered: one long-lived worker,
  restarted when it has to be killed. Spawn cost on Windows is still
  unmeasured, which is now an argument *for* the long-lived worker rather
  than a question.
- Whether folder-discovery is worth its own process at all, or whether it
  belongs in the supervisor. It is the cheapest stage and the least
  dangerous, and after the identify split it may not be worth it.
- Whether sort-job needs to move out at all. It cannot be killed mid-move by
  definition, so what a supervisor would add there is a question rather than
  an answer.
- A memory fuse on Windows. A Job Object through `ctypes` is the only
  stdlib-reachable route and nobody has tried it.

---

## 2. OCR for scanned paperwork — **built, and the number was wrong**

`tesseract` is now found at runtime the way `ffprobe` and `exiftool` are
meant to be, and a photographed page is read and handed to the same
induction as any other document. Absent, nothing changes.

**The 56% figure in the earlier version of this file was wrong, and it was
mine.** 197 of 352 PDFs have no *readable* text layer, which is true. What
that was taken to mean — that 197 of them are photographs of pages — is not.
Looking at what is actually inside them:

| Of 195 PDFs with no readable text layer | |
| --- | --- |
| Hold only a letterhead-sized image | **185** |
| Hold a page-sized image — a real scan | **10** |

So OCR helps about ten files on that machine, not a hundred and ninety-seven.
The other 185 are documents with real text in them that auto-sort fails to
read, which is a different problem and a much larger one. See item 5.

What was built, and why it looks the way it does:

- The page image is lifted straight out of the PDF. A JPEG inside a PDF is a
  JPEG, copied byte for byte — no rasteriser, no decoder, nothing installed.
  The other filters would each need an image encoder written here to produce
  something another program could open, which is a lot of code for the files
  that do not use DCTDecode.
- The **largest** image, not the first: nearly every scan arrives with the
  sender's logo in front of it. And only if it is page-sized, because OCR on
  a 218×62 logo costs a process launch to learn the sender's name.
- Text from OCR is **LIKELY** where a text layer is STRONG. It is a machine's
  reading of a photograph of the words, and `rn` becomes `m` at any
  resolution a fax ever used.
- It runs inside the identify worker, where it can be killed, with a
  twenty-second limit inside the worker's thirty. Measured on real scans:
  **1.0 to 1.5 seconds a page**, 126 to 403 words each.
- The cost report does not list it. Slow is what OCR *is*; expensive is
  relative to what the file asked for, and a report full of every scan in
  the folder would bury the one file that actually misbehaved.

Still open:

- **Filters other than JPEG.** CCITT and JBIG2 fax images, and Flate raw
  bitmaps, are skipped. Writing a PNM out of a Flate bitmap is not hard and
  would need the colour space handled honestly; the fax codecs are a real
  decoder each.
- **Pages that are not images at all.** A PDF whose text cannot be decoded
  has nothing to OCR without rendering it first, which needs `pdftoppm` or
  equivalent. That is the natural second optional program, and item 5 may
  make it unnecessary.
- **macOS has far better OCR built in** (the Vision framework), reachable
  through `ctypes` the same way the tray is. Worth doing after tesseract,
  not instead of it: one platform only, and the machine this tool is aimed
  at is more often a Windows box.

---

## 3. A Linux tray

macOS and Windows have one. Linux gets `UnavailableTray` and a desktop entry
in the applications menu, which is enough to open the log page but is not an
icon.

The honest blocker: a native tray means **StatusNotifierItem over D-Bus**,
which from the standard library means speaking the wire protocol — SASL
handshake, binary marshalling, exporting an object with properties, plus
`com.canonical.dbusmenu` for the menu itself. That is several hundred lines
of exactly the kind of code that ships broken when it cannot be tested on
the desktop it targets.

KDE hosts SNI, and a Steam Deck in Desktop Mode is a real KDE machine that
has now run this program. So it is testable. It is just not small, and the
desktop entry already solves the actual problem, which was "there is no way
in but a terminal".

---

## 4. Windows

Code-reviewed, never executed. Not once.

What is known to be right by reading: `paths.state_dir()` resolves
`%APPDATA%`, the kind folders map to `Videos` rather than macOS's `Movies`,
`$RECYCLE.BIN` is recognised as a wastebasket.

What cannot be known without a machine: the Startup-folder shortcut (needs
PowerShell), the tray (`Shell_NotifyIcon` through `ctypes`), whether
`Zone.Identifier` provenance survives real browsers, and how badly process
spawn cost hurts item 1.

The decision on record is to **ship and wait for complaints through approved
channels** rather than guess. That remains sensible. It is listed here so it
is listed somewhere.

---

## 5. The documents that are not scans

Found while building item 2, and much bigger than item 2.

**185 of 195** PDFs with no readable text layer, on the machine this was
developed against, are not scans at all. They contain ordinary text, set in
ordinary base-14 fonts with `WinAnsiEncoding` and no embedded font files —
about as readable as a PDF gets. auto-sort extracts the words correctly and
then throws them away.

The thrower is `_readable()` in `readers/pdftext.py`, which asks whether
letters make up 45% of the characters. It exists to reject the letter-soup
that comes out of a subset-encoded CID font with no `ToUnicode` map, and
that is a real thing it has to reject. But an invoice is full of amounts,
dates, customer numbers and reference codes:

```
chars: 62994   letters: 22425   ratio: 0.36   ->  rejected
words with letters: 3035
```

Three thousand words of German, thrown away for being 36% letters.

The fix is to judge on **words rather than characters** — soup does not
produce repeated alphabetic tokens — and the test has to be built against
examples of both, which means finding a real subset-CID file to tune the
rejection side against. Otherwise this trades a false negative for a false
positive and the induction starts learning from noise.

Worth noting for whoever does it: these files extract 3,035 words and still
produce a useless *heading*, because the first five hundred characters of
the extracted text are reference numbers rather than the letterhead. Text
order in a content stream is drawing order, not reading order. Getting the
words back is most of the job; getting the top of the page is the other
half.

---

## Not planned

Unchanged from [DESIGN.md](DESIGN.md#deliberately-not-here): no content
recognition beyond structural reads, nothing cloud, no telemetry, no
deleting, and no becoming a media library manager.

One addition worth stating explicitly, because it came up: **no rewriting
the rules ordering model.** First-match-wins on a readable ordered list is
why a non-programmer can open that file and understand it. It has a real
cost — every bug about a rule that never fires traces back to it — but the
three reports that now catch those (`check-rules` naming unreachable rules,
rules that never win, and categories with no rule) are the right compensation.
A scoring model would be harder to predict and impossible to explain.
