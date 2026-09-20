# Rules

The rules file is the whole configuration of what auto-sort does. It is a
plain text file someone can open in Notepad, read top to bottom, and understand
without knowing Python.

## Why INI, and not TOML or JSON

TOML would be the modern answer, and `tomllib` is standard library — from 3.11.
The 3.8 floor means vendoring a parser, which is a dependency wearing a
disguise. JSON has no comments, and a configuration file you cannot annotate is
one nobody maintains.

`configparser` has been in the standard library since forever, runs on every
Python auto-sort will ever meet, supports comments, preserves section order, and
is legible to someone who has edited an `.ini` before — which is most people who
have ever edited a config file at all. Its weakness is nesting, and these rules
do not nest.

## Two files, and only one of them is yours

| File | Owner | Written by |
| --- | --- | --- |
| `rules.ini` | You | Only you. auto-sort reads it and never writes it. |
| `state.db` | auto-sort | Watched folders, port, paused flag, the ledger, the queue. |

The split exists because `configparser` cannot write a file back without
eating the comments. Anything the tray or the log window can change lives in
`state.db`, so the file you hand-edit is never rewritten underneath you.

Location: `~/.config/auto-sort/rules.ini` on Linux,
`~/Library/Application Support/auto-sort/rules.ini` on macOS,
`%APPDATA%\auto-sort\rules.ini` on Windows. A `rules.ini` next to the checkout
wins over all of them, which is how you test one without touching your real
setup.

## A complete small file

```ini
[settings]
dry_run        = yes           ; say what you would do, do nothing
unsorted       = leave         ; leave | gather
unsorted_into  = ~/Unsorted
on_collision   = suffix        ; suffix | skip
min_confidence = 0.6
settle_seconds = 3
poll_seconds   = 5
preserve_dates = yes

[watch]
folders = ~/Downloads, ~/Desktop
ignore  = *.part, *.crdownload, *.download, ~$*, .DS_Store, Thumbs.db
depth   = 3                    ; how far into subfolders to look

; ---------------------------------------------------------------------------
; Rules are tried in file order. The first one that matches wins, and nothing
; below it is considered. Put the specific ones first.
; ---------------------------------------------------------------------------

[rule: screenshots]
when = kind = image and looks_like = screenshot
into = ~/Pictures/Screenshots/{taken:%Y-%m}

[rule: camera photos]
when = kind = image and camera is set
into = ~/Pictures/{taken:%Y}/{taken:%Y-%m-%d} {camera}

[rule: raw with its jpeg]
when = kind = image and format in dng, cr2, cr3, nef, arw, raf
into = ~/Pictures/RAW/{taken:%Y}

[rule: everything else with pixels in it]
when = kind = image
into = ~/Pictures/Loose/{format}

[rule: films]
when = kind = video and duration > 70m and looks_like = scene-release
into = ~/Videos/Films

[rule: episodes]
when = kind = video and duration between 18m and 70m and looks_like = scene-release
into = ~/Videos/Series/{title}/Season {season}

[rule: phone clips]
when = kind = video and aspect < 1 and duration < 3m
into = ~/Videos/Clips/{created:%Y-%m}

[rule: screen recordings]
when = kind = video and (fps >= 50 or encoder ~ *ScreenCapture*)
into = ~/Videos/Recordings/{created:%Y-%m}

[rule: video, unclassified]
when = kind = video
into = ~/Videos/Loose

[rule: tagged music]
when = kind = audio and artist is set and album is set
into = ~/Music/{artist}/{album}
as   = {track:02} {title}.{ext}

[rule: sample packs]
when = kind = audio and duration < 5s and channels = 1
into = ~/Music/Samples/{dir}

[rule: voice memos]
when = kind = audio and channels = 1 and duration < 30m and artist is unset
into = ~/Music/Voice/{created:%Y-%m}

[rule: audio, unclassified]
when = kind = audio
into = ~/Music/Loose/{format}

[rule: scanned paperwork]
when = kind = document and format = pdf and producer ~ *Scan*
into = ~/Documents/Scans/{added:%Y}

[rule: bank statements]
when = kind = document and name ~ *statement* and from_host ~ *.bank.example
into = ~/Documents/Finance/{added:%Y}

[rule: documents]
when = kind = document
into = ~/Documents/{format}

[rule: subtitles travelling alone]
when = kind = subtitle and bundle_role = primary
into = ~/Videos/Subtitles

[rule: 3d]
when = kind = model3d
into = ~/Models/{stem}

[rule: installers]
when = kind in app, disk-image
into = ~/Downloads/Installers/{added:%Y-%m}

[rule: archives]
when = kind = archive and size > 50mb
into = ~/Downloads/Archives

[rule: duplicates]
when = duplicate_of is set
into = ~/Downloads/Duplicates
```

