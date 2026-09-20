"""Read a folder, and write the rules that folder turns out to need.

The opposite of shipping a taxonomy. Nobody decides in advance that there
should be a folder for each artist, or for each camera, or for episodes: the
corpus is surveyed, the groupings that actually exist in it are counted, and
the ones worth making a folder for become rules. A folder of four hundred
thousand files from one site needs a different structure from a folder of
holiday photographs, and neither of them should have to be described by hand.

**What makes a grouping worth proposing** is the whole algorithm, and it is
three questions rather than one:

*Coverage* -- how many files even have this fact? A camera model that appears
on eleven files out of forty thousand is not a structure, it is a detail.

*Shape* -- how many folders would it make, and how full would they be? A
thousand uploaders with one file each is not organisation; it is the same pile
with more steps. The test is the median group, not the mean, because one
prolific artist and nine hundred one-offs has a flattering mean.

*Residue* -- what is left over. A proposal that files 12% of a folder and
leaves 88% in `_Unsorted` is worse than useless because it looks like
progress. The report says the residue plainly, and says what kind of residue
it is: files named after their own checksum carry nothing to sort by, and no
amount of further filename work will change that. They are the honest
boundary of cheap processing.

Nothing here moves anything. It writes a rules file for somebody to read,
which the ordinary engine then runs with its ordinary preview, ledger and
undo. A tool that reorganised a disk by a structure nobody had seen would be
a tool nobody could check.
"""

from __future__ import annotations

import collections
import os

import bundles
import identify
import paths
import shapes
import userdirs

# Stop tracking a fact once it has this many distinct values. Past this point
# it is an identifier rather than a category, and counting the rest costs
# memory on exactly the folders that can least afford it.
MAX_DISTINCT = 4000

# A facet has to reach all three of these to become a rule.
MIN_COVERAGE = 0.02          # of all items surveyed
MIN_CONCENTRATION = 0.4      # share of files landing in a shared folder
MAX_GROUPS = 600             # folders one facet may create


class Facet(object):
    """One possible way of grouping, and the rule it would become.

    `base` is the path under the kind's own system folder -- `Art/{uploader}`
    under Pictures for an image and under Movies for a clip. Splitting by kind
    at render time, rather than inventing one destination for everything, is
    what lets a photograph land in Pictures and a video in Movies instead of
    both ending up in a parallel tree that duplicates what the operating
    system already provides.
    """

    def __init__(self, key, needs, base, precedence, when=None, note="",
                 group_by=None, rename=None, min_confidence=None,
                 kind=None, per_kind=False):
        self.key = key
        self.needs = tuple(needs)
        self.base = base
        self.precedence = precedence
        self.when = when or " and ".join("%s is set" % f for f in needs)
        self.note = note
        self.group_by = group_by or (needs[0] if needs else None)
        self.rename = rename
        self.min_confidence = min_confidence
        self.kind = kind            # a single kind this only applies to
        self.per_kind = per_kind    # one rule per kind that actually occurs


FACETS = (
    Facet("site-uploader", ("uploader",), "Art/{uploader}", 10,
          when="uploader is set",
          note="one folder per artist, which is what the filenames carry",
          group_by="uploader", per_kind=True),
    Facet("source", ("source",), "From/{source}", 20,
          when="source is set and uploader is unset",
          note="by the service it came from, which the operating system "
               "recorded at download time",
          group_by="source", per_kind=True),
    Facet("camera", ("camera",), "{taken:%Y}/{taken:%Y-%m-%d} {camera}", 30,
          when="camera is set and taken is set",
          note="photographs, by the day and the body that took them",
          group_by="camera", kind="image"),
    Facet("screenshot", ("capture",), "Screenshots/{added:%Y-%m}", 40,
          when="capture = screenshot",
          note="screenshots", group_by="capture", kind="image"),
    Facet("series", ("title", "season"), "Series/{title}/Season {season}", 50,
          when="episode is set",
          note="episodes, by series", group_by="title", kind="video"),
    Facet("music", ("artist", "album"), "{artist}/{album}", 60,
          when="artist is set and album is set",
          note="tagged music", group_by="artist", kind="audio",
          rename="{track:02} {song_title}.{ext}"),
    Facet("paperwork", ("paperwork",), "{paperwork}", 70,
          when="paperwork is set",
          note="documents whose name says what they are",
          group_by="paperwork", kind="document", min_confidence=0.4),
    Facet("duration", ("duration_class",), "{duration_class}", 90,
          when="duration is set",
          note="video, split by length", group_by="duration_class",
          kind="video"),
)


