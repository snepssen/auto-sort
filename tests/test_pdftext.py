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
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures                                          # noqa: E402
import identify                                          # noqa: E402
import signatures                                        # noqa: E402
import propose                                           # noqa: E402
import rules                                             # noqa: E402
import shapes                                            # noqa: E402
import sorter                                            # noqa: E402
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

    def test_a_page_full_of_numbers_is_still_a_page(self):
        """The bug this replaced: judged on characters, not words.

        An invoice is amounts, dates, customer numbers and reference codes.
        One real document came out at 62,994 characters, 36% of them
        letters, and 3,035 words -- and was thrown away for being 36%
        letters. On one folder the old test discarded 177 readable
        documents and then held every one of them as an unreadable scan.
        """
        numbers = " ".join("4711%03d 12,%02d EUR 2019-05-%02d" % (n, n, n % 28 + 1)
                           for n in range(40))
        text, image_only = self.extract(text=INVOICE + "\n" + numbers)
        self.assertFalse(image_only)
        self.assertIn("RECHNUNG", text)
        letters = sum(1 for char in text if char.isalpha())
        self.assertLess(letters, len(text) * 0.45, "the old test would pass")

    def test_the_smallest_real_document(self):
        """A one-line invoice is an ordinary thing for somebody to have."""
        text, image_only = self.extract(text=INVOICE)
        self.assertFalse(image_only)
        self.assertIn("Stadtwerke", text)

    def test_page_furniture_is_not_the_text_of_a_document(self):
        """A stamp or a page number must not stop a scan being read."""
        text, image_only = self.extract(text="Seite 1 von 3")
        self.assertEqual(text, "")

    def test_codes_that_decode_to_nothing_are_still_refused(self):
        """A subset CID font with no table comes back as control bytes."""
        soup = "".join(chr(index % 32) for index in range(400))
        text, _image_only = self.extract(text=soup)
        self.assertEqual(text, "")

    def test_a_fonts_glyph_numbers_are_not_words(self):
        """A subset font with no table numbers its glyphs from scratch.

        Read as characters they pass every test for words and mean
        nothing. One real folder had 171 documents whose headings agreed
        on the same seven of them, and agreement is what this program
        treats as evidence -- it would have named a folder after it.
        """
        codes = "".join(chr(0x80 + (index * 7) % 120) for index in range(300))
        self.assertTrue(pdftext._glyph_codes(codes))

    def test_a_page_of_greek_is_words(self):
        """Wholly above ASCII and wholly letters. The test takes both."""
        greek = "Τιμολόγιο αριθμός 4711 ημερομηνία 3 Μαΐου 2019 " * 8
        self.assertFalse(pdftext._glyph_codes(greek))

    def test_ordinary_accented_text_is_words(self):
        for line in ("Rechnung Nr 4711 Stadtwerke München Betrag",
                     "Loonbrief Individuele rekening Kantoor afhaling",
                     "Számla sorszáma fizetési határidő"):
            self.assertFalse(pdftext._glyph_codes(line * 6), line)

    def test_soup_is_dropped_a_font_at_a_time(self):
        """A readable font and an unreadable one, interleaved in one stream.

        That is how it arrives: a German invoice whose body is in Times and
        whose bullets and rules come from a subset font with no character
        map. Refusing the file for the second throws away the first.
        """
        # The real bytes, from the real series that prompted this.
        codes = "ìª® êí0@Âè ï®ÞÍà\x9c€ì°"
        runs = []
        for word in INVOICE.split():
            runs.append(("F1", word, 10.0))
            runs.append((pdftext._GAP, " ", 0.0))
            runs.append(("F9", codes[:4], 10.0))  # three or four at a time
            runs.append((pdftext._GAP, " ", 0.0))
        text = pdftext._keep_readable_fonts(runs)
        self.assertIn("RECHNUNG", text)
        self.assertIn("Stadtwerke", text)
        self.assertFalse(any(ord(char) > 0x7f and char not in "üöäßÜÖÄ"
                             for char in text), text)

    def test_strings_too_short_to_judge_alone_are_judged_together(self):
        """A real series of 172 documents drew its soup three characters at
        a time, and each string on its own passed every test."""
        pieces = ["ìª®", "êí0", "@Âè", "ï®Þ", "Íà\x9c", "€ì°"]
        self.assertFalse(pdftext._glyph_codes("Íàx"))
        runs = [("F9", piece, 10.0) for piece in pieces * 10]
        self.assertEqual(pdftext._keep_readable_fonts(runs).strip(), "")

    def test_a_single_readable_font_is_all_kept(self):
        runs = [("F1", word, 10.0) for word in INVOICE.split()]
        self.assertEqual(pdftext._keep_readable_fonts(runs),
                         "".join(INVOICE.split()))

    def test_a_scan_with_a_stamp_on_it_still_asks_for_ocr(self):
        path = self.build("scan.pdf", text="Eingegangen 03 Mai",
                          image_only=True)
        record = identify.identify(path, tier=identify.TIER_HEADER)
        self.assertTrue(record.value("needs_ocr"))

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

    PAYSLIP = ("Lohnabrechnung\nJanuar 2024\nPersonalnummer 300\n"
               "Brutto und Netto Bezuege des Arbeitnehmers im Monat")

    def test_a_page_with_words_is_not_a_scan_whatever_it_is_called(self):
        """Three payslips named `doc_7.pdf` went to Scans/<year>, a folder
        nothing is ever learnt from, on the strength of a name that looks
        like a scanner's -- while their own text said what they were."""
        record = self.identify("doc_7.pdf", producer="DATEV",
                               text=self.PAYSLIP)
        self.assertTrue(record.value("text_layer"))
        self.assertNotEqual(record.value("capture"), "scan")

    def test_a_page_with_no_words_is_still_a_scan_by_its_name(self):
        record = self.identify("doc_7.pdf", producer="DATEV",
                               image_only=True)
        self.assertEqual(record.value("capture"), "scan")

    def test_too_few_words_to_be_read_do_not_outrank_the_name(self):
        record = self.identify("doc_7.pdf", producer="DATEV",
                               text="Seite 1")
        self.assertFalse(record.value("text_layer"))
        self.assertEqual(record.value("capture"), "scan")

    def test_scanning_software_still_says_scan_over_a_text_layer(self):
        """Only the name gives way. A scanner that writes its own OCR
        layer is still a scanner."""
        record = self.identify("doc_7.pdf", producer="HP ScanJet Pro firmware",
                               text=self.PAYSLIP)
        self.assertEqual(record.value("capture"), "scan")

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


