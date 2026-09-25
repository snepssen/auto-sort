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

**folder-discovery** — walks trees, finds candidates. *Not built, and the
case for it is weaker than it looked.*
Fails on: permissions, dead network mounts, a volume that disappears
mid-walk. Cheap, restartable, holds no state worth protecting. Correct
response to a hang: kill and retry later.

Symlink loops were listed here and are not a hazard: `os.walk` does not
follow symlinked directories, and a loop three levels deep yields one item
at depth 40. Checked, not assumed.

What is left is a volume vanishing mid-walk, which truncates the walk
silently and can drop queue entries that the next scan rebuilds anyway.
That is a small, self-healing fault, and a process boundary is a large
answer to it.

**identify-catalogue** — reads bytes and works out what a file is. **Built**
(`jobs.py`, `identify_worker.py`, `tool_worker.py`).
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

The optional programs were then split out again, one process each. They do
not read bytes; they wait for somebody else's program, and OCR is seconds a
page by its nature — so inside the identify worker a single scanned page
stopped it reading anything else, and a supervisor could not tell "wedged on
a file" from "waiting for tesseract". Each now has its own patience
(ffprobe 25s, OCR 60s), its own process, and its own idle timeout. A worker
costs about **20 MB**, mostly interpreter, so they start on the first file
that wants one and are let go after two minutes idle. Measured with a scan
and a video in one folder: **4 processes and 107 MB at peak, 51 MB once the
tool workers were reaped**. Over the target while it lasts, recorded rather
than prevented, exactly as the budget section says.

`tools = auto | inline | off` in `[settings]` chooses between a process per
tool, the tools inside the identify worker, and no external program at all.

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

100 MB is the target for auto-sort's footprint **at rest**, and that is the
number that matters, because at rest is what auto-sort mostly is. While it
is actually working it expands, and it is supposed to: a folder with a
scanned page and a video in it will hold four processes and 107 MB for a few
seconds, and then let go of two of them.

The reason that is acceptable rather than merely tolerated is that the work
is **paid for once**. A file arrives, it is read, it is sorted, and it is
never read again — so the expensive pass over somebody's twenty-year folder
is a thing that happens on a Tuesday afternoon and then does not happen
again. A tool's answer is kept against the file's size and modification
time, so even a second run over the same folder costs nothing:

```
pass 1: 4.14s over 11 files, tool workers running=2, recalled=0
pass 2: 0.37s over 11 files, tool workers running=0, recalled=3
```

That matters more than it looks, because the *normal* first use of a new
folder reads it twice: the first run is forced to be a preview and the run
that applies it reads everything again. Before this, every scanned page in
the folder was read twice before anything had even gone wrong.

Today, measured:

| | |
| --- | --- |
| Program on disk | **908 KB** of Python, ~1,052 KB with the log page and docs |
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

- **Filters other than JPEG and Flate.** Flate bitmaps are now rewrapped as
  a PNG (8-bit grey or colour, 1-bit grey): two certificates were exactly
  that. CCITT and JBIG2 fax images are still skipped; the fax codecs are a
  real decoder each.
- **AES-256 encryption (revision 6)** -- not planned. Revisions 2 to 4 are
  read when a file opens without a password or with one kept in the
  Keychain. A revision 6 file is marked `encryption_unread` and, like one
  whose password is not known, waits in `Documents/PDF/Encrypted/<day>` as
  a holding folder. Locked PDFs are rare enough that setting them apart is
  the right size of answer; SHA-2 key hardening and AES encryption in the
  loop are not.
- **Pages that are not images at all.** A PDF whose text cannot be decoded
  has nothing to OCR without rendering it first, which needs `pdftoppm` or
  equivalent. That is the natural second optional program, and item 5 may
  make it unnecessary.
- **macOS's own OCR** -- built. The Vision framework is used through
  `osascript`, preferred over tesseract on macOS 10.15 and later, with
  tesseract kept for everywhere else.

---

## 3. A Linux tray — **built**

The blocker was stated honestly and turned out to be the whole job: a
native tray on Linux means a **StatusNotifierItem over D-Bus**, and every
Python binding for D-Bus is a package somebody has to install. So the wire
protocol is spoken with the standard library, in `dbuswire.py`, the way the
macOS tray speaks to the Objective-C runtime through `ctypes`: a Unix
socket, the SASL EXTERNAL handshake, and the binary message format --
signatures, alignment, variants, arrays, dicts and structs. `tray.py`
exports `org.kde.StatusNotifierItem` and `com.canonical.dbusmenu` on top of
it, with the same menu as macOS and Windows.

Tested on the desktop it targets, as this section said it had to be: a
Steam Deck in Desktop Mode, Plasma 6.7.3, with a person looking. The icon,
its menu, left click to the log page, Open log, Pause and Resume from the
tray and from the log page, Sort now, Restart and Quit all did what they
say. `busctl`, a separate D-Bus implementation, reads every property and the
whole menu back. The wire tests hold the writer to bytes laid out by hand
from the specification and the reader to messages recorded from that
session.

| | Measured |
| --- | --- |
| Code | 671 lines of `dbuswire.py`, ~350 in `tray.py` |
| Installed dependencies | **none** |
| What the tray adds to a process | **1.6 MB** |
| Daemon at rest, with the tray | **34.6 MB** |

