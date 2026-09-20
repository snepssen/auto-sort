# auto-sort

A folder arrives at a state no-one chose. `New Folder/New Folder/New Folder`,
eleven years of Downloads, a USB stick that was a backup once. auto-sort will
sit in the tray, watch the folders it has been told to watch, work out what
each file actually is, and file it where the rules say — one file at a time,
as they arrive, for as long as the machine is on.

Identification, the safety-critical moving core, and the persistent watcher
are complete. A sort is a dry run by default, every member is written to a
SQLite ledger before it is touched, and the first attempted apply for a folder
and rule file is forcibly turned into a preview. Real moves verify content,
never overwrite, keep bundles together, and can be restored with `undo`. The
watcher keeps its settle queue and paused state across restarts. Its local log
shows every ledgered operation and can reveal the recorded file safely. See
[DESIGN.md](DESIGN.md) for the whole shape and [RULES.md](RULES.md) for
configuration.

## What works now

```sh
python3 autosort.py explain ~/Downloads/some-file.bin
python3 autosort.py explain ~/Downloads/some-file.bin --rules ./rules.ini
python3 autosort.py scan ~/Downloads --list 40
python3 autosort.py check-rules ./rules.ini
python3 autosort.py sort ~/Downloads --rules ./rules.ini
python3 autosort.py sort ~/Downloads --rules ./rules.ini --apply
python3 autosort.py undo last
python3 autosort.py watch --rules ./rules.ini --apply
python3 autosort.py status
python3 autosort.py open-log
python3 autosort.py pause
python3 autosort.py resume
python3 autosort.py sort-now
python3 autosort.py autostart status
python3 autosort.py autostart install --rules ./rules.ini
python3 autosort.py autostart remove
```

`explain` prints every fact about one file, which reader established it, and
how sure that reader was. When a rules file is present, it also prints each rule
tried and the destination the first match would choose. `check-rules` catches a
bad section, option, expression or template without touching any files. `scan`
groups a folder into items and counts what is in it. `sort` journals a complete
plan and prints it without moving anything unless `--apply` is given or
`dry_run = no` is set. Even then, a new folder/rules pairing must complete one
preview first. `undo` verifies that each destination still has the recorded
hash before restoring it. `watch` runs the low-priority polling service in the
foreground, which makes it suitable for a terminal now and a platform service
later. It persists observations until bundles have been unchanged for
`settle_seconds`, keeps queued work when a watched volume disappears, and never
watches its own output. `pause` and `resume` survive both daemon and machine
restarts; `sort-now` wakes the loop without waiting for its next interval.
`open-log` opens the live daemon's loopback operations console, which shows
recent moves and lets you pause, resume, sort now, or reveal a recorded file in
its file manager. Its dense ledger defaults to the latest 50 files (up to 500
in steps of 50) and shows the destination alongside the rule and the actual
classification facts used by the sorter. Dates use the browser's locale and
time zone. Column and manual destination preferences stay in that browser. Per
row, it can reveal, copy, move, restore a recorded move, or move the current
file to the system Trash/Recycle Bin; copy and move never overwrite, every
operation is ledgered, and classification facts follow later file operations.
`autostart` is deliberately separate from `watch`: `status` only reports the
per-user login launcher, `install` first validates the rules file then adds it,
and `remove` deactivates and deletes only that launcher. Nothing starts at
login unless `install` is explicitly requested.

```
  IMG_1354.HEIC
  /Users/someone/Downloads/IMG_1354.HEIC
  3.0 MB   modified 2026-09-17 08:29:37

  Read by
    signature        image/heif (heic)
    image:heif       11 fields
    filename         4 facts
    provenance       5 facts

  What is known
    kind                 image             strong   extension  agreed by signature
    width                5712              certain  exif
    camera               iPhone 17         strong   exif
    gps                  56.392792,-3.43…  strong   exif
    capture              camera-still      likely   name:camera
    from_app             AirDrop           strong   quarantine
    origin               airdrop           strong   quarantine
```

## How it decides

Four readers, cheapest first, each allowed to overrule the one before it when
it has better evidence.

**The extension.** Six hundred of them, mapped to thirteen kinds and two
hundred canonical formats. Right most of the time, free, and never trusted
further than `likely`. Nineteen extensions are marked ambiguous — `.ts` is
TypeScript or an MPEG transport stream, `.key` is a Keynote deck or a private
key — and for those the extension says nothing at all until the bytes are read.

