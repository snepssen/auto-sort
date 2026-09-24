"""What a document calls itself in type bigger than its body.

"A document says what it is at the top" was learnt from CVs and letters
somebody wrote, where the first line is the title. Official paperwork puts
registration numbers, insurers and a payroll program's version stamp first,
and its title further down -- but always drawn bigger than the body. On real
paperwork the only thing drawn bigger still is who the document is for.
"""

from __future__ import annotations

import os
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from readers import pdftext                              # noqa: E402

GAP = pdftext._GAP


def body(words, size=8.5):
    runs = []
    for word in words.split():
        runs += [("F1", word, size), (GAP, " ", 0.0)]
    return runs


class FindingTheTitle(unittest.TestCase):

    def test_the_largest_emphasised_text(self):
        runs = (body("R.S.Z.-nummer Erkenningsnummer Wetsverzekering Kallo")
                + body("ARBEIDSOVEREENKOMST VOOR UITZENDARBEID", 11.0)
                + body("Tussen de werkgever en de werknemer " * 20))
        self.assertEqual(pdftext.title(runs),
                         "ARBEIDSOVEREENKOMST VOOR UITZENDARBEID")

    def test_not_the_person_it_is_addressed_to(self):
        """The recipient's address is often the largest thing on a
        payslip. It says who the document is for, not what it is."""
        runs = (body("Herrn Tamas Török Haart 96 Neumünster", 11.0)
                + body("Lohn-/Gehalts-Abrechnung", 10.0)
                + body("Programmversion zvoove Payroll Tätigkeit " * 30, 8.0))
        self.assertEqual(pdftext.title(runs, exclude={"tamas", "torok"}),
                         "Lohn-/Gehalts-Abrechnung")

    def test_nor_a_signature_stamp_on_their_behalf(self):
        runs = (body("Digitally signed on behalf of Tamas Torok", 12.0)
                + body("ARBEIDSOVEREENKOMST", 11.0)
                + body("word " * 60))
        self.assertEqual(pdftext.title(runs, exclude={"tamas", "torok"}),
                         "ARBEIDSOVEREENKOMST")

    def test_nothing_emphasised_means_no_title(self):
        self.assertEqual(pdftext.title(body("all one size " * 30)), "")

    def test_a_title_drawn_on_every_page_is_said_once(self):
        runs = (body("Loonbrief", 13.0) + body("text " * 40)
                + body("Loonbrief", 13.0) + body("text " * 40))
        self.assertEqual(pdftext.title(runs), "Loonbrief")

    def test_a_greeting_is_not_a_title(self):
        """It ends with a comma in nearly every language that uses one."""
        runs = body("Dear Hiring Manager,", 12.0) + body("body " * 40, 10.9)
        self.assertEqual(pdftext.title(runs), "")

    def test_a_paragraph_set_large_is_not_a_title(self):
        runs = body("one two three four five six seven eight nine ten "
                    "eleven twelve thirteen", 14.0) + body("x " * 80)
        self.assertEqual(pdftext.title(runs), "")

    def test_stray_glyphs_are_dropped_from_a_title(self):
        runs = body("Luik Í1 Í@ A", 12.0) + body("body " * 40)
        self.assertEqual(pdftext.title(runs), "Luik A")


class OnTheFirstPage(unittest.TestCase):

    def test_a_later_pages_section_heading_is_not_the_title(self):
        """Six-page contracts were titled `Luik A` from a form attached at
        the back, drawn larger than the contract's own title."""
        runs = (body("ARBEIDSOVEREENKOMST VOOR UITZENDARBEID", 11.0)
                + body("tussen de werkgever en de werknemer " * 150)
                + body("Luik A", 14.0) + body("in te vullen " * 20))
        self.assertEqual(pdftext.title(runs),
                         "ARBEIDSOVEREENKOMST VOOR UITZENDARBEID")


class _Peek:
    def __init__(self, data):
        self.data = data

    def at(self, offset, size):
        return self.data[offset:offset + size]


def _stream(number, content):
    packed = zlib.compress(content)
    return (b"%d 0 obj\n<</Length %d/Filter/FlateDecode>>\nstream\n"
            % (number, len(packed)) + packed + b"\nendstream\nendobj\n")


