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
producer.

**Tier 2 — external programs, if they happen to exist.** ffprobe for containers
Tier 1 declines, exiftool for exotic RAW. Found at runtime the way siphon finds
things; absent, skipped silently, and the rule that wanted `duration` simply
does not match.

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
5. Autostart install/uninstall per platform.
6. `bootstrap.py`, launchers, and the optional Tier 2 programs.
