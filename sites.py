"""Filenames written by the places files come from.

A download folder is not a random pile. Most of it was named by software, and
that software followed a convention. `1770665382.flaich_illustration16.png` is
a FurAffinity submission and the uploader's name is sitting in the middle of
it. `9212888c5c4816ac0d7fc2be7baa8027.webm` is a booru file named after its own
MD5, which tells you where it came from and nothing else at all. Both facts
are worth having, and the second is worth having *because* it is a dead end:
it marks the files that no amount of cheap processing will ever organise.

This is the layer that lets structure emerge rather than be planned. Nobody
has to decide in advance that there should be a folder per artist. The
uploader is simply a fact, like a camera model or an album, and a rule filing
by `{uploader}` builds whatever folders the corpus turns out to need.

**Nothing here touches the network.** Every site in this table has an API or a
tag page that would say far more than the filename does, and reaching for it
would mean accounts, credentials, rate limits and a tool that stops working
when someone is offline. What is on the disk is what gets used.

Confidence follows how much a pattern could be a coincidence. FurAffinity's
shape -- ten digits, a dot, a username, an underscore -- is not something an
ordinary filename does by accident, so it is `strong`. A bare 32-character hex
name is certainly a hash and only probably a booru, so the hash is `certain`
and the site is a `weak` guess. Seven alphanumeric characters could be an
Imgur id or could be somebody's initials, and is not claimed at all.
"""

from __future__ import annotations

import re

from evidence import CERTAIN, STRONG, LIKELY, WEAK


def _fur_affinity(stem, ext):
    """`1770665382.uploader_title` -- submission time, artist, then title.

    The uploader ends at the first underscore, which is FurAffinity's own
    convention and not a guess: the timestamp and the dot before it are what
    make the shape unmistakable.
    """
    match = re.match(r"^(?P<id>\d{9,10})\.(?P<uploader>[A-Za-z0-9.~-]{2,40})"
                     r"_(?P<title>.+)$", stem)
    if not match:
        return None
    title = re.sub(r"_?\[(resized_for_web|resized|web)\]$", "",
                   match.group("title"), flags=re.I)
    return {
        "site": ("furaffinity", STRONG),
        "uploader": (match.group("uploader").lower(), STRONG),
        "post_id": (match.group("id"), STRONG),
        "post_title": (title.replace("_", " ").strip(), LIKELY),
        "posted_epoch": (int(match.group("id")), LIKELY),
    }


def _hash_named(stem, ext):
    """A file named after its own checksum. The commonest booru convention.

    The hash is certain -- 32 or 40 hex characters and nothing else is not
    an accident. The site is *not* claimed at all, and that restraint is the
    point. Boorus name files this way, and so do browser caches, download
    managers, content-addressed stores and git. Guessing "booru" from a hash
    would hand the proposer a `site` to build a folder out of, and it would
    be a folder named after a guess.

    So these files carry a hash and the fact that they are opaque, and
    nothing else. They are the honest limit of filename analysis: no
    uploader, no title, no date, and no further filename work will produce
    one. Organising them needs something that looks at the picture.
    """
    lowered = stem.lower()
    if not re.match(r"^[0-9a-f]{32}$", lowered):
        if not re.match(r"^[0-9a-f]{40}$", lowered):
            return None
        algorithm = "sha1"
    else:
        algorithm = "md5"
    return {
        "content_hash_name": (lowered, CERTAIN),
        "hash_algorithm": (algorithm, CERTAIN),
        "opaque": (True, CERTAIN),
    }


def _booru_with_tags(stem, ext):
    """Downloaders that keep the tag list: `__tag_tag__<md5>`."""
    match = re.match(r"^__(?P<tags>.{3,180}?)__(?P<hash>[0-9a-f]{32})$",
                     stem, re.I)
    if not match:
        return None
    tags = [tag for tag in match.group("tags").split("_") if tag]
    found = {
        "site": ("booru-style", LIKELY),
        "content_hash_name": (match.group("hash").lower(), CERTAIN),
        "tags": (", ".join(tags[:12]), LIKELY),
    }
    # Booru tag lists conventionally lead with the artist.
    if tags:
        found["uploader"] = (tags[0].lower(), WEAK)
    return found


