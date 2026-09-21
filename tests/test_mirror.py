"""The second copy, and the disk that is usually not there.

Every test here is really about one promise: sorting never waits for a
backup and never fails because of one. The drive being asleep, unplugged,
full or in a drawer is the ordinary case for the people this is for, so it
is what most of these describe.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger                                            # noqa: E402
import mirror                                            # noqa: E402


class WhereACopyGoes(unittest.TestCase):

    def test_it_shadows_the_home_folder(self):
        """A backup nobody can read is a backup nobody restores from."""
        home = os.path.expanduser("~")
        self.assertEqual(
            mirror.relative_for(os.path.join(home, "Documents", "R", "x.pdf")),
            os.path.join("Documents", "R", "x.pdf"))

    def test_anything_outside_home_keeps_its_own_shape(self):
        self.assertTrue(mirror.relative_for("/Volumes/Old/scan.pdf")
                        .startswith("Elsewhere"))


class IsTheDiskThere(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-mirror-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_folder_that_does_not_exist_is_not_available(self):
        self.assertFalse(mirror.available(os.path.join(self.dir, "nope")))

    def test_availability_is_decided_by_writing_not_by_looking(self):
        """An empty mount point is still a directory.

        When a disk goes away its mount point usually remains, as an empty
        folder on the machine's own disk. Copying a backup into that fills
        the boot drive with a copy of itself, which is the opposite of a
        backup, so the question is asked by writing a file.
        """
        read_only = os.path.join(self.dir, "readonly")
        os.makedirs(read_only)
        os.chmod(read_only, 0o500)
        try:
            self.assertFalse(mirror.available(read_only))
        finally:
            os.chmod(read_only, 0o700)

    def test_nothing_is_left_behind_by_the_check(self):
        mirror.available(self.dir)
        self.assertEqual(os.listdir(self.dir), [])


class CopyingOne(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-copy-")
        self.root = os.path.join(self.dir, "disk")
        os.makedirs(self.root)
        self.source = os.path.join(self.dir, "x.pdf")
        with open(self.source, "wb") as handle:
            handle.write(b"paperwork" * 500)
        import hashlib
        self.digest = hashlib.sha256(b"paperwork" * 500).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_copy_arrives_with_the_right_bytes(self):
        where = mirror.copy_one(self.source, self.root, "Documents/x.pdf",
                                self.digest)
        self.assertTrue(os.path.isfile(where))
        with open(where, "rb") as handle:
            self.assertEqual(handle.read(), b"paperwork" * 500)

    def test_a_missing_disk_is_a_reason_to_wait_not_an_error(self):
        with self.assertRaises(mirror.Unavailable):
            mirror.copy_one(self.source, os.path.join(self.dir, "gone"),
                            "Documents/x.pdf", self.digest)

    def test_a_copy_that_does_not_match_is_not_given_the_real_name(self):
        """A part-file is better than a half file wearing a whole name."""
        with self.assertRaises(IOError):
            mirror.copy_one(self.source, self.root, "Documents/x.pdf",
                            "0" * 64)
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "Documents", "x.pdf")))
        self.assertEqual(
            [n for n in os.listdir(os.path.join(self.root, "Documents"))
             if n.endswith(".auto-sort-part")], [])


class TheQueue(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-queue-")
        self.root = os.path.join(self.dir, "disk")
        os.makedirs(self.root)
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.files = []
        for number in range(3):
            path = os.path.join(self.dir, "f%d.txt" % number)
            with open(path, "wb") as handle:
                handle.write(b"x" * (100 + number))
            self.files.append(path)
            self.journal.queue_mirror(None, path, "Documents/f%d.txt" % number,
                                      100 + number, "")

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_queue_empties_when_the_disk_is_there(self):
        copied, waiting, skipped = mirror.drain(self.journal, self.root)
        self.assertEqual((copied, waiting, skipped), (3, 0, 0))

    def test_a_missing_disk_leaves_the_queue_exactly_as_it_was(self):
        """Three weeks in a drawer is three weeks of queue and nothing else."""
        copied, waiting, _skipped = mirror.drain(
            self.journal, os.path.join(self.dir, "gone"))
        self.assertEqual(copied, 0)
        self.assertEqual(waiting, 3)
        self.assertEqual(len(self.journal.pending_mirror()), 3)

    def test_draining_twice_copies_nothing_twice(self):
        mirror.drain(self.journal, self.root)
        copied, waiting, _s = mirror.drain(self.journal, self.root)
        self.assertEqual((copied, waiting), (0, 0))

    def test_a_file_that_has_since_gone_is_set_aside_not_retried_forever(self):
        os.remove(self.files[1])
        copied, waiting, skipped = mirror.drain(self.journal, self.root)
        self.assertEqual((copied, skipped), (2, 1))
        self.assertEqual(waiting, 0)

    def test_queueing_the_same_bytes_at_the_same_place_is_free(self):
        before = len(self.journal.pending_mirror())
        self.journal.queue_mirror(None, self.files[0], "Documents/f0.txt",
                                  100, "")
        self.assertEqual(len(self.journal.pending_mirror()), before)


if __name__ == "__main__":
    unittest.main()


class TheBinIsNotBackedUp(unittest.TestCase):
    """auto-sort puts duplicates in the bin; a backup must not fetch them out.

    Found by running it: the first real drain cheerfully preserved
    `.Trash/What the Storm Keeps.md`. A backup that resurrects what somebody
    threw away undoes the tidying that made them trust the program.
    """

    def test_a_file_in_the_bin_gets_no_place_in_the_mirror(self):
        home = os.path.expanduser("~")
        self.assertIsNone(mirror.relative_for(
            os.path.join(home, ".Trash", "old.png")))

    def test_every_platforms_bin(self):
        for bin_name in (".Trash", ".Trashes", "Trash-1000",
                         "Trash (auto-sort)", "$RECYCLE.BIN"):
            self.assertTrue(mirror.in_a_bin(os.path.join("/x", bin_name, "f")),
                            bin_name)

    def test_an_ordinary_file_is_not_in_a_bin(self):
        self.assertFalse(mirror.in_a_bin("/Users/x/Documents/Trashy Novel.pdf"))
