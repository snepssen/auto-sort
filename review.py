"""Rules that have never once been the answer.

Induction proposes generously and has to. A word heading three documents
might be the kind of document or it might be the town it was posted from,
and nothing in the filename or the page says which -- so `Rechnung` and
`Stadtwerke` are both offered, and the folder gets fourteen rules where four
would do.

Time settles it, and settles it for free. Household paperwork is nearly the
same letter month after month, so every bill that arrives is another trial.
`Rechnung` wins them and its count climbs. `Stadtwerke` matches the same
files and never wins one, because `Rechnung` sits above it and got there
first -- and every further bill makes that more certain rather than less. A
rule shadowed a hundred times is not waiting for its turn. It is a word that
happened to be in the room.

This counts those trials. It reads only what the ledger already recorded --
which rule placed each file, and what was known about it -- so it costs no
disk and says nothing about files it has not seen.

Nothing here edits anybody's rules. The rules file belongs to the person who
owns it; this prints what the record shows and they decide.
"""

from __future__ import annotations

import collections
import json
import os
import re

import owner
import rules
import shapes

MIN_TRIALS = 3          # the project's threshold for "that is a pattern"


class Usage(object):
    """What the record shows about one rule."""

    def __init__(self, rule):
        self.rule = rule
        self.name = rule.name
        self.placed = 0          # times it was the answer
        self.shadowed = 0        # times it matched and something above won
        self.shadowed_by = collections.Counter()
        # Times it matched a file filed by a rule that is now below it, or
        # gone: the file is its to take next time, not a loss.
        self.waiting = 0

    @property
    def dead(self):
        """Matched often enough to judge, and never once chosen.

        A holding rule is never dead however often it loses. Catch-alls
        exist to be last, and one that has never fired is a safety net
        doing its job -- the day it catches something is the day it earns
        its keep. Judging those by the same count would tell somebody to
        delete the rule that guarantees nothing is left behind.
        """
        if getattr(self.rule, "holding", False):
            return False
        return (self.placed == 0 and self.waiting == 0
                and self.shadowed >= MIN_TRIALS)

    def __repr__(self):
        return "<Usage %s placed=%d shadowed=%d>" % (
            self.name, self.placed, self.shadowed)


def _facts_of(row):
    try:
        return json.loads(row["facts_json"] or "{}")
    except (TypeError, ValueError):
        return {}


def usage(journal, rule_set, limit=20000):
    """`(usages, files)` -- one Usage per rule, and how many files were read.

    A rule's condition is replayed against the facts the ledger kept, which
    is exact for what matched and deliberately ignores the confidence floor:
    the question here is whether a rule was ever *reachable*, and a rule that
    matched but was outranked is shadowed however the floor would have gone.
    """
    usages = collections.OrderedDict(
        (rule.name, Usage(rule)) for rule in rule_set.rules)
    order = dict((rule.name, index)
                 for index, rule in enumerate(rule_set.rules))
    files = 0
    for row in journal.placed_moves(None, limit):
        facts = _facts_of(row)
        if not facts:
            continue
        files += 1
        winner = row["rule_name"]
        if winner in usages:
            usages[winner].placed += 1
        for rule in rule_set.rules:
            if rule.name == winner:
                continue
            # Every rule, not just the ones above the winner. A shadowed
            # rule is by definition one that never got evaluated, because
            # something earlier already claimed the file -- so stopping at
            # the winner would hide precisely the rules being looked for.
            try:
                matched, _used = rule.condition.evaluate(facts)
            except Exception:    # noqa: BLE001 - a rule must never crash this
                continue
            if not matched:
                continue
            # Only a rule above this one can have beaten it. A winner now
            # below it, or no longer in the file, filed this before the
            # rules said otherwise -- read as a loss, a contract rule added
            # above the one that had filed 169 contracts was reported as
            # always losing to it, and as safe to delete.
            if winner in order and order[winner] < order[rule.name]:
                usages[rule.name].shadowed += 1
                usages[rule.name].shadowed_by[winner] += 1
            else:
                usages[rule.name].waiting += 1
    return list(usages.values()), files


class InsideWords(object):
    """A learnt word that only ever matched inside longer words."""

    def __init__(self, rule, fact, word):
        self.rule = rule
        self.fact = fact
        self.word = word
        self.whole = 0           # files where it was a word of its own
        self.inside = 0          # files where it was buried in another word
        self.hosts = collections.Counter()

    def __repr__(self):
        return "InsideWords(%s, %r, %d/%d)" % (
            self.rule.name, self.word, self.inside, self.inside + self.whole)


