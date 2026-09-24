"""The starter rules file has to actually fire.

A rules file that parses and then silently does nothing is the worst outcome
this project has, because it looks like success. The example shipped with
auto-sort is the first thing anybody runs, so it is tested the same way the
code is: build a file of each kind it claims to handle, and assert which rule
claims it.

This suite exists because of one bug. Two rules filed by `{happened}`, which
is the best date available -- and for a file carrying no capture metadata that
is the date it arrived, which is weak evidence and does not clear the default
confidence floor. Both rules parsed, validated, matched their `when`, and then
declined at the destination. Nothing in the test suite noticed, because
nothing tested what the example does to real files.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autosort                                          # noqa: E402
import bundles                                           # noqa: E402
import fixtures
import names                                          # noqa: E402
import identify                                          # noqa: E402
import paths                                             # noqa: E402
import rules                                             # noqa: E402


class StarterRules(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rule_set = rules.load(paths.example_rules_file())

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def build(self, name, maker, **kwargs):
        path = maker(os.path.join(self.directory, name), **kwargs)
        old = time.time() - 600
        os.utime(path, (old, old))
        return path

    def claim(self, path):
        """(rule name or None, why not) for one file under the example rules."""
        item = bundles.Item(os.path.abspath(path))
        record = identify.identify(item)
        decision, near_miss = self.rule_set.decide(record,
                                                   source_root=self.directory)
        if decision is not None:
            return decision.rule.name, None
        return None, (near_miss.reason if near_miss else "no rule matched")

    def assertClaimedBy(self, path, expected):
        actual, why = self.claim(path)
        self.assertEqual(actual, expected,
                         "%s was claimed by %r, not %r%s"
                         % (os.path.basename(path), actual, expected,
                            " (%s)" % why if why else ""))

    def claim_from(self, path, origin):
        """As `claim`, for a file the system says arrived by `origin`."""
        import evidence
        record = identify.identify(bundles.Item(os.path.abspath(path)))
        record.set("origin", origin, "quarantine", evidence.STRONG)
        decision, _miss = self.rule_set.decide(record,
                                               source_root=self.directory)
        return decision

    def test_attachments_wait_with_the_attachments(self):
        """Where a file came from, before a folder named only by its type,
        and after every rule that knows what the file actually is."""
        document = self.build("minutes.docx", fixtures.docx)
        picture = self.build("image.png", fixtures.png, alpha=False)
        photo = self.build("IMG_0001.JPG", fixtures.jpeg)
        for path, origin, rule in (
                (document, "email", "email attachments -- documents"),
                (picture, "message", "message media -- images"),
                (photo, "email", "camera photos")):
            decision = self.claim_from(path, origin)
            self.assertEqual(decision.rule.name if decision else None, rule,
                             os.path.basename(path))
        decision = self.claim_from(document, "email")
        self.assertTrue(decision.rule.holding)
        self.assertNotIn(os.sep + "Downloads" + os.sep, decision.destination)

    def test_nothing_is_filed_back_into_downloads(self):
        for rule in self.rule_set.rules:
            if rule.into and rule.mode != "leave":
                self.assertNotIn("/Downloads/", rule.into.replace(os.sep, "/"),
                                 rule.name)

    def test_encrypted_pdfs_that_cannot_be_read_wait_apart(self):
        import test_pdfcrypt
        from readers import pdfcrypt
        pdfcrypt._known = []
        try:
            for name, data in (
                    ("locked.pdf", test_pdfcrypt._rc4_pdf(user=bytes(32))),
                    ("aes256.pdf", test_pdfcrypt._aes_256_pdf())):
                path = self.build(name, fixtures.text, body=data)
                self.assertClaimedBy(path, "encrypted PDFs")
                record = identify.identify(bundles.Item(path))
                decision, _miss = self.rule_set.decide(
                    record, source_root=self.directory)
                self.assertRegex(os.path.dirname(decision.destination),
                                 r"/Documents/PDF/Encrypted/\d{4}-\d{2}-\d{2}$")
            # One that opens is read, and goes wherever its words say.
            path = self.build("open.pdf", fixtures.text,
                              body=test_pdfcrypt._rc4_pdf())
            self.assertNotEqual(self.claim(path)[0], "encrypted PDFs")
        finally:
            pdfcrypt.forget()

    # -- the file parses and is internally sound --------------------------

    def test_the_example_parses(self):
        self.assertTrue(self.rule_set.rules)
        self.assertTrue(self.rule_set.settings.dry_run,
                        "the example must never ship with dry run off")

    def test_it_passes_its_own_check(self):
        """A folder named only by a file's type is a waiting room.

        Left unmarked, the documents in `Documents/PDF` counted as claimed
        by a real category: nothing learnt from them was ever suggested,
        and nothing adopted could move them out.
        """
        heard = io.StringIO()
        with contextlib.redirect_stdout(heard):
            autosort._report_unpromotable(self.rule_set)
        self.assertEqual(heard.getvalue(), "")

    def test_scans_wait_to_be_read(self):
        """`Scans/<year>` names a folder only by how the file was made,
        which is a waiting room -- `propose` already says so of its own
        scan facet -- and a scanned invoice read by OCR has to be able to
        leave it for `Invoice` like any other invoice."""
        rule = [rule for rule in self.rule_set.rules
                if rule.name == "scanned paperwork"][0]
        import ledger
        import review
        with ledger.Ledger(os.path.join(self.directory, "state.db")) \
                as journal:
            run = journal.start_run("sort", source_root=self.directory,
                                    dry_run=False)
            number = 0
            for word, rule_name in (("Invoice", "scanned paperwork"),
                                    ("Contract", "documents"),
                                    ("Payslip", "documents")):
                for sender in ("Acme Widgets", "Northwind", "Globex"):
                    number += 1
                    journal.add_move(
                        run, number, 1, "move", rule_name,
                        os.path.join(self.directory, "d%d.pdf" % number),
                        os.path.join(self.directory, "o", "d%d.pdf" % number),
                        1, "", status="done",
                        holding=[rule.holding for rule in self.rule_set.rules
                                 if rule.name == rule_name][0],
                        facts={"kind": "document", "capture": "scan",
                               "heading": "%s %d from %s" % (
                                   word, number, sender)})
            found, _seen = review.emerging(journal, self.rule_set,
                                           waiting_only=True)
        self.assertIn("Invoice", [word for word, _count in found])
        self.assertTrue(rule.holding)

    def test_every_rule_has_a_destination_or_says_leave(self):
        for rule in self.rule_set.rules:
            if rule.mode == "leave":
                continue
            self.assertTrue(rule.into,
                            "rule %r has no destination" % rule.name)

    def test_rule_names_are_unique(self):
        names = [rule.name for rule in self.rule_set.rules]
        self.assertEqual(len(names), len(set(names)))

    # -- and it claims what it says it claims -----------------------------

    def test_screenshot(self):
        self.assertClaimedBy(
            self.build("Screenshot 2026-09-19 at 14.03.22.png", fixtures.png),
            "screenshots")

    def test_camera_photograph(self):
        self.assertClaimedBy(self.build("IMG_4021.jpg", fixtures.jpeg),
                             "camera photos")

    def test_messaging_export_beats_camera(self):
        # Named as a WhatsApp export and still carrying camera tags: it is a
        # picture somebody sent, not one this person took.
        self.assertClaimedBy(
            self.build("IMG-20260919-WA0001.jpg", fixtures.jpeg),
            "phone and messaging pictures")

    def test_loose_image(self):
        self.assertClaimedBy(
            self.build("whatever.png", fixtures.png, width=700, height=500),
            "loose images")

    def test_episode(self):
        self.assertClaimedBy(
            self.build("Some.Show.S03E07.1080p.WEB-DL-NTb.mkv", fixtures.mp4),
            "episodes")

    def test_film(self):
        self.assertClaimedBy(
            self.build("Some.Film.2019.1080p.BluRay-GRP.mp4", fixtures.mp4,
                       seconds=5400),
            "films")

    def test_screen_recording(self):
        self.assertClaimedBy(
            self.build("Screen Recording 2026-09-19 at 09.10.11.mov",
                       fixtures.mp4),
            "screen recordings")

    def test_phone_clip_files_by_arrival_date(self):
        # The regression this suite was written for: a portrait clip carries
        # no capture date, so a rule filing it by `{happened}` never fires.
        self.assertClaimedBy(
            self.build("clip.mp4", fixtures.mp4, width=1080, height=1920,
                       seconds=40),
            "phone clips")

    def test_tagged_music(self):
        self.assertClaimedBy(self.build("07 Song.mp3", fixtures.mp3),
                             "tagged music")

    def test_sample(self):
        self.assertClaimedBy(
            self.build("kick_01_128bpm.wav", fixtures.wav, seconds=1.5),
            "sample packs")

    def test_voice_recording_files_by_arrival_date(self):
        self.assertClaimedBy(
            self.build("memo.wav", fixtures.wav, seconds=120, channels=1),
            "voice recordings")

    def test_scanned_document(self):
        self.assertClaimedBy(self.build("Scan 2026-09-19.pdf", fixtures.pdf),
                             "scanned paperwork")

    def test_the_example_asks_for_no_fact_that_nothing_sets(self):
        """A shipped example referencing a dead fact is a broken example.

        The paperwork rule was exactly that when its word list was removed:
        it stayed behind asking for `paperwork is set`, could never match,
        and sent every document to the catch-all without saying why. A rule
        that cannot fire is worse than no rule, because the file it was
        meant to place still moves -- just somewhere else, quietly.
        """
        dead = {"paperwork"}
        for rule in self.rule_set.rules:
            asked = rule.condition.facts_used() | rule.template_facts
            self.assertFalse(
                asked & dead,
                "rule %r asks for %s, which nothing produces any more"
                % (rule.name, ", ".join(sorted(asked & dead))))

    def test_ordinary_document(self):
        self.assertClaimedBy(self.build("notes.docx", fixtures.docx),
                             "documents")

    def test_model(self):
        self.assertClaimedBy(
            self.build("model.obj", fixtures.text,
                       body="v 1.0 2.0 3.0\nv 4.0 5.0 6.0\nf 1 2\n"),
            "3d models")

    def test_lone_subtitle(self):
        self.assertClaimedBy(
            self.build("orphan.srt", fixtures.text,
                       body="1\n00:00:01,000 --> 00:00:04,000\nHi\n"),
            "subtitles on their own")

    def test_os_litter_is_left_alone(self):
        path = self.build(".DS_Store", fixtures.text,
                          body=b"\x00\x00\x00\x01Bud1")
        name, _why = self.claim(path)
        self.assertEqual(name, "os litter")
        rule = [r for r in self.rule_set.rules if r.name == "os litter"][0]
        self.assertEqual(rule.mode, "leave")


class Init(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.target = os.path.join(self.directory, "config", "rules.ini")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def run_init(self):
        """init talks to a person; the test suite does not need to hear it."""
        with contextlib.redirect_stdout(io.StringIO()):
            return autosort.init(self.target)

    def test_init_writes_a_file_that_loads(self):
        self.assertEqual(self.run_init(), 0)
        self.assertTrue(os.path.exists(self.target))
        rule_set = rules.load(self.target)
        self.assertTrue(rule_set.rules)

    def test_init_refuses_to_overwrite(self):
        self.assertEqual(self.run_init(), 0)
        with open(self.target, "a", encoding="utf-8") as handle:
            handle.write("\n; a change somebody made\n")
        self.assertEqual(self.run_init(), 1)
        with open(self.target, "r", encoding="utf-8") as handle:
            self.assertIn("a change somebody made", handle.read())

    def test_init_writes_this_machines_own_folders(self):
        """A German Linux desktop's Bilder, not a second Pictures beside it."""
        import userdirs
        home = userdirs.home()
        own = {"pictures": os.path.join(home, "Bilder"),
               "video": os.path.join(home, "Videos"),
               "music": os.path.join(home, "Musik"),
               "documents": os.path.join(home, "Dokumente"),
               "downloads": os.path.join(home, "Downloads")}
        with mock.patch.object(userdirs, "all_dirs", return_value=own):
            self.assertEqual(self.run_init(), 0)
        with open(self.target, "r", encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("into = ~/Bilder/Screenshots/", body)
        self.assertIn("into = ~/Dokumente/{ext:upper}\n", body)
        self.assertNotIn("~/Pictures/", body)
        self.assertNotIn("~/Documents/", body)
        self.assertNotIn("~/Movies", body)

    def test_one_name_for_the_video_folder(self):
        """Movies on a Mac, Videos elsewhere -- never both in one file."""
        import userdirs
        with open(paths.example_rules_file(), encoding="utf-8") as handle:
            body = userdirs.localise(handle.read())
        video = userdirs.short(userdirs.path("video"))
        other = "~/Videos" if video.endswith("Movies") else "~/Movies"
        self.assertIn("into = %s/" % video, body)
        self.assertNotIn(other + "/", body)

    def test_a_missing_rules_file_says_what_to_do(self):
        with self.assertRaises(rules.RuleError) as caught:
            rules.load(os.path.join(self.directory, "absent.ini"))
        self.assertIn("auto-sort init", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
