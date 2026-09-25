"""Learning a folder's naming conventions instead of being told them.

A table of site patterns does not scale and cannot be finished. There is
always another site, and filling the table means somebody going and
downloading junk from each one first. But the conventions are *discoverable*,
because a convention is by definition a thing that repeats.

The observation this rests on is narrow and reliable:

    In a filename written by software, a field that repeats across many files
    is a category, and a field that is different in every file is an
    identifier.

That is the whole idea. Given forty files shaped `<digits>.<word>_<rest>`,
the digits are all different -- an identifier -- and the words repeat across
several files each -- a category. Nothing here knows what FurAffinity is. It
knows that position two is worth a folder and position one is not, and it
knows it from counting.

What comes out is a regular expression with a named group on the category,
written into the rules file as an ordinary `extract`. So a convention the
survey worked out is visible, editable, and indistinguishable from one
somebody typed. A tool that silently learned a filing scheme nobody could
read would be a tool nobody should run.

**Hyphens are not separators here.** `codyblue-731` and `multyashka-sweet`
are single names, and splitting on the hyphen turns one convention into
several and finds none of them.
"""

from __future__ import annotations

import collections
import re
import unicodedata

# How many leading fields to look at. Past this the tail is usually a free
# title with a varying number of words in it, which never clusters.
FIELDS = 3

# How many times something has to happen before it is a pattern rather than
# a coincidence. Two is an accident -- any two files share something -- and
# waiting for six means a household with one of each kind of bill never gets
# a folder for any of them. Three is the smallest number that can show a
# trend, and it is used for every "how often" question in the project so
# that one answer governs all of them.
MIN_OCCURRENCES = 3

# A convention needs this many files before it is one rather than a
# coincidence.
MIN_SUPPORT = MIN_OCCURRENCES

# A field is a category when its values repeat this much: distinct values
# over occurrences, so 1.0 is "different every time" and 0.1 is "ten files
# each".
MAX_CATEGORY_RATIO = 0.6

# The ceiling the computer owner's own name is held to instead. See
# `owner.py`: a name at the top of a pile of post describes the pile.
OWNER_NAME_RATIO = 0.1
MIN_CONCENTRATION = 0.4         # of files, in values shared by MIN_OCCURRENCES
MIN_CATEGORY_VALUES = 3
MIN_CATEGORY_LENGTH = 3
MIN_CATEGORY_LETTERS = 0.5      # of the characters, on average

_SEPARATORS = ".…_ "
_SPLIT = re.compile(r"[.…_ ]")
_HEX = re.compile(r"^[0-9a-f]+$")


def tokenise(stem, fields=FIELDS):
    """[(separator before, text)] for the leading fields, plus the tail.

    The separator of the first field is the empty string. The tail is
    whatever is left after `fields` splits, and is never treated as a field
    because its shape varies.
    """
    parts = []
    cursor = 0
    while len(parts) < fields:
        match = _SPLIT.search(stem, cursor)
        if match is None:
            break
        parts.append((stem[cursor:match.start()], match.group()))
        cursor = match.end()
    tail = stem[cursor:]
    tokens = []
    previous = ""
    for text, separator in parts:
        tokens.append((previous, text))
        previous = separator
    # The tail is whatever is left, and it is never classified. A title has a
    # different number of words in every file, so classifying it splits one
    # convention into a dozen and finds none of them -- which is exactly what
    # happened to FurAffinity's names on the first attempt.
    tokens.append((previous, tail))
    return tokens


TAIL = "tail"


def classify(text):
    """The coarse class of one field.

    Coarse on purpose. `flaich`, `codyblue-731` and `multyashka-sweet` have
    to land in the same class or the convention they share is invisible, so
    anything containing a letter and no space is simply a word.
    """
    if not text:
        return "empty"
    if text.isdigit():
        return "num%d" % len(text)
    lowered = text.lower()
    if _HEX.match(lowered) and len(lowered) in (32, 40, 64):
        return "hex%d" % len(lowered)
    if any(character.isalpha() for character in text):
        return "word"
    return "other"


