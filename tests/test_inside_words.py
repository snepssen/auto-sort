"""Words that only ever matched inside other words.

`contains` matching inside a word is deliberate and load-bearing: it is what
lets a learnt `Vertrag` catch `Mietvertrag`, and it is why a German
household's post files itself without anybody writing a list of German
words anywhere in this program.

Nothing can tell that apart from `art` inside `Chart` -- it is the same
operation. What the record can say is that a word has never once turned up
on its own, which is the shape a wrong one takes, and that is all this
claims to say.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger                                            # noqa: E402
import review                                            # noqa: E402
import rules                                             # noqa: E402


RULES = """[settings]
dry_run = no

[watch]
folders = %s

[rule: what the page calls itself: Vertrag]
when = heading contains Vertrag
into = %s/Vertrag

[rule: what the page calls itself: art]
when = heading contains art
into = %s/art

[rule: everything else]
when = name is set
into = %s/rest
"""


class AskingTheCondition(unittest.TestCase):
    """A condition knows what it is made of."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-inside-")
        self.path = os.path.join(self.dir, "rules.ini")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(RULES % (self.dir, self.dir, self.dir, self.dir))
        self.rule_set = rules.load(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_every_comparison_is_reachable(self):
        by_name = dict((rule.name, rule) for rule in self.rule_set.rules)
        found = by_name["what the page calls itself: Vertrag"] \
            .condition.comparisons()
        self.assertEqual([(c.fact, c.operator, c.value) for c in found],
                         [("heading", "contains", "Vertrag")])

    def test_a_condition_made_of_several_parts(self):
        text = "kind = document and (heading contains Rechnung or year > 2010)"
        condition = rules.Parser(text).parse()
        self.assertEqual(
            sorted(c.fact for c in condition.comparisons()),
            ["heading", "kind", "year"])

    def test_a_negated_part_is_still_a_part(self):
        condition = rules.Parser("not heading contains draft").parse()
        self.assertEqual(len(condition.comparisons()), 1)


class FindingTheHost(unittest.TestCase):

    def test_a_word_of_its_own(self):
        whole, hosts = review._hosts_of("Vertrag", "Vertrag vom 3. Mai")
        self.assertTrue(whole)
        self.assertEqual(hosts, [])

    def test_buried_in_a_longer_word(self):
        whole, hosts = review._hosts_of("Vertrag", "Mietvertrag 2019")
        self.assertFalse(whole)
        self.assertEqual(hosts, ["mietvertrag"])

    def test_both_at_once_counts_as_a_word_of_its_own(self):
        whole, hosts = review._hosts_of("Vertrag", "Mietvertrag und Vertrag")
        self.assertTrue(whole)

    def test_case_and_punctuation_do_not_hide_it(self):
        whole, _hosts = review._hosts_of("art", "ART, and more")
        self.assertTrue(whole)

    def test_a_word_that_is_not_there_at_all(self):
        whole, hosts = review._hosts_of("art", "nothing here")
        self.assertFalse(whole)
        self.assertEqual(hosts, [])


class WhatTheRecordShows(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-inside2-")
        self.path = os.path.join(self.dir, "rules.ini")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(RULES % (self.dir, self.dir, self.dir, self.dir))
        self.rule_set = rules.load(self.path)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.run = self.journal.start_run("sort", source_root=self.dir,
                                          dry_run=False)
        self.number = 0

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def filed(self, rule_name, heading):
        self.number += 1
        move = self.journal.add_move(
            self.run, self.number, 1, "move", rule_name,
            os.path.join(self.dir, "f%d.pdf" % self.number),
            os.path.join(self.dir, "out", "f%d.pdf" % self.number),
            10, "", "done",
            facts={"heading": heading, "kind": "document"})
        self.journal.update_move(move, "done")

    def report(self):
        found, _files = review.inside_words(self.journal, self.rule_set)
        return dict((entry.word, entry) for entry in found)

    def test_a_word_that_turns_up_on_its_own_is_not_reported(self):
        for _ in range(4):
            self.filed("what the page calls itself: Vertrag", "Vertrag")
        self.assertEqual(self.report(), {})

    def test_a_word_that_never_does_is(self):
        for host in ("Chart of accounts", "Chartered surveyor",
                     "Cartography notes"):
            self.filed("what the page calls itself: art", host)
        found = self.report()
        self.assertIn("art", found)
        self.assertEqual(found["art"].inside, 3)
        self.assertEqual(found["art"].whole, 0)

    def test_one_appearance_on_its_own_is_enough_to_clear_it(self):
        """Mietvertrag three times and Vertrag once is a working rule."""
        for _ in range(3):
            self.filed("what the page calls itself: Vertrag", "Mietvertrag")
        self.filed("what the page calls itself: Vertrag", "Vertrag")
        self.assertEqual(self.report(), {})

    def test_two_files_is_not_yet_a_pattern(self):
        """Three occurrences is this project's threshold everywhere else."""
        for _ in range(2):
            self.filed("what the page calls itself: art", "Chart")
        self.assertEqual(self.report(), {})

    def test_it_names_what_the_word_was_hiding_in(self):
        for _ in range(3):
            self.filed("what the page calls itself: art", "Chart of accounts")
        self.assertEqual(self.report()["art"].hosts.most_common(1)[0][0],
                         "chart")

    def test_files_another_rule_won_are_not_counted_against_this_one(self):
        for _ in range(4):
            self.filed("everything else", "Chart of accounts")
        self.assertEqual(self.report(), {})

    def test_rules_without_contains_are_never_reported(self):
        simple = rules.Parser("kind = document").parse()
        self.assertEqual(simple.comparisons()[0].operator, "=")
        for _ in range(4):
            self.filed("everything else", "anything")
        self.assertEqual(self.report(), {})


if __name__ == "__main__":
    unittest.main()
