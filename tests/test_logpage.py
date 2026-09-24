"""Loopback log page security and ledger rendering tests."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger                                             # noqa: E402
import logpage                                            # noqa: E402
import rules                                              # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures                                            # noqa: E402


class LogPageTests(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.state_file = os.path.join(self.directory, "state.db")
        self.source = os.path.join(self.directory, "source.png")
        self.destination = os.path.join(self.directory, "Sorted", "source.png")
        with open(self.source, "wb") as handle:
            handle.write(b"source")
        os.makedirs(os.path.dirname(self.destination))
        with open(self.destination, "wb") as handle:
            handle.write(b"destination")
        self.journal = ledger.Ledger(self.state_file)
        run_id = self.journal.start_run("sort", self.directory, "rules", False)
        self.move_id = self.journal.add_move(
            run_id, 1, 1, "move", "images", self.source, self.destination,
            11, "hash", facts={"kind": "image", "format": "png",
                               "width": 1920, "height": 1080})
        self.journal.update_move(self.move_id, "done")
        self.journal.finish_run(run_id, "completed")
        self.page = logpage.LogPage(self.journal, 48765, "test-token")

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def request(self, method, target, headers=None, body=b""):
        return self.page.handle_request(method, target, headers or {}, body)

    def payload(self, response):
        return json.loads(response.body.decode("utf-8"))

    def url(self, path):
        return path + "?token=test-token"

    def origin(self):
        return {"Origin": "http://127.0.0.1:48765"}

    def test_page_and_api_require_the_url_token(self):
        self.assertEqual(self.request("GET", "/").status, 403)
        response = self.request("GET", self.url("/"))
        self.assertEqual(response.status, 200)
        self.assertIn(b'const token="test-token"', response.body)
        self.assertIn(b"Open queue", response.body)

        moves = self.request("GET", self.url("/api/moves"))
        self.assertEqual(moves.status, 200)
        row = self.payload(moves)["moves"][0]
        self.assertEqual(row["id"], self.move_id)
        self.assertEqual(row["destination"], self.destination)
        self.assertEqual(row["facts"]["kind"], "image")
        self.assertEqual(row["facts"]["width"], 1920)

    def test_mutating_controls_require_same_origin(self):
        denied = self.request("POST", self.url("/api/pause"))
        self.assertEqual(denied.status, 403)
        self.assertFalse(self.journal.paused())

        paused = self.request("POST", self.url("/api/pause"), self.origin())
        self.assertEqual(paused.status, 200)
        self.assertTrue(self.journal.paused())

        resumed = self.request("POST", self.url("/api/resume"), self.origin())
        self.assertEqual(resumed.status, 200)
        self.assertFalse(self.journal.paused())

    def test_reveal_uses_a_ledger_id_not_a_client_path(self):
        with mock.patch("logpage.reveal") as reveal:
            response = self.request(
                "POST", self.url("/api/reveal/%d" % self.move_id)
                + "&path=/etc/passwd", self.origin())
        self.assertEqual(response.status, 200)
        reveal.assert_called_once_with(self.destination)
        self.assertEqual(self.request(
            "POST", self.url("/api/reveal/not-a-number"),
            self.origin()).status, 400)
        self.assertEqual(self.request(
            "POST", self.url("/api/reveal/99999"),
            self.origin()).status, 404)

    def test_reveal_declines_missing_recorded_paths(self):
        os.unlink(self.destination)
        os.unlink(self.source)
        self.assertEqual(self.request(
            "POST", self.url("/api/reveal/%d" % self.move_id),
            self.origin()).status, 409)

    def test_move_limits_are_bounded_multiples_of_fifty(self):
        self.assertEqual(self.request(
            "GET", "/api/moves?token=test-token&limit=100").status, 200)
        self.assertEqual(self.request(
            "GET", "/api/moves?token=test-token&limit=51").status, 400)
        self.assertEqual(self.request(
            "GET", "/api/moves?token=test-token&limit=550").status, 400)

    def a_preview(self):
        run_id = self.journal.start_run("sort", self.directory, "rules", True)
        move = self.journal.add_move(
            run_id, 1, 1, "move", "images", self.source,
            self.destination + ".preview", 11, "hash", status="dry-run")
        self.journal.finish_run(run_id, "dry-run")
        return move

    def listed(self, target):
        import json
        response = self.request("GET", target)
        return [row["id"] for row in json.loads(response.body)["moves"]]

    def test_a_preview_is_not_listed_as_a_move(self):
        """It moved nothing, and each first run is one: every file showed
        twice on a page headed "everything auto-sort has moved"."""
        preview = self.a_preview()
        shown = self.listed("/api/moves?token=test-token&limit=50")
        self.assertIn(self.move_id, shown)
        self.assertNotIn(preview, shown)

    def test_previews_are_there_when_asked_for(self):
        preview = self.a_preview()
        self.assertIn(preview, self.listed(
            "/api/moves?token=test-token&limit=50&previews=1"))

    def test_nor_found_by_a_search_unless_asked(self):
        preview = self.a_preview()
        self.assertNotIn(preview, self.listed(
            "/api/moves?token=test-token&limit=50&q=source"))
        self.assertIn(preview, self.listed(
            "/api/moves?token=test-token&limit=50&q=source&previews=1"))

    def test_copy_resolves_the_file_from_its_ledger_id(self):
        folder = os.path.join(self.directory, "Copies")
        os.mkdir(folder)
        response = self.request(
            "POST", self.url("/api/file/copy/%d" % self.move_id), self.origin(),
            json.dumps({"folder": folder}).encode("utf-8"))
        self.assertEqual(response.status, 200)
        copied = os.path.join(folder, "source.png")
        self.assertTrue(os.path.exists(copied))
        with open(copied, "rb") as handle:
            self.assertEqual(handle.read(), b"destination")
        copied_row = self.journal.move(self.payload(response)["move_id"])
        self.assertEqual(json.loads(copied_row["facts_json"])["kind"], "image")

    def test_restore_returns_a_recorded_move_to_its_original_path(self):
        os.unlink(self.source)
        response = self.request(
            "POST", self.url("/api/file/undo/%d" % self.move_id),
            self.origin(), b"{}")
        self.assertEqual(response.status, 200)
        self.assertTrue(os.path.exists(self.source))
        self.assertFalse(os.path.exists(self.destination))

    def test_file_actions_refuse_cross_origin_and_bad_folder(self):
        denied = self.request("POST", self.url("/api/file/copy/%d" % self.move_id),
                              body=b'{"folder":"/tmp"}')
        self.assertEqual(denied.status, 403)
        bad = self.request("POST", self.url("/api/file/move/%d" % self.move_id),
                           self.origin(), b'{"folder":"/missing"}')
        self.assertEqual(bad.status, 400)

    def test_trash_is_delegated_to_the_platform_not_unlinked(self):
        with mock.patch("logpage.trash") as move_to_trash:
            response = self.request(
                "POST", self.url("/api/file/trash/%d" % self.move_id),
                self.origin(), b"{}")
        self.assertEqual(response.status, 200)
        move_to_trash.assert_called_once_with(self.destination)

    def test_http_connection_has_security_headers(self):
        server, client = socket.socketpair()
        try:
            client.sendall((
                "GET /api/status?token=test-token HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n\r\n").encode("ascii"))
            self.page.handle_connection(server, server.recv(4096))
            response = client.recv(8192)
            self.assertIn(b"HTTP/1.1 200 OK", response)
            self.assertIn(b"Cache-Control: no-store", response)
            self.assertIn(b"X-Frame-Options: DENY", response)
        finally:
            server.close()
            client.close()

    def test_linux_reveal_falls_back_when_dbus_is_absent(self):
        with mock.patch("logpage.sys.platform", "linux"), \
                mock.patch("logpage.os.name", "posix"), \
                mock.patch("logpage.subprocess.Popen",
                           side_effect=[OSError("no dbus"), mock.Mock()]) as run:
            logpage.reveal(self.destination)
        self.assertEqual(run.call_args_list[1][0][0][0], "xdg-open")

    def test_linux_reveal_falls_back_when_nothing_answers_dbus(self):
        # `dbus-send --type=method_call` alone reports success the moment the
        # message reaches the session bus, whether or not any file manager is
        # listening on FileManager1 -- a message with no service registered
        # for it still returns 0. That is the common case on a desktop that
        # has dbus-send installed but no file manager running, and it is
        # distinct from the OSError case above (the binary is simply
        # missing). Without --print-reply this looked like success and the
        # fallback never ran.
        with mock.patch("logpage.sys.platform", "linux"), \
                mock.patch("logpage.os.name", "posix"), \
                mock.patch("logpage.subprocess.run",
                           return_value=mock.Mock(returncode=0)) as run, \
                mock.patch("logpage.subprocess.Popen") as popen:
            logpage.reveal(self.destination)
        self.assertIn("--print-reply", run.call_args[0][0])
        popen.assert_not_called()

    def test_linux_reveal_uses_print_reply_to_detect_no_listener(self):
        with mock.patch("logpage.sys.platform", "linux"), \
                mock.patch("logpage.os.name", "posix"), \
                mock.patch("logpage.subprocess.run",
                           return_value=mock.Mock(returncode=1)) as run, \
                mock.patch("logpage.subprocess.Popen") as popen:
            logpage.reveal(self.destination)
        self.assertIn("--print-reply", run.call_args[0][0])
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0][0], "xdg-open")


class OneTimeSortOfAFolder(unittest.TestCase):
    """`/api/import`: the "One-time sort of a folder..." button's endpoint.

    Found on a live desktop, not in a mock: the button's first-ever apply
    for a USB-stick-like folder reported "Sorted 1 item(s)." while the file
    never moved. `sorter.execute` forces a folder's first apply under a
    given rules fingerprint to a preview -- the same safety net `sort
    --apply` already respects on the CLI -- but `_import` read the result
    through `getattr(result, "moved", len(plan.items))` and
    `getattr(result, "failures", [])`, names `RunResult` has never had
    (it has `completed` and an integer `failed`), so both defaults fired
    unconditionally and `"applied": True` was hardcoded regardless of
    `result.dry_run`. No test exercised this endpoint at all.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.intake = os.path.join(self.directory, "usb")
        self.output = os.path.join(self.directory, "output")
        os.makedirs(self.intake)
        os.makedirs(self.output)
        self.rules_file = os.path.join(self.directory, "rules.ini")
        self.state_file = os.path.join(self.directory, "state.db")
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write("""
[settings]
dry_run = no
settle_seconds = 0

[watch]
folders =

[rule: images]
when = kind = image
into = {output}/Pictures
""".format(output=self.output))
        self.journal = ledger.Ledger(self.state_file)
        self.page = logpage.LogPage(
            self.journal, 48766, "test-token",
            rules_getter=lambda: rules.load(self.rules_file))

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def request(self, method, target, headers=None, body=b""):
        return self.page.handle_request(method, target, headers or {}, body)

    def payload(self, response):
        return json.loads(response.body.decode("utf-8"))

    def origin(self):
        return {"Origin": "http://127.0.0.1:48766"}

    def apply_folder(self, folder):
        return self.request(
            "POST", "/api/import?token=test-token", self.origin(),
            json.dumps({"folder": folder, "apply": True}).encode("utf-8"))

    def test_a_folders_first_apply_does_not_claim_success_it_did_not_earn(self):
        source = fixtures.png(os.path.join(self.intake, "photo.png"))
        response = self.apply_folder(self.intake)
        body = self.payload(response)

        self.assertTrue(os.path.exists(source),
                        "the file must not have moved: this was a forced preview")
        self.assertFalse(body["applied"])
        self.assertEqual(body["moved"], 0)
        self.assertTrue(body.get("forced_preview"))

    def test_the_second_apply_for_the_same_folder_actually_moves_it(self):
        source = fixtures.png(os.path.join(self.intake, "photo.png"))
        self.apply_folder(self.intake)          # forced preview, as above
        response = self.apply_folder(self.intake)
        body = self.payload(response)

        self.assertTrue(body["applied"])
        self.assertEqual(body["moved"], 1)
        self.assertEqual(body["failed"], 0)
        self.assertFalse(os.path.exists(source))
        self.assertTrue(os.path.exists(
            os.path.join(self.output, "Pictures", "photo.png")))


