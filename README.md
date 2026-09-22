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
python3 autosort.py propose ~/Downloads --out my-rules.ini
python3 autosort.py regroup ~/Downloads
python3 autosort.py corrections ~/Downloads
python3 autosort.py adopt --apply
python3 autosort.py duplicates ~/Music --apply
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

## A scanned page is not a photograph

A scanner writes into the same EXIF tags a camera does — Make, Model, even a
lens field some firmware fills with nonsense — so `camera is set` is true of
a scanned tax return exactly as it is true of a photograph. Filed by the
obvious rule, twenty years of paperwork lands in Pictures, in folders named
after an Epson.

Two things separate them, and neither alone is enough. A scanner writes no
exposure: no shutter speed, no aperture, no ISO — none of it means anything
to a lamp on a rail. That only says *not a camera*, which is equally true of
a screenshot or an exported PNG. And a scan is a picture of a sheet of known
physical size, which the file states twice over — pixel dimensions and
resolution — so a scanned A4 page divides out to 8.27 by 11.69 inches at
whatever DPI it was scanned at. Shape alone is worse than useless: a sweep of
a real library flagged pieces of digital art as A4 purely because root-two is
a pleasant aspect to crop to. It is the *conjunction* that means something,
and only because 72 DPI — what every editor and web exporter writes — is
excluded outright as a resolution nobody scans at.

The device is filed under `scanner`, not `camera`, which is what actually
removes the collision. Guarding the photographs rule with `capture != scan`
does not work: a comparison against an absent fact is false, and an ordinary
photograph has no `capture` fact at all, so the guard would stop the rule
matching anything.

```ini
[rule: scanned paperwork]
when = kind = document and capture = scan and scan_of = page
into = ~/Documents/Scans/{added:%Y}
holding = yes
```

Scanned prints go to Pictures rather than Documents — a scan of somebody's
grandmother is a photograph, whatever the platen was — but only once a
scanner is named outright, because 4×6 is 3:2 and 5×7 is nearly A4, and shape
alone would misfile half a photo library as paper.

## Reading what a document says

`scan0001.pdf`. `Document1.pdf`. `20090314.pdf`. That is what a decade of
bank portals, scanner drivers and Save As dialogues actually produced, and
those are exactly the files somebody is required to keep for twenty-five more
years. The filename is a dead end and the metadata usually is too — the
words on the page are the only evidence left, and until this was built,
nothing read them.

Getting at them needs nothing but `zlib`. Page content is a stream of drawing
operators, the streams are Flate-compressed, and the text-showing operators
take a plain string. There is no need to lay out a page or resolve an object
graph to learn that the first word on it is `Rechnung`.

Two things had to be right or the feature would read as noise on anything
written this century. Modern writers subset their fonts, so the bytes in a
content stream are glyph numbers, not letters — decoded naively they read as
`(OHFWURWHFKQLFDO` where the page says `Electrotechnical`. Every such file
carries a `ToUnicode` table for exactly this reason, so it is parsed and the
codes mapped through whichever font was active when the string was drawn; a
font with no table falls back to reading its bytes as characters, which is
right for the old files this matters most for. And PDF separates words by
moving the pen, not by drawing a space character, so raw extraction reads as
`TamásTörökMultilingualHousekeeper` with no boundary a keyword could ever
match — pen moves and kerning past a threshold are read as the spaces they
are.

Only the first five hundred characters of a page are ever offered anywhere
else in the program. A document announces what it is at the top and mentions
everything else further down: read whole, a CV that lists two certifications
looks like a certificate, and a covering letter that mentions a booking looks
like a ticket, both observed on real files. Read from the top, both say
nothing, correctly — their filenames already carried the answer, and a
reader that stays quiet leaves better evidence standing rather than
overruling it.

A page that is a photograph of a page — no text layer, however hard it is
looked at — is told apart from one that simply has nothing to say. It gets
`needs_ocr` and is held rather than guessed at, because there was nothing to
find, which is a different fact from finding nothing.

## Categories nobody configured, in languages nobody taught it

There used to be a table here: sixteen kinds of paperwork, each a regular
expression for the words that name it in six languages. It sorted post
written in those six languages and filed a lone invoice into a folder called
`invoice` holding one file, and the very first thing it needed after being
finished was a seventh language. That is not a table, it is an admission that
the pile this tool is for was never going to be described in advance.

What replaced it is counting, over the same evidence the previous two
sections produce — a document's heading, or its filename when it has no
readable text. A word heading three or more of somebody's files, and not
nearly all of them, is a category those files chose. A word heading nearly
every one of them is the letterhead — a name, a bank, a town — and describes
the pile instead of dividing it, so it is dropped. On a real pile of Belgian
and German paperwork this finds `Loonbrief`, `Rechnung`, `Steuerbescheid` and
`Mietvertrag` with no vocabulary anywhere in the code, and correctly finds
nothing at all in a folder of CVs that all begin with the same person's name.

