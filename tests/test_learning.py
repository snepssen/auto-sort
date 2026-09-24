"""The background sorter adding the categories its waiting documents show.

`check-rules` used to say which words had earned a folder and leave the
rest to somebody running `adopt`. The daemon does that last step now --
from documents still waiting in a holding folder only, never again for a
word whose rule somebody deleted -- and the new rules file moves the
waiting documents into their folder.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autosort                                          # noqa: E402
import daemon                                            # noqa: E402
import ledger                                            # noqa: E402
import learning                                          # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402

RULES = """
[settings]
dry_run = no
{setting}

[watch]
folders = {root}

[rule: letters]
when = heading contains Brief
into = {out}/Letters

[rule: waiting]
when = kind = document
into = {out}/Unfiled
holding = yes
"""

SENDERS = ("Acme Widgets", "Northwind Traders", "Globex Industries",
           "Initech Services")


class Learning(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-learning-")
        self.root = os.path.join(self.dir, "in")
        self.out = os.path.join(self.dir, "out")
        self.documents = os.path.join(self.dir, "Documents")
        self.rules = os.path.join(self.dir, "rules.ini")
        self.state = os.path.join(self.dir, "state.db")
        os.makedirs(self.root)
        # Where learnt categories go: never the real Documents folder.
        patcher = mock.patch("userdirs.home_for",
                             side_effect=lambda kind, sub="": self.documents)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.number = 0
        for word in ("Invoice", "Contract", "Payslip"):
            for _ in range(4):
                self.place(word)
        for _ in range(3):
            self.place("Brief", waiting=False)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def place(self, word, waiting=True):
        """A document headed `word`, filed where the ledger says."""
        self.number += 1
        heading = "%s %d from %s for the period ending in March" % (
            word, self.number, SENDERS[self.number % len(SENDERS)])
        folder = os.path.join(self.out, "Unfiled" if waiting else "Letters")
        os.makedirs(folder, exist_ok=True)
        name = "document %02d.txt" % self.number
        path = os.path.join(folder, name)
        with open(path, "w") as handle:
            handle.write(heading + "\n\nThe details follow below.\n")
        with ledger.Ledger(self.state) as journal:
            run = journal.start_run("sort", source_root=self.root,
                                    dry_run=False)
            journal.add_move(run, 1, 1, "move",
                             "waiting" if waiting else "letters",
                             os.path.join(self.root, name), path, 1, "",
                             status="done",
                             facts={"kind": "document", "heading": heading},
                             holding=waiting)
            journal.finish_run(run, "completed")
        return path

    def write(self, setting=""):
        with open(self.rules, "w") as handle:
            handle.write(RULES.format(root=self.root, out=self.out,
                                      setting=setting))

    def text(self):
        with open(self.rules) as handle:
            return handle.read()

    def run_daemon(self, moments, messages=None):
        messages = [] if messages is None else messages
        with daemon.PollingDaemon(self.rules, self.state, port=0,
                                  output=messages.append) as service:
            for moment in moments:
                service.cycle(now_value=moment)
        return messages

    def test_it_learns_and_the_waiting_documents_move_in(self):
        self.write()
        messages = self.run_daemon(range(1000, 1006))
        text = self.text()
        for word in ("Invoice", "Contract", "Payslip"):
            self.assertIn("[rule: what the page calls itself: %s]" % word,
                          text)
            self.assertEqual(
                len(os.listdir(os.path.join(self.documents, word))), 4,
                messages)
        self.assertTrue(any(message.startswith("Learnt ")
                            for message in messages), messages)
        # Above the holding rule, or it could never fire.
        self.assertLess(text.index("calls itself: Invoice]"),
                        text.index("[rule: waiting]"))
        self.assertTrue(os.path.exists(self.rules + ".before-adopt"))
        rules.load(self.rules)

    def test_it_never_takes_a_document_from_its_category(self):
        # Three letters with a real rule of their own: a word at the top of
        # them is not a waiting room to empty.
        self.write()
        self.run_daemon(range(1000, 1004))
        self.assertNotIn("calls itself: Brief", self.text())
        self.assertEqual(
            len(os.listdir(os.path.join(self.out, "Letters"))), 3)

    def test_a_category_somebody_deleted_stays_deleted(self):
        self.write()
        self.run_daemon(range(1000, 1004))
        text = self.text()
        start = text.index("[rule: what the page calls itself: Payslip]")
        end = text.index("\n\n", start) + 2
        with open(self.rules, "w") as handle:
            handle.write(text[:start] + text[end:])
        # Half an hour and more later, and again the day after.
        self.run_daemon([4000, 4001, 4002, 90000, 90001])
        self.assertNotIn("calls itself: Payslip]", self.text())
        with ledger.Ledger(self.state) as journal:
            self.assertIn("Payslip",
                          json.loads(journal.get_state("learn_refused")))

    def test_report_says_so_once_and_writes_nothing(self):
        self.write("learn = report")
        before = self.text()
        messages = self.run_daemon([1000, 1001, 5000, 5001])
        self.assertEqual(self.text(), before)
        said = [message for message in messages
                if message.startswith("Waiting documents have shown")]
        self.assertEqual(len(said), 1, messages)
        self.assertIn("Invoice (4)", said[0])

    def test_off_never_looks(self):
        self.write("learn = off")
        before = self.text()
        self.run_daemon([1000, 1001, 5000])
        self.assertEqual(self.text(), before)

    def test_soon_after_something_new_is_filed(self):
        self.write("learn = report")
        with daemon.PollingDaemon(self.rules, self.state, port=0,
                                  output=[].append) as service:
            service.cycle(now_value=1000)
            asked = []
            original = learning.plan

            def counting(*args, **kwargs):
                asked.append(1)
                return original(*args, **kwargs)
            with mock.patch.object(learning, "plan", side_effect=counting):
                service.cycle(now_value=1090)      # nothing new: not yet
                self.assertEqual(asked, [])
                self.place("Invoice")
                service.cycle(now_value=1100)      # something new
                self.assertEqual(len(asked), 1)
                service.cycle(now_value=1120)      # within the minute
                self.assertEqual(len(asked), 1)

    def test_the_adopt_command_writes_the_same_rules(self):
        self.write()
        heard = io.StringIO()
        with contextlib.redirect_stdout(heard):
            self.assertEqual(autosort.adopt_categories(
                self.rules, self.state, apply_changes=True), 0)
        text = self.text()
        self.assertIn("[rule: what the page calls itself: Invoice]", text)
        self.assertIn("Added to", heard.getvalue())
        rules.load(self.rules)


class AfterLearning(Learning):
    """A learnt category is not a person's edit.

    Every rules revision needs a preview before the background sorter moves
    anything under it, and a preview in the sweep pauses the daemon. So a
    category auto-sort added by itself paused sorting at the next download,
    until somebody noticed and clicked Resume -- a program that stops the
    moment it learns something. The gate exists for what a person writes.

    With `regroup = report` nothing else previews the new revision first,
    so every download after learning met the gate; with `apply` it was
    only luck of the order that the promotion pass usually got there first.
    """

    def approve_current_rules(self):
        """What reviewing the first preview and resuming leaves behind."""
        with ledger.Ledger(self.state) as journal:
            run = journal.start_run("sort", source_root=self.root,
                                    dry_run=True)
            journal.finish_run(run, "completed")
            journal.record_preview(self.root,
                                   sorter.rules_hash(rules.load(self.rules)),
                                   run)

    def arrive(self, name="arrived.txt"):
        path = os.path.join(self.root, name)
        with open(path, "w") as handle:
            handle.write("Something new, just downloaded.\n")
        os.utime(path, (900, 900))
        return path

    def test_a_download_after_learning_is_sorted_without_pausing(self):
        self.write("regroup = report")
        self.approve_current_rules()
        messages = self.run_daemon(range(1000, 1006))
        self.assertIn("calls itself: Invoice]", self.text())
        arrived = self.arrive()
        messages = self.run_daemon(range(2000, 2020), messages)
        self.assertFalse(os.path.exists(arrived), messages)
        with ledger.Ledger(self.state) as journal:
            self.assertFalse(journal.paused(), messages)
            summaries = [row["summary"] for row in journal.connection.execute(
                "SELECT summary FROM runs WHERE action = 'learn'")]
        self.assertIn("no preview needed: only a learnt category was added",
                      summaries)

    def test_an_edit_by_a_person_still_gets_its_preview(self):
        self.write("regroup = report")
        self.approve_current_rules()
        self.run_daemon(range(1000, 1006))
        with open(self.rules, "a") as handle:
            handle.write("\n[rule: somebody's own]\nwhen = name = x\n"
                         "into = %s/Mine\n" % self.out)
        arrived = self.arrive()
        messages = self.run_daemon(range(2000, 2020))
        self.assertTrue(os.path.exists(arrived), messages)
        with ledger.Ledger(self.state) as journal:
            self.assertTrue(journal.paused(), messages)

    def test_learning_before_anything_was_approved_skips_nothing(self):
        """Only a revision already approved is carried over: a learnt
        category added to rules nobody has previewed yet is still new."""
        self.write("regroup = report")
        self.run_daemon(range(1000, 1006))
        with ledger.Ledger(self.state) as journal:
            self.assertFalse(journal.has_preview(
                self.root, sorter.rules_hash(rules.load(self.rules))))


if __name__ == "__main__":
    unittest.main()