class WhatTheTopOfAPageSays(unittest.TestCase):
    """Words, not tokens."""

    def test_a_page_that_starts_with_words_is_unchanged(self):
        from readers import document
        self.assertEqual(document.heading_of(INVOICE),
                         "RECHNUNG Nr Stadtwerke Muenchen GmbH Betrag:")

    def test_a_band_of_numbers_is_stepped_over(self):
        """One real series opened every page with dates and account
        numbers, and six tokens of it had no word in them at all."""
        from readers import document
        page = ("31.12.2019 94112559131 0001202000 1 20200113 0000 "
                "RBU Loonstrook Individuele rekening Periode")
        self.assertEqual(document.heading_of(page),
                         "RBU Loonstrook Individuele rekening Periode")

    def test_the_window_still_ends_where_it_ended(self):
        """A document says what it is at the top; a word mentioned further
        down must not become its heading just because the top was numbers.
        """
        from readers import document
        page = "4711 " * 200 + "Certificate"
        self.assertEqual(document.heading_of(page), "")


class OnlyWhatThePageDraws(unittest.TestCase):
    """A PDF stores things that are not its page -- attached files, fonts,
    pictures, metadata -- and some of them contain `BT` by chance."""

    def stream(self, dictionary, body):
        header = b"5 0 obj " + dictionary + b"\nstream\n"
        data = b"%PDF-1.4\n" + header + body + b"\nendstream endobj\n"
        return data, len(b"%PDF-1.4\n") + len(header) - len(b"stream\n") - 1

    def test_an_attached_file_is_not_the_page(self):
        """171 real payslips each carried a whole PDF attached inside, and
        three bytes of its compressed insides were offered as the name the
        series had chosen for itself."""
        data, at = self.stream(b"<</Type/EmbeddedFile/Length 20>>",
                               b"BT (x) Tj ET")
        self.assertFalse(pdftext._is_page_content(data, at, b"BT (x) Tj ET"))

    def test_a_picture_is_not_the_page(self):
        data, at = self.stream(b"<</Subtype/Image/Width 9>>", b"BT")
        self.assertFalse(pdftext._is_page_content(data, at, b"BT (x) Tj ET"))

    def test_a_form_that_may_draw_images_is_still_a_page(self):
        """`/ImageC` in the list of things a page may draw says nothing
        about what the stream is -- matching it as a substring refused
        every page of a real payslip."""
        data, at = self.stream(
            b"<</Subtype/Form/Resources<</ProcSet[/PDF/ImageC/Text]>>>>",
            b"BT (x) Tj ET")
        self.assertTrue(pdftext._is_page_content(data, at, b"BT (x) Tj ET"))

    def test_glyph_codes_in_strings_do_not_make_a_page_binary(self):
        """A privacy policy's page drew every word as two-byte glyph codes,
        was 69% printable overall, and was refused -- 1,270 words lost."""
        body = b"BT /F1 12 Tf 72 700 Td " + b" ".join(
            b"(" + bytes([0, code, 0, code + 1, 0, code + 2]) + b") Tj"
            for code in range(1, 200)) + b" ET"
        data, at = self.stream(b"<</Subtype/Form/Length 9>>", body)
        self.assertTrue(pdftext._is_page_content(data, at, body))

    def test_binary_with_an_early_bracket_is_still_binary(self):
        import os as _os
        binary = b"(" + _os.urandom(4000) + b"BT"
        data, at = self.stream(b"<</Length 9>>", binary)
        self.assertFalse(pdftext._is_page_content(data, at, binary))

    def test_bytes_that_are_mostly_not_instructions(self):
        data, at = self.stream(b"<</Length 9>>", b"")
        binary = bytes(range(256)) * 4 + b"BT"
        self.assertFalse(pdftext._is_page_content(data, at, binary))


