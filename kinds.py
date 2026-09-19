"""The taxonomy, and what an extension claims a file is.

The first filter is the obvious one: the extension. It is right most of the
time, it costs nothing, and on a folder of forty thousand files it is the only
thing that can afford to touch all of them. It is also the least trustworthy
thing in the record, so what it produces is a claim at `LIKELY`, which any
header the signature reader manages to parse will overrule.

Two parts of this table matter more than the bulk of it:

`AMBIGUOUS` names the extensions where the kind genuinely cannot be settled
from the name. `.ts` is a TypeScript source file or an MPEG transport stream.
`.key` is a Keynote presentation or a private key. `.mod` is tracker music or
video off an old JVC camcorder. For these the extension contributes nothing
until a reader looks inside, and pretending otherwise is how a folder of source
code ends up in Videos.

`FORMATS` canonicalises. `jpg`, `jpeg`, `jpe` and `jfif` are one format, and a
rules file should be able to say `format = jpeg` once rather than list the
spellings. The extension as typed is kept separately as `ext`.
"""

from __future__ import annotations

# The kinds. Every item gets exactly one, and `unknown` is a legitimate answer
# that must not be papered over — a rule can match it and put it somewhere for
# a human to look at.
KINDS = ("image", "video", "audio", "document", "subtitle", "archive",
         "model3d", "font", "code", "app", "disk-image", "data", "unknown")