def _e621(stem, ext):
    match = re.match(r"^(?:e621|e6|e926)[ _-]?(?P<id>\d{4,9})"
                     r"(?:[ _-](?P<rest>.+))?$", stem, re.I)
    if not match:
        return None
    found = {"site": ("e621", STRONG), "post_id": (match.group("id"), STRONG)}
    if match.group("rest"):
        found["tags"] = (match.group("rest").replace("_", " ")[:180], WEAK)
    return found


def _pixiv(stem, ext):
    """`98765432_p0`, optionally with a size suffix the site adds."""
    match = re.match(r"^(?P<id>\d{6,9})_p(?P<page>\d{1,3})"
                     r"(?:_(?P<size>master\d+|square\d+|custom\d+))?$", stem)
    if not match:
        return None
    found = {"site": ("pixiv", STRONG), "post_id": (match.group("id"), STRONG),
             "page": (int(match.group("page")), STRONG)}
    if match.group("size"):
        found["web_sized"] = (True, CERTAIN)
    return found


def _deviantart(stem, ext):
    """`title_by_username_d<base36>` -- DeviantArt's own download name."""
    match = re.match(r"^(?P<title>.+?)_by_(?P<uploader>[A-Za-z0-9-]{2,40})"
                     r"_d(?P<id>[a-z0-9]{6,12})(?P<extra>[-_].*)?$", stem,
                     re.I)
    if not match:
        return None
    return {
        "site": ("deviantart", STRONG),
        "uploader": (match.group("uploader").lower(), STRONG),
        "post_id": (match.group("id"), STRONG),
        "post_title": (match.group("title").replace("_", " ").strip(),
                       LIKELY),
    }


def _meta_cdn(stem, ext):
    """`10665432_10152345678_1234567890_n` -- Facebook and Instagram.

    Three long runs of digits and a single-letter size suffix. It must be
    matched before Inkbunny, whose shape is `<id>_<username>_<title>` and
    which will otherwise read the second run of digits as an artist's name:
    on a family machine that files hundreds of pictures from Facebook groups
    into an artwork folder, which is where this test came from.

    Facebook and Instagram share Meta's naming and cannot be told apart from
    the filename, so the site is only a guess -- and the download URL, when
    there is one, already says which exactly. What is certain is that the
    name carries no artist, no title and no date.
    """
    if not re.match(r"^\d{6,}_\d{6,}_\d{6,}_[a-z]{1,2}$", stem):
        return None
    middle = stem.split("_")[1]
    return {
        "site": ("facebook", WEAK),
        "post_id": (middle, LIKELY),
        "opaque": (True, CERTAIN),
    }


def _inkbunny(stem, ext):
    """`<id>_<username>_<title>`.

    The username must contain a letter. Without that test every Facebook
    filename in the world matched, because a run of digits is a perfectly
    good `[A-Za-z0-9]` string -- and the underscore was in the username's
    own character class as well, so it swallowed the first word of the title
    too.
    """
    match = re.match(r"^(?P<id>\d{6,8})_(?P<uploader>[A-Za-z0-9.~-]{2,30})"
                     r"_(?P<title>.+)$", stem)
    if not match:
        return None
    uploader = match.group("uploader")
    if not any(character.isalpha() for character in uploader):
        return None
    # Distinguished from FurAffinity only by the id being too short to be a
    # unix timestamp, so it is claimed more cautiously.
    return {
        "site": ("inkbunny", LIKELY),
        "uploader": (uploader.lower(), LIKELY),
        "post_id": (match.group("id"), LIKELY),
        "post_title": (match.group("title").replace("_", " ").strip(), WEAK),
    }


