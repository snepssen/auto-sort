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
import urllib.parse

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

    def _read_trashinfo_path(self, trashed_path):
        info = os.path.join(os.path.dirname(os.path.dirname(trashed_path)),
                            "info",
                            os.path.basename(trashed_path) + ".trashinfo")
        with open(info) as handle:
            body = handle.read()
        self.sent.append(info)
        for line in body.splitlines():
            if line.startswith("Path="):
                return line[len("Path="):]
        self.fail("no Path= line in %s" % info)

    @unittest.skipUnless(os.name != "nt" and sys.platform != "darwin",
                         "the .trashinfo note is XDG-only")
    def test_trashinfo_path_is_percent_encoded_for_a_literal_percent_sign(self):
        """A name that already looks percent-encoded must round-trip.

        Real trash readers (verified against KDE's own kio_trash) percent-
        decode the Path field on read. Writing it raw means a name like
        `100%20discount.txt` is misread back as `100 discount.txt` -- a
        path that was never real. Restore would target the wrong place.
        """
        original = self.write("100%20discount.txt")
        where = trash.send(original)
        raw_path = self._read_trashinfo_path(where)
        self.assertEqual(urllib.parse.unquote(raw_path), original)

    @unittest.skipUnless(os.name != "nt" and sys.platform != "darwin",
                         "the .trashinfo note is XDG-only")
    def test_trashinfo_path_matches_kio_trashs_own_encoding(self):
        """Byte-for-byte match against KDE's kio_trash for a tricky name.

        Confirmed on a live SteamOS/KDE desktop: `kioclient5 move` writes
        `caf%C3%A9%20r%C3%A9sum%C3%A9.txt` for `café résumé.txt`. Anything
        else here is a dialect only this project's own reader understands.
        """
        original = self.write("café résumé.txt")
        where = trash.send(original)
        raw_path = self._read_trashinfo_path(where)
        self.assertTrue(raw_path.endswith(
            "caf%C3%A9%20r%C3%A9sum%C3%A9.txt"))


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


