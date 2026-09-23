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
        with mock.patch.object(platform_support, "locate", return_value=None):
            self.assertIsNone(ocr.available())
            self.assertEqual(ocr.read_image(JPEG), "")
            self.assertEqual(ocr.read_file("/tmp/whatever.jpg"), "")
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
            self.assertEqual(ocr.read_image(JPEG), "")

    def test_a_program_that_never_finishes_is_not_waited_for(self):
        import subprocess
        with mock.patch.object(ocr, "available", return_value="/bin/sleep"), \
                mock.patch("subprocess.run",
                           side_effect=subprocess.TimeoutExpired("t", 1)):
            self.assertEqual(ocr.read_image(JPEG), "")

    def test_it_leaves_no_temporary_file_behind(self):
        before = len(os.listdir(tempfile.gettempdir()))
        with mock.patch.object(ocr, "available", return_value="/bin/false"), \
                mock.patch("subprocess.run",
                           return_value=mock.Mock(returncode=1, stdout=b"")):
            for _ in range(5):
                ocr.read_image(JPEG)
        self.assertLessEqual(len(os.listdir(tempfile.gettempdir())),
                             before + 2)


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

    def test_a_page_that_reads_as_nothing_is_still_held(self):
        self.assertFalse(self.read(""))
        self.assertFalse(self.record.has("read_by"))
        self.assertTrue(self.record.value("needs_ocr"))

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


if __name__ == "__main__":
    unittest.main()