_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _hosts_of(word, text):
    """`(whole, [longer words it was found inside])` for one value."""
    folded = str(word).lower()
    whole = False
    hosts = []
    for match in _WORD.finditer(str(text).lower()):
        token = match.group(0)
        if token == folded:
            whole = True
        elif folded in token:
            hosts.append(token)
    return whole, hosts


def inside_words(journal, rule_set, limit=20000):
    """Learnt words that have never once matched a word of their own.

    `contains` matching inside words is deliberate and load-bearing: it is
    what lets a learnt `Vertrag` catch `Mietvertrag`, which is the whole
    reason a German household's post files itself without anybody writing a
    list of German words. The collision between two *learnt* words is
    already handled -- the shorter one is asked for as a word of its own.

    What is not handled is a short word matching inside an unrelated long
    one, and the honest position is that nothing here can tell the two
    apart: `Vertrag` inside `Mietvertrag` and `art` inside `Chart` are the
    same operation. What can be said is what the record shows, which is that
    this word has never once turned up on its own -- every file it claimed,
    it claimed from inside something else. That is worth pointing at.

    One rule per word exists precisely so that a person can delete one.
    Nothing here does it for them.
    """
    by_rule = collections.OrderedDict()
    for rule in rule_set.rules:
        for comparison in rule.condition.comparisons():
            if comparison.operator == "contains" and comparison.value:
                key = (rule.name, comparison.fact, str(comparison.value))
                by_rule.setdefault(
                    key, InsideWords(rule, comparison.fact,
                                     str(comparison.value)))
    if not by_rule:
        return [], 0

    files = 0
    for row in journal.placed_moves(None, limit):
        facts = _facts_of(row)
        if not facts:
            continue
        files += 1
        for (name, fact, word), report in by_rule.items():
            if row["rule_name"] != name:
                continue
            value = facts.get(fact)
            if value is None:
                continue
            whole, hosts = _hosts_of(word, value)
            if whole:
                report.whole += 1
            elif hosts:
                report.inside += 1
                report.hosts.update(hosts)
    # Only the ones the record has actually judged. A word that has placed
    # two files has not yet said anything; three is this project's threshold
    # for calling something a pattern and it is the threshold here too.
    return [report for report in by_rule.values()
            if report.inside >= MIN_TRIALS and report.whole == 0], files


class NearMiss(object):
    """A rule asking about a fact that is there, for a value that is not."""

    def __init__(self, rule, fact, wanted, actual):
        self.rule = rule
        self.fact = fact
        self.wanted = wanted            # what the rule asks for
        self.actual = actual            # [(value, files)] that are there

    @property
    def files(self):
        return sum(count for _value, count in self.actual)

    def __repr__(self):
        return "NearMiss(%s, %s)" % (self.rule.name, self.fact)


def near_misses(journal, rule_set, limit=20000):
    """Rules that have never placed a file, and the values they just miss.

    The failure this is for looked like this, on a real machine. A rule
    said `from_host ~ *.furaffinity.net`; forty-three pictures were filed
    with `from_host = furaffinity.net`, because a host is recorded as its
    registrable domain and `d.furaffinity.net` is stored as the site it
    belongs to. A leading `*.` requires something in front of the dot, so
    the rule matched none of them and they went to a holding folder --
    where the person found them, moved them back, and watched it happen
    again.

    Every existing report was silent about it. "Never matched anything" is
    not evidence of a mistake -- a rule for a kind of file you do not own
    yet is supposed to match nothing -- so silence is the right default.
    What is *not* silence is a rule asking about a fact that plenty of
    files have, for a value that none of them has. That is the shape of a
    typo, and it can be said out loud without guessing at anybody's
    intent: here is what the rule wants, and here is what is actually
    there.
    """
    usages, files = usage(journal, rule_set, limit)
    never = set(use.rule.name for use in usages
                if not use.placed and not use.shadowed and not use.waiting)
    if not never:
        return [], files

    values = collections.defaultdict(collections.Counter)
    for row in journal.placed_moves(None, limit):
        for name, value in _facts_of(row).items():
            if isinstance(value, (str, int, float)) and value != "":
                values[name][value] += 1

    found = []
    for rule in rule_set.rules:
        if rule.name not in never:
            continue
        for comparison in rule.condition.comparisons():
            if comparison.operator not in ("~", "=", "contains"):
                continue
            seen = values.get(comparison.fact)
            if not seen or sum(seen.values()) < MIN_TRIALS:
                continue
            if any(_would_match(comparison, value) for value in seen):
                continue
            near = [(value, count) for value, count in seen.most_common()
                    if _nearly(comparison.value, value)]
            if not near:
                # The rule wants something nothing here resembles, which is
                # what a rule for a kind of file you do not own yet looks
                # like, and it is supposed to match nothing.
                continue
            found.append(NearMiss(rule, comparison.fact,
                                  "%s %s" % (comparison.operator,
                                             comparison.value),
                                  near[:3]))
    return found, files


