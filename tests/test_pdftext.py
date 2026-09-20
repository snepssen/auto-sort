"""Reading what a PDF says, and knowing when it says nothing.

The negative cases matter more than the positive ones here. A keyword search
over a long document will eventually find a keyword, and a sorter that files
a CV as a certificate because the word appears in its skills list has made a
confident mistake, which is worse than no answer at all. So the classifier
reads the letterhead and stops, and several tests below exist to hold it
there.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures                                          # noqa: E402
import identify                                          # noqa: E402
import signatures                                        # noqa: E402
from readers import pdftext                              # noqa: E402

INVOICE = ("RECHNUNG Nr 4471\n"
           "Stadtwerke Muenchen GmbH\n"
           "Betrag: 84,20 EUR\n"
           "Zahlbar bis 14 Tage nach Erhalt der Rechnung")


class Extraction(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-pdf-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def build(self, name="doc.pdf", **kwargs):
        path = os.path.join(self.dir, name)
        fixtures.pdf(path, **kwargs)
        return path

    def extract(self, name="doc.pdf", **kwargs):
        peek = signatures.Peek(self.build(name, **kwargs))
        try:
            return pdftext.extract(peek)
        finally:
            peek.close()

    def test_a_plain_font_is_read_directly(self):
        """Old files, written before subsetting: the bytes are the letters."""
        text, image_only = self.extract(text=INVOICE)
        self.assertIn("RECHNUNG", text)
        self.assertIn("Stadtwerke", text)
        self.assertFalse(image_only)

    def test_a_subset_font_is_read_through_its_own_table(self):
        """Modern files: the bytes are glyph numbers and mean nothing alone.

        Every current writer subsets its fonts, so without following the
        ToUnicode table this reads as mojibake and the feature is useless on
        anything made this century.
        """
        text, _ = self.extract(text=INVOICE, subset_font=True)
        self.assertIn("RECHNUNG", text)
        self.assertIn("Muenchen", text)

    def test_glyph_numbers_without_a_table_are_not_offered_as_text(self):
        """Refusing to answer beats answering with noise."""
        path = self.build(text=INVOICE, subset_font=True)
        with open(path, "rb") as handle:
            raw = handle.read()
        with open(path, "wb") as handle:
            handle.write(raw.replace(b"/ToUnicode 6 0 R", b"                "))
        peek = signatures.Peek(path)
        try:
            text, _image_only = pdftext.extract(peek)
        finally:
            peek.close()
        self.assertEqual(text, "")

    def test_words_are_separated(self):
        """PDF moves the pen between words rather than drawing a space.

        Without reading those moves the page comes back as one long run of
        letters, and no keyword with a word boundary in it can ever match.
        """
        text, _ = self.extract(text=INVOICE)
        self.assertIn("Stadtwerke Muenchen", text)

    def test_an_encrypted_file_is_declined(self):
        path = self.build(text=INVOICE)
        with open(path, "rb") as handle:
            raw = handle.read()
        with open(path, "wb") as handle:
            handle.write(raw.replace(b"trailer<<", b"trailer<</Encrypt 9 0 R"))
        peek = signatures.Peek(path)
        try:
            text, _ = pdftext.extract(peek)
        finally:
            peek.close()
        self.assertEqual(text, "")

    def test_a_page_that_is_a_picture_reports_itself_as_one(self):
        text, image_only = self.extract(text=None, image_only=True)
        self.assertEqual(text, "")
        self.assertTrue(image_only)


# Enough neutral prose to push what follows past the letterhead window.
FILLER = "Seite eins von zwei. " * 30


class Classification(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-pdfclass-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def identify(self, name, **kwargs):
        path = os.path.join(self.dir, name)
        fixtures.pdf(path, **kwargs)
        return identify.identify(path)

    def test_a_file_whose_name_says_nothing_is_read(self):
        """The whole point: `scan0001.pdf` is not a dead end any more."""
        record = self.identify("scan0001.pdf", producer="Acrobat",
                               text=INVOICE, subset_font=True)
        self.assertEqual(record.value("paperwork"), "invoice")
        self.assertTrue(record.value("text_layer"))

    def test_the_body_of_a_document_does_not_classify_it(self):
        """A CV listing certifications is a CV, not a certificate.

        Observed on real files: read whole, every CV in a folder came back
        as `certificate` and a covering letter as `ticket`.
        """
        record = self.identify(
            "Document1.pdf", producer="Acrobat",
            text=("Tamas Torok\nIT Support Technician\n" + FILLER
                  + "Holds a current certificate and a diploma. " * 12))
        self.assertIsNone(record.value("paperwork"))

    def test_the_letterhead_outranks_the_body(self):
        """Position beats frequency, which is the whole design.

        The word at the top appears once; the wrong word below it appears
        twelve times and must still lose.
        """
        record = self.identify(
            "Document2.pdf", producer="Acrobat",
            text=("RECHNUNG Nr 4471\nStadtwerke Muenchen GmbH\n" + FILLER
                  + "Holds a current certificate and a diploma. " * 12))
        self.assertEqual(record.value("paperwork"), "invoice")

    def test_a_scanned_page_is_held_rather_than_guessed_at(self):
        record = self.identify("scan0002.pdf",
                               producer="HP ScanJet Pro firmware",
                               text=None, image_only=True)
        self.assertIs(record.value("text_layer"), False)
        self.assertTrue(record.value("needs_ocr"))
        self.assertIsNone(record.value("paperwork"))
        self.assertEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("scan_of"), "page")


if __name__ == "__main__":
    unittest.main()
