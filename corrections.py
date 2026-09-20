"""Noticing when somebody moved a file back, and learning from it.

Every placement this tool makes is a claim, and the ledger records it. So the
strongest signal available is free: if a file is no longer where auto-sort put
it, the person disagreed, and they said where it should have gone by putting
it there. No feedback prompt, no training step, no asking anybody to grade
anything -- just the difference between the ledger and the disk.

This is the one learning signal that is about *this person's* judgement rather
than about the files. A convention learnt from filenames says what a folder
contains; a correction says what its owner wants, which is the thing no amount
of reading headers will ever produce.

**It proposes, it does not adjust.** A correction becomes a candidate rule
with the count behind it, shown to somebody who says yes or no. Silently
changing where files go because of something inferred from a folder is exactly
the behaviour that makes a tool impossible to trust, and it is also how a
single tidy-up afternoon teaches the wrong lesson permanently.

The other half is just as useful and needs no inference at all: which rules
get overridden. A rule corrected eleven times is a rule that is wrong, and
saying so is worth more than guessing at a replacement for it.
"""

from __future__ import annotations

import collections
import json
import os

import mover

# Facts that are unique per file, or are plumbing. Proposing a rule keyed on
# one of these produces a rule that matches exactly one file.
NOT_PREDICTIVE = frozenset((
    "name", "stem", "path", "dir", "size", "modified", "added", "age",
    "happened", "taken", "digitised", "created", "content_created",
    "content_modified", "sha256", "post_id", "content_hash_name", "gps",
    "description", "comment", "doc_title", "post_title", "song_title",
    "from_url", "referrer", "name_date", "name_time", "downloaded",
    "duration", "width", "height", "aspect", "megapixels", "bitrate",
    "samplerate", "iso", "focal_length", "aperture", "serial", "checksum",
    "pages", "words", "lines", "bpm", "posted_epoch", "tags", "keywords",
    "version", "sequence_prefix", "sequence_number", "source_id",
    "release_year", "year", "track", "disc", "episode", "title",
))

# How many files have to agree before a correction is a preference.
MIN_SUPPORT = 3
# How exclusively a fact has to point at one folder. Below this it is a fact
# that turns up everywhere and predicts nothing.
MIN_PRECISION = 0.8

MAX_INDEX_ENTRIES = 400000


