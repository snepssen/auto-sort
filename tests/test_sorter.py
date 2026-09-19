"""Safety tests for planning, moving, journalling and undo."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import contextlib
import io
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures                                          # noqa: E402
import autosort                                          # noqa: E402
import bundles                                           # noqa: E402
import ledger                                            # noqa: E402
import mover                                             # noqa: E402
import provenance                                        # noqa: E402
import rules                                             # noqa: E402
import sorter                                            # noqa: E402


class MovePrimitives(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.directory, name)

    def write(self, name, body=b"content"):
        filename = self.path(name)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "wb") as handle:
            handle.write(body)
        return filename

    def test_same_volume_move_preserves_content_and_removes_source(self):
        source = self.write("source.bin", b"one")
        destination = self.path("there/destination.bin")
        expected = mover.hash_path(source)
        result = mover.transfer(source, destination, expected_hash=expected)
        self.assertTrue(result.source_removed)
        self.assertFalse(os.path.exists(source))
        self.assertEqual(mover.hash_path(destination), expected)

    def test_destination_is_never_overwritten(self):
        source = self.write("source.bin", b"new")
        destination = self.write("destination.bin", b"old")
        with self.assertRaises(mover.MoveError):
            mover.transfer(source, destination,
                           expected_hash=mover.hash_path(source))
        self.assertTrue(os.path.exists(source))
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"old")

    def test_copy_is_verified_and_keeps_source(self):
        source = self.write("source.bin", b"copy me")
        destination = self.path("copies/copy.bin")
        expected = mover.hash_path(source)
        result = mover.transfer(source, destination, operation="copy",
                                expected_hash=expected)
        self.assertTrue(result.copied)
        self.assertFalse(result.source_removed)
        self.assertTrue(os.path.exists(source))
        self.assertEqual(mover.hash_path(destination), expected)

    def test_cross_volume_order_is_copy_verify_then_remove(self):
        source = self.write("source.bin", b"cross volume")
        destination = self.path("other/destination.bin")
        expected = mover.hash_path(source)
        with mock.patch("mover.same_volume", return_value=False):
            result = mover.transfer(source, destination,
                                    expected_hash=expected)
        self.assertTrue(result.copied)
        self.assertTrue(result.source_removed)
        self.assertFalse(os.path.exists(source))
        self.assertEqual(mover.hash_path(destination), expected)

    def test_copy_refuses_to_expand_a_hard_link(self):
        source = self.write("source.bin", b"linked")
        os.link(source, self.path("other-link.bin"))
        with self.assertRaises(mover.MoveError):
            mover.transfer(source, self.path("copy.bin"), operation="copy",
                           expected_hash=mover.hash_path(source))

    def test_changed_source_is_refused(self):
        source = self.write("source.bin", b"before")
        expected = mover.hash_path(source)
        self.write("source.bin", b"after")
        with self.assertRaises(mover.MoveError):
            mover.transfer(source, self.path("destination.bin"),
                           expected_hash=expected)

    def test_package_hash_changes_with_a_member(self):
        os.makedirs(self.path("Thing.app/Contents"))
        member = self.write("Thing.app/Contents/info", b"one")
        before = mover.hash_path(self.path("Thing.app"))
        with open(member, "wb") as handle:
            handle.write(b"two")
        self.assertNotEqual(before, mover.hash_path(self.path("Thing.app")))

    def test_package_directory_moves_without_copying_its_contents(self):
        package = self.path("Thing.app")
        os.makedirs(os.path.join(package, "Contents"))
        self.write("Thing.app/Contents/info", b"one")
        destination = self.path("Elsewhere/Thing.app")
        expected = mover.hash_path(package)
        mover.transfer(package, destination, expected_hash=expected)
        self.assertFalse(os.path.exists(package))
        self.assertEqual(mover.hash_path(destination), expected)

    def test_package_directory_can_be_verified_as_a_copy(self):
        package = self.path("Thing.app")
        os.makedirs(os.path.join(package, "Contents"))
        self.write("Thing.app/Contents/info", b"one")
        destination = self.path("Copy/Thing.app")
        expected = mover.hash_path(package)
        mover.transfer(package, destination, operation="copy",
                       expected_hash=expected)
        self.assertTrue(os.path.exists(package))
        self.assertEqual(mover.hash_path(destination), expected)

    @unittest.skipUnless(sys.platform == "darwin", "macOS metadata API")
    def test_native_copy_preserves_extended_attributes(self):
        import ctypes
        source = self.write("source.bin", b"metadata")
        attribute = b"com.example.autosort-test"
        value = b"kept"
        libc = ctypes.CDLL(None, use_errno=True)
        libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                 ctypes.c_void_p, ctypes.c_size_t,
                                 ctypes.c_uint32, ctypes.c_int]
        buffer = ctypes.create_string_buffer(value)
        result = libc.setxattr(os.fsencode(source), attribute, buffer,
                               len(value), 0, 0)
        if result != 0:
            self.skipTest("temporary filesystem does not support xattrs")
        destination = self.path("copied.bin")
        mover.transfer(source, destination, operation="copy",
                       expected_hash=mover.hash_path(source))
        self.assertEqual(provenance.xattr(destination, attribute.decode()), value)


class SortRuns(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.root = os.path.join(self.directory, "inbox")
        self.output = os.path.join(self.directory, "output")
        os.makedirs(self.root)
        os.makedirs(self.output)
        self.rules_file = os.path.join(self.directory, "rules.ini")
        self.state_file = os.path.join(self.directory, "state.db")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def configure(self, body=None, dry_run="yes", collision="suffix"):
        if body is None:
            body = """
