"""The loopback-only log page and ledger-ID-based file reveal endpoint."""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import urllib.parse

import costs
import mirror
import platform_support
import mover
import sorter

MAX_HEADER_BYTES = 16384
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "index.html")


class Response(object):
    def __init__(self, status=200, body=b"", content_type="text/plain; charset=utf-8",
                 command=""):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.content_type = content_type
        self.command = command


class LogPage(object):
    """Small HTTP application hosted by the daemon's already-locked socket."""

    def __init__(self, journal, port, token, rules_getter=None,
                 rule_path=None, reader=None):
        self.journal = journal
        self.port = int(port)
        self.token = str(token)
        # A getter rather than a rule set: the daemon reloads its rules
        # whenever the file changes, and a page showing the set that was
        # loaded at boot would quietly go stale.
        self.rules_getter = rules_getter
        self.rule_path = rule_path
        # The daemon's supervised reader, where there is one. A one-time
        # sort started from this page runs on the same thread that serves
        # the page, so an unreadable file in the chosen folder would
        # otherwise take the page down with it -- and the page is where
        # somebody would go to find out why nothing is happening.
        self.reader = reader

    @property
    def url(self):
        return "http://127.0.0.1:%d/?token=%s" % (
            self.port, urllib.parse.quote(self.token, safe=""))

    def handle_connection(self, connection, initial):
        """Serve one HTTP request, or return None for the daemon wake protocol."""
        if not _looks_like_http(initial):
            return None
        try:
            method, target, headers, body = _read_request(connection, initial)
            response = self.handle_request(method, target, headers, body)
        except (ValueError, OSError) as error:
            response = _json_response(400, {"error": "bad request: %s" % error})
        _send_response(connection, response)
        return response.command

    def handle_request(self, method, target, headers=None, body=b""):
        headers = dict((str(key).lower(), value)
                       for key, value in (headers or {}).items())
        parsed = urllib.parse.urlsplit(target)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if not self._valid_token(query):
            return _json_response(403, {"error": "invalid log token"})

        if parsed.path == "/" and method == "GET":
            return Response(200, self._page(), "text/html; charset=utf-8")
        if parsed.path == "/api/status" and method == "GET":
            return _json_response(200, self._status())
        if parsed.path == "/api/moves" and method == "GET":
            try:
                limit = int(query.get("limit", [50])[0])
            except ValueError:
                return _json_response(400, {"error": "limit must be a number"})
            if limit < 50 or limit > 500 or limit % 50:
                return _json_response(400, {
                    "error": "limit must be a multiple of 50, from 50 to 500"})
            text = query.get("q", [""])[0].strip()[:200]
            previews = query.get("previews", ["0"])[0] == "1"
            rows = self.journal.search_moves(text, limit, previews) if text \
                else self.journal.recent_moves(limit, previews)
            return _json_response(200, {"moves": [self._move(row)
                                                  for row in rows],
                                        "searched": bool(text)})

        if method == "POST" and parsed.path in (
                "/api/pause", "/api/resume", "/api/sort-now",
                "/api/restart"):
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            if parsed.path == "/api/pause":
                self.journal.set_paused(True)
                return _json_response(200, self._status(), "")
            if parsed.path == "/api/resume":
                self.journal.set_paused(False)
                return _json_response(200, self._status(), "")
            if parsed.path == "/api/restart":
                # The same command the tray sends. The page will go quiet
                # for a moment and come back on its own: the token lives in
                # the ledger rather than in the process, so this URL is
                # still the right one afterwards.
                return _json_response(202, {"restarting": True}, "restart")
            return _json_response(202, {"queued": True}, "sort-now")

        if parsed.path == "/api/rules" and method == "GET":
            return _json_response(200, self._rules())
        if parsed.path == "/api/folders" and method == "GET":
            return _json_response(200, self._folders())
        if parsed.path == "/api/costs" and method == "GET":
            return _json_response(200, self._costs())
        if method == "POST" and parsed.path.startswith("/api/costs/forget/"):
            if not self._same_origin(headers):
                return _json_response(403,
                                      {"error": "cross-origin request refused"})
            try:
                cost_id = int(parsed.path.rsplit("/", 1)[-1])
            except ValueError:
                return _json_response(400, {"error": "not a row number"})
            self.journal.forget_cost(cost_id)
            return _json_response(200, self._costs())
        if parsed.path == "/api/reading" and method == "GET":
            return _json_response(200, self._reading())
        if parsed.path == "/api/backup" and method == "GET":
            return _json_response(200, self._backup())
        if parsed.path == "/api/backup" and method == "POST":
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            return self._set_backup(_body(body))
        if parsed.path == "/api/folders/watch" and method == "POST":
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            payload = _body(body)
            folder = str(payload.get("folder", "")).strip()
            action = str(payload.get("action", "add"))
            if not folder:
                return _json_response(400, {"error": "no folder given"})
            folder = os.path.abspath(os.path.expanduser(folder))
            extra = self.journal.extra_watch_folders()
            if action == "remove":
                extra = [path for path in extra if path != folder]
            else:
                if not os.path.isdir(folder):
                    return _json_response(400, {"error": "that is not a folder"})
                rule_set = self.rules_getter() if self.rules_getter else None
                fixed = [os.path.abspath(os.path.expanduser(path))
                         for path in (rule_set.watch.folders if rule_set else ())]
                if folder in fixed:
                    return _json_response(400, {
                        "error": "your rules file already watches that folder"})
                if folder not in extra:
                    extra.append(folder)
            self.journal.set_extra_watch_folders(extra)
            return _json_response(200, {"watch_added": extra})
        if parsed.path == "/api/import" and method == "POST":
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            payload = _body(body)
            folder = os.path.abspath(os.path.expanduser(
                str(payload.get("folder", "")).strip()))
            if not payload.get("folder") or not os.path.isdir(folder):
                return _json_response(400, {"error": "that is not a folder"})
            return self._import(folder, bool(payload.get("apply")))
        if parsed.path == "/api/choose-folder" and method == "POST":
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            chosen = choose_folder()
            if chosen is None:
                return _json_response(200, {"path": "", "cancelled": True})
            return _json_response(200, {"path": chosen, "cancelled": False})
        if method == "POST" and parsed.path.startswith("/api/reveal/"):
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            text_id = parsed.path.rsplit("/", 1)[-1]
            try:
                move_id = int(text_id)
            except ValueError:
                return _json_response(400, {"error": "move id must be a number"})
            return self._reveal(move_id)

        if method == "POST" and parsed.path.startswith("/api/file/"):
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            parts = parsed.path.split("/")
            if len(parts) != 5:
                return _json_response(404, {"error": "not found"})
            action, text_id = parts[3], parts[4]
            try:
                move_id = int(text_id)
            except ValueError:
                return _json_response(400, {"error": "move id must be a number"})
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                return _json_response(400, {"error": "request must contain JSON"})
            if not isinstance(payload, dict):
                return _json_response(400, {"error": "request must contain an object"})
            return self._file_action(action, move_id, payload)

        return _json_response(404, {"error": "not found"})

    def _valid_token(self, query):
        supplied = query.get("token", [""])[0]
        return hmac.compare_digest(str(supplied), self.token)

    def _same_origin(self, headers):
        return headers.get("origin") == "http://127.0.0.1:%d" % self.port

    def _status(self):
        return {
            "paused": self.journal.paused(),
            "port": self.port,
            "queue": dict((row["status"], row["count"])
                          for row in self.journal.queue_counts()),
        }

    def _reading(self):
        """What this machine can read, and what it could not.

        Somebody whose scanned post is being filed by nothing but its file
        type has no way to find out why. The answer is usually a program
        they have never heard of and do not have, and nothing anywhere was
        saying so where they would look.
        """
        return {
            "programs": platform_support.inventory(),
            "filed_unread": self.journal.waiting_for_reading(),
        }

    def _costs(self):
        """The files that were expensive to read.

        This is the closest thing to a crash report that will ever exist
        here, and it is deliberately shaped like a list of files rather
        than like a diagnostic: the person reading it owns these files and
        can act on them, and would not know what to do with a stack trace.
        """
        rows = self.journal.expensive(50, costs.SLOW_SECONDS,
                                      costs.GREEDY_BYTES)
        return {"files": [{"id": row["id"], "path": row["path"],
                           "name": row["file_name"], "size": row["size"],
                           "seconds": round(row["seconds"], 2),
                           "growth": row["growth"], "peak": row["peak"],
                           "reason": row["reason"],
                           "readings": row["readings"],
                           "last_seen": row["last_seen"]} for row in rows],
                "budget": costs.SOFT_BUDGET}

    def _backup(self):
        """Whether a second copy is being kept, where, and how far behind."""
        root = self.journal.get_state("mirror_root") or ""
        enabled = self.journal.get_state("mirror_enabled") == "yes"
        counts = self.journal.mirror_counts()
        return {
            "enabled": enabled,
            "root": root,
            "available": mirror.available(root) if root else False,
            "copied": counts.get("copied", {}).get("files", 0),
            "copied_bytes": counts.get("copied", {}).get("bytes", 0),
            "waiting": counts.get("pending", {}).get("files", 0),
            "waiting_bytes": counts.get("pending", {}).get("bytes", 0),
            "set_aside": counts.get("failed", {}).get("files", 0),
        }

    def _set_backup(self, payload):
        """Turn the second copy on or off, or point it somewhere else."""
        if payload.get("action") == "off":
            self.journal.set_state("mirror_enabled", "no")
            return _json_response(200, self._backup())

        root = str(payload.get("root", "")).strip()
        if not root:
            return _json_response(400, {"error": "no folder given"})
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            return _json_response(400, {"error": "that is not a folder"})
        home = os.path.abspath(os.path.expanduser("~"))
        if root == home or home.startswith(os.path.join(root, "")):
            return _json_response(400, {
                "error": "a backup inside the folder it is backing up is not "
                         "a backup; choose another disk"})
        if root.startswith(os.path.join(home, "")):
            return _json_response(400, {
                "error": "that is inside your home folder, so one accident "
                         "takes both copies; choose another disk"})
        if not mirror.available(root):
            return _json_response(400, {
                "error": "that folder cannot be written to right now"})
        self.journal.set_state("mirror_root", root)
        self.journal.set_state("mirror_enabled", "yes")
        queued = mirror.backfill(self.journal) \
            if payload.get("backfill", True) else 0
        result = self._backup()
        result["queued"] = queued
        return _json_response(200, result)

    def _import(self, folder, apply_it):
        """Sort one folder that is not an intake, once.

        A USB stick, a burned disc, the folder somebody's brother left on
        the desktop. It is the ordinary sort with the ordinary rules and the
        ordinary ledger -- so `undo` reverses it exactly like anything else
        -- and the only thing that makes it special is that nobody wants
        this folder watched afterwards.
        """
        rule_set = self.rules_getter() if self.rules_getter else None
        if rule_set is None:
            return _json_response(409, {"error": "rules could not be read"})
        for watched in (rule_set.watch.folders or ()):
            watched = os.path.abspath(os.path.expanduser(watched))
            if folder == watched:
                return _json_response(400, {
                    "error": "that folder is already watched; it is sorted "
                             "on its own"})
        try:
            plan = sorter.build_plan(folder, rule_set, journal=self.journal,
                                     reader=self.reader)
        except (OSError, ValueError) as error:
            return _json_response(400, {"error": str(error)})

        preview = [{
            "name": os.path.basename(item.members[0].source),
            "destination": item.members[0].destination,
            "rule": item.rule_name,
        } for item in plan.items[:200]]
        if not apply_it:
            return _json_response(200, {
                "folder": folder, "planned": len(plan.items),
                "skipped": len(plan.skipped), "applied": False,
                "moves": preview})
        result = sorter.execute(plan, rule_set, self.journal, dry_run=False)
        if result.dry_run:
            # `execute` forces a folder's first run under a given rules
            # fingerprint to a preview even when the caller asked to apply
            # -- the same safety net `sort --apply` respects on the CLI.
            # Nothing moved. Reporting "applied" here anyway (as this used
            # to, and as `getattr(result, "moved", ...)` masked by quietly
            # falling back to the planned count on a RunResult that has no
            # `moved` attribute at all) told the person who just clicked
            # "One-time sort of a folder..." that their USB stick was
            # filed when every item was still sitting where it started.
            return _json_response(200, {
                "folder": folder, "planned": len(plan.items),
                "skipped": len(plan.skipped), "applied": False,
                "forced_preview": result.forced_preview,
                "moved": 0, "failed": 0, "moves": preview})
        return _json_response(200, {
            "folder": folder, "planned": len(plan.items),
            "skipped": len(plan.skipped), "applied": True,
            "moved": result.completed,
            "failed": result.failed,
            "moves": preview})

    def _rules(self):
        """The rules as they stand, in the order they are tried.

        Read-only. This page shows what the file says; the file itself
        belongs to whoever wrote it and nothing here rewrites it.
        """
        rule_set = self.rules_getter() if self.rules_getter else None
        if rule_set is None:
            return {"source": self.rule_path or "", "rules": [],
                    "error": "rules could not be read"}
        return {
            "source": rule_set.source,
            "rules": [{
                "name": rule.name,
                "when": rule.when_text,
                "into": rule.into or "",
                "holding": bool(rule.holding),
                "rename": rule.rename or "",
            } for rule in rule_set.rules],
        }

    def _folders(self):
        """Where files come in from, and where they are sent."""
        rule_set = self.rules_getter() if self.rules_getter else None
        if rule_set is None:
            return {"watch": [], "destinations": [], "source": ""}
        roots, seen = [], set()
        for rule in rule_set.rules:
            if not rule.into:
                continue
            literal = rule.into.split("{")[0].rstrip(os.sep)
            if literal and literal not in seen:
                seen.add(literal)
                roots.append({"path": literal, "holding": bool(rule.holding)})
        added = set(self.journal.extra_watch_folders())
        return {
            "watch": [{"path": path, "added_here": path in added}
                      for path in (rule_set.watch.folders or ())],
            "added": sorted(added),
            "depth": rule_set.watch.depth,
            "destinations": roots,
            "source": rule_set.source,
        }

    def _move(self, row):
        result = dict((key, row[key]) for key in (
            "id", "run_id", "action", "operation", "rule_name", "source",
            "destination", "size", "sha256", "status", "error",
            "created_at", "completed_at", "undone_at", "started_at",
            "finished_at"))
        path = row["destination"] if os.path.lexists(row["destination"]) \
            else row["source"]
        result["current_path"] = path
        result["file_name"] = os.path.basename(path)
        result["extension"] = os.path.splitext(path)[1].lstrip(".").lower()
        facts = self._facts(row)
        facts.setdefault("name", result["file_name"])
        facts.setdefault("format", result["extension"])
        facts.setdefault("size", row["size"])
        result["facts"] = facts
        return result

    @staticmethod
    def _facts(row):
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except (KeyError, TypeError, ValueError):
            return {}
        return facts if isinstance(facts, dict) else {}

    def _reveal(self, move_id):
        row = self.journal.move(move_id)
        if row is None:
            return _json_response(404, {"error": "unknown move id"})
        destination = row["destination"]
        source = row["source"]
        path = destination if os.path.lexists(destination) else source
        if not os.path.lexists(path):
            return _json_response(409, {"error": "recorded file is missing"})
        try:
            reveal(path)
        except (OSError, subprocess.SubprocessError) as error:
            return _json_response(500, {"error": "cannot reveal file: %s" % error})
        return _json_response(200, {"revealed": move_id})

    def _file_action(self, action, move_id, payload):
        row = self.journal.move(move_id)
        if row is None:
            return _json_response(404, {"error": "unknown move id"})
        source = self._current_path(row)
        if source is None:
            return _json_response(409, {"error": "recorded file is missing"})
        facts = self._facts(row)
        if action == "trash":
            return self._trash(move_id, source, facts)
        if action == "undo":
            if row["operation"] not in ("move", "restore"):
                return _json_response(409, {"error": "only a move can be restored"})
            target = row["source"] if source == row["destination"] \
                else row["destination"]
            return self._transfer(move_id, source, target, "restore", facts)
        if action not in ("copy", "move"):
            return _json_response(404, {"error": "unknown file action"})
        folder = payload.get("folder")
        if not isinstance(folder, str) or not os.path.isdir(folder):
            return _json_response(400, {"error": "choose an existing destination folder"})
        target = os.path.join(os.path.abspath(folder), os.path.basename(source))
        return self._transfer(move_id, source, target, action, facts)

    def _current_path(self, row):
        for path in (row["destination"], row["source"]):
            if os.path.lexists(path):
                return path
        return None

    def _transfer(self, move_id, source, destination, operation, facts=None):
        if os.path.lexists(destination):
            return _json_response(409, {"error": "destination already exists"})
        try:
            size = mover.size_path(source)
            digest = mover.hash_path(source)
        except (OSError, subprocess.SubprocessError) as error:
            return _json_response(409, {"error": "cannot read recorded file: %s" % error})
        run_id = self.journal.start_run("file-manager", os.path.dirname(source),
                                        "ledger:%d" % move_id, False)
        new_id = self.journal.add_move(run_id, 1, 1, operation,
                                       "[file manager #%d]" % move_id,
                                       source, destination, size, digest,
                                       facts=facts)
        try:
            mover.transfer(source, destination,
                           operation="move" if operation in ("move", "restore") else "copy",
                           expected_hash=digest)
        except (OSError, mover.MoveError) as error:
            self.journal.update_move(new_id, "failed", str(error))
            self.journal.finish_run(run_id, "failed", str(error))
            return _json_response(409, {"error": "file action failed: %s" % error})
        self.journal.update_move(new_id, "done" if operation != "copy" else "copied")
        self.journal.finish_run(run_id, "completed")
        return _json_response(200, {"move_id": new_id, "operation": operation,
                                    "destination": destination})

    def _trash(self, move_id, source, facts=None):
        try:
            size = mover.size_path(source)
            digest = mover.hash_path(source)
        except (OSError, subprocess.SubprocessError) as error:
            return _json_response(409, {"error": "cannot read recorded file: %s" % error})
        run_id = self.journal.start_run("file-manager", os.path.dirname(source),
                                        "ledger:%d" % move_id, False)
        new_id = self.journal.add_move(run_id, 1, 1, "trash",
                                       "[file manager #%d]" % move_id, source,
                                       "[system trash]", size, digest,
                                       facts=facts)
        try:
            trash(source)
        except OSError as error:
            self.journal.update_move(new_id, "failed", str(error))
            self.journal.finish_run(run_id, "failed", str(error))
            return _json_response(409, {"error": "could not move to Trash: %s" % error})
        self.journal.update_move(new_id, "done")
        self.journal.finish_run(run_id, "completed")
        return _json_response(200, {"move_id": new_id, "operation": "trash"})

    def _page(self):
        with open(PAGE_FILE, "r", encoding="utf-8") as handle:
            source = handle.read()
        return source.replace("{{TOKEN_JSON}}", json.dumps(self.token))