class _Peek:
    def __init__(self, data):
        self.data = data

    def at(self, offset, size):
        return self.data[offset:offset + size]


# How Qt (and so every wkhtmltopdf document) writes: glyph ids through
# a two-byte font, each glyph its own string moved to with `Td`, spaces
# drawn as glyphs, and a character map stored uncompressed in the
# bracketed form.
_QT_TEXT = "Loonbrief voor de maand september met alle bedragen erop"


def _qt_like(compress_map=False):
    import zlib
    letters = sorted(set(_QT_TEXT))
    glyph = dict((char, index + 1) for index, char in enumerate(letters))
    targets = b" ".join(b"<%04X>" % ord(char) for char in letters)
    cmap = (b"begincmap\n1 begincodespacerange\n<0000> <FFFF>\n"
            b"endcodespacerange\n1 beginbfrange\n<0001> <%04X> [%s]\n"
            b"endbfrange\nendcmap" % (len(letters), targets))
    steps = b"\n".join(b"7 0 Td <%04X> Tj" % glyph[char] for char in _QT_TEXT)
    page = zlib.compress(b"BT\n/F6 14 Tf 1 0 0 -1 0 0 Tm\n" + steps + b"\nET")
    if compress_map:
        cmap_object = (b"6 0 obj\n<</Length %d/Filter/FlateDecode>>stream\n"
                       % len(zlib.compress(cmap)) + zlib.compress(cmap))
    else:
        cmap_object = b"6 0 obj\n<</Length %d>>stream\n" % len(cmap) + cmap
    return (b"%PDF-1.4\n"
            b"5 0 obj\n<</Type/Font/Subtype/Type0/Encoding/Identity-H"
            b"/ToUnicode 6 0 R>>\nendobj\n"
            + cmap_object + b"\nendstream\nendobj\n"
            b"4 0 obj\n<</Type/Page/Resources<</Font<</F6 5 0 R>>>>"
            b"/Contents 7 0 R>>\nendobj\n"
            b"7 0 obj\n<</Length %d/Filter/FlateDecode>>stream\n" % len(page)
            + page + b"\nendstream\nendobj\n%%EOF\n")