_PUNCTUATION = re.compile(r"[^0-9a-z]+")


def _nearly(wanted, value):
    """Is this the value the rule was reaching for, spelt differently?

    Reduced to letters and digits, so that `*.furaffinity.net` and
    `furaffinity.net` are the same thing and `archive` and `document` are
    not. Either may contain the other: a rule can ask for too much or too
    little, and both mistakes read the same way from here.

    The point of the test is to leave alone the rule that simply matches
    nothing. Somebody who owns no 3D models has a rule for them that will
    match nothing until the day they do, and saying anything about it
    would be noise.
    """
    left = _PUNCTUATION.sub("", str(wanted).lower())
    right = _PUNCTUATION.sub("", str(value).lower())
    if len(left) < 3 or len(right) < 3:
        return False
    return left in right or right in left


def _would_match(comparison, value):
    """Whether this rule's test accepts a value that is really out there."""
    try:
        return bool(comparison._apply(value))
    except Exception:                        # noqa: BLE001
        return False


# The facts that come from *reading* a file, which is to say the ones a
# better reader changes. Everything else in a record describes where the file
# was, how it arrived and when -- true at the source, and not something that
# can be read back off the file where it sits now.
READ_FACTS = frozenset((
    "heading", "words_read", "text_layer", "needs_ocr", "read_by",
    "scan_pixels", "scan_of", "doc_title", "author", "producer", "pages",
))


def refresh_held(journal, limit=5000, tier=None):
    """Read the files in holding folders again, with the reader as it is now.

    Facts are recorded when a file is filed and never looked at again, which
    is right for a file that has been placed and wrong for one that is
    waiting: waiting is exactly what these files are doing, and they are
    being judged on what an older reader said about them. On a real machine
    a series of 171 documents was remembered with glyph numbers for headings
    after the reader stopped producing them, so the report that could have
    named the series could not see what it said.

    Only holding files, only files still where they were put, and only the
    facts that come from reading. Returns how many records changed.
    """
    import identify
    changed = 0
    tier = identify.TIER_HEADER if tier is None else tier
    for row in journal.held_moves(limit):
        path = row["destination"]
        if not path or not os.path.exists(path):
            continue
        try:
            fresh = identify.identify(path, tier=tier)
        except (OSError, ValueError):
            continue
        stored = _facts_of(row)
        updated = dict(stored)
        for name in READ_FACTS:
            value = fresh.value(name)
            if value is None:
                updated.pop(name, None)
            else:
                updated[name] = value
        if updated != stored:
            journal.set_facts(row["id"], updated)
            changed += 1
    return changed


def dead_rules(journal, rule_set, limit=20000):
    """Only the ones the record has already judged."""
    usages, files = usage(journal, rule_set, limit)
    return [use for use in usages if use.dead], files


def _named_by_a_rule(word, rule_set, fact="heading"):
    """Would any existing rule already claim a document headed with this?

    Asked of the rules themselves rather than by matching their text, so a
    hand-written rule counts exactly as much as a generated one.
    """
    probe = {fact: word, "name": "probe", "kind": "document"}
    for rule in rule_set.rules:
        if getattr(rule, "holding", False):
            continue        # a catch-all claims everything and names nothing
        try:
            matched, _used = rule.condition.evaluate(probe)
        except Exception:                    # noqa: BLE001
            continue
        if matched:
            return True
    return False