[rule: images]
when = kind = image
into = {output}/Pictures
""".format(output=self.output)
        source = """
[settings]
dry_run = {dry_run}
settle_seconds = 0
on_collision = {collision}

[watch]
folders = {root}
depth = 3

{body}
""".format(dry_run=dry_run, collision=collision, root=self.root, body=body)
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write(source)
        return rules.load(self.rules_file)

    def image(self, name="photo.png"):
        return fixtures.png(os.path.join(self.root, name))

    def test_first_apply_is_forced_to_a_preview_then_can_move(self):
        source = self.image()
        rule_set = self.configure(dry_run="no")
        plan = sorter.build_plan(self.root, rule_set)
        self.assertEqual(len(plan.items), 1)
        with ledger.Ledger(self.state_file) as journal:
            preview = sorter.execute(plan, rule_set, journal, dry_run=False)
            self.assertTrue(preview.dry_run)
            self.assertTrue(preview.forced_preview)
            self.assertTrue(os.path.exists(source))

            applied = sorter.execute(plan, rule_set, journal, dry_run=False)
            self.assertFalse(applied.dry_run)
            self.assertEqual(applied.completed, 1)
            destination = plan.members[0].destination
            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.exists(destination))
            rows = journal.moves(applied.run_id)
            self.assertEqual(rows[0]["status"], "done")

    def test_dry_run_records_intent_but_touches_no_paths(self):
        source = self.image()
        rule_set = self.configure()
        plan = sorter.build_plan(self.root, rule_set)
        with ledger.Ledger(self.state_file) as journal:
            result = sorter.execute(plan, rule_set, journal)
            self.assertTrue(result.dry_run)
            self.assertTrue(os.path.exists(source))
            self.assertFalse(os.path.exists(plan.members[0].destination))
            self.assertEqual(journal.moves(result.run_id)[0]["status"],
                             "dry-run")

    def test_bundle_moves_together_and_undo_restores_it(self):
        video = os.path.join(self.root, "Film.mkv")
        subtitle = os.path.join(self.root, "Film.en.srt")
        fixtures.text(video, b"not a real film")
        fixtures.text(subtitle, "1\n")
        rule_set = self.configure(body="""
[rule: films]
when = kind = video
into = {output}/Films
""".format(output=self.output), dry_run="no")
        plan = sorter.build_plan(self.root, rule_set)
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(len(plan.items[0].members), 2)
        with ledger.Ledger(self.state_file) as journal:
            sorter.execute(plan, rule_set, journal, dry_run=True)
            applied = sorter.execute(plan, rule_set, journal, dry_run=False)
            for member in plan.members:
                self.assertFalse(os.path.exists(member.source))
                self.assertTrue(os.path.exists(member.destination))

            undone = sorter.undo(journal, applied.run_id)
            self.assertEqual(undone.failed, 0)
            self.assertTrue(os.path.exists(video))
            self.assertTrue(os.path.exists(subtitle))
            for member in plan.members:
                self.assertFalse(os.path.exists(member.destination))

    def test_bundle_failure_rolls_back_members_already_moved(self):
        video = os.path.join(self.root, "Film.mkv")
        subtitle = os.path.join(self.root, "Film.en.srt")
        fixtures.text(video, b"not a real film")
        fixtures.text(subtitle, "1\n")
        rule_set = self.configure(body="""