# kind, canonical format, the extensions that spell it.
_TABLE = (
    # -- images ----------------------------------------------------------
    ("image", "jpeg", "jpg jpeg jpe jfif jif"),
    ("image", "png", "png apng"),
    ("image", "gif", "gif"),
    ("image", "bmp", "bmp dib"),
    ("image", "tiff", "tif tiff"),
    ("image", "webp", "webp"),
    ("image", "heif", "heic heif hif"),
    ("image", "avif", "avif avifs"),
    ("image", "jxl", "jxl"),
    ("image", "jpeg2000", "jp2 j2k jpf jpx jpm"),
    ("image", "icon", "ico cur icns"),
    ("image", "svg", "svg svgz"),
    ("image", "psd", "psd psb"),
    ("image", "xcf", "xcf"),
    ("image", "affinity", "afphoto afdesign"),
    ("image", "targa", "tga icb vda vst"),
    ("image", "netpbm", "pbm pgm ppm pnm pam"),
    ("image", "openexr", "exr"),
    ("image", "radiance", "hdr"),
    ("image", "dds", "dds"),
    ("image", "pcx", "pcx"),
    ("image", "raw", "dng cr2 cr3 crw nef nrw arw srf sr2 raf orf rw2 raw "
                     "pef ptx dcr kdc k25 mrw x3f 3fr iiq mos erf srw gpr "
                     "rwl fff mef"),
    # -- video -----------------------------------------------------------
    ("video", "mp4", "mp4 m4v"),
    ("video", "quicktime", "mov qt"),
    ("video", "matroska", "mkv mk3d"),
    ("video", "webm", "webm"),
    ("video", "avi", "avi"),
    ("video", "windows-media", "wmv asf"),
    ("video", "flash-video", "flv f4v f4p"),
    ("video", "mpeg", "mpg mpeg mpe m1v m2v mp2 mpv"),
    ("video", "mpeg-ts", "m2ts mts ts2 tp trp m2t"),
    ("video", "program-stream", "vob evo m2p"),
    ("video", "ogg-video", "ogv ogm"),
    ("video", "3gpp", "3gp 3g2 3gpp"),
    ("video", "realvideo", "rm rmvb"),
    ("video", "divx", "divx"),
    ("video", "mxf", "mxf"),
    ("video", "dv", "dv dif"),
    ("video", "redcode", "r3d"),
    ("video", "braw", "braw"),
    ("video", "yuv4mpeg", "y4m"),
    ("video", "project", "prproj veg vpj imovieproj fcpxml drp kdenlive "
                         "mswmm wlmp camproj cmproj"),
    # -- audio -----------------------------------------------------------
    ("audio", "mp3", "mp3 mp2 mpga"),
    ("audio", "aac", "aac adts"),
    ("audio", "mp4-audio", "m4a m4b m4r m4p"),
    ("audio", "flac", "flac"),
    ("audio", "wav", "wav wave"),
    ("audio", "aiff", "aif aiff aifc"),
    ("audio", "ogg", "ogg oga"),
    ("audio", "opus", "opus"),
    ("audio", "windows-media-audio", "wma"),
    ("audio", "alac", "alac"),
    ("audio", "monkeys-audio", "ape"),
    ("audio", "wavpack", "wv"),
    ("audio", "tta", "tta"),
    ("audio", "matroska-audio", "mka"),
    ("audio", "ac3", "ac3 eac3"),
    ("audio", "dts", "dts dtshd"),
    ("audio", "amr", "amr awb"),
    ("audio", "sun-audio", "au snd"),
    ("audio", "midi", "mid midi rmi kar"),
    ("audio", "core-audio", "caf"),
    ("audio", "dsd", "dsf dff"),
    ("audio", "speex", "spx"),
    ("audio", "realaudio", "ra"),
    ("audio", "tracker", "xm it s3m mtm 669 stm far okt"),
    ("audio", "playlist", "m3u m3u8 pls cue xspf wpl asx"),
    ("audio", "project", "als flp logicx band ptx ptf rpp cwp song cpr npr "
                         "sesx aup aup3 reason ableton omf"),
    # -- documents -------------------------------------------------------
    ("document", "pdf", "pdf"),
    ("document", "postscript", "ps eps"),
    ("document", "word", "doc docx docm dot dotx dotm"),
    ("document", "opendocument-text", "odt ott fodt"),
    ("document", "rtf", "rtf"),
    ("document", "plain-text", "txt text log nfo readme diz asc"),
    ("document", "markdown", "md markdown mdown mkd mdx"),
    ("document", "restructuredtext", "rst"),
    ("document", "latex", "tex bib cls sty"),
    ("document", "wordperfect", "wpd wp wp5 wp6"),
    ("document", "pages", "pages"),
    ("document", "epub", "epub"),
    ("document", "mobi", "mobi azw azw3 azw4 prc"),
    ("document", "fictionbook", "fb2"),
    ("document", "djvu", "djvu djv"),
    ("document", "chm", "chm"),
    ("document", "xps", "xps oxps"),
    ("document", "comic-archive", "cbz cbr cb7 cbt"),
    ("document", "excel", "xls xlsx xlsm xlsb xlt xltx"),
    ("document", "opendocument-sheet", "ods ots fods"),
    ("document", "numbers", "numbers"),
    ("document", "delimited", "csv tsv"),
    ("document", "powerpoint", "ppt pptx pptm pps ppsx pot potx"),
    ("document", "opendocument-slides", "odp otp fodp"),
    ("document", "keynote", "keynote"),
    ("document", "visio", "vsd vsdx"),
    ("document", "onenote", "one onetoc2"),
    ("document", "note", "enex rtfd webarchive"),
    ("document", "calendar", "ics ical ifb vcs"),
    ("document", "contact", "vcf vcard abbu"),
    ("document", "email", "eml emlx msg mbox mbx pst ost"),
    # -- subtitles and captions -------------------------------------------
    ("subtitle", "subrip", "srt"),
    ("subtitle", "webvtt", "vtt webvtt"),
    ("subtitle", "advanced-substation", "ass ssa"),
    ("subtitle", "vobsub", "idx"),
    ("subtitle", "pgs", "sup"),
    ("subtitle", "sami", "smi sami"),
    ("subtitle", "subviewer", "sbv"),
    ("subtitle", "timed-text", "ttml dfxp xml-tt itt"),
    ("subtitle", "eia-608", "scc"),
    ("subtitle", "spruce", "stl"),
    ("subtitle", "lyrics", "lrc"),
    # -- archives ---------------------------------------------------------
    ("archive", "zip", "zip zipx"),
    ("archive", "rar", "rar r00 r01"),
    ("archive", "7z", "7z"),
    ("archive", "tar", "tar"),
    ("archive", "gzip", "gz tgz taz"),
    ("archive", "bzip2", "bz2 tbz tbz2"),
    ("archive", "xz", "xz txz"),
    ("archive", "zstd", "zst tzst"),
    ("archive", "lzma", "lzma lz lz4 lzo"),
    ("archive", "cab", "cab"),
    ("archive", "arj", "arj"),
    ("archive", "lha", "lha lzh"),
    ("archive", "ace", "ace"),
    ("archive", "stuffit", "sit sitx hqx"),
    ("archive", "cpio", "cpio"),
    ("archive", "compress", "z taz"),
    ("archive", "wim", "wim swm esd"),
    # -- 3D and CAD --------------------------------------------------------
    ("model3d", "wavefront", "obj mtl"),
    ("model3d", "fbx", "fbx"),
    ("model3d", "collada", "dae"),
    ("model3d", "3ds", "3ds max"),
    ("model3d", "blender", "blend blend1 blend2"),
    ("model3d", "stl", "stl"),
    ("model3d", "ply", "ply"),
    ("model3d", "gltf", "gltf glb"),
    ("model3d", "usd", "usd usda usdc usdz"),
    ("model3d", "alembic", "abc"),
    ("model3d", "step", "step stp"),
    ("model3d", "iges", "iges igs"),
    ("model3d", "solidworks", "sldprt sldasm slddrw"),
    ("model3d", "fusion", "f3d f3z"),
    ("model3d", "sketchup", "skp"),
    ("model3d", "cinema4d", "c4d"),
    ("model3d", "maya", "ma mb"),
    ("model3d", "lightwave", "lwo lws"),
    ("model3d", "zbrush", "ztl zpr"),
    ("model3d", "3mf", "3mf"),
    ("model3d", "amf", "amf"),
    ("model3d", "x3d", "x3d wrl vrml"),
    ("model3d", "voxel", "vox qb"),
    ("model3d", "autocad", "dwg dxf"),
    ("model3d", "gcode", "gcode gco g bgcode"),
    ("model3d", "houdini", "hip hipnc"),
    ("model3d", "unity", "unitypackage prefab"),
    # -- fonts --------------------------------------------------------------
    ("font", "truetype", "ttf ttc"),
    ("font", "opentype", "otf otc"),
    ("font", "woff", "woff woff2"),
    ("font", "embedded-opentype", "eot"),
    ("font", "type1", "pfb pfm afm"),
    ("font", "bitmap-font", "bdf pcf fon fnt"),
    # -- source, markup and configuration -----------------------------------
    ("code", "python", "py pyw pyi pyx"),
    ("code", "javascript", "js mjs cjs jsx"),
    ("code", "typescript", "tsx mts cts"),
    ("code", "c", "c h"),
    ("code", "cpp", "cpp cc cxx hpp hh hxx c++"),
    ("code", "csharp", "cs csx"),
    ("code", "java", "java jar class"),
    ("code", "kotlin", "kt kts"),
    ("code", "swift", "swift"),
    ("code", "objective-c", "mm"),
    ("code", "ruby", "rb erb gemspec"),
    ("code", "php", "php php3 php4 phtml"),
    ("code", "go", "go"),
    ("code", "rust", "rs"),
    ("code", "shell", "sh bash zsh fish ksh csh"),
    ("code", "powershell", "ps1 psm1 psd1"),
    ("code", "perl", "pl pm t"),
    ("code", "lua", "lua"),
    ("code", "r", "r rmd"),
    ("code", "sql", "sql"),
    ("code", "html", "html htm xhtml"),
    ("code", "css", "css scss sass less styl"),
    ("code", "xml", "xml xsl xslt xsd dtd rss atom"),
    ("code", "json", "json json5 jsonl ndjson geojson"),
    ("code", "yaml", "yaml yml"),
    ("code", "toml", "toml"),
    ("code", "ini", "ini cfg conf properties"),
    ("code", "notebook", "ipynb"),
    ("code", "patch", "patch diff"),
    ("code", "vue", "vue svelte astro"),
    ("code", "build", "gradle cmake mk am ac gyp bzl"),
    ("code", "dockerfile", "dockerfile containerfile"),
    ("code", "web-assembly", "wasm wat"),
    # -- applications and packages -------------------------------------------
    ("app", "windows-executable", "exe scr com"),
    ("app", "windows-installer", "msi msix msixbundle appx appxbundle"),
    ("app", "macos-app", "app"),
    ("app", "macos-installer", "pkg mpkg"),
    ("app", "android", "apk aab xapk apks"),
    ("app", "ios", "ipa"),
    ("app", "debian", "deb udeb"),
    ("app", "rpm", "rpm"),
    ("app", "appimage", "appimage"),
    ("app", "snap", "snap"),
    ("app", "flatpak", "flatpak flatpakref"),
    ("app", "script-executable", "bat cmd run"),
    ("app", "browser-extension", "crx xpi"),
    # -- disk images ----------------------------------------------------------
    ("disk-image", "iso", "iso"),
    ("disk-image", "dmg", "dmg sparseimage sparsebundle cdr toast"),
    ("disk-image", "raw-image", "img ima"),
    ("disk-image", "cue-bin", "cue nrg mdf mds ccd"),
    ("disk-image", "vhd", "vhd vhdx"),
    ("disk-image", "vmdk", "vmdk"),
    ("disk-image", "qcow", "qcow qcow2 qed"),
    ("disk-image", "virtualbox", "vdi"),
    # -- opaque data -----------------------------------------------------------
    ("data", "sqlite", "sqlite sqlite3 db db3 s3db"),
    ("data", "property-list", "plist"),
    ("data", "registry", "reg"),
    ("data", "backup", "bak bkp old orig"),
    ("data", "partial", "part crdownload download opdownload partial "
                        "!ut !qb aria2 tmp temp"),
    ("data", "pickle", "pkl pickle"),
    ("data", "numpy", "npy npz"),
    ("data", "columnar", "parquet feather orc avro arrow"),
    ("data", "hdf", "hdf5 h5 nc"),
    ("data", "statistics", "sav dta rdata rds mat"),
    ("data", "torrent", "torrent"),
    ("data", "key-material", "pem crt cer der p12 pfx jks keystore pub "
                             "gpg asc-key"),
    ("data", "shortcut", "lnk url webloc desktop alias"),
    ("data", "binary", "bin dat raw-data"),
)