A rule with no `into` and `mode = leave` is how you say *this one stays where
it is* and stop later rules from claiming it.

## The `when` expression

One line. `and`, `or`, `not`, parentheses. A small recursive-descent parser,
no `eval`, and a syntax error names the rule and the column rather than raising.

| Operator | Example |
| --- | --- |
| `=` `!=` | `kind = image` |
| `<` `<=` `>` `>=` | `size >= 4gb` |
| `between … and …` | `duration between 18m and 70m` |
| `in` | `format in mp4, mkv, mov` |
| `~` | `name ~ *invoice*` — glob, case-insensitive |
| `re` | `stem re ^IMG_\d{4}$` |
| `is set` / `is unset` | `camera is set` |

Numbers take suffixes: `kb mb gb tb` for sizes, `s m h d` for durations and
ages. A fact that could not be established is *unset*, and every comparison
against unset is false — so a rule needing `duration` simply does not fire on a
machine without ffprobe, rather than firing wrongly.

## Facts

Present only when they could be established.

**Always** — `name`, `stem`, `ext`, `dir`, `path`, `depth`, `source_root`,
`size`, `added`, `modified`, `age`, `kind`, `format`, `mime`, `confidence`,
`extension_lies` (the signature disagreed with the extension).

**Bundle** — `bundle` (is part of a set), `bundle_role` (`primary`/`sidecar`),
`members` (count), `is_dir`.

**Provenance** — `from_url`, `from_host`, `quarantined`, `downloaded`.

**Filename shape** — `looks_like`, one of `screenshot`, `scene-release`,
`camera`, `phone-export`, `whatsapp`, `invoice`, `installer`, `sample`, or
unset. Derived from patterns, so always the lowest-confidence fact in the
record. Also `title`, `season`, `episode`, `year` when a scene-style name parses.

**Images** — `width`, `height`, `aspect`, `alpha`, `colours`, `camera`, `lens`,
`taken`, `gps`, `orientation`, `software`.

**Video** — `duration`, `width`, `height`, `aspect`, `fps`, `encoder`,
`created`, `audio_tracks`, `subtitle_tracks`, `chapters`.

**Audio** — `duration`, `channels`, `samplerate`, `bitrate`, `lossless`,
`artist`, `album`, `title`, `track`, `genre`, `year`.

**Documents** — `pages`, `producer`, `author`, `doc_title`, `encrypted`.

**Content** — `duplicate_of`, and `labels.*` if a Tier 3 classifier plugin is
installed. Nothing ships one; rules referencing `labels.*` are inert without
it, which is the point.

## Destinations

`into` is a folder template, `as` an optional filename template. Tokens are
facts in braces.

- `{taken:%Y-%m}` — a date fact with a `strftime` format.
- `{track:02}` — a number, zero-padded.
- `{artist|Unknown}` — a fallback for when the fact is unset. Without one, a
  rule whose template needs a missing fact does not match, and the next rule
  gets its turn. That is deliberate: it is how `[rule: tagged music]` hands
  untagged files down to `[rule: audio, unclassified]` without either rule
  having to know about the other.
- Every token is sanitised for the destination filesystem before it is used —
  separators stripped, reserved Windows names escaped, length clamped. An
  ID3 tag is untrusted input and a malicious one should not be able to write
  outside the target folder.

Relative destinations resolve against the item's `source_root`, so
`into = Sorted/{kind}` on a USB stick sorts within the stick.

## Per-rule options

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `move` | `move`, `copy`, `leave` |
| `stop` | `yes` | `no` continues matching, for a rule that only tags |
| `min_confidence` | from `[settings]` | Floor for this rule. Applies to every fact the `when` consulted **and** every fact the destination fills in, so a rule filing by `{happened}` needs `happened` to clear it. |
| `newer_than` / `older_than` | — | `older_than = 180d` for an archive rule |
| `only_on` | all | `macos`, `windows`, `linux` |

