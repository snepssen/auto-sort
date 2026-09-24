# auto-sort

A folder arrives at a state no-one chose. `New Folder/New Folder/New Folder`,
eleven years of Downloads, a USB stick that was a backup once. auto-sort sits
in the tray, watches the folders it has been told to watch, works out what each
file actually is, and files it where the rules say — one file at a time, as
they arrive, for as long as the machine is on.

It is not a one-off tidy-up run. It is the thing that means the mess never
accumulates again, and it has to be dull enough to leave running for years.

## The machine it has to run on

The machines that need this most are the worst ones. A laptop from 2011 with a
full disk. A Windows box nobody has updated. A Linux install whose desktop
environment has no tray protocol worth the name. If auto-sort needs a model
download, a GPU, or a package manager, it does not run where it is needed.

So the constraints are hard, and they come before every other decision here:

| Constraint | Why |
| --- | --- |
| **Standard library only.** | Same call as siphon. No pip step, no wheel that fails to build on an old compiler, no numpy. |
| **Zero *required* external programs.** | This is the departure from siphon, which cannot work without ffmpeg. auto-sort must sort correctly with nothing installed but Python. ffprobe and exiftool are enrichment: present, the rules get more facts; absent, the rules get fewer and still work. |
| **Python 3.8 floor.** | Lower than siphon's 3.10 on purpose — 3.8 is what Ubuntu 20.04 and a lot of forgotten machines have. No `match`, no `:=` in anything load-bearing, no `X | Y` annotations, `from __future__ import annotations` everywhere. |
| **Polling by default.** | inotify and FSEvents are faster and lie more. Network shares and FAT32 sticks do not deliver events reliably, and the failure is silent — the worst kind for a background process. A stat-loop over a Downloads folder every five seconds costs nothing measurable. Native watching is an optional accelerator, never the only path. |
| **No content models.** | Deferred, not refused. The classifier seam is defined below so a capable machine can opt in later without the core changing shape. |
| **Bounded memory, low priority.** | `os.nice`, one worker, streamed reads, a batch ceiling. It must be invisible on a machine with 2 GB of RAM. |

The honest consequence: auto-sort will categorise by **type, structure, metadata
and provenance** — not by looking at pictures. That gets the great majority of a
real Downloads folder right, and it gets it right *explainably*, which matters
more for a thing that moves your files unattended.

## What a file is

Extensions are a hint. `.jpg` files that are PNGs, `.mp4` files with no video
track, and `photo.jpg.exe` are all ordinary contents of the folders this tool
exists for. Identification runs as a ladder and stops as soon as the rules in
play have the facts they need.

**Tier 0 — signatures and stat.** A table of magic numbers read from the first
few hundred bytes, plus size, dates and the extension. No dependency: the
signature table is ours, not libmagic's. Around 150 entries covers effectively
everything that turns up. Output is a `kind` (`image`, `video`, `audio`,
`document`, `subtitle`, `archive`, `model3d`, `font`, `code`, `app`,
`disk-image`, `data`, `unknown`) and a `format`. A disagreement between
signature and extension is itself a fact rules can match on.

**Tier 1 — headers, parsed ourselves.** Reading the first 64 KB and the trailer
gets you EXIF from JPEG APP1, PNG chunks, ID3v2 and Vorbis comments, MP4/MOV
atoms, Matroska EBML, RIFF, the PDF trailer and `/Info` dictionary. This is
`struct` and seeks — a few hundred lines per format, no dependency, and fast
enough to run on every file. It is where the leverage is: camera make and
model, capture date, artist and album, duration, track layout, page count, PDF
producer. The same tier now also reads the *words* on a PDF's first page —
inflating content streams, following subset-font `ToUnicode` maps, capped at
four megabytes of decompressed output per stream so one adversarial file
cannot ask the process for a gigabyte — and tells a scanner from a camera by
the tags a lamp on a rail has no reason to write. Structural reads, still: no
model, nothing that looks at the pixels or listens to the audio, which is
what keeps this tier and the deferred one below apart.

**Tier 2 — external programs, if they happen to exist.** ffprobe for containers
Tier 1 declines, exiftool for exotic RAW, tesseract for a page that was scanned
rather than typed. Found at runtime the way siphon finds things; absent, skipped
silently, and the rule that wanted `duration` simply does not match. Asked only
about the files that came back with a gap they could fill, so a folder that
parsed cleanly starts no processes at all.