# Extensions that two kinds both use, where the name settles nothing. Left to
# the signature reader, and worth their own list because each one is a real
# folder somebody has ruined.
AMBIGUOUS = {
    "ts":   (("code", "typescript"), ("video", "mpeg-ts")),
    "key":  (("document", "keynote"), ("data", "key-material")),
    "mod":  (("audio", "tracker"), ("video", "mpeg")),   # JVC camcorders
    "sub":  (("subtitle", "microdvd"), ("disk-image", "raw-image")),
    "img":  (("disk-image", "raw-image"), ("image", "raw")),
    "bin":  (("data", "binary"), ("disk-image", "cue-bin")),
    "dat":  (("data", "binary"), ("video", "vcd")),
    "m":    (("code", "objective-c"), ("code", "matlab")),
    "asc":  (("document", "plain-text"), ("data", "key-material")),
    "r":    (("code", "r"), ("archive", "rar")),
    "z":    (("archive", "compress"), ("data", "binary")),
    "raw":  (("image", "raw"), ("data", "binary")),
    "g":    (("model3d", "gcode"), ("data", "binary")),
    "max":  (("model3d", "3ds"), ("data", "binary")),
    "t":    (("code", "perl"), ("document", "plain-text")),
    "snd":  (("audio", "sun-audio"), ("data", "binary")),
    "one":  (("document", "onenote"), ("data", "binary")),
    "prc":  (("document", "mobi"), ("data", "binary")),
    "class": (("code", "java"), ("data", "binary")),
}