class Survey(object):
    """What one folder turned out to contain."""

    def __init__(self, root):
        self.root = root
        self.items = 0
        self.files = 0
        self.bytes = 0
        self.values = collections.defaultdict(collections.Counter)
        self.overflowed = set()
        self.kinds = collections.Counter()
        self.opaque = 0
        self.opaque_unplaceable = 0
        self.unreadable = 0
        self.sites = collections.Counter()
        self.stems = []                  # for convention induction
        self.stem_sources = {}
        self.conventions = []
        self.convention_depth = None
        self.kind_by_fact = collections.defaultdict(collections.Counter)

    def observe(self, record, members=1):
        self.items += 1
        self.files += members
        self.bytes += record.value("size") or 0
        self.kinds[record.value("kind", "unknown")] += 1
        if record.value("opaque"):
            self.opaque += 1
            # An opaque file that at least names its site can still be filed
            # by site, which is better than nothing. The residue that matters
            # is the one with no handle of any kind on it.
            if not record.value("site"):
                self.opaque_unplaceable += 1
        if record.value("site"):
            self.sites[record.value("site")] += 1
        stem = record.value("stem")
        if stem and record.value("kind") in ("image", "video", "audio"):
            self.stems.append(stem)
            source = record.value("source")
            if source:
                self.stem_sources[stem] = source
        kind = record.value("kind", "unknown")
        for facet in FACETS:
            if facet.group_by and record.has(facet.group_by):
                self.kind_by_fact[facet.group_by][kind] += 1
        for name in record.names():
            if name in _NOT_A_CATEGORY:
                continue
            if name in self.overflowed:
                continue
            counter = self.values[name]
            if len(counter) >= MAX_DISTINCT:
                # An identifier, not a category. Stop paying for it.
                self.overflowed.add(name)
                self.values.pop(name, None)
                continue
            counter[str(record.value(name))[:120]] += 1

    def kinds_for(self, fact, floor=0.05):
        """The kinds that actually carry a fact, ignoring a stray few."""
        counts = self.kind_by_fact.get(fact)
        if not counts:
            return []
        total = sum(counts.values())
        return [kind for kind, count in counts.most_common()
                if count >= max(2, total * floor)]

    def coverage(self, fact):
        counter = self.values.get(fact)
        if not counter or not self.items:
            return 0.0
        return sum(counter.values()) / float(self.items)

    def groups(self, fact):
        return self.values.get(fact, collections.Counter())


# Facts that are unique per file, or are plumbing. Counting them would fill
# memory and propose a folder per file.
_NOT_A_CATEGORY = {
    "name", "stem", "path", "dir", "size", "modified", "added", "age",
    "happened", "taken", "digitised", "content_created", "content_modified",
    "created", "post_id", "content_hash_name", "sha256", "gps",
    "description", "comment", "doc_title", "post_title", "song_title",
    "subtitle_for", "sequence_prefix", "sequence_number", "source_id",
    "from_url", "referrer", "name_date", "name_time", "downloaded",
    "duration", "width", "height", "aspect", "megapixels", "bitrate",
    "samplerate", "iso", "focal_length", "aperture", "serial", "checksum",
    "release_year", "year", "track", "disc", "episode", "pages", "words",
    "lines", "bpm", "posted_epoch", "tags", "keywords", "version",
}


def survey(root, tier=identify.TIER_HEADER, depth=3, limit=None,
           on_progress=None):
    """Walk a folder and count what is in it. Reads nothing twice."""
    found = Survey(os.path.abspath(root))
    for item in bundles.walk(root, max_depth=depth):
        try:
            record = identify.identify(item, tier=tier)
        except (OSError, ValueError):
            found.unreadable += 1
            continue
        found.observe(record, len(item.members))
        if on_progress and found.items % 2000 == 0:
            on_progress(found.items)
        if limit and found.items >= limit:
            break
    found.conventions, found.convention_depth = shapes.learn_best(found.stems)
    return found