**Tier 3 — content classification.** Not built. The seam is a `Classifier`
protocol — given a path and the Tier 0–2 record, return labels with
confidences — resolved from a plugin directory at start-up. Nothing in the core
imports it, nothing requires it, and a machine that has one gets extra facts
under a `labels` namespace that rules can match. This is how image/audio
recognition arrives later without a rewrite.

### Provenance is stronger evidence than content

Where a file came from usually says more than what is inside it. On macOS,
`kMDItemWhereFroms` holds the download URL. On Windows, Zone.Identifier in the
NTFS alternate data stream holds the referrer. Both are free to read, and
`from_host = bandcamp.com` is a better basis for a decision than any amount of
audio analysis. Read them at Tier 0, and preserve them when moving.

## A funnel has to empty

Downloads is not a location, it is a transit area, and almost everything
unwanted on a computer arrived through it. The design consequence is sharper
than it sounds: **an unmatched file is a failure, not a safe outcome.**

That contradicts the rule stated earlier — that leaving a file alone beats
filing it wrongly — and the contradiction is resolved by where it goes rather
than by whether it moves. A file nothing could say much about still leaves the
funnel, into a dated holding folder under the system folder for its kind. It
is obviously provisional, it is findable, it is in the ledger, and `undo`
takes it back. What it is not is another item in a folder nobody will ever
open again.

So generated rule sets end with catch-alls covering every kind present, plus a
final rule matching anything at all. A proposal that files the recognisable
half looks like progress and delivers none.

Destinations are the folders the operating system already provides, which on
most machines are empty while Downloads holds everything. Inventing a parallel
tree beside them would be a second mess with a tidier name. They are resolved
per platform rather than assumed, because a relocated or localised Pictures
folder is common and writing to the wrong one creates exactly the duplicate
this avoids.

**The exception is volume.** A folder that is not on the home volume is sorted
in place instead. Filing an external drive into the home folders turns every
move from a rename into a copy, a verification and a delete, and moves data
the person deliberately keeps elsewhere onto their internal disk.

## What was filed too early has to be reachable

A corpus reveals itself over time, so the files that arrive first are always
filed worst — not through error, but because there was nothing to learn from
yet. A sorter that cannot revisit them has a permanent floor on how well it
can ever do, and the only workaround is asking somebody to move files back
into the funnel so they can come out again, which is absurd on its face.

The ledger makes it unnecessary. It records where each file was put, by which
rule, and what was known about it, which is enough to ask the question again
without the file moving anywhere first.

Everything then turns on scope, because the careless version of this feature
is a disaster: reconsidering every placement on a timer, with rules that
disagree with each other, would reshuffle a disk endlessly and invisibly. So:

**A file only ever moves up.** A rule declares `holding = yes` when its
destination is a waiting room rather than an answer. Files it placed may be
promoted to a rule that is not a holding rule, and nothing is ever put back
into one. The answer comes from the same rules, in the same order, that a new
arrival would meet, so asking twice gives the same place: run it twice and
the second run finds nothing.

**Provisionality is recorded, not re-derived.** The flag goes in the ledger
when the move happens. Working it out later from the rule's name would tie a
file's history to a string that changes every time a rules file is
regenerated.

**A file that moved is out of scope entirely.** Not where the ledger says it
is means somebody moved it, which is an answer. Corrections learn from those;
regrouping does not touch them.

And a promotion is not a special kind of move. It goes through the same
planner, the same collision and volume checks, the same forced preview, the
same ledger and the same undo — which is the only reason it is safe to let a
background process do it at all.

**Filed files are reconsidered when what judged them changes.** A specific
decision was made on the rules and the reader of the day it arrived, and both
improve: four contracts sat under their own letterhead because the reader of
the day missed their titles. The background sorter reads filed files again
once for each new rules file and each new reader -- keyed on the rules' hash
and a hash of the reader's own code, never on a clock -- records what they say
now, and moves a file only when a real category claims it elsewhere: never
into a holding folder, never a file somebody moved, never a part of a bundle.
`auto-sort refile` shows the same thing on demand, and `regroup = report`
turns both into a line in the log. A program called auto-sort that left the
old mess alone until asked would be keeping half its promise.
A file whose rule has been deleted is decided afresh by
every rule, because the rules file promises that deleting a line takes its
folder with it. A placement ends when the program itself moves the file on,
and undoing that move makes it stand again; without that, every regrouped
file was counted twice by every report.

