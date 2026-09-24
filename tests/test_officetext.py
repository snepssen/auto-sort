"""Reading what a Word, OpenDocument, RTF or Markdown file says.

Only PDFs had their words read; a tenancy agreement in `.docx` was filed by
its name and nothing else. The documents here are built by the test --
a fixture of somebody's post is somebody's post.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import identify                                          # noqa: E402
from readers import officetext                           # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(path, paragraphs, styles=""):
    body = "".join(
        '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (
            '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else "",
            '<w:rPr><w:sz w:val="%d"/></w:rPr>' % size if size else "", text)
        for text, style, size in paragraphs)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml",
                         '<w:document %s><w:body>%s</w:body></w:document>'
                         % (W, body))
        archive.writestr("word/styles.xml", '<w:styles %s>'
                         '<w:docDefaults><w:rPrDefault><w:rPr>'
                         '<w:sz w:val="22"/></w:rPr></w:rPrDefault>'
                         '</w:docDefaults>%s</w:styles>' % (W, styles))


class Word(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-office-")
        self.path = os.path.join(self.dir, "Tenancy.docx")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_title_is_what_is_set_larger(self):
        """By a style's size, whatever the style is called in any language."""
        _docx(self.path, [
            ("Model Private Residential Tenancy Agreement", "Kop1", None),
            ("This agreement is made between the landlord and the tenant "
             "for the let property described below " * 3, None, None)],
            styles='<w:style w:type="paragraph" w:styleId="Kop1">'
                   '<w:rPr><w:sz w:val="36"/></w:rPr></w:style>')
        record = identify.identify(self.path, tier=identify.TIER_HEADER)
        self.assertEqual(record.value("title_drawn"),
                         "Model Private Residential Tenancy Agreement")
        self.assertTrue(record.value("heading").startswith("Model Private"))
        self.assertGreater(record.value("words_read"), 20)

    def test_a_style_based_on_another_takes_its_size(self):
        _docx(self.path, [("Rechnung Nummer vier", "Mine", None),
                          ("Text " * 40, None, None)],
              styles='<w:style w:type="paragraph" w:styleId="Big">'
                     '<w:rPr><w:sz w:val="40"/></w:rPr></w:style>'
                     '<w:style w:type="paragraph" w:styleId="Mine">'
                     '<w:basedOn w:val="Big"/></w:style>')
        _text, runs = officetext.read(self.path, "word")
        self.assertEqual(runs[0][2], 20.0)

    def test_nothing_larger_means_the_top_of_the_page(self):
        _docx(self.path, [("Dear landlord, about the flat " * 5, None, None)])
        record = identify.identify(self.path, tier=identify.TIER_HEADER)
        self.assertFalse(record.has("title_drawn"))
        self.assertTrue(record.value("heading").startswith("Dear landlord"))

    def test_a_damaged_file_is_just_unread(self):
        with open(self.path, "wb") as handle:
            handle.write(b"PK\x03\x04 not really a zip")
        self.assertEqual(officetext.read(self.path, "word"), ("", []))


class OpenDocument(unittest.TestCase):

    def test_a_heading_set_larger(self):
        folder = tempfile.mkdtemp(prefix="autosort-odt-")
        path = os.path.join(folder, "Brief.odt")
        text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        style = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
        fo = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
        content = (
            '<office:document-content xmlns:office="urn:oasis:names:tc:'
            'opendocument:xmlns:office:1.0" xmlns:text="%s" '
            'xmlns:style="%s" xmlns:fo="%s"><office:automatic-styles>'
            '<style:style style:name="Big"><style:text-properties '
            'fo:font-size="20pt"/></style:style><style:style style:name="Body">'
            '<style:text-properties fo:font-size="11pt"/></style:style>'
            '</office:automatic-styles><office:body><office:text>'
            '<text:h text:style-name="Big">Kündigung der Wohnung</text:h>'
            '<text:p text:style-name="Body">%s</text:p>'
            '</office:text></office:body></office:document-content>'
            % (text, style, fo, "Sehr geehrte Damen und Herren " * 10))
        try:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("mimetype",
                                 "application/vnd.oasis.opendocument.text")
                archive.writestr("content.xml", content)
            record = identify.identify(path, tier=identify.TIER_HEADER)
            self.assertEqual(record.value("title_drawn"),
                             "Kündigung der Wohnung")
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class RichText(unittest.TestCase):

    def test_sizes_and_skipped_groups(self):
        data = (rb"{\rtf1\ansi{\fonttbl{\f0 Times New Roman;}}"
                rb"{\info{\title ignored}}\f0\fs40 Mietvertrag\par"
                rb"\fs22 Zwischen dem Vermieter und dem Mieter wird "
                rb"folgender Vertrag geschlossen \'fcber die Wohnung\par}")
        text, runs = officetext._rtf(data)
        self.assertNotIn("Times New Roman", text)
        self.assertNotIn("ignored", text)
        self.assertIn("über die Wohnung", text)
        from readers import pdftext
        self.assertEqual(pdftext.title(runs), "Mietvertrag")