class WhatQtWrites(unittest.TestCase):
    """Two real payslips written this way were read as having no words at
    all, and sent to be OCR'd as though they were photographs."""

    def test_the_whole_document_reads(self):
        text, image_only = pdftext.extract(_Peek(_qt_like()))
        self.assertEqual(text, _QT_TEXT)
        self.assertFalse(image_only)

    def test_a_map_stored_plain_is_still_a_map(self):
        compressed, _ = pdftext.extract(_Peek(_qt_like(compress_map=True)))
        plain, _ = pdftext.extract(_Peek(_qt_like()))
        self.assertEqual(plain, compressed)

    def test_a_range_that_lists_its_targets(self):
        codes, width = pdftext._parse_cmap(
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"1 beginbfrange <0001> <0003> [<0053> <0044> <0020>] endbfrange")
        self.assertEqual(width, 2)
        self.assertEqual(codes, {1: "S", 2: "D", 3: " "})

    def test_the_list_is_not_read_as_ranges_of_its_own(self):
        """Read as the run form, `<0035> <002F> <0039>` inside the list
        became a mapping of its own."""
        codes, _width = pdftext._parse_cmap(
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"1 beginbfrange <0001> <0004> [<0035> <002F> <0039> <0032>] "
            b"endbfrange")
        self.assertEqual(sorted(codes), [1, 2, 3, 4])

    def test_words_placed_one_at_a_time_keep_their_gaps(self):
        """The other kind of writer: a word per string, moved with `Td`."""
        body = b"BT /F1 10 Tf " + b" ".join(
            b"30 0 Td (%s) Tj" % word.encode()
            for word in "one two three four five six seven eight nine ten "
                        "eleven twelve thirteen fourteen fifteen sixteen "
                        "seventeen eighteen nineteen twenty".split()) + b" ET"
        text = pdftext._page_text(body, {})
        self.assertTrue(text.startswith("one two three four"), text)

    def test_a_jump_along_the_line_is_still_a_gap(self):
        """One glyph at a time, then a jump to the next column."""
        steps = [b"6 0 Td (%s) Tj" % char.encode() for char in "Betrag"]
        steps.append(b"200 0 Td (1) Tj")
        steps += [b"6 0 Td (%s) Tj" % char.encode() for char in "2345678901234567"]
        body = b"BT /F1 10 Tf " + b" ".join(steps) + b" ET"
        self.assertEqual(pdftext._page_text(body, {}), "Betrag 12345678901234567")


