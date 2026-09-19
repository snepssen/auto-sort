"""Rules are decisions about files, so their failure mode must be declining.

Bad configuration is rejected when it is loaded.  Missing evidence, a weak
fact, or a template that cannot be filled makes that rule decline and lets the
next one run; none of those conditions may invent a destination.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evidence                                          # noqa: E402
import paths                                             # noqa: E402
import rules                                             # noqa: E402


class Expressions(unittest.TestCase):

    def test_precedence_parentheses_and_not(self):
        expression = rules.parse(
            "kind = image or (kind = video and not duration > 30m)")
        self.assertTrue(expression.test({"kind": "image", "duration": 99999}))
        self.assertTrue(expression.test({"kind": "video", "duration": 60}))
        self.assertFalse(expression.test({"kind": "video", "duration": 4000}))

    def test_missing_facts_only_match_is_unset(self):
        self.assertFalse(rules.parse("camera = Canon").test({}))
        self.assertFalse(rules.parse("camera != Canon").test({}))
        self.assertTrue(rules.parse("camera is unset").test({}))

    def test_boolean_words_do_not_make_every_nonempty_string_true(self):
        self.assertFalse(rules.parse("title = yes").test({"title": "Maybe"}))
        self.assertTrue(rules.parse("lossless = yes").test({"lossless": True}))

    def test_sizes_durations_globs_regex_and_lists(self):
        facts = {"size": 6 * 1024 * 1024, "duration": 65,
                 "name": "Invoice 19.PDF", "format": "pdf", "stem": "IMG_0042"}
        self.assertTrue(rules.parse("size > 5mb").test(facts))
        self.assertTrue(rules.parse("duration between 1m and 2m").test(facts))
        self.assertTrue(rules.parse("name ~ *invoice*").test(facts))
        self.assertTrue(rules.parse(r"stem re ^IMG_\d{4}$").test(facts))
        self.assertTrue(rules.parse("format in docx, pdf, odt").test(facts))

    def test_list_does_not_consume_a_closing_parenthesis(self):
        expression = rules.parse("(format in jpg, png) and kind = image")
        self.assertTrue(expression.test({"format": "png", "kind": "image"}))

    def test_bad_expression_names_its_column(self):
        with self.assertRaises(rules.RuleError):
            rules.parse("kind = image and")


class TemplatesAndPaths(unittest.TestCase):

    def test_dates_numbers_and_fallbacks(self):
        facts = {"taken": "2026-09-19 14:03:22", "track": 7}
        rendered = rules.render_template(
            "Pictures/{taken:%Y-%m}/{artist|Unknown}/{track:02}.flac", facts)
        self.assertEqual(rendered,
                         os.path.join("Pictures", "2026-09", "Unknown",
                                      "07.flac"))

    def test_missing_fact_does_not_become_unknown(self):
        with self.assertRaises(Exception) as caught:
            rules.render_template("Music/{artist}/{album}", {"artist": "A"})
        self.assertEqual(caught.exception.__class__.__name__, "_MissingFact")

    def test_tag_cannot_create_path_components(self):
        rendered = rules.render_template("Music/{artist}", {"artist": "AC/DC"})
        self.assertEqual(rendered, os.path.join("Music", "AC-DC"))

    def test_reserved_and_awkward_names_are_portable(self):
        self.assertEqual(paths.sanitise("CON.txt"), "_CON.txt")
        self.assertEqual(paths.sanitise("  AC/DC ?  "), "AC-DC")

    def test_unique_never_overwrites(self):
        directory = tempfile.mkdtemp()
        try:
            first = os.path.join(directory, "thing.txt")
            with open(first, "w", encoding="utf-8") as handle:
                handle.write("one")
            self.assertEqual(paths.unique(first),
                             os.path.join(directory, "thing (2).txt"))
            self.assertEqual(paths.unique(first, {
                os.path.join(directory, "thing (2).txt")}),
                os.path.join(directory, "thing (3).txt"))
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class Configuration(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.filename = os.path.join(self.directory, "rules.ini")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def write(self, source):
        with open(self.filename, "w", encoding="utf-8") as handle:
            handle.write(source)
        return rules.load(self.filename)

    def record(self, **facts):
        record = evidence.Record("item")
        for name, value in facts.items():
            confidence = evidence.WEAK if name.startswith("weak_") \
                else evidence.CERTAIN
            stored_name = name[5:] if name.startswith("weak_") else name
            record.set(stored_name, value, "test", confidence)
        return record

    def test_complete_file_loads_without_percent_interpolation(self):
        loaded = self.write("""
