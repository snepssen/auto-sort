"""auto-sort's project page, as a catalogue.

Everything particular to this project lives here; `build.py` supplies the
chrome every page in the family shares. Adding a section is a dict in
`sections` — the jump navigation, derived from these, picks it up on its own.

Prose is authored HTML: a paragraph may carry a link, an `<em>` or a
`<span class="mono">`. It is written by whoever owns this repository and is
not escaped.
"""

FONTS = "fonts.css"

PAGE = {
    "meta": {
        "slug": "auto-sort",
        "name": "auto-sort",
        "title": "auto-sort",
        "badge": "macOS · Windows · Linux · MIT",
        "fonts": FONTS,
        "description": (
            "A Downloads folder that empties itself. auto-sort learns how a "
            "folder is organised from the folder, files everything into the "
            "folders your computer already has, and can undo all of it."),
        "og_description": (
            "Work out what each file actually is, learn the structure from "
            "the corpus rather than a configuration file, and never move "
            "anything that cannot be explained or reversed."),
        "subhead": (
            "Almost everything unwanted on a computer arrived through the "
            "Downloads folder. It is not somewhere anybody keeps things — it "
            "is where files land on the way to somewhere else — so auto-sort "
            "treats it as a funnel: everything leaves, and nothing is kept "
            "back. It works out what each file is from its bytes rather than "
            "its name, learns the structure from the folder itself, and "
            "writes down every move so that all of it can be undone."),
        "stats": [
            "<b>600</b> extensions across 13 kinds",
            "<b>145</b> magic numbers, no libmagic",
            "<b>0</b> dependencies, accounts or network calls",
        ],
    },

    "header_blocks": [
        {
            "kind": "report",
            "text": (
                '<span class="muted">$</span> auto-sort propose ~/Downloads\n'
                "\n"
                "  696 items, 757 files, 10.1 GB\n"
                "\n"
                "  Downloaded from\n"
                "    furaffinity           43\n"
                "    twitter               50\n"
                "\n"
                "  Structure this folder suggests\n"
                "    site-uploader     43 items -&gt;  21 folders, 70% sharing one\n"
                "    duration          60 items -&gt;   4 folders, 88% sharing one\n"
                "\n"
                '  <span class="muted">Considered, not proposed</span>\n'
                '    <span class="muted">camera        only 0.3% of items have camera</span>\n'
                '    <span class="muted">music         nothing in this folder has artist</span>'),
            "caption": ("What it refuses matters more than what it proposes. "
                        "A folder per artist is the obvious structure for "
                        "downloaded art — and on a folder where twenty-one "
                        "artists share forty-three files, it is declined."),
        },
    ],

    "sections": [
        {
            "id": "evidence",
            "jump": "How it decides",
            "eyebrow": "Identification",
            "heading": "Evidence, not file extensions",
            "blocks": [
                {"kind": "prose", "class": "lede", "text": [
                    "A <span class=\"mono\">.jpg</span> that is a PNG, an "
                    "<span class=\"mono\">.mp4</span> with no video track and "
                    "an <span class=\"mono\">invoice.pdf.exe</span> are "
                    "ordinary contents of the folders this exists for. Four "
                    "readers run cheapest first, and each may overrule the one "
                    "before it when it has better evidence."]},
                {"kind": "cards", "cards": [
                    {"title": "The extension",
                     "formats": "600 spellings · 13 kinds",
                     "note": "Right most of the time, free, and never trusted "
                             "further than <em>likely</em>. Nineteen are "
                             "marked ambiguous, because .ts is TypeScript or "
                             "a transport stream and the name settles it."},
                    {"title": "The bytes",
                     "formats": "145 signatures",
                     "note": "With refiners for the containers several kinds "
                             "share: PK\\x03\\x04 is a zip, and also every "
                             "Office document, EPUB, Android package and USDZ "
                             "model ever made."},
                    {"title": "The header",
                     "formats": "EXIF · ID3 · MP4 · EBML · PDF",
                     "note": "Camera and capture date, artist and album, "
                             "duration and track layout, producer and page "
                             "count — parsed here rather than shelled out to "
                             "anything."},
                    {"title": "Where it came from",
                     "formats": "kMDItemWhereFroms · Zone.Identifier",
                     "note": "Your operating system already recorded the "
                             "download URL. It is the sharpest evidence "
                             "available and almost nothing uses it."},
                ]},
                {"kind": "heading", "text": "Facts carry their source"},
                {"kind": "prose", "text": [
                    "A stronger source overrules a weaker one and <em>the "
                    "disagreement is kept</em>, so “the extension lies” is "
                    "itself something a rule can match. Agreement raises "
                    "confidence: an artist from an ID3 tag is strong, and the "
                    "same artist also parsed out of the filename is stronger, "
                    "because two independent readers would have to be wrong in "
                    "the same direction.",
                    "A fact that could not be established is absent rather "
                    "than empty. A missing reader therefore narrows what can "
                    "match instead of making it match wrongly — which is what "
                    "lets one rules file work on a capable machine and a bare "
                    "one."]},
            ],
        },
        {
            "id": "learns",
            "jump": "What it learns",
            "eyebrow": "Structure",
            "heading": "It learns the folder, rather than being told",
            "blocks": [
                {"kind": "prose", "class": "lede", "text": [
                    "Writing a rules file by hand means deciding in advance "
                    "what shape a folder should be. That is backwards: a disk "
                    "with four hundred thousand files on it already has a "
                    "shape, and the job is to find it."]},
                {"kind": "heading", "text": "Conventions it works out"},
                {"kind": "prose", "text": [
                    "A naming convention is, by definition, something that "
                    "repeats — so it can be counted rather than tabulated. "
                    "<em>In a filename written by software, a field that "
                    "repeats across many files is a category; a field "
                    "different in every file is an identifier.</em>",
                    "Given forty-three files shaped "
                    "<span class=\"mono\">&lt;digits&gt;.&lt;word&gt;_&lt;rest&gt;</span>, "
                    "the digits are all different and the words repeat, so it "
                    "writes a rule that captures the word. Nothing in the code "
                    "knows what that site is. There is no table of websites "
                    "anywhere in the project, and nothing ever contacts one."]},
                {"kind": "report", "text": (
                    '<span class="muted">; 43 files, 21 distinct values in one field,</span>\n'
                    '<span class="muted">; 70% of them sharing a folder.</span>\n'
                    "[rule: furaffinity names]\n"
                    "when    = kind = image and source = furaffinity\n"
                    "extract = stem re ^\\d{10}\\.(?P&lt;group&gt;[a-z0-9-]+)_.*\n"
                    "into    = ~/Pictures/furaffinity/{group}"),
                 "caption": "Written by the survey, not by a person — and "
                            "readable, editable and arguable because of it."},
                {"kind": "heading", "text": "What you correct"},
                {"kind": "prose", "text": [
                    "The ledger says where each file was put; the disk says "
                    "where it is. Any difference is you disagreeing, and where "
                    "you moved it to is the answer. auto-sort reports which "
                    "rules you kept overriding — often more useful than any "
                    "guess — and proposes a rule when enough corrections "
                    "agree. It proposes; it never silently adjusts."]},
                {"kind": "heading", "text": "What it filed too early"},
                {"kind": "prose", "text": [
                    "A folder reveals itself gradually, so the files that "
                    "arrive first are always filed worst — not through error, "
                    "but because there was nothing to learn from yet. "
                    "<span class=\"mono\">regroup</span> goes back for them, "
                    "moving what sits in a holding folder into structure that "
                    "has since become visible, without anybody dragging files "
                    "back into Downloads. Only provisional placements are "
                    "revisited, so it is idempotent and cannot reshuffle a "
                    "disk."]},
            ],
        },
        {
            "id": "safe",
            "jump": "Safety",
            "eyebrow": "Reversibility",
            "heading": "It is careful, and it can be undone",
            "blocks": [
                {"kind": "prose", "class": "lede", "text": [
                    "Every move is a row in a ledger before it happens and is "
                    "confirmed after: source, destination, size, content hash, "
                    "and the rule that fired. That ledger is the feature. It is "
                    "the difference between a tool somebody points at their "
                    "home directory and one they run once and delete."]},
                {"kind": "cards", "cards": [
                    {"title": "Preview first",
                     "formats": "dry run by default",
                     "note": "And the first real run against any folder is "
                             "forced to a preview you have to look at before a "
                             "second run will move anything."},
                    {"title": "Undo",
                     "formats": "auto-sort undo last",
                     "note": "Walks the ledger backwards, including removing "
                             "the empty folders the run created — and never "
                             "the ones that were already there."},
                    {"title": "Never deletes",
                     "formats": "not even duplicates",
                     "note": "The strongest action available is a move. "
                             "De-duplication means moving copies somewhere for "
                             "a person to empty."},
                    {"title": "Never overwrites",
                     "formats": "collisions get a suffix",
                     "note": "A cross-volume move copies, verifies by hash, "
                             "then removes — never the other order. An "
                             "external drive is sorted in place."},
                ]},
                {"kind": "prose", "text": [
                    "Nothing here touches the network, and it is not a service. "
                    "There is nothing to sign up for, no telemetry and no "
                    "crash reporter — which is also why a failure has no way "
                    "of reaching anybody on its own."]},
            ],
        },
        {
            "id": "running",
            "jump": "Running it",
            "eyebrow": "Getting started",
            "heading": "It runs itself, from an icon",
            "blocks": [
                {"kind": "prose", "class": "lede", "text": [
                    "Double-click <b>Start auto-sort.command</b> on macOS, "
                    "<span class=\"mono\">start.sh</span> on Linux or "
                    "<span class=\"mono\">start.bat</span> on Windows. It "
                    "writes a rules file if there is not one, says what it is "
                    "about to watch, points out that dry run is on, and then "
                    "runs — leaving an icon in the menu bar and a log page "
                    "behind it. None of that requires knowing what Python is."]},
                {"kind": "report", "text": (
                    "auto-sort propose ~/Downloads --out my-rules.ini "
                    '<span class="muted">what this folder needs</span>\n'
                    "auto-sort sort ~/Downloads --rules my-rules.ini  "
                    '<span class="muted">see what it would do</span>\n'
                    "auto-sort explain FILE                           "
                    '<span class="muted">why one file goes where</span>\n'
                    "auto-sort corrections                            "
                    '<span class="muted">what you moved back</span>\n'
                    "auto-sort undo last                              "
                    '<span class="muted">take it all back</span>'),
                 "caption": "The terminal is optional and the icon is not a "
                            "wrapper around it — both drive the same ledger."},
                {"kind": "heading", "text": "What it needs"},
                {"kind": "prose", "text": [
                    "<b>Python 3.8 or newer, and nothing else</b> — no pip "
                    "install, on any platform. Reading headers, learning "
                    "conventions, deciding, moving files and remembering all of "
                    "it are done with <span class=\"mono\">struct</span>, "
                    "<span class=\"mono\">re</span>, <span class=\"mono\">os</span> "
                    "and <span class=\"mono\">sqlite3</span>, which every "
                    "Python already has.",
                    "Even the menu bar icon has no dependency: it is built on "
                    "the Objective-C runtime through "
                    "<span class=\"mono\">ctypes</span>, the same way the "
                    "Windows one is built on "
                    "<span class=\"mono\">Shell_NotifyIcon</span>. "
                    "<span class=\"mono\">ffprobe</span> and "
                    "<span class=\"mono\">exiftool</span> are used if they "
                    "happen to be installed, and declining both still leaves a "
                    "working sorter."]},
                {"kind": "heading", "text": "What it deliberately does not do"},
                {"kind": "prose", "text": [
                    "It does not look at content — no image, audio or video "
                    "recognition. Files named after their own checksum carry "
                    "nothing to sort by, and the report says how many there "
                    "are rather than pretending otherwise. It does not rename "
                    "a film library from an online database; that is Radarr's "
                    "job and it is better at it. And it does not delete."]},
            ],
        },
    ],

    "footer": {
        "repo": "https://github.com/snepssen/auto-sort",
        "note": ("auto-sort is MIT licensed. The design notes and the rules "
                 "format are in the repository, and both are longer than this "
                 "page."),
    },
}
