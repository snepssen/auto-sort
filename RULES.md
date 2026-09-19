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
| `min_confidence` | from `[settings]` | Floor for this rule |
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