class RevealError(Exception):
    """No file manager could be reached at all."""


def reveal(path):
    """Ask the platform file manager to reveal a ledger-resolved path."""
    path = os.path.abspath(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
        return
    if os.name == "nt":                                      # pragma: no cover
        subprocess.Popen(["explorer", "/select," + path])
        return
    # Waited for, not fired and forgotten -- which needs `--print-reply`.
    # Without it, `dbus-send --type=method_call` returns 0 the instant the
    # message reaches the bus, whether or not anything is listening on
    # FileManager1: on a desktop with `dbus-send` but no file manager
    # answering it -- which is most minimal ones -- the call "succeeded",
    # `selected` was true, and Reveal did nothing at all with no fallback
    # and nothing said. Selecting the file is nicer; opening the folder it
    # is in is the part that must not be optional.
    uri = "file://" + urllib.parse.quote(path)
    selected = False
    try:
        done = subprocess.run([
            "dbus-send", "--session", "--print-reply",
            "--dest=org.freedesktop.FileManager1", "--type=method_call",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            "array:string:%s" % uri, "string:",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        selected = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        selected = False
    if not selected:
        try:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError as error:
            raise RevealError("no file manager answered: %s" % error)


def trash(path):
    """Move to the platform Trash/Recycle Bin; never unlink from the page."""
    path = os.path.abspath(path)
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e",
                        'tell application "Finder" to delete POSIX file %s' %
                        _applescript_string(path)], check=True)
        return
    if os.name == "nt":                                      # pragma: no cover
        quoted = path.replace("'", "''")
        command = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
                   "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('%s', "
                   "'OnlyErrorDialogs', 'SendToRecycleBin')" % quoted)
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True)
        return
    subprocess.run(["gio", "trash", path], check=True)