def signature(stem, fields=FIELDS):
    """A hashable description of a filename's leading shape."""
    tokens = tokenise(stem, fields)
    shape = []
    for index, (separator, text) in enumerate(tokens):
        last = index == len(tokens) - 1
        shape.append((separator, TAIL if last else classify(text)))
    return tuple(shape)


def concentration(counts):
    """The share of files that land in a folder with company.

    The right test for "is this worth a folder", and better than either the
    mean or the median. Forty-three pictures by twenty-one artists has a
    median of one, which sounds like failure -- and yet thirty of those files
    sit in folders holding three or more, which is a real structure that will
    only firm up as more arrive. The pathological case this has to reject is
    a thousand files with a thousand distinct values, and that scores zero
    here while scoring exactly the same median as the good case.
    """
    total = sum(counts)
    if not total:
        return 0.0
    return sum(count for count in counts
               if count >= MIN_OCCURRENCES) / float(total)


class Convention(object):
    """One naming shape found in a corpus, and what repeats inside it."""

    def __init__(self, shape, count, fields, category, examples,
                 members=()):
        self.shape = shape
        self.count = count
        self.fields = fields            # [(separator, class, Counter)]
        self.category = category        # index into fields, or None
        self.examples = examples
        self.examples_all = tuple(members)

    @property
    def groups(self):
        return len(self.fields[self.category][2]) if self.category is not None \
            else 0

    @property
    def concentration(self):
        if self.category is None:
            return 0.0
        return concentration(self.fields[self.category][2].values())

    @property
    def median(self):
        if self.category is None:
            return 0
        counts = sorted(self.fields[self.category][2].values())
        middle = len(counts) // 2
        if not counts:
            return 0
        if len(counts) % 2:
            return counts[middle]
        return (counts[middle - 1] + counts[middle]) / 2.0

    def pattern(self, name="group"):
        """A regular expression anchored at the start, capturing the category.

        Character classes are derived from the values actually seen, not from
        the class name, so the pattern is as tight as the evidence allows and
        no tighter.
        """
        pieces = ["^"]
        for index, (separator, kind, values) in enumerate(self.fields):
            if separator:
                pieces.append(re.escape(separator))
            body = _character_class(values, kind)
            if index == self.category:
                pieces.append("(?P<%s>%s)" % (name, body))
            else:
                pieces.append(body)
        return "".join(pieces)

    def describe(self):
        parts = []
        for index, (separator, kind, values) in enumerate(self.fields):
            label = kind if index != self.category else "%s*" % kind
            parts.append((separator or "") + label)
        return "".join(parts)


def _looks_machine_made(shape):
    """Whether a convention was written by software or typed by a person.

    The tell is an identifier: a long run of digits or a checksum. Software
    that names files puts one in, because it has to guarantee uniqueness.
    People do not, because they are naming a thing rather than distinguishing
    it from nine hundred others.

    Without this test the strongest "conventions" in a real folder are
    somebody's own song titles, and the tool proposes filing music by its
    first word.
    """
    for _separator, kind in shape:
        if kind.startswith("hex"):
            return True
        if kind.startswith("num") and int(kind[3:] or 0) >= 6:
            return True
    return False


def _character_class(values, kind):
    """A class covering every value seen at one position, and a length."""
    if kind == TAIL:
        return ".*"
    if kind.startswith("num"):
        lengths = set(len(value) for value in values)
        if len(lengths) == 1:
            return r"\d{%d}" % lengths.pop()
        return r"\d{%d,%d}" % (min(lengths), max(lengths))
    if kind.startswith("hex"):
        lengths = set(len(value) for value in values)
        return "[0-9a-fA-F]{%d}" % lengths.pop() if len(lengths) == 1 \
            else "[0-9a-fA-F]+"

    characters = set()
    for value in values:
        characters.update(value)
    body = ""
    if any(character.islower() for character in characters):
        body += "a-z"
    if any(character.isupper() for character in characters):
        body += "A-Z"
    if any(character.isdigit() for character in characters):
        body += "0-9"
    extras = sorted(character for character in characters
                    if not character.isalnum())
    # A literal hyphen has to be last inside a class, and the rest are
    # escaped rather than reasoned about.
    tail = "".join(re.escape(character) for character in extras
                   if character != "-")
    if "-" in extras:
        tail += "-"
    return "[%s%s]+" % (body or "^%s" % re.escape(_SEPARATORS), tail)


