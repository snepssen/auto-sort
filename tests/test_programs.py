"""Tier 2: the optional programs, and the work they were supposed to do.

`ffprobe` and `exiftool` were offered by the installer and named in the
documentation for months without a single line of code calling either of
them. This is that code, and most of these tests are about the two ways it
is allowed to do nothing: the program is not installed, or it is installed
and the file did not need it.

Nothing here requires either program to be present. The output is stubbed,
because a test suite that passes on the machine with the tools and fails on
the machine without them is testing the machine.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence                                          # noqa: E402
import identify                                          # noqa: E402
import platform_support                                  # noqa: E402
import readers                                           # noqa: E402
from readers import exiftool, probe                      # noqa: E402


def record_for(kind, **facts):
    record = evidence.Record("/tmp/thing")
    record.set("kind", kind, "test", evidence.CERTAIN)
    for name, value in facts.items():
        record.set(name, value, "header", evidence.CERTAIN)
    return record


FFPROBE = json.dumps({
    "format": {"duration": "128.4", "bit_rate": "4219289",
               "tags": {"title": "A Film", "creation_time": "2019-05-03"}},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920,
         "height": 1080, "avg_frame_rate": "30000/1001"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2,
         "sample_rate": "48000"},
        {"codec_type": "subtitle", "codec_name": "subrip"},
        {"codec_type": "subtitle", "codec_name": "subrip"},
    ],
    "chapters": [{"id": 0}, {"id": 1}, {"id": 2}],
})

EXIFTOOL = json.dumps([{
    "Make": "NIKON CORPORATION", "Model": "NIKON D7000",
    "LensModel": "35mm f/1.8", "ImageWidth": 4928, "ImageHeight": 3264,
    "DateTimeOriginal": "2011:07:04 18:22:01", "ISO": 400,
    "ExposureTime": 0.004, "FNumber": 1.8,
    "GPSLatitude": 51.5074, "GPSLongitude": -0.1278,
    "GPSLatitudeRef": "N", "GPSLongitudeRef": "W",
}])

SCANNER = json.dumps([{
    "Make": "EPSON", "Model": "Perfection V600",
    "ImageWidth": 2480, "ImageHeight": 3508,
    "XResolution": 300, "YResolution": 300, "ResolutionUnit": 2,
}])


class AskingOnlyWhenItWouldHelp(unittest.TestCase):

    def test_a_video_with_no_duration_is_worth_asking_about(self):
        self.assertTrue(probe.wanted(record_for("video")))

    def test_a_video_that_parsed_cleanly_is_not(self):
        self.assertFalse(probe.wanted(
            record_for("video", duration=90.0, width=1920)))

    def test_a_video_with_a_duration_but_no_size_still_is(self):
        self.assertTrue(probe.wanted(record_for("video", duration=90.0)))

    def test_a_document_is_never_worth_asking_ffprobe_about(self):
        self.assertFalse(probe.wanted(record_for("document")))

    def test_a_picture_nothing_could_read_is_worth_asking_about(self):
        self.assertTrue(exiftool.wanted(record_for("image")))

    def test_a_picture_whose_header_was_read_is_not(self):
        """Measured: the looser gate cost twenty seconds and found nothing."""
        self.assertFalse(exiftool.wanted(record_for("image", width=800)))

    def test_a_cloud_placeholder_is_not_fetched_to_look_at_it(self):
        record = record_for("image")
        record.set("dataless", True, "stat", evidence.CERTAIN)
        self.assertFalse(exiftool.wanted(record))


class WhatFfprobeAdds(unittest.TestCase):

    def read(self, output=FFPROBE, record=None):
        record = record or record_for("video")
        with mock.patch.object(platform_support, "output",
                               return_value=output):
            probe.read("/tmp/film.mkv", record)
        return record

    def test_the_facts_a_header_could_not_give(self):
        record = self.read()
        self.assertEqual(record.value("duration"), 128.4)
        self.assertEqual(record.value("width"), 1920)
        self.assertEqual(record.value("height"), 1080)
        self.assertEqual(record.value("encoder"), "h264")

    def test_a_frame_rate_written_as_a_fraction(self):
        self.assertEqual(self.read().value("fps"), 29.97)

    def test_tracks_are_counted(self):
        record = self.read()
        self.assertEqual(record.value("audio_tracks"), 1)
        self.assertEqual(record.value("subtitle_tracks"), 2)
        self.assertEqual(record.value("chapters"), 3)

    def test_it_fills_gaps_and_never_argues(self):
        """The file's own header is the better authority about itself."""
        record = self.read(record=record_for("video", duration=999.0))
        self.assertEqual(record.value("duration"), 999.0)
        self.assertEqual(record.conflicts, [])

    def test_a_length_of_zero_is_not_a_length(self):
        output = json.dumps({"format": {"duration": "0"}, "streams": []})
        self.assertFalse(self.read(output).has("duration"))

    def test_cover_art_is_not_the_picture(self):
        """An album's artwork is a video stream as far as a container
        knows."""
        output = json.dumps({"format": {}, "streams": [
            {"codec_type": "video", "width": 600, "height": 600,
             "disposition": {"attached_pic": 1}},
            {"codec_type": "audio", "channels": 2}]})
        record = self.read(output, record_for("audio"))
        self.assertFalse(record.has("width"))

    def test_nonsense_output_is_not_a_crash(self):
        for output in ("", "not json at all", "{}", "[]", None):
            self.assertIsNotNone(self.read(output))

    def test_no_program_means_no_facts(self):
        record = record_for("video")
        with mock.patch.object(platform_support, "locate", return_value=None):
            platform_support.forget()
            probe.read("/tmp/film.mkv", record)
        platform_support.forget()
        self.assertFalse(record.has("duration"))


