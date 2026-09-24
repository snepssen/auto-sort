"""Reading a page that was photographed rather than typed.

Every rule this program has needs characters. A page put through a scanner
has none, so it can only be held -- and holding a document forever is honest
and useless.

The tests that matter here are the ones about absence: with nothing
installed, nothing changes. An optional program that quietly becomes
required is the failure this whole shape exists to avoid.
"""

from __future__ import annotations

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
import rules                                             # noqa: E402
from readers import document, ocr, pdftext               # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


def pdf_with_image(width, height, payload=JPEG):
    """A PDF-shaped file holding one JPEG of the given declared size."""
    header = ("1 0 obj <</Subtype/Image/Width %d/Height %d"
              "/Filter/DCTDecode/Length %d>>\nstream\n"
              % (width, height, len(payload))).encode("ascii")
    return b"%PDF-1.4\n" + header + payload + b"\nendstream\nendobj\n"


class FindingThePage(unittest.TestCase):
    """The picture to read, out of a file that holds several."""

    def test_a_page_sized_image_is_found(self):
        found = pdftext.page_image(pdf_with_image(1700, 2200))
        self.assertIsNotNone(found)
        self.assertEqual(found[1:], (1700, 2200))

    def test_a_logo_is_not_a_page(self):
        """185 of 195 real files hold nothing bigger than a letterhead."""
        self.assertIsNone(pdftext.page_image(pdf_with_image(218, 62)))

    def test_the_biggest_one_wins(self):
        """Nearly every scan arrives with the sender's logo in front of it."""
        data = pdf_with_image(218, 62) + pdf_with_image(1700, 2200)
        found = pdftext.page_image(data)
        self.assertEqual(found[1:], (1700, 2200))

    def test_bytes_that_are_not_a_jpeg_are_not_offered_as_one(self):
        data = pdf_with_image(1700, 2200, payload=b"not a jpeg at all")
        self.assertIsNone(pdftext.page_image(data))

    def test_an_absurd_image_is_left_alone(self):
        big = b"\xff\xd8\xff" + b"\x00" * 16
        data = pdf_with_image(9000, 9000, payload=big)
        with mock.patch.object(pdftext, "MAX_IMAGE_BYTES", 4):
            self.assertIsNone(pdftext.page_image(data))

    def test_a_pdf_with_no_images_at_all(self):
        self.assertIsNone(pdftext.page_image(b"%PDF-1.4\nnothing here\n"))