## Checking a file before trusting the file

```sh
auto-sort explain ~/Downloads/some-weird-thing.bin
```

Prints the facts that were established and how, then each rule in order with
why it did or did not match, then the destination it would choose. Every
support conversation about this tool will start here, so it ships in milestone
1 with the engine rather than later.

## Previewing, applying and undoing

`sort` is read-only while `dry_run = yes` (the default):

```sh
auto-sort sort ~/Downloads
```

Use `--apply` to explicitly request the moves. The first apply for each watched
folder and exact rules-file revision is still forced to be a dry run; review
its output and repeat the command to apply it. Changing the rules requires a
fresh preview.

```sh
auto-sort sort ~/Downloads --apply
auto-sort undo last
```

Every bundle member is recorded in `state.db` before the filesystem operation.
Same-volume moves preserve the original inode. Cross-volume moves copy to a
temporary name, verify SHA-256, install without replacing anything, and only
then remove the source. `undo` performs the same hash check in reverse and
refuses the entire bundle if any member changed or its original path is now
occupied.

## Running the watcher

The persistent watcher runs in the foreground so a terminal, launch agent, or
service manager can own its lifetime:

```sh
auto-sort watch --apply
```

It polls each `[watch]` folder, stores bundle observations in `state.db`, and
only hands an item to the normal sorter after its size and timestamps remain
unchanged for `settle_seconds`. Pulling a removable watched volume retains its
queue. Paths produced by sorting are excluded from subsequent scans, including
destinations inside a watched folder.

The first real background sort for a folder and exact rules revision creates a
preview, prints every proposed destination, and pauses. Review it, then run:

```sh
auto-sort resume
auto-sort sort-now
```

`pause` and `resume` are durable state, not signals, so the choice survives a
restart. `status` reports whether the loopback single-instance port is live and
counts queued items by state. Use `watch --once` for one observation cycle in a
script or test; normal operation leaves it running.

With `depth = 0`, a watched folder acts as a strict inbox. Files and package
directories are classified normally, while an ordinary top-level folder is
treated as one atomic item: its contents are neither entered nor rearranged.
This lets a rule such as `when = is_dir = yes` move the folder intact and keeps
the intake empty without dismantling old projects or archives.

## Reading the local log

While the watcher is running, `open-log` opens its log page:

```sh
auto-sort open-log
```

The page is bound only to `127.0.0.1` and each daemon start creates a fresh,
unguessable token embedded in the URL. Mutating requests also require the page's
own loopback origin. The **Reveal** button sends only a ledger move ID: the
daemon resolves the corresponding recorded source or destination itself and
never accepts a path from the browser.

The dense move ledger initially shows the latest 50 files and can load up to
500 in steps of 50. Its defaults are Name, Kind, Format, Destination, Rule,
Date, Size, and Status. Kind, format, dimensions, media tags, timestamps,
provenance, and the other optional classification columns come from the same
identified fact record evaluated by the rules engine; they are not a separate
UI guess. The **View configuration** panel saves column choices and a manual
Copy/Move destination folder in that browser. Displayed operation dates use the
browser's locale and time zone instead of exposing the ledger's ISO timestamp.

Reveal, Copy, Move, Restore, and Trash actions still send only a ledger move ID.
Copy and Move require an existing destination folder and refuse collisions.
Every new operation inherits the original classification facts so later rows
remain filterable and auditable. **Trash** uses the operating system's
Trash/Recycle Bin rather than permanently deleting a file.

## Tray capability

The watcher stays useful without a desktop integration. When PyObjC is present
on macOS, or when Windows exposes its standard notification area API, it
provides **Open log**, **Pause/Resume**, **Sort now**, and **Quit**. Linux
desktops without a StatusNotifier service continue headless; `open-log`,
`pause`, `resume`, and `sort-now` remain available from the command line.

## Starting at login

Starting at login is an explicit per-user choice; running `watch` alone never
creates a background launcher. Inspect the current state, then install it only
after the rules file passes validation:

```sh
auto-sort autostart status
auto-sort autostart install --rules ~/Library/Application\ Support/auto-sort/rules.ini
```

`install` uses a macOS LaunchAgent, Linux XDG autostart entry, or Windows
Startup shortcut. To stop future automatic launches and remove only the entry
created for auto-sort:

```sh
auto-sort autostart remove
```