def emerging(journal, rule_set, fact="heading", limit=20000):
    """Words that now head enough filed documents to deserve a folder.

    The other half of the same idea. `dead_rules` finds categories the
    record has disproved; this finds ones it has since proved and nobody
    has written down.

    It matters because rules are generated once and the post keeps coming.
    A kind of letter that did not exist when the rules were written has no
    rule of its own, so it is claimed by whatever else happens to match --
    on a real run, four `Mahnung` letters were filed under `Stadtwerke`,
    the company that sent them, because that word was also on the page and
    had a rule. The documents were not lost, but they were sorted by who
    wrote them instead of what they are, and nothing said so.

    Reads only the ledger's own record of what it filed, so it costs no
    disk and knows nothing it was not already told.
    """
    headings = []
    for row in journal.placed_moves(None, limit):
        facts = _facts_of(row)
        value = facts.get(fact)
        if value:
            headings.append(value)
    if not headings:
        return [], 0

    # Candidates: words the documents themselves repeat, that no rule names.
    # The same allowance the proposer makes for whoever this computer
    # belongs to: their name heads half the post in the house and divides
    # none of it.
    candidates = [word for word, _count
                  in shapes.learn_terms(
                      headings, owner=owner.account(),
                      person=owner.names())
                  if not _named_by_a_rule(word, rule_set, fact)]
    if not candidates:
        return [], len(headings)

    # A document counts towards a word only when that word comes before
    # whatever currently claims the document. That single comparison settles
    # both awkward cases at once.
    #
    # A `Mahnung` filed under `Stadtwerke` still counts for `Mahnung`,
    # because the kind of letter is printed above the name of the company
    # that sent it -- so the existing rule is claiming it by a worse word
    # and the new rule deserves to go above it.
    #
    # And a `Rechnung` already filed by the `Rechnung` rule does not count
    # towards `Stadtwerke`, which appears later on the same page. Without
    # this, deleting a noise rule would only make the next run offer it
    # straight back.
    claimed_at = {}
    for heading in headings:
        winner = None
        for rule in rule_set.rules:
            if getattr(rule, "holding", False):
                continue
            probe = {fact: heading, "name": "probe", "kind": "document"}
            try:
                matched, _used = rule.condition.evaluate(probe)
            except Exception:                # noqa: BLE001
                continue
            if matched:
                winner = rule.name.split(": ")[-1]
                break
        claimed_at[heading] = _position(heading, winner)

    earned = collections.Counter()
    for heading in headings:
        best, where = None, claimed_at[heading]
        for word in candidates:
            at = _position(heading, word)
            if at is None or at >= where:
                continue
            if best is None or at < _position(heading, best):
                best = word
        if best is not None:
            earned[best] += 1

    found = [(word, earned[word]) for word in candidates
             if earned[word] >= shapes.MIN_OCCURRENCES]
    return found, len(headings)


def outranked(journal, rule_set, words, fact="heading", limit=20000):
    """The rules currently claiming the documents these words should get.

    A new rule has to sit above every one of them or it will never fire.
    """
    wanted = set(word.lower() for word in words)
    beaten = set()
    for row in journal.placed_moves(None, limit):
        heading = (_facts_of(row).get(fact) or "").lower()
        if not heading or not any(word in heading for word in wanted):
            continue
        for rule in rule_set.rules:
            if getattr(rule, "holding", False):
                continue
            probe = {fact: _facts_of(row).get(fact), "name": "probe",
                     "kind": "document"}
            try:
                matched, _used = rule.condition.evaluate(probe)
            except Exception:                # noqa: BLE001
                continue
            if matched:
                beaten.add(rule.name)
                break
    return beaten


def _position(heading, word):
    """Where a word sits among a heading's words, or the end if absent.

    Absent counts as the end rather than as nothing, so a document no rule
    claims is a document any candidate word can win.
    """
    words = shapes._WORD.findall(heading or "")
    if word:
        lowered = [item.lower() for item in words]
        target = word.lower()
        for index, item in enumerate(lowered):
            if item == target:
                return index
    return len(words) + 1


# ---------------------------------------------------------------------------
# Adopting a category without rewriting anybody's file
# ---------------------------------------------------------------------------
#
# `propose` regenerates a rules file from scratch, which is right the first
# time and wrong every time after: it discards whatever the person wrote,
# reordered or deleted since. So a category that emerges later cost them
# their edits to adopt, and the honest advice was to adopt it by hand.
#
# Appending is not the answer either. First match wins, so a rule added at
# the end sits below the catch-alls and can never fire -- it would look
# adopted and do nothing, which is the silent failure this project keeps
# running into.
#
# The rule goes immediately above the first catch-all instead: after
# everything specific the person has written, before anything that claims
# what is left. Every other line in the file is untouched, byte for byte.