[rule: films]
when = kind = video
into = {output}/Films
""".format(output=self.output), dry_run="no")
        plan = sorter.build_plan(self.root, rule_set)
        real_transfer = mover.transfer
        calls = {"count": 0}

        def fail_second(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise mover.MoveError("simulated failure")
            return real_transfer(*args, **kwargs)

        with ledger.Ledger(self.state_file) as journal:
            sorter.execute(plan, rule_set, journal, dry_run=True)
            with mock.patch("sorter.mover.transfer", side_effect=fail_second):
                result = sorter.execute(plan, rule_set, journal, dry_run=False)
            self.assertEqual(result.failed, 1)
            self.assertTrue(os.path.exists(video))
            self.assertTrue(os.path.exists(subtitle))
            self.assertFalse(os.path.exists(plan.members[0].destination))
            statuses = [row["status"] for row in journal.moves(result.run_id)]
            self.assertEqual(statuses, ["rolled-back", "failed"])

    def test_collision_suffixes_without_overwriting(self):
        source = self.image()
        existing = os.path.join(self.output, "Pictures", "photo.png")
        os.makedirs(os.path.dirname(existing))
        with open(existing, "wb") as handle:
            handle.write(b"keep")
        rule_set = self.configure()
        plan = sorter.build_plan(self.root, rule_set)
        self.assertEqual(os.path.basename(plan.members[0].destination),
                         "photo (2).png")
        with open(existing, "rb") as handle:
            self.assertEqual(handle.read(), b"keep")
        self.assertTrue(os.path.exists(source))

    def test_collision_skip_declines_the_item(self):
        self.image()
        existing = os.path.join(self.output, "Pictures", "photo.png")
        os.makedirs(os.path.dirname(existing))
        with open(existing, "wb") as handle:
            handle.write(b"keep")
        rule_set = self.configure(collision="skip")
        plan = sorter.build_plan(self.root, rule_set)
        self.assertFalse(plan.items)
        self.assertIn("already exists", plan.skipped[0][1])

    def test_bundle_members_may_not_collapse_to_one_portable_name(self):
        first = os.path.join(self.root, "first.txt")
        second = os.path.join(self.root, "second.txt")
        item = bundles.Item(first, [first, second])
        destinations = [os.path.join(self.output, "same.txt")] * 2
        _paths, reason = sorter._resolve_collisions(
            item, destinations, [first, second], set(), "suffix")
        self.assertIn("same portable name", reason)

    def test_autosortignore_applies_per_directory_and_can_negate(self):
        os.makedirs(os.path.join(self.root, "private"))
        fixtures.png(os.path.join(self.root, "private", "hidden.png"))
        fixtures.png(os.path.join(self.root, "private", "keep.png"))
        with open(os.path.join(self.root, ".autosortignore"), "w",
                  encoding="utf-8") as handle:
            handle.write("private/**\n!private/keep.png\n")
        rule_set = self.configure()
        plan = sorter.build_plan(self.root, rule_set)
        planned_names = [os.path.basename(member.source)
                         for member in plan.members]
        self.assertEqual(planned_names, ["keep.png"])
        self.assertTrue(any(".autosortignore" in reason
                            for _path, reason in plan.skipped))

    def test_active_state_and_configuration_paths_can_be_protected(self):
        active = self.image("active-state.png")
        ordinary = self.image("ordinary.png")
        rule_set = self.configure()
        plan = sorter.build_plan(self.root, rule_set, exclude=(active,))
        self.assertEqual([member.source for member in plan.members], [ordinary])
        self.assertTrue(any("state file" in reason
                            for _path, reason in plan.skipped))

    def test_cli_preview_apply_and_undo(self):
        source = self.image()
        self.configure(dry_run="no")
        arguments = ["sort", self.root, "--rules", self.rules_file,
                     "--state", self.state_file, "--apply"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(autosort.main(arguments), 0)
        self.assertIn("DRY RUN", output.getvalue())
        self.assertTrue(os.path.exists(source))

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(autosort.main(arguments), 0)
        self.assertIn("SORTED", output.getvalue())
        self.assertFalse(os.path.exists(source))

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(autosort.main(
                ["undo", "last", "--state", self.state_file]), 0)
        self.assertIn("UNDONE", output.getvalue())
        self.assertTrue(os.path.exists(source))

    def test_undo_refuses_a_destination_changed_after_sorting(self):
        source = self.image()
        rule_set = self.configure(dry_run="no")
        plan = sorter.build_plan(self.root, rule_set)
        with ledger.Ledger(self.state_file) as journal:
            sorter.execute(plan, rule_set, journal, dry_run=True)
            applied = sorter.execute(plan, rule_set, journal, dry_run=False)
            with open(plan.members[0].destination, "ab") as handle:
                handle.write(b"changed")
            undone = sorter.undo(journal, applied.run_id)
            self.assertEqual(undone.failed, 1)
            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.exists(plan.members[0].destination))

    def test_undo_preflights_the_whole_bundle_before_restoring_any_member(self):
        video = os.path.join(self.root, "Film.mkv")
        subtitle = os.path.join(self.root, "Film.en.srt")
        fixtures.text(video, b"not a real film")
        fixtures.text(subtitle, "1\n")
        rule_set = self.configure(body="""