**The bytes.** A table of 145 magic numbers, and refiners for the containers
that several kinds share: `ftyp` is an MP4, an iPhone photo, a Canon raw or an
audiobook; `PK\x03\x04` is a zip and also every Office document, EPUB, Android
package and USDZ model ever made. Text has no magic number, so a separate
sniffer recognises thirty shapes — WebVTT, SubRip, G-code, OBJ, COLLADA, JSON —
and reports plain text at `weak`, because everything from a shell script to a
subtitle file decodes as UTF-8 and the extension knows better than the bytes do.

**The header.** EXIF, ID3, Vorbis comments, MP4 atoms, Matroska EBML, RIFF
chunks, PDF info dictionaries, OOXML properties. Camera and lens and capture
date; artist and album and track; duration and geometry and track layout;
producer and page count and author. One TIFF directory parser serves EXIF in
JPEGs, TIFFs, HEICs and every camera raw, because they are all the same
structure.

**The name, and where it came from.** Thirteen filename detectors —
screenshots in eight languages, twenty-two camera and phone naming schemes,
messaging exports, scene releases, music naming, producer samples, paperwork
keywords, scanner output, installers, and the litter browsers and file managers
leave behind. Then the operating system's own record of the file's origin:
`kMDItemWhereFroms` and the quarantine agent on macOS, the `Zone.Identifier`
stream on Windows, `user.xdg.origin.url` on Linux. That last one is the
sharpest evidence in the record and almost nothing uses it — it is the
difference between a PDF that came from Mail and the same PDF downloaded from
a bank.