def _twitter(stem, ext):
    """A Twitter/X media id: fifteen characters of base64url.

    The length alone is not enough -- `focus10advanced` is fifteen characters
    and is a word. Requiring upper case, lower case and a digit, and rejecting
    anything that reads as words, is what separates an id from a name somebody
    typed.
    """
    if not re.match(r"^[A-Za-z0-9_-]{15}$", stem):
        return None
    uppers = sum(1 for ch in stem if ch.isupper())
    lowers = sum(1 for ch in stem if ch.islower())
    digits = sum(1 for ch in stem if ch.isdigit())
    if uppers < 2 or lowers < 2 or digits < 1:
        return None
    # A run of five or more letters of one case reads as a word, not an id.
    if re.search(r"[a-z]{6,}", stem) or re.search(r"[A-Z]{6,}", stem):
        return None
    return {"site": ("twitter", LIKELY), "post_id": (stem, LIKELY),
            "opaque": (True, LIKELY)}


def _tumblr(stem, ext):
    match = re.match(r"^tumblr_(?:inline_)?(?P<id>[A-Za-z0-9]{8,24})"
                     r"(?:_(?P<size>\d{2,4}))?", stem)
    if not match:
        return None
    found = {"site": ("tumblr", STRONG), "post_id": (match.group("id"),
                                                     STRONG)}
    if match.group("size"):
        found["web_sized"] = (True, CERTAIN)
    return found


def _newgrounds(stem, ext):
    match = re.match(r"^(?P<uploader>[A-Za-z0-9-]{2,30})[-_]"
                     r"(?P<title>.+?)[-_](?P<id>\d{6,9})$", stem)
    if not match:
        return None
    return {"site": ("newgrounds", WEAK),
            "uploader": (match.group("uploader").lower(), WEAK),
            "post_id": (match.group("id"), WEAK)}


def _chan(stem, ext):
    """Thirteen digits is a millisecond timestamp, which is how 4chan names."""
    match = re.match(r"^(?P<id>1\d{12})(?:[ _-](?P<rest>.*))?$", stem)
    if not match:
        return None
    return {"site": ("imageboard", WEAK), "post_id": (match.group("id"),
                                                      LIKELY),
            "opaque": (True, LIKELY)}


def _discord(stem, ext):
    if re.match(r"^(image|video|unknown)\d*$", stem, re.I):
        return {"site": ("discord", WEAK), "opaque": (True, LIKELY)}
    if stem.upper().startswith("SPOILER_"):
        return {"site": ("discord", LIKELY), "marked_spoiler": (True, STRONG)}
    return None


def _patreon(stem, ext):
    match = re.match(r"^(?:\[?patreon\]?|patreon)[ _-]+(?P<rest>.+)$", stem,
                     re.I)
    if not match:
        return None
    return {"site": ("patreon", LIKELY),
            "post_title": (match.group("rest").replace("_", " ").strip(),
                           WEAK)}


# Order is priority: the first detector to claim a name wins, so the shapes
# that cannot be mistaken for anything come before the ones that can.
DETECTORS = (
    ("furaffinity", _fur_affinity),
    ("deviantart", _deviantart),
    ("booru-tags", _booru_with_tags),
    ("e621", _e621),
    ("pixiv", _pixiv),
    ("tumblr", _tumblr),
    ("patreon", _patreon),
    ("meta-cdn", _meta_cdn),
    ("inkbunny", _inkbunny),
    ("hash", _hash_named),
    ("twitter", _twitter),
    ("chan", _chan),
    ("discord", _discord),
    ("newgrounds", _newgrounds),
)

# Kinds these conventions are ever used for. A `.py` file named like a hash is
# a build artefact, not a booru download.
KINDS = ("image", "video", "audio", "unknown", None)


def read(stem, ext, kind=None):
    """(detector name, {fact: (value, confidence)}) or (None, {}).

    `stem` is the filename without its extension, exactly as written.
    """
    if kind is not None and kind not in KINDS:
        return None, {}
    for name, detector in DETECTORS:
        try:
            found = detector(stem, ext)
        except (re.error, ValueError, TypeError):
            continue
        if found:
            return name, found
    return None, {}
