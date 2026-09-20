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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autosort                                          # noqa: E402
import bundles                                           # noqa: E402
import fixtures                                          # noqa: E402
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

    # -- the file parses and is internally sound --------------------------

    def test_the_example_parses(self):
        self.assertTrue(self.rule_set.rules)
        self.assertTrue(self.rule_set.settings.dry_run,
                        "the example must never ship with dry run off")

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

    def test_paperwork_by_keyword_needs_its_own_lower_floor(self):
        # `paperwork` is read out of the filename and is weak evidence. This
        # rule only fires because it lowers min_confidence for itself.
        self.assertClaimedBy(
            self.build("Invoice 4021.pdf", fixtures.pdf,
                       producer="Microsoft Word"),
            "paperwork by name")

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

    def test_a_missing_rules_file_says_what_to_do(self):
        with self.assertRaises(rules.RuleError) as caught:
            rules.load(os.path.join(self.directory, "absent.ini"))
        self.assertIn("auto-sort init", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
