"""What a filename admits, and how much of it to believe.

People name files carelessly and software names them systematically, and the
systematic half is a gift. `IMG_4021.HEIC` is a photo off an iPhone.
`Show.Name.S03E07.1080p.WEB-DL-GROUP` carries a series, a season, an episode,
a resolution and a release group. `Artist - Title.mp3` carries most of an ID3
tag. `Invoice_2026-0114.pdf` says what a human would have to open the file to
find out.

Every fact in this module is **weak**, and that is not a defect. A filename is
a claim by whoever last renamed the file, and the folders this tool exists for
are full of files renamed by people who were not thinking about it. What makes
these facts useful is that they *corroborate*: an artist parsed out of a
filename is a guess, and an artist parsed out of a filename that matches the
ID3 tag is close to certain. The evidence model exists to make that
distinction, so the job here is to guess honestly and let it be checked.

The detectors all run. They are not alternatives — a file can be a WhatsApp
export and a screenshot and carry a date, and each of those is a separate
fact. Only `looks_like` is single-valued, and it is resolved by the order of
`_DETECTORS`, most specific first.
"""

from __future__ import annotations

import re

import sites
from evidence import CERTAIN, STRONG, LIKELY, WEAK


class Context(object):
    """One filename, in the three spellings the detectors need.

    Underscores are the reason this exists. `\b` does not fire between `p`
    and `_`, so `setup_x64` does not match `\bsetup\b` and half the keyword
    detectors silently found nothing. Every detector that matches *words* gets
    `plain`, where underscores have become spaces. Every detector that matches
    a *format* — `IMG_4021`, `.en.forced.srt`, `._IMG_0001` — gets the name
    exactly as it was written, because there the underscore is the syntax.

    `flat` additionally turns dots into spaces, but only for names written in
    the dotted scene style, so that `Some.Show.S03E07` tokenises while
    `Blender-4.2.1` keeps its version number intact.
    """

    __slots__ = ("name", "stem", "ext", "lower", "plain", "flat", "kind")

    def __init__(self, filename, kind=None):
        stem, _, ext = filename.rpartition(".")
        if not stem:
            stem, ext = filename, ""
        self.name = filename
        self.stem = stem
        self.ext = ext.lower()
        self.lower = filename.lower()
        self.plain = stem.replace("_", " ")
        dotted = stem.count(".") >= 2 and " " not in stem
        self.flat = self.plain.replace(".", " ") if dotted else self.plain
        self.kind = kind


class Found(object):
    """The facts one filename yielded, and which detector yielded each."""

    def __init__(self):
        self.facts = []          # (name, value, confidence, detector)
        self.labels = []         # looks_like candidates, best first

    def add(self, detector, name, value, confidence):
        if value is None or value == "":
            return
        self.facts.append((name, value, confidence, "name:" + detector))

    def label(self, detector, label, confidence=LIKELY):
        self.labels.append((label, confidence, "name:" + detector))

    def __len__(self):
        return len(self.facts)


# ---------------------------------------------------------------------------
# Dates, which turn up inside half the other detectors
# ---------------------------------------------------------------------------

_MONTHS = ("jan feb mar apr may jun jul aug sep oct nov dec".split())
_MONTH_NAMES = {}
for _i, _m in enumerate(_MONTHS, 1):
    _MONTH_NAMES[_m] = _i
for _i, _m in enumerate(("january february march april may june july august "
                         "september october november december").split(), 1):
    _MONTH_NAMES[_m] = _i
del _i, _m

_DATE_PATTERNS = (
    # ISO and near-ISO. Unambiguous, so trusted furthest.
    (re.compile(r"(?<!\d)(20\d{2}|19\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?"
                r"(0[1-9]|[12]\d|3[01])(?!\d)"), "ymd", LIKELY),
    # Day-first with a day that cannot be a month.
    (re.compile(r"(?<!\d)(1[3-9]|2\d|3[01])[-_.](0[1-9]|1[0-2])[-_.]"
                r"(20\d{2}|19\d{2})(?!\d)"), "dmy", LIKELY),
    # Month-first with a day that cannot be a month.
    (re.compile(r"(?<!\d)(0[1-9]|1[0-2])[-_.](1[3-9]|2\d|3[01])[-_.]"
                r"(20\d{2}|19\d{2})(?!\d)"), "mdy", LIKELY),
    # Written months: "Sep 19, 2026", "19 September 2026".
    (re.compile(r"(?<![a-z])([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(20\d{2}|19\d{2})",
                re.I), "Mdy", LIKELY),
    (re.compile(r"(?<!\d)(\d{1,2})\s+([a-z]{3,9})\.?\s+(20\d{2}|19\d{2})",
                re.I), "dMy", LIKELY),
)

_TIME_PATTERN = re.compile(
    r"(?<!\d)([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d)?"
    r"(?:[-_.]?(\d{3}))?(?!\d)\s*(am|pm)?", re.I)


