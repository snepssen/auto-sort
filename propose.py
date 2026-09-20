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

    `needs` are the facts that must be present; `into` is the destination
    written in terms of them. `precedence` orders the generated file, lowest
    first, so that specific groupings are tried before general ones.
    """

    def __init__(self, key, needs, into, precedence, when=None, note="",
                 group_by=None, rename=None, min_confidence=None):
        self.key = key
        self.needs = tuple(needs)
        self.into = into
        self.precedence = precedence
        self.when = when or " and ".join("%s is set" % f for f in needs)
        self.note = note
        self.group_by = group_by or (needs[0] if needs else None)
        self.rename = rename
        self.min_confidence = min_confidence


FACETS = (
    Facet("site-uploader", ("site", "uploader"),
          "{root}/Art/{uploader}", 10,
          when="uploader is set",
          note="one folder per artist, which is what the filenames carry",
          group_by="uploader"),
    Facet("site-only", ("site",),
          "{root}/Art/_{site}", 20,
          when="site is set and uploader is unset",
          note="downloaded from a site that named the file but not the artist",
          group_by="site"),
    Facet("camera", ("camera",),
          "{root}/Photos/{taken:%Y}/{camera}", 30,
          when="kind = image and camera is set and taken is set",
          note="photographs, by the body that took them",
          group_by="camera"),
    Facet("screenshot", ("capture",),
          "{root}/Screenshots/{added:%Y-%m}", 40,
          when="capture = screenshot",
          note="screenshots", group_by="capture"),
    Facet("series", ("title", "season"),
          "{root}/Series/{title}/Season {season}", 50,
          when="kind = video and episode is set",
          note="episodes, by series", group_by="title"),
    Facet("music", ("artist", "album"),
          "{root}/Music/{artist}/{album}", 60,
          when="kind = audio and artist is set and album is set",
          note="tagged music", group_by="artist",
          rename="{track:02} {song_title}.{ext}"),
    Facet("paperwork", ("paperwork",),
          "{root}/Documents/{paperwork}", 70,
          when="kind = document and paperwork is set",
          note="documents whose name says what they are",
          group_by="paperwork", min_confidence=0.4),
    Facet("source", ("source",),
          "{root}/From/{source}", 25,
          when="source is set",
          note="by the service it was downloaded from, which the operating "
               "system recorded at the time",
          group_by="source"),
    Facet("duration", ("duration_class",),
          "{root}/Video/{duration_class}", 90,
          when="kind = video and duration is set",
          note="video, split by length", group_by="duration_class"),
    Facet("format", ("kind", "format"),
          "{root}/{kind}/{format}", 100,
          when="kind is set",
          note="everything else, by what it is", group_by="format"),
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
    def __init__(self, facet, covered, groups, median, reason):
        self.facet = facet
        self.covered = covered
        self.groups = groups
        self.median = median
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

        import shapes
        share = shapes.concentration(counter.values())

        reason = None
        if groups == 0:
            reason = "nothing in this folder has %s" % facet.group_by
        elif coverage < MIN_COVERAGE:
            reason = ("only %.1f%% of items have %s"
                      % (coverage * 100, ", ".join(facet.needs)))
        elif groups > MAX_GROUPS:
            reason = "would make %d folders" % groups
        elif share < MIN_CONCENTRATION and facet.key != "format":
            reason = ("%.0f%% of them would be alone in a folder"
                      % ((1 - share) * 100))
        results.append(Proposal(facet, covered, groups, median, reason))
    return results


def default_root(surveyed):
    """Where a proposal should file things by default: beside them.

    Not `~/Sorted`. A move inside one filesystem is a rename -- instant, and
    atomic. A move across one is a copy, a hash, and a delete, which on a USB
    stick holding a hundred gigabytes is the difference between a second and
    an afternoon, and introduces the only step in this program that can lose
    data. Sorting a folder into a subfolder of itself keeps every move on the
    volume the files are already on, which is also what somebody tidying an
    external drive means.
    """
    return os.path.join(_short(surveyed), "Sorted")


def render(found, proposals, root_token=None):
    """The proposed rules file, as text, with the counts that justified it."""
    root_token = root_token or default_root(found.root)
    accepted = [p for p in proposals if p.accepted]
    accepted.sort(key=lambda p: p.facet.precedence)
    rejected = [p for p in proposals if not p.accepted]

    lines = [
        "; auto-sort rules proposed from %s" % found.root,
        "; %s items, %s files, %s" % (
            "{:,}".format(found.items), "{:,}".format(found.files),
            _size(found.bytes)),
        ";",
        "; Every rule below is here because this folder turned out to need",
        "; it -- the counts are what was actually found, not estimates. Read",
        "; it, delete what you disagree with, then:",
        ";",
        ";   auto-sort check-rules THIS_FILE",
        ";   auto-sort sort %s --rules THIS_FILE" % _short(found.root),
        "",
        "[settings]",
        "dry_run        = yes",
        "unsorted       = leave",
        "on_collision   = suffix",
        "min_confidence = 0.6",
        "settle_seconds = 3",
        "",
        "[watch]",
        "folders = %s" % _short(found.root),
        "depth   = 3",
        "",
    ]

    if not accepted:
        lines += ["; Nothing in this folder grouped well enough to propose a",
                  "; rule for. See the report for what was considered.", ""]

    for index, convention in enumerate(found.conventions, 1):
        source = convention_source(found, convention)
        values = convention.fields[convention.category][2]
        lines.append("; A naming convention these files share, learnt from")
        lines.append("; them rather than configured: %d files, %d distinct"
                     % (convention.count, convention.groups))
        lines.append("; values in one field, %.0f%% of them sharing a folder."
                     % (convention.concentration * 100))
        lines.append(";   values: %s" % ", ".join(
            name for name, _count in values.most_common(6)))
        lines.append("; Nothing here knows what those values mean. If you can")
        lines.append("; see it, rename the capture and the folder follows.")
        name = "%s names" % source if source else "name pattern %d" % index
        lines.append("[rule: %s]" % name)
        if source:
            # Every file that taught this pattern came from one service, so
            # the rule says so and cannot misfire on a lookalike.
            lines.append("when    = source = %s" % source)
        else:
            lines.append("when    = kind in image, video, audio")
        lines.append("extract = stem re %s" % convention.pattern("group"))
        lines.append("into    = %s/%s/{group}"
                     % (root_token, source or "By name"))
        lines.append("")

    for proposal in accepted:
        facet = proposal.facet
        lines.append("; %s -- %d items across %d folder%s, median %g each"
                     % (facet.note, proposal.covered, proposal.groups,
                        "" if proposal.groups == 1 else "s",
                        proposal.median))
        top = found.groups(facet.group_by).most_common(4)
        if top and facet.group_by not in ("kind", "format"):
            lines.append(";   largest: %s" % ", ".join(
                "%s (%d)" % (name, count) for name, count in top))
        lines.append("[rule: %s]" % _rule_name(facet, found))
        lines.append("when = %s" % facet.when)
        lines.append("into = %s" % facet.into.replace("{root}", root_token))
        if facet.rename:
            lines.append("as   = %s" % facet.rename)
        if facet.min_confidence is not None:
            lines.append("min_confidence = %g" % facet.min_confidence)
        lines.append("")

    if found.opaque:
        placed = found.opaque - found.opaque_unplaceable
        lines += [
            "; %d items are named after their own checksum or a site's"
            % found.opaque,
            "; internal id, so they carry no artist, no title and no date.",
        ]
        if placed:
            lines.append("; %d of them at least name the site they came from,"
                         % placed)
            lines.append("; which a rule above files them by.")
        if found.opaque_unplaceable:
            lines += [
                "; %d have no handle of any kind. No further filename work"
                % found.opaque_unplaceable,
                "; will change that: this is where cheap processing ends and",
                "; looking at the content would have to begin. They are left",
                "; in place rather than gathered, so they stay findable.",
                "[rule: nothing to go on]",
                "when = opaque = yes and site is unset",
                "mode = leave",
            ]
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
    return {"site-only": "downloads by site", "camera": "photographs",
            "screenshot": "screenshots", "series": "episodes",
            "music": "tagged music", "paperwork": "paperwork by name",
            "host": "by source site", "duration": "video by length",
            "format": "everything else"}.get(facet.key, facet.key)


def _size(count):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return "%.1f %s" % (count, unit)
        count /= 1024.0
    return str(count)


def _short(path):
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path.startswith(home) else path