Nothing is decided by looking at an image, listening to audio, or asking a
model. See the [constraints](DESIGN.md#the-machine-it-has-to-run-on): this has
to run on the machines that need it most, which are the worst ones.

## Evidence, not values

Every fact carries its source and a confidence, and the merge rules are the
point of the design:

- A stronger source overrules a weaker one, and **the disagreement is kept**.
  `extension_lies` is a fact rules can match on.
- **Agreement raises confidence.** An artist from an ID3 tag is strong; the
  same artist also parsed out of the filename is stronger. Halving the
  remaining distance each time means three agreeing guesses never outrank one
  parsed header field.
- **A fact that could not be established is absent** — not empty, not zero.
  Rules treat absence as "no match", so a missing reader narrows what can fire
  instead of making it fire wrongly.

## Items, not files

The scanner emits bundles. `Film.mkv` with its `.srt`, `.nfo` and poster is one
item; raw and JPEG are one photograph; a twelve-frame render is one sequence; a
three-part RAR is one archive; a `.app` or `.pages` directory is one document
and is never descended into. Whatever the rules eventually decide, the whole
item moves — which is the difference between a sorted folder and a folder full
of 3D models that open grey.

## Downloads is a funnel

Almost everything bad on a computer arrives through the Downloads folder, and
it is not a place anybody keeps things — it is where files land on their way
somewhere else. So auto-sort treats it as one: **everything leaves, and
nothing is kept back.** A sorter that files the half it recognises and leaves
the rest behind has not emptied anything; the folder refills and the tool
looks like it worked.

That means every kind present gets a destination, and the generated rules end
with catch-alls. Anything nothing could say more about still leaves, into a
dated holding folder — because an undated holding folder just becomes the
Downloads folder again.

**It files into the folders the system already made.** Pictures, Movies, Music
and Documents sit empty on most machines while everything piles up in
Downloads. A second set of media folders beside them would be one more mess
with a tidier name. Those folders are found properly per platform, not
assumed: `~/.config/user-dirs.dirs` on Linux, so a relocated or localised
`Bilder` is honoured; the Shell Folders registry on Windows, so Documents
moved to another drive still works; and fixed English paths on macOS, where
`~/Movies` is correct even when Finder displays something else.

Run against a real Downloads folder of 701 items, 688 were planned and the
only 13 left were `.DS_Store` files:

```
  Where this folder empties to
    ~/Pictures/Unfiled           image 314
    ~/Documents/Archives         archive 156
    ~/Documents/Unfiled          document 127
    ~/Movies/Unfiled             video 60
    ~/Music/Unfiled              audio 19
    (anything a rule above did not claim; nothing stays behind)
```

**An external drive is not a funnel**, and is sorted in place under a `Sorted`
folder on the drive itself. Filing a USB stick into `~/Pictures` would copy
every file onto the internal disk — a copy, a hash and a delete each, instead
of a rename — which is the opposite of what tidying a drive means. The test is
simply whether the folder is on the home volume.

## Structure that builds itself

A folder does not need to be told what shape it should be. `propose` surveys
one, counts the groupings that actually exist in it, and writes the rules that
folder turns out to need:

```sh
auto-sort propose ~/Downloads --out my-rules.ini
auto-sort sort ~/Downloads --rules my-rules.ini
```

```
  696 items, 757 files, 10.1 GB

  Downloaded from
    twitter               50
    furaffinity           43

  Structure this folder suggests
    site-only             100 items ->    3 folders, median 43 each
    duration               60 items ->    4 folders, median 7.5 each
    format                695 items ->   22 folders, median 3 each

  Considered, not proposed
    site-uploader      the middle folder would hold 1 file
    camera             only 0.3% of items have camera
    music              nothing in this folder has artist
```

**What it refuses matters more than what it proposes.** A folder per artist is
the obvious structure for downloaded art — and on that folder it was declined,
because twenty-one artists across forty-three files means the middle folder
would hold one picture. That is not organisation, it is the same pile with
more steps. The test is the *median* group, never the mean, because one
prolific artist and twenty one-offs has a flattering mean. Give it a folder
where artists repeat and the same facet is accepted.

Destinations default to a subfolder of the folder being surveyed, so every
move stays on one volume — a rename rather than a copy, a hash and a delete.
That matters most on the external drive somebody is tidying.

### Where a file came from, and what its name means

Neither of these needs a table of websites in it, which matters because that
table can never be finished — there is always another site, and filling it in
means somebody going and downloading junk from each one first.

**The source is already recorded.** Every mainstream browser writes the
download URL into the file's metadata: `kMDItemWhereFroms` on macOS,
`Zone.Identifier` on Windows, `user.xdg.origin.url` on Linux. auto-sort reads
it, strips the delivery-network decoration off the hostname and keeps the
registrable domain's first label. On a real Downloads folder that yielded
`furaffinity`, `e621`, `bsky`, `twimg`, `fbcdn`, `pinterest`, `wikimedia` and
`suno` — **none of which is named anywhere in the code**. A site nobody has
heard of groups correctly on its first file.

**The naming convention is learnt by counting.** A convention is, by
definition, a thing that repeats, which makes it discoverable:

> In a filename written by software, a field that repeats across many files is
> a category, and a field that is different in every file is an identifier.

Given forty-three files shaped `<digits>.<word>_<rest>`, the digits are all
different and the words repeat, so `propose` writes:

```ini
; A naming convention these files share, learnt from them rather than
; configured: 43 files, 21 distinct values in one field, 70% of them
; sharing a folder.
;   values: multyashka-sweet, terrathewizard, kinniro, keihound, ...
[rule: furaffinity names]
when    = source = furaffinity
extract = stem re ^\d{10}\.(?P<group>[a-z0-9-]+)_.*
into    = ~/Downloads/Sorted/furaffinity/{group}
```

Nothing in that was configured, and nothing in the code knows what
FurAffinity is. The depth to look at is self-selecting: these names shatter
into three unusable clusters when four fields are examined and resolve into
one clean convention at two, so every plausible depth is tried and the one
explaining the most files wins.

Two guards keep it honest. A convention must contain a **machine identifier**
— a long run of digits, or a checksum — because software that names files puts
one in to guarantee uniqueness and people do not; without that test the
strongest "convention" in a real folder was somebody's own song titles, and
the tool offered to file their music by its first word. And a field must
**concentrate**: at least 40% of files landing somewhere with company. Median
is the wrong measure here — forty-three pictures by twenty-one artists has a
median of one and is a real structure, while a thousand files with a thousand
values has the same median and is not.

`sites.py` still recognises a handful of conventions directly — FurAffinity,
DeviantArt, booru tag lists, Pixiv, Twitter — but it is now a convenience for
files that were *not* downloaded by a browser (AirDrop, a USB stick, a
message) rather than the mechanism. The mechanism is the two above.

A bare checksum name is deliberately not guessed at. Boorus name files that
way, and so do browser caches, download managers and git — so the hash is
recorded, the file is marked `opaque`, and no site is claimed. Those files are
the honest boundary of cheap processing, counted separately in the report from
the ones that at least name their source. Sorting them further needs something
that looks at the picture, which is the [deferred Tier 3
seam](DESIGN.md#what-a-file-is) and not built.

## Learning from what you moved back

The strongest signal available, and it costs nothing to collect: the ledger
says where every file was put, and the disk says where it is now. Any
difference is somebody disagreeing, and they said what they wanted by putting
the file somewhere else.

```sh
auto-sort corrections ~/Downloads
```

```
  Since the last check: 6 placements changed
    6 found somewhere else, 0 gone

  Rules you overrode
    all images                         6 times

  What that suggests
    looks_like = furaffinity  ->  ~/Downloads/Art
      6 files, 100% of them
```

Two outputs, and the first needs no inference at all: **a rule overridden six
times is wrong**, and saying so is often worth more than guessing at a
replacement for it. The second is the guess, and it is a candidate rule with
its count attached, written to a file for you to read — `--out` writes it,
nothing applies it. A placement that silently changed because of something
inferred from a folder is the behaviour that makes a background process
impossible to trust.

The daemon checks for this on its own, every half hour. The check is nearly
free when nothing has moved — one `stat` per recorded placement — and only
indexes the tree when something actually has, so tidying up for an hour costs
one index build rather than seven hundred.

**What stops it over-claiming** is measuring against everything placed rather
than against the corrections alone. The first working version announced
confidently that `alpha = True` predicted a folder: every file moved out of
`Images` happened to be a PNG with an alpha channel — and so did every
screenshot left exactly where it was put. A fact shared by the files you moved
*and* the files you did not move predicts nothing. With that fixed, the same
folder correctly yields no rule at all, just the note that one rule was
overridden five times.

## Starting from nothing

```sh
auto-sort init          # writes a starter rules file and tells you where
auto-sort sort ~/Downloads
```

`init` copies [rules.example.ini](rules.example.ini) to the platform's
configuration folder and refuses to touch one that is already there. The
starter arrives with dry run **on**, and even once that is off the first run
against a new folder is forced to a preview you have to look at before a
second run will move anything.

Every rule in that file is tested against a built fixture, because a rules
file that parses and then silently does nothing is the worst way this tool can
be wrong -- it looks like it worked.

## Requirements

Python 3.8 or newer. Nothing else — no pip install, no external programs, no
models. `ffprobe` and `exiftool` will be used if they happen to be there, and
their absence costs facts rather than function.

For a copied checkout, use `./start.sh` on macOS/Linux, double-click
`Start auto-sort.command` on macOS, or use `start.bat` on Windows. These
launchers require only Python 3.8+. If `ffprobe` or `exiftool` is missing they
offer, but never require, package-manager installation; declining or an
unavailable package manager still starts auto-sort. Run `python3 bootstrap.py`
yourself to make the same optional offer.

```sh
python3 -m unittest discover -s tests
```

131 tests, no binary fixtures committed: every sample file is assembled from
its own specification at test time.

## Where this is going

1. **Identification, grouping and `explain`** ✓
2. **Rules engine, ledger, real moves and `undo`** ✓
3. **The background daemon: watch, settle, queue, pause** ✓
4. **The loopback log page and ledger-ID file reveal** ✓
   Optional native status items are available through PyObjC on macOS and the
   standard-library Windows notification API. Linux continues headless when a
   StatusNotifier service is not available, with the log page as its UI.
5. **Explicit per-user start at login** ✓
   `autostart install` writes a LaunchAgent on macOS, an XDG autostart entry on
   Linux, or a Startup shortcut on Windows; `autostart remove` reverses it.
6. **Self-contained bootstrap and launchers** ✓
   `start.sh`, `Start auto-sort.command`, and `start.bat` start with Python
   alone. `bootstrap.py` can offer optional `ffprobe` and `exiftool` installs,
   but never invokes `sudo` and never blocks the sorter.

[DESIGN.md](DESIGN.md) covers all six, including the filesystem hazards that
have to be handled before anything is allowed to move a file.
