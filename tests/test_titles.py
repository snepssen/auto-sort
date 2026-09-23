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
