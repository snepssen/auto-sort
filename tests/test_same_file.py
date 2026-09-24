"""Two paths, one file.

A Mac's disk ignores case, so `LOONBRIEF` and `Loonbrief` are one folder.
Nine payslips already in it were planned to move into it, where the
collision check would have found each one in its own way.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths                                             # noqa: E402


class OneFileTwoNames(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-same-")
        self.folder = os.path.join(self.dir, "LOONBRIEF")
        os.makedirs(self.folder)
        self.file = os.path.join(self.folder, "slip.pdf")
        with open(self.file, "w") as handle:
            handle.write("x")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_same_path_is_the_same_file(self):
        self.assertTrue(paths.same_file(self.file, self.file))

    def test_a_folder_reached_another_way_is_the_same_file(self):
        other = os.path.join(self.dir, "Loonbrief-link")
        os.symlink(self.folder, other)
        self.assertTrue(paths.same_file(
            self.file, os.path.join(other, "slip.pdf")))

    def test_a_different_case_on_a_disk_that_ignores_it(self):
        spelled = os.path.join(self.dir, "Loonbrief", "slip.pdf")
        if not os.path.exists(spelled):
            self.skipTest("this disk tells case apart")
        self.assertTrue(paths.same_file(self.file, spelled))

    def test_a_file_not_there_yet_is_not_the_same(self):
        self.assertFalse(paths.same_file(
            self.file, os.path.join(self.dir, "elsewhere", "slip.pdf")))

    def test_two_files_with_one_name_are_two_files(self):
        twin = os.path.join(self.dir, "slip.pdf")
        with open(twin, "w") as handle:
            handle.write("x")
        self.assertFalse(paths.same_file(self.file, twin))


if __name__ == "__main__":
    unittest.main()