def _body(raw):
    """A POST body as a dict, never raising and never returning anything else."""
    try:
        payload = json.loads((raw or b"").decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError, AttributeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def choose_folder():
    """Ask the operating system for a folder, and get a real path back.

    A web page cannot do this. `<input type=file webkitdirectory>` hands over
    file names without a usable path and wants to upload them, which is the
    opposite of what a local sorter needs. So the request comes back here and
    the platform's own chooser is opened -- the same shape as `reveal`, which
    has always shelled out to the file manager.

    Returns the path, or None when the person cancelled or no chooser could
    be opened. A missing chooser is not an error: the page keeps a text field
    beside the button for exactly that case, and a typed path works as well
    as a picked one.
    """
    if sys.platform == "darwin":
        script = ('POSIX path of (choose folder with prompt '
                  '"Choose a folder for auto-sort to tidy")')
        done = _run(["osascript", "-e", script])
        return done.strip() or None if done is not None else None
    if os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")
        done = _run(["powershell", "-NoProfile", "-Command", script])
        return done.strip() or None if done is not None else None
    for chooser in (["zenity", "--file-selection", "--directory"],
                    ["kdialog", "--getexistingdirectory", os.path.expanduser("~")]):
        done = _run(chooser)
        if done is not None:
            return done.strip() or None
    return None


def _run(command):
    """The chooser's answer, or None if it could not be asked at all.

    A cancelled dialog and a missing program both come back as no folder,
    and the difference matters: one is an answer and the other is a reason
    to show the text field instead.
    """
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=300,
                              universal_newlines=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else ""


def _applescript_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_log(state_file=None):
    """Open the current daemon log URL without accepting a user-supplied URL."""
    import webbrowser
    import ledger
    with ledger.Ledger(state_file) as journal:
        port = int(journal.get_state("daemon_port", 47653))
        token = journal.get_state("web_token")
    if not token:
        return False
    return bool(webbrowser.open("http://127.0.0.1:%d/?token=%s" % (
        port, urllib.parse.quote(token, safe=""))))


def _looks_like_http(data):
    return data.startswith((b"GET ", b"POST ", b"OPTIONS ", b"HEAD "))


def _read_request(connection, initial):
    data = initial
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_HEADER_BYTES:
            raise ValueError("headers are too large")
        block = connection.recv(4096)
        if not block:
            raise ValueError("incomplete request")
        data += block
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1"):
        raise ValueError("invalid request line")
    headers = {}
    for line in lines[1:]:
        if ":" not in line:
            raise ValueError("invalid header")
        key, value = line.split(":", 1)
        headers[key.lower()] = value.strip()
    try:
        content_length = int(headers.get("content-length", "0"))
    except ValueError:
        raise ValueError("invalid content length")
    if content_length < 0 or content_length > 1024 * 1024:
        raise ValueError("invalid content length")
    while len(body) < content_length:
        block = connection.recv(min(4096, content_length - len(body)))
        if not block:
            raise ValueError("incomplete body")
        body += block
    return parts[0], parts[1], headers, body[:content_length]


def _send_response(connection, response):
    reason = {
        200: "OK", 202: "Accepted", 400: "Bad Request", 403: "Forbidden",
        404: "Not Found", 409: "Conflict", 500: "Internal Server Error",
    }.get(response.status, "Error")
    headers = [
        "HTTP/1.1 %d %s" % (response.status, reason),
        "Content-Type: %s" % response.content_type,
        "Content-Length: %d" % len(response.body),
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        "X-Frame-Options: DENY",
        "Content-Security-Policy: default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        "Connection: close",
        "",
        "",
    ]
    connection.sendall("\r\n".join(headers).encode("ascii") + response.body)


def _json_response(status, payload, command=""):
    return Response(status, json.dumps(payload, indent=2, default=str) + "\n",
                    "application/json; charset=utf-8", command)
