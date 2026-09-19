"""What is known about an item, where each fact came from, and how sure it is.

Every decision auto-sort makes is made unattended, on somebody else's files,
and will be questioned later — usually by somebody looking at a file that
ended up somewhere surprising. So a fact is never just a value. It carries the
reader that established it and a confidence, and both survive into the log.

Three behaviours here do real work:

**A weaker source never overwrites a stronger one.** The extension says `.mp3`
and the header says FLAC; the header wins, and the disagreement is kept rather
than discarded, because "the extension lies" is itself something rules match on.

**Agreement raises confidence.** An artist name from an ID3 tag is strong. The
same artist name also parsed out of the filename makes it stronger, because two
independent readers would have to be wrong in the same direction. This is the
only way a filename-derived fact ever becomes trustworthy.

**Nothing is invented.** A fact that could not be established is absent, not
empty, not zero, not "unknown". Rules treat absence as "no match", so a
missing ffprobe narrows what can fire instead of making it fire wrongly.
"""

from __future__ import annotations

# Confidence bands. Named because a bare 0.7 in a reader tells nobody anything,
# and because the bands are what `min_confidence` in a rules file is choosing
# between.
CERTAIN = 1.0    # a magic number; a field parsed out of a header
STRONG = 0.85    # a container's own metadata; an extension no-one else uses
LIKELY = 0.70    # an extension agreeing with nothing; a distinctive name shape
WEAK = 0.45      # a keyword in a filename; an inference from size or duration

BANDS = ((CERTAIN, "certain"), (STRONG, "strong"),
         (LIKELY, "likely"), (WEAK, "weak"))


def band(confidence):
    """The name of the weakest band this confidence still clears."""
    for floor, name in BANDS:
        if confidence >= floor:
            return name
    return "guess"


class Fact(object):
    """One thing known about an item.

    `source` is the reader that established it — "signature", "extension",
    "exif", "id3", "name:screenshot", "wherefroms". It is shown verbatim in
    `explain` output, so it reads as an explanation rather than an identifier.
    """

    __slots__ = ("name", "value", "source", "confidence", "corroborated_by")

    def __init__(self, name, value, source, confidence):
        self.name = name
        self.value = value
        self.source = source
        self.confidence = confidence
        self.corroborated_by = []

    def corroborate(self, source):
        """Another reader found the same value. Move confidence toward certain.

        Halving the remaining distance rather than adding a constant keeps
        three weak agreeing readers from ever reaching the certainty of one
        parsed header field, which is the correct ordering: agreement between
        guesses is still guessing, just less badly.
        """
        self.corroborated_by.append(source)
        self.confidence = min(CERTAIN, self.confidence
                              + (CERTAIN - self.confidence) * 0.5)

    def __repr__(self):
        return "Fact(%s=%r from %s at %.2f)" % (
            self.name, self.value, self.source, self.confidence)


class Conflict(object):
    """Two readers that disagreed, kept for the log and for rules to match."""

    __slots__ = ("name", "kept", "rejected", "rejected_source")

    def __init__(self, name, kept, rejected, rejected_source):
        self.name = name
        self.kept = kept
        self.rejected = rejected
        self.rejected_source = rejected_source

    def __str__(self):
        return "%s: kept %r (%s), rejected %r (%s)" % (
            self.name, self.kept.value, self.kept.source,
            self.rejected, self.rejected_source)


class Record(object):
    """Everything established about one item, in the order it was learnt.

    Insertion order is preserved because `explain` prints the record as a
    narrative — the cheap facts first, then whatever the readers added — and a
    sorted or grouped dump loses the sense of how the conclusion was reached.
    """

    def __init__(self, path=None):
        self.path = path
        self._facts = {}
        self._order = []
        self.conflicts = []
        self.notes = []          # sentences for a person, not for rules
        self.readers = []        # readers that ran, in order, for explain

    # -- writing ----------------------------------------------------------

    def set(self, name, value, source, confidence):
        """Record a fact. Returns the Fact now held under that name.

        Values that are None or empty strings are dropped rather than stored:
        a reader that found nothing must leave the fact absent, so that rules
        keyed on it decline instead of matching an empty value.
        """
        if value is None or value == "" or value == ():
            return self._facts.get(name)

        existing = self._facts.get(name)
        if existing is None:
            fact = Fact(name, value, source, confidence)
            self._facts[name] = fact
            self._order.append(name)
            return fact

        if _same(existing.value, value):
            existing.corroborate(source)
            return existing

        if confidence > existing.confidence:
            self.conflicts.append(
                Conflict(name, Fact(name, value, source, confidence),
                         existing.value, existing.source))
            replacement = Fact(name, value, source, confidence)
            self._facts[name] = replacement
            return replacement

        self.conflicts.append(Conflict(name, existing, value, source))
        return existing

    def note(self, sentence):
        self.notes.append(sentence)

    def reader_ran(self, name, detail=""):
        self.readers.append((name, detail))

    # -- reading ----------------------------------------------------------

    def has(self, name):
        return name in self._facts

    def value(self, name, default=None):
        fact = self._facts.get(name)
        return default if fact is None else fact.value

    def fact(self, name):
        return self._facts.get(name)

    def confidence(self, name, default=0.0):
        fact = self._facts.get(name)
        return default if fact is None else fact.confidence

    def source(self, name, default=None):
        fact = self._facts.get(name)
        return default if fact is None else fact.source

    def names(self):
        return list(self._order)

    def items(self):
        return [(name, self._facts[name]) for name in self._order]

    def as_dict(self):
        """Plain values, for a rules engine or a log row."""
        return dict((name, self._facts[name].value) for name in self._order)

    def __contains__(self, name):
        return name in self._facts

    def __len__(self):
        return len(self._facts)

    def __repr__(self):
        return "Record(%s, %d facts)" % (self.path, len(self._facts))


def _same(a, b):
    """Whether two readers found the same thing, allowing for presentation.

    Tags are written by people and by twelve different taggers. "The Beatles"
    and "the beatles" are corroboration, not conflict, and treating them as a
    disagreement would throw away the agreement that makes a filename-derived
    fact usable.
    """
    if a == b:
        return True
    if isinstance(a, str) and isinstance(b, str):
        return _flatten(a) == _flatten(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, bool) or isinstance(b, bool):
            return bool(a) == bool(b)
        scale = max(abs(a), abs(b), 1.0)
        return abs(a - b) / scale < 0.02     # 2% apart is the same duration
    return False


def _flatten(text):
    return "".join(ch for ch in text.lower() if ch.isalnum())