[rule: films]
when = kind = video
into = {output}/Films
""".format(output=self.output), dry_run="no")
        plan = sorter.build_plan(self.root, rule_set)
        with ledger.Ledger(self.state_file) as journal:
            sorter.execute(plan, rule_set, journal, dry_run=True)
            applied = sorter.execute(plan, rule_set, journal, dry_run=False)
            with open(plan.members[1].destination, "ab") as handle:
                handle.write(b"changed")
            undone = sorter.undo(journal, applied.run_id)
            self.assertEqual(undone.failed, 1)
            self.assertFalse(os.path.exists(video))
            self.assertFalse(os.path.exists(subtitle))
            self.assertTrue(os.path.exists(plan.members[0].destination))
            self.assertTrue(os.path.exists(plan.members[1].destination))

    def test_changed_rules_require_another_preview(self):
        self.image()
        first = self.configure(dry_run="no")
        plan = sorter.build_plan(self.root, first)
        with ledger.Ledger(self.state_file) as journal:
            sorter.execute(plan, first, journal, dry_run=True)
            changed = self.configure(body="""
[rule: images]
when = kind = image
into = {output}/Changed
""".format(output=self.output), dry_run="no")
            changed_plan = sorter.build_plan(self.root, changed)
            result = sorter.execute(changed_plan, changed, journal, dry_run=False)
            self.assertTrue(result.forced_preview)

    def test_rule_fingerprint_is_the_content_that_was_parsed(self):
        loaded = self.configure()
        parsed_hash = sorter.rules_hash(loaded)
        with open(self.rules_file, "a", encoding="utf-8") as handle:
            handle.write("\n; changed after load\n")
        self.assertEqual(sorter.rules_hash(loaded), parsed_hash)
        self.assertNotEqual(sorter.rules_hash(rules.load(self.rules_file)),
                            parsed_hash)

    def test_reconcile_confirms_a_move_completed_before_journal_update(self):
        source = self.image()
        destination = os.path.join(self.output, "photo.png")
        digest = mover.hash_path(source)
        size = mover.size_path(source)
        with ledger.Ledger(self.state_file) as journal:
            run_id = journal.start_run("sort", self.root, "rules", False)
            move_id = journal.add_move(run_id, 1, 1, "move", "images",
                                      source, destination, size, digest)
            mover.transfer(source, destination, expected_hash=digest)
            messages = sorter.reconcile(journal)
            self.assertTrue(messages)
            row = journal.moves(run_id)[0]
            self.assertEqual(row["id"], move_id)
            self.assertEqual(row["status"], "done")
            self.assertEqual(journal.run(run_id)["status"], "recovered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