Three things learnt building it, each now in the code:

- **kded owns the watcher, not plasmashell.** On Plasma 6 the
  `StatusNotifierWatcher` lives in kded, which forgets every item when it
  restarts and tells none of them. The item listens for the watcher's name
  changing hands and registers again. Tried under a live session:
  `plasma-kded6.service` restarted, and a running daemon's item was back in
  the new watcher's list within a second, ahead of every other application.
- **A host may call back before it replies.** Waiting for a reply without
  answering calls would stall until the timeout, so a call keeps answering
  while it waits.
- **The login race.** With no watcher when the daemon starts, it says why
  in the log and still appears if one turns up.

Still open:

- **Desktops with no host at all.** GNOME shows nothing without the
  AppIndicator extension. The daemon says so and carries on headless, and
  the applications-menu entry -- which now starts the daemon if nothing is
  running -- is the way in there.
- **Other hosts.** Only Plasma has been looked at. XFCE, Cinnamon and
  waybar implement the same protocol and have not. What is known to differ
  between them is covered from the specifications: the item answers under
  the freedesktop names as well as KDE's (swaybar runs a watcher under
  each), carries its icon as pixels as well as a theme name, and answers
  the newer dbusmenu calls (`EventGroup`, `AboutToShowGroup`) waybar's
  menu library uses. What a host asked the item for is counted and kept,
  so `auto-sort diagnose` can tell "no icon" from "an icon whose host
  wanted it to draw its own menu". Waiting on somebody with those desktops.

## 4. Windows

Code-reviewed against the Win32 documentation, never executed. Not once.

The review found the tray could not have appeared on any 64-bit Windows:
every call went through `ctypes.windll` with no declared signature, so
ctypes cut each handle to 32 bits -- `GetModuleHandleW`'s address halved,
`DefWindowProcW` raising on the window's first message. And the
notification-icon structure stopped at `szTip`, a size no Windows version
accepts. Now every call goes through `winapi.py`, which declares all of them
from the documentation; tests on any machine hold every call in the program
to that table and the structure to its documented 976 bytes. The menu posts
`WM_NULL` after itself as Microsoft documents, and the icon is re-added when
Explorer restarts.

What is known to be right by reading: `paths.state_dir()` resolves
`%APPDATA%`, the kind folders map to `Videos` rather than macOS's `Movies`,
`$RECYCLE.BIN` is recognised as a wastebasket.

What cannot be known without a machine: whether the icon then actually
appears and its menu works, the Startup-folder shortcut (needs PowerShell),
whether `Zone.Identifier` provenance survives real browsers, and how badly
process spawn cost hurts item 1. Deleted files go to a `Trash (auto-sort)`
folder rather than the Recycle Bin until the Recycle Bin can be checked.

The decision on record stands: **ship, say plainly that it has not been
run, and ask for `auto-sort diagnose` from whoever tries it.** GitHub
Actions now runs the test suite and the install line on real Windows on
every push (`.github/workflows/tests.yml`); it cannot show a tray icon.

---

## 5. The documents that are not scans — **fixed**

Found while building item 2, and it was much bigger than item 2.

**185 of 195** PDFs with no readable text layer, on the machine this was
developed against, were not scans at all. They contained ordinary text, set
in ordinary base-14 fonts with `WinAnsiEncoding` and no embedded font files
— about as readable as a PDF gets. auto-sort extracted the words correctly
and then threw them away.

The thrower was `_readable()` in `readers/pdftext.py`, which asked whether
letters made up 45% of the *characters*. It exists to reject the letter-soup
a subset-encoded CID font produces with no `ToUnicode` map, and that is a
real thing it has to reject. But an invoice is amounts, dates, customer
numbers and reference codes:

```
chars: 62994   letters: 22425   ratio: 0.36   ->  rejected
words with letters: 3035
```

Three thousand words of German, discarded for being 36% letters.

It now counts **words** rather than characters, and the threshold is eight —
what the smallest real document says. Measured across the same 346 files:

| | before | after |
| --- | --- | --- |
| Read as text | 151 | **304** |
| Held as an unreadable page | 195 | **22** |

Of the 22 still held, 8 hold a real page-sized picture and are read by OCR;
the other 14 are the fax-codec gap in item 2.

Two things worth knowing about the fix. The threshold has a wide gap to sit
in — of 334 files without a page-sized picture, 20 have no words at all, 13
have fewer than eight, and 296 have more than twenty — so it is not balanced
on a knife edge. And what it **cannot** tell apart is a one-byte subset font
whose encoding is a substitution: the same words with the letters swapped.
That is a cipher of real prose and has the shape of prose, so no test
without a dictionary would catch it, and this one does not pretend to. If it
happens the cost is a category named something nobody can read — visible in
the log, listed by `check-rules`, one line to delete. The cost of the old
test was 177 readable documents.

The heading turned out to be fine, which contradicts what an earlier version
of this file said. 175 of 177 recovered documents produce a word-shaped
heading from the existing first-500-characters window; the earlier claim
that they did not was a fault in how I measured it, not in the code.

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