[settings]
dry_run = yes ; safe by default
min_confidence = 0.7

[watch]
folders = ~/Downloads, ~/Desktop
ignore = *.part, *.download
depth = 2

[rule: photos]
when = kind = image
into = ~/Pictures/{happened:%Y-%m}
""")
        self.assertTrue(loaded.settings.dry_run)
        self.assertEqual(loaded.watch.depth, 2)
        self.assertEqual(len(loaded.watch.folders), 2)
        self.assertEqual(loaded.rules[0].name, "photos")

    def test_first_match_wins_and_destination_is_explainable(self):
        loaded = self.write("""
[settings]
min_confidence = 0.6

[rule: screenshots]
when = kind = image and capture = screenshot
into = Sorted/{happened:%Y-%m}

[rule: images]
when = kind = image
into = Sorted/Loose
""")
        record = self.record(kind="image", capture="screenshot",
                             happened="2026-09-19 10:00:00",
                             name="shot.png", dir=self.directory)
        result = loaded.decision(record, source_root=self.directory)
        self.assertEqual(result.rule.name, "screenshots")
        self.assertEqual(result.destination, os.path.join(
            self.directory, "Sorted", "2026-09", "shot.png"))
        self.assertTrue(result.trace)

    def test_missing_destination_fact_falls_through(self):
        loaded = self.write("""
[rule: tagged music]
when = kind = audio
into = Music/{artist}/{album}

[rule: loose audio]
when = kind = audio
into = Music/Loose
""")
        record = self.record(kind="audio", artist="Someone",
                             name="song.flac", dir=self.directory)
        evaluations = loaded.evaluate(record, source_root=self.directory)
        self.assertFalse(evaluations[0].matched)
        self.assertIn("album", evaluations[0].reason)
        self.assertTrue(evaluations[1].matched)

    def test_only_consulted_or_branch_is_subject_to_confidence_floor(self):
        loaded = self.write("""
[settings]
min_confidence = 0.8

[rule: visual]
when = kind = image or weak_hint = maybe
into = Sorted
""")
        record = self.record(kind="image", weak_weak_hint="maybe",
                             name="x.png", dir=self.directory)
        self.assertTrue(loaded.decision(record, self.directory).matched)

        # The same is true when the weak, false branch appears first.  The
        # certain right branch is sufficient proof of the expression.
        loaded = self.write("""
[settings]
min_confidence = 0.8

[rule: visual]
when = weak_hint = other or kind = image
into = Sorted
""")
        self.assertTrue(loaded.decision(record, self.directory).matched)

    def test_weak_evidence_declines(self):
        loaded = self.write("""
[settings]
min_confidence = 0.8

[rule: guessed]
when = kind = image and capture = screenshot
into = Sorted
""")
        record = self.record(kind="image", weak_capture="screenshot",
                             name="x.png", dir=self.directory)
        result = loaded.evaluate(record, self.directory)[0]
        self.assertFalse(result.matched)
        self.assertIn("capture 0.45", result.reason)

    def test_weak_template_fact_cannot_choose_a_directory(self):
        loaded = self.write("""
[settings]
min_confidence = 0.8

[rule: music]
when = kind = audio
into = Music/{artist}
""")
        record = self.record(kind="audio", weak_artist="A Guess",
                             name="song.flac", dir=self.directory)
        result = loaded.evaluate(record, self.directory)[0]
        self.assertFalse(result.matched)
        self.assertIn("artist 0.45", result.reason)

    def test_platform_and_age_gates(self):
        loaded = self.write("""
[rule: old mac files]
when = kind = archive
into = Archive
older_than = 30d
only_on = macos
""")
        record = self.record(kind="archive", age=45, name="old.zip",
                             dir=self.directory)
        self.assertFalse(loaded.rules[0].evaluate(
            record, self.directory, platform="linux").matched)
        self.assertTrue(loaded.rules[0].evaluate(
            record, self.directory, platform="macos").matched)

    def test_unknown_key_is_a_configuration_error(self):
        with self.assertRaises(rules.RuleError) as caught:
            self.write("""
[rule: typo]
when = kind = image
into = Pictures
min_confidnce = 0.9
""")
        self.assertIn("min_confidnce", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