class ANameIsWorthMoreThanTheDiskSpace(unittest.TestCase):
    """Binning the readable copy of a file is a loss the bytes do not show."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-names-")
        self.holding = os.path.join(self.dir, "Documents", "Codex")
        self.chosen = os.path.join(self.dir, "Pictures", "Art")
        for folder in (self.holding, self.chosen):
            os.makedirs(folder)

    def write(self, folder, name, body):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(body)
        return path

    def scan(self):
        duplicates.forget_folders()
        return duplicates.scan([self.dir], intake=[],
                               holding=[self.holding], min_size=1)

    def test_a_uuid_scores_near_nothing(self):
        self.assertLess(duplicates.name_information(
            "exec-63512093-74d5-4282-a7fc-159ff1ce12ea"), 0.25)
        self.assertEqual(duplicates.name_information("B04 - Oli"), 1.0)

    def test_the_only_readable_name_is_not_thrown_away(self):
        """Observed on a real disk: it would have kept 138 UUIDs.

        The copy that survives is the one somebody has to find again, so
        the name crosses over before the other copy is binned.
        """
        body = b"png" * 3000
        keeper = self.write(
            self.chosen, "exec-63512093-74d5-4282-a7fc-159ff1ce12ea.png", body)
        self.write(self.holding, "B04 - Oli.png", body)
        group = self.scan()[0]
        self.assertEqual(group.keeper, keeper)
        self.assertEqual(group.rename_to, "B04 - Oli.png")

    def test_with_no_readable_name_anywhere_the_ordinary_rule_applies(self):
        """Nothing to rescue, so nothing is held back.

        The guard exists to save a name, not to protect hex from being
        binned. Two UUIDs are two UUIDs and the spare is still spare.
        """
        body = b"png" * 3000
        self.write(self.chosen,
                   "exec-63512093-74d5-4282-a7fc-159ff1ce12ea.png", body)
        spare = self.write(self.holding,
                           "exec-a1b2c3d4-9f8e-4746-a120-f168529ad20e.png",
                           body)
        group = self.scan()[0]
        self.assertIsNone(group.rename_to)
        self.assertEqual(group.losers, [spare])

    def test_a_readable_keeper_still_wins_normally(self):
        body = b"png" * 3000
        keep = self.write(self.chosen, "Arctic Fox.png", body)
        spare = self.write(self.holding, "B01 - Arctic Fox v3.png", body)
        group = self.scan()[0]
        self.assertEqual(group.keeper, keep)
        self.assertEqual(group.losers, [spare])

    def test_the_name_moves_to_where_the_file_belongs(self):
        """A machine-named folder takes the readable name and keeps it.

        Leaving both copies would be safe and useless: the folder stays
        unreadable and the disk stays full. Moving the name across makes
        binning the spare lossless, which is the only version worth doing.
        """
        for number in range(6):
            self.write(self.chosen, "exec-%08x-74d5-4282-a7fc-159ff1ce12ea.png"
                       % number, b"other%d" % number)
            self.write(self.holding, "B0%d - Track.png" % number,
                       b"other%d" % number)
        body = b"png" * 3000
        keeper = self.write(
            self.chosen, "exec-63512093-74d5-4282-a7fc-159ff1ce12ea.png", body)
        spare = self.write(self.holding, "B04 - Oli.png", body)
        duplicates.forget_folders()
        group = [g for g in self.scan() if g.keeper == keeper][0]
        self.assertEqual(group.rename_to, "B04 - Oli.png")
        self.assertEqual(group.losers, [spare])

    def test_one_readable_name_does_not_redeem_a_folder_of_hex(self):
        """`sneppy.png` among a hundred UUIDs is an accident, not a scheme."""
        for number in range(8):
            self.write(self.chosen, "exec-%08x-74d5-4282-a7fc-159ff1ce12ea.png"
                       % number, b"filler%d" % number)
        duplicates.forget_folders()
        self.assertLess(duplicates.folder_information(self.chosen),
                        duplicates.READABLE)

    def tearDown(self):
        duplicates.forget_folders()
        shutil.rmtree(self.dir, ignore_errors=True)


class TheDesktopAlreadySaidWhichIsTheCopy(unittest.TestCase):
    """`Love (1).wav` beside `Ledger of Love.wav`, both in the same drawer.

    Location cannot separate these and the tool declines coin tosses, so
    without a tie-break two identical files sit there forever. The desktop
    that made the copy wrote which one it was into the name.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-copymark-")
        self.holding = os.path.join(self.dir, "Music", "Unfiled")
        os.makedirs(self.holding)

    def tearDown(self):
        duplicates.forget_folders()
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, body=b"wav" * 4000):
        path = os.path.join(self.holding, name)
        with open(path, "wb") as handle:
            handle.write(body)
        return path

    def scan(self):
        duplicates.forget_folders()
        return duplicates.scan([self.dir], intake=[],
                               holding=[self.holding], min_size=1)

    def test_only_the_bracketed_number_counts(self):
        self.assertTrue(duplicates.copy_marked("Love (1).wav"))
        self.assertTrue(duplicates.copy_marked("report (12).pdf"))
        self.assertFalse(duplicates.copy_marked("Ledger of Love.wav"))
        # A number that is part of the name, not a mark the desktop added.
        self.assertFalse(duplicates.copy_marked("B02 - Arctic Fox II.png"))
        self.assertFalse(duplicates.copy_marked("Symphony No. 5.wav"))

    def test_the_marked_copy_is_the_spare(self):
        keep = self.write("Ledger of Love.wav")
        spare = self.write("Love (1).wav")
        group = self.scan()[0]
        self.assertEqual(group.keeper, keep)
        self.assertEqual(group.losers, [spare])

    def test_two_unmarked_names_are_still_a_coin_toss_and_are_left(self):
        self.write("Ledger of Love.wav")
        self.write("Something About Love.wav")
        group = self.scan()[0]
        self.assertEqual(group.losers, [])
        self.assertEqual(len(group.undecided), 1)