def _index(roots):
    """(basename, size) -> [paths], for finding a file that has moved.

    Name and size rather than content, because hashing a whole tree to find
    out whether anything moved would cost more than the sorting did. A
    candidate is confirmed by hash, so the cheap key only has to be cheap and
    nearly unique, not certain.
    """
    found = collections.defaultdict(list)
    entries = 0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [name for name in subdirectories
                                 if not name.startswith(".")]
            for name in names:
                path = os.path.join(directory, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                found[(name, size)].append(path)
                entries += 1
                if entries >= MAX_INDEX_ENTRIES:
                    return found
    return found


def detect(journal, roots=(), source_root=None, verify=True):
    """Find placements that are no longer where they were put.

    Returns [(move row, outcome, where it is now or None)]. Outcomes are
    `moved` when the file was found somewhere else and `missing` when it was
    not found at all -- deleted, renamed beyond recognition, or on a volume
    that is not mounted, which are not distinguishable from here and are all
    reasons not to guess.
    """
    placed = journal.placed_moves(source_root)
    gone = [row for row in placed
            if row["destination"] and not os.path.lexists(row["destination"])]
    if not gone:
        return []

    search_roots = list(roots)
    for row in gone:
        if row["source_root"] and row["source_root"] not in search_roots:
            search_roots.append(row["source_root"])
    index = _index(search_roots)

    results = []
    for row in gone:
        name = os.path.basename(row["destination"])
        candidates = [path for path in index.get((name, row["size"]), [])
                      if path != row["destination"]]
        found_at = None
        if len(candidates) == 1:
            found_at = candidates[0]
        elif candidates:
            found_at = _disambiguate(candidates, row["sha256"])
        if found_at and verify and row["sha256"]:
            try:
                if mover.hash_path(found_at) != row["sha256"]:
                    found_at = None
            except OSError:
                found_at = None
        outcome = "moved" if found_at else "missing"
        journal.record_correction(row["id"], row["rule_name"],
                                  row["destination"], found_at, outcome,
                                  row["facts_json"])
        results.append((row, outcome, found_at))
    return results


def _disambiguate(candidates, expected_hash):
    if not expected_hash:
        return None
    for path in candidates:
        try:
            if mover.hash_path(path) == expected_hash:
                return path
        except OSError:
            continue
    return None


class Preference(object):
    """A folder somebody kept choosing, and the fact that predicts it."""

    def __init__(self, fact, value, folder, support, precision, examples):
        self.fact = fact
        self.value = value
        self.folder = folder
        self.support = support
        self.precision = precision
        self.examples = examples

    def rule(self, name=None):
        lines = [
            "; Learnt from %d file%s you moved out of where auto-sort put"
            % (self.support, "" if self.support == 1 else "s"),
            "; them. %.0f%% of everything placed with %s = %s ended up in"
            % (self.precision * 100, self.fact, self.value),
            "; this folder, so it is where you seem to want them.",
        ]
        for example in self.examples[:3]:
            lines.append(";   %s" % os.path.basename(example))
        lines.append("[rule: %s]" % (name or self.suggested_name()))
        lines.append("when = %s = %s" % (self.fact, self.value))
        lines.append("into = %s" % self.folder)
        return "\n".join(lines)

    def suggested_name(self):
        return "you moved %s = %s here" % (self.fact, self.value)

    def __repr__(self):
        return "Preference(%s=%s -> %s, %d files)" % (
            self.fact, self.value, self.folder, self.support)


def population(journal, source_root=None):
    """How often each (fact, value) appears across everything placed.

    The denominator, and the whole difference between a useful suggestion and
    a confident wrong one. Measured only against corrections, `alpha = True`
    predicts a folder perfectly -- every file moved out of Images happened to
    be a PNG with an alpha channel. So did every screenshot that was left
    exactly where it was put. A fact that is true of the files somebody moved
    *and* of the files they did not move predicts nothing, and only the wider
    population shows that.
    """
    counts = collections.Counter()
    for row in journal.placed_moves(source_root):
        for fact, value in _facts_of(row):
            counts[(fact, value)] += 1
    return counts


def induce(rows, among=None, min_support=MIN_SUPPORT,
           min_precision=MIN_PRECISION):
    """Facts that predict where somebody actually put things.

    Deliberately the simplest thing that can work: count how often each
    (fact, value) appears among the files that were corrected into one
    folder, against how often it appears among everything that was placed at
    all. A fact holding for most of the files that carry it, and pointing at
    one folder, is a preference. Anything else is a coincidence.

    No weighting, no model, nothing that has to be retrained. Somebody has to
    be able to read the result and agree or disagree with it, and a rule that
    says "these five files all came from furaffinity and you moved all five
    here" can be argued with in a way that a learnt weight cannot.
    """
    by_pair = collections.Counter()
    by_pair_folder = collections.Counter()
    examples = collections.defaultdict(list)

    for row in rows:
        folder = _folder_of(row)
        if not folder:
            continue
        for fact, value in _facts_of(row):
            by_pair[(fact, value)] += 1
            by_pair_folder[(fact, value, folder)] += 1
            examples[(fact, value, folder)].append(row["found_at"])

    preferences = []
    for (fact, value, folder), support in by_pair_folder.items():
        if support < min_support:
            continue
        # Against everything placed when that is known, and against the
        # corrections alone only when it is not.
        total = (among or by_pair).get((fact, value)) or by_pair[(fact, value)]
        precision = support / float(total)
        if precision < min_precision:
            continue
        preferences.append(Preference(fact, value, folder, support,
                                      precision,
                                      examples[(fact, value, folder)]))

    # Strongest first, and only one rule per folder: the best explanation of
    # why those files went there, not five overlapping ones.
    preferences.sort(key=lambda item: (-item.support, -item.precision))
    seen = set()
    kept = []
    for preference in preferences:
        if preference.folder in seen:
            continue
        seen.add(preference.folder)
        kept.append(preference)
    return kept


def overridden_rules(rows):
    """Which rules got corrected, most-corrected first.

    Needs no inference at all, and is frequently the more useful half: a rule
    overridden eleven times is wrong, and saying so beats guessing at a
    replacement.
    """
    counts = collections.Counter()
    for row in rows:
        if row["rule_name"]:
            counts[row["rule_name"]] += 1
    return counts.most_common()


def _folder_of(row):
    path = row["found_at"] if "found_at" in row.keys() else None
    return os.path.dirname(path) if path else None


def _facts_of(row):
    try:
        facts = json.loads(row["facts_json"] or "{}")
    except (TypeError, ValueError):
        return []
    pairs = []
    for fact, value in facts.items():
        if fact in NOT_PREDICTIVE or value is None or value == "":
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value)
        if len(text) > 60:
            continue
        pairs.append((fact, text))
    return pairs