def _is_category(kind, values, total):
    """Whether one field repeats enough, and looks enough like a name."""
    distinct = len(values)
    if distinct < MIN_CATEGORY_VALUES or distinct >= total:
        return False
    if distinct / float(total) > MAX_CATEGORY_RATIO:
        return False
    if kind.startswith("num") or kind.startswith("hex"):
        # A number that repeats is a year or a page, not a name. Left alone
        # rather than guessed at.
        return False
    sampled = list(values)[:200]
    if not sampled:
        return False
    average_length = sum(len(value) for value in sampled) / float(len(sampled))
    if average_length < MIN_CATEGORY_LENGTH:
        return False
    letters = sum(sum(1 for character in value if character.isalpha())
                  for value in sampled)
    total_characters = sum(len(value) for value in sampled) or 1
    return letters / float(total_characters) >= MIN_CATEGORY_LETTERS


_WORD = re.compile(r"[^\W\d_]{%d,}" % MIN_CATEGORY_LENGTH, re.UNICODE)


def _fold(word):
    """One key for one word, however it was accented or capitalised.

    `Tamás` and `Tamas` are a person written twice, not two people. Counted
    apart they each stayed under the letterhead ceiling and both came back
    as categories; counted together they are what they always were.
    """
    return "".join(
        character for character
        in unicodedata.normalize("NFKD", word.lower())
        if not unicodedata.combining(character))


def _best_spelling(counter):
    """The spelling to name a folder with, out of the ones people wrote.

    Commonest wins. On a tie -- RECHNUNG once, Rechnung once, rechnung once,
    which is ordinary across twenty years of a filing habit -- the
    capitalised form wins, because it is the one somebody would have typed
    had they been naming the folder themselves.
    """
    return sorted(counter.items(),
                  key=lambda pair: (-pair[1],
                                    not pair[0].istitle(),
                                    pair[0].isupper(),
                                    pair[0]))[0][0]


