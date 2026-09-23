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
        found = self.terms(owner={"tamás", "török"})
        self.assertNotIn("Tamás", found)
        self.assertIn("Rechnung", found)
        self.assertIn("Loonbrief", found)

    def test_an_accented_name_still_matches_its_own_word(self):
        """`Tamás` folds to `tamas` in the counting and must fold on both
        sides of the comparison, or the name never matches itself."""
        self.assertNotIn("Tamás", self.terms(owner={"Tamás"}))

    def test_a_surname_that_is_also_a_word_keeps_its_chance(self):
        """`Koch` is a cook, `Baker` is a baker, `Bill` is a bill. Below
        the lower ceiling it is the language, not the letterhead."""
        headings = (["Bill from the garage %d" % n for n in range(4)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(40)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(30)]
                    + ["Steuerbescheid Finanzamt %d" % n for n in range(30)])
        found = [word for word, _count
                 in shapes.learn_terms(headings, owner={"bill"})]
        self.assertIn("Bill", found)

    def test_a_login_name_never_gets_that_chance(self):
        """It is not a word in any language. Somebody typed it once."""
        headings = (["tamtor export %d" % n for n in range(4)]
                    + ["Rechnung Stadtwerke %d" % n for n in range(40)]
                    + ["Mietvertrag Wohnung %d" % n for n in range(30)]
                    + ["Steuerbescheid Finanzamt %d" % n for n in range(30)])
        found = [word for word, _count
                 in shapes.learn_terms(headings, never={"tamtor"})]
        self.assertNotIn("tamtor", found)

    def test_knowing_nobody_changes_nothing(self):
        self.assertEqual(self.terms(owner=set(), never=set()), self.terms())


if __name__ == "__main__":
    unittest.main()