def _contract():
    """Page two stored before page one, and `/F7` meaning two fonts.

    The shape of three real employment contracts: page two's resources come
    first in the file and declare `/F7` as a font with two-byte codes and a
    table of its own; page one declares `/F7` as plain Times-Bold and draws
    its title in it.
    """
    cmap = (b"begincmap\n1 begincodespacerange <0000> <FFFF> "
            b"endcodespacerange\n1 beginbfchar <0374> <2013> endbfchar\n"
            b"endcmap")
    later = (b"BT /F1 14 Tf 1 0 0 1 50 700 Tm (Luik A) Tj "
             b"/F1 8 Tf (in te vullen door de werknemer zelf) Tj ET")
    first = (b"BT /F7 11 Tf 1 0 0 1 150 650 Tm "
             b"[(ARBEIDSOVEREENKOMST) -250 (VOOR) -250 (UITZENDARBEID)] TJ "
             b"/F5 8.5 Tf 1 0 0 1 50 600 Tm "
             b"(Tussen de werkgever en de werknemer wordt het volgende "
             b"overeengekomen voor de duur van deze opdracht) Tj ET")
    parts = [b"%PDF-1.4\n",
             b"1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n",
             b"2 0 obj\n<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>\nendobj\n",
             b"5 0 obj\n<</Type/Font/Subtype/Type0/BaseFont/Calibri"
             b"/Encoding/Identity-H/ToUnicode 6 0 R>>\nendobj\n",
             _stream(6, cmap),
             _stream(7, later),
             b"4 0 obj\n<</Type/Page/Parent 2 0 R/Resources<</Font<<"
             b"/F7 5 0 R/F1 9 0 R>>>>/Contents 7 0 R>>\nendobj\n",
             b"8 0 obj\n<</Type/Font/Subtype/Type1/BaseFont/Times-Bold"
             b"/Encoding/WinAnsiEncoding>>\nendobj\n",
             b"9 0 obj\n<</Type/Font/Subtype/Type1/BaseFont/Helvetica"
             b"/Encoding/WinAnsiEncoding>>\nendobj\n",
             _stream(10, first),
             b"3 0 obj\n<</Type/Page/Parent 2 0 R/Resources<</Font<<"
             b"/F7 8 0 R/F5 9 0 R>>>>/Contents 10 0 R>>\nendobj\n",
             b"trailer\n<</Root 1 0 R>>\n%%EOF\n"]
    return b"".join(parts)


class PageOnesOwnFonts(unittest.TestCase):
    """A resource name means what the page using it says it means."""

    def test_the_title_is_read_in_page_ones_font(self):
        _text, _image_only, found = pdftext.read(_Peek(_contract()))
        self.assertEqual(found, "ARBEIDSOVEREENKOMST VOOR UITZENDARBEID")

    def test_page_one_is_found_through_the_tree(self):
        data = _contract()
        streams, fonts = pdftext._first_page(data, pdftext._objects(data))
        self.assertEqual(streams, [10])
        self.assertEqual(fonts, {"F7": 8, "F5": 9})

    def test_a_file_whose_tree_cannot_be_followed_reads_as_before(self):
        data = _contract().replace(b"/Root 1 0 R", b"/Root 99 0 R").replace(
            b"/Type/Catalog", b"/Type/Nothing")
        self.assertEqual(pdftext._first_page(data, pdftext._objects(data)),
                         ([], {}))
        text, _image_only, _found = pdftext.read(_Peek(data))
        self.assertIn("werkgever", text)


class TheHeadingKeepsTheSender(unittest.TestCase):
    """Four CompTIA certificates headed only "OF COMPLETION" no longer said
    CompTIA anywhere, and every rule learnt from a sender stopped matching."""

    def test_the_title_comes_first_and_the_top_of_the_page_after(self):
        from readers import document
        self.assertEqual(
            document._title_then_top(
                "OF COMPLETION", "Tamas Torok CompTIA FC0-U61: IT"),
            "OF COMPLETION Tamas Torok CompTIA FC0-U61: IT")

    def test_what_the_title_said_is_not_said_twice(self):
        from readers import document
        self.assertEqual(
            document._title_then_top("Lohn-/Gehalts-Abrechnung",
                                     "Lohn-/Gehalts-Abrechnung zvoove"),
            "Lohn-/Gehalts-Abrechnung zvoove")

    def test_no_title_is_the_top_of_the_page(self):
        from readers import document
        self.assertEqual(document._title_then_top("", "Rechnung Nr 4711"),
                         "Rechnung Nr 4711")


class DropCapitals(unittest.TestCase):
    """A capital drawn large on its own, and the pen jump after it."""

    def test_a_capital_joined_to_the_rest_of_its_word(self):
        runs = ([("F7", "C", 16.0), ("F7", "ERTIFICATE", 12.0),
                 (GAP, " ", 0.0), ("F7", "OF", 12.0), (GAP, " ", 0.0),
                 ("F7", "COMPLETION", 12.0)] + body("body " * 40, 10.0))
        self.assertEqual(pdftext.title(runs), "CERTIFICATE OF COMPLETION")

    def test_not_across_a_gap(self):
        """`Luik A` then body text in lower case looks exactly like a drop
        capital, and is not one."""
        runs = body("Luik A", 12.0) + body("in te vullen " * 20, 8.5)
        self.assertEqual(pdftext.title(runs), "Luik A")

    def test_a_lone_capital_word_stays_a_word(self):
        """`A` followed by a capitalised word is not a drop capital."""
        runs = ([("F7", "A", 12.0), (GAP, " ", 0.0), ("F7", "Title", 12.0)]
                + body("body " * 40, 10.0))
        self.assertEqual(pdftext.title(runs), "A Title")


if __name__ == "__main__":
    unittest.main()
