"""Where a file came from, and what it means when nothing says so.

Verified on a live SteamOS desktop: a real download through the Deck's own
Flatpak browser lands with zero xattrs -- not because the filesystem can't
carry them (`setfattr`/`getfattr` work fine on the same volume) but because
the browser never writes `user.xdg.origin.url`. That gap must not look like
"checked, and this file has no known origin" -- it is "the strongest
evidence this module has was never available on this desktop."
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence                                          # noqa: E402
import provenance                                         # noqa: E402
from identify import Sink                                 # noqa: E402


@unittest.skipUnless(sys.platform.startswith("linux"), "xdg xattr behaviour")
class ADownloadWithNoOriginXattr(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-provenance-")
        self.downloads = os.path.join(self.dir, "Downloads")
        os.makedirs(self.downloads)
        self.path = os.path.join(self.downloads, "report.pdf")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real pdf")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def read(self):
        record = evidence.Record(self.path)
        provenance.read(self.path, Sink(record, ""))
        return record

    def test_missing_xattr_on_a_file_in_downloads_leaves_a_note(self):
        try:
            os.listxattr(self.path)
        except OSError:
            self.skipTest("this filesystem does not support xattrs at all")
        record = self.read()
        self.assertEqual(record.value("origin"), "download")
        self.assertEqual(record.confidence("origin"), evidence.WEAK)
        self.assertTrue(any("xdg.origin.url" in note for note in record.notes),
                        record.notes)

    def test_a_real_origin_xattr_needs_no_note(self):
        try:
            os.setxattr(self.path, "user.xdg.origin.url",
                       b"https://example.com/report.pdf")
        except OSError:
            self.skipTest("this filesystem does not support xattrs at all")
        record = self.read()
        self.assertEqual(record.value("from_url"),
                         "https://example.com/report.pdf")
        self.assertEqual(record.notes, [])


if __name__ == "__main__":
    unittest.main()