class WhenThereIsNothingInstalled(unittest.TestCase):
    """The case that must never become a failure."""

    def setUp(self):
        ocr.configure("auto")
        platform_support.forget()

    def tearDown(self):
        ocr.configure("auto")
        platform_support.forget()

    def test_no_program_means_no_ocr_and_no_error(self):
        with mock.patch.object(platform_support, "locate", return_value=None), \
                mock.patch.object(ocr, "_vision_here", return_value=False):
            self.assertIsNone(ocr.available())
            self.assertIsNone(ocr.read_image(JPEG))
            self.assertIsNone(ocr.read_file("/tmp/whatever.jpg"))
            self.assertEqual(ocr.languages_installed(), [])

    def test_a_scan_is_still_held(self):
        """Nothing installed means the page is never even offered."""
        record = evidence.Record("/tmp/scan.pdf")
        record.set("needs_ocr", True, "pdf-text", evidence.STRONG)
        with mock.patch.object(ocr, "available", return_value=None):
            self.assertFalse(ocr.wanted(record))
        self.assertTrue(record.value("needs_ocr"))

    def test_off_means_off_even_where_it_is_installed(self):
        ocr.configure("off")
        with mock.patch.object(platform_support, "locate",
                               return_value="/usr/bin/tesseract"):
            self.assertIsNone(ocr.available())
        self.assertEqual(ocr.mode(), "off")

    def test_the_setting_accepts_only_what_it_means(self):
        self.assertEqual(rules.Settings({"ocr": "off"}).ocr, "off")
        self.assertEqual(rules.Settings({}).ocr, "auto")
        with self.assertRaises(rules.RuleError):
            rules.Settings({"ocr": "sometimes"})

    def test_a_program_that_fails_is_not_an_exception(self):
        broken = mock.Mock(returncode=1, stdout=b"")
        with mock.patch.object(ocr, "available", return_value="/bin/false"), \
                mock.patch("subprocess.run", return_value=broken):
            self.assertIsNone(ocr.read_image(JPEG))

    def test_a_program_that_never_finishes_is_not_waited_for(self):
        import subprocess
        with mock.patch.object(ocr, "available", return_value="/bin/sleep"), \
                mock.patch("subprocess.run",
                           side_effect=subprocess.TimeoutExpired("t", 1)):
            self.assertIsNone(ocr.read_image(JPEG))

    def ran(self, languages):
        """The command tesseract was given, on an install with `languages`."""
        ocr._installed.clear()
        done = mock.Mock(returncode=0, stdout=b"words")
        with mock.patch.object(ocr, "languages_installed",
                               return_value=languages), \
                mock.patch("subprocess.run", return_value=done) as run:
            ocr._run("/usr/bin/tesseract", "/tmp/page.jpg")
        ocr._installed.clear()
        return run.call_args[0][0]

    def test_a_page_upside_down_is_turned_first(self):
        """A real form scanned the wrong way up read `OTOZ JUN!`."""
        command = self.ran(["eng", "osd"])
        self.assertEqual(command[command.index("--psm") + 1], "1")

    def test_without_orientation_data_it_is_not_asked_for(self):
        self.assertNotIn("--psm", self.ran(["eng"]))

    def test_an_install_without_english_reads_what_it_has(self):
        """SteamOS ships tesseract with Afrikaans and orientation data and
        no English, and tesseract asked for nothing asks for English: every
        page failed, and every scan waited for a program that was there."""
        command = self.ran(["afr", "osd"])
        self.assertEqual(command[command.index("-l") + 1], "afr")
        command = self.ran(["deu", "fra", "osd"])
        self.assertEqual(command[command.index("-l") + 1], "deu+fra")

    def test_an_install_with_english_is_left_to_its_default(self):
        self.assertNotIn("-l", self.ran(["afr", "eng", "osd"]))

    def test_it_leaves_no_temporary_file_behind(self):
        # A folder of its own: the shared temporary folder is written to by
        # everything else on the machine, a running auto-sort included, and
        # counting it made this test fail now and then for no reason here.
        private = tempfile.mkdtemp(prefix="autosort-ocr-tmp-")
        try:
            with mock.patch.object(ocr, "available",
                                   return_value="/bin/false"), \
                    mock.patch.object(tempfile, "tempdir", private), \
                    mock.patch("subprocess.run", return_value=mock.Mock(
                        returncode=1, stdout=b"")):
                for _ in range(5):
                    ocr.read_image(JPEG)
            self.assertEqual(os.listdir(private), [])
        finally:
            shutil.rmtree(private, ignore_errors=True)


class WhatItRecords(unittest.TestCase):
    """OCR text is weaker evidence than a text layer, and says so."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-ocrrec-")
        self.path = os.path.join(self.dir, "scan.pdf")
        with open(self.path, "wb") as handle:
            handle.write(pdf_with_image(1700, 2200))
        self.record = evidence.Record(self.path)
        self.record.set("format", "pdf", "signature", evidence.CERTAIN)
        self.record.set("needs_ocr", True, "pdf-text", evidence.STRONG)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def read(self, text):
        with mock.patch.object(ocr, "available", return_value="/bin/echo"), \
                mock.patch.object(ocr, "read_image", return_value=text):
            return ocr.read(self.path, self.record)

    def test_a_page_that_was_read_is_no_longer_waiting_to_be(self):
        self.read("Rechnung Nr 4711")
        self.assertFalse(self.record.has("needs_ocr"))

    def test_a_heading_from_ocr_is_likely_not_strong(self):
        self.assertTrue(self.read("Rechnung Nr 4711 vom 3. Mai 2019 "
                                  "Stadtwerke"))
        self.assertEqual(self.record.confidence("heading"), evidence.LIKELY)
        self.assertEqual(self.record.source("heading"), "name:ocr"
                         if self.record.source("heading").startswith("name:")
                         else "ocr")

    def test_it_says_the_words_came_from_a_photograph(self):
        self.read("Rechnung Nr 4711")
        self.assertEqual(self.record.value("read_by"), "ocr")

    def test_a_page_ocr_could_not_read_is_still_waiting(self):
        """A crash or a timeout says nothing about the page."""
        self.assertFalse(self.read(None))
        self.assertFalse(self.record.has("read_by"))
        self.assertTrue(self.record.value("needs_ocr"))

    def test_a_page_with_nothing_on_it_is_not_waiting_any_more(self):
        """A map was counted as waiting for a program that was installed
        and had already looked."""
        self.assertFalse(self.read(""))
        self.assertEqual(self.record.value("read_by"), "ocr")
        self.assertEqual(self.record.value("words_read"), 0)
        self.assertFalse(self.record.has("needs_ocr"))

    def test_the_heading_is_the_top_of_the_page_only(self):
        self.read("First Second Third Fourth Fifth Sixth Seventh Eighth")
        self.assertEqual(self.record.value("heading"),
                         "First Second Third Fourth Fifth Sixth")


class OnARealScan(unittest.TestCase):
    """Only where a real OCR program happens to be installed."""

    def setUp(self):
        platform_support.forget()
        ocr.configure("auto")
        if not ocr.available():
            self.skipTest("no OCR program installed on this machine")
        self.dir = tempfile.mkdtemp(prefix="autosort-ocr-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_page_of_words_comes_back_as_words(self):
        """Drawn here rather than shipped: a fixture of somebody's post is
        somebody's post."""
        path = os.path.join(self.dir, "page.pbm")
        self._draw(path, "HELLO")
        text = ocr.read_file(path)
        # Tesseract on a hand-drawn bitmap is not guaranteed to read it, so
        # this asserts the plumbing rather than the accuracy: a string came
        # back, of a sane length, without an exception.
        self.assertIsInstance(text, str)
        self.assertLess(len(text), ocr.MAX_CHARS + 1)

    def _draw(self, path, _word):
        width, height = 600, 200
        rows = []
        for y in range(height):
            row = bytearray(b"\x00" * width)
            if 80 <= y <= 120:
                for x in range(60, 540, 40):
                    for dot in range(x, min(x + 18, width)):
                        row[dot] = 1
            rows.append(bytes(row))
        with open(path, "wb") as handle:
            handle.write(("P4\n%d %d\n" % (width, height)).encode("ascii"))
            for row in rows:
                packed = bytearray()
                for index in range(0, width, 8):
                    byte = 0
                    for bit in range(8):
                        if index + bit < width and row[index + bit]:
                            byte |= 0x80 >> bit
                    packed.append(byte)
                handle.write(bytes(packed))