Three refinements earned their place against real files, not a clean example.
**Spelling.** Twenty years of typing habits give `Rechnung`, `rechnung` and
`RECHNUNG` in one folder; words are counted folded and the folder takes
whichever spelling was commonest. **A letter's own template is not a pile of
categories** — a payslip says `Loonbrief` at the top and `Kantoor` and
`nummer` further down, on the same forty documents every time, and a word
whose documents are entirely accounted for by an earlier word belongs to that
word's template rather than beside it. **A name spanning two categories is
neither** — `Tamás` heads both the CVs and the payslips and is a person, not
a third kind of document.

```ini
[rule: what these files are called: Rechnung]
when = stem contains Rechnung
into = ~/Documents/Rechnung
; 9 files are named it
```

One rule per learnt word, in the order the words usually sit on the page, so
a kind of document comes before whoever sent it. `auto-sort adopt` can add
one of these later without touching a line you wrote — see below.

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

## Keeping it running

```sh
auto-sort restart          # stop it and start it again on the current code
auto-sort status           # is it running, is it paused, what is queued
auto-sort pause / resume
```

The daemon re-reads its rules whenever the file changes, but it cannot reload
*itself* — a change to auto-sort's own code only takes effect in a new
process, and it is needed at exactly the moment it is least obvious: right
after a change, when everything looks fine and the old code is still running.

Where a service manager owns the process, `restart` asks it to do the swap so
the replacement stays supervised. macOS is the only platform here that has
one; an XDG autostart entry and a Startup-folder shortcut say what to run at
login and manage nothing afterwards, so there the daemon is asked to stand
down and a detached replacement is started directly.

Either way it waits for a process with a **different pid** before reporting
success. Checking that something answers passes the moment the old process
replies, which it does right up until it exits.

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

## Going back for what was filed too early

A folder teaches auto-sort gradually. The first three pictures from an artist
are not a pattern; the twentieth makes one. Everything that arrived before
that point went into a holding folder — correctly, there was nothing better to
do with it — and the obvious failure is that it stays there forever while only
new arrivals benefit.

```sh
auto-sort regroup ~/Downloads          # show what could move
auto-sort regroup ~/Downloads --apply  # move it
```

```
  3 files can move out of a holding folder into structure
  that has become visible since they were filed.

  Into
    ...Pictures/By name/kinniro                     1
    ...Pictures/By name/lemas                       1
    ...Pictures/By name/koul                        1
```

Nobody drags anything back into Downloads. The ledger already records where
each file was put, by which rule, and what was known about it at the time,
which is enough to reconsider the decision without the file ever moving back.

**Promotion only, and only out of holding.** A file placed by a rule marked
`holding = yes` may move to a rule that is not. Nothing else is ever
reconsidered. That single restriction is what stops this becoming churn: a
decision that was already specific is never relitigated, so editing a rules
file cannot quietly reshuffle a disk and a file cannot ping-pong between two
rules that both want it. Run it twice and the second run does nothing.

**Anything you moved yourself is untouchable.** If a file is not exactly where
the ledger says it was put, you moved it, and that is an answer rather than a
gap — `corrections` learns from it, and regrouping will not overrule it.

A promotion is an ordinary move: same planner, same collision handling, same
forced preview the first time, same ledger, and `auto-sort undo` reverses it
like anything else. The daemon checks every half hour and reports; set
`regroup = apply` in `[settings]` to let it act.

The flag lives in the ledger, recorded when the file was placed, rather than
being worked out later from the rule's name — names change every time a rules
file is regenerated, and a file's history must not depend on that.

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

## Keeping the rules honest

`check-rules` used to mean "does this file parse." Two failures never showed
up in that answer, and both look exactly like success: the funnel empties,
every file moves, and the wrong thing happened anyway.

**A rule that matches and then quietly does nothing.** `min_confidence` is a
floor on *acting*, not on matching — a rule whose destination needs a fact
that is only a guess matches perfectly, declines, and the file goes to a
catch-all with nothing said. `Scans/{happened:%Y}` did exactly this: a
scanned page has no capture date, so `happened` falls back to a WEAK guess at
when the file arrived, under the floor, and the rule silently never fired.
`check-rules` now walks the watched folder and names any rule that matched
real files and placed none of them:

```
  1 rule matched files in ~/Downloads and never placed one:
    scans by year
      matched 6, placed 0 -- below confidence 0.60: happened 0.45
```

