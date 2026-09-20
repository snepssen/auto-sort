"""A folder is filed by what is in it, not by the fact that it is a folder.

Every other item in this program is classified by reading it. Directories
were the exception -- routed on `is_dir` alone -- and on a real Downloads
folder that put a video project, a hundred and sixty-six pieces of artwork
and a folder of screenshots into the documents cabinet, because "it is a
folder" answers what kind of thing it is and says nothing about where it
belongs.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundles                                           # noqa: E402
import evidence                                          # noqa: E402
import fixtures                                          # noqa: E402
import folders                                           # noqa: E402
import identify                                          # noqa: E402


class Surveying(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def make(self, name):
        path = os.path.join(self.directory, name)
        os.makedirs(path, exist_ok=True)
        return path

    def fill(self, path, **counts):
        makers = {"png": fixtures.png, "jpeg": fixtures.jpeg,
                  "mp3": fixtures.mp3, "pdf": fixtures.pdf,
                  "mp4": fixtures.mp4}
        for extension, count in counts.items():
            for index in range(count):
                makers[extension](os.path.join(
                    path, "file%d.%s" % (index, extension)))
        return path

    def test_a_folder_of_pictures_contains_images(self):
        found = folders.survey(self.fill(self.make("art"), png=6))
        self.assertEqual(found.dominant[0], "image")
        self.assertEqual(found.dominant[1], 1.0)
        self.assertEqual(found.files, 6)

    def test_bytes_decide_rather_than_file_counts(self):
        # A real folder held twenty-three clips beside eighty-three
        # thumbnails and is plainly a video folder. Counting files calls it
        # something else.
        path = self.make("memes")
        fixtures.mp4(os.path.join(path, "big.mp4"), seconds=600)
        with open(os.path.join(path, "big.mp4"), "ab") as handle:
            handle.write(b"\0" * 400000)
        for index in range(20):
            fixtures.png(os.path.join(path, "thumb%d.png" % index))
        found = folders.survey(path)
        self.assertEqual(found.dominant[0], "video")
        self.assertEqual(found.dominant_by_count, "image")

    def test_nothing_is_claimed_when_nothing_dominates(self):
        # Balanced by *size*, not by file count: the two are different
        # questions and this module answers the first one.
        path = self.fill(self.make("mixed"), mp3=6)
        for index in range(6):
            document = os.path.join(path, "doc%d.pdf" % index)
            fixtures.pdf(document)
            with open(document, "ab") as handle:
                handle.write(b"%% padding\n" * 500)
        record = evidence.Record(path)
        folders.read(path, record)
        self.assertTrue(record.value("contains_mixed"))
        self.assertFalse(record.has("contains"),
                         "a rule keyed on `contains` must decline here")

    def test_confidence_follows_how_dominant_the_kind_is(self):
        strong = self.fill(self.make("all-art"), png=10)
        record = evidence.Record(strong)
        folders.read(strong, record)
        self.assertEqual(record.value("contains"), "image")
        self.assertGreaterEqual(record.confidence("contains"), evidence.STRONG)

    def test_an_empty_folder_says_so(self):
        path = self.make("nothing")
        record = evidence.Record(path)
        folders.read(path, record)
        self.assertTrue(record.value("empty"))
        self.assertEqual(record.value("contains_files"), 0)

    def test_hidden_files_and_os_litter_do_not_count(self):
        path = self.fill(self.make("clean"), png=3)
        for name in (".DS_Store", ".hidden", "Thumbs.db"):
            with open(os.path.join(path, name), "wb") as handle:
                handle.write(b"\0" * 50000)
        found = folders.survey(path)
        self.assertEqual(found.files, 3)
        self.assertEqual(found.dominant[0], "image")

    def test_a_package_inside_is_not_unpacked(self):
        # An .app would otherwise drown the folder in its own resources.
        path = self.fill(self.make("with-app"), pdf=4)
        inside = os.path.join(path, "Thing.app", "Contents", "Resources")
        os.makedirs(inside)
        for index in range(40):
            fixtures.png(os.path.join(inside, "res%d.png" % index))
        found = folders.survey(path)
        self.assertEqual(found.dominant[0], "document")
        self.assertEqual(found.files, 4)

    def test_the_walk_is_bounded(self):
        path = self.make("many")
        for index in range(30):
            fixtures.png(os.path.join(path, "f%d.png" % index))
        found = folders.survey(path, max_files=10)
        self.assertTrue(found.truncated)
        self.assertEqual(found.files, 10)

    def test_a_folder_that_cannot_be_read_does_not_raise(self):
        record = evidence.Record("/nowhere/at/all")
        folders.read("/nowhere/at/all", record)
        self.assertTrue(record.value("empty"))


class ThroughIdentify(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_a_directory_given_by_path_is_recognised_as_one(self):
        # `Item` used to default `is_dir` to False, so anything built from a
        # bare path -- explain, and every caller outside the scanner --
        # claimed a directory was a file and none of this ever ran.
        path = os.path.join(self.directory, "clips")
        os.makedirs(path)
        fixtures.mp4(os.path.join(path, "a.mp4"))
        self.assertTrue(bundles.Item(path).is_dir)
        record = identify.identify(path)
        self.assertEqual(record.value("kind"), "folder")
        self.assertEqual(record.value("contains"), "video")

    def test_a_package_is_not_surveyed(self):
        path = os.path.join(self.directory, "Thing.app")
        os.makedirs(os.path.join(path, "Contents"))
        fixtures.png(os.path.join(path, "Contents", "icon.png"))
        record = identify.identify(path)
        self.assertTrue(record.value("is_package"))
        self.assertFalse(record.has("contains"),
                         "a package is one document, not a folder of things")

    def test_a_video_folder_can_be_told_from_a_document_folder(self):
        video = os.path.join(self.directory, "project")
        papers = os.path.join(self.directory, "paperwork")
        os.makedirs(video)
        os.makedirs(papers)
        for index in range(4):
            fixtures.mp4(os.path.join(video, "clip%d.mp4" % index))
            fixtures.pdf(os.path.join(papers, "doc%d.pdf" % index))
        self.assertEqual(identify.identify(video).value("contains"), "video")
        self.assertEqual(identify.identify(papers).value("contains"),
                         "document")


if __name__ == "__main__":
    unittest.main(verbosity=2)