# Extensions that are always a member of a set and never the thing being
# sorted. `bundles` uses this; it is here because it is a property of the
# format, not of the grouping strategy.
ALWAYS_SIDECAR = {
    "mtl", "aae", "thm", "xmp", "nfo", "sfv", "md5", "sha1", "sha256",
    "torrent-part", "idx", "cue", "m3u", "log", "pls", "sbv", "lrc",
    "bif", "srt", "sub", "ass", "ssa", "vtt", "sup", "smi", "info",
}

EXTENSIONS = {}
FORMATS = {}
for _kind, _format, _spellings in _TABLE:
    FORMATS.setdefault(_format, _kind)
    for _ext in _spellings.split():
        # First declaration wins, so the table reads top to bottom as a
        # priority order and a later duplicate is a typo rather than a
        # silent override.
        EXTENSIONS.setdefault(_ext, (_kind, _format))
del _kind, _format, _spellings, _ext


def classify_extension(ext):
    """(kind, format) for an extension, or None.

    `ext` comes in without its dot and in any case. An extension in AMBIGUOUS
    returns None deliberately: the caller must ask a reader, and a caller that
    treats None as "unknown file" rather than "unknown yet" is a bug worth
    seeing early.
    """
    if not ext:
        return None
    ext = ext.lower().lstrip(".")
    if ext in AMBIGUOUS:
        return None
    return EXTENSIONS.get(ext)


def candidates(ext):
    """Every kind an ambiguous extension could be, for `explain` to list."""
    if not ext:
        return ()
    return AMBIGUOUS.get(ext.lower().lstrip("."), ())


def kind_of_format(fmt):
    return FORMATS.get(fmt)


def is_known(ext):
    ext = (ext or "").lower().lstrip(".")
    return ext in EXTENSIONS or ext in AMBIGUOUS