def _date_from(text):
    """(iso date string, matched text) or (None, None).

    Ambiguous day/month orderings are refused rather than guessed. A photo
    filed under the wrong month is a small error; a tool that silently
    reorders dates is one nobody can check.
    """
    for pattern, order, _confidence in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        a, b, c = match.group(1), match.group(2), match.group(3)
        try:
            if order == "ymd":
                year, month, day = int(a), int(b), int(c)
            elif order == "dmy":
                day, month, year = int(a), int(b), int(c)
            elif order == "mdy":
                month, day, year = int(a), int(b), int(c)
            elif order == "Mdy":
                month = _MONTH_NAMES.get(a.lower())
                if month is None:
                    continue
                day, year = int(b), int(c)
            else:
                day = int(a)
                month = _MONTH_NAMES.get(b.lower())
                if month is None:
                    continue
                year = int(c)
        except (TypeError, ValueError):
            continue
        if not (1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
            continue
        return "%04d-%02d-%02d" % (year, month, day), match.group(0)
    return None, None


def _time_from(text, after=""):
    match = _TIME_PATTERN.search(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3) or 0)
    meridiem = (match.group(5) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return "%02d:%02d:%02d" % (hour, minute, second)


def detect_dates(ctx, out):
    stem, ext, lower = ctx.plain, ctx.ext, ctx.lower
    """A date anywhere in the name, and a time if one follows it."""
    iso, matched = _date_from(stem)
    if iso:
        out.add("date", "name_date", iso, LIKELY)
        remainder = stem.split(matched, 1)[-1] if matched else ""
        # Only a time directly after the date counts. Left looser, the
        # sequence number in `IMG-20260919-WA0001` reads as 00:01.
        # "at", "um", "kl" and friends sit between the date and the time in
        # every locale's screenshot name, so allow one short connector word
        # and nothing else.
        if re.match(r"^[ \-_.tT]*(?:at|um|kl|om|the|a|à)?[ \-_.]*\d",
                    remainder, re.I):
            clock = _time_from(remainder[:24])
        else:
            clock = None
        if clock:
            out.add("date", "name_time", clock, LIKELY)
        return
    # A bare epoch timestamp, which browsers and messaging apps both produce.
    for match in re.finditer(r"(?<!\d)(1[0-9]{9})(\d{3})?(?!\d)", stem):
        seconds = int(match.group(1))
        if 1000000000 < seconds < 2000000000:
            import datetime
            stamp = datetime.datetime.fromtimestamp(
                seconds, datetime.timezone.utc)
            out.add("date", "name_date", stamp.strftime("%Y-%m-%d"), WEAK)
            out.add("date", "name_time", stamp.strftime("%H:%M:%S"), WEAK)
            return


# ---------------------------------------------------------------------------
# Screenshots and screen recordings
# ---------------------------------------------------------------------------

_SHOT_WORDS = (
    r"screenshot|screen[ _-]?shot|screen[ _-]?capture|scrn|scr|"
    r"bildschirmfoto|bildschirmaufnahme|capture[ _]?d.?.?cran|"
    r"captura[ _]de[ _]pantalla|schermafbeelding|sk.rmavbild|"
    r"sk.rmbillede|skjermbilde|kuvakaappaus|zrzut[ _]ekranu|"
    r"снимок[ _]экрана|スクリーンショット|螢幕截圖|屏幕截图|cleanshot|shottr"
)
_SCREENSHOT = re.compile(r"^\s*(?:%s)(?![a-z])" % _SHOT_WORDS,
                         re.I | re.U)
_RECORDING = re.compile(
    r"^\s*(screen[ _-]?recording|bildschirmaufnahme|screencast|"
    r"screen[ _-]?record)(?![a-z])", re.I)
_ANDROID_SHOT = re.compile(
    r"^screenshot_\d{8}[-_]\d{6}(?:\d{3})?(?:_(?P<package>[a-z0-9_]+\."
    r"[a-z0-9_.]+))?$", re.I)


def detect_screenshot(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    if _RECORDING.match(stem):
        out.label("screenshot", "screen-recording", STRONG)
        out.add("screenshot", "capture", "screen-recording", STRONG)
        return
    if not _SCREENSHOT.match(stem):
        return
    out.label("screenshot", "screenshot", STRONG)
    out.add("screenshot", "capture", "screenshot", STRONG)
    android = _ANDROID_SHOT.match(stem)
    if android and android.group("package"):
        package = android.group("package")
        out.add("screenshot", "from_package", package, STRONG)
        out.add("screenshot", "from_app", _app_for_package(package), LIKELY)


_PACKAGES = {
    "com.whatsapp": "WhatsApp", "org.telegram": "Telegram",
    "com.instagram": "Instagram", "com.twitter": "Twitter",
    "com.google.android.youtube": "YouTube", "com.spotify": "Spotify",
    "com.android.chrome": "Chrome", "com.facebook": "Facebook",
    "com.discord": "Discord", "com.reddit": "Reddit", "com.slack": "Slack",
    "com.snapchat": "Snapchat", "com.zhiliaoapp.musically": "TikTok",
    "com.google.android.apps.maps": "Maps", "com.amazon": "Amazon",
}


def _app_for_package(package):
    for prefix, label in _PACKAGES.items():
        if package.startswith(prefix):
            return label
    parts = package.split(".")
    return parts[-1].capitalize() if parts else None


# ---------------------------------------------------------------------------
# Cameras and phones
# ---------------------------------------------------------------------------

_CAMERA_SERIES = (
    # pattern, the make it implies (or None), whether it is video
    (re.compile(r"^IMG_E?(\d{4,5})$", re.I), "Apple or generic", False),
    (re.compile(r"^IMG[-_](\d{8})[-_](\d{6})"), None, False),
    (re.compile(r"^_MG_(\d{4})$", re.I), "Canon", False),
    (re.compile(r"^_DSC(\d{4})$", re.I), "Sony or Nikon", False),
    (re.compile(r"^DSC[-_]?(\d{4,5})$", re.I), "Sony or Nikon", False),
    (re.compile(r"^DSCN(\d{4})$", re.I), "Nikon", False),
    (re.compile(r"^DSCF(\d{4})$", re.I), "Fujifilm", False),
    (re.compile(r"^P(\d{7})$"), "Panasonic or Olympus", False),
    (re.compile(r"^PXL_(\d{8})_(\d{9})", re.I), "Google Pixel", False),
    (re.compile(r"^DJI_(\d{4})$", re.I), "DJI", False),
    (re.compile(r"^DJI_(\d{14})_(\d{4})", re.I), "DJI", False),
    (re.compile(r"^GOPR(\d{4})$", re.I), "GoPro", False),
    (re.compile(r"^G[PHXL](\d{2})(\d{4})$", re.I), "GoPro", True),
    (re.compile(r"^MVI_(\d{4})$", re.I), "Canon", True),
    (re.compile(r"^MOV_?(\d{4})$", re.I), None, True),
    (re.compile(r"^VID[-_](\d{8})[-_](\d{6})"), None, True),
    (re.compile(r"^MAH(\d{5})$", re.I), "Panasonic AVCHD", True),
    (re.compile(r"^R(\d{7})$"), "Ricoh", False),
    (re.compile(r"^L(\d{7})$"), "Leica", False),
    (re.compile(r"^(\d{3})_(\d{4})$"), None, False),
    (re.compile(r"^PANO[-_]?(\d+)", re.I), None, False),
    (re.compile(r"^BURST(\d+)", re.I), None, False),
    (re.compile(r"^SVID[-_](\d{8})", re.I), None, True),
)


def detect_camera(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    for pattern, make, is_video in _CAMERA_SERIES:
        match = pattern.match(stem)
        if not match:
            continue
        out.label("camera", "camera", LIKELY)
        out.add("camera", "camera_series", pattern.pattern, WEAK)
        if make:
            out.add("camera", "camera_hint", make, WEAK)
        if is_video:
            out.add("camera", "capture", "camera-video", LIKELY)
        else:
            out.add("camera", "capture", "camera-still", LIKELY)
        if re.match(r"^IMG_E\d", stem, re.I):
            out.add("camera", "edited", True, LIKELY)
        return


# ---------------------------------------------------------------------------
# Messaging and social exports
# ---------------------------------------------------------------------------

_MESSAGING = (
    (re.compile(r"^(IMG|VID|AUD|PTT|DOC|STK)-(\d{8})-WA(\d{4})", re.I),
     "WhatsApp"),
    (re.compile(r"^WhatsApp (Image|Video|Audio|Document|Animated Gif)", re.I),
     "WhatsApp"),
    (re.compile(r"^Signal[-_ ]", re.I), "Signal"),
    (re.compile(r"^(photo|video|audio|file|voice)_\d{4}-\d{2}-\d{2}_", re.I),
     "Telegram"),
    (re.compile(r"^FB_IMG_\d+", re.I), "Facebook"),
    (re.compile(r"^received_\d{10,}", re.I), "Facebook Messenger"),
    (re.compile(r"^Snapchat-\d+", re.I), "Snapchat"),
    (re.compile(r"^(instagram|insta)[-_]", re.I), "Instagram"),
    (re.compile(r"^InShot_\d+", re.I), "InShot"),
    (re.compile(r"^(tiktok|douyin)[-_]", re.I), "TikTok"),
    (re.compile(r"^Viber[-_ ]image", re.I), "Viber"),
    (re.compile(r"^SPIcon|^IMG_\d{4}_iOS", re.I), None),
)


def detect_messaging(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    for pattern, app in _MESSAGING:
        if not pattern.match(stem):
            continue
        out.label("messaging", "phone-export", STRONG)
        if app:
            out.add("messaging", "from_app", app, STRONG)
            out.add("messaging", "origin", "message", LIKELY)
        return


# ---------------------------------------------------------------------------
# Scene releases: films, series and anime
# ---------------------------------------------------------------------------

_VOCAB = {
    "resolution": ("2160p 1080p 1080i 720p 576p 576i 480p 480i 4320p 4k uhd "
                   "fhd hd sd").split(),
    "source": ("bluray blu-ray bdrip brrip bdremux remux webrip web-dl webdl "
               "web hdtv pdtv dsr dvdrip dvd dvdscr dvdr hdrip camrip cam "
               "telesync telecine ts tc r5 vhsrip vodrip hdcam amzn nf dsnp "
               "hmax atvp hulu pcok stan crav uhdbd").split(),
    "codec": ("x264 x265 h264 h265 h.264 h.265 hevc avc av1 xvid divx vp9 "
              "mpeg2 10bit 8bit 10-bit hdr hdr10 hdr10+ dovi dv sdr hi10p "
              "avc1").split(),
    "audio": ("aac ac3 eac3 ddp dd dd+ dts dts-hd dtshd truehd atmos flac "
              "opus mp3 lpcm pcm aac2 ddp5 dd5 5.1 7.1 2.0 6ch 2ch").split(),
    "edition": ("extended unrated uncut directors director's theatrical "
                "remastered restored imax criterion anniversary special "
                "ultimate final collectors").split(),
    "flag": ("proper repack rerip internal limited complete multi dual "
             "subbed dubbed hardsub softsub subs nosub retail readnfo "
             "rartv rarbg sample").split(),
    "language": ("english french german spanish italian japanese korean "
                 "chinese russian portuguese dutch polish swedish danish "
                 "norwegian finnish hindi arabic turkish czech hungarian "
                 "eng fre fra ger deu spa ita jpn jap kor chi rus por dut "
                 "pol swe dan nor fin hin ara tur cze hun vostfr multi "
                 "truefrench vf vo vost").split(),
}
_VOCAB_LOOKUP = {}
for _group, _words in _VOCAB.items():
    for _word in _words:
        _VOCAB_LOOKUP.setdefault(_word, _group)
del _group, _words, _word

_EPISODE_PATTERNS = (
    re.compile(r"(?<![a-z0-9])s(?P<season>\d{1,2})[ ._-]?e(?P<episode>\d{1,3})"
               r"(?:[ ._-]?e?(?P<episode_end>\d{1,3}))?(?![0-9])", re.I),
    re.compile(r"(?<![a-z0-9])(?P<season>\d{1,2})x(?P<episode>\d{1,3})"
               r"(?![0-9])", re.I),
    re.compile(r"(?<![a-z0-9])season[ ._-]?(?P<season>\d{1,2})[ ._-]+"
               r"episode[ ._-]?(?P<episode>\d{1,3})", re.I),
    re.compile(r"(?<![a-z0-9])s(?P<season>\d{1,2})(?![0-9ep])", re.I),
)
_YEAR = re.compile(r"(?<!\d)(19[3-9]\d|20[0-4]\d)(?!\d)")
_GROUP_TRAILER = re.compile(r"[-‑]\s*([A-Za-z0-9_.]{2,20})$")
_ANIME_GROUP = re.compile(r"^\[([^\]]{2,30})\]\s*")
_ANIME_EPISODE = re.compile(r"\s-\s(\d{1,3})(?:v\d)?(?:\s|$|\[)")
_CHECKSUM = re.compile(r"\[([0-9A-Fa-f]{8})\]")


def _words(flat):
    """Tokens with their character offsets, so cuts stay in one coordinate."""
    return [(match.group(0), match.start())
            for match in re.finditer(r"\S+", flat)]


def detect_release(ctx, out):
    stem, ext, lower = ctx.flat, ctx.ext, ctx.lower
    """A film or episode name in the form release groups have used for decades.

    The rule for the title is that it ends at the first token that belongs to
    the vocabulary, the first season marker, or the year — whichever comes
    first. That is crude, and it is also what every parser in this space does,
    because the format has no delimiter between title and metadata beyond the
    fact that the metadata is drawn from a closed set of words.
    """
    words = _words(stem)
    if len(words) < 2:
        return

    anime_group = _ANIME_GROUP.match(stem)
    hits = {}
    first_vocab = None
    for token, offset in words:
        group = _VOCAB_LOOKUP.get(token.lower().strip("[](){}"))
        if group:
            hits.setdefault(group, token.lower().strip("[](){}"))
            if first_vocab is None and offset > 0:
                first_vocab = offset

    season = episode = None
    episode_at = None
    for pattern in _EPISODE_PATTERNS:
        match = pattern.search(stem)
        if match:
            groups = match.groupdict()
            season = int(groups.get("season") or 0) or None
            episode = (int(groups["episode"])
                       if groups.get("episode") else None)
            episode_at = match.start()
            break

    if episode is None and anime_group:
        # Fansub naming has no SxxExx: `[Group] Title - 07 [1080p]`. The bare
        # number after a dash is the episode, and the group in brackets is
        # what makes that reading safe rather than a guess about a hyphen.
        anime = _ANIME_EPISODE.search(stem)
        if anime:
            episode = int(anime.group(1))
            season = None
            episode_at = anime.start()

    year_match = _YEAR.search(stem)
    year_at = year_match.start() if year_match else None

    # Not enough evidence to call this a release at all. Two vocabulary hits,
    # or an episode marker, or a year with one hit.
    strength = len(hits) + (2 if episode is not None else 0)
    if year_match and hits:
        strength += 1
    if strength < 2:
        return

    cut = min(x for x in (first_vocab, episode_at, year_at,
                          len(stem)) if x is not None and x > 0)
    head = stem[:cut]
    if anime_group:
        out.add("release", "release_group", anime_group.group(1), LIKELY)
        head = head[anime_group.end():]

    title = re.sub(r"\s{2,}", " ", head).strip(" -._[]()")
    if title:
        out.add("release", "title", title, LIKELY)

    if episode is not None:
        if season is not None:
            out.add("release", "season", season, STRONG)
        out.add("release", "episode", episode, STRONG)
        out.label("release", "episode", STRONG)
    elif season is not None:
        out.add("release", "season", season, LIKELY)
        out.label("release", "season", LIKELY)
    else:
        out.label("release", "scene-release", LIKELY)

    if year_match:
        out.add("release", "release_year", int(year_match.group(1)), LIKELY)
    for group, token in hits.items():
        out.add("release", group, token, LIKELY)

    checksum = _CHECKSUM.search(stem)
    if checksum:
        out.add("release", "checksum", checksum.group(1).upper(), STRONG)
    if not anime_group:
        trailer = _GROUP_TRAILER.search(stem)
        if trailer and trailer.group(1).lower() not in _VOCAB_LOOKUP:
            out.add("release", "release_group", trailer.group(1), LIKELY)
    if lower.startswith("sample") or ".sample." in lower:
        out.label("release", "release-sample", STRONG)


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------

_GARNISH = re.compile(
    r"[\[(]\s*(official\s*(music\s*)?(video|audio|visualiser|visualizer)|"
    r"lyrics?(\s*video)?|audio|hd|hq|4k|full\s*album|free\s*download|"
    r"explicit|clean|remaster(ed)?(\s*\d{4})?|\d{3,4}\s*kbps|"
    r"with\s*lyrics|music\s*video|mv|live|extended\s*mix)\s*[\])]", re.I)
_SITE_PREFIX = re.compile(r"^\s*(www\.[^\s_-]+|\[[^\]]*\.(com|net|org)\])"
                          r"\s*[-_ ]+", re.I)
_YOUTUBE_ID = re.compile(r"\[([A-Za-z0-9_-]{11})\]\s*$")
_FEATURING = re.compile(r"\s*[\[(]?\s*(?:feat\.?|ft\.?|featuring|with)\s+"
                        r"([^)\]]+)[\])]?\s*$", re.I)
# `01. Track`, `01 - Track` and plain `01 Track` are all in circulation.
# A bare space counts, which is why the value ceiling below matters: it
# is what stops `2001 A Space Odyssey` becoming track 2001.
_TRACK_PREFIX = re.compile(r"^\s*(\d{1,3})\s*(?:[-._)]\s*|\s)(?=\S)")
_DISC_TRACK = re.compile(r"^\s*(\d{1,2})[-.](\d{2})\s*[-._ ]\s*(?=\S)")
_VINYL_PREFIX = re.compile(r"^\s*([A-F])(\d{1,2})\s*[-._ ]+(?=\S)")


def detect_music(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    """`Artist - Title`, and the dozen decorations people put around it.

    The artist/title *order* is genuinely unknowable from a filename — plenty
    of files are named `Title - Artist` — so both land at `WEAK` and wait to
    be corroborated by a tag. What is worth more is everything stripped on the
    way: a track number, a disc number, a featured artist and a YouTube id are
    all unambiguous when they are there.
    """
    stripped_track = False
    working = _SITE_PREFIX.sub("", stem)
    working = _GARNISH.sub(" ", working)

    identifier = _YOUTUBE_ID.search(working)
    if identifier and not identifier.group(1).isdigit():
        out.add("music", "source_id", identifier.group(1), WEAK)
        working = working[:identifier.start()]

    disc = _DISC_TRACK.match(working)
    if disc:
        out.add("music", "disc", int(disc.group(1)), LIKELY)
        out.add("music", "track", int(disc.group(2)), LIKELY)
        working = working[disc.end():]
    else:
        vinyl = _VINYL_PREFIX.match(working)
        if vinyl:
            out.add("music", "vinyl_side", vinyl.group(1).upper(), LIKELY)
            out.add("music", "track", int(vinyl.group(2)), LIKELY)
            working = working[vinyl.end():]
        else:
            numbered = _TRACK_PREFIX.match(working)
            if numbered and int(numbered.group(1)) <= 200:
                out.add("music", "track", int(numbered.group(1)), LIKELY)
                working = working[numbered.end():]
                stripped_track = True

    working = working.replace("_-_", " - ").strip(" -_.")
    if not working:
        return

    featured = _FEATURING.search(working)
    if featured:
        out.add("music", "featuring", featured.group(1).strip(" .-"), LIKELY)
        working = working[:featured.start()].strip()

    parts = [part.strip() for part in re.split(r"\s+[-–—]\s+", working)
             if part.strip()]
    if len(parts) == 2:
        out.add("music", "artist", parts[0], WEAK)
        out.add("music", "song_title", parts[1], WEAK)
        out.label("music", "artist-title", WEAK)
    elif len(parts) == 3:
        # Artist - Album - Title is the common three-part shape; a middle part
        # that is a number means Artist - NN - Title instead.
        if parts[1].isdigit():
            out.add("music", "artist", parts[0], WEAK)
            out.add("music", "track", int(parts[1]), LIKELY)
            out.add("music", "song_title", parts[2], WEAK)
        else:
            out.add("music", "artist", parts[0], WEAK)
            out.add("music", "album", parts[1], WEAK)
            out.add("music", "song_title", parts[2], WEAK)
        out.label("music", "artist-title", WEAK)
    elif stripped_track and len(parts) == 1:
        # This detector stripped a track number off the front, so whatever is
        # left is the title. Without that, a one-part name says nothing at all
        # and inventing a `song_title` from it labelled every file on the disk.
        out.add("music", "song_title", parts[0], WEAK)


_SAMPLE_WORDS = re.compile(
    r"\b(kick|snare|hat|hi-?hat|clap|rim|tom|crash|ride|perc|shaker|"
    r"cowbell|808|909|707|clave|loop|oneshot|one[-_ ]shot|stab|riser|"
    r"downlifter|uplifter|impact|whoosh|sweep|fx|foley|vox|acapella|"
    r"bassline|arp|pluck|pad|lead|sub|drone|texture)\b", re.I)
_BPM = re.compile(r"(?<!\d)(\d{2,3})\s*_?bpm\b|(?<![\w])bpm[ _]?(\d{2,3})",
                  re.I)
_KEY = re.compile(r"(?<![a-z])([A-G](?:#|b)?)[ _-]?(maj|min|m|major|minor)"
                  r"(?![a-z])")


def detect_sample(ctx, out):
    stem, ext, lower = ctx.plain, ctx.ext, ctx.lower
    """Producer samples, which must never be filed with somebody's music."""
    hit = _SAMPLE_WORDS.search(stem)
    bpm = _BPM.search(stem)
    key = _KEY.search(stem)
    if not (hit or bpm):
        return
    if hit:
        out.add("sample", "sample_type", hit.group(1).lower(), LIKELY)
    if bpm:
        value = int(bpm.group(1) or bpm.group(2))
        if 40 <= value <= 300:
            out.add("sample", "bpm", value, STRONG)
    if key:
        out.add("sample", "musical_key",
                key.group(1) + key.group(2).lower()[:3], LIKELY)
    out.label("sample", "sample", LIKELY)


# ---------------------------------------------------------------------------
# Paperwork
# ---------------------------------------------------------------------------

_PAPERWORK = (
    ("invoice", r"\b(invoice|rechnung|facture|factura|fattura|faktura)\b"),
    ("receipt", r"\b(receipt|kvitto|kvittering|quittung|recibo|ricevuta)\b"),
    ("statement", r"\b(statement|bank[ _-]?statement|kontoauszug|"
                  r"releve|extracto)\b"),
    ("payslip", r"\b(payslip|pay[ _-]?stub|paystub|salary|wage[ _-]?slip|"
                r"lohnabrechnung|p60|p45)\b"),
    ("tax", r"\b(tax|hmrc|irs|vat|moms|self[ _-]?assessment|1099|w-?2|"
            r"steuer|p11d)\b"),
    ("contract", r"\b(contract|agreement|nda|terms|tenancy|lease|"
                 r"vertrag|contrat)\b"),
    ("insurance", r"\b(insurance|policy|claim|versicherung|assurance)\b"),
    ("order", r"\b(order|purchase[ _-]?order|po[-_ ]?\d{4,}|bestellung)\b"),
    ("quote", r"\b(quote|quotation|estimate|offerte|angebot|devis)\b"),
    ("ticket", r"\b(ticket|boarding[ _-]?pass|itinerary|booking|"
               r"reservation|e-?ticket|flugticket)\b"),
    ("certificate", r"\b(certificate|certification|diploma|warranty|"
                    r"guarantee|zertifikat)\b"),
    ("cv", r"\b(cv|resume|curriculum[ _-]?vitae|cover[ _-]?letter|"
           r"lebenslauf)\b"),
    ("report", r"\b(report|minutes|agenda|proposal|summary|bericht)\b"),
    ("manual", r"\b(manual|handbook|instructions|user[ _-]?guide|"
               r"datasheet|spec[ _-]?sheet|bedienungsanleitung)\b"),
    ("licence", r"\b(licen[cs]e|licencia|lizenz|serial|activation)\b"),
    ("medical", r"\b(prescription|referral|discharge|test[ _-]?results|"
                r"vaccination|rezept)\b"),
)
_PAPERWORK = tuple((label, re.compile(pattern, re.I))
                   for label, pattern in _PAPERWORK)
_REFERENCE = re.compile(r"\b((?:inv|po|ref|no|nr|#)[-_ ]?\d{3,12})\b", re.I)


def detect_paperwork(ctx, out):
    stem, ext, lower = ctx.plain, ctx.ext, ctx.lower
    for label, pattern in _PAPERWORK:
        if pattern.search(stem):
            out.add("paperwork", "paperwork", label, WEAK)
            out.label("paperwork", label, WEAK)
            reference = _REFERENCE.search(stem)
            if reference:
                out.add("paperwork", "reference",
                        reference.group(1).upper().replace(" ", ""), WEAK)
            return


_SCANNER = (
    (re.compile(r"^scan[_ -]?\d*", re.I), None),
    (re.compile(r"^scanned[ _-]", re.I), None),
    (re.compile(r"^CamScanner", re.I), "CamScanner"),
    (re.compile(r"^Adobe Scan", re.I), "Adobe Scan"),
    (re.compile(r"^(NEW )?DOC[-_ ]?\d", re.I), None),
    (re.compile(r"^SKM[_ ]?C\d", re.I), "Konica Minolta"),
    (re.compile(r"^EPSON\d*", re.I), "Epson"),
    (re.compile(r"^img\d{8}_\d{4}$", re.I), None),
    (re.compile(r"^Document[ _-]?\d", re.I), None),
    (re.compile(r"^(Notes|Note)[ _-]?\d{1,2}[-_ ]", re.I), None),
)


def detect_scan(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    for pattern, device in _SCANNER:
        if pattern.match(stem):
            out.label("scan", "scan", LIKELY)
            out.add("scan", "capture", "scan", LIKELY)
            if device:
                out.add("scan", "from_app", device, LIKELY)
            return


# ---------------------------------------------------------------------------
# Software
# ---------------------------------------------------------------------------

_INSTALLER = re.compile(
    r"\b(setup|install(er|ation)?|portable|x64|x86|amd64|arm64|aarch64|"
    r"win(dows)?(32|64)?|macos|darwin|linux|universal|full|offline)\b", re.I)
_VERSION = re.compile(r"(?<![\w.])v?(\d{1,3}(?:\.\d{1,4}){1,3})"
                      r"(?:[-_]?(alpha|beta|rc\d*|dev|nightly))?(?![\w.])",
                      re.I)


def detect_software(ctx, out):
    stem, ext, lower = ctx.plain, ctx.ext, ctx.lower
    version = _VERSION.search(stem)
    installer = _INSTALLER.search(stem)
    if version:
        out.add("software", "version", version.group(1), LIKELY)
        if version.group(2):
            out.add("software", "channel", version.group(2).lower(), LIKELY)
    if installer:
        out.add("software", "platform_tag", installer.group(1).lower(), WEAK)
    if installer and (version or re.search(r"\b(setup|install)", lower)):
        out.label("software", "installer", LIKELY)
        # The product name is what precedes the first version or platform tag.
        cut = min(x.start() for x in (version, installer) if x)
        product = re.sub(r"[-_.]+", " ", stem[:cut]).strip()
        if product:
            out.add("software", "product", product, WEAK)


# ---------------------------------------------------------------------------
# What a browser, a file manager and an operating system leave behind
# ---------------------------------------------------------------------------

# Only the shapes a file manager actually produces. A name that merely ends
# in a digit is not a copy, and treating it as one marked every photo off
# every camera ever made as a duplicate.
_COPY_SUFFIX = re.compile(
    r"(?:\((\d{1,2})\)|[ _-](?:copy|kopie|kopia|copia)(?:[ _-](\d{1,2}))?)$",
    re.I)
_COPY_WORD = re.compile(r"^(copy of|copia de|kopie von)\b", re.I)
_GENERIC = re.compile(
    r"^(untitled|unnamed|new (file|document|folder|image|text document)|"
    r"document|image|photo|picture|video|audio|file|download|downloaded|"
    r"output|export|final|draft|temp|tmp|test|asdf|aaa|blob|index|sample)"
    r"[ _-]?\d{0,3}$", re.I)
_HASH_NAME = re.compile(r"^[0-9a-f]{32}$|^[0-9a-f]{40}$|^[0-9a-f]{64}$", re.I)
_UUID_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_WEB_SIZED = re.compile(r"(^|[-_])(\d{2,4}x\d{2,4}|thumb|thumbnail|small|"
                        r"medium|large|min|preview|@[23]x|scaled|cropped)"
                        r"([-_]|$)", re.I)
_VERSION_WORD = re.compile(
    r"\b(final|FINAL|draft|wip|rev\d*|revision|v\d+|version\s?\d+|"
    r"old|backup|bak|orig|original|new|latest|updated?)\b")
_OS_LITTER = {".ds_store", "thumbs.db", "desktop.ini", "icon\r",
              ".localized", "__macosx", ".spotlight-v100", ".trashes",
              ".fseventsd", "$recycle.bin", "system volume information",
              ".apdisk", "ehthumbs.db"}


def detect_litter(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    full = (stem + ("." + ext if ext else "")).lower()
    if full in _OS_LITTER or stem.startswith("._"):
        out.label("litter", "os-litter", CERTAIN)
        out.add("litter", "litter", True, CERTAIN)
        return
    if _GENERIC.match(stem):
        out.add("litter", "generic_name", True, STRONG)
        out.label("litter", "generic-name", LIKELY)
    if _HASH_NAME.match(stem):
        out.add("litter", "hash_name", True, CERTAIN)
        out.label("litter", "hash-name", LIKELY)
    elif _UUID_NAME.match(stem):
        out.add("litter", "hash_name", True, CERTAIN)
        out.label("litter", "hash-name", LIKELY)
    if _COPY_WORD.match(stem):
        out.add("litter", "copy_marker", "copy of", STRONG)
    else:
        copied = _COPY_SUFFIX.search(stem)
        if copied and stem != copied.group(0).strip():
            out.add("litter", "copy_marker", copied.group(0).strip(), LIKELY)
    if _WEB_SIZED.search(stem):
        out.add("litter", "web_sized", True, LIKELY)
    version = _VERSION_WORD.search(stem)
    if version:
        out.add("litter", "version_marker", version.group(1).lower(), WEAK)
    if "%20" in stem or "%2F" in stem.upper():
        out.add("litter", "url_encoded", True, CERTAIN)


_SUBTITLE_TAGS = re.compile(
    r"\.(?P<lang>[a-z]{2,3}(?:-[A-Za-z]{2,4})?)"
    r"(?:\.(?P<flags>forced|sdh|cc|hi|default))?$", re.I)
_SUBTITLE_WORDS = {"forced", "sdh", "cc", "hi", "default"}


def detect_subtitle_tags(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    """`.en.srt`, `.pt-BR.forced.srt` — the language lives in the stem."""
    if (ext or "").lower() not in ("srt", "vtt", "ass", "ssa", "sub", "sup",
                                   "smi", "idx", "ttml"):
        return
    working = stem
    flags = []
    while True:
        match = _SUBTITLE_TAGS.search(working)
        if not match:
            break
        tag = match.group("lang").lower()
        if match.group("flags"):
            flags.append(match.group("flags").lower())
        if tag in _SUBTITLE_WORDS:
            flags.append(tag)
            working = working[:match.start()]
            continue
        out.add("subtitle", "subtitle_language", tag, LIKELY)
        out.add("subtitle", "subtitle_for", working[:match.start()], LIKELY)
        break
    for flag in flags:
        out.add("subtitle", "subtitle_" + flag, True, LIKELY)


_SEQUENCE = re.compile(r"^(?P<prefix>.*?)(?P<number>\d{2,5})$")


def detect_sequence(ctx, out):
    stem, ext, lower = ctx.stem, ctx.ext, ctx.lower
    """`page_0001`, `render.0042` — one frame of something, not a lone file."""
    if any(name in ("camera_series", "capture", "from_app",
                    "subtitle_language") for name, _v, _c, _d in out.facts):
        return                      # a camera's own counter, already named
    match = _SEQUENCE.match(stem)
    if not match:
        return
    prefix = match.group("prefix").rstrip("-_. ")
    number = match.group("number")
    # Zero padding is what distinguishes `page_0042` from `CV 2026`. Without
    # it every filename ending in a year became a frame in a sequence.
    if not prefix or len(number) < 3 or not number.startswith("0"):
        return
    out.add("sequence", "sequence_prefix", prefix, WEAK)
    out.add("sequence", "sequence_number", int(number), WEAK)


def detect_site(ctx, out):
    """Filenames written by the site a file was downloaded from.

    Placed first among the shape detectors because these conventions are
    exact. A FurAffinity name is a timestamp, a dot, a username and a title,
    and letting the music parser see it first reads the whole thing as an
    artist and a track at the same confidence as a real one.

    This is also where emergent structure comes from. Nobody decides in
    advance that there should be a folder per artist: `uploader` is just a
    fact, and a rule filing by it builds whatever folders the corpus needs.
    """
    name, found = sites.read(ctx.stem, ctx.ext, ctx.kind)
    if not found:
        return
    for fact, (value, confidence) in found.items():
        out.add("site:" + name, fact, value, confidence)
    site = found.get("site")
    if site:
        out.label("site:" + name, site[0], site[1])


# Detector, and the kinds it is allowed to speak about. `None` means any,
# including a file whose kind is not yet settled.
#
# The gate is not an optimisation. An ungated music detector reads
# `Boarding Pass - LHR.pdf` as an artist and a title, and `Some Film - 2019`
# as an album, and does it at the same confidence as it reads an actual song.
# Every one of those is a fact that then has to be argued with downstream.
# Detectors that only make sense for one kind say so here.
_ANY = None
_DETECTORS = (
    ("litter", detect_litter, _ANY),
    ("site", detect_site, ("image", "video", "audio")),
    ("screenshot", detect_screenshot, ("image", "video")),
    ("messaging", detect_messaging, ("image", "video", "audio", "document")),
    ("camera", detect_camera, ("image", "video")),
    ("scan", detect_scan, ("document", "image")),
    ("release", detect_release, ("video", "audio", "archive", "subtitle")),
    ("subtitle", detect_subtitle_tags, ("subtitle",)),
    ("sample", detect_sample, ("audio",)),
    ("software", detect_software, ("app", "disk-image", "archive")),
    ("paperwork", detect_paperwork, ("document", "image", "archive")),
    ("music", detect_music, ("audio",)),
    ("dates", detect_dates, _ANY),
    ("sequence", detect_sequence, ("image", "video", "model3d")),
)


def read(filename, kind=None):
    """Everything a filename admits. Returns a `Found`.

    `filename` is the base name with its extension, not a path. `kind` is the
    kind already established by the extension or the signature, and passing it
    is strongly preferred: it is what stops the detectors for other kinds from
    contributing noise. Passing `None` runs everything, which is the right
    behaviour for a file nothing else could identify and the wrong behaviour
    for one that has already been read.
    """
    ctx = Context(filename, kind)
    out = Found()
    for _name, detector, allowed in _DETECTORS:
        if allowed is not None and kind is not None and kind not in allowed:
            continue
        try:
            detector(ctx, out)
        except (re.error, ValueError, TypeError, IndexError, KeyError):
            # A detector that throws on a pathological name must not stop the
            # other twelve, and must never stop the file being sorted.
            continue
    if out.labels:
        best = max(out.labels, key=lambda item: item[1])
        out.facts.append(("looks_like", best[0], best[1], best[2]))
    return out