## Disagreement is the best evidence there is

Everything else in this design reads files. A correction reads the person.

The ledger already records every placement, so noticing one that no longer
holds is the difference between a database and a `stat` call. When a file is
not where it was put, it was moved, and where it was moved to is a statement
about what its owner wanted that no amount of header parsing could produce.

Three constraints keep it honest:

**It proposes; it never adjusts.** A correction becomes a candidate rule with
its count, shown to somebody who accepts or discards it. Silently changing
future placements because of an inference drawn from a folder is how a
background process becomes untrustworthy, and one tidy-up afternoon would
teach the wrong lesson permanently.

**Precision is measured against everything placed**, not against the
corrections alone. A fact common to the files somebody moved is worthless if
it is equally common among the files they left alone, and only the wider
population shows that.

**Which rules get overridden needs no inference and is often the better
half.** A rule corrected eleven times is wrong, and reporting that beats
guessing at a replacement.

## Conventions are learnt, not tabulated

A table of per-site filename patterns is the obvious way to read a download
folder and the wrong one. It cannot be finished, it goes stale, and extending
it means visiting each new site to collect samples. Two mechanisms replace it,
and neither contains the name of a website.

The **source** is not inferred at all. The operating system recorded the
download URL at the time, exactly, and reducing a hostname to a service is a
property of the domain name system rather than of any site: strip the delivery
decoration, take the registrable domain, keep its first label.

The **convention** is counted rather than described, on the observation that a
field repeating across many files is a category while a field unique to each
file is an identifier. What comes out is a regular expression with a named
group, written into the rules file as an ordinary `extract`, so a learnt
convention is visible, editable and indistinguishable from a typed one. A tool
that silently learned a filing scheme nobody could read would be a tool nobody
should run.

`extract` capture names are checked against the facts that decide a file's
identity. A pattern capturing `(?P<name>…)` reads as innocent and renames
every file it matches to the captured text, losing the extension with it —
which is exactly what it did, on a real folder, before the check existed.

## Structure is derived, not configured

The rules file says where things go, and writing one by hand means deciding in
advance what shape a folder should be. That is backwards for the folders this
tool exists for: a disk with four hundred thousand files on it already has a
shape, and the job is to find it rather than impose one.

`propose` surveys a folder, counts every fact that recurs in it, and judges
each possible grouping on three questions:

- **Coverage** — how many items even have this fact. A camera model on eleven
  files out of forty thousand is a detail, not a structure.
- **Shape** — how many folders it would make and how full they would be,
  measured at the *median* rather than the mean, because one prolific artist
  and nine hundred one-offs has a flattering mean.
- **Residue** — what is left over. A proposal that files an eighth of a folder
  and leaves the rest looks like progress and is worse than doing nothing.

What survives becomes a rules file with the counts that justified each rule
written above it, which a person reads before anything runs. The generated
file then goes through the same preview, ledger and undo as a hand-written
one. A tool that reorganised a disk according to a structure nobody had seen
would be a tool nobody could check, so the derivation stops at a proposal.

This is also where the tiers pay off. The survey runs at whatever depth is
affordable — stat alone on a first pass over a huge folder, headers when it
matters — and the facts simply get thinner rather than absent.

## The unit of work is not always a file

The single most common way a sorter ruins a folder is moving one file out of a
set. `.obj` without its `.mtl` and textures. A video without its `.srt`. RAW
without the JPEG the photographer paired it with. A DAW session without
`Audio Files/`. iPhone `.aae` edit sidecars, Live Photo HEIC+MOV pairs, BluRay
`BDMV` trees, `.app` bundles, `__MACOSX` litter.

So the scanner emits **bundles**, not paths: a primary plus its sidecars, or a
directory treated as one opaque item. Classification and rules run on the
bundle; the move moves all of it or none of it. Bundle detection is
stem-matching plus a table of known sidecar extensions and package directory
markers, and it is the first thing to get tests.

## Deciding where it goes

Rules are declarative data, ordered, first match wins — see `RULES.md` for the
format. Three things about the engine matter here:

