"""The one word this program is allowed to know without counting it.

Categories come from counting what turns up, in whatever language the post
arrives in, and there is no table of document types anywhere here. But the
operating system was told the owner's name when the account was made, and
that name is at the top of their payslip, their tenancy agreement and their
phone bill. It heads fifty documents and divides none of them.

The counting cannot see that -- fifty out of five hundred is exactly the
shape of a real category -- so it is told.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import owner                                             # noqa: E402
import shapes                                            # noqa: E402


class AskingTheMachine(unittest.TestCase):

    def setUp(self):
        owner.forget()

    def tearDown(self):
        owner.forget()

    def test_a_full_name_becomes_words(self):
        with mock.patch.object(owner, "_from_account",
                               return_value="Tamás Török"):
            self.assertEqual(owner.names(), {"tamás", "török"})

    def test_the_other_gecos_fields_are_not_the_name(self):
        """The record is comma-separated: name, office, telephone."""
        with mock.patch.object(owner, "_from_account",
                               return_value="Ada Lovelace,Room 12,555-8080"):
            self.assertEqual(owner.names(), {"ada", "lovelace"})

    def test_honorifics_are_not_the_name(self):
        with mock.patch.object(owner, "_from_account",
                               return_value="Dr Grace Hopper"):
            self.assertEqual(owner.names(), {"grace", "hopper"})

    def test_an_account_with_no_name_is_an_ordinary_answer(self):
        with mock.patch.object(owner, "_from_account", return_value=""), \
                mock.patch.dict(os.environ, {"USERFULLNAME": ""}, clear=False):
            self.assertEqual(owner.names(), set())

    def test_the_machine_name_counts_as_an_account_name(self):
        """`sausage@factory` is somebody's idea of a hostname and turns up
        in exported headers across everything they own."""
        owner.forget()
        with mock.patch.object(owner, "_machine",
                               return_value="Tamass-MacBook-Pro.local"):
            self.assertIn("macbook", owner.account())

    def test_a_network_suffix_is_not_the_machines_name(self):
        owner.forget()
        with mock.patch.object(owner, "_machine", return_value="sausage"):
            self.assertIn("sausage", owner.account())

    def test_the_login_is_kept_apart_from_the_name(self):
        with mock.patch.object(owner, "_from_account",
                               return_value="Tamás Török"), \
                mock.patch.dict(os.environ, {"USER": "tamtor"}, clear=False):
            self.assertNotIn("tamtor", owner.names())
            self.assertIn("tamtor", owner.account())

    def test_nothing_here_raises_on_a_system_that_answers_nothing(self):
        with mock.patch.object(owner, "_from_account",
                               side_effect=KeyError("no such user")):
            try:
                owner.names()
            except KeyError:
                self.fail("a missing account record must not raise")


class WhatItChanges(unittest.TestCase):
    """Three hundred documents, fifty of which lead with the owner's name."""

    def headings(self):
        mine = ["Tamás Török Loonbrief %d" % n for n in range(50)]
        bills = ["Rechnung Stadtwerke %d" % n for n in range(40)]
        leases = ["Mietvertrag Wohnung %d" % n for n in range(30)]
        taxes = ["Steuerbescheid Finanzamt %d" % n for n in range(30)]
        return mine + bills + leases + taxes

    def terms(self, **kwargs):
        return [word for word, _count
                in shapes.learn_terms(self.headings(), **kwargs)]

    def test_without_being_told_the_name_is_a_category(self):
        self.assertIn("Tamás", self.terms())

    def test_told_whose_computer_it_is_it_is_not(self):
        found = self.terms(person={"tamás", "török"})
        self.assertNotIn("Tamás", found)
        self.assertIn("Rechnung", found)
        self.assertIn("Loonbrief", found)

    def test_an_accented_name_still_matches_its_own_word(self):
        """`Tamás` folds to `tamas` in the counting and must fold on both
        sides of the comparison, or the name never matches itself."""
        self.assertNotIn("Tamás", self.terms(person={"Tamás"}))

    def test_a_real_name_is_never_a_category_however_rare(self):
        """The allowance a surname used to get let the owner's own name
        through twice on a real machine. It is gone."""
        headings = (["Török statement %d" % n for n in range(23)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(100)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(100)]
                    + ["Steuerbescheid Finanzamt %d" % n for n in range(74)])
        found = [word for word, _count
                 in shapes.learn_terms(headings, person={"török"})]
        self.assertNotIn("Török", found)

    def test_a_login_that_is_also_a_word_keeps_its_chance(self):
        """`Koch` is a cook, `Baker` is a baker, `Bill` is a bill. For an
        account name -- not the real name -- below the lower ceiling it is
        the language, not the letterhead."""
        headings = (["Bill from the garage %d" % n for n in range(4)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(40)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(30)]
                    + ["Steuerbescheid Finanzamt %d" % n for n in range(30)])
        found = [word for word, _count
                 in shapes.learn_terms(headings, owner={"bill"})]
        self.assertIn("Bill", found)

    def test_a_login_name_inside_a_path_is_not_a_category(self):
        """Measured: every occurrence of one in a real folder of 314
        documents was inside `/Users/<name>/...` and not one was a word."""
        headings = (["/Users/tamtor/Documents/export %d" % n
                     for n in range(6)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(40)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(30)]
                    + ["Steuerbescheid Finanzamt %d" % n for n in range(30)])
        found = [word for word, _count
                 in shapes.learn_terms(headings, owner={"tamtor"})]
        self.assertNotIn("tamtor", found)
        self.assertNotIn("Users", found)

    def test_a_moniker_the_documents_actually_use_keeps_its_chance(self):
        """`sausage` is a perfectly good username and a perfectly good
        thing for a butcher's invoice to say at the top."""
        headings = (["Sausage Factory invoice %d" % n for n in range(6)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(40)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(30)]
                    + ["Steuerbescheid Finanzamt %d" % n for n in range(30)])
        found = [word for word, _count
                 in shapes.learn_terms(headings, owner={"sausage"})]
        self.assertIn("Sausage", found)

    def test_knowing_nobody_changes_nothing(self):
        self.assertEqual(self.terms(owner=set()), self.terms())