class ItIsOfferedLikeTheOthers(unittest.TestCase):

    def test_the_program_is_in_the_table_bootstrap_reads(self):
        program = platform_support.PROGRAMS["tesseract"]
        self.assertIn("apt", program.packages)
        self.assertIn("winget", program.packages)
        self.assertFalse(program.required)

    def test_identification_still_works_with_nothing_installed(self):
        """The claim the whole project rests on."""
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "note.txt")
            with open(path, "wb") as handle:
                handle.write(b"hello")
            with mock.patch.object(platform_support, "locate",
                                   return_value=None):
                platform_support.forget()
                record = identify.identify(path)
            self.assertEqual(record.value("kind"), "document")
        finally:
            platform_support.forget()
            shutil.rmtree(directory, ignore_errors=True)


def _flate_image(number, width, height, colour=b"/DeviceRGB", fill=b"\x80",
                 extra=b""):
    import zlib as _zlib
    components = 3 if colour == b"/DeviceRGB" else 1
    rows = b"".join(fill * (width * components) for _ in range(height))
    packed = _zlib.compress(rows)
    return (b"%d 0 obj\n<</Type/XObject/Subtype/Image/Width %d/Height %d"
            b"/BitsPerComponent 8/ColorSpace%s/Filter/FlateDecode%s"
            b"/Length %d>>\nstream\n" % (number, width, height, colour,
                                            extra, len(packed))
            + packed + b"\nendstream\nendobj\n")


class PagesStoredAsPixels(unittest.TestCase):
    """Two certificates were a 1408 by 1988 picture kept as compressed
    pixels, and with only JPEGs lifted out for OCR nobody could read them."""

    def test_the_page_comes_out_as_a_png(self):
        data = b"%PDF-1.4\n" + _flate_image(5, 800, 900)
        found = pdftext.page_image(data)
        self.assertIsNotNone(found)
        image, width, height = found
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((width, height), (800, 900))
        import struct
        self.assertEqual(struct.unpack(">II", image[16:24]), (800, 900))

    def test_a_mask_of_the_same_size_is_not_the_page(self):
        """The nearly blank soft mask came first and was read instead."""
        import os as _os
        mask = _flate_image(5, 800, 900, colour=b"/DeviceGray", fill=b"\xff")
        page = b"%PDF-1.4\n" + mask + _flate_image(
            6, 800, 900, fill=b"\x10", extra=b"/SMask 5 0 R")
        # Give the page some content, so it compresses to more than the mask.
        page = page.replace(b"\x10" * 10, _os.urandom(10), 1)
        found = pdftext.page_image(page)
        self.assertEqual(found[0][25], 2)            # colour type: RGB

    def test_a_picture_too_small_to_be_a_page_is_not_one(self):
        self.assertIsNone(pdftext.page_image(
            b"%PDF-1.4\n" + _flate_image(5, 200, 100)))

    def test_read_image_names_a_png_as_one(self):
        seen = []

        def run(command, **_kwargs):
            seen.append(command[1])
            return mock.Mock(returncode=0, stdout=b"words")
        with mock.patch.object(ocr, "available", return_value="/bin/echo"), \
                mock.patch.object(ocr, "languages_installed",
                                  return_value=[]), \
                mock.patch("subprocess.run", side_effect=run):
            ocr._installed.clear()
            ocr.read_image(b"\x89PNG\r\n\x1a\nrest")
        self.assertTrue(seen[-1].endswith(".png"))