**Confidence is part of the decision.** Every fact carries how it was
established, and a rule can require a floor. A rule keyed on `format` from a
magic number is certain; one keyed on a filename pattern is not.

**Below the floor means do not move.** Unmatched or low-confidence items are
left exactly where they are and listed in the log, or moved to a single
`_Unsorted/` if the user prefers gathering. A file in the wrong folder is worse
than an unsorted one: it is lost *and* the user has stopped trusting the tool.

**Nothing is ever deleted.** Not duplicates, not junk, not `__MACOSX`. The
strongest action available is a move, and de-duplication means moving copies to
a `Duplicates/` folder for a human to empty.

## Moving it safely

The ledger is the feature. Every move is a row in SQLite before it happens and
is confirmed after: source, destination, size, content hash, rule that fired,
run id, timestamp. `auto-sort undo <run>` walks it backwards. The log window
reads from it. Dry-run is the default until the user says otherwise, and the
first run on a new folder is always a dry run whose report they are shown.

The hazards are mostly filesystem, and mostly silent:

| Hazard | Handling |
| --- | --- |
| Cross-volume move | `rename` fails across devices, so: copy, verify by hash, then unlink. Never the other order. |
| Half-written file | Settle detection — size and mtime unchanged for N seconds, and not matching `*.part`, `*.crdownload`, `*.download`, `*.tmp`, `~$*`. On Windows, additionally try an exclusive open. |
| FAT32 / exFAT stick | 4 GB file ceiling, 2-second timestamp granularity, no symlinks, case-insensitive collisions, short path limits. Detect the filesystem and sanitise names against the *destination's* rules, not the source's. |
| Windows paths | `MAX_PATH` 260 unless prefixed `\\?\`; reserved names `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`9`, `LPT1`–`9`; `<>:"/\|?*` illegal; trailing dots and spaces silently stripped. |
| Name collision | Suffix ` (2)`, never overwrite. If the hashes match it is a duplicate, and that is a different decision, not an overwrite. |
| Sorting its own output | Destinations are excluded from watching, and a move whose target is inside a watched root is checked for a cycle before it runs. |
| Symlinks, hardlinks, APFS clones | Never follow, never break. A clone copied naively stops being a clone and the disk fills. |
| iCloud / OneDrive placeholders | A dataless file that gets hashed downloads gigabytes. Check the dataless flag in `st_flags` on macOS and the offline attribute on Windows, and skip. |
| Volume disappears mid-run | Requeue, do not fail, do not mark done. USB sticks are pulled. |
| Metadata loss | A plain copy drops extended attributes, Finder tags and where-froms. Preserve them, or the provenance the tool relies on is destroyed by the tool. |
| `.autosortignore` | Honoured, gitignore syntax, per directory. |

## Running in the background

One process, `autosortd`. The loopback port it serves the log window on doubles
as the single-instance lock — if the bind fails, another copy is already
running and this one opens its window instead of starting a second sorter.

The work queue lives in SQLite, so a reboot mid-sort resumes rather than
forgets. The loop is: scan watched roots → group into bundles → wait for settle
→ identify → match rules → move → record. Idle cost is one stat pass per
interval; when nothing changed, nothing else happens.

Pausing is a first-class state, not a kill. Someone about to download forty
files into a specific folder needs a Pause that survives, not a Quit they have
to remember to undo.

## The tray, and the window

The tray icon is the whole interface, and the window behind it is a page on
`127.0.0.1` — the same arrangement as `siphon/app.py`, for the same reasons: no
build step, no bundler, one `index.html`, and it renders on any browser old
enough to be on these machines. Token in the URL, loopback-only bind, origin
checked, because a local server that moves files must not be drivable by any
open tab.

Menu: **Open log** · **Pause sorting** · **Sort now** · **Watched folders…** ·
**Quit**.

The log lists what moved, when, why — which rule fired — and a **Reveal**
button per row. Reveal takes a ledger row *id*, never a path from the request,
and the server resolves the path itself; otherwise the endpoint is an
arbitrary-command hole.

| Platform | Reveal |
| --- | --- |
| macOS | `open -R <path>` |
| Windows | `explorer /select,<path>` |
| Linux | `dbus-send` to `org.freedesktop.FileManager1.ShowItems` — covers Nautilus, Dolphin, Thunar, Nemo — falling back to `xdg-open` on the parent directory |

