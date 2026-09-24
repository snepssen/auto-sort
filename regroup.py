"""Going back for the files that were filed before there was a better answer.

A folder teaches auto-sort what it contains, and it does it gradually. The
first twenty pictures from a site are not a pattern; the sixtieth makes one
visible. Everything that arrived before that point was filed into a holding
folder, correctly -- there was nothing better to do with it at the time -- and
the obvious failure is that it stays there forever while only new arrivals get
the benefit of what the old ones taught.

Making somebody drag those files back into Downloads to be re-sorted would be
absurd, and it is what every tool of this kind quietly requires. So: the
ledger already records where each file was put, by which rule, and what was
known about it. That is enough to reconsider a decision without the file ever
moving back.

**Promotion only, and only out of holding.** A file placed by a rule marked
`holding = yes` may be moved to a rule that is not. Nothing else is ever
reconsidered. That single restriction is what stops this from becoming churn:
a decision that was already specific is never relitigated, so editing a rules
file cannot silently reshuffle a disk, and a file cannot ping-pong between two
rules that both think they want it.

**Anything the person touched is left alone.** If a file is not exactly where
the ledger says it was put, they moved it, and that is an answer rather than a
gap. `corrections` learns from those; this does not touch them.
"""

from __future__ import annotations

import collections
import json
import os

import copy

import bundles
import rules as rules_module
import sorter


class Candidate(object):
    """A file sitting in a holding folder that might now have a home."""

    def __init__(self, row, path, source_root, facts):
        self.row = row
        self.path = path
        self.source_root = source_root
        self.facts = facts
        self.rule_name = row["rule_name"]


def candidates(journal, rule_set, source_root=None, limit=20000):
    """Files placed by a holding rule that are still exactly where they were.

    A file the ledger cannot still find is somebody's business, not ours: it
    was moved, renamed or deleted, and re-filing it would be overruling a
    decision rather than completing one.
    """
    # By the flag the ledger recorded at the time, with the rule name kept
    # only as a fallback for placements made before that column existed.
    # Names change whenever a rules file is regenerated, and a file's history
    # must not depend on that.
    named = set(rule.name for rule in rule_set.rules if rule.holding)
    named.add("[unsorted]")

    corrected = set(row["move_id"] for row in journal.corrections(None))
    found = []
    for row in journal.placed_moves(source_root, limit):
        recorded = row["holding"] if "holding" in row.keys() else 0
        if not recorded and row["rule_name"] not in named:
            continue
        if row["id"] in corrected:
            continue
        destination = row["destination"]
        if not destination or not os.path.isfile(destination):
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except (TypeError, ValueError):
            facts = {}
        found.append(Candidate(row, destination,
                               row["source_root"] or os.path.dirname(
                                   destination), facts))
    return found


def filed(journal, rule_set, source_root=None, limit=20000):
    """Files a real category placed, still exactly where it put them.

    What `refile` reconsiders, and everything `candidates` is not. The same
    restraint applies: a file somebody moved is their answer, and it is
    left alone. So is a file that went as one part of a bundle -- a video
    and its subtitles went together and are not split up afterwards.
    """
    named = set(rule.name for rule in rule_set.rules if rule.holding)
    named.add("[unsorted]")
    corrected = set(row["move_id"] for row in journal.corrections(None))
    bundled = set(
        (row[0], row[1]) for row in journal.connection.execute(
            "SELECT run_id, item_number FROM moves "
            " WHERE status IN ('done','copied') "
            " GROUP BY run_id, item_number HAVING COUNT(*) > 1"))
    found = []
    for row in journal.placed_moves(source_root, limit):
        recorded = row["holding"] if "holding" in row.keys() else 0
        if recorded or row["rule_name"] in named:
            continue
        if row["id"] in corrected:
            continue
        if (row["run_id"], row["item_number"]) in bundled:
            continue
        destination = row["destination"]
        if not destination or not os.path.isfile(destination):
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except (TypeError, ValueError):
            facts = {}
        found.append(Candidate(row, destination,
                               row["source_root"] or os.path.dirname(
                                   destination), facts))
    return found


def promotable(rule_set):
    """The same rule set with its holding rules taken out.

    This is how a promotion is decided, and it is deliberately not a separate
    code path. Planning against a rule set with no holding rules means a file
    that still has no better answer simply matches nothing and is left where
    it is, while one that does gets planned by exactly the machinery that
    would have placed it on the way in -- same collision handling, same
    cross-volume checks, same fingerprinting, same ledger. A second
    implementation of "where should this go" would be a second set of bugs.
    """
    settings = copy.copy(rule_set.settings)
    settings.unsorted = "leave"          # never gather during a regroup
    kept = [rule for rule in rule_set.rules if not rule.holding]
    return rules_module.RuleSet(settings, rule_set.watch, kept,
                                rule_set.source, rule_set.source_hash)


def build(journal, rule_set, source_root=None, limit=20000, pick=None):
    """[(root, plan)] describing every file that can be promoted.

    Grouped by the folder each file originally came from, so that a rule with
    a relative destination resolves the way it did the first time round.

    `pick` chooses the files considered: `candidates` (the default, files
    in holding folders) or `filed` (files a real category placed). Either
    way the answer comes from the rules with their holding rules taken out,
    so a file is only ever moved to a category -- never back into a pen.
    """
    pick = pick or candidates
    grouped = collections.defaultdict(list)
    for candidate in pick(journal, rule_set, source_root, limit):
        grouped[candidate.source_root].append(candidate)
    if not grouped:
        return []

    promoting = promotable(rule_set)
    plans = []
    for root, group in sorted(grouped.items()):
        items = [bundles.Item(candidate.path) for candidate in group]
        # No ledger here on purpose: a promotion is a file that is
        # already in the ledger moving again, so it would be found as a
        # duplicate of itself.
        plan = sorter.build_plan(root, promoting, items=items)
        # Everything that matched nothing is simply still waiting, which is
        # the normal case and not worth reporting as a skip.
        plan.skipped = [entry for entry in plan.skipped
                        if not entry[1].startswith("no rule matched")
                        and entry[1] != "already at its destination"]
        if plan.items or plan.skipped:
            plans.append((root, plan))
    return plans


def summarise(plans):
    """What the promotions would do, grouped for somebody to read."""
    by_rule = collections.Counter()
    by_destination = collections.Counter()
    total = 0
    for _root, plan in plans:
        for item in plan.items:
            by_rule[item.rule_name] += 1
            by_destination[os.path.dirname(item.members[0].destination)] += 1
            total += 1
    return total, by_rule.most_common(), by_destination.most_common(12)
