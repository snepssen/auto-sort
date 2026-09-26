"""A filesystem's listing order must not decide whether companions stay together."""
import itertools
import os
import tempfile
import unittest

import bundles


class BundleOrder(unittest.TestCase):
    def test_companions_keep_their_primary_in_every_order_and_size(self):
        for primary, companion in (("model.obj", "model.mtl"),
                                   ("photo.cr2", "photo.jpg"),
                                   ("model.obj", "model.png")):
            for sizes in ((7, 7), (1, 100), (100, 1)):
                with tempfile.TemporaryDirectory() as folder:
                    for name, size in zip((primary, companion), sizes):
                        with open(os.path.join(folder, name), "wb") as handle:
                            handle.write(b"x" * size)
                    for names in itertools.permutations((primary, companion)):
                        with self.subTest(names=names, sizes=sizes):
                            items = bundles.group(folder, names)
                            self.assertEqual(len(items), 1)
                            self.assertEqual(os.path.basename(items[0].primary), primary)
                            self.assertEqual({os.path.basename(p) for p in items[0].members},
                                             {primary, companion})

    def test_two_rendered_pictures_remain_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("photo.jpg", "photo.png"):
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(b"fixture")
            for names in itertools.permutations(("photo.jpg", "photo.png")):
                items = bundles.group(folder, names)
                self.assertEqual(len(items), 2)
                self.assertTrue(all(len(item.members) == 1 for item in items))

    def test_equal_rank_has_a_stable_primary(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("photo.cr2", "photo.nef"):
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(b"fixture")
            results = [bundles.group(folder, names)[0].primary for names in
                       itertools.permutations(("photo.cr2", "photo.nef"))]
            self.assertEqual(len(set(results)), 1)


if __name__ == "__main__":
    unittest.main()