Tray support itself is where cross-platform gets ugly, and the rule is that
**absence of a tray must never stop sorting**:

- **Windows** — `Shell_NotifyIcon` through `ctypes`. Stdlib, no pywin32. Run
  under `pythonw.exe` so no console window appears.
- **macOS** — `NSStatusItem` via PyObjC when it is importable, inside an app
  bundle with `LSUIElement` set so there is no Dock icon. Homebrew Pythons do
  not ship PyObjC, so this is a capability check, not an assumption.
- **Linux** — StatusNotifier over D-Bus where the desktop provides it. Many do
  not, honestly, so the documented fallback is a desktop-menu entry that opens
  the log page while the daemon runs headless.

When no tray backend is available the daemon says so once, in the log, and
carries on. A `.desktop` file, Start-menu shortcut, or `.command` becomes the
way in.

## Starting at log-in

Off until asked, and symmetrical — whatever installs it uninstalls it.

| Platform | Mechanism |
| --- | --- |
| macOS | `~/Library/LaunchAgents/com.snepssen.auto-sort.plist`, `RunAtLoad` + `KeepAlive`, loaded with `launchctl bootstrap gui/$UID` |
| Windows | Shortcut in the per-user Startup folder, pointing at `pythonw.exe`. Preferred over the `HKCU\...\Run` key because a user can see and delete a shortcut. |
| Linux | `~/.config/autostart/auto-sort.desktop` for any XDG desktop; a `systemd --user` unit where systemd is present and the user prefers it |

Nothing here needs root, and nothing here writes outside the user's own
profile. A tool that sorts files does not get to install a system service.

## Self-install

`bootstrap.py` and `platform_support.py`, in the shape siphon already uses: a
`Program` table naming each optional program, what stops working without it,
and the package name per manager; `--check` exits 0 when everything is present;
the offer defaults to yes; `sudo` is never run on anyone's behalf, only
printed. `start.sh`, `start.bat` and `Start auto-sort.command` check first and
only ask when something is missing.

The difference from siphon is that **`required` is empty**. Every entry in
auto-sort's table is `required=False`, and the launcher never blocks. The
conversation is "ffprobe would let it read durations — install it?", not "this
will not run".

This is now the third repo wanting this module — siphon has it, auto-sort needs
it, and media-preflight finds its tools but only prints install lines. The
tempting move is tools-core; the right move is probably not. `ORGANISING.md`
says every tool here starts from its own start script and is self-contained,
and a shared bootstrap would mean auto-sort cannot install anything until
tools-core is on the machine — which is exactly the dependency the pattern
exists to avoid. So: copy it, with a provenance comment naming siphon as the
original, and keep the `Program` table per repo since the programs differ. The
duplication is three files and it buys each tool the ability to stand alone.

## Deliberately not here

- **Content recognition.** Seam defined, implementation deferred, and no
  dependency admitted in the meantime.
- **Anything cloud.** No metadata lookups by default, no LLM, no telemetry. A
  tool with unattended access to someone's whole disk does not talk to the
  network.
- **Deleting.** Ever.
- **Being a media library manager.** Renaming films from TMDB is Radarr's job
  and it is better at it.

## Build order

1. Signature table, Tier 0/1 identification, bundle grouping, `sort --dry-run`
   over a folder. No daemon, no UI. Fixtures for the nasty cases.
2. Rules engine and config parsing (`RULES.md`), ledger, real moves, `undo`.
3. Daemon: watch loop, settle detection, queue, pause, single-instance lock.
4. Log page on loopback, reveal, and the tray backends behind a capability
   check.
5. Autostart install/uninstall per platform, plus a menu entry where there is
   no tray to click.
6. `bootstrap.py`, launchers, and the optional Tier 2 programs.
7. Structure that derives and corrects itself: `propose`, `regroup`,
   `corrections`, and a `check-rules`/`adopt` pair that can name a rule that
   never wins or never fires, and add an earned one without rewriting the
   file a person wrote.
8. PDF text as Tier 1 evidence, scan detection, and the induction that
   replaced every hand-written keyword table in the project with counting.
9. Duplicate detection on-disk, the optional off-by-default mirror, and
   ledger compaction — everything a machine accumulates over years now has
   an answer for not accumulating forever.
