"""Folders whose contents only work together.

Ten CV folders, each a LaTeX build with its class file and a font map beside
the `.tex`, were sorted one file at a time by name. Every `altacv.cls` ended
up pooled in one folder, away from every CV that needed it, and none of the
ten would compile afterwards. Nothing was lost -- everything was journalled
-- but a sorter that breaks a build to file it has not tidied anything.
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
import identify                                          # noqa: E402


class KnowingAProjectWhenItSeesOne(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-proj-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def folder(self, name, files):
        path = os.path.join(self.dir, name)
        os.makedirs(path)
        for filename, content in files.items():
            target = os.path.join(path, filename)
            if content is None:
                os.makedirs(target)
            else:
                with open(target, "w") as handle:
                    handle.write(content)
        return path

    def test_a_latex_build_is_a_project(self):
        path = self.folder("Sales Advisor", {
            "cv.tex": "\\documentclass{altacv}\n\\begin{document}",
            "altacv.cls": "%", "glyphtounicode.tex": "%"})
        self.assertEqual(bundles.project_kind(path), "latex")

    def test_a_lone_tex_file_is_just_a_document(self):
        path = self.folder("notes", {"notes.tex": "\\documentclass{article}"})
        self.assertIsNone(bundles.project_kind(path))

    def test_a_class_file_with_no_document_is_not_a_build(self):
        path = self.folder("classes", {"altacv.cls": "%", "part.tex": "%"})
        self.assertIsNone(bundles.project_kind(path))

    def test_other_kinds(self):
        for name, files, kind in (
                ("repo", {".git": None, "README": "x"}, "repository"),
                ("site", {"package.json": "{}"}, "code"),
                ("song", {"mix.rpp": "x", "take1.wav": "x"}, "audio"),
                ("scene", {"shot.blend": "x"}, "3d")):
            self.assertEqual(bundles.project_kind(self.folder(name, files)),
                             kind, name)

    def test_the_name_of_the_folder_decides_nothing(self):
        path = self.folder("My Project", {"a.txt": "x", "b.pdf": "x"})
        self.assertIsNone(bundles.project_kind(path))

    def test_it_is_walked_as_one_item_and_not_into(self):
        self.folder("CV", {"cv.tex": "\\documentclass{altacv}",
                           "altacv.cls": "%"})
        items = list(bundles.walk(self.dir, max_depth=3))
        self.assertEqual([os.path.basename(i.primary) for i in items], ["CV"])
        self.assertEqual(items[0].reason, "project folder")

    def test_a_rule_can_see_what_kind_it_is(self):
        path = self.folder("CV", {"cv.tex": "\\documentclass{altacv}",
                                  "altacv.cls": "%"})
        record = identify.identify(bundles.Item(path, [path], is_dir=True))
        self.assertEqual(record.value("project"), "latex")

    def test_the_watched_folder_is_never_one_itself(self):
        """It is where things arrive, not a thing."""
        with open(os.path.join(self.dir, "cv.tex"), "w") as handle:
            handle.write("\\documentclass{x}")
        with open(os.path.join(self.dir, "x.cls"), "w") as handle:
            handle.write("%")
        names = sorted(os.path.basename(i.primary)
                       for i in bundles.walk(self.dir, max_depth=3))
        self.assertIn("cv.tex", names)


if __name__ == "__main__":
    unittest.main()
