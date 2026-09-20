"""Scanned pages, and the many things that look like one but are not.

The interesting tests here are the negative ones. Deciding that something is
a scan is easy; the whole difficulty is declining to say it about the far
larger pile of images that merely share one property with a scan. Digital art
is page-shaped, exported artwork carries print resolutions, and a photograph
of a document is neither. Each of those has a case below.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence                                          # noqa: E402
import fixtures                                          # noqa: E402
import identify                                          # noqa: E402
from readers import scans                                # noqa: E402

A4_AT_300 = (2480, 3508)
LETTER_AT_300 = (2550, 3300)


class ScanDetection(unittest.TestCase):

    def detect(self, width=A4_AT_300[0], height=A4_AT_300[1], **found):
        record = evidence.Record("/tmp/example.jpg")
        found.setdefault("x_resolution", 300)
        found.setdefault("y_resolution", found["x_resolution"])
        found.setdefault("resolution_unit", 2)
        scans.detect(found, width, height, record)
        return record

    # -- things that are scans ------------------------------------------

    def test_a_flatbed_names_itself_in_the_model_tag(self):
        record = self.detect(make="EPSON", model="Perfection V600 Photo")
        self.assertEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("scan_of"), "page")
        self.assertEqual(record.value("paper"), "A4")
        self.assertEqual(record.value("scan_dpi"), 300)

    def test_scanning_software_is_enough_on_its_own(self):
        record = self.detect(software="EPSON Scan")
        self.assertEqual(record.value("capture"), "scan")

    def test_a_page_shaped_image_at_a_scan_resolution_is_a_scan(self):
        """No make, no model, no software: the shape has to carry it."""
        record = self.detect()
        self.assertEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("paper"), "A4")
        self.assertLess(record.fact("capture").confidence, evidence.STRONG)

    def test_letter_is_recognised_as_well_as_a4(self):
        record = self.detect(*LETTER_AT_300)
        self.assertEqual(record.value("paper"), "Letter")

    def test_a_page_scanned_sideways_is_still_a_page(self):
        record = self.detect(A4_AT_300[1], A4_AT_300[0])
        self.assertEqual(record.value("paper"), "A4")

    def test_resolution_written_per_centimetre_is_converted(self):
        record = self.detect(x_resolution=118.11, y_resolution=118.11,
                             resolution_unit=3)
        self.assertEqual(record.value("scan_dpi"), 300)
        self.assertEqual(record.value("paper"), "A4")

    def test_a_named_scanner_may_report_a_photographic_print(self):
        record = self.detect(1200, 1800, make="FUJITSU",
                             model="ScanSnap iX500")
        self.assertEqual(record.value("scan_of"), "print")
        self.assertEqual(record.value("paper"), "4x6")

    def test_a_named_scanner_at_an_odd_size_is_still_a_page(self):
        record = self.detect(1000, 1400, model="CanoScan LiDE 220")
        self.assertEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("scan_of"), "page")

    # -- things that are not --------------------------------------------

    def test_a_camera_is_never_a_scan(self):
        """Exposure tags settle it, whatever the shape works out to."""
        record = self.detect(make="EPSON", model="Perfection V600",
                             exposure_time=0.004, aperture=2.8, iso=400)
        self.assertIsNone(record.value("capture"))

    def test_seventy_two_dpi_is_not_a_scan_resolution(self):
        """The default every editor and web exporter writes."""
        record = self.detect(1200, 1697, x_resolution=72, y_resolution=72)
        self.assertIsNone(record.value("capture"))

    def test_artwork_that_happens_to_be_page_shaped_is_not_a_scan(self):
        """Root-two is a pleasant aspect to crop to, and art is cropped to it.

        This is a real false positive, not a hypothetical: a sweep of one
        library flagged six pieces of digital art as A4 on shape alone.
        """
        record = self.detect(1200, 849, x_resolution=72, y_resolution=72,
                             software="Adobe Photoshop CC (Windows)")
        self.assertIsNone(record.value("capture"))

    def test_an_editor_exporting_at_print_resolution_is_not_a_scan(self):
        record = self.detect(software="Adobe Photoshop CC (Windows)")
        self.assertIsNone(record.value("capture"))

    def test_an_editor_is_believed_when_a_scanner_is_also_named(self):
        """Scanned, then retouched, and saved with both tags intact."""
        record = self.detect(make="EPSON", model="Perfection V600",
                             software="Adobe Photoshop CC (Windows)")
        self.assertEqual(record.value("capture"), "scan")

    def test_a_three_by_two_image_is_not_a_print_on_shape_alone(self):
        """4x6 is 3:2, which is simply what most cameras produce."""
        record = self.detect(1200, 1800)
        self.assertIsNone(record.value("capture"))

    def test_axes_that_disagree_describe_no_real_sheet(self):
        record = self.detect(x_resolution=300, y_resolution=600)
        self.assertIsNone(record.value("capture"))

    def test_a_resolution_with_no_unit_means_nothing(self):
        record = self.detect(resolution_unit=1)
        self.assertIsNone(record.value("capture"))

    def test_an_image_with_no_resolution_at_all_is_left_alone(self):
        record = evidence.Record("/tmp/example.jpg")
        scans.detect({}, 2480, 3508, record)
        self.assertIsNone(record.value("capture"))


class ThroughTheWholeLadder(unittest.TestCase):
    """The same thing again, but from a file on disk via `identify`."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-scans-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def identify(self, name, **kwargs):
        path = os.path.join(self.dir, name)
        fixtures.jpeg(path, **kwargs)
        return identify.identify(path)

    def test_a_scanned_invoice_is_identified_as_a_scan(self):
        record = self.identify(
            "scan0001.jpg", width=2480, height=3508, make="EPSON",
            model="Perfection V600 Photo", software="EPSON Scan", dpi=300)
        self.assertEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("paper"), "A4")
        self.assertEqual(record.value("kind"), "image")

    def test_a_photograph_from_a_camera_is_not(self):
        record = self.identify("IMG_0042.jpg", width=4032, height=3024,
                               make="Apple", model="iPhone 13 mini")
        self.assertNotEqual(record.value("capture"), "scan")
        self.assertEqual(record.value("camera"), "iPhone 13 mini")

    def test_a_scanner_is_not_filed_as_a_camera(self):
        """The collision that makes this whole module worth having.

        A scanner writes Make and Model exactly as a camera does, so a rule
        gathering photographs `by the body that took them` -- the obvious
        way to write that rule, and the way auto-sort proposes it -- will
        gather scanned tax returns into a folder named after an Epson and
        file them in Pictures.
        """
        record = self.identify(
            "Steuerbescheid.jpg", width=2480, height=3508, make="EPSON",
            model="Perfection V600 Photo", software="EPSON Scan", dpi=300)
        self.assertIsNone(record.value("camera"))
        self.assertIsNone(record.value("camera_make"))
        # The bare Model, because that is what the image reader stores; the
        # point of the test is which *fact* it lands under, not its spelling.
        self.assertEqual(record.value("scanner"), "Perfection V600 Photo")

    def test_the_scanner_is_still_recorded_somewhere(self):
        record = self.identify("Rechnung.jpg", width=2550, height=3300,
                               make="Hewlett-Packard", model="HP ScanJet 5590",
                               dpi=300)
        self.assertEqual(record.value("paper"), "Letter")
        self.assertIn("ScanJet", record.value("scanner"))

    def test_naming_the_scanner_does_not_conflict_with_itself(self):
        """The `camera` fix, on the fact `device` becomes for a scanner.

        Both facts come out of the same loop in readers/image.py, so a
        second STRONG write that prepended the Make raised a Conflict here
        exactly as it did on photographs -- on every scanned page, where the
        user is even more likely to go looking at `explain` to find out why
        their tax return was filed where it was.
        """
        record = self.identify(
            "Steuerbescheid.jpg", width=2480, height=3508, make="EPSON",
            model="Perfection V600 Photo", software="EPSON Scan", dpi=300)
        self.assertEqual(record.value("scanner"), "Perfection V600 Photo")
        self.assertEqual(record.value("scanner_make"), "EPSON")
        self.assertEqual([str(c) for c in record.conflicts], [])

    def test_a_screenshot_is_still_a_screenshot(self):
        record = self.identify("Screenshot.jpg", width=2880, height=1800,
                               make=None, model=None, taken=None)
        self.assertEqual(record.value("capture"), "screenshot")


if __name__ == "__main__":
    unittest.main()
