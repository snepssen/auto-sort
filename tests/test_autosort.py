"""Milestone 1: identification, naming and grouping.

Run with `python3 -m unittest discover -s tests` from the repository root, or
`python3 tests/test_autosort.py`.

The cases that earn their place are the ones that have already been wrong
once. Every assertion about a filename detector below corresponds to a real
false positive found while writing it — a Python file read as Markdown, a
camera's frame counter read as a duplicate marker, a tax return read as an
album track. A test suite for a classifier is mostly a record of its mistakes.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundles                                           # noqa: E402
import evidence                                          # noqa: E402
import fixtures                                          # noqa: E402
import identify                                          # noqa: E402
import kinds                                             # noqa: E402
import names                                             # noqa: E402
import signatures                                        # noqa: E402


class Evidence(unittest.TestCase):

    def test_stronger_source_wins_and_the_loser_is_kept(self):
        record = evidence.Record("x")
        record.set("format", "mp3", "extension", evidence.LIKELY)
        record.set("format", "flac", "signature", evidence.CERTAIN)
        self.assertEqual(record.value("format"), "flac")
        self.assertEqual(len(record.conflicts), 1)

    def test_weaker_source_does_not_win(self):
        record = evidence.Record("x")
        record.set("kind", "audio", "signature", evidence.CERTAIN)
        record.set("kind", "video", "extension", evidence.LIKELY)
        self.assertEqual(record.value("kind"), "audio")

    def test_agreement_raises_confidence_without_reaching_certainty(self):
        record = evidence.Record("x")
        record.set("artist", "The Beatles", "id3", evidence.STRONG)
        before = record.confidence("artist")
        record.set("artist", "the beatles", "name:music", evidence.WEAK)
        self.assertGreater(record.confidence("artist"), before)
        self.assertLess(record.confidence("artist"), evidence.CERTAIN)
        self.assertEqual(record.conflicts, [])

    def test_absent_is_absent_rather_than_empty(self):
        record = evidence.Record("x")
        record.set("artist", "", "id3", evidence.STRONG)
        record.set("album", None, "id3", evidence.STRONG)
        self.assertFalse(record.has("artist"))
        self.assertFalse(record.has("album"))


class Extensions(unittest.TestCase):

    def test_case_and_dot_are_ignored(self):
        self.assertEqual(kinds.classify_extension(".JPG"),
                         ("image", "jpeg"))

    def test_spellings_share_a_format(self):
        for spelling in ("jpg", "jpeg", "jpe", "jfif"):
            self.assertEqual(kinds.classify_extension(spelling)[1], "jpeg")

    def test_ambiguous_extensions_refuse_to_answer(self):
        for spelling in ("ts", "key", "mod", "sub"):
            self.assertIsNone(kinds.classify_extension(spelling))
            self.assertTrue(kinds.candidates(spelling))

    def test_unknown_extension_is_none_not_a_crash(self):
        self.assertIsNone(kinds.classify_extension("qqqzzz"))
        self.assertIsNone(kinds.classify_extension(""))


class Signatures(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def sniff(self, path):
        with signatures.Peek(path) as peek:
            return signatures.sniff(peek) or signatures.sniff_text(peek)

    def test_png_is_certain(self):
        path = fixtures.png(os.path.join(self.directory, "a.png"))
        kind, fmt, _detail, confidence = self.sniff(path)
        self.assertEqual((kind, fmt), ("image", "png"))
        self.assertEqual(confidence, evidence.CERTAIN)

    def test_ooxml_is_a_document_not_an_archive(self):
        path = fixtures.docx(os.path.join(self.directory, "a.docx"))
        kind, fmt, _detail, _confidence = self.sniff(path)
        self.assertEqual((kind, fmt), ("document", "word"))

    def test_generic_text_is_weak_so_the_extension_still_wins(self):
        path = fixtures.text(os.path.join(self.directory, "a.txt"),
                             "just some words\nand some more\n")
        _kind, _fmt, _detail, confidence = self.sniff(path)
        self.assertEqual(confidence, evidence.WEAK)

    def test_a_recognised_text_shape_is_strong(self):
        path = fixtures.text(os.path.join(self.directory, "a.srt"),
                             "1\n00:00:01,000 --> 00:00:04,000\nHello\n")
        kind, fmt, _detail, confidence = self.sniff(path)
        self.assertEqual((kind, fmt), ("subtitle", "subrip"))
        self.assertEqual(confidence, evidence.STRONG)

    def test_a_python_file_is_not_markdown(self):
        # '# comment' and '# Heading' are the same bytes; sniffing Markdown
        # from content mislabelled every script on the disk.
        path = fixtures.text(os.path.join(self.directory, "a.py"),
                             "# a comment\nimport os\nprint(os.getcwd())\n")
        record = identify.identify(path)
        self.assertEqual(record.value("format"), "python")

    def test_empty_file_does_not_raise(self):
        path = fixtures.text(os.path.join(self.directory, "empty.bin"), b"")
        self.assertIsNone(self.sniff(path))


class Names(unittest.TestCase):

    def facts(self, filename, kind=None):
        return dict((name, value)
                    for name, value, _c, _s in names.read(filename,
                                                          kind).facts)

    def test_screenshot_in_several_languages(self):
        for filename in ("Screenshot 2026-09-19 at 14.03.22.png",
                         "Bildschirmfoto 2026-09-19 um 14.03.22.png",
                         "Screenshot_20260919-140322.jpg"):
            self.assertEqual(self.facts(filename, "image").get("capture"),
                             "screenshot", filename)

    def test_android_screenshot_names_its_app(self):
        found = self.facts("Screenshot_20260919-140322_com.whatsapp.jpg",
                           "image")
        self.assertEqual(found.get("from_app"), "WhatsApp")

    def test_camera_series(self):
        for filename, hint in (("IMG_4021.HEIC", "Apple or generic"),
                               ("DSCF0123.RAF", "Fujifilm"),
                               ("GX010042.MP4", "GoPro"),
                               ("MVI_8842.MOV", "Canon")):
            kind = "video" if filename.lower().endswith((".mp4", ".mov")) \
                else "image"
            self.assertEqual(self.facts(filename, kind).get("camera_hint"),
                             hint, filename)

    def test_a_camera_frame_number_is_not_a_copy_marker(self):
        self.assertNotIn("copy_marker", self.facts("IMG_4021.HEIC", "image"))
        self.assertEqual(self.facts("image (3).png", "image")
                         .get("copy_marker"), "(3)")

    def test_episode_and_film(self):
        episode = self.facts(
            "Some.Show.S03E07.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv", "video")
        self.assertEqual(episode.get("title"), "Some Show")
        self.assertEqual((episode.get("season"), episode.get("episode")),
                         (3, 7))
        film = self.facts(
            "Some.Film.Name.2019.1080p.BluRay.x264-GROUP.mkv", "video")
        self.assertEqual(film.get("title"), "Some Film Name")
        self.assertEqual(film.get("release_year"), 2019)
        self.assertEqual(film.get("release_group"), "GROUP")

    def test_anime_numbering(self):
        found = self.facts("[SubGroup] Series Title - 07 [1080p][A1B2C3D4].mkv",
                           "video")
        self.assertEqual(found.get("title"), "Series Title")
        self.assertEqual(found.get("episode"), 7)
        self.assertNotIn("season", found)     # the name never said

    def test_music_only_speaks_about_audio(self):
        self.assertNotIn("artist", self.facts("Boarding Pass - LHR.pdf",
                                              "document"))
        self.assertEqual(self.facts("Artist Name - Song Title.flac", "audio")
                         .get("artist"), "Artist Name")

    def test_music_does_not_invent_a_title(self):
        self.assertNotIn("song_title", self.facts("Untitled.png", "image"))

    def test_paperwork_keywords(self):
        for filename, label in (("bank statement march.pdf", "statement"),
                                ("Invoice 4021.pdf", "invoice"),
                                ("CV Tam 2026.docx", "cv"),
                                ("Boarding Pass - LHR.pdf", "ticket")):
            self.assertEqual(self.facts(filename, "document")
                             .get("paperwork"), label, filename)

    def test_installer_survives_underscores(self):
        # \b does not fire between 'p' and '_', so `setup_x64` matched nothing
        # until the detectors were given an underscore-flattened name.
        found = self.facts("setup_x64_v3.11.2.exe", "app")
        self.assertEqual(found.get("looks_like"), "installer")
        self.assertEqual(found.get("version"), "3.11.2")

    def test_sequence_needs_zero_padding(self):
        self.assertEqual(self.facts("page_0042.png", "image")
                         .get("sequence_number"), 42)
        self.assertNotIn("sequence_number", self.facts("CV 2026.pdf",
                                                       "document"))

    def test_subtitle_language_and_flags(self):
        found = self.facts("Movie.Name.2019.en.forced.srt", "subtitle")
        self.assertEqual(found.get("subtitle_language"), "en")
        self.assertTrue(found.get("subtitle_forced"))

    def test_litter(self):
        for filename in (".DS_Store", "Thumbs.db", "._IMG_4021.HEIC"):
            self.assertTrue(self.facts(filename).get("litter"), filename)

    def test_a_time_must_follow_its_date(self):
        found = self.facts("IMG-20260919-WA0001.jpg", "image")
        self.assertEqual(found.get("name_date"), "2026-09-19")
        self.assertNotIn("name_time", found)


class DerivedFacts(unittest.TestCase):

    def test_fur_affinity_creator_needs_matching_provenance(self):
        filename = "1497725735.multyashka-sweet_by_artist.jpg"
        record = evidence.Record(filename)
        record.set("name", filename, "path", evidence.CERTAIN)
        record.set("from_host", "d.furaffinity.net", "wherefroms",
                   evidence.STRONG)
        identify._derive(record)
        self.assertEqual(record.value("creator"), "multyashka-sweet")

    def test_epoch_filename_alone_does_not_invent_a_creator(self):
        filename = "1497725735.multyashka-sweet_by_artist.jpg"
        record = evidence.Record(filename)
        record.set("name", filename, "path", evidence.CERTAIN)
        identify._derive(record)
        self.assertFalse(record.has("creator"))

    def test_pathological_names_do_not_raise(self):
        for filename in ("", ".", "..", "a" * 300 + ".jpg", "\x00.jpg",
                         "🎧 — ‽.mp3", "....", "no-extension"):
            names.read(filename)


class Bundles(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.directory, name)

    def test_subtitles_and_artwork_travel_with_the_film(self):
        fixtures.mp4(self.path("Film.mkv"))
        fixtures.text(self.path("Film.en.srt"), "1\n")
        fixtures.text(self.path("Film.nfo"), "info")
        fixtures.png(self.path("Film-poster.jpg"))
        items = bundles.group(self.directory)
        self.assertEqual(len(items), 1)
        self.assertEqual(os.path.basename(items[0].primary), "Film.mkv")
        self.assertEqual(len(items[0].members), 4)

    def test_two_photographs_are_two_items(self):
        fixtures.jpeg(self.path("a.jpg"))
        fixtures.jpeg(self.path("b.jpg"))
        self.assertEqual(len(bundles.group(self.directory)), 2)

    def test_raw_and_jpeg_are_one_photograph(self):
        fixtures.jpeg(self.path("DSC0001.jpg"))
        fixtures.text(self.path("DSC0001.dng"), b"II*\x00")
        items = bundles.group(self.directory)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0].members), 2)

    def test_a_render_sequence_is_one_item(self):
        for frame in range(12):
            fixtures.png(self.path("render_%04d.png" % frame))
        items = bundles.group(self.directory)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].sequence, 12)

    def test_a_short_sequence_is_not_collapsed(self):
        for frame in range(3):
            fixtures.png(self.path("render_%04d.png" % frame))
        self.assertEqual(len(bundles.group(self.directory)), 3)

    def test_multipart_archive(self):
        for part in range(1, 4):
            fixtures.text(self.path("big.part%d.rar" % part), b"Rar!\x1a\x07")
        items = bundles.group(self.directory)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0].members), 3)

    def test_a_package_directory_is_one_item_and_is_not_entered(self):
        inside = os.path.join(self.directory, "Thing.app", "Contents")
        os.makedirs(inside)
        fixtures.text(os.path.join(inside, "Info.plist"), "<plist/>")
        items = list(bundles.walk(self.directory))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_dir)

    def test_depth_zero_treats_a_top_level_folder_as_one_inbox_item(self):
        folder = self.path("Old project")
        os.makedirs(folder)
        fixtures.text(os.path.join(folder, "notes.txt"), b"keep together")

        items = list(bundles.walk(self.directory, max_depth=0))

        self.assertEqual([item.primary for item in items], [folder])
        self.assertEqual(items[0].members, [folder])
        self.assertTrue(items[0].is_dir)
        self.assertEqual(items[0].reason, "top-level inbox folder")
        self.assertEqual(identify.identify(items[0]).value("kind"), "folder")


class EndToEnd(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.directory, name)

    def test_photograph(self):
        record = identify.identify(fixtures.jpeg(self.path("IMG_4021.jpg")))
        self.assertEqual(record.value("kind"), "image")
        self.assertEqual(record.value("camera"), "Canon EOS R6")
        self.assertEqual(record.value("width"), 4032)
        self.assertEqual(record.value("happened"), "2026-09-19 14:03:22")
        self.assertEqual(record.value("orientation_class"), "landscape")
        # Canon writes "Canon" in both EXIF fields, which is why this fixture
        # never showed the self-conflict that every other marque did.
        self.assertEqual([str(c) for c in record.conflicts], [])

    def test_the_camera_is_the_model_and_the_make_is_kept_beside_it(self):
        """The Make is not glued onto the front of the Model.

        It used to be, in a second `set` that could never win — both writes
        were STRONG, and `set` only replaces on strictly greater confidence —
        so every photograph carrying a camera tag ended up with a Conflict
        saying the reader disagreed with itself. `explain` shows conflicts to
        people, so a conflict on every photograph is a conflict that means
        nothing. The bare Model is also the better name: this file is a
        "NIKON D7000", not a "NIKON CORPORATION NIKON D7000".
        """
        record = identify.identify(fixtures.jpeg(
            self.path("DSC_0031.jpg"), make="NIKON CORPORATION",
            model="NIKON D7000"))
        self.assertEqual(record.value("camera"), "NIKON D7000")
        self.assertEqual(record.value("camera_make"), "NIKON CORPORATION")
        self.assertEqual([str(c) for c in record.conflicts], [])

    def test_tagged_audio(self):
        record = identify.identify(fixtures.mp3(self.path("07 Song.mp3")))
        self.assertEqual(record.value("artist"), "Test Artist")
        self.assertEqual(record.value("track"), 7)
        # The filename said 07 as well, so the tag should be corroborated.
        self.assertTrue(record.fact("track").corroborated_by)

    def test_scanned_pdf_is_recognised_by_its_producer(self):
        record = identify.identify(fixtures.pdf(self.path("Doc 1.pdf")))
        self.assertEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("pages"), 3)

    def test_a_lying_extension_is_caught_and_recorded(self):
        record = identify.identify(fixtures.png(self.path("holiday.jpg")))
        self.assertEqual(record.value("format"), "png")
        self.assertTrue(record.conflicts)

    def test_a_caller_supplied_record_is_actually_used(self):
        # Record defines __len__, so `record or Record(path)` threw the
        # caller's record away and filled a new one instead.
        path = fixtures.mp4(self.path("thing.mp4"))
        record = evidence.Record(path)
        returned = identify.identify(path, record=record)
        self.assertIs(returned, record)
        self.assertEqual(record.value("kind"), "video")

    def test_track_counts_are_read_from_the_container(self):
        record = identify.identify(fixtures.mp4(self.path("thing.mp4"),
                                                audio_tracks=2))
        self.assertEqual(record.value("video_tracks"), 1)
        self.assertEqual(record.value("audio_tracks"), 2)

    def test_tier_stat_reads_no_bytes(self):
        path = fixtures.png(self.path("thing.jpg"))
        record = identify.identify(path, tier=identify.TIER_STAT)
        self.assertEqual(record.value("format"), "jpeg")   # extension only
        self.assertFalse(record.has("width"))

    def test_a_directory_of_mixed_rubbish_does_not_raise(self):
        fixtures.png(self.path("a.png"))
        fixtures.text(self.path("b.bin"), os.urandom(4096))
        fixtures.text(self.path(".DS_Store"), b"\x00\x00\x00\x01Bud1")
        fixtures.text(self.path("c"), "no extension at all")
        fixtures.text(self.path("d.unknownext"), b"\xff\xfe\x00\x01")
        os.symlink("/nowhere/at/all", self.path("broken.lnk"))
        for item in bundles.walk(self.directory):
            record = identify.identify(item)
            self.assertTrue(record.has("name"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