class WhatAMacPrints(unittest.TestCase):
    """Quartz gives each glyph or two a text block and a matrix of its own,
    and read block by block a payslip said `P a ym e n ts`."""

    widths = {"F1": ({ord("P"): 600, ord("a"): 500, ord("y"): 500,
                      ord("s"): 450}, 500.0, 1)}

    def drawn(self, *placed):
        return b" ".join(
            b"BT 10 0 0 10 %s 700 Tm /F1 1 Tf (%s) Tj ET"
            % (str(x).encode(), glyph.encode()) for x, glyph in placed)

    def runs(self, body, widths):
        runs, _current = pdftext._page_runs(body, {}, None, widths)
        return pdftext._keep_readable_fonts(runs)

    def test_a_glyph_that_starts_where_the_last_ended_joins_it(self):
        # P is 600 wide at size 10: it ends at 106, where `a` begins.
        body = self.drawn((100, "P"), (106, "a"), (111, "y"), (120, "s"))
        self.assertEqual(self.runs(body, self.widths), "Pay s")

    def test_without_widths_nothing_changes(self):
        body = self.drawn((100, "P"), (106, "a"))
        self.assertEqual(self.runs(body, None), "P a")

    def test_a_new_line_is_a_gap(self):
        body = (b"BT 10 0 0 10 100 700 Tm /F1 1 Tf (P) Tj ET "
                b"BT 10 0 0 10 106 680 Tm /F1 1 Tf (a) Tj ET")
        self.assertEqual(self.runs(body, self.widths), "P a")

    def test_the_rest_of_a_word_in_a_tj_after_its_matrix(self):
        """`[(At) 1 (ho)] TJ`, then a new block for `(ll)`: the pen is
        followed through the TJ, so the new block is known to continue it."""
        widths = {"F1": ({ord(c): 500 for c in "Athol"}, 500.0, 1)}
        body = (b"BT 10 0 0 10 100 700 Tm /F1 1 Tf [(At) 0 (ho)] TJ ET "
                b"BT 10 0 0 10 120 700 Tm /F1 1 Tf (ll) Tj ET")
        self.assertEqual(self.runs(body, widths), "Atholl")

    def test_a_step_the_width_of_the_last_glyph_is_the_same_word(self):
        widths = {"F1": ({ord("P"): 640, ord("a"): 570}, 500.0, 1)}
        self.assertFalse(pdftext._stepped_gap(b" Tj 0.64 0 Td ",
                                              ("F1", 1.0, b"P"), widths))
        self.assertTrue(pdftext._stepped_gap(b" Tj 0.9 0 Td ",
                                             ("F1", 1.0, b"P"), widths))

    def test_a_step_backwards_is_another_column(self):
        """A Belgian payslip steps left to right-aligned columns, and read as
        a continuation its headings ran together: `BedragBasisAantal`."""
        widths = {"F1": ({ord(c): 500 for c in "Bedrag"}, 500.0, 1)}
        self.assertTrue(pdftext._stepped_gap(b" Tj -52 0 Td ",
                                             ("F1", 5.8, b"Bedrag"), widths))

    def test_cid_widths_in_both_forms(self):
        self.assertEqual(pdftext._cid_widths(b"[1 [500 600] 10 12 250]"),
                         {1: 500.0, 2: 600.0, 10: 250.0, 11: 250.0,
                          12: 250.0})