def convention_source(found, convention):
    """The one service a convention's files came from, if there is just one.

    A pattern learnt from a folder is a statement about those files, not
    about every file in the world shaped like them. When every file that
    taught it came from one place the rule can say so, and then it cannot
    misfire on something similar-looking from somewhere else.
    """
    sources = collections.Counter()
    for stem in convention.examples_all:
        source = found.stem_sources.get(stem)
        if source:
            sources[source] += 1
    if not sources:
        return None
    winner, count = sources.most_common(1)[0]
    return winner if count >= 0.9 * convention.count else None


class Proposal(object):
    def __init__(self, facet, covered, groups, median, share, reason):
        self.facet = facet
        self.covered = covered
        self.groups = groups
        self.median = median
        self.share = share      # of covered files landing in a shared folder
        self.reason = reason
        self.accepted = reason is None

    @property
    def key(self):
        return self.facet.key


def _median(counts):
    ordered = sorted(counts)
    if not ordered:
        return 0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def assess(found):
    """Every facet, judged, accepted or not, with the reason either way."""
    results = []
    for facet in FACETS:
        coverage = min(found.coverage(fact) for fact in facet.needs) \
            if facet.needs else 0.0
        counter = found.groups(facet.group_by)
        covered = sum(counter.values())
        groups = len(counter)
        median = _median(list(counter.values()))

        share = shapes.concentration(counter.values())

        reason = None
        if groups == 0:
            reason = "nothing in this folder has %s" % facet.group_by
        elif coverage < MIN_COVERAGE:
            reason = ("only %.1f%% of items have %s"
                      % (coverage * 100, ", ".join(facet.needs)))
        elif groups > MAX_GROUPS:
            reason = "would make %d folders" % groups
        elif share < MIN_CONCENTRATION:
            reason = ("%.0f%% of them would be alone in a folder"
                      % ((1 - share) * 100))
        results.append(Proposal(facet, covered, groups, median, share,
                                reason))
    return results


def same_volume_as_home(target):
    try:
        return os.stat(target).st_dev == os.stat(userdirs.home()).st_dev
    except OSError:
        return False


def is_funnel(root):
    """Whether this folder is somewhere files pass through, or somewhere
    they live.

    Downloads on the internal disk is a funnel: the right destination for
    what is in it is the folders the system already has, and the whole point
    is that it ends up empty. An external drive is not. Sorting a USB stick
    into `~/Pictures` would quietly copy a hundred gigabytes onto the
    internal disk -- a copy, a hash and a delete for every file, rather than
    a rename -- and would be the opposite of what somebody tidying that drive
    asked for.

    So: on the home volume, file into the system folders. Anywhere else,
    tidy in place.
    """
    return same_volume_as_home(root)


def destination_root(found, kind):
    """The base folder for one kind, honouring which volume this is."""
    if is_funnel(found.root):
        return userdirs.home_for(kind)
    return os.path.join(found.root, "Sorted", _KIND_FOLDERS.get(
        kind, kind.title() if kind else "Other"))


# Names for in-place sorting, where there are no system folders to use.
_KIND_FOLDERS = {
    "image": "Pictures", "video": "Video", "audio": "Music",
    "document": "Documents", "subtitle": "Subtitles", "model3d": "3D",
    "font": "Fonts", "archive": "Archives", "disk-image": "Disk Images",
    "app": "Installers", "code": "Code", "data": "Data",
    "unknown": "Unsorted",
}


def catch_all_root(found, kind):
    if is_funnel(found.root):
        return userdirs.catch_all_for(kind)
    base = destination_root(found, kind)
    return base if _KIND_FOLDERS.get(kind) not in (
        "Pictures", "Video", "Music", "Documents") \
        else os.path.join(base, "Unfiled")


