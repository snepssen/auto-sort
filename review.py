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
        return self.placed == 0 and self.shadowed >= MIN_TRIALS

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
            if matched:
                usages[rule.name].shadowed += 1
                usages[rule.name].shadowed_by[winner] += 1
    return list(usages.values()), files


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
    found = [(word, count) for word, count in shapes.learn_terms(headings)
             if not _named_by_a_rule(word, rule_set, fact)]
    return found, len(headings)