class WhatExiftoolAdds(unittest.TestCase):

    def read(self, output=EXIFTOOL, record=None):
        record = record or record_for("image")
        with mock.patch.object(platform_support, "output",
                               return_value=output):
            exiftool.read("/tmp/photo.psd", record)
        return record

    def test_the_camera_and_when(self):
        record = self.read()
        self.assertEqual(record.value("camera"), "NIKON D7000")
        self.assertEqual(record.value("camera_make"), "NIKON CORPORATION")
        self.assertEqual(record.value("taken"), "2011-07-04 18:22:01")
        self.assertEqual(record.value("width"), 4928)

    def test_coordinates_come_back_in_the_usual_shape(self):
        self.assertEqual(self.read().value("gps"), "51.5074,-0.1278")

    def test_a_southern_westerly_position_keeps_its_signs(self):
        south = json.loads(EXIFTOOL)
        south[0].update({"GPSLatitude": 33.9, "GPSLongitude": 18.4,
                         "GPSLatitudeRef": "S", "GPSLongitudeRef": "E"})
        record = self.read(json.dumps(south))
        self.assertEqual(record.value("gps"), "-33.9,18.4")

    def test_a_scanner_is_not_a_camera(self):
        """The Epson bug, through the second door.

        A flatbed writes into the same tags a camera does, and a rule that
        gathers photographs by the body that took them would otherwise
        gather twenty years of paperwork into a folder named after one.
        """
        record = self.read(SCANNER)
        self.assertEqual(record.value("scanner"), "Perfection V600")
        self.assertIsNone(record.value("camera"))
        self.assertEqual(record.value("capture"), "scan")

    def test_it_fills_gaps_and_never_argues(self):
        record = self.read(record=record_for("image", camera="Canon EOS 5D"))
        self.assertEqual(record.value("camera"), "Canon EOS 5D")

    def test_nonsense_output_is_not_a_crash(self):
        for output in ("", "{}", "[]", "[1,2,3]", "not json", None):
            self.assertIsNotNone(self.read(output))