class InFilenamesTheNameGetsNoAllowance(unittest.TestCase):
    """A heading is written by the sender; a filename by the owner.

    Nobody names a file after themselves to say what kind of file it is:
    `Tamas_Torok_CV.pdf` is a CV. On a real Downloads folder the owner's
    name was in 26 of 398 document filenames -- under the allowance a
    surname keeps in headings, and proposed as a category because of it.
    """

    def stems(self):
        return (["Tamas_Torok_CV_%d" % n for n in range(26)]
                + ["Payslip_%d" % n for n in range(40)]
                + ["CoverLetter_%d" % n for n in range(10)]
                + ["Contract_%d" % n for n in range(8)]
                + ["misc_%d_%s" % (n, "x" * (n % 5)) for n in range(300)])

    def test_with_the_heading_allowance_it_survives(self):
        found = [w for w, _c in shapes.learn_terms(self.stems(),
                                                   owner={"tamas"})]
        self.assertIn("Tamas", found)

    def test_with_none_it_does_not(self):
        found = [w for w, _c in shapes.learn_terms(self.stems(),
                                                   owner={"tamas"},
                                                   owner_share=0)]
        self.assertNotIn("Tamas", found)
        self.assertIn("Payslip", found)


class AFileNamedAfterItsOwnFormat(unittest.TestCase):

    def record(self, ext, fmt):
        import evidence
        record = evidence.Record("/x")
        record.set("ext", ext, "stat", evidence.CERTAIN)
        record.set("format", fmt, "signature", evidence.CERTAIN)
        return record

    def test_its_own_format_is_taken_out(self):
        import propose
        self.assertEqual(propose._without_own_format(
            "pdf_export", self.record("pdf", "pdf")), "export")

    def test_other_format_names_are_ordinary_words(self):
        """`backup`, `project`, `calendar` and `note` are file formats too,
        and good names for a folder when they are not what the file is."""
        import propose
        for stem in ("Project_plan", "Backup 2019", "Calendar export"):
            self.assertEqual(propose._without_own_format(
                stem, self.record("pdf", "pdf")), stem)


if __name__ == "__main__":
    unittest.main()