class TablesThatSayLessThanTheyShould(unittest.TestCase):
    """Character maps as real writers leave them, not as the spec has them."""

    def test_the_codes_listed_decide_the_width(self):
        """Declared `<0000> <FFFF>`, listed one byte at a time: read two
        bytes at a time, a real certificate went blank."""
        codes, width = pdftext._parse_cmap(
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"2 beginbfchar <77> <0077> <92> <2019> endbfchar")
        self.assertEqual(width, 1)
        self.assertEqual(pdftext._through(b"w\x92s", codes, width), "w\u2019s")

    def test_a_one_byte_code_left_out_is_itself(self):
        """A table of only the exceptions: the ligature, the curly quote."""
        codes = {0x92: "\u2019", 0x1F: "fi"}
        self.assertEqual(pdftext._through(b"\x1Fgures", codes, 1), "figures")

    def test_a_two_byte_code_left_out_is_nothing(self):
        self.assertEqual(pdftext._through(b"\x00\x41", {}, 2), "")

    def test_the_end_of_a_stream_is_not_the_start_of_one(self):
        data = (b"1 0 obj <</Length 3>> stream\nabc\nendstream\nendobj\n"
                b"2 0 obj <</Length 3>> stream\ndef\nendstream\nendobj\n")
        self.assertEqual(len(pdftext._STREAM.findall(data)), 2)


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

    def test_the_sender_is_not_a_second_category(self):
        """`Finanzamt` heads exactly the documents `Steuerbescheid` does.

        Counting alone cannot separate them -- both appear three times --
        but the *same* three is the giveaway: the sender is part of the
        letter's template, not a category standing beside it. Only the word
        at the front of the line survives.
        """
        letters = ["Steuerbescheid 2011 Finanzamt Muenchen",
                   "Steuerbescheid 2012 Finanzamt Muenchen",
                   "Steuerbescheid 2013 Finanzamt Muenchen",
                   "Rechnung 4471 Stadtwerke Muenchen",
                   "Rechnung 5120 Stadtwerke Muenchen",
                   "Rechnung 6033 Stadtwerke Muenchen"]
        words = [word for word, _count in shapes.learn_terms(letters)]
        self.assertIn("Steuerbescheid", words)
        self.assertIn("Rechnung", words)
        self.assertNotIn("Finanzamt", words)
        self.assertNotIn("Stadtwerke", words)