_SECTION = re.compile(r"^\s*\[rule:\s*(?P<name>.*?)\s*\]\s*$")
_HOLDING_YES = re.compile(r"^\s*holding\s*=\s*(yes|true|on|1)\s*$", re.I)


def section_line(text, rule_name):
    """Where a named rule's section begins, or None."""
    for index, line in enumerate(text.splitlines()):
        match = _SECTION.match(line)
        if match and match.group("name") == rule_name:
            return index
    return None


def insertion_point(text, above=()):
    """The line to insert at: above the first catch-all, and above `above`.

    A new rule below the rule already claiming its documents is a rule that
    never fires -- adopted, visibly present, and silently doing nothing.
    That is this project's favourite way to fail, so the rules a new one has
    to beat are passed in and it goes above the earliest of them.

    With no catch-all and nothing to beat it lands at the end, which means
    somebody removed the guarantee that nothing is left behind: a choice to
    respect rather than quietly undo.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines)
              if _SECTION.match(line)]
    candidates = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        if any(_HOLDING_YES.match(line) for line in lines[start:end]):
            candidates.append(start)
            break
    for name in above:
        where = section_line(text, name)
        if where is not None:
            candidates.append(where)
    return min(candidates) if candidates else len(lines)


def adopt(text, blocks, above=()):
    """`text` with `blocks` inserted where they will actually be reached."""
    if not blocks:
        return text
    lines = text.splitlines()
    at = insertion_point(text, above)
    addition = []
    for block in blocks:
        addition.extend(block)
    if addition and addition[-1] != "":
        addition.append("")
    merged = lines[:at] + addition + lines[at:]
    return "\n".join(merged) + ("\n" if text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# Rules that match and then decline
# ---------------------------------------------------------------------------
#
# The decision layer can fail in a way nobody sees. A rule's `when` matches
# perfectly and the rule still does not act, because its destination needs a
# fact that is only a guess -- and `min_confidence` is a floor on acting, not
# on matching. The file still moves, to a catch-all, and nothing is said.
#
# `Scans/{happened:%Y}` did this. A scanned page has no capture date and
# usually no date in its name, so `happened` falls back to when the file
# arrived, which is WEAK and under the floor. Eight of nine scanned documents
# went to Unfiled. The ninth was called `20090314.pdf`, got a LIKELY date out
# of its own name, and was the only reason anybody noticed.
#
# The engine already explains this per file -- `below confidence 0.60:
# happened 0.45` comes straight out of `Rule.evaluate`. What was missing was
# anybody counting. One file declining is a file; thirty declining and none
# ever placed is a rule that does not work.

MIN_DECLINES = 3


class Reach(object):
    """How a rule fared against real files, rather than against a parser."""

    def __init__(self, rule):
        self.rule = rule
        self.name = rule.name
        self.placed = 0
        self.declined = 0
        self.why = collections.Counter()

    @property
    def broken(self):
        return self.placed == 0 and self.declined >= MIN_DECLINES

    @property
    def reason(self):
        return self.why.most_common(1)[0][0] if self.why else ""


def reachability(rule_set, folders, tier=None, limit=400, depth=3):
    """`(reaches, files)` -- every rule, judged against files on disk.

    Real identification, so real confidences. Replaying stored facts would
    not do: the ledger keeps what a fact was, not how sure anybody was, and
    the whole failure here is about how sure.
    """
    import bundles
    import identify as identify_module
    if tier is None:
        tier = identify_module.TIER_HEADER

    reaches = collections.OrderedDict(
        (rule.name, Reach(rule)) for rule in rule_set.rules)
    seen = 0
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for item in bundles.walk(folder, max_depth=depth):
            if seen >= limit:
                break
            try:
                record = identify_module.identify(item, tier=tier)
            except (OSError, ValueError):
                continue
            seen += 1
            for result in rule_set.evaluate(record, source_root=folder):
                name = result.rule.name
                if name not in reaches:
                    continue
                if result.matched and (result.rule.mode == "leave"
                                       or result.destination is not None):
                    reaches[name].placed += 1
                    break
                if not result.matched and result.reason != rules._NO_MATCH:
                    reaches[name].declined += 1
                    reaches[name].why[result.reason] += 1
    return list(reaches.values()), seen


def unreachable(rule_set, folders, **kwargs):
    reaches, files = reachability(rule_set, folders, **kwargs)
    return [reach for reach in reaches if reach.broken], files
