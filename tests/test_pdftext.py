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
import shapes                                            # noqa: E402
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
        self.assertTrue(record.value("text_layer"))
        self.assertIn("RECHNUNG", record.value("heading"))

    def test_the_reader_does_not_decide_what_the_document_is(self):
        """No classification happens here, in any language.

        The heading is reported and nothing else. What a word means is
        settled by counting how many documents share it, which is a question
        about a folder rather than about a file.
        """
        record = self.identify("scan0003.pdf", producer="Acrobat",
                               text=INVOICE)
        self.assertIsNone(record.value("paperwork"))

    def test_the_heading_comes_from_the_top_of_the_page(self):
        """Position is the whole reason this is worth reading at all.

        A document announces what it is at the top and mentions everything
        else below. Read whole, a CV listing certifications looks like a
        certificate and a covering letter that mentions a booking looks like
        a ticket -- both seen on real files.
        """
        record = self.identify(
            "Document1.pdf", producer="Acrobat",
            text=("RECHNUNG Nr 4471\nStadtwerke Muenchen GmbH\n" + FILLER
                  + "Holds a current certificate and a diploma. " * 12))
        heading = record.value("heading")
        self.assertTrue(heading.startswith("RECHNUNG"))
        self.assertNotIn("certificate", heading)

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


class Induction(unittest.TestCase):
    """Categories the documents chose, counted rather than looked up."""

    GERMAN = [
        "Rechnung Nr 4471 Stadtwerke Muenchen GmbH",
        "Rechnung Nr 5120 Telekom Deutschland Betrag",
        "Rechnung Nr 6033 Stadtwerke Muenchen GmbH",
        "Steuerbescheid 2011 Finanzamt Muenchen Einkommensteuer",
        "Steuerbescheid 2012 Finanzamt Muenchen Einkommensteuer",
        "Steuerbescheid 2013 Finanzamt Muenchen Einkommensteuer",
        "Mietvertrag Wohnung Hausverwaltung Bauer Paragraph",
        "Mietvertrag Garage Hausverwaltung Bauer Paragraph",
        "Mietvertrag Wohnung Hausverwaltung Klein Paragraph",
    ]

    def words(self, headings):
        return [word for word, _count in shapes.learn_terms(headings)]

    def test_a_language_the_program_has_never_heard_of(self):
        self.assertIn("Rechnung", self.words(self.GERMAN))
        self.assertIn("Steuerbescheid", self.words(self.GERMAN))

    def test_and_another_one(self):
        """Hungarian, which shares no root with any word above."""
        hungarian = ["Szamla 2011 Elmu Budapest", "Szamla 2012 Elmu Budapest",
                     "Szamla 2013 Fogaz Budapest",
                     "Adobevallas 2011 NAV Budapest",
                     "Adobevallas 2012 NAV Budapest",
                     "Adobevallas 2013 NAV Budapest",
                     "Berleti szerzodes Kovacs Budapest",
                     "Berleti szerzodes Nagy Budapest",
                     "Berleti szerzodes Toth Budapest"]
        words = self.words(hungarian)
        self.assertIn("Szamla", words)
        self.assertIn("Adobevallas", words)

    def test_twice_is_not_a_pattern(self):
        """Three occurrences name a pattern; two are a coincidence."""
        pairs = ["Rechnung eins", "Rechnung zwei",
                 "Steuerbescheid eins", "Steuerbescheid zwei",
                 "Mietvertrag eins", "Mietvertrag zwei"]
        self.assertEqual(shapes.learn_terms(pairs), [])

    def test_a_letterhead_is_not_a_category(self):
        """A word on every document describes the pile, not a division.

        Nine CVs all headed with the same person's name: there is no
        structure here and saying so is the correct answer.
        """
        cvs = ["Tamas Torok Multilingual Housekeeper Technician",
               "Tamas Torok Multilingual Service Worker",
               "Tamas Torok Sales Advisor Candidate",
               "Tamas Torok IT Support Technician",
               "Tamas Torok Multilingual Generalist Operations"]
        self.assertEqual(shapes.learn_terms(cvs), [])

    def test_one_word_repeating_is_not_a_structure(self):
        """Everything in one folder sorts exactly as well as nothing."""
        same = ["Rechnung %d Stadtwerke" % n for n in range(9)]
        self.assertEqual(shapes.learn_terms(same), [])

    def test_spelling_is_not_three_folders(self):
        """Twenty years of typing habits give one word three spellings."""
        mixed = ["Rechnung Stadtwerke", "rechnung telekom", "RECHNUNG allianz",
                 "Steuerbescheid 2009", "steuerbescheid 2010",
                 "Steuerbescheid 2011", "Mietvertrag Wohnung",
                 "mietvertrag garage", "Mietvertrag Keller"]
        words = [word for word, _count in shapes.learn_terms(mixed)]
        self.assertEqual(sorted(words),
                         ["Mietvertrag", "Rechnung", "Steuerbescheid"])

    def test_the_kind_of_document_comes_before_the_sender(self):
        """Ordered by where a word sits, so a type beats who sent it.

        `Finanzamt` heads as many documents as `Steuerbescheid` does. The
        one at the front of the line is the one that names the folder, and
        nothing here knows which of them is a kind and which a sender.
        """
        letters = ["Steuerbescheid 2011 Finanzamt Muenchen",
                   "Steuerbescheid 2012 Finanzamt Muenchen",
                   "Steuerbescheid 2013 Finanzamt Muenchen",
                   "Rechnung 4471 Stadtwerke Muenchen",
                   "Rechnung 5120 Stadtwerke Muenchen",
                   "Rechnung 6033 Stadtwerke Muenchen"]
        words = [word for word, _count in shapes.learn_terms(letters)]
        self.assertLess(words.index("Steuerbescheid"), words.index("Finanzamt"))
        self.assertLess(words.index("Rechnung"), words.index("Stadtwerke"))