**A rule that has had its chance and lost it, over and over.** Household
paperwork repeats — the same letter, month after month — so every bill that
arrives is another trial, and induction proposes generously: a word that
turns up three times might be the kind of document, or it might be the town
it was posted from, and nothing in the page says which. Time settles it. A
rule that has matched a hundred files and never once been the answer, because
something above it always wins first, is named:

```
  9 rules have never been the answer, across 126 filed files:
    Stadtwerke               matched  10, always lost to Rechnung
```

A catch-all is never named this way, however often it loses — it exists to
be last, and the day it fires is the day it earns its keep. Neither is a rule
that has simply never matched anything: silence is not evidence.

**And the reverse.** A kind of letter that did not exist when the rules were
written has no rule of its own, so it gets claimed by whatever else happens
to match — often the company that sent it, because that word is on the page
too. `check-rules` also names words that now head enough filed documents to
deserve a folder and have none:

```
  1 word has headed enough filed documents to deserve a folder,
  and no rule names it (out of 29 documents read):
    Mahnung                  heads 4 of them
```

`auto-sort adopt` writes that one rule and nothing else — inserted above
every rule currently claiming those documents by a worse word, or it would
sit there unreachable exactly like the first failure above, and above the
first catch-all, or first match would never reach it. Every other line in
the file, comments included, is untouched:

```sh
auto-sort adopt              # preview what would be added
auto-sort adopt --apply      # add it
```

`propose` regenerates a rules file from scratch, which is right the first
time and wrong every time after — it would discard whatever you had since
written, reordered or deleted. `adopt` is how the rules keep learning without
that cost.

## Two copies of the same file is one too many

macOS has no cut-and-paste for files, so tidying by hand means copy, then
remember to go back and delete the original — and the second half is the
half that does not happen. `auto-sort duplicates` finds files that are
byte-for-byte the same and clears the spare copy, reading the disk rather
than the ledger, because a copy filed by hand is invisible to anything that
only remembers what auto-sort itself moved.

Which copy is the real one is decided by where it belongs before it is
decided by how it got there. A `.md` lyric sheet copied next to the music it
was written for is still a document, and the sorter itself would file it
under Documents — a duplicate check that disagreed would tidy the disk one
way and file it the other. Only among copies that agree on that does location
matter: a copy in the intake funnel or in a holding folder loses to a copy in
a folder somebody chose. Two copies in two chosen folders are somebody's own
filing and are left alone.

```
  153.0 MB, 2 copies
    keep   ~/Movies/Projects/Protoke Video/Portrait/core/B04 Oli.mp4
    spare  ~/Music/Core Aura/B/B04 Oli-short.mp4
```

Real duplicates copied by hand hide in the strangest places for exactly this
reason — video files sitting in a Music folder beside the album they were
made for, invisible to the ledger because both copies were placed by hand and
neither was ever in a funnel.

A name is worth more than the disk space, and this is checked before
anything is binned. A folder can be so thoroughly machine-named — `exec-
63512093-74d5-4282-a7fc-159ff1ce12ea.png` a hundred and thirty-eight times
over — that keeping its "correct" copy would erase the only readable name in
the group. When that happens, the name is moved across before the spare is
binned, not thrown away with it: the file goes where its kind belongs, and
the name goes with it. Judged per folder rather than per file, because one
accidentally well-named file among a hundred machine names is an accident,
not a scheme worth protecting.

The spare copy goes to the operating system's own bin — not deleted, still
there, already understood, emptied on the person's own schedule — and the
move is journalled like any other, so `undo` reaches it without anyone
opening the Trash.

## A second copy, for people who have never made one

Nobody this tool is for has a backup, and telling them to make one does not
fix that. `auto-sort` turning on a second copy while it does the sorting it
was already doing does. Off by default — out of the box this touches nothing
but the folders the machine already has — and on in a couple of clicks from
the same log page.

The disk being unplugged, asleep, full, or in a drawer is the *ordinary*
case for the people this matters most to, not the exception, so sorting
never waits on it and never fails because of it. The intention to copy a
file is written to the ledger the instant it is filed, while its hash is
still in hand; the copying happens whenever the disk is actually there. A
drive missing for three weeks means a queue three weeks long and nothing
else — no failed sorts, no files stuck in Downloads.

Whether the disk is there is decided by writing to it, not by looking. A
mount point whose disk has gone is still a directory — an empty one, on the
machine's own disk — and copying a backup into that quietly fills the boot
drive with a copy of itself. Copies are written beside their final name,
hashed as they go, and only take the real name once the bytes match what was
recorded, so a power cut leaves a stray part-file rather than half a file
wearing a whole one's name.