def destination(found, facet, kind):
    """A facet's folder for one kind, written the way a person reads it."""
    base = destination_root(found, kind)
    return userdirs.short(os.path.join(base, facet.base) if facet.base
                          else base)


def _when_for(facet, kind):
    if facet.per_kind or facet.kind:
        return "kind = %s and %s" % (kind, facet.when)
    return facet.when


def render(found, proposals):
    """The proposed rules file, as text, with the counts that justified it.

    Written for a funnel. Downloads is not a place anybody keeps things, it
    is where everything arrives on its way somewhere, so the file ends with
    catch-alls that guarantee every kind present has somewhere to go. A
    proposal that files the recognisable half and leaves the rest behind does
    not empty anything: the folder refills and the tool looks like it worked.

    Destinations are the folders the operating system already made. Pictures,
    Movies, Music and Documents sit empty on most machines while everything
    piles up in Downloads, and adding a second set beside them would be one
    more mess with a tidier name.
    """
    accepted = [p for p in proposals if p.accepted]
    accepted.sort(key=lambda p: p.facet.precedence)
    rejected = [p for p in proposals if not p.accepted]

    lines = [
        "; auto-sort rules proposed from %s" % userdirs.short(found.root),
        "; %s items, %s files, %s" % (
            "{:,}".format(found.items), "{:,}".format(found.files),
            _size(found.bytes)),
        ";",
    ] + ([
        "; This folder is treated as a funnel: somewhere files arrive on",
        "; their way elsewhere, not somewhere they live. So every kind that",
        "; turned up here has a destination below, and the last few rules",
        "; are catch-alls -- otherwise the folder keeps the half nothing",
        "; recognised and fills straight back up.",
        ";",
        "; Everything goes to the folders this machine already has, which",
        "; on most computers sit empty while Downloads fills up.",
    ] if is_funnel(found.root) else [
        "; This is not the home volume, so it is sorted in place, under a",
        "; Sorted folder here -- somewhere files live rather than somewhere",
        "; they pass through. Filing it into the system folders would copy",
        "; every file onto the internal disk instead of renaming it where",
        "; it lies, which is the opposite of tidying a drive.",
        ";",
        "; Every kind present still has a destination, and the last rules",
        "; are catch-alls, so nothing is left where it was.",
    ]) + [
        ";",
        "; Read it, delete what you disagree with, then:",
        ";",
        ";   auto-sort check-rules THIS_FILE",
        ";   auto-sort sort %s --rules THIS_FILE" % userdirs.short(found.root),
        "",
        "[settings]",
        "dry_run        = yes",
        "unsorted       = leave",
        "on_collision   = suffix",
        "min_confidence = 0.6",
        "settle_seconds = 3",
        "",
        "[watch]",
        "folders = %s" % userdirs.short(found.root),
        "depth   = 3",
        "",
    ]

    for index, convention in enumerate(found.conventions, 1):
        source = convention_source(found, convention)
        values = convention.fields[convention.category][2]
        kinds = found.kinds_for("site") or ["image"]
        lines.append("; A naming convention these files share, learnt from")
        lines.append("; them rather than configured: %d files, %d distinct"
                     % (convention.count, convention.groups))
        lines.append("; values in one field, %.0f%% of them sharing a folder."
                     % (convention.concentration * 100))
        lines.append(";   values: %s" % ", ".join(
            name for name, _count in values.most_common(6)))
        lines.append("; Nothing here knows what those values mean. If you can")
        lines.append("; see it, rename the capture and the folder follows.")
        for kind in kinds[:3]:
            label = "%s names" % source if source else "name pattern %d" % index
            if len(kinds[:3]) > 1:
                label += " (%s)" % kind
            lines.append("[rule: %s]" % label)
            condition = "kind = %s" % kind
            if source:
                condition += " and source = %s" % source
            lines.append("when    = %s" % condition)
            lines.append("extract = stem re %s" % convention.pattern("group"))
            lines.append("into    = %s"
                         % userdirs.short(os.path.join(
                             destination_root(found, kind),
                             source or "By name", "{group}")))
            lines.append("")

    for proposal in accepted:
        facet = proposal.facet
        kinds = [facet.kind] if facet.kind else (
            found.kinds_for(facet.group_by) if facet.per_kind else ["document"])
        kinds = [kind for kind in kinds if kind] or ["document"]
        lines.append("; %s -- %d items across %d folder%s, %.0f%% of them "
                     "sharing one"
                     % (facet.note, proposal.covered, proposal.groups,
                        "" if proposal.groups == 1 else "s",
                        proposal.share * 100))
        top = found.groups(facet.group_by).most_common(4)
        if top and facet.group_by not in ("kind", "format"):
            lines.append(";   largest: %s" % ", ".join(
                "%s (%d)" % (name, count) for name, count in top))
        for kind in kinds[:4]:
            label = _rule_name(facet, found)
            if len(kinds[:4]) > 1:
                label += " (%s)" % kind
            lines.append("[rule: %s]" % label)
            lines.append("when = %s" % _when_for(facet, kind))
            lines.append("into = %s" % destination(found, facet, kind))
            if facet.rename:
                lines.append("as   = %s" % facet.rename)
            if facet.min_confidence is not None:
                lines.append("min_confidence = %g" % facet.min_confidence)
            lines.append("")

    lines += [
        "; ---------------------------------------------------------------",
        "; The catch-alls. Everything above is a better answer than these,",
        "; and they are marked `holding = yes`, which means a file they",
        "; placed can be promoted out later: when enough more files arrive",
        "; for a pattern to show, `auto-sort regroup` moves the ones already",
        "; filed here into it, without anybody putting them back in",
        "; Downloads first.",
        "; and these exist so that nothing is left where it was: a sorter",
        "; that keeps back what it did not recognise has not emptied",
        "; anything. Dated, because an undated holding folder becomes the",
        "; problem it was meant to solve.",
        "; ---------------------------------------------------------------",
        "",
    ]
    for kind, count in found.kinds.most_common():
        if not kind:
            continue
        lines.append("; %d item%s" % (count, "" if count == 1 else "s"))
        lines.append("[rule: remaining %s]" % kind)
        lines.append("when = kind = %s" % kind)
        lines.append("into = %s"
                     % userdirs.short(os.path.join(
                         catch_all_root(found, kind), "{added:%Y-%m}")))
        lines.append("holding = yes")
        lines.append("")

    lines += [
        "; And anything at all that reached here, which should be nothing.",
        "[rule: anything left]",
        "when = name is set",
        "into = %s" % userdirs.short(os.path.join(
            catch_all_root(found, "unknown"), "{added:%Y-%m}")),
        "holding = yes",
        "",
    ]

    if found.opaque:
        placed = found.opaque - found.opaque_unplaceable
        lines += [
            "; %d items are named after their own checksum or a site's"
            % found.opaque,
            "; internal id, so they carry no artist, no title and no date.",
        ]
        if placed:
            lines.append("; %d of them at least name the service they came"
                         % placed)
            lines.append("; from, which a rule above files them by.")
        if found.opaque_unplaceable:
            lines.append("; %d have no handle of any kind. They still leave"
                         % found.opaque_unplaceable)
            lines.append("; this folder -- a catch-all takes them -- but only")
            lines.append("; something that looks at the content could do")
            lines.append("; better than a dated holding folder.")
        lines.append("")

    if rejected:
        lines.append("; Considered and not proposed:")
        for proposal in rejected:
            lines.append(";   %-16s %s" % (proposal.key, proposal.reason))
        lines.append("")
    return "\n".join(lines)


def _rule_name(facet, found):
    if facet.key == "site-uploader":
        sites = ", ".join(name for name, _c in found.sites.most_common(3))
        return "art by uploader" + (" (%s)" % sites if sites else "")
    return {"source": "by service", "camera": "photographs",
            "screenshot": "screenshots", "series": "episodes",
            "music": "tagged music", "paperwork": "paperwork by name",
            "duration": "video by length"}.get(facet.key, facet.key)


def _size(count):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return "%.1f %s" % (count, unit)
        count /= 1024.0
    return str(count)


def _short(path):
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path.startswith(home) else path