10. Cost reporting. Every identification is timed and its memory high-water
    mark taken, and the few files that were expensive get a row of their own
    in the ledger and a page of their own in the log. There is no crash
    reporter and there never will be, so a file that makes the machine go
    quiet has to leave its own note or nobody ever learns which file it was.
11. Identification in its own process. The stage that reads what somebody
    else wrote is the stage that hangs, and a thread cannot be taken away
    from work it refuses to stop doing. A worker that stops answering is
    killed, the file is set aside with a reason, and the tray icon, the log
    page and the nineteen thousand files behind it carry on.
12. Installers read as installers. A version number followed by a dotted tag
    is still a version number, an installer's own extension is evidence that
    it is one, and a destination folder is spelled the way the disk already
    spells it -- so eleven years of one program's installers gather in one
    folder rather than eleven dated ones or two differing by a capital
    letter.
13. Reading a page that was photographed. An optional OCR program, found at
    runtime and never a dependency, with the page image lifted straight out
    of the PDF rather than rendered. What it returns is text, which the
    induction already knows what to do with, recorded a band weaker than a
    text layer because it is a machine's reading of a picture of the words.
14. Tier 2, connected. ffprobe and exiftool had been offered by the
    installer and described in this document for months without a line of
    code calling either one. They now fill gaps and never argue: a fact the
    built-in reader established is left exactly as it was, and a file that
    parsed cleanly never launches anything.
15. A process per optional program. The tools wait on somebody else's
    program rather than reading bytes, so they fail differently and are
    given their own patience, their own process and their own idle
    timeout. What they find goes back to the supervisor, which merges it
    under the same rule the tools follow in process: gaps, not arguments.
16. Judging a page by its words. The readability test counted letters as
    a share of characters, which an invoice full of amounts and reference
    numbers fails while being perfectly readable. Counting words instead
    took one real folder from 151 documents read to 304.
17. Refusing what is not a word. A subset font with no character map
    yields glyph numbers that read as words and mean nothing; they are
    dropped a string at a time, and again as candidate categories, by
    asking two questions that a page of Greek or Japanese answers
    differently from a page of glyph numbers.
18. The owner's own name, from the account record. No table and no
    vocabulary: the system was told it when the account was made. Held
    to a much lower ceiling than other words rather than banned, because
    a surname is often also a word -- and the login and machine names with
    it, since a moniker is a word too. What settles those is that a word
    only ever found inside a path or an address was never used as one.
19. A title by how big it is drawn. Official paperwork puts registration
    numbers first and its title four hundred characters down; what it
    always does is draw the title larger than the body. Page one is what
    the page tree says, read with the fonts page one declares, and a
    heading is the title and then the top of the page, so the sender's
    rules keep matching.
20. Every writer's way of spacing words. Qt places a glyph at a time,
    Quartz gives each glyph a text block of its own, some writers step
    glyph by glyph; with the fonts' own width tables, a string that starts
    where the last ended continues its word. Uncompressed streams are
    streams too. Each change measured against the reader before it on
    every PDF on the machine; none lost a word.
21. Encrypted is not locked. The standard security handler (RC4, AES-128)
    written out in the standard library, the empty password checked
    against the file's own check value and nothing else tried; a Mac's
    PDFKit agrees file for file about which open. The owner may keep their
    own passwords in the Keychain, never in the rules or the ledger.
22. OCR that reaches what was already filed, turns a page the right way up,
    reads pages kept as raw pixels by rewrapping them as PNG, and, on a
    Mac, uses the text recognition the system already has, so reading a
    scanned page needs nothing installed.
23. Not only PDFs. Word (by style sizes), OpenDocument, RTF, Markdown and
    text; Word 97 `.doc` through its compound file and piece table; newer
    Pages through its Snappy-compressed archive; email by subject,
    invitations by summary, saved pages by title.
24. The reports tell the truth about the rules. A rule somebody wrote is
    not outranked by a word the counting found; a rule is asked with all a
    document's facts; a title word is counted only where it is written as
    one; a learnt category is named by the phrase its documents share.
25. Nothing left for a person to type. Filed files are judged again when
    the rules or the reader change; what waits in a holding folder moves the
    moment a rule claims it; a category the waiting documents show is added
    to the rules file by itself, with a note, and one somebody deletes stays
    deleted. All of it a batch per cycle, through the supervised reader, so
    a decade of Downloads never stops the tray answering. The starter file
    names this machine's own folders and passes its own check.
