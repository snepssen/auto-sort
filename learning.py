"""Adding a category the filed documents have shown, to the rules file.

`auto-sort adopt` and the background sorter both come here, so that a rule
written by hand and one written unattended are the same rule in the same
place: immediately above whatever would otherwise claim those documents,
every other line of the file untouched, byte for byte.

The background sorter asks a narrower question than `adopt` does. It learns
only from documents still waiting in a holding folder -- its job is to
empty waiting rooms, not to take a document away from a category it
already has -- and never again from a word whose rule somebody deleted.
That is the difference between a program that learns and one that keeps
putting back what it was told to take away.
"""

from __future__ import annotations

import hashlib
import os

import propose
import review
import rules
import sorter
import userdirs

# What the ledger says about a revision that needed no preview, and why.
LEARNT_ONLY = "no preview needed: only a learnt category was added"


class Adoption(object):
    """What would be added, and the file it would be added to."""

    def __init__(self, found, headings, blocks=(), beaten=(), text="",
                 merged="", source=None):
        self.found = found              # [(word, documents)]
        self.headings = headings        # documents read
        self.blocks = list(blocks)      # the rules, as lines
        self.beaten = set(beaten)       # the rules they must sit above
        self.text = text                # the file as it is
        self.merged = merged            # the file as it would be
        self.source = source

    @property
    def names(self):
        return [line[len("[rule: "):-1] for block in self.blocks
                for line in block if line.startswith("[rule: ")]

    @property
    def line(self):
        """Where the rules go, counting from one."""
        return review.insertion_point(self.text, self.beaten) + 1


def plan(journal, rule_set, waiting_only=False, refused=(), note=None):
    """An `Adoption` for the categories that have earned a rule.

    `note` is a comment put above each rule, saying who wrote it.
    """
    found, headings = review.emerging(journal, rule_set,
                                      waiting_only=waiting_only)
    refused = set(word.casefold() for word in refused)
    found = [(word, count) for word, count in found
             if word.casefold() not in refused]
    if not found:
        return Adoption([], headings)

    # The guard against a short word swallowing a longer one has to see the
    # words already in the file, not just the new ones.
    existing = [rule.name.split(": ")[-1] for rule in rule_set.rules]
    others = [word for word, _count in found] + existing
    root = userdirs.home_for("document")
    # What the filed documents say, so each folder can be named after the
    # phrase they share rather than the one word that finds them.
    said = [review._facts_of(row).get("heading")
            for row in journal.placed_moves()]
    said = [heading for heading in said if heading]
    blocks = [propose.term_rule("heading", "what the page calls itself",
                                word, count, "documents say it",
                                root, others, said)
              for word, count in found]
    if note:
        blocks = [["; %s" % note] + list(block) for block in blocks]
    with open(rule_set.source, "r", encoding="utf-8") as handle:
        text = handle.read()
    beaten = review.outranked(journal, rule_set,
                              [word for word, _count in found])
    return Adoption(found, headings, blocks, beaten, text,
                    review.adopt(text, blocks, beaten), rule_set.source)


def write(adoption, journal=None):
    """Add the rules; None, or a sentence saying why nothing changed.

    The file as it was is kept beside it, and the new one is read back
    before this says it worked: a file that would not load is put back
    exactly as it was, because a rules file that does not load stops all
    sorting.

    With `journal`, a watched folder whose previous revision was already
    previewed is recorded as needing no preview for this one: see
    `_carry_preview`.
    """
    source = adoption.source
    backup = source + ".before-adopt"
    try:
        with open(backup, "w", encoding="utf-8") as handle:
            handle.write(adoption.text)
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(adoption.merged)
    except OSError as error:
        return "could not write %s: %s" % (source, error)
    try:
        written = rules.load(source)
    except rules.RuleError as error:
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(adoption.text)
        return ("the file would not load afterwards, so it was put back "
                "exactly as it was: %s" % error)
    if journal is not None:
        _carry_preview(journal, adoption.text, written)
    return None


def _carry_preview(journal, before_text, written):
    """Let an approved folder keep its approval across a learnt category.

    Every rules revision needs a preview before the background sorter moves
    anything under it, and a preview in the sweep pauses the daemon. The
    gate exists for what a person writes. A category auto-sort added by
    itself paused sorting at the next download until somebody clicked
    Resume -- unnoticed only when the promotion pass happened to preview
    first, and every time with `regroup = report`.

    Only a folder already previewed under exactly the file this was added
    to is carried over. The hash is of the text the rule was inserted into,
    not of what the daemon last loaded, so an edit somebody made in between
    is not waved through with it; and rules nobody has previewed yet are
    no more approved for having had a category added.
    """
    before = sorter.rules_hash(rules.RuleSet(
        None, None, [], written.source,
        hashlib.sha256(before_text.encode("utf-8")).hexdigest()))
    after = sorter.rules_hash(written)
    for root in written.watch.folders:
        root = os.path.abspath(root)
        if not journal.has_preview(root, before):
            continue
        run = journal.start_run("learn", source_root=root, rules_hash=after,
                                dry_run=True)
        journal.finish_run(run, "completed", summary=LEARNT_ONLY)
        journal.record_preview(root, after, run)


def backup_name(adoption):
    return os.path.basename(adoption.source + ".before-adopt")