Turning it on protects what is already there, not only what arrives next —
twenty years of files were sorted before anyone clicked the button. And the
wastebasket is never mirrored: auto-sort's own duplicate cleanup puts spare
copies there, and a backup that faithfully preserves the bin resurrects
exactly what the tidying just removed.

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

## Starting it without learning a command line

Double-click **Start auto-sort.command** on macOS, `start.sh` on Linux, or
`start.bat` on Windows. With no arguments the launcher runs `auto-sort start`,
which writes a rules file if there is not one, says what it is about to watch,
points out that dry run is on, and then runs — leaving an icon in the menu bar
and a log page to click through to.

Nothing about that path requires knowing what Python is.

## Requirements

**Python 3.8 or newer, and nothing else.** No pip install, on any platform.
That is not an aesthetic preference. Reading headers, learning naming
conventions, deciding, moving files and remembering every one of those
decisions in the ledger are done with `struct`, `re`, `os` and `sqlite3`,
which every Python already has. Once it is installed it runs by itself and
improves by itself, without turning into a development environment somebody
has to maintain.

The menu bar icon is no exception. It is built on the Objective-C runtime
through `ctypes`, the same way the Windows tray is built on
`Shell_NotifyIcon` — both talk to the system directly. An earlier version used
PyObjC and it was a mistake twice over: forty megabytes for one icon, and it
**cannot be installed at all** on a Homebrew, Debian or Fedora Python, because
those are marked externally managed under PEP 668 and refuse `pip install`.
The icon was unreachable on exactly the machines most likely to run this.

Two optional *programs* genuinely add something, and the launcher offers them
on first run — described by what they let the tool do, never by package name
first:

| | What it adds | Without it |
| --- | --- | --- |
| `ffprobe` | durations and frame sizes for containers the built-in parsers decline | those facts are absent, and rules needing them decline |
| `exiftool` | metadata from uncommon cameras and RAW formats | the same, for a smaller set of files |

Declining both still leaves a working sorter. `bootstrap.py` never runs
`sudo` — on a system whose package manager needs root it prints the command
for you to run — never installs without being asked, never prompts when there
is nobody to answer, and never blocks the sorter when something fails.

```sh
python3 -m unittest discover -s tests
```

418 tests, no binary fixtures committed: every sample file is assembled from
its own specification at test time.

## Where this is going

1. **Identification, grouping and `explain`** ✓
2. **Rules engine, ledger, real moves and `undo`** ✓
3. **The background daemon: watch, settle, queue, pause** ✓
4. **The loopback log page and ledger-ID file reveal** ✓
   Optional native status items are built on the Objective-C runtime through
   `ctypes` on macOS and the standard-library Windows notification API on
   Windows — no PyObjC, no dependency of any kind. Linux continues headless
   when a StatusNotifier service is not available; a menu entry and the log
   page are its UI there instead of a tray icon.
5. **Explicit per-user start at login** ✓
   `autostart install` writes a LaunchAgent on macOS, an XDG autostart entry on
   Linux, or a Startup shortcut on Windows, plus an applications-menu entry on
   Linux where there is otherwise no tray to click; `autostart remove`
   reverses all of it.
6. **Self-contained bootstrap and launchers** ✓
   `start.sh`, `Start auto-sort.command`, and `start.bat` start with Python
   alone. `bootstrap.py` can offer optional `ffprobe` and `exiftool` installs,
   but never invokes `sudo` and never blocks the sorter.
7. **Structure that builds and corrects itself** ✓
   `propose` surveys a folder and writes the rules it turns out to need,
   without a table of sites, document types or languages anywhere in the
   code. `regroup` promotes files out of holding once a pattern shows;
   `corrections` learns from a placement somebody moved back;
   `check-rules` names a rule that matches and never wins, or never
   fires at all; `adopt` writes a newly-earned rule without rewriting
   anyone's file.
8. **Reading a document, not just its name** ✓
   PDF text extraction (subset-font `ToUnicode` maps included), a scanner
   told apart from a camera, and a decompression cap so one adversarial
   PDF cannot ask for a gigabyte.
9. **Never losing a second copy** ✓
   `duplicates` finds and clears byte-identical files already on disk,
   choosing by where a file belongs before how it got there and never at
   the cost of the only readable name in a folder. An optional, off-by-
   default mirror keeps a second copy on another disk, queued rather than
   blocking, and never touching the wastebasket.
10. **The log page as the whole interface** ✓
    Three views — the move ledger, the rules as learnt, and folders and
    drives — a native folder picker for a one-time sort or a new intake,
    and ledger search and compaction so the database that remembers
    everything does not need a disk of its own.

[DESIGN.md](DESIGN.md) covers the whole shape, including the filesystem
hazards that have to be handled before anything is allowed to move a file.