class WordsInsideWords(unittest.TestCase):
    """A learnt word that lives inside another learnt word.

    German compounds make this ordinary rather than exotic: learn `Vertrag`
    and `Mietvertrag` from the same folder and a plain substring match hands
    every tenancy agreement to the shorter rule.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-inside-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def sorted_into(self, names):
        for name in names:
            fixtures.pdf(os.path.join(self.dir, name + ".pdf"),
                         producer="HP ScanJet Pro firmware", image_only=True)
        found = propose.survey(self.dir)
        text = propose.render(found, propose.assess(found)).replace(
            "settle_seconds = 3", "settle_seconds = 0")
        path = os.path.join(self.dir, "r.ini")
        with open(path, "w") as handle:
            handle.write(text)
        plan = sorter.build_plan(self.dir, rules.load(path))
        return {os.path.basename(item.members[0].source):
                os.path.basename(os.path.dirname(
                    item.members[0].destination))
                for item in plan.items}

    # Three distinct categories, because two is not yet a structure and
    # nothing would be learnt at all.
    PILE = ["Mietvertrag Wohnung", "Mietvertrag Garage", "mietvertrag Keller",
            "Vertrag 2011", "Kauf Vertrag Auto", "Vertrag Telekom",
            "Rechnung Stadtwerke", "rechnung telekom", "RECHNUNG allianz"]

    def test_the_longer_word_keeps_its_own_files(self):
        placed = self.sorted_into(self.PILE)
        self.assertEqual(placed["Mietvertrag Wohnung.pdf"], "Mietvertrag")
        self.assertEqual(placed["mietvertrag Keller.pdf"], "Mietvertrag")

    def test_the_shorter_word_still_gets_its_own(self):
        """Including the one with nothing in front of it to match a space."""
        placed = self.sorted_into(self.PILE)
        self.assertEqual(placed["Vertrag 2011.pdf"], "Vertrag")
        self.assertEqual(placed["Kauf Vertrag Auto.pdf"], "Vertrag")

    def test_two_categories_are_not_yet_a_structure(self):
        """The reason the pile above has three kinds in it and not two."""
        placed = self.sorted_into(self.PILE[:6])
        self.assertNotIn("Mietvertrag", set(placed.values()))

    def test_a_name_spelled_two_ways_is_one_name(self):
        """Accents split a person in half and let both halves through.

        Observed on a real library: `Tamás` and `Tamas` were counted
        separately, each stayed under the letterhead ceiling, and both came
        back as categories.
        """
        letters = ["Tamás Török Rechnung eins", "Tamas Torok Rechnung zwei",
                   "TAMÁS TÖRÖK Rechnung drei", "Tamás Török Rechnung vier",
                   "Tamas Torok Mietvertrag eins",
                   "Tamás Török Mietvertrag zwei",
                   "TAMAS TOROK Mietvertrag drei"]
        words = [shapes._fold(word) for word, _count
                 in shapes.learn_terms(letters)]
        self.assertEqual(words.count("tamas"), len([w for w in words
                                                    if w == "tamas"]))
        self.assertLessEqual(words.count("tamas"), 1)

    def test_a_word_inside_two_categories_is_neither(self):
        """Whoever the documents are about is not a third kind of document."""
        letters = ["Loonbrief Tamas Kantoor", "Loonbrief Tamas Kantoor",
                   "Loonbrief Tamas Kantoor", "Payroll Tamas Office",
                   "Payroll Tamas Office", "Payroll Tamas Office"]
        words = [word for word, _count in shapes.learn_terms(letters)]
        self.assertIn("Loonbrief", words)
        self.assertIn("Payroll", words)
        self.assertNotIn("Tamas", words)


class PatternsThatMustNotBacktrack(unittest.TestCase):
    """A regex that is quadratic on binary data stops the whole program.

    auto-sort reads PDFs on the daemon's only thread. A pattern that takes
    a day on four megabytes does not fail, or log, or time out -- it simply
    stops sorting, stops answering the log page, and freezes the tray, and
    the only diagnosis available to the person it happens to is that the
    icon has gone. This one did exactly that on a real machine, on a Belgian
    document whose name and contents were long runs of digits.
    """

    def scan(self, blob):
        start = time.time()
        list(pdftext._OBJ.finditer(blob))
        return time.time() - start

    def test_a_long_run_of_digits_is_scanned_in_linear_time(self):
        small = self.scan(b"9" * 20000)
        large = self.scan(b"9" * 160000)
        # Eight times the input. Quadratic would be sixty-four times the
        # work; a generous ceiling of twenty still fails it loudly while
        # tolerating a slow or busy machine.
        self.assertLess(large, max(small * 20, 0.5),
                        "object scan is super-linear: %.3fs then %.3fs"
                        % (small, large))

    def test_four_megabytes_of_digits_finishes_promptly(self):
        self.assertLess(self.scan(b"8" * (4 * 1024 * 1024)), 2.0)

    def test_it_still_finds_the_object_headers_it_is_for(self):
        for blob, want in ((b"12 0 obj", b"12"), (b"\n7 0 obj\n", b"7"),
                           (b"123456 65535 obj", b"123456"),
                           (b"4 0  obj", b"4")):
            match = pdftext._OBJ.search(blob)
            self.assertIsNotNone(match, blob)
            self.assertEqual(match.group(1), want)

    def test_a_number_that_is_not_an_object_header_is_not_one(self):
        self.assertIsNone(pdftext._OBJ.search(b"999 not an object"))


class APdfMustNotBeAbleToAskForAGigabyte(unittest.TestCase):
    """`zlib.decompress` has no output limit and a PDF is compressed.

    A real 0.91 MB file in a real folder grew the process by 171 MB on its
    own. Only the first few thousand characters are ever used, so a stream
    that wants more than the cap has nothing to offer that is worth it.
    """

    def test_a_highly_compressible_stream_is_capped(self):
        import zlib
        bomb = zlib.compress(b"A" * (64 * 1024 * 1024))
        self.assertLess(len(bomb), 100000, "test blob is not compressible")
        out = pdftext._unzip(bomb)
        self.assertIsNotNone(out)
        self.assertLessEqual(len(out), pdftext.MAX_INFLATE)

    def test_an_ordinary_stream_is_returned_whole(self):
        import zlib
        body = b"BT /F1 12 Tf (Rechnung) Tj ET" * 20
        self.assertEqual(pdftext._unzip(zlib.compress(body)), body)

    def test_rubbish_is_declined_rather_than_raising(self):
        self.assertIsNone(pdftext._unzip(b"not compressed at all"))