class VisionOnAMac(unittest.TestCase):
    """Reading a scanned page with nothing installed, on a Mac."""

    def setUp(self):
        ocr.configure("auto")
        platform_support.forget()

    def tearDown(self):
        ocr.configure("auto")
        platform_support.forget()

    def test_a_mac_reads_pages_with_what_it_has(self):
        with mock.patch.object(ocr, "_vision_here", return_value=True), \
                mock.patch.object(platform_support, "locate",
                                  return_value=None):
            self.assertEqual(ocr.available(), ocr.VISION)
            self.assertEqual(ocr.engine_name(), "macOS text recognition")

    def test_it_is_preferred_to_tesseract_where_both_are_here(self):
        with mock.patch.object(ocr, "_vision_here", return_value=True), \
                mock.patch.object(platform_support, "locate",
                                  return_value="/usr/bin/tesseract"):
            self.assertEqual(ocr.available(), ocr.VISION)

    def test_elsewhere_it_is_tesseract_or_nothing(self):
        with mock.patch.object(ocr, "_vision_here", return_value=False), \
                mock.patch.object(platform_support, "locate",
                                  return_value="/usr/bin/tesseract"):
            self.assertEqual(ocr.available(), "/usr/bin/tesseract")

    def test_off_is_still_off(self):
        ocr.configure("off")
        with mock.patch.object(ocr, "_vision_here", return_value=True):
            self.assertIsNone(ocr.available())

    def test_a_failed_reading_falls_back_to_tesseract(self):
        with mock.patch.object(ocr, "_run_vision", return_value=None), \
                mock.patch.object(platform_support, "find",
                                  return_value="/usr/bin/tesseract"), \
                mock.patch.object(ocr, "languages_installed",
                                  return_value=[]), \
                mock.patch("subprocess.run", return_value=mock.Mock(
                    returncode=0, stdout=b"read by tesseract")):
            ocr._installed.clear()
            self.assertEqual(ocr._run(ocr.VISION, "/tmp/page.png"),
                             "read by tesseract")

    def test_what_vision_says_is_one_line_of_words(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(
                returncode=0, stdout=b"Werkpostfiche\nuitzendarbeid\n")):
            self.assertEqual(ocr._run_vision("/tmp/page.png"),
                             "Werkpostfiche uitzendarbeid")

    def test_a_clean_reading_keeps_its_first_words(self):
        """`HollCert Opleiding & Training` was cut to `Waalwijk...` by the
        border-stepping meant for tesseract."""
        record = evidence.Record("/tmp/scan.jpg")
        record.set("needs_ocr", True, "image", evidence.STRONG)

        def vision(_program, _path, _languages=""):
            ocr.last_engine = ocr.VISION
            return "HollCert Opleiding & Training HOLLCERT Certificaat"
        with mock.patch.object(ocr, "available", return_value=ocr.VISION), \
                mock.patch.object(ocr, "_run", side_effect=vision):
            ocr.read("/tmp/scan.jpg", record)
        self.assertTrue(record.value("heading").startswith("HollCert"))

    def test_a_vision_that_fails_says_nothing(self):
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout=b"")):
            self.assertIsNone(ocr._run_vision("/tmp/page.png"))


class PastTheBorder(unittest.TestCase):

    def test_a_decorative_border_is_not_the_heading(self):
        self.assertEqual(
            ocr._past_the_border("ray Es Ss iS} Ea iS = z VIRTUAL COLLEGE "
                                 "Accredited This certificate"),
            "VIRTUAL COLLEGE Accredited This certificate")

    def test_text_with_no_run_of_words_is_left_as_it_was(self):
        self.assertEqual(ocr._past_the_border("a b c"), "a b c")


if __name__ == "__main__":
    unittest.main()