class TheTierItself(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-tier2-")
        self.path = os.path.join(self.dir, "thing.mkv")
        with open(self.path, "wb") as handle:
            handle.write(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_an_enricher_that_throws_loses_its_facts_and_nothing_else(self):
        record = record_for("video")
        with mock.patch.object(probe, "read",
                               side_effect=RuntimeError("exploded")):
            readers.enrich(self.path, record)
        self.assertTrue(any("probe declined" in note
                            for note in record.notes), record.notes)

    def test_header_tier_starts_no_processes(self):
        """`--tier header` is also how somebody says "start nothing"."""
        with mock.patch.object(platform_support, "output") as ran:
            identify.identify(self.path, tier=identify.TIER_HEADER)
        ran.assert_not_called()

    def test_the_top_tier_asks(self):
        with mock.patch.object(platform_support, "output",
                               return_value=FFPROBE) as ran:
            record = identify.identify(self.path)
        self.assertTrue(ran.called)
        self.assertEqual(record.value("duration"), 128.4)

    def test_everything_still_works_with_nothing_installed(self):
        """The claim the whole project rests on."""
        with mock.patch.object(platform_support, "locate", return_value=None):
            platform_support.forget()
            record = identify.identify(self.path)
        platform_support.forget()
        self.assertEqual(record.value("kind"), "video")


class TheSharedRunner(unittest.TestCase):

    def tearDown(self):
        platform_support.forget()

    def test_an_absent_program_answers_nothing(self):
        with mock.patch.object(platform_support, "locate", return_value=None):
            platform_support.forget()
            self.assertIsNone(platform_support.output("ffprobe", ["-x"]))

    def test_a_program_that_fails_answers_nothing(self):
        with mock.patch.object(platform_support, "locate",
                               return_value="/bin/false"), \
                mock.patch("subprocess.run",
                           return_value=mock.Mock(returncode=1, stdout=b"")):
            platform_support.forget()
            self.assertIsNone(platform_support.output("ffprobe", ["-x"]))

    def test_a_program_that_never_finishes_is_not_waited_for(self):
        import subprocess
        with mock.patch.object(platform_support, "locate",
                               return_value="/bin/sleep"), \
                mock.patch("subprocess.run",
                           side_effect=subprocess.TimeoutExpired("s", 1)):
            platform_support.forget()
            self.assertIsNone(platform_support.output("ffprobe", ["-x"]))


class BuiltIntoAMac(unittest.TestCase):
    """Reading scanned pages needs nothing installed on a Mac, and the list
    of programs must not say otherwise."""

    def test_the_ocr_row_says_it_is_built_in(self):
        from unittest import mock
        import platform_support
        from readers import ocr
        with mock.patch.object(ocr, "_vision_here", return_value=True), \
                mock.patch.object(platform_support, "locate",
                                  return_value=None):
            platform_support.forget()
            row = [entry for entry in platform_support.inventory()
                   if entry["key"] == "tesseract"][0]
        platform_support.forget()
        self.assertTrue(row["installed"])
        self.assertEqual(row["built_in"], "macOS text recognition")
        self.assertEqual(row["install"], "")


class OnALockedRoot(unittest.TestCase):
    """`status` and the log page told a Steam Deck to run `sudo pacman -S`,
    which a root filesystem SteamOS keeps read-only refuses. Only the
    launcher said so; the two places people look afterwards did not."""

    def rows(self, locked):
        with mock.patch.object(platform_support, "current_manager",
                               return_value="pacman"), \
                mock.patch.object(platform_support, "locate",
                                  return_value=None), \
                mock.patch.object(platform_support, "immutable_root",
                                  return_value=locked):
            platform_support.forget()
            listing = platform_support.inventory()
        platform_support.forget()
        return [row for row in listing if row["install"]]

    def test_every_install_line_says_it_will_fail_until_unlocked(self):
        rows = self.rows(locked=True)
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("sudo pacman", row["install"])
            self.assertIn("unlocked", row["install_note"])

    def test_an_ordinary_root_gets_no_note(self):
        for row in self.rows(locked=False):
            self.assertEqual(row["install_note"], "")


if __name__ == "__main__":
    unittest.main()