def learn_terms(headings, min_occurrences=MIN_OCCURRENCES,
                max_share=MAX_CATEGORY_RATIO, cap=40, owner=(),
                owner_share=None, person=(), titled=False,
                min_values=MIN_CATEGORY_VALUES):
    """Words that enough documents lead with to be a category they chose.

    This is deliberately not `learn`. That one groups files by the shape of
    their name and then hunts for the field that *varies* within a group,
    which is right for `1234_artist_title.png` and exactly wrong here: the
    word this is looking for is the one that stays the same across a pile of
    bills and differs from the pile of tax letters next to it. Constant
    within its group is the whole signal.

    Two counts decide it, and no vocabulary does. A word heading at least
    `min_occurrences` documents has happened often enough to be a pattern
    rather than a coincidence. A word heading more than `max_share` of them
    is on the letterhead -- somebody's own name, their town, their bank --
    and describes the pile rather than dividing it. What survives both is
    what those documents call themselves, in whatever language they were
    written, including ones nobody involved here can read.

    Returns `[(word, documents)]` ordered by how early the word usually
    appears, or `[]` when there is no structure worth the name. Earliest
    first, rather than commonest first, because a document announces what it
    is before it says where it came from: across a pile of German bills
    `Muenchen` is the commoner word and `Rechnung` is the one at the front,
    and the folders anybody wants are the second kind. Nothing here knows
    which is which -- only where they sat.

    `titled` is for headings: see `_set_as_a_title`. `min_values` is how
    many such words there must be before any of them is trusted; one, to
    ask which words would count if there were enough of them.
    """
    total = len(headings)
    if total < min_occurrences:
        return []
    frequency = collections.Counter()
    bare = collections.Counter()
    spellings = collections.defaultdict(collections.Counter)
    positions = collections.defaultdict(list)
    documents = collections.defaultdict(set)
    for number, heading in enumerate(headings):
        # Counted case-insensitively -- RECHNUNG in a letterhead and
        # Rechnung in a filename are one word -- but the spelling people
        # actually use most is the one that names the folder.
        seen = {}
        for index, word in enumerate(_WORD.findall(heading or "")):
            key = _fold(word)
            seen.setdefault(key, index)
            spellings[key][word] += 1
        # Which of them were standing on their own rather than buried in a
        # path or an address. See `_bare_words`.
        standing = _bare_words(heading)
        for key, index in seen.items():
            frequency[key] += 1
            positions[key].append(index)
            documents[key].add(number)
            if key in standing:
                bare[key] += 1

    ceiling = max(min_occurrences, total * max_share)
    # The owner's own name is at the top of their payslip, their tenancy
    # agreement and their phone bill. It heads fifty documents and divides
    # none of them, and the counting cannot see that -- fifty out of five
    # hundred is the shape of a real category. So it is held to a much
    # lower ceiling. Not banned: a surname is often also a word, and a
    # `Bill` in three documents out of three hundred is the language rather
    # than the letterhead.
    # Folded the same way the headings are, or an accented name never
    # matches the word it is being compared with: `Tamás` folds to `tamas`
    # on one side of the comparison and not the other.
    owner = set(_fold(word) for word in (owner or ()))
    # The owner's real name gets no allowance at all. It was given one at
    # first, so that a surname which is also a word -- Koch, Baker, Bill --
    # could still become a folder, and on a real machine that allowance let
    # the owner's own name through twice: from how they name their CVs, and
    # from 23 documents with their surname at the top. The case it protected
    # was hypothetical and the case it let through was not.
    person = set(_fold(word) for word in (person or ()))
    share = OWNER_NAME_RATIO if owner_share is None else owner_share
    # A share of nothing means no allowance at all: see `propose`, which
    # asks for that for filenames, because a filename is written by the
    # owner and nobody names a file after themselves to say what it is.
    own_ceiling = max(min_occurrences, total * share) if share > 0 else -1
    terms = [(_best_spelling(spellings[key]), count)
             for key, count in frequency.items()
             if key not in person
             and bare[key] >= min_occurrences
             and min_occurrences <= count
             <= (own_ceiling if key in owner else ceiling)
             and _is_a_word(_best_spelling(spellings[key]))
             and _near_the_front(positions[key])
             and (not titled or _set_as_a_title(spellings[key]))]
    if len(terms) < min_values:
        # One word repeating is a letterhead; two is not yet a shape. Three
        # distinct answers is the smallest thing that sorts anything.
        return []
    def rank(pair):
        key = _fold(pair[0])
        where = positions[key]
        return (sum(where) / float(len(where)), -pair[1], key)

    terms.sort(key=rank)
    return _without_boilerplate(terms, documents,
                                positions=positions)[:cap]


# How deep into a heading a word may usually sit and still be what the
# document calls itself. The heading is six words; a word that is normally
# the fourth or later is a detail inside a sentence, not an announcement.
# Measured on one real folder: `Service` sat at position 3 and was never
# once first, `This` at 2, and every genuine category at 0.
MAX_HEADING_POSITION = 3


# A chunk of a heading that is a path, a web address or an email address
# rather than words: the login name in `/Users/tamtor/Documents/...`, the
# host in `sausage@factory`, the domain in `post.example.co.uk`.
# A slash between letters, as in a path -- not after a hyphen, which is how
# German shares the end of a compound: `Lohn-/Gehalts-Abrechnung` is "wage
# and salary statement", not a file, and treating it as one hid a real
# category of payslips.
_AN_ADDRESS = re.compile(r"(?<!-)[/\\]|@|\w\.\w+\.\w")


