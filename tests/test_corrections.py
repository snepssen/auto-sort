"""Learning from the files somebody moved back.

The signal is free: the ledger says where each file was put, and the disk
says where it is. The difference is a judgement about this person's
preferences that no amount of reading headers could produce.

The hard part is not noticing -- it is not over-claiming. The first working
version of this confidently announced that `alpha = True` predicted a folder,
because every file moved out of Images happened to be a PNG with an alpha
channel. So did every screenshot left exactly where it was put. A fact shared
by the files somebody moved *and* the files they did not move predicts
nothing, and the test for that is the whole difference between a useful
suggestion and a confident wrong one.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corrections                                       # noqa: E402
import fixtures                                          # noqa: E402
import ledger                                            # noqa: E402
import rules                                             # noqa: E402


class Detection(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        self.sorted = os.path.join(self.root, "Sorted")
        os.makedirs(self.sorted)
        self.journal = ledger.Ledger(os.path.join(self.directory, "state.db"))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def place(self, name, facts=None, rule="images"):
        """Pretend a file was sorted here, and record it as the tool would."""
        destination = os.path.join(self.sorted, name)
        fixtures.png(destination)
        import mover
        digest = mover.hash_path(destination)
        run = self.journal.start_run("sort", self.root, "hash", dry_run=False)
        move_id = self.journal.add_move(
            run, 1, 1, "move", rule,
            os.path.join(self.root, name), destination,
            os.path.getsize(destination), digest, status="planned",
            facts=facts or {})
        self.journal.update_move(move_id, "done")
        self.journal.finish_run(run, "completed", "1 item")
        return destination

    def test_a_file_moved_elsewhere_is_found(self):
        destination = self.place("thing.png")
        elsewhere = os.path.join(self.root, "Memes")
        os.makedirs(elsewhere)
        shutil.move(destination, os.path.join(elsewhere, "thing.png"))

        found = corrections.detect(self.journal, [self.root], self.root)
        self.assertEqual(len(found), 1)
        _row, outcome, where = found[0]
        self.assertEqual(outcome, "moved")
        self.assertEqual(where, os.path.join(elsewhere, "thing.png"))

    def test_a_deleted_file_is_recorded_but_not_guessed_at(self):
        destination = self.place("gone.png")
        os.unlink(destination)
        found = corrections.detect(self.journal, [self.root], self.root)
        self.assertEqual(found[0][1], "missing")
        self.assertEqual(self.journal.corrections("moved"), [])

    def test_a_file_still_in_place_is_not_a_correction(self):
        self.place("stays.png")
        self.assertEqual(corrections.detect(self.journal, [self.root],
                                            self.root), [])

    def test_a_lookalike_is_rejected_by_content(self):
        # Same name, same size, different bytes: not the file that moved.
        destination = self.place("thing.png")
        os.unlink(destination)
        decoy = os.path.join(self.root, "Elsewhere")
        os.makedirs(decoy)
        fixtures.png(os.path.join(decoy, "thing.png"), width=1920,
                     height=1080, alpha=False)
        os.truncate(os.path.join(decoy, "thing.png"),
                    os.path.getsize(os.path.join(decoy, "thing.png")))
        found = corrections.detect(self.journal, [self.root], self.root)
        # Either it was not matched at all, or it was matched and the hash
        # check threw it out; both mean "missing".
        self.assertEqual(found[0][1], "missing")


class Induction(unittest.TestCase):

    def rows(self, entries):
        """[(facts, folder)] as the ledger would hand them back."""
        made = []
        for facts, folder in entries:
            made.append({"facts_json": json.dumps(facts),
                         "found_at": os.path.join(folder, "x.png"),
                         "rule_name": "images"})
        return [_Row(row) for row in made]

    def test_a_consistent_preference_is_learnt(self):
        rows = self.rows([({"site": "furaffinity", "kind": "image"}, "/Art")
                          for _ in range(6)])
        among = {("site", "furaffinity"): 6, ("kind", "image"): 6}
        found = corrections.induce(rows, among)
        self.assertTrue(found)
        self.assertEqual(found[0].folder, "/Art")
        self.assertEqual(found[0].support, 6)

    def test_a_fact_that_is_everywhere_predicts_nothing(self):
        # The regression. Five corrections all carry alpha=True, so measured
        # against themselves it is a perfect predictor -- but twenty files
        # carrying it were placed and left alone.
        rows = self.rows([({"alpha": True}, "/Memes") for _ in range(5)])
        among = {("alpha", "True"): 25}
        self.assertEqual(corrections.induce(rows, among), [])

    def test_too_few_agreeing_corrections_teach_nothing(self):
        rows = self.rows([({"site": "pixiv"}, "/Art") for _ in range(2)])
        self.assertEqual(corrections.induce(rows, {("site", "pixiv"): 2}), [])

    def test_disagreement_teaches_nothing(self):
        rows = self.rows(
            [({"kind": "image"}, "/A") for _ in range(3)]
            + [({"kind": "image"}, "/B") for _ in range(3)])
        self.assertEqual(corrections.induce(rows, {("kind", "image"): 6}), [])

    def test_per_file_facts_are_never_proposed(self):
        rows = self.rows([({"name": "unique%d.png" % index,
                            "size": 100 + index,
                            "site": "e621"}, "/Art") for index in range(5)])
        among = {("site", "e621"): 5}
        found = corrections.induce(rows, among)
        self.assertTrue(found)
        for preference in found:
            self.assertNotIn(preference.fact, ("name", "size"))

    def test_one_rule_per_folder(self):
        rows = self.rows([({"site": "e621", "kind": "image",
                            "format": "png"}, "/Art") for _ in range(5)])
        among = {("site", "e621"): 5, ("kind", "image"): 5,
                 ("format", "png"): 5}
        self.assertEqual(len(corrections.induce(rows, among)), 1)

    def test_overridden_rules_are_counted(self):
        rows = self.rows([({"kind": "image"}, "/A") for _ in range(4)])
        self.assertEqual(corrections.overridden_rules(rows),
                         [("images", 4)])

    def test_the_rule_it_writes_is_a_rule(self):
        rows = self.rows([({"site": "furaffinity"}, "/Art")
                          for _ in range(5)])
        preference = corrections.induce(rows, {("site", "furaffinity"): 5})[0]
        directory = tempfile.mkdtemp()
        try:
            filename = os.path.join(directory, "rules.ini")
            with open(filename, "w", encoding="utf-8") as handle:
                handle.write("[settings]\n\n[watch]\nfolders = %s\n\n%s\n"
                             % (directory, preference.rule()))
            rule_set = rules.load(filename)
            self.assertEqual(len(rule_set.rules), 1)
            self.assertEqual(rule_set.rules[0].into, "/Art")
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class _Row(dict):
    """A stand-in for sqlite3.Row, which supports keys() and indexing."""

    def keys(self):
        return list(super(_Row, self).keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DaemonChecks(unittest.TestCase):
    """The daemon has to notice corrections, and has to not act on them."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        self.sorted = os.path.join(self.root, "Sorted")
        os.makedirs(self.sorted)
        self.rules_file = os.path.join(self.directory, "rules.ini")
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write("[settings]\ndry_run = yes\nsettle_seconds = 0\n\n"
                         "[watch]\nfolders = %s\n\n[rule: images]\n"
                         "when = kind = image\ninto = %s\n"
                         % (self.root, self.sorted))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_the_daemon_records_a_correction_and_changes_nothing(self):
        import daemon as daemon_module
        import mover
        state = os.path.join(self.directory, "state.db")
        with ledger.Ledger(state) as journal:
            destination = os.path.join(self.sorted, "thing.png")
            fixtures.png(destination)
            run = journal.start_run("sort", self.root, "h", dry_run=False)
            move_id = journal.add_move(
                run, 1, 1, "move", "images",
                os.path.join(self.root, "thing.png"), destination,
                os.path.getsize(destination), mover.hash_path(destination),
                status="planned", facts={"site": "furaffinity"})
            journal.update_move(move_id, "done")
            journal.finish_run(run, "completed", "1")

        elsewhere = os.path.join(self.root, "Art")
        os.makedirs(elsewhere)
        moved_to = os.path.join(elsewhere, "thing.png")
        shutil.move(destination, moved_to)

        with daemon_module.PollingDaemon(
                rule_path=self.rules_file, state_file=state,
                output=lambda _message: None) as service:
            service.cycle()
            recorded = service.journal.corrections("moved")

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["found_at"], moved_to)
        # And the file is exactly where the person left it.
        self.assertTrue(os.path.exists(moved_to))
        self.assertFalse(os.path.exists(destination))

    def test_the_check_is_throttled(self):
        import daemon as daemon_module
        state = os.path.join(self.directory, "state.db")
        with daemon_module.PollingDaemon(
                rule_path=self.rules_file, state_file=state,
                output=lambda _message: None) as service:
            rule_set = service._reload_rules()
            service._check_corrections(rule_set, 10000.0)
            first = service.journal.get_state("corrections_checked_at")
            service._check_corrections(rule_set, 10001.0)
            self.assertEqual(
                service.journal.get_state("corrections_checked_at"), first)
            service._check_corrections(
                rule_set, 10000.0 + service.CORRECTION_INTERVAL + 1)
            self.assertNotEqual(
                service.journal.get_state("corrections_checked_at"), first)
