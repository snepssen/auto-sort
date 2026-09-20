"""The bin, and the scan that decides what goes in it.

auto-sort does not delete. The bin is the one place a file can go that looks
like losing it and is not: the file is still there, the person already knows
how to open it, and the move is in the ledger so `undo` reaches it without
them opening anything. Every test here is really about that promise.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duplicates                                        # noqa: E402
import trash                                             # noqa: E402


class Bin(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-bin-")
        self.sent = []

    def tearDown(self):
        for path in self.sent:
            if os.path.exists(path):
                os.remove(path)
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, body=b"contents"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(body)
        return path

    def test_the_file_still_exists_afterwards(self):
        path = self.write("autosort-test-one.txt")
        where = trash.send(path)
        self.sent.append(where)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.isfile(where))

    def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(self):
        """Bins collect repeats; this one must not eat its own contents."""
        first = trash.send(self.write("autosort-test-two.txt", b"first"))
        self.sent.append(first)
        second = trash.send(self.write("autosort-test-two.txt", b"second"))
        self.sent.append(second)
        self.assertNotEqual(first, second)
        with open(first, "rb") as handle:
            self.assertEqual(handle.read(), b"first")

    def test_a_missing_file_raises_rather_than_passing_quietly(self):
        with self.assertRaises(trash.TrashError):
            trash.send(os.path.join(self.dir, "not-here.txt"))

    def test_a_folder_is_refused(self):
        with self.assertRaises(trash.TrashError):
            trash.send(self.dir)

    def test_a_bin_is_chosen_for_a_file_not_written_yet(self):
        """`folder_for` is asked before the file exists, and used to answer
        with the root volume's bin because it could not stat the path."""
        home = os.path.expanduser("~")
        self.assertTrue(
            trash.folder_for(os.path.join(home, "Music", "nothing.wav"))
            .startswith(home))


class WhichCopyIsReal(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-dupes-")
        self.intake = os.path.join(self.dir, "Downloads")
        self.holding = os.path.join(self.dir, "Music", "Unfiled")
        self.chosen = os.path.join(self.dir, "Music", "Album")
        for folder in (self.intake, self.holding, self.chosen):
            os.makedirs(folder)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, folder, name, body):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(body)
        return path

    def scan(self):
        return duplicates.scan([self.dir], intake=[self.intake],
                               holding=[self.holding], min_size=1)

    def test_a_copy_in_the_funnel_loses_to_one_in_a_real_folder(self):
        body = b"x" * 5000
        spare = self.write(self.intake, "song.wav", body)
        keep = self.write(self.chosen, "song.wav", body)
        groups = self.scan()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].keeper, keep)
        self.assertEqual(groups[0].losers, [spare])

    def test_a_copy_in_a_holding_folder_loses_too(self):
        body = b"y" * 5000
        spare = self.write(self.holding, "song.wav", body)
        self.write(self.chosen, "song.wav", body)
        self.assertEqual(self.scan()[0].losers, [spare])

    def test_two_copies_in_chosen_folders_are_left_alone(self):
        """Two deliberate copies are somebody's filing, not a mistake."""
        body = b"z" * 5000
        other = os.path.join(self.dir, "Music", "Other")
        os.makedirs(other)
        self.write(self.chosen, "song.wav", body)
        self.write(other, "song.wav", body)
        group = self.scan()[0]
        self.assertEqual(group.losers, [])
        self.assertEqual(len(group.undecided), 1)

    def test_different_names_are_still_the_same_file(self):
        """The case an eye misses and the reason this is worth having."""
        body = b"w" * 5000
        spare = self.write(self.holding, "Love (1).wav", body)
        keep = self.write(self.chosen, "Ledger of Love.wav", body)
        group = self.scan()[0]
        self.assertEqual(group.keeper, keep)
        self.assertEqual(group.losers, [spare])

    def test_the_same_name_is_not_the_same_file(self):
        """Two takes that share a name are two takes."""
        self.write(self.holding, "Unlucky.wav", b"take one" + b"a" * 5000)
        self.write(self.chosen, "Unlucky.wav", b"take two" + b"b" * 5000)
        self.assertEqual(self.scan(), [])

    def test_a_package_is_not_walked_into(self):
        """A Photos library keeps identical thumbnails and is entitled to.

        Reading inside one buries the real answer under hundreds of false
        ones, and reads somebody's photographs to do it.
        """
        library = os.path.join(self.dir, "Pictures", "P.photoslibrary", "d")
        os.makedirs(library)
        body = b"thumb" * 1000
        self.write(library, "a.jpeg", body)
        self.write(library, "b.jpeg", body)
        self.assertEqual(self.scan(), [])


if __name__ == "__main__":
    unittest.main()