def _bare_words(heading):
    """The words of a heading that were standing on their own.

    A word that only ever turns up inside a path or an address is an
    identifier rather than a category. A login name is the usual one --
    every occurrence of one in a real folder of 314 documents was inside
    `/Users/<name>/...` and not one was a word -- but the rule is worth
    more than that case: nothing a document *calls itself* is only ever
    found inside a URL.

    This also settles what to do about somebody whose account is named
    after their online moniker rather than themselves. `sausage` is a
    perfectly good username and also a perfectly good thing for a butcher's
    invoice to say at the top, and the difference between the two is not
    the word, it is whether it was ever used as one.
    """
    standing = set()
    for chunk in str(heading or "").split():
        if _AN_ADDRESS.search(chunk):
            continue
        for part in _PART.split(chunk):
            if _generated(part):
                continue
            for word in _WORD.findall(part):
                standing.add(_fold(word))
    return standing


# The pieces of a file name, which uses these where a heading uses spaces.
_PART = re.compile(r"[_.\-]+")
_SWITCH = re.compile(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)", re.UNICODE)
# Letters and digits changing places this often is a machine's identifier.
_GENERATED_SWITCHES = 3


def _generated(part):
    """Is this a token some system generated, rather than a word with a
    number on it?

    `Payslip2019` and `P45` change between letters and digits once;
    `4wT3kE89lew` does it five times. The letters inside an identifier
    like that are not words, and on a real machine three portal downloads
    named with one were proposed a folder called `lew`.
    """
    return len(_SWITCH.findall(part)) >= _GENERATED_SWITCHES


def _set_as_a_title(spellings):
    """Was this word mostly written the way a title is?

    A heading now runs on from the title into the top of the page, and
    prose runs with it: `your` was offered as a category from "YOUR
    ACCOMMODATION AGREEMENT", "Be your glorious self" and "It is your
    choice" -- three documents with nothing in common but a pronoun.
    Titles are set with capitals and sentences are not, in every script
    that has capitals at all; so a word from headings has to carry one in
    most of the places it turns up. `ePayslip` does, and so does every
    German noun. A script with no case passes, having nothing to say.
    """
    total = sum(spellings.values())
    if not total:
        return False
    sample = next(iter(spellings))
    if sample.lower() == sample.upper():
        return True
    capitalised = sum(count for spelling, count in spellings.items()
                      if any(char.isupper() for char in spelling))
    return capitalised * 2 > total