class WhatThisMachineCanRead(unittest.TestCase):
    """Somebody whose scanned post is being filed by nothing but its file
    type has no way to find out why. The answer is a program they have
    never heard of and do not have."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-reading-")
        self.journal = ledger.Ledger(os.path.join(self.dir, "state.db"))
        self.page = logpage.LogPage(self.journal, 48766, "test-token")

    def tearDown(self):
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def ask(self):
        response = self.page.handle_request(
            "GET", "/api/reading?token=test-token")
        return json.loads(response.body)

    def filed(self, number, facts):
        run = self.journal.start_run("sort", source_root=self.dir,
                                     dry_run=False)
        move = self.journal.add_move(
            run, number, 1, "move", "documents",
            os.path.join(self.dir, "f%d.pdf" % number),
            os.path.join(self.dir, "out", "f%d.pdf" % number),
            10, "", "done", facts=facts)
        self.journal.update_move(move, "done")
        placed = os.path.join(self.dir, "out", "f%d.pdf" % number)
        os.makedirs(os.path.dirname(placed), exist_ok=True)
        with open(placed, "w") as handle:
            handle.write("%PDF")
        return placed

    def test_it_lists_every_optional_program(self):
        keys = [row["key"] for row in self.ask()["programs"]]
        self.assertEqual(sorted(keys), ["exiftool", "ffprobe", "tesseract"])

    def test_each_one_says_what_it_is_for_and_how_to_get_it(self):
        for row in self.ask()["programs"]:
            self.assertTrue(row["purpose"])
            self.assertIn("installed", row)

    def test_pages_nobody_could_read_are_counted(self):
        self.filed(1, {"kind": "document", "needs_ocr": True})
        self.filed(2, {"kind": "document", "needs_ocr": True})
        self.filed(3, {"kind": "document", "heading": "Rechnung"})
        self.assertEqual(self.ask()["filed_unread"], 2)

    def test_a_page_moved_away_by_hand_is_not_waiting(self):
        self.filed(1, {"kind": "document", "needs_ocr": True})
        os.remove(self.filed(2, {"kind": "document", "needs_ocr": True}))
        self.assertEqual(self.ask()["filed_unread"], 1)

    def test_a_folder_where_everything_was_read_says_nothing(self):
        self.filed(1, {"kind": "document", "heading": "Rechnung"})
        self.assertEqual(self.ask()["filed_unread"], 0)

    def test_an_undone_move_is_not_still_waiting(self):
        self.filed(1, {"kind": "document", "needs_ocr": True})
        self.journal.connection.execute(
            "UPDATE moves SET undone_at = '2026-01-01T00:00:00'")
        self.journal.connection.commit()
        self.assertEqual(self.ask()["filed_unread"], 0)


if __name__ == "__main__":
    unittest.main()
