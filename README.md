# auto-sort

A folder arrives at a state no-one chose. `New Folder/New Folder/New Folder`,
eleven years of Downloads, a USB stick that was a backup once. auto-sort will
sit in the tray, watch the folders it has been told to watch, work out what
each file actually is, and file it where the rules say — one file at a time,
as they arrive, for as long as the machine is on.

**It does not move anything yet.** This is milestone one of six: the part that
works out what a file is. See [DESIGN.md](DESIGN.md) for the whole shape and
[RULES.md](RULES.md) for the configuration format the rules engine will read.

## What works now

```sh
python3 autosort.py explain ~/Downloads/some-file.bin
python3 autosort.py scan ~/Downloads --list 40
```

`explain` prints every fact about one file, which reader established it, and
how sure that reader was. `scan` groups a folder into items and counts what is
in it. Neither writes anything.

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

## Requirements

Python 3.8 or newer. Nothing else — no pip install, no external programs, no
models. `ffprobe` and `exiftool` will be used if they happen to be there, and
their absence costs facts rather than function.

```sh
python3 -m unittest discover -s tests
```

44 tests, no fixtures committed: every sample file is assembled from its own
specification at test time.

## Where this is going

1. **Identification, grouping and `explain`** ← you are here
2. Rules engine, ledger, real moves and `undo`
3. The background daemon: watch, settle, queue, pause
4. The log page on `127.0.0.1`, reveal-in-file-manager, and the tray
5. Starting at log-in, on all three platforms
6. `bootstrap.py` and the launchers

[DESIGN.md](DESIGN.md) covers all six, including the filesystem hazards that
have to be handled before anything is allowed to move a file.