def _near_the_front(positions):
    """A document says what it is before it says anything else about
    itself. Not "always first" -- a letter that leads with its sender still
    names itself on the next line -- but not buried either."""
    if not positions:
        return False
    middle = sorted(positions)[len(positions) // 2]
    return middle < MAX_HEADING_POSITION


def _is_a_word(word):
    """Is this a word, or a font's glyph numbers read as characters?

    A little soup survives the filtering `pdftext` does, and one surviving
    word is enough to name a folder `ÍäÎá`. So the same question is asked
    again here, of one word, and the sharper form it takes is about script
    rather than about byte values.

    **No language written in the Latin alphabet spells a word entirely out
    of accented letters.** `München`, `Számla` and `Đường` all have plain
    letters in them; `ÍäÎá` does not, because it is four glyph numbers that
    happened to land in the accented range. Greek, Cyrillic, Hebrew, Thai
    and Japanese words have no plain Latin letters either -- and are not
    Latin, which is what tells the two apart.

    Anything with a symbol, a currency sign or an ordinal mark inside it is
    refused outright: no script puts those in the middle of a word.
    """
    if not word:
        return False
    # Not `Lo`. That category holds every Chinese, Japanese, Hebrew and
    # Thai letter as well as the ordinal marks, and refusing it would
    # refuse the word for "invoice" in several languages.
    if any(unicodedata.category(char)
           in ("So", "Sk", "Sc", "Sm", "Cf", "Co", "Cn", "No")
           for char in word):
        return False
    letters = [char for char in word if char.isalpha()]
    if not letters:
        return False
    latin = 0
    plain = 0
    for char in letters:
        if "a" <= char.lower() <= "z":
            plain += 1
            latin += 1
            continue
        try:
            if unicodedata.name(char).startswith("LATIN "):
                latin += 1
        except ValueError:
            pass
    written_in_latin = latin >= len(letters) * 0.8
    return plain > 0 if written_in_latin else True


def _without_boilerplate(terms, documents, overlap=0.9, positions=None):
    """Drop a word that only ever appears alongside an earlier one.

    A payslip says `Loonbrief` at the top and then `Kantoor`, `afhaling`
    and `nummer` further down, on all forty of them and nowhere else.
    Counting alone cannot tell those apart -- each is in exactly forty
    documents -- but the *same* forty is the giveaway. A word whose
    documents are all already accounted for by a word that comes before it
    is part of that word's template, not a category beside it.

    This is the same judgement `check-rules` makes after a few hundred files
    have moved, made here before any of them move.
    """
    # A word yields to one that heads every document it heads *and more*.
    # German payslips open with `Programmversion: zvoove Payroll ...` -- the
    # payroll software stamping its own version on the page -- and the
    # earlier word won for being earlier, so four payslips got a folder
    # named after the software. `Payroll` heads those four and every other
    # payslip besides. The name of the program that printed a document
    # belongs to one series; the kind of document spans several, which is
    # the whole difference between them and does not need a vocabulary.
    #
    # But only to a wider word that sits at least as near the front. The
    # town a pile of letters was posted from is wider than every kind of
    # letter in the pile -- `Muenchen` heads the bills *and* the tax
    # assessments -- and it sits at the end of the heading every time. A
    # kind of document leads; `Payroll` leads the other payslips it heads.
    positions = positions or {}

    def middle(word):
        where = positions.get(_fold(word)) or [0]
        return sorted(where)[len(where) // 2]

    wider = set()
    for word, _count in terms:
        mine = documents[_fold(word)]
        for other, _other_count in terms:
            if other == word:
                continue
            theirs = documents[_fold(other)]
            if len(theirs) > len(mine) and \
                    len(mine - theirs) <= (1 - overlap) * len(mine) and \
                    middle(other) <= middle(word):
                wider.add(word)
                break
    terms = [(word, count) for word, count in terms if word not in wider]

    kept = []
    claimed = []
    for word, count in terms:
        mine = documents[_fold(word)]
        if any(len(mine - theirs) <= (1 - overlap) * len(mine)
               for theirs in claimed):
            continue
        # A word sitting inside two different categories at once is not a
        # third category, it is whoever the documents are about. A name
        # spans the payslips and the job applications and the certificates;
        # `Loonbrief` spans only payslips. Neither the ceiling nor the
        # subset test sees this, because the name is a minority of the pile
        # and a subset of nothing.
        shared = sum(1 for theirs in claimed
                     if len(mine & theirs) >= 0.25 * len(mine))
        if shared >= 2:
            continue
        kept.append((word, count))
        claimed.append(mine)
    return kept


def learn_best(stems, min_support=MIN_SUPPORT, depths=(2, 3, 4)):
    """Learn at several depths and keep whichever saw the most clearly.

    How many leading fields to look at is not something that can be decided
    in advance. Look at too few and two conventions blur into one; look at
    too many and one convention shatters into a dozen, because the title at
    the end of it has a different number of words in every file. That is not
    a hypothetical: FurAffinity's names split into three unusable clusters at
    a depth of three and resolve into one clean convention at two.

    So all the plausible depths are tried and the one that explains the most
    files wins, ties going to the shallowest because the simpler pattern is
    the one a person can read.
    """
    best = []
    best_score = -1
    best_depth = None
    for depth in depths:
        conventions = [convention for convention
                       in learn(stems, depth, min_support)
                       if convention.category is not None]
        score = sum(convention.count for convention in conventions)
        if score > best_score:
            best, best_score, best_depth = conventions, score, depth
    return best, best_depth


def learn(stems, fields=FIELDS, min_support=MIN_SUPPORT):
    """Every naming convention in a corpus, strongest first.

    `stems` is an iterable of filenames without extensions. Nothing is read
    from disk and nothing is kept but counts, so this is affordable on a
    folder with four hundred thousand names in it.
    """
    clusters = collections.defaultdict(list)
    for stem in stems:
        if not stem:
            continue
        clusters[signature(stem, fields)].append(stem)

    conventions = []
    for shape, members in clusters.items():
        if len(members) < min_support:
            continue
        if not _looks_machine_made(shape):
            continue
        columns = []
        for index, (separator, kind) in enumerate(shape):
            values = collections.Counter()
            for stem in members:
                tokens = tokenise(stem, fields)
                if index < len(tokens):
                    values[tokens[index][1]] += 1
            columns.append((separator, kind, values))

        category = None
        best = None
        for index, (separator, kind, values) in enumerate(columns):
            if kind == TAIL or not _is_category(kind, values, len(members)):
                continue
            ratio = len(values) / float(len(members))
            if best is None or ratio < best:
                best, category = ratio, index

        convention = Convention(shape, len(members), columns, category,
                                members[:3], members)
        # A field where almost nothing repeats is an identifier that
        # happened to pass the ratio test, not a category.
        if convention.category is not None \
                and convention.concentration < MIN_CONCENTRATION:
            convention.category = None
        conventions.append(convention)
    conventions.sort(key=lambda convention: (-convention.count,))
    return conventions


# ---------------------------------------------------------------------------
# What to call the folder
# ---------------------------------------------------------------------------

# How long a folder's name may grow from its word.
PHRASE_WORDS = 6
# The share of a word's documents that must say the whole phrase.
PHRASE_SHARE = 0.9
# Numbers are tokens too, so that they stand between the words either side
# of them: "Invoice 12 from Acme" and "Invoice 13 from Acme" share
# `Invoice`, not `Invoice from`. A number every document prints -- a form's
# own number -- is shared like any word, and can be part of the name.
_PHRASE_TOKEN = re.compile(r"[^\W_][\w’'-]*|\u00b7", re.UNICODE)
# The mark a heading leaves where it stepped over a number (see
# `readers.document.heading_of`): no folder name reaches across one.
_PHRASE_BREAK = "\u00b7"


def shared_phrase(word, values, exclude=()):
    """The run of words around `word` that its documents all say, or `word`.

    A word is what a category is found by, and often a poor name for it.
    Three UK tax forms were learnt as `Details` -- the first word of
    "Details of employee leaving work", which is printed at the top of
    every one of them -- and a folder called `Details` says nothing. The
    documents had named themselves; the name was just longer than a word.

    So the folder is named after the longest run of words, around the one
    that was learnt, that nearly all of its documents share. It is still
    found by the word alone. Nothing here knows any language: the phrase is
    whatever the documents repeat. Owner names (`exclude`) are never part
    of it, and it neither starts nor ends on a word of two letters, which
    in every alphabet is a joining word more often than a name.
    """
    target = _fold(word)
    banned = set(_fold(name) for name in exclude) | {_PHRASE_BREAK}
    matching = []
    for value in values:
        tokens = _PHRASE_TOKEN.findall(str(value or ""))
        folded = [_fold(token) for token in tokens]
        if target in folded:
            matching.append((tokens, folded))
    if len(matching) < MIN_OCCURRENCES:
        return word
    needed = max(MIN_OCCURRENCES, int(len(matching) * PHRASE_SHARE + 0.999))

    def said_by(window):
        size = len(window)
        count = 0
        for _tokens, folded in matching:
            if any(folded[start:start + size] == window
                   for start in range(len(folded) - size + 1)):
                count += 1
        return count

    tokens, folded = matching[0]
    best = None
    for at in [index for index, token in enumerate(folded)
               if token == target]:
        for size in range(PHRASE_WORDS, 1, -1):
            for start in range(max(0, at - size + 1),
                               min(at, len(folded) - size) + 1):
                window = folded[start:start + size]
                if banned.intersection(window):
                    continue
                if len(window[0]) < 3 or len(window[-1]) < 3:
                    continue
                if best is not None and size <= len(best[1]):
                    continue
                if said_by(window) >= needed:
                    best = (start, window)
    if best is None:
        return word
    start, window = best
    return " ".join(tokens[start:start + len(window)])