class Markdown(unittest.TestCase):

    def test_the_first_hash_line_is_the_title(self):
        text, runs = officetext._markdown(
            b"Some preamble\n\n# Meeting notes, June\n\nWe talked about "
            + b"the thing " * 30)
        self.assertEqual(officetext.known_title(runs), "Meeting notes, June")
        self.assertTrue(text.startswith("Some preamble"))


class Word97(unittest.TestCase):
    """A `.doc` from before 2007: a compound file and a piece table."""

    def test_a_real_one_reads(self):
        import shutil as _shutil
        import subprocess
        tool = _shutil.which("textutil")
        if not tool:
            self.skipTest("textutil (macOS) writes the fixture")
        folder = tempfile.mkdtemp(prefix="autosort-doc-")
        try:
            source = os.path.join(folder, "in.txt")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("Mietvertrag für die Wohnung\n\nZwischen dem "
                             "Vermieter und dem Mieter wird folgender "
                             "Vertrag geschlossen.\n")
            target = os.path.join(folder, "Mietvertrag.doc")
            subprocess.run([tool, "-convert", "doc", source, "-output",
                            target], check=True, capture_output=True)
            record = identify.identify(target, tier=identify.TIER_HEADER)
            self.assertTrue(record.value("heading").startswith(
                "Mietvertrag für die Wohnung"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_field_instructions_are_not_text(self):
        from readers import worddoc
        self.assertEqual(
            worddoc._clean("Seite \x13 PAGE \\* MERGEFORMAT \x141\x15 von 3"),
            "Seite 1 von 3")

    def test_something_that_only_looks_like_one_is_just_unread(self):
        from readers import worddoc
        self.assertEqual(worddoc.text(worddoc.MAGIC + os.urandom(3000)), "")
        self.assertEqual(worddoc.text(b"not a compound file"), "")



class NewerPages(unittest.TestCase):
    """The body of a newer Pages document is a UTF-8 string inside
    Snappy-compressed protobuf."""

    def test_snappy_literals_and_copies(self):
        # "abc" as a literal, then six bytes copied from three back.
        stream = bytes([9, (3 - 1) << 2]) + b"abc" + bytes([0x09, 3])
        self.assertEqual(officetext._unsnappy(stream, 100), b"abcabcabc")

    def test_a_copy_from_before_the_start_is_refused(self):
        stream = bytes([6, 0x09, 3])
        with self.assertRaises(ValueError):
            officetext._unsnappy(stream, 100)

    def test_it_will_not_grow_past_its_limit(self):
        with self.assertRaises(ValueError):
            officetext._unsnappy(bytes([0x80, 0x80, 0x80, 0x10]), 1000)

    def test_the_longest_run_of_text_is_the_body(self):
        body = ("Assessment of Pre-Settled Status Eligibility and what "
                "follows from it for the applicant").encode("utf-8")
        payload = b"\x0a\x02\x08\x01\x12" + bytes([len(body)]) + body \
            + b"\x1a\x03abc"
        literal = bytes([len(payload)]) + bytes([(len(payload) - 1) << 2]) \
            if len(payload) <= 60 else bytes([len(payload), 60 << 2,
                                              len(payload) - 1])
        chunk = literal + payload
        archive = b"\x00" + len(chunk).to_bytes(3, "little") + chunk
        self.assertTrue(officetext._iwa_text(archive).startswith(
            "Assessment of Pre-Settled Status"))



class MailCalendarsAndPages(unittest.TestCase):
    """What they are about is in the subject, the summary and the title."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-mail-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    EMAIL = (b"From: Stadtwerke <rechnung@stadtwerke.example>\r\n"
             b"Subject: Ihre Rechnung Nr. 4711\r\n"
             b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
             b"Sehr geehrte Kundin, anbei Ihre Rechnung f\xc3\xbcr Mai.\r\n")

    def test_an_email_is_titled_by_its_subject(self):
        from readers import pdftext
        text, runs = officetext.read(self.write("a.eml", self.EMAIL), "email")
        self.assertEqual(officetext.known_title(runs), "Ihre Rechnung Nr. 4711")
        self.assertIn("für Mai", text)

    def test_apple_mail_puts_a_byte_count_in_front(self):
        path = self.write("a.emlx", b"%d\n" % len(self.EMAIL) + self.EMAIL)
        text, _runs = officetext.read(path, "email")
        self.assertTrue(text.startswith("Ihre Rechnung Nr. 4711"))

    def test_a_calendar_invitation_is_titled_by_its_summary(self):
        from readers import pdftext
        path = self.write("invite.ics", (
            b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Introduction call\\, "
            b"with the\r\n  agency\r\nLOCATION:Perth\r\nDESCRIPTION:Bring "
            b"your ID\\nand a CV\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"))
        text, runs = officetext.read(path, "calendar")
        self.assertEqual(officetext.known_title(runs),
                         "Introduction call, with the agency")
        self.assertIn("Bring your ID and a CV", text)

    def test_a_saved_page_is_titled_by_its_title(self):
        from readers import pdftext
        path = self.write("receipt.html", (
            b"<html><head><title>Order confirmation &amp; receipt</title>"
            b"<script>var secret = 1;</script><style>p{}</style></head>"
            b"<body><h1>Thanks</h1><p>Your order has shipped.</p></body>"
            b"</html>"))
        text, runs = officetext.read(path, "html")
        self.assertEqual(officetext.known_title(runs),
                         "Order confirmation & receipt")
        self.assertNotIn("secret", text)
        self.assertIn("Your order has shipped", text)



class Spreadsheets(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-sheets-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_workbook_is_its_text_in_order(self):
        path = os.path.join(self.dir, "Statement.xlsx")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml",
                             '<workbook><sheets><sheet name="Mai 2024" '
                             'sheetId="1"/></sheets></workbook>')
            archive.writestr("xl/sharedStrings.xml",
                             "<sst><si><t>Kontoauszug Girokonto</t></si>"
                             "<si><r><t>Buchungs</t></r><r><t>tag</t></r></si>"
                             "<si><t>Betrag &amp; Saldo</t></si></sst>")
        record = identify.identify(path, tier=identify.TIER_HEADER)
        self.assertTrue(record.value("heading").startswith(
            "Kontoauszug Girokonto Buchungstag"))

    def test_a_workbook_with_no_text_is_its_sheet_names(self):
        path = os.path.join(self.dir, "Numbers only.xlsx")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml",
                             '<workbook><sheets><sheet name="Haushaltsbuch"/>'
                             '</sheets></workbook>')
        text, _runs = officetext.read(path, "excel")
        self.assertEqual(text, "Haushaltsbuch")

    def test_a_csv_is_its_first_rows(self):
        path = os.path.join(self.dir, "export.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("Date,Description,Amount,Balance\n"
                         "2024-05-01,Rent,-900.00,1200.00\n")
        record = identify.identify(path, tier=identify.TIER_HEADER)
        self.assertTrue(record.value("heading").startswith(
            "Date,Description,Amount,Balance"))


if __name__ == "__main__":
    unittest.main()
