"""Recognising a file that has already been filed somewhere else.

The ledger has stored a sha256 for every file auto-sort ever moved and did
nothing with them, so `duplicate_of` was a fact no reader set and the rule
written against it could never fire.

Duplicates arrive by accident. macOS has no cut and paste for files, so
moving one means copying it and then remembering to delete the original, and
the second half is the half that does not happen. Both halves of that are
tested here: a copy meeting a file filed weeks ago, and two copies arriving
together in one run, which is the commoner case because the copy is usually
why they arrived together.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duplicates                                        # noqa: E402
import evidence                                          # noqa: E402
import fixtures                                          # noqa: E402
import ledger                                            # noqa: E402
import mover                                             # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402


class Lookup(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def make(self, name, **kwargs):
        return fixtures.jpeg(os.path.join(self.directory, name), **kwargs)

    def test_an_identical_copy_is_found(self):
        original = self.make("one.jpg")
        copy = os.path.join(self.directory, "two.jpg")
        shutil.copy2(original, copy)
        index = duplicates.Index()
        index.remember(os.path.getsize(original),
                       mover.hash_path(original), original)
        self.assertEqual(index.find(copy), original)

    def test_a_different_file_is_not(self):
        original = self.make("one.jpg")
        other = self.make("two.jpg", make="Nikon", model="Nikon Z6")
        index = duplicates.Index()
        index.remember(os.path.getsize(original),
                       mover.hash_path(original), original)
        self.assertIsNone(index.find(other))

    def test_only_a_size_collision_costs_a_read(self):
        # Hashing every candidate would make identification cost a full read
        # of the disk. Two files of different sizes cannot be identical.
        original = self.make("one.jpg")
        index = duplicates.Index()
        index.remember(os.path.getsize(original),
                       mover.hash_path(original), original)
        for index_number in range(5):
            fixtures.png(os.path.join(self.directory,
                                      "p%d.png" % index_number))
            index.find(os.path.join(self.directory,
                                    "p%d.png" % index_number))
        self.assertEqual(index.hashed, 0,
                         "nothing shared a size, so nothing should be read")

    def test_a_copy_of_something_since_deleted_is_not_a_duplicate(self):
        original = self.make("one.jpg")
        copy = os.path.join(self.directory, "two.jpg")
        shutil.copy2(original, copy)
        index = duplicates.Index()
        index.remember(os.path.getsize(original),
                       mover.hash_path(original), original)
        os.unlink(original)
        self.assertIsNone(index.find(copy),
                          "there is nothing left for it to be a copy of")

    def test_a_planned_file_counts_before_it_exists(self):
        # Two copies arriving together: the first is only planned, so
        # requiring it to exist would miss every pair of this kind.
        first = self.make("one.jpg")
        second = os.path.join(self.directory, "two.jpg")
        shutil.copy2(first, second)
        index = duplicates.Index()
        index.remember(os.path.getsize(first), mover.hash_path(first),
                       "/nowhere/one.jpg", must_exist=False)
        self.assertEqual(index.find(second), "/nowhere/one.jpg")

    def test_a_file_is_never_its_own_duplicate(self):
        original = self.make("one.jpg")
        index = duplicates.Index()
        index.remember(os.path.getsize(original),
                       mover.hash_path(original), original)
        self.assertIsNone(index.find(original))

    def test_empty_files_are_not_all_duplicates_of_each_other(self):
        for name in ("a.txt", "b.txt"):
            open(os.path.join(self.directory, name), "wb").close()
        index = duplicates.Index()
        index.remember(0, "", os.path.join(self.directory, "a.txt"))
        self.assertIsNone(index.find(os.path.join(self.directory, "b.txt")))

    def test_annotate_sets_the_fact_a_rule_can_match(self):
        original = self.make("one.jpg")
        copy = os.path.join(self.directory, "two.jpg")
        shutil.copy2(original, copy)
        index = duplicates.Index()
        index.remember(os.path.getsize(original),
                       mover.hash_path(original), original)
        record = evidence.Record(copy)
        duplicates.annotate(record, index, copy)
        self.assertEqual(record.value("duplicate_of"), original)
        self.assertEqual(record.confidence("duplicate_of"), evidence.CERTAIN)


class ThroughAPlan(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        self.home = os.path.join(self.directory, "home")
        os.makedirs(self.root)
        os.makedirs(self.home)
        self.state = os.path.join(self.directory, "state.db")
        with open(os.path.join(self.directory, "rules.ini"), "w") as handle:
            handle.write("""
[settings]
dry_run = no
settle_seconds = 0

[watch]
folders = {root}
depth = 0

[rule: duplicates]
when = duplicate_of is set
into = {home}/Duplicates

[rule: images]
when = kind = image
into = {home}/Pictures
""".format(root=self.root, home=self.home))
        self.rule_set = rules.load(os.path.join(self.directory, "rules.ini"))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def drop(self, name):
        path = fixtures.jpeg(os.path.join(self.root, name))
        os.utime(path, (time.time() - 600,) * 2)
        return path

    def apply(self, journal):
        for _attempt in range(2):
            plan = sorter.build_plan(self.root, self.rule_set,
                                     journal=journal)
            if not plan.items:
                break
            sorter.execute(plan, self.rule_set, journal, dry_run=False)

    def test_a_copy_of_an_already_filed_file_is_recognised(self):
        original = self.drop("holiday.jpg")
        with ledger.Ledger(self.state) as journal:
            self.apply(journal)
            filed = os.path.join(self.home, "Pictures", "holiday.jpg")
            self.assertTrue(os.path.exists(filed))

            copy = os.path.join(self.root, "holiday copy.jpg")
            shutil.copy2(filed, copy)
            os.utime(copy, (time.time() - 600,) * 2)
            plan = sorter.build_plan(self.root, self.rule_set,
                                     journal=journal)
            self.assertEqual(len(plan.items), 1)
            self.assertEqual(plan.items[0].rule_name, "duplicates")
        self.assertFalse(os.path.exists(original))

    def test_two_copies_arriving_together_keep_one(self):
        first = self.drop("poster.jpg")
        second = os.path.join(self.root, "poster (1).jpg")
        shutil.copy2(first, second)
        os.utime(second, (time.time() - 600,) * 2)
        with ledger.Ledger(self.state) as journal:
            plan = sorter.build_plan(self.root, self.rule_set,
                                     journal=journal)
            rules_used = sorted(item.rule_name for item in plan.items)
        self.assertEqual(rules_used, ["duplicates", "images"],
                         "one copy should be kept and one flagged")

    def test_without_a_ledger_a_plan_still_works(self):
        self.drop("holiday.jpg")
        plan = sorter.build_plan(self.root, self.rule_set)
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].rule_name, "images")

    def test_nothing_is_deleted(self):
        first = self.drop("poster.jpg")
        second = os.path.join(self.root, "poster (1).jpg")
        shutil.copy2(first, second)
        os.utime(second, (time.time() - 600,) * 2)
        with ledger.Ledger(self.state) as journal:
            self.apply(journal)
        remaining = []
        for directory, _dirs, names in os.walk(self.home):
            remaining.extend(names)
        self.assertEqual(len(remaining), 2,
                         "both copies must survive; auto-sort never deletes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
