"""The loopback-only log page and ledger-ID-based file reveal endpoint."""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import urllib.parse

import mover

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

    def __init__(self, journal, port, token):
        self.journal = journal
        self.port = int(port)
        self.token = str(token)

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
            return _json_response(200, {"moves": [self._move(row)
                                                  for row in
                                                  self.journal.recent_moves(limit)]})

        if method == "POST" and parsed.path in (
                "/api/pause", "/api/resume", "/api/sort-now"):
            if not self._same_origin(headers):
                return _json_response(403, {"error": "cross-origin request refused"})
            if parsed.path == "/api/pause":
                self.journal.set_paused(True)
                return _json_response(200, self._status(), "")
            if parsed.path == "/api/resume":
                self.journal.set_paused(False)
                return _json_response(200, self._status(), "")
            return _json_response(202, {"queued": True}, "sort-now")

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


def reveal(path):
    """Ask the platform file manager to reveal a ledger-resolved path."""
    path = os.path.abspath(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
        return
    if os.name == "nt":                                      # pragma: no cover
        subprocess.Popen(["explorer", "/select," + path])
        return
    uri = "file://" + urllib.parse.quote(path)
    try:
        subprocess.Popen([
            "dbus-send", "--session",
            "--dest=org.freedesktop.FileManager1", "--type=method_call",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            "array:string:%s" % uri, "string:",
        ])
    except OSError:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


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
